"""
Kayan V2 — AI Processing Pipeline & Semantic Memory Engine

Implements:
    - AIService: Two-pass multimodal audio pipeline (transcription → structured JSON)
    - MemoryService: Hybrid semantic RAG with pgvector cosine distance retrieval

Architecture:
    Pass 1: Raw audio → gemini-3.1-flash-lite → verbatim transcript
    Pass 2: Transcript → Gemini Structured Output → typed Nutrition/Workout JSON
    Memory: gemini-embedding-001 → pgvector HNSW → hybrid threshold retrieval

Per-Request BYOK Client Execution (P0):
    No global genai.Client is instantiated at process start. Instead, every
    AI-dependent request resolves a per-request client via `resolve_client`:
      - If the user has a configured BYOK key (encrypted_byok + byok_iv), the
        server decrypts the user DEK with the MASTER_KEK, decrypts the BYOK key
        with the DEK, and builds a client bound to the user's own key.
      - Otherwise it falls back to the shared server key (settings.gemini_api_key).
    The resolved client is threaded down into every Gemini call so a single
    request performs the DEK/BYOK decryption exactly once.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai import SemanticMemory
from app.models.identity import Profile
from app.services.security import security_service

logger = logging.getLogger(__name__)


# =============================================================
# Structured Output Schemas (Pydantic models for Gemini JSON mode)
# =============================================================


class MealType(str, Enum):
    """Valid meal context types."""
    breakfast = "breakfast"
    lunch = "lunch"
    dinner = "dinner"
    snack = "snack"
    unknown = "unknown"


class FoodItem(BaseModel):
    """A single parsed food item from user input."""
    name: str = Field(description="Name of the food item")
    serving_size_g: float = Field(description="Serving size in grams")
    calories: float = Field(description="Estimated calories for this serving")
    protein_g: float = Field(description="Protein in grams")
    carbs_g: float = Field(description="Carbohydrates in grams")
    fat_g: float = Field(description="Fat in grams")
    is_estimated: bool = Field(default=True, description="Whether macros are estimated")


class NutritionEntry(BaseModel):
    """Structured nutrition log parsed from user input."""
    entry_type: str = Field(default="nutrition", description="Always 'nutrition'")
    meal_type: MealType = Field(description="Meal context")
    items: list[FoodItem] = Field(description="List of food items consumed")
    total_calories: float = Field(description="Sum of all item calories")
    total_protein_g: float = Field(description="Sum of all protein")
    total_carbs_g: float = Field(description="Sum of all carbs")
    total_fat_g: float = Field(description="Sum of all fat")


class ExerciseSet(BaseModel):
    """A single set within a workout exercise."""
    set_number: int = Field(description="Set index (1-based)")
    reps: int | None = Field(default=None, description="Number of repetitions")
    weight_kg: float | None = Field(default=None, description="Weight in kilograms")
    rpe: float | None = Field(default=None, description="Rate of Perceived Exertion (1-10)")
    completed: bool = Field(default=True, description="Whether set was completed")


class WorkoutExercise(BaseModel):
    """A single exercise with its sets."""
    exercise_name: str = Field(description="Canonical exercise name")
    category: str = Field(description="Exercise category: strength, hypertrophy, or cardio")
    primary_muscle_group: str | None = Field(default=None, description="Primary muscle targeted")
    sets: list[ExerciseSet] = Field(description="List of sets performed")


class WorkoutEntry(BaseModel):
    """Structured workout log parsed from user input."""
    entry_type: str = Field(default="workout", description="Always 'workout'")
    session_name: str | None = Field(default=None, description="Workout session name if mentioned")
    exercises: list[WorkoutExercise] = Field(description="List of exercises performed")
    total_volume_kg: float | None = Field(default=None, description="Total volume (weight × reps)")


class ParsedEntry(BaseModel):
    """Top-level parsed entry — either nutrition or workout."""
    entry_type: str = Field(description="Either 'nutrition' or 'workout'")
    nutrition: NutritionEntry | None = Field(default=None, description="Present if entry_type is nutrition")
    workout: WorkoutEntry | None = Field(default=None, description="Present if entry_type is workout")


# =============================================================
# AIService — Two-Pass Multimodal Pipeline
# =============================================================


class AIService:
    """
    Orchestrates the two-pass AI pipeline for audio/text processing.

    Pass 1: Audio → verbatim transcription (domain-biased)
    Pass 2: Text → structured JSON (Nutrition or Workout entity)

    Clients are NEVER created at process start. Callers resolve a per-request
    client via `resolve_client(db, user_id)` and pass it into each method.
    """

    MODEL_NAME = "gemini-3.1-flash-lite"
    EMBEDDING_MODEL = "gemini-embedding-001"
    EMBEDDING_DIMENSIONS = 768

    TRANSCRIPTION_SYSTEM_PROMPT = (
        "CRITICAL: If audio is phonetically ambiguous, prioritize vocabulary "
        "related to food, exercises, and health metrics. "
        "Translate 'I8' or 'I82' to 'I ate' or 'I ate 2'. "
        "Output ONLY verbatim transcribed words."
    )

    STRUCTURED_PARSE_SYSTEM_PROMPT = (
        "You are a fitness and nutrition data extraction engine. "
        "Parse the user's natural language input into a structured JSON object. "
        "Determine if the input describes a NUTRITION entry (food/drink consumed) "
        "or a WORKOUT entry (exercises performed). "
        "\n\n"
        "RULES:\n"
        "1. If the input mentions eating, drinking, food items, meals, or calories → entry_type: 'nutrition'\n"
        "2. If the input mentions exercises, sets, reps, weights, gym, or training → entry_type: 'workout'\n"
        "3. For nutrition: estimate macros per 100g from your knowledge, then scale to serving size.\n"
        "4. For workouts: map informal exercise names to canonical names "
        "(e.g., 'squats' → 'Back Squat', 'bench' → 'Bench Press').\n"
        "5. Convert imperial units to metric (lbs → kg, oz → g).\n"
        "6. If quantities are ambiguous, provide reasonable estimates and mark is_estimated=true.\n"
        "7. Calculate totals (total_calories, total_volume_kg) from individual items/sets.\n"
    )

    AI_DECISION_VALIDATOR_PROMPT = (
        "You are a semantic relevance validator. Given a user's current input and a "
        "retrieved memory context, determine if the memory is RELEVANT to the user's input.\n\n"
        "RULES:\n"
        "- Return ONLY 'true' or 'false' (lowercase, no quotes, no explanation).\n"
        "- 'true' means the memory provides useful context for understanding the user's input.\n"
        "- 'false' means the memory is unrelated or would mislead interpretation.\n"
        "- Example: User says 'eggs' but memory is about 'protein shake' → false\n"
        "- Example: User says 'my usual breakfast' and memory describes their breakfast → true\n"
    )

    def __init__(self) -> None:
        """
        Initialize the service WITHOUT building any Gemini client.

        Per the P0 BYOK requirement, no global client is created at process
        start. The shared server-key client is lazily built on first fallback
        use and cached for the process lifetime.
        """
        self._server_client: genai.Client | None = None

    def _get_server_client(self) -> genai.Client:
        """
        Lazily builds and caches the shared server-key Gemini client.

        Used only as the fallback when a user has no BYOK key configured.

        Raises:
            RuntimeError: If no server GEMINI_API_KEY is configured.
        """
        if self._server_client is None:
            if not settings.gemini_api_key:
                raise RuntimeError(
                    "No Gemini API key available. Configure GEMINI_API_KEY "
                    "or set a per-user BYOK key."
                )
            self._server_client = genai.Client(api_key=settings.gemini_api_key)
        return self._server_client

    async def resolve_client(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> genai.Client:
        """
        Resolves the correct Gemini client for a given user request.

        Resolution order:
            1. Fetch the user's Profile ORM row (encryption fields live here;
               ProfileResponse intentionally omits them for security).
            2. If encrypted_byok + byok_iv are populated:
                 a. Decrypt the user DEK with the MASTER_KEK.
                 b. Decrypt the BYOK key with the raw DEK.
                 c. Return a per-request client bound to the user's own key.
            3. Otherwise (or on any BYOK decryption failure), fall back to the
               shared server-key client.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.

        Returns:
            genai.Client: A client bound to the user's BYOK key or the server key.
        """
        stmt = select(Profile).where(Profile.id == user_id)
        result = await db.execute(stmt)
        profile = result.scalar_one_or_none()

        if (
            profile is not None
            and profile.encrypted_byok
            and profile.byok_iv
            and profile.encrypted_dek
            and profile.dek_iv
        ):
            try:
                raw_dek = security_service.decrypt_with_kek(
                    encrypted_dek=profile.encrypted_dek,
                    iv=profile.dek_iv,
                    salt=profile.dek_salt,
                )
                byok_key = security_service.decrypt_user_data(
                    encrypted_data=profile.encrypted_byok,
                    iv=profile.byok_iv,
                    user_dek=raw_dek,
                )
                logger.info(
                    f"[AI] Using per-user BYOK client for user {str(user_id)[:8]}"
                )
                return genai.Client(api_key=byok_key)
            except Exception as e:
                logger.error(
                    f"[AI] BYOK decryption failed for user {str(user_id)[:8]} — "
                    f"falling back to server key: {e}"
                )
                return self._get_server_client()

        logger.info(
            f"[AI] No BYOK configured for user {str(user_id)[:8]} — using server key"
        )
        return self._get_server_client()

    async def transcribe_audio_verbatim(
        self,
        client: genai.Client,
        audio_bytes: bytes,
        mime_type: str,
    ) -> str:
        """
        Pass 1: High-fidelity verbatim transcription with fitness domain bias.

        Sends raw audio binary to gemini-3.1-flash-lite with a strict system prompt
        that prioritizes fitness/nutrition vocabulary for phonetically ambiguous audio.

        Args:
            client: The per-request resolved Gemini client (BYOK or server key).
            audio_bytes: Raw audio file bytes.
            mime_type: MIME type of the audio (e.g., 'audio/webm', 'audio/mp4').

        Returns:
            str: Verbatim transcribed text. Empty string if no speech detected.

        Raises:
            Exception: On API communication failure.
        """
        logger.info(
            f"[TRANSCRIBE] Pass 1 started — {len(audio_bytes)} bytes, mime: {mime_type}"
        )

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.MODEL_NAME,
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
                    system_instruction=self.TRANSCRIPTION_SYSTEM_PROMPT,
                    temperature=0.0,
                ),
            )

            transcript = response.text.strip()
            logger.info(f"[TRANSCRIBE] Pass 1 complete — '{transcript[:100]}'")
            return transcript

        except Exception as e:
            logger.error(f"[TRANSCRIBE] Pass 1 failed: {e}")
            raise

    async def parse_verbatim_text_to_json(
        self,
        client: genai.Client,
        transcript: str,
    ) -> dict[str, Any]:
        """
        Pass 2: Structured JSON extraction from verbatim text.

        Sends the transcript to Gemini with Structured Output configuration,
        forcing compliance against our Pydantic entity schema (NutritionEntry
        or WorkoutEntry).

        Args:
            client: The per-request resolved Gemini client (BYOK or server key).
            transcript: The verbatim text from Pass 1.

        Returns:
            dict: Parsed structured data matching either NutritionEntry or WorkoutEntry schema.

        Raises:
            ValueError: If response cannot be parsed into valid JSON.
        """
        logger.info(f"[PARSE] Pass 2 started — input: '{transcript[:100]}'")

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.MODEL_NAME,
                contents=f"Parse this fitness/health input into structured data:\n\n{transcript}",
                config=types.GenerateContentConfig(
                    system_instruction=self.STRUCTURED_PARSE_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ParsedEntry,
                    temperature=0.2,
                ),
            )

            result = json.loads(response.text)
            logger.info(f"[PARSE] Pass 2 complete — type: {result.get('entry_type')}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[PARSE] Pass 2 returned invalid JSON: {e}")
            raise ValueError(f"Gemini returned unparseable response: {e}")
        except Exception as e:
            logger.error(f"[PARSE] Pass 2 failed: {e}")
            raise

    async def run_ai_decision_validator(
        self,
        client: genai.Client,
        user_input: str,
        memory_chunk: str,
    ) -> bool:
        """
        Validates gray-zone semantic matches (0.25 <= distance <= 0.35).

        Prompts Gemini to determine if the user's active input logically
        references the retrieved semantic memory context.

        Args:
            client: The per-request resolved Gemini client (BYOK or server key).
            user_input: The user's current text input.
            memory_chunk: The retrieved memory context_chunk text.

        Returns:
            bool: True if memory is relevant, False otherwise.
        """
        logger.info("[VALIDATOR] Running AI decision validator for gray-zone match")

        try:
            prompt = (
                f"User's current input: \"{user_input}\"\n\n"
                f"Retrieved memory context: \"{memory_chunk}\"\n\n"
                "Is this memory relevant to the user's current input? "
                "Answer ONLY 'true' or 'false'."
            )

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.AI_DECISION_VALIDATOR_PROMPT,
                    temperature=0.0,
                ),
            )

            answer = response.text.strip().lower()
            is_relevant = answer == "true"
            logger.info(f"[VALIDATOR] Decision: {is_relevant} (raw: '{answer}')")
            return is_relevant

        except Exception as e:
            logger.error(f"[VALIDATOR] Decision validation failed: {e}")
            # On failure, reject the match (conservative approach)
            return False

    async def generate_embedding(
        self,
        client: genai.Client,
        text: str,
    ) -> list[float]:
        """
        Generates a 768-dimensional embedding vector using gemini-embedding-001.

        Args:
            client: The per-request resolved Gemini client (BYOK or server key).
            text: The text to embed.

        Returns:
            list[float]: 768-dimensional float vector.
        """
        try:
            response = await asyncio.to_thread(
                client.models.embed_content,
                model=self.EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.EMBEDDING_DIMENSIONS
                ),
            )
            embedding = response.embeddings[0].values
            logger.debug(f"[EMBEDDING] Generated {len(embedding)}-dim vector")
            return list(embedding)

        except Exception as e:
            logger.error(f"[EMBEDDING] Generation failed: {e}")
            raise


# =============================================================
# MemoryService — Hybrid Semantic RAG Engine
# =============================================================


class MemoryService:
    """
    Manages semantic memory retrieval and consolidation using pgvector.

    Implements the Hybrid Retrieval Rule:
        - distance < 0.25: Accept immediately
        - 0.25 <= distance <= 0.35: AI Decision Validator (gray zone)
        - distance > 0.35: Reject completely

    Uses HNSW index with cosine distance for fast approximate nearest neighbor search.

    All AI calls require a per-request `client` (BYOK or server key) threaded in
    by the caller.
    """

    # Retrieval thresholds
    ACCEPT_THRESHOLD = 0.25
    GRAY_ZONE_UPPER = 0.35
    # Consolidation threshold (memories closer than this are candidates for merging)
    CONSOLIDATION_THRESHOLD = 0.15
    # Maximum memories to retrieve per query
    MAX_RETRIEVAL_CANDIDATES = 10

    def __init__(self, ai_service: AIService) -> None:
        """
        Initialize MemoryService with a reference to AIService for embeddings
        and decision validation.

        Args:
            ai_service: The AIService instance for embedding generation and validation.
        """
        self.ai = ai_service

    async def search_semantic_memory(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        query_text: str,
        client: genai.Client,
    ) -> list[str]:
        """
        Performs hybrid semantic memory retrieval for a user query.

        1. Converts query_text to a 768-dim embedding via gemini-embedding-001.
        2. Queries semantic_memory using cosine distance (pgvector).
        3. Applies the Hybrid Retrieval Rule:
           - distance < 0.25 → accept immediately
           - 0.25 <= distance <= 0.35 → AI Decision Validator
           - distance > 0.35 → reject
        4. Updates last_accessed_at for accepted memories.
        5. Returns accepted memory texts for LLM context injection.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            query_text: The user's current input text to match against.
            client: The per-request resolved Gemini client (BYOK or server key).

        Returns:
            list[str]: Accepted memory context_chunk texts, ordered by relevance.
        """
        logger.info(f"[MEMORY] Searching semantic memory for user {str(user_id)[:8]}...")

        try:
            # Step 1: Generate query embedding
            query_embedding = await self.ai.generate_embedding(client, query_text)

            # Step 2: Vector similarity search using cosine distance
            # pgvector's cosine_distance returns values in [0, 2] where 0 = identical
            stmt = (
                select(
                    SemanticMemory,
                    SemanticMemory.embedding.cosine_distance(query_embedding).label(
                        "distance"
                    ),
                )
                .where(SemanticMemory.user_id == user_id)
                .order_by(
                    SemanticMemory.embedding.cosine_distance(query_embedding)
                )
                .limit(self.MAX_RETRIEVAL_CANDIDATES)
            )

            result = await db.execute(stmt)
            rows = result.all()

            if not rows:
                logger.info("[MEMORY] No memories found for user")
                return []

            # Step 3: Apply Hybrid Retrieval Rule
            accepted_memories: list[str] = []
            accepted_ids: list[uuid.UUID] = []

            for memory, distance in rows:
                if distance < self.ACCEPT_THRESHOLD:
                    # Immediate accept — high confidence match
                    accepted_memories.append(memory.context_chunk)
                    accepted_ids.append(memory.id)
                    logger.debug(
                        f"[MEMORY] ACCEPTED (d={distance:.4f}): "
                        f"'{memory.context_chunk[:50]}...'"
                    )

                elif distance <= self.GRAY_ZONE_UPPER:
                    # Gray zone — invoke AI Decision Validator
                    is_relevant = await self.ai.run_ai_decision_validator(
                        client=client,
                        user_input=query_text,
                        memory_chunk=memory.context_chunk,
                    )
                    if is_relevant:
                        accepted_memories.append(memory.context_chunk)
                        accepted_ids.append(memory.id)
                        logger.debug(
                            f"[MEMORY] ACCEPTED via validator (d={distance:.4f}): "
                            f"'{memory.context_chunk[:50]}...'"
                        )
                    else:
                        logger.debug(
                            f"[MEMORY] REJECTED by validator (d={distance:.4f}): "
                            f"'{memory.context_chunk[:50]}...'"
                        )

                else:
                    # Beyond gray zone — reject all remaining (ordered by distance)
                    logger.debug(
                        f"[MEMORY] REJECTED (d={distance:.4f}): "
                        f"'{memory.context_chunk[:50]}...'"
                    )
                    break  # All subsequent results will be further away

            # Step 4: Update last_accessed_at for accepted memories
            if accepted_ids:
                await db.execute(
                    update(SemanticMemory)
                    .where(SemanticMemory.id.in_(accepted_ids))
                    .values(last_accessed_at=datetime.now(timezone.utc))
                )
                await db.commit()

            logger.info(
                f"[MEMORY] Retrieved {len(accepted_memories)} memories "
                f"from {len(rows)} candidates"
            )
            return accepted_memories

        except Exception as e:
            logger.error(f"[MEMORY] Search failed: {e}")
            await db.rollback()
            return []

    async def store_memory(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        context_text: str,
        client: genai.Client,
    ) -> SemanticMemory:
        """
        Stores a new semantic memory with its embedding vector.

        Args:
            db: Async database session.
            user_id: The user's UUID.
            context_text: The text content to store and embed.
            client: The per-request resolved Gemini client (BYOK or server key).

        Returns:
            SemanticMemory: The created memory record.
        """
        logger.info(f"[MEMORY] Storing new memory for user {str(user_id)[:8]}...")

        embedding = await self.ai.generate_embedding(client, context_text)

        memory = SemanticMemory(
            id=uuid.uuid4(),
            user_id=user_id,
            context_chunk=context_text,
            embedding=embedding,
            importance_weight=1,
            last_accessed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        db.add(memory)
        await db.commit()
        await db.refresh(memory)

        logger.info(f"[MEMORY] Stored memory {str(memory.id)[:8]}")
        return memory

    async def reconcile_and_consolidate_memory(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        client: genai.Client,
    ) -> int:
        """
        Background maintenance routine for semantic memory consolidation.

        Detects memories with high similarity (distance < 0.15) belonging to the
        same user, prompts Gemini to reconcile contradictions, and consolidates
        into a single authoritative memory.

        Process:
            1. Scan all user memories pairwise for near-duplicates.
            2. For each cluster of similar memories, prompt AI to synthesize.
            3. Keep the newest record, update its content and increment importance_weight.
            4. Delete obsolete/conflicting records.

        Args:
            db: Async database session.
            user_id: The user's UUID.
            client: The per-request resolved Gemini client (BYOK or server key).

        Returns:
            int: Number of memories consolidated (deleted).
        """
        logger.info(
            f"[CONSOLIDATE] Starting memory reconciliation for user {str(user_id)[:8]}..."
        )

        consolidated_count = 0

        try:
            # Fetch all memories for this user
            stmt = (
                select(SemanticMemory)
                .where(SemanticMemory.user_id == user_id)
                .order_by(SemanticMemory.created_at.desc())
            )
            result = await db.execute(stmt)
            memories = list(result.scalars().all())

            if len(memories) < 2:
                logger.info("[CONSOLIDATE] Fewer than 2 memories — nothing to consolidate")
                return 0

            # Track which memory IDs have already been processed
            processed_ids: set[uuid.UUID] = set()

            for i, anchor in enumerate(memories):
                if anchor.id in processed_ids:
                    continue

                # Find near-duplicates for this anchor
                similar_stmt = (
                    select(
                        SemanticMemory,
                        SemanticMemory.embedding.cosine_distance(
                            anchor.embedding
                        ).label("distance"),
                    )
                    .where(
                        SemanticMemory.user_id == user_id,
                        SemanticMemory.id != anchor.id,
                        SemanticMemory.id.notin_(processed_ids),
                    )
                    .having(
                        SemanticMemory.embedding.cosine_distance(anchor.embedding)
                        < self.CONSOLIDATION_THRESHOLD
                    )
                    .order_by(
                        SemanticMemory.embedding.cosine_distance(anchor.embedding)
                    )
                )

                similar_result = await db.execute(similar_stmt)
                similar_rows = similar_result.all()

                if not similar_rows:
                    continue

                # Collect the cluster: anchor + similar memories
                cluster_texts = [anchor.context_chunk]
                obsolete_ids: list[uuid.UUID] = []

                for similar_memory, distance in similar_rows:
                    cluster_texts.append(similar_memory.context_chunk)
                    obsolete_ids.append(similar_memory.id)
                    processed_ids.add(similar_memory.id)

                # Prompt AI to reconcile the cluster
                reconciled_text = await self._reconcile_cluster(client, cluster_texts)

                if reconciled_text:
                    # Update the anchor (newest) with reconciled content
                    new_embedding = await self.ai.generate_embedding(client, reconciled_text)
                    new_weight = anchor.importance_weight + len(obsolete_ids)

                    await db.execute(
                        update(SemanticMemory)
                        .where(SemanticMemory.id == anchor.id)
                        .values(
                            context_chunk=reconciled_text,
                            embedding=new_embedding,
                            importance_weight=new_weight,
                            last_accessed_at=datetime.now(timezone.utc),
                        )
                    )

                    # Delete obsolete memories
                    await db.execute(
                        delete(SemanticMemory).where(
                            SemanticMemory.id.in_(obsolete_ids)
                        )
                    )

                    consolidated_count += len(obsolete_ids)
                    processed_ids.add(anchor.id)

                    logger.info(
                        f"[CONSOLIDATE] Merged {len(obsolete_ids)} memories into "
                        f"{str(anchor.id)[:8]} (weight: {new_weight})"
                    )

            await db.commit()
            logger.info(
                f"[CONSOLIDATE] Complete — consolidated {consolidated_count} memories"
            )
            return consolidated_count

        except Exception as e:
            logger.error(f"[CONSOLIDATE] Reconciliation failed: {e}")
            await db.rollback()
            return 0

    async def _reconcile_cluster(
        self,
        client: genai.Client,
        memory_texts: list[str],
    ) -> str | None:
        """
        Prompts Gemini to reconcile a cluster of similar/conflicting memories
        into a single authoritative statement.

        Args:
            client: The per-request resolved Gemini client (BYOK or server key).
            memory_texts: List of similar memory context_chunk texts.

        Returns:
            str | None: The reconciled text, or None if reconciliation fails.
        """
        try:
            numbered_memories = "\n".join(
                f"{i + 1}. {text}" for i, text in enumerate(memory_texts)
            )

            prompt = (
                "You are a memory reconciliation engine. The following memory entries "
                "belong to the same user and are semantically very similar. "
                "They may contain contradictions, outdated information, or redundancy.\n\n"
                f"Memory entries:\n{numbered_memories}\n\n"
                "TASK: Synthesize these into a SINGLE, concise, authoritative statement "
                "that preserves the most current and accurate information. "
                "If there is a contradiction (e.g., 'allergic to eggs' vs 'ate eggs'), "
                "prefer the NEWEST information (listed first) as the ground truth.\n\n"
                "Output ONLY the reconciled statement. No explanations or metadata."
            )

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.ai.MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                ),
            )

            reconciled = response.text.strip()
            logger.debug(f"[CONSOLIDATE] Reconciled: '{reconciled[:80]}...'")
            return reconciled

        except Exception as e:
            logger.error(f"[CONSOLIDATE] Reconciliation prompt failed: {e}")
            return None


# =============================================================
# Module-level singletons for dependency injection
# =============================================================

ai_service = AIService()
memory_service = MemoryService(ai_service=ai_service)
