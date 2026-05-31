"""
Project Pulse — Gemini AI Service
Handles the text parsing pipeline for user input.
Uses Gemini 3.1 Flash Lite via the Google GenAI SDK.

Supports BYOK (Bring Your Own Key): if a user has stored a personal
API key in Supabase Vault, their key is used instead of the system key.
"""

import json
import logging

from google import genai
from google.genai import types
from sqlalchemy import text as sql_text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Default system client (uses the global GEMINI_API_KEY)
client = genai.Client(api_key=settings.gemini_api_key)

MODEL_NAME = "gemini-3.1-flash-lite"


def get_client_for_user(user_id: str) -> genai.Client:
    """
    Resolve the Gemini client for a given user.
    If the user has an encrypted BYOK key in their profile, decrypt and use it.
    Otherwise, fall back to the system key.
    """
    try:
        db = SessionLocal()
        result = db.execute(
            sql_text("SELECT encrypted_api_key FROM profiles WHERE id = :uid"),
            {"uid": user_id},
        ).mappings().fetchone()
        db.close()

        if result and result["encrypted_api_key"]:
            from app.utils.crypto import decrypt_key
            plain_key = decrypt_key(result["encrypted_api_key"])
            logger.info(f"[BYOK] Using user's personal API key for {user_id[:8]}...")
            return genai.Client(api_key=plain_key)
    except Exception as e:
        logger.warning(f"[BYOK] Key resolution failed (using system key): {e}")

    return client


SYSTEM_PROMPT = """You are Project Pulse's AI Health Concierge. Your job is to parse user fitness/health inputs and return structured JSON data.

## RULES:
1. Determine the user's INTENT from their input. Classify as exactly one of: "food", "workout", "biometric", or "note".
2. Extract all relevant data and return it in the exact JSON schema for that intent type.
3. Always include a `confidence_score` (float 0.0 to 1.0) indicating how confident you are in your parsing.
4. Always include a `short_persona_response` (string, 1-2 sentences) — a friendly, concise acknowledgment of what was logged, written in the voice of a professional fitness coach.
5. UNIT CONVERSION: If the user provides imperial units (lbs, oz, inches, miles), convert to metric for `canonical_weight`/`canonical_value`/`canonical_unit` fields, but PRESERVE the original values in `original_weight`/`original_value`/`original_unit` fields.
6. If the user provides metric units, set both canonical and original to the same values.
7. If quantities are not explicitly stated, estimate them and set `is_estimated: true`.

## OUTPUT SCHEMAS (return ONLY the JSON, no markdown):

### For type "food":
{
  "type": "food",
  "confidence_score": 0.0-1.0,
  "short_persona_response": "string",
  "parsed_data": {
    "meal_context": "Breakfast|Lunch|Dinner|Snack|Unknown",
    "items": [
      {
        "name": "string",
        "canonical_weight": number_in_grams,
        "canonical_unit": "g",
        "original_weight": number_as_user_said,
        "original_unit": "string_as_user_said",
        "macros": { "p": protein_g, "c": carbs_g, "f": fat_g, "kcal": calories },
        "is_estimated": boolean
      }
    ],
    "total_macros_calculated": { "p": number, "c": number, "f": number, "kcal": number }
  }
}

### For type "workout":
{
  "type": "workout",
  "confidence_score": 0.0-1.0,
  "short_persona_response": "string",
  "parsed_data": {
    "exercise_name": "string",
    "muscle_group": "string",
    "sets": [
      {
        "index": number,
        "reps": number,
        "canonical_weight": number_in_kg,
        "canonical_unit": "kg",
        "original_weight": number_as_user_said,
        "original_unit": "string_as_user_said",
        "rpe": number_or_null
      }
    ],
    "total_volume": number
  }
}

### For type "biometric":
{
  "type": "biometric",
  "confidence_score": 0.0-1.0,
  "short_persona_response": "string",
  "parsed_data": {
    "metric_type": "body_weight|body_fat|height|heart_rate|steps|blood_pressure",
    "canonical_value": number_in_metric,
    "canonical_unit": "kg|%|cm|bpm|steps|mmHg",
    "original_value": number_as_user_said,
    "original_unit": "string_as_user_said"
  }
}

### For type "note" (general health notes, goals, or unclassifiable input):
{
  "type": "note",
  "confidence_score": 0.0-1.0,
  "short_persona_response": "string",
  "parsed_data": {
    "content": "string — the user's note or observation",
    "tags": ["relevant", "tags"]
  }
}
"""


