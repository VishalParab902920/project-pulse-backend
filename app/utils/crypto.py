"""
Project Pulse — Encryption Utility
AES-256 encryption for BYOK API key storage using Fernet (symmetric).

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _get_fernet() -> Fernet:
    """Get the Fernet cipher instance from the environment key."""
    if not settings.encryption_key:
        raise ValueError("ENCRYPTION_KEY not configured in environment variables")
    return Fernet(settings.encryption_key.encode())


def encrypt_key(plain_key: str) -> str:
    """
    Encrypt a plain-text API key using AES-256 (Fernet).
    Returns a base64-encoded encrypted string safe for database storage.
    """
    f = _get_fernet()
    encrypted = f.encrypt(plain_key.encode())
    return encrypted.decode()


def decrypt_key(encrypted_key: str) -> str:
    """
    Decrypt an encrypted API key back to plain-text.
    Raises ValueError if the key is invalid or tampered.
    """
    try:
        f = _get_fernet()
        decrypted = f.decrypt(encrypted_key.encode())
        return decrypted.decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt API key — invalid or corrupted token")
