"""
Project Pulse — Memory Router
POST /api/v1/memory        — Store a new semantic memory with embedding.
GET  /api/v1/memory/search — Vector similarity search.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.memory import MemoryCreateRequest, MemoryResponse, MemorySearchResult
from app.services.gemini import get_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["memory"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.post("/memory", response_model=MemoryResponse)
async def create_memory(request: MemoryCreateRequest, db: Session = Depends(get_db)):
    """
    Store a new semantic memory with its vector embedding.
    The embedding is generated from the label text.
    """
    try:
        # Generate embedding for the label
        embedding = await get_embedding(request.label)

        # Convert embedding to PostgreSQL vector format
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        # Insert into user_memory (upsert on label conflict)
        import json
        content_json = json.dumps(request.content)

        result = db.execute(
            text("""
                INSERT INTO user_memory (user_id, category, label, content, content_embedding)
                VALUES (:user_id, :category, :label, cast(:content as jsonb), cast(:embedding as vector))
                ON CONFLICT (user_id, lower(label))
                DO UPDATE SET content = cast(:content as jsonb), content_embedding = cast(:embedding as vector), updated_at = NOW()
                RETURNING id, category, label, content
            """),
            {
                "user_id": MOCK_USER_ID,
                "category": request.category,
                "label": request.label,
                "content": content_json,
                "embedding": embedding_str,
            },
        ).mappings().fetchone()

        db.commit()

        if not result:
            raise HTTPException(status_code=500, detail="Failed to insert memory")

        logger.info(f"[MEMORY] Stored: '{request.label}' ({request.category})")

        return MemoryResponse(
            id=result["id"],
            category=result["category"],
            label=result["label"],
            content=result["content"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[MEMORY] Create error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to store memory: {str(e)}")


@router.get("/memory/search", response_model=list[MemorySearchResult])
async def search_memory(
    q: str = Query(..., min_length=1, description="Search query text"),
    limit: int = Query(3, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """
    Perform a vector similarity search on the user's semantic memory.
    Returns the closest matches ranked by cosine distance.
    """
    try:
        # Generate embedding for the search query
        query_embedding = await get_embedding(q)
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        results = db.execute(
            text("""
                SELECT label, category, content,
                       content_embedding <=> cast(:embedding as vector) AS distance
                FROM user_memory
                WHERE user_id = :user_id
                ORDER BY content_embedding <=> cast(:embedding as vector)
                LIMIT :limit
            """),
            {
                "user_id": MOCK_USER_ID,
                "embedding": embedding_str,
                "limit": limit,
            },
        ).mappings().fetchall()

        return [
            MemorySearchResult(
                label=r["label"],
                category=r["category"],
                content=r["content"],
                distance=float(r["distance"]),
            )
            for r in results
        ]

    except Exception as e:
        logger.error(f"[MEMORY] Search error: {e}")
        raise HTTPException(status_code=500, detail=f"Memory search failed: {str(e)}")