async def parse_user_input(raw_input: str, user_id: str, memory_context: str | None = None) -> dict:
    """
    Send user input to Gemini 3.1 Flash Lite for intent routing and data extraction.
    Uses the user's BYOK key if available, otherwise falls back to system key.

    Returns a dict with keys: type, confidence_score, short_persona_response, parsed_data
    """
    try:
        # Resolve client (BYOK or system)
        active_client = get_client_for_user(user_id)

        # Build the system instruction — inject memory context if available
        system_instruction = SYSTEM_PROMPT
        if memory_context:
            system_instruction = f"{memory_context}\n\n{SYSTEM_PROMPT}"

        response = active_client.models.generate_content(
            model=MODEL_NAME,
            contents=f"Parse this fitness/health input:\n\n{raw_input}",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )

        # Parse the JSON response
        result = json.loads(response.text)

        # Validate required fields exist
        if "type" not in result:
            raise ValueError("Gemini response missing 'type' field")
        if "parsed_data" not in result:
            raise ValueError("Gemini response missing 'parsed_data' field")

        return {
            "type": result.get("type", "note"),
            "confidence_score": float(result.get("confidence_score", 0.5)),
            "short_persona_response": result.get("short_persona_response", "Logged."),
            "parsed_data": result.get("parsed_data", {}),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Gemini returned invalid JSON: {e}")
        return {
            "type": "note",
            "confidence_score": 0.3,
            "short_persona_response": "I had trouble parsing that. Saved as a note for your review.",
            "parsed_data": {"content": raw_input, "tags": ["unparsed"]},
        }
    except Exception as e:
        logger.error(f"Gemini service error: {e}")
        raise


async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    """
    Two-Pass Step 1: High-fidelity verbatim transcription with fitness domain bias.
    Sends raw audio to Gemini with a domain-aware transcription prompt.

    Returns the transcribed text as a plain string, or empty string if silent.
    """
    TRANSCRIPTION_PROMPT = (
        "You are an accurate, high-fidelity audio transcription tool. "
        "Transcribe the spoken words in the audio file verbatim. "
        "CRITICAL: This is a fitness and nutrition app. If the audio is phonetically ambiguous, "
        "prioritize vocabulary related to food, exercises, weights, and health metrics.\n\n"
        "Domain Vocabulary Guide:\n"
        "- Translate 'I8' or 'i8' to 'I ate'.\n"
        "- Translate '2x' or '2 x' to 'two eggs' or '2 eggs' if in food context, or 'two sets/reps' in gym context.\n"
        "- Prioritize words like: 'ate', 'eggs', 'chicken', 'rice', 'oats', 'protein', 'calories', "
        "'reps', 'sets', 'bench press', 'squat', 'deadlift', 'grams', 'kilograms', 'pounds', "
        "'water', 'weigh', 'weight', 'breakfast', 'lunch', 'dinner', 'snack'.\n"
        "- Common mishearings: 'aid' -> 'ate', 'wait' -> 'weight', 'axe' -> 'eggs', 'too' -> 'two'.\n\n"
        "Do not output any explanations or metadata. Output ONLY the verbatim transcribed words. "
        "If silent or no speech detected, return an empty string."
    )

    try:
        logger.info(f"[TRANSCRIBE] Received {len(audio_bytes)} bytes, mime: {mime_type}")

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                        types.Part.from_text(text="Transcribe this audio verbatim."),
                    ],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=TRANSCRIPTION_PROMPT,
                temperature=0.0,
            ),
        )

        transcript = response.text.strip()
        logger.info(f"[TRANSCRIBE] Result: '{transcript}'")
        return transcript

    except Exception as e:
        logger.error(f"[TRANSCRIBE] Error: {e}")
        raise


