from typing import List, Optional
from types import SimpleNamespace
from uuid import UUID
from datetime import datetime
import asyncpg

from models.pawn_model import UserRole, PwdChangeStatus
from services.auth_service import hash_password


def _row_to_ns(row: Optional[asyncpg.Record]) -> Optional[SimpleNamespace]:
    if row is None:
        return None
    data = dict(row)
    data["role"] = UserRole(data["role"])
    data["pwd_change_status"] = (
        PwdChangeStatus(data["pwd_change_status"]) if data.get("pwd_change_status") else None
    )
    return SimpleNamespace(**data)


async def get_all_employees(conn: asyncpg.Connection) -> List[SimpleNamespace]:
    rows = await conn.fetch(
        "SELECT * FROM users WHERE role = $1 ORDER BY created_at", UserRole.employee.value
    )
    return [_row_to_ns(r) for r in rows]


async def get_user_by_id(conn: asyncpg.Connection, user_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return _row_to_ns(row)


async def get_user_by_username(conn: asyncpg.Connection, username: str) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow("SELECT * FROM users WHERE username = $1", username)
    return _row_to_ns(row)


async def create_employee(
    conn: asyncpg.Connection, username: str, password: str, name: str
) -> SimpleNamespace:
    from uuid import uuid4
    row = await conn.fetchrow(
        """INSERT INTO users (id, username, password_hash, name, role, is_active, created_at)
           VALUES ($1, $2, $3, $4, $5, true, $6)
           RETURNING *""",
        uuid4(), username, hash_password(password), name, UserRole.employee.value, datetime.utcnow(),
    )
    return _row_to_ns(row)


async def deactivate_employee(conn: asyncpg.Connection, user_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        "UPDATE users SET is_active = false WHERE id = $1 RETURNING *", user_id
    )
    return _row_to_ns(row)


async def request_password_change(conn: asyncpg.Connection, user_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        """UPDATE users SET pwd_change_status = $2, pwd_change_requested_at = $3
           WHERE id = $1 RETURNING *""",
        user_id, PwdChangeStatus.pending.value, datetime.utcnow(),
    )
    return _row_to_ns(row)


async def cancel_password_request(conn: asyncpg.Connection, user_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        """UPDATE users SET pwd_change_status = NULL, pwd_change_requested_at = NULL
           WHERE id = $1 RETURNING *""",
        user_id,
    )
    return _row_to_ns(row)


async def approve_password_change(conn: asyncpg.Connection, user_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        "UPDATE users SET pwd_change_status = $2 WHERE id = $1 RETURNING *",
        user_id, PwdChangeStatus.approved.value,
    )
    return _row_to_ns(row)


async def deny_password_change(conn: asyncpg.Connection, user_id: UUID) -> Optional[SimpleNamespace]:
    return await cancel_password_request(conn, user_id)


async def set_new_password(
    conn: asyncpg.Connection, user_id: UUID, new_password: str
) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        """UPDATE users SET password_hash = $2, pwd_change_status = NULL, pwd_change_requested_at = NULL
           WHERE id = $1 RETURNING *""",
        user_id, hash_password(new_password),
    )
    return _row_to_ns(row)


async def change_own_password(
    conn: asyncpg.Connection, user_id: UUID, new_password: str
) -> Optional[SimpleNamespace]:
    return await set_new_password(conn, user_id, new_password)
