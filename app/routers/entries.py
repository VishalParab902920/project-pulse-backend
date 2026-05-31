"""
Project Pulse — Entries Router
GET /api/v1/entries/pending — Fetch pending review queue
PATCH /api/v1/entries/{entry_id} — Update entry status or parsed_data
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entry import Entry
from app.schemas.entries import EntryResponse, EntryPatchRequest, EntryPatchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["entries"])

# Mock user_id until Auth is integrated
MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.get("/entries/pending", response_model=list[EntryResponse])
async def get_pending_entries(db: Session = Depends(get_db)):
    """Fetch all pending entries for the current user, sorted by creation time."""
    entries = (
        db.query(Entry)
        .filter(Entry.user_id == UUID(MOCK_USER_ID))
        .filter(Entry.status == "pending")
        .order_by(desc(Entry.created_at))
        .all()
    )

    return [
        EntryResponse(
            id=e.id,
            user_id=e.user_id,
            type=e.type,
            status=e.status,
            raw_input=e.raw_input,
            media_path=e.media_path,
            parsed_data=e.parsed_data,
            confidence_score=e.confidence_score,
            created_at=e.created_at.isoformat() if e.created_at else None,
            occurred_at=e.occurred_at.isoformat() if e.occurred_at else None,
        )
        for e in entries
    ]


@router.get("/entries/pending/count")
async def get_pending_count(db: Session = Depends(get_db)):
    """Return the count of pending entries for badge display."""
    count = (
        db.query(Entry)
        .filter(Entry.user_id == UUID(MOCK_USER_ID))
        .filter(Entry.status == "pending")
        .count()
    )
    return {"count": count}


@router.patch("/entries/{entry_id}", response_model=EntryPatchResponse)
async def patch_entry(
    entry_id: UUID,
    request: EntryPatchRequest,
    db: Session = Depends(get_db),
):
    """Update an entry's status or parsed_data."""
    entry = (
        db.query(Entry)
        .filter(Entry.id == entry_id)
        .filter(Entry.user_id == UUID(MOCK_USER_ID))
        .first()
    )

    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if request.status is not None:
        if request.status not in ("confirmed", "assumed", "pending"):
            raise HTTPException(status_code=400, detail="Invalid status value")
        entry.status = request.status

    if request.parsed_data is not None:
        entry.parsed_data = request.parsed_data

    db.commit()
    db.refresh(entry)

    return EntryPatchResponse(
        id=entry.id,
        user_id=entry.user_id,
        type=entry.type,
        status=entry.status,
        raw_input=entry.raw_input,
        parsed_data=entry.parsed_data,
        confidence_score=entry.confidence_score,
    )
