"""
Project Pulse V2 — Dual-Layer Envelope Encryption Engine
Implements AES-256-GCM envelope encryption with KEK/DEK architecture.

Architecture:
    MASTER_KEK (env var) → encrypts per-user DEKs → stored in profiles table
    User DEK (decrypted at runtime) → encrypts user-sensitive data (integrations, BYOK keys)

Security Guarantees:
    - AES-256-GCM authenticated encryption (confidentiality + integrity)
    - Unique 12-byte IV generated for EVERY encryption operation (never reused)
    - KEK rotation re-encrypts all DEKs atomically without touching user data
    - Raw keys never persisted to disk or logs
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


class SecurityService:
    """
    Envelope encryption service implementing the KEK/DEK dual-layer pattern.

    The Master KEK encrypts per-user DEKs. Each user's DEK encrypts their
    sensitive downstream data (integration credentials, BYOK API keys).
    """

    def __init__(self) -> None:
        self._kek: bytes | None = None

    @property
    def kek(self) -> bytes:
        """
        Lazily loads and validates the MASTER_KEK from environment.
        Returns the raw 32-byte key.

        Raises:
            ValueError: If MASTER_KEK is not configured or invalid length.
        """
        if self._kek is None:
            raw_kek = settings.master_kek
            if not raw_kek:
                raise ValueError(
                    "MASTER_KEK is not configured. Set the 'MASTER_KEK' environment "
                    "variable to a base64-encoded 32-byte key. Generate with: "
                    "python -c \"import os, base64; print(base64.b64encode(os.urandom(32)).decode())\""
                )
            decoded = base64.b64decode(raw_kek)
            if len(decoded) != 32:
                raise ValueError(
                    f"MASTER_KEK must decode to exactly 32 bytes (got {len(decoded)}). "
                    "Ensure it is a valid base64-encoded AES-256 key."
                )
            self._kek = decoded
        return self._kek

    @staticmethod
    def generate_dek() -> bytes:
        """
        Generates a cryptographically secure random 32-byte Data Encryption Key.

        Returns:
            bytes: A 256-bit random key suitable for AES-256-GCM.
        """
        return os.urandom(32)

    def encrypt_with_kek(self, plaintext: bytes) -> dict[str, str]:
        """
        Encrypts a user's DEK using the MASTER_KEK with AES-256-GCM.

        Args:
            plaintext: The raw DEK bytes to encrypt (must be 32 bytes).

        Returns:
            dict with keys:
                - encrypted_dek: Base64-encoded ciphertext (includes GCM auth tag)
                - dek_iv: Base64-encoded 12-byte initialization vector
                - dek_salt: Base64-encoded 16-byte salt (reserved for future KDF use)

        Raises:
            ValueError: If MASTER_KEK is not configured.
        """
        iv = os.urandom(12)
        salt = os.urandom(16)

        aesgcm = AESGCM(self.kek)
        ciphertext = aesgcm.encrypt(nonce=iv, data=plaintext, associated_data=salt)

        return {
            "encrypted_dek": base64.b64encode(ciphertext).decode("utf-8"),
            "dek_iv": base64.b64encode(iv).decode("utf-8"),
            "dek_salt": base64.b64encode(salt).decode("utf-8"),
        }

    def decrypt_with_kek(self, encrypted_dek: str, iv: str, salt: str | None = None) -> bytes:
        """
        Decrypts a user's DEK using the MASTER_KEK and stored IV.

        Args:
            encrypted_dek: Base64-encoded ciphertext from the profiles table.
            iv: Base64-encoded 12-byte IV from the profiles table.
            salt: Base64-encoded salt used as associated data during encryption.
                  If None, decryption uses empty associated data (backward compat).

        Returns:
            bytes: The raw 32-byte DEK.

        Raises:
            ValueError: If MASTER_KEK is not configured.
            cryptography.exceptions.InvalidTag: If ciphertext is tampered or wrong key.
        """
        ciphertext = base64.b64decode(encrypted_dek)
        nonce = base64.b64decode(iv)
        associated_data = base64.b64decode(salt) if salt else None

        aesgcm = AESGCM(self.kek)
        plaintext = aesgcm.decrypt(nonce=nonce, data=ciphertext, associated_data=associated_data)

        return plaintext

    @staticmethod
    def encrypt_user_data(plaintext_data: str, user_dek: bytes) -> dict[str, str]:
        """
        Encrypts downstream sensitive data using the user's raw DEK with AES-256-GCM.
        Used for encrypting integration credentials, BYOK API keys, etc.

        Args:
            plaintext_data: The string data to encrypt.
            user_dek: The user's raw 32-byte DEK (decrypted from profiles).

        Returns:
            dict with keys:
                - encrypted_data: Base64-encoded ciphertext (includes GCM auth tag)
                - iv: Base64-encoded 12-byte initialization vector
        """
        iv = os.urandom(12)
        data_bytes = plaintext_data.encode("utf-8")

        aesgcm = AESGCM(user_dek)
        ciphertext = aesgcm.encrypt(nonce=iv, data=data_bytes, associated_data=None)

        return {
            "encrypted_data": base64.b64encode(ciphertext).decode("utf-8"),
            "iv": base64.b64encode(iv).decode("utf-8"),
        }

    @staticmethod
    def decrypt_user_data(encrypted_data: str, iv: str, user_dek: bytes) -> str:
        """
        Decrypts user data with their raw DEK and stored IV.

        Args:
            encrypted_data: Base64-encoded ciphertext.
            iv: Base64-encoded 12-byte IV.
            user_dek: The user's raw 32-byte DEK.

        Returns:
            str: The decrypted plaintext string.

        Raises:
            cryptography.exceptions.InvalidTag: If ciphertext is tampered or wrong key.
        """
        ciphertext = base64.b64decode(encrypted_data)
        nonce = base64.b64decode(iv)

        aesgcm = AESGCM(user_dek)
        plaintext = aesgcm.decrypt(nonce=nonce, data=ciphertext, associated_data=None)

        return plaintext.decode("utf-8")


# Module-level singleton for dependency injection
security_service = SecurityService()
