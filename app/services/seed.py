"""
Project Pulse — Development Seed
Ensures a mock profile exists for local development/testing.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal

logger = logging.getLogger(__name__)

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


def seed_dev_profile():
    """
    Insert a mock auth user and profile for development if they don't already exist.
    Uses the service-role DB connection which bypasses RLS.
    """
    db: Session = SessionLocal()
    try:
        # Check if mock profile already exists
        result = db.execute(
            text("SELECT id FROM profiles WHERE id = :uid"),
            {"uid": MOCK_USER_ID},
        ).fetchone()

        if result is not None:
            logger.info("Mock dev profile already exists.")
            return

        # First ensure the auth.users row exists (required by FK on profiles)
        auth_exists = db.execute(
            text("SELECT id FROM auth.users WHERE id = :uid"),
            {"uid": MOCK_USER_ID},
        ).fetchone()

        if auth_exists is None:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                text("""
                    INSERT INTO auth.users (
                        id, instance_id, aud, role, email,
                        encrypted_password, email_confirmed_at,
                        created_at, updated_at, confirmation_token,
                        recovery_token, email_change_token_new, email_change
                    ) VALUES (
                        :uid, '00000000-0000-0000-0000-000000000000',
                        'authenticated', 'authenticated', 'dev@projectpulse.local',
                        '$2a$10$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ12',
                        :now, :now, :now, '', '', '', ''
                    )
                """),
                {"uid": MOCK_USER_ID, "now": now},
            )
            logger.info(f"Seeded mock auth.users row: {MOCK_USER_ID}")

        # Now insert the profile
        db.execute(
            text("""
                INSERT INTO profiles (id, persona_name, persona_vibe, unit_preference, subscription_tier, onboarding_status)
                VALUES (:uid, 'Atlas', 'Professional Coach', 'metric', 'beta_free', 'complete')
            """),
            {"uid": MOCK_USER_ID},
        )
        db.commit()
        logger.info(f"Seeded mock dev profile: {MOCK_USER_ID}")

    except Exception as e:
        db.rollback()
        logger.warning(f"Could not seed dev profile: {e}")
    finally:
        db.close()
