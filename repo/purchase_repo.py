from typing import Optional
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from datetime import date, datetime
import asyncpg
from fastapi import HTTPException, status

from models.pawn_model import CollateralType
from models.schemas import PurchaseCreate, PurchaseUpdate


def _to_decimal(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except Exception:
        return None


def _row_to_ns(row: asyncpg.Record) -> SimpleNamespace:
    data = dict(row)
    data["metal_type"] = CollateralType(data["metal_type"])
    return SimpleNamespace(**data)


async def get_next_serial(conn: asyncpg.Connection) -> int:
    max_serial = await conn.fetchval("SELECT MAX(serial_no) FROM purchases")
    return (max_serial or 0) + 1


async def check_serial_unique(
    conn: asyncpg.Connection, serial_no: int, series: Optional[str]
) -> None:
    normalized_series = series.strip().upper() if series and series.strip() else None
    exists = await conn.fetchval(
        "SELECT 1 FROM purchases WHERE serial_no = $1 AND series IS NOT DISTINCT FROM $2",
        serial_no, normalized_series,
    )
    if exists:
        label = f"{normalized_series}{serial_no}" if normalized_series else str(serial_no)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Form #{label} already exists. Please use a different form number.",
        )


async def get_all_purchases(
    conn: asyncpg.Connection,
    search: Optional[str] = None,
    metal_type: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: Optional[int] = None,
) -> tuple:
    clauses = []
    params: list = []

    def add(clause: str, value) -> None:
        params.append(value)
        clauses.append(clause.format(n=len(params)))

    if search:
        params.append(f"%{search}%")
        n = len(params)
        clauses.append(
            f"(seller_name ILIKE ${n} OR relative_name ILIKE ${n} OR item_description ILIKE ${n} "
            f"OR phone ILIKE ${n} OR address ILIKE ${n} OR aadhar ILIKE ${n} "
            f"OR (COALESCE(series, '') || serial_no::text) ILIKE ${n})"
        )
    if metal_type:
        add("metal_type = ${n}", metal_type)
    if date_from:
        add("purchase_date >= ${n}", date_from)
    if date_to:
        add("purchase_date <= ${n}", date_to)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = await conn.fetchval(f"SELECT COUNT(*) FROM purchases {where_sql}", *params)

    query = f"SELECT * FROM purchases {where_sql} ORDER BY serial_no DESC"
    if limit is not None:
        query += f" LIMIT {limit}"
    rows = await conn.fetch(query, *params)
    return [_row_to_ns(r) for r in rows], (total or 0)


async def create_purchase(
    conn: asyncpg.Connection,
    data: PurchaseCreate,
    created_by_username: str,
    created_by_name: str,
) -> SimpleNamespace:
    # App-level check for the friendly message (NULL series isn't covered by the
    # constraint); the partial unique index + UniqueViolation catch is the backstop.
    await check_serial_unique(conn, data.serial_no, data.series)
    now = datetime.utcnow()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO purchases (
                id, serial_no, series, purchase_date, seller_name, relative_name, phone, aadhar,
                address, item_description, item_weight, metal_type, amount_paid, notes,
                created_by_username, created_by_name, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
            )
            RETURNING *
            """,
            uuid4(),
            data.serial_no,
            data.series.strip().upper() if data.series and data.series.strip() else None,
            data.purchase_date or date.today(),
            data.seller_name,
            data.relative_name or None,
            data.phone or None,
            data.aadhar or None,
            data.address or None,
            data.item_description,
            _to_decimal(data.item_weight),
            data.metal_type.value,
            _to_decimal(data.amount_paid) or Decimal("0"),
            data.notes or None,
            created_by_username,
            created_by_name,
            now,
            now,
        )
    except asyncpg.UniqueViolationError:
        label = (
            f"{data.series.strip().upper()}{data.serial_no}"
            if data.series and data.series.strip() else str(data.serial_no)
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Form #{label} already exists. Please use a different form number.",
        )
    return _row_to_ns(row)


_DECIMAL_FIELDS = {"item_weight", "amount_paid"}


async def update_purchase(
    conn: asyncpg.Connection, purchase_id: UUID, data: PurchaseUpdate
) -> Optional[SimpleNamespace]:
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        row = await conn.fetchrow("SELECT * FROM purchases WHERE id = $1", purchase_id)
        return _row_to_ns(row) if row else None

    if "metal_type" in updates and updates["metal_type"] is not None:
        mt = updates["metal_type"]
        updates["metal_type"] = mt.value if hasattr(mt, "value") else mt

    for field in _DECIMAL_FIELDS:
        if field in updates:
            converted = _to_decimal(updates[field])
            if field == "amount_paid" and converted is None:
                converted = Decimal("0")
            updates[field] = converted

    set_parts = []
    params: list = []
    for col, val in updates.items():
        params.append(val)
        set_parts.append(f"{col} = ${len(params)}")
    params.append(datetime.utcnow())
    set_parts.append(f"updated_at = ${len(params)}")
    params.append(purchase_id)

    row = await conn.fetchrow(
        f"UPDATE purchases SET {', '.join(set_parts)} WHERE id = ${len(params)} RETURNING *",
        *params,
    )
    return _row_to_ns(row) if row else None


async def delete_purchase(conn: asyncpg.Connection, purchase_id: UUID) -> bool:
    result = await conn.execute("DELETE FROM purchases WHERE id = $1", purchase_id)
    return result == "DELETE 1"
