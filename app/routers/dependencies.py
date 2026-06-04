"""
Project Pulse V2 — Authentication Dependencies
Implements JWT validation supporting both ES256 (asymmetric JWKS) and HS256 (symmetric).

Supabase projects using newer auth versions sign user access tokens with ES256 (ECDSA P-256).
The public key is fetched from the project's JWKS endpoint and cached in memory.

Architecture:
    1. Extracts Bearer token from Authorization header
    2. Inspects token header to determine algorithm (ES256 or HS256)
    3. For ES256: verifies using cached JWKS public key from Supabase
    4. For HS256: verifies using SUPABASE_JWT_SECRET
    5. Validates audience and expiration claims
    6. Resolves or auto-provisions the user's local profile row
    7. Returns validated ProfileResponse for downstream route handlers
"""

import asyncio
import logging
import uuid

import httpx
import jwt
from jwt import PyJWK
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAlgorithmError,
    InvalidAudienceError,
    InvalidTokenError,
)
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.identity import Profile, UserBiometric
from app.schemas.identity import ProfileResponse
from app.services.security import security_service

logger = logging.getLogger(__name__)

# HTTP Bearer token extraction scheme
security_scheme = HTTPBearer(
    scheme_name="Supabase JWT",
    description="Supabase access token from auth.signIn()",
    auto_error=True,
)

# In-memory JWKS cache
_jwks_cache: dict[str, PyJWK] = {}
_jwks_loaded: bool = False


def _get_supabase_jwks_url() -> str:
    """Constructs the JWKS endpoint URL from the Supabase project URL."""
    base_url = settings.supabase_url.rstrip("/")
    return f"{base_url}/auth/v1/.well-known/jwks.json"


async def _load_jwks() -> None:
    """
    Fetches and caches the JWKS public keys from the Supabase auth endpoint.
    Called once on first token verification, then cached for the process lifetime.

    The blocking httpx.get is dispatched to a worker thread via asyncio.to_thread
    so a cold-start JWKS fetch never locks up the event loop under concurrent traffic.
    """
    global _jwks_cache, _jwks_loaded

    jwks_url = _get_supabase_jwks_url()
    logger.info(f"[AUTH] Fetching JWKS from {jwks_url}")

    try:
        response = await asyncio.to_thread(httpx.get, jwks_url, timeout=10.0)
        response.raise_for_status()
        jwks_data = response.json()

        for key_data in jwks_data.get("keys", []):
            kid = key_data.get("kid")
            if kid:
                _jwks_cache[kid] = PyJWK(key_data)
                logger.info(f"[AUTH] Cached JWKS key: kid={kid}, alg={key_data.get('alg')}")

        _jwks_loaded = True
    except Exception as e:
        logger.error(f"[AUTH] Failed to fetch JWKS: {e}")
        _jwks_loaded = False


async def _get_signing_key(token: str) -> tuple:
    """
    Determines the correct signing key and algorithm for a token.

    Returns:
        tuple: (key, algorithms_list) for jwt.decode()
    """
    global _jwks_loaded

    # Parse the token header
    try:
        header = jwt.get_unverified_header(token)
    except DecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    alg = header.get("alg", "")
    kid = header.get("kid")

    # ES256 (asymmetric) — use JWKS public key
    if alg == "ES256":
        if not _jwks_loaded:
            await _load_jwks()

        if not _jwks_cache:
            logger.error("[AUTH] No JWKS keys available for ES256 verification")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication keys unavailable",
            )

        if kid and kid in _jwks_cache:
            return _jwks_cache[kid].key, ["ES256"]

        # If kid not found, try the first available key
        first_key = next(iter(_jwks_cache.values()))
        logger.warning(f"[AUTH] kid '{kid}' not in cache, using first available key")
        return first_key.key, ["ES256"]

    # HS256 (symmetric) — use JWT secret
    if alg == "HS256":
        secret = settings.supabase_jwt_secret
        if not secret or secret.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server authentication configuration error",
            )
        return secret.strip().encode("utf-8"), ["HS256"]

    # Unsupported algorithm
    logger.error(f"[AUTH] Unsupported token algorithm: {alg}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Unsupported token algorithm: {alg}",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """
    FastAPI dependency that validates the incoming Supabase JWT token
    and returns the authenticated user's profile.

    Supports both:
    - ES256 (ECDSA P-256): Newer Supabase projects with asymmetric signing
    - HS256 (HMAC-SHA256): Legacy Supabase projects with symmetric signing

    Auto-Provisioning:
        If the JWT is valid but no local profile exists, this dependency
        automatically generates a unique DEK, encrypts it with MASTER_KEK,
        and inserts a new profile row.
    """
    token = credentials.credentials

    # Step 1: Resolve signing key based on token header algorithm
    signing_key, algorithms = await _get_signing_key(token)

    # Step 2: Decode and verify the JWT
    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=algorithms,
            audience="authenticated",
            options={
                "verify_exp": True,
                "verify_iss": False,
                "verify_aud": True,
            },
            leeway=30,  # Allow 30 seconds of clock skew between client and server
        )
    except ExpiredSignatureError as e:
        logger.warning(f"[AUTH] Token expired: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidAlgorithmError as e:
        logger.error(f"[AUTH] Algorithm error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token algorithm verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidAudienceError as e:
        logger.warning(f"[AUTH] Invalid audience: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except DecodeError as e:
        logger.warning(f"[AUTH] Decode error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token decode failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidTokenError as e:
        logger.warning(f"[AUTH] Invalid token ({type(e).__name__}): {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Step 3: Extract user UUID from 'sub' claim
    user_id_str = payload.get("sub")
    if not user_id_str:
        logger.warning("[AUTH] Token missing 'sub' claim")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        logger.warning(f"[AUTH] Invalid UUID in 'sub' claim: {user_id_str}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Step 4: Look up the user's local profile
    stmt = select(Profile).where(Profile.id == user_id)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    # Step 5: Auto-provision if profile doesn't exist (first login)
    if profile is None:
        logger.info(f"[AUTH] Auto-provisioning profile for user {str(user_id)[:8]}...")
        try:
            raw_dek = security_service.generate_dek()
            encrypted_result = security_service.encrypt_with_kek(raw_dek)

            profile = Profile(
                id=user_id,
                encrypted_dek=encrypted_result["encrypted_dek"],
                dek_salt=encrypted_result["dek_salt"],
                dek_iv=encrypted_result["dek_iv"],
                timezone="UTC",
            )
            db.add(profile)
            await db.flush()  # Register profile.id in session before FK reference

            # Co-provision a blank biometrics row in the same transaction.
            # This enforces a strict 1:1 DB invariant between profiles and
            # user_biometrics, guaranteeing GET /biometrics always returns 200 OK
            # with null fields instead of a 404 for newly registered users.
            blank_biometrics = UserBiometric(user_id=user_id)
            db.add(blank_biometrics)

            await db.commit()
            await db.refresh(profile)

            logger.info(
                f"[AUTH] Profile + blank biometrics provisioned for user {str(user_id)[:8]}"
            )

        except Exception as e:
            await db.rollback()
            logger.error(f"[AUTH] Profile provisioning failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to provision user profile",
            )

    return ProfileResponse.model_validate(profile)
