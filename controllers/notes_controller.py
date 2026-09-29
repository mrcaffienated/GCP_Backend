import uuid as _uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
import asyncpg
from pydantic import BaseModel
from datetime import date

from db.database import get_db
from utils.jwt_utils import require_boss

router = APIRouter()


class NoteIn(BaseModel):
    note_date: date
    content: str


class NoteOut(BaseModel):
    id: str
    note_date: date
    content: str
    created_at: str

    model_config = {"from_attributes": True}


def _to_out(row: asyncpg.Record) -> NoteOut:
    return NoteOut(
        id=str(row["id"]),
        note_date=row["note_date"],
        content=row["content"],
        created_at=row["created_at"].isoformat(),
    )


@router.get("/", response_model=list[NoteOut])
async def list_notes(
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    rows = await db.fetch(
        "SELECT * FROM boss_notes ORDER BY note_date DESC, created_at DESC"
    )
    return [_to_out(n) for n in rows]


@router.post("/", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteIn,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    from datetime import datetime
    row = await db.fetchrow(
        """INSERT INTO boss_notes (id, note_date, content, created_at)
           VALUES ($1, $2, $3, $4) RETURNING *""",
        _uuid.uuid4(), body.note_date, body.content.strip(), datetime.utcnow(),
    )
    return _to_out(row)


class NoteUpdate(BaseModel):
    note_date: Optional[date] = None
    content: Optional[str] = None


@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str,
    body: NoteUpdate,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    updates = {}
    if body.note_date is not None:
        updates["note_date"] = body.note_date
    if body.content is not None:
        updates["content"] = body.content.strip()
    if not updates:
        row = await db.fetchrow("SELECT * FROM boss_notes WHERE id = $1", _uuid.UUID(note_id))
        if not row:
            raise HTTPException(status_code=404, detail="Note not found")
        return _to_out(row)

    set_parts = []
    params: list = []
    for col, val in updates.items():
        params.append(val)
        set_parts.append(f"{col} = ${len(params)}")
    params.append(_uuid.UUID(note_id))

    row = await db.fetchrow(
        f"UPDATE boss_notes SET {', '.join(set_parts)} WHERE id = ${len(params)} RETURNING *",
        *params,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Note not found")
    return _to_out(row)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    result = await db.execute("DELETE FROM boss_notes WHERE id = $1", _uuid.UUID(note_id))
    if result != "DELETE 1":
        raise HTTPException(status_code=404, detail="Note not found")
