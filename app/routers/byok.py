"""
Project Pulse — BYOK Router
POST   /api/v1/profile/byok      — Encrypt and save user's API key to profiles table.
POST   /api/v1/profile/byok/test — Test a key's validity before saving.
DELETE /api/v1/profile/byok      — Remove user's stored key.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from google import genai
from google.genai import types

from app.database import get_db
from app.schemas.byok import BYOKSaveRequest, BYOKResponse, BYOKTestRequest
from app.utils.crypto import encrypt_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/profile", tags=["byok"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.post("/byok", response_model=BYOKResponse)
async def save_byok_key(request: BYOKSaveRequest, db: Session = Depends(get_db)):
    """
    Encrypt the user's Gemini API key with AES-256 and store in the profiles table.
    """
    try:
        # Encrypt the key using Fernet (AES-256)
        encrypted = encrypt_key(request.api_key)

        # Save encrypted key to profiles table
        db.execute(
            sql_text("""
                UPDATE profiles
                SET encrypted_api_key = :encrypted_key,
                    subscription_tier = 'byok_ad_supported'
                WHERE id = :uid
            """),
            {"uid": MOCK_USER_ID, "encrypted_key": encrypted},
        )
        db.commit()

        logger.info(f"[BYOK] ✓ Encrypted API key saved for user {MOCK_USER_ID}")
        print(f"[BYOK] ✓ Key encrypted and saved to profiles.encrypted_api_key")

        return BYOKResponse(
            status="saved",
            message="Your API key has been encrypted (AES-256) and stored securely. All AI operations will now use your personal quota.",
        )

    except Exception as e:
        logger.error(f"[BYOK] Save failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save key: {str(e)}")


@router.post("/byok/test", response_model=BYOKResponse)
async def test_byok_key(request: BYOKTestRequest):
    """
    Test a Gemini API key's validity by making a lightweight generation call.
    Does NOT save the key — just validates it works.
    """
    try:
        test_client = genai.Client(api_key=request.api_key)
        response = test_client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents="Say 'OK' in one word.",
            config=types.GenerateContentConfig(temperature=0.0),
        )

        if response.text and len(response.text.strip()) > 0:
            return BYOKResponse(
                status="valid",
                message="Key is valid! Gemini responded successfully.",
            )
        else:
            return BYOKResponse(
                status="invalid",
                message="Key connected but received an empty response.",
            )

    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "UNAUTHENTICATED" in error_msg:
            return BYOKResponse(status="invalid", message="Invalid API key. Please check and try again.")
        elif "403" in error_msg or "PERMISSION_DENIED" in error_msg:
            return BYOKResponse(status="invalid", message="Key lacks permissions. Enable the Generative Language API.")
        else:
            return BYOKResponse(status="error", message=f"Test failed: {error_msg[:100]}")


@router.delete("/byok", response_model=BYOKResponse)
async def delete_byok_key(db: Session = Depends(get_db)):
    """
    Remove the user's stored encrypted API key.
    """
    try:
        db.execute(
            sql_text("""
                UPDATE profiles
                SET encrypted_api_key = NULL,
                    subscription_tier = 'beta_free'
                WHERE id = :uid
            """),
            {"uid": MOCK_USER_ID},
        )
        db.commit()

        logger.info(f"[BYOK] Key removed for user {MOCK_USER_ID}")
        return BYOKResponse(status="removed", message="Your API key has been removed. Using system quota.")

    except Exception as e:
        logger.error(f"[BYOK] Delete failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to remove key: {str(e)}")
