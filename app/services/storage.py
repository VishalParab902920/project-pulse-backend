"""
Project Pulse — Supabase Storage Service
Handles file uploads to Supabase Storage buckets.
"""

import logging
import uuid

from supabase import create_client

from app.config import settings

logger = logging.getLogger(__name__)

# Initialize Supabase client for storage operations
supabase = create_client(settings.supabase_url, settings.supabase_key)

BUCKET_NAME = "entry-photos"


async def upload_image(image_bytes: bytes, mime_type: str, user_id: str, bucket_name: str = BUCKET_NAME) -> str:
    """
    Upload an image to Supabase Storage and return the public URL.

    Args:
        image_bytes: Raw image bytes
        mime_type: MIME type (e.g., 'image/jpeg')
        user_id: User ID for path namespacing

    Returns:
        Public URL of the uploaded image
    """
    # Generate a unique filename
    ext = "jpg" if "jpeg" in mime_type or "jpg" in mime_type else "png"
    filename = f"{user_id}/{uuid.uuid4().hex}.{ext}"

    try:
        # Upload to Supabase Storage
        supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": mime_type},
        )

        # Get the public URL
        public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
        logger.info(f"[STORAGE] Uploaded: {filename} -> {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"[STORAGE] Upload failed: {e}")
        # Return empty string if upload fails — entry still saves without photo
        return ""
