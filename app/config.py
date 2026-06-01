"""
Project Pulse — Configuration
Loads environment variables via Pydantic BaseSettings.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Supabase Database (individual components for safe URL construction) ---
    supabase_db_host: str = "localhost"
    supabase_db_port: int = 5432
    supabase_db_name: str = "postgres"
    supabase_db_user: str = "postgres"
    supabase_db_password: str = ""

    # --- Supabase API ---
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_jwt_secret: str = ""  # Supabase JWT secret for local token verification

    # --- Gemini AI ---
    gemini_api_key: str = ""

    # --- Encryption (Envelope Encryption Architecture) ---
    master_kek: str = ""  # Base64-encoded 32-byte AES-256 Master Key Encryption Key
    encryption_key: str = ""  # Legacy Fernet key (deprecated, kept for migration)

    # --- Application ---
    environment: str = "development"
    debug: bool = True
    allowed_origins: list[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
