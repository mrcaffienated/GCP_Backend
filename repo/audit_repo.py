import asyncio
from typing import Optional
from uuid import uuid4
from datetime import datetime
import asyncpg

from db.database import open_connection


async def _write_audit(action_type: str, user_id: Optional[str], metadata: Optional[str]) -> None:
    try:
        async with open_connection() as conn:
            await conn.execute(
                "INSERT INTO audit_logs (id, timestamp, action_type, user_id, details) "
                "VALUES ($1, $2, $3, $4, $5)",
                uuid4(), datetime.utcnow(), action_type, user_id, metadata,
            )
    except Exception:
        pass  # Audit log is non-critical — never let it break the response


def log_action_bg(
    action_type: str,
    user_id: Optional[str] = None,
    metadata: Optional[str] = None,
) -> None:
    """Fire-and-forget: schedules the audit write as a background asyncio task
    so the calling request doesn't wait on it.

    Note (Cloudflare Workers): a request-scoped isolate may be torn down as
    soon as the HTTP response is sent, which can cut this off before it
    completes — same non-critical trade-off as before (it never blocks or
    fails the response either way), just worth knowing in that environment.
    """
    asyncio.ensure_future(_write_audit(action_type, user_id, metadata))


async def log_action(
    conn: asyncpg.Connection,
    action_type: str,
    user_id: Optional[str] = None,
    metadata: Optional[str] = None,
) -> None:
    """Blocking audit write on the caller's own connection (startup/seeding, login)."""
    await conn.execute(
        "INSERT INTO audit_logs (id, timestamp, action_type, user_id, details) "
        "VALUES ($1, $2, $3, $4, $5)",
        uuid4(), datetime.utcnow(), action_type, user_id, metadata,
    )