VISION_SYSTEM_PROMPT = """You are an expert nutrition and visual analysis tool for Project Pulse, a fitness tracking app.
Analyze the provided image of food. Identify the dishes, estimate their weight/portions in grams, and calculate the macronutrients.

## RULES:
1. Always return type "food".
2. Include a confidence_score (0.0-1.0) based on how clearly you can identify the food.
3. Include a short_persona_response (1-2 sentences) — encouraging, coach-like.
4. Estimate portions in grams. Set is_estimated: true for all visual estimates.
5. Calculate macros (protein, carbs, fat, calories) for each item.

## OUTPUT SCHEMA (return ONLY JSON, no markdown):
{
  "type": "food",
  "confidence_score": 0.0-1.0,
  "short_persona_response": "string",
  "parsed_data": {
    "meal_context": "Breakfast|Lunch|Dinner|Snack|Unknown",
    "items": [
      {
        "name": "string",
        "canonical_weight": number_in_grams,
        "canonical_unit": "g",
        "original_weight": number_in_grams,
        "original_unit": "g",
        "macros": { "p": protein_g, "c": carbs_g, "f": fat_g, "kcal": calories },
        "is_estimated": true
      }
    ],
    "total_macros_calculated": { "p": number, "c": number, "f": number, "kcal": number }
  }
}
"""


async def parse_image_input(image_bytes: bytes, mime_type: str, caption: str | None = None) -> dict:
    """
    Send a food image (+ optional caption) to Gemini for visual analysis and macro estimation.
    The caption acts as 'Ground Truth' to supplement visual analysis with user context.
    Returns structured food data matching the TIDC food schema.
    """
    try:
        logger.info(f"[VISION] Received {len(image_bytes)} bytes, mime: {mime_type}, caption: '{caption or ''}'")

        # Build the text prompt — include caption if provided
        if caption:
            user_text = (
                f"Analyze this food image. The user provided this caption: '{caption}'. "
                "Use this caption as Ground Truth to adjust your visual analysis. "
                "If the caption mentions ingredients invisible in the photo (e.g., 'cooked in butter', "
                "'added olive oil dressing', 'sugar-free'), add them to the ingredient list and calculate their macros. "
                "If there is a conflict between what you see and what the caption says, trust the user's caption. "
                "Identify all items, estimate portions, and calculate macros."
            )
        else:
            user_text = "Analyze this food image. Identify all items, estimate portions, and calculate macros."

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        types.Part.from_text(text=user_text),
                    ],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=VISION_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )

        result = json.loads(response.text)
        logger.info(f"[VISION] Result type: {result.get('type')} | Confidence: {result.get('confidence_score')}")

        if "parsed_data" not in result:
            raise ValueError("Gemini vision response missing 'parsed_data' field")

        return {
            "type": result.get("type", "food"),
            "confidence_score": float(result.get("confidence_score", 0.5)),
            "short_persona_response": result.get("short_persona_response", "Food logged from photo."),
            "parsed_data": result.get("parsed_data", {}),
        }

    except json.JSONDecodeError as e:
        logger.error(f"[VISION] Invalid JSON from Gemini: {e}")
        return {
            "type": "food",
            "confidence_score": 0.3,
            "short_persona_response": "I had trouble analyzing that image. Saved for your review.",
            "parsed_data": {"meal_context": "Unknown", "items": [], "total_macros_calculated": {"p": 0, "c": 0, "f": 0, "kcal": 0}},
        }
    except Exception as e:
        logger.error(f"[VISION] Error: {e}")
        raise


async def get_embedding(text: str) -> list[float]:
    """
    Generate a 768-dimensional text embedding using Google's gemini-embedding-001 model.
    Configured to output 768 dims to match our pgvector column definition.
    Used for semantic memory storage and similarity search.
    """
    try:
        response = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768),
        )
        embedding = response.embeddings[0].values
        logger.info(f"[EMBEDDING] Generated {len(embedding)}-dim vector for: '{text[:50]}'")
        return list(embedding)
    except Exception as e:
        logger.error(f"[EMBEDDING] Error: {e}")
        raise
