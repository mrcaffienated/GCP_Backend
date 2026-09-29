from typing import Optional
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from datetime import date, datetime
import asyncpg
from fastapi import HTTPException, status

from models.pawn_model import CollateralType
from models.schemas import PawnCreate, PawnUpdate


def _to_decimal(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except Exception:
        return None


# ── Row → object assembly ───────────────────────────────────────────────────
# Repo functions hand back SimpleNamespace objects (not asyncpg.Record) so
# models/schemas.py's PawnOut.from_orm() and utils/interest_calc.py keep
# working completely unchanged via plain attribute access — only the data
# access layer changed, not the business logic that reads it.

def _sub_ns(row: asyncpg.Record) -> SimpleNamespace:
    return SimpleNamespace(**dict(row))


def row_to_pawn_ns(
    row: asyncpg.Record,
    aa_rows: list = (),
    pp_rows: list = (),
    ip_rows: list = (),
) -> SimpleNamespace:
    data = dict(row)
    data["collateral_type"] = CollateralType(data["collateral_type"])
    data["edit_history"] = data.get("edit_history") or []
    ns = SimpleNamespace(**data)
    ns.additional_amounts = [_sub_ns(r) for r in aa_rows]
    ns.prepayments = [_sub_ns(r) for r in pp_rows]
    ns.interest_payments = [_sub_ns(r) for r in ip_rows]
    return ns


async def _load_sub_items(conn: asyncpg.Connection, pawn_ids: list) -> tuple:
    if not pawn_ids:
        return {}, {}, {}

    def _group(rows):
        out: dict = {}
        for r in rows:
            out.setdefault(r["pawn_id"], []).append(r)
        return out

    aa = await conn.fetch(
        "SELECT * FROM additional_amounts WHERE pawn_id = ANY($1::uuid[]) ORDER BY date, created_at",
        pawn_ids,
    )
    pp = await conn.fetch(
        "SELECT * FROM prepayments WHERE pawn_id = ANY($1::uuid[]) ORDER BY date, created_at",
        pawn_ids,
    )
    ip = await conn.fetch(
        "SELECT * FROM interest_payments WHERE pawn_id = ANY($1::uuid[]) ORDER BY date, created_at",
        pawn_ids,
    )
    return _group(aa), _group(pp), _group(ip)


async def get_next_serial(conn: asyncpg.Connection) -> int:
    max_serial = await conn.fetchval("SELECT MAX(serial_no) FROM pawns")
    return (max_serial or 0) + 1


async def check_serial_unique(
    conn: asyncpg.Connection,
    serial_no: int,
    series: Optional[str],
    exclude_id: Optional[UUID] = None,
) -> None:
    normalized_series = series.strip().upper() if series and series.strip() else None
    if exclude_id:
        exists = await conn.fetchval(
            "SELECT 1 FROM pawns WHERE serial_no = $1 AND series IS NOT DISTINCT FROM $2 AND id != $3",
            serial_no, normalized_series, exclude_id,
        )
    else:
        exists = await conn.fetchval(
            "SELECT 1 FROM pawns WHERE serial_no = $1 AND series IS NOT DISTINCT FROM $2",
            serial_no, normalized_series,
        )
    if exists:
        label = f"{normalized_series}{serial_no}" if normalized_series else str(serial_no)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Entry #{label} already exists. Please use a different serial number.",
        )


# ── Single-pawn loader ─────────────────────────────────────────────────────

async def get_pawn_by_id(conn: asyncpg.Connection, pawn_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow("SELECT * FROM pawns WHERE id = $1", pawn_id)
    if not row:
        return None
    aa, pp, ip = await _load_sub_items(conn, [pawn_id])
    return row_to_pawn_ns(row, aa.get(pawn_id, []), pp.get(pawn_id, []), ip.get(pawn_id, []))


# ── List (server-side filtering + pagination) ──────────────────────────────

async def get_all_pawns(
    conn: asyncpg.Connection,
    search: Optional[str] = None,
    collateral_type: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    serial_from: Optional[int] = None,
    serial_to: Optional[int] = None,
    skip: int = 0,
    limit: Optional[int] = None,
    light: bool = False,
) -> tuple:
    clauses = []
    params: list = []

    def add(clause: str, value) -> None:
        params.append(value)
        clauses.append(clause.format(n=len(params)))

    if serial_from is not None:
        add("serial_no >= ${n}", serial_from)
    if serial_to is not None:
        add("serial_no <= ${n}", serial_to)
    if search:
        params.append(f"%{search}%")
        n = len(params)
        clauses.append(
            f"(borrower_name ILIKE ${n} OR relative_name ILIKE ${n} OR item_description ILIKE ${n} "
            f"OR phone ILIKE ${n} OR address ILIKE ${n} OR (COALESCE(series, '') || serial_no::text) ILIKE ${n})"
        )
    if collateral_type:
        add("collateral_type = ${n}", collateral_type)
    if status == "active":
        clauses.append("is_released = false AND is_cancelled = false")
    elif status == "released":
        clauses.append("is_released = true AND is_sold = false AND is_cancelled = false")
    elif status == "sold":
        clauses.append("is_sold = true AND is_cancelled = false")
    elif status == "cancelled":
        clauses.append("is_cancelled = true")
    if date_from:
        add("entry_date >= ${n}", date_from)
    if date_to:
        add("entry_date <= ${n}", date_to)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = await conn.fetchval(f"SELECT COUNT(*) FROM pawns {where_sql}", *params)

    list_params = list(params)
    list_params.append(skip)
    query = f"SELECT * FROM pawns {where_sql} ORDER BY serial_no DESC OFFSET ${len(list_params)}"
    if limit is not None:
        list_params.append(limit)
        query += f" LIMIT ${len(list_params)}"
    rows = await conn.fetch(query, *list_params)

    if light:
        return [row_to_pawn_ns(r) for r in rows], (total or 0)

    ids = [r["id"] for r in rows]
    aa, pp, ip = await _load_sub_items(conn, ids)
    pawns = [
        row_to_pawn_ns(r, aa.get(r["id"], []), pp.get(r["id"], []), ip.get(r["id"], []))
        for r in rows
    ]
    return pawns, (total or 0)


# ── Create ──────────────────────────────────────────────────────────────────

_PAWN_INSERT_SQL = """
INSERT INTO pawns (
    id, serial_no, series, entry_date, borrower_name, relative_name, phone, aadhar, address,
    item_description, item_weight, item_weight_gold, item_weight_silver, collateral_type,
    loan_amount, interest_rate, loan_amount_gold, interest_rate_gold, loan_amount_silver,
    interest_rate_silver, is_released, is_cancelled, created_by_username, created_by_name,
    edit_history, created_at, updated_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
    $21, $22, $23, $24, $25, $26, $27
)
RETURNING *
"""


async def create_pawn(
    conn: asyncpg.Connection,
    data: PawnCreate,
    created_by_username: str,
    created_by_name: str,
) -> SimpleNamespace:
    # App-level uniqueness check is REQUIRED: the (serial_no, series) UNIQUE
    # constraint does NOT catch duplicates when series is NULL (Postgres treats
    # NULLs as distinct), and most entries have no series. The UniqueViolation
    # catch below is a backstop for the series-present case.
    await check_serial_unique(conn, data.serial_no, data.series)

    is_cancelled = bool(getattr(data, "is_cancelled", False))
    now = datetime.utcnow()
    pawn_id = uuid4()

    try:
        row = await conn.fetchrow(
            _PAWN_INSERT_SQL,
            pawn_id,
            data.serial_no,
            data.series or None,
            data.entry_date or date.today(),
            data.borrower_name,
            data.relative_name,
            data.phone or None,
            data.aadhar or None,
            data.address or None,
            data.item_description,
            _to_decimal(data.item_weight),
            _to_decimal(data.item_weight_gold),
            _to_decimal(data.item_weight_silver),
            data.collateral_type.value,
            _to_decimal(data.loan_amount) or Decimal("0"),
            _to_decimal(data.interest_rate),
            _to_decimal(data.loan_amount_gold),
            _to_decimal(data.interest_rate_gold),
            _to_decimal(data.loan_amount_silver),
            _to_decimal(data.interest_rate_silver),
            is_cancelled,  # is_released — a bill can be created already cancelled/voided
            is_cancelled,
            created_by_username,
            created_by_name,
            [],
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
            detail=f"Entry #{label} already exists. Please use a different serial number.",
        )
    return row_to_pawn_ns(row)


# ── Update ──────────────────────────────────────────────────────────────────

_DECIMAL_FIELDS = {
    "item_weight", "item_weight_gold", "item_weight_silver",
    "loan_amount", "interest_rate",
    "loan_amount_gold", "interest_rate_gold",
    "loan_amount_silver", "interest_rate_silver",
}


async def update_pawn(conn: asyncpg.Connection, pawn_id: UUID, data: PawnUpdate) -> Optional[SimpleNamespace]:
    existing = await conn.fetchrow("SELECT edit_history FROM pawns WHERE id = $1", pawn_id)
    if not existing:
        return None

    updates = data.model_dump(exclude_unset=True)
    edit_entry = updates.pop("edit_entry", None)
    if edit_entry:
        history = list(existing["edit_history"] or [])
        history.append(edit_entry)
        updates["edit_history"] = history

    if "collateral_type" in updates and updates["collateral_type"] is not None:
        ct = updates["collateral_type"]
        updates["collateral_type"] = ct.value if hasattr(ct, "value") else ct

    for field in _DECIMAL_FIELDS:
        if field in updates:
            converted = _to_decimal(updates[field])
            if field == "loan_amount" and converted is None:
                converted = Decimal("0")
            updates[field] = converted

    if not updates:
        return await get_pawn_by_id(conn, pawn_id)

    set_parts = []
    params: list = []
    for col, val in updates.items():
        params.append(val)
        set_parts.append(f"{col} = ${len(params)}")
    params.append(datetime.utcnow())
    set_parts.append(f"updated_at = ${len(params)}")
    params.append(pawn_id)

    await conn.execute(
        f"UPDATE pawns SET {', '.join(set_parts)} WHERE id = ${len(params)}",
        *params,
    )
    return await get_pawn_by_id(conn, pawn_id)


# ── Status mutations ────────────────────────────────────────────────────────

async def release_pawn(
    conn: asyncpg.Connection,
    pawn_id: UUID,
    released_date: Optional[date] = None,
    actual_release_amount: Optional[float] = None,
) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        """UPDATE pawns SET is_released = true, released_date = $2,
               actual_release_amount = COALESCE($3, actual_release_amount),
               updated_at = $4
           WHERE id = $1 RETURNING id""",
        pawn_id, released_date or date.today(), _to_decimal(actual_release_amount), datetime.utcnow(),
    )
    if not row:
        return None
    return await get_pawn_by_id(conn, pawn_id)


async def mark_active(conn: asyncpg.Connection, pawn_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        """UPDATE pawns SET is_released = false, released_date = NULL,
               is_sold = false, sold_date = NULL, is_cancelled = false, updated_at = $2
           WHERE id = $1 RETURNING id""",
        pawn_id, datetime.utcnow(),
    )
    if not row:
        return None
    return await get_pawn_by_id(conn, pawn_id)


async def cancel_pawn(conn: asyncpg.Connection, pawn_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow(
        """UPDATE pawns SET is_cancelled = true, is_released = true, updated_at = $2
           WHERE id = $1 RETURNING id""",
        pawn_id, datetime.utcnow(),
    )
    if not row:
        return None
    return await get_pawn_by_id(conn, pawn_id)


async def mark_sold(
    conn: asyncpg.Connection, pawn_id: UUID, sold_date: Optional[date] = None
) -> Optional[SimpleNamespace]:
    sd = sold_date or date.today()
    row = await conn.fetchrow(
        """UPDATE pawns SET is_sold = true, sold_date = $2, is_released = true,
               released_date = $2, updated_at = $3
           WHERE id = $1 RETURNING id""",
        pawn_id, sd, datetime.utcnow(),
    )
    if not row:
        return None
    return await get_pawn_by_id(conn, pawn_id)


# ── Renewal ─────────────────────────────────────────────────────────────────

async def renew_pawn(
    conn: asyncpg.Connection,
    old_pawn_id: UUID,
    new_data: "PawnCreate",
    created_by_username: str,
    created_by_name: str,
) -> tuple:
    old_exists = await conn.fetchval("SELECT 1 FROM pawns WHERE id = $1", old_pawn_id)
    if not old_exists:
        return None, None

    await check_serial_unique(conn, new_data.serial_no, new_data.series)

    now = datetime.utcnow()
    new_pawn_id = uuid4()

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO pawns (
                id, serial_no, series, entry_date, borrower_name, relative_name, phone, aadhar, address,
                item_description, item_weight, item_weight_gold, item_weight_silver, collateral_type,
                loan_amount, interest_rate, loan_amount_gold, interest_rate_gold, loan_amount_silver,
                interest_rate_silver, is_released, is_cancelled, created_by_username, created_by_name,
                edit_history, created_at, updated_at, renewed_from
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19,
                $20, false, false, $21, $22, $23, $24, $25, $26
            )
            """,
            new_pawn_id,
            new_data.serial_no,
            new_data.series or None,
            new_data.entry_date or date.today(),
            new_data.borrower_name,
            new_data.relative_name,
            new_data.phone or None,
            new_data.aadhar or None,
            new_data.address or None,
            new_data.item_description,
            _to_decimal(new_data.item_weight),
            _to_decimal(new_data.item_weight_gold),
            _to_decimal(new_data.item_weight_silver),
            new_data.collateral_type.value,
            _to_decimal(new_data.loan_amount) or Decimal("0"),
            _to_decimal(new_data.interest_rate),
            _to_decimal(new_data.loan_amount_gold),
            _to_decimal(new_data.interest_rate_gold),
            _to_decimal(new_data.loan_amount_silver),
            _to_decimal(new_data.interest_rate_silver),
            created_by_username,
            created_by_name,
            [],
            now,
            now,
            old_pawn_id,
        )

        await conn.execute(
            """UPDATE pawns SET is_released = true, released_date = $2, renewed = true,
                   renewed_to = $3, updated_at = $4
               WHERE id = $1""",
            old_pawn_id, date.today(), new_pawn_id, now,
        )

    old_out = await get_pawn_by_id(conn, old_pawn_id)
    new_out = await get_pawn_by_id(conn, new_pawn_id)
    return old_out, new_out


async def link_renewal(
    conn: asyncpg.Connection,
    current_pawn_id: UUID,
    old_serial_no: int,
    old_series: Optional[str],
) -> tuple:
    normalized_series = old_series.strip().upper() if old_series and old_series.strip() else None
    old = await conn.fetchrow(
        "SELECT id, is_released, entry_date FROM pawns WHERE serial_no = $1 AND series IS NOT DISTINCT FROM $2",
        old_serial_no, normalized_series,
    )
    if not old:
        return None, None

    current = await conn.fetchrow("SELECT id, entry_date FROM pawns WHERE id = $1", current_pawn_id)
    if not current:
        return None, None

    release_date = current["entry_date"] or date.today()
    now = datetime.utcnow()

    async with conn.transaction():
        await conn.execute(
            "UPDATE pawns SET renewed_from = $2, updated_at = $3 WHERE id = $1",
            current_pawn_id, old["id"], now,
        )
        if old["is_released"]:
            await conn.execute(
                "UPDATE pawns SET renewed_to = $2, renewed = true, updated_at = $3 WHERE id = $1",
                old["id"], current_pawn_id, now,
            )
        else:
            await conn.execute(
                """UPDATE pawns SET renewed_to = $2, renewed = true, is_released = true,
                       released_date = $3, updated_at = $4
                   WHERE id = $1""",
                old["id"], current_pawn_id, release_date, now,
            )

    old_out = await get_pawn_by_id(conn, old["id"])
    current_out = await get_pawn_by_id(conn, current_pawn_id)
    return old_out, current_out


# ── Sub-items ────────────────────────────────────────────────────────────────

async def add_additional_amount(
    conn: asyncpg.Connection, pawn_id: UUID, data: "AdditionalAmountCreate"
) -> Optional[SimpleNamespace]:
    try:
        await conn.execute(
            "INSERT INTO additional_amounts (id, pawn_id, amount, date, interest_rate, note, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            uuid4(), pawn_id, data.amount, data.date, data.interest_rate, data.note or "", datetime.utcnow(),
        )
    except asyncpg.ForeignKeyViolationError:
        return None
    return await get_pawn_by_id(conn, pawn_id)


async def add_prepayment(
    conn: asyncpg.Connection, pawn_id: UUID, data: "PrepaymentCreate"
) -> Optional[SimpleNamespace]:
    try:
        await conn.execute(
            "INSERT INTO prepayments (id, pawn_id, amount, date, note, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            uuid4(), pawn_id, data.amount, data.date, data.note or "", datetime.utcnow(),
        )
    except asyncpg.ForeignKeyViolationError:
        return None
    return await get_pawn_by_id(conn, pawn_id)


async def add_interest_payment(
    conn: asyncpg.Connection, pawn_id: UUID, data: "InterestPaymentCreate"
) -> Optional[SimpleNamespace]:
    try:
        await conn.execute(
            "INSERT INTO interest_payments (id, pawn_id, amount, date, note, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            uuid4(), pawn_id, data.amount, data.date, data.note or "", datetime.utcnow(),
        )
    except asyncpg.ForeignKeyViolationError:
        return None
    return await get_pawn_by_id(conn, pawn_id)


async def delete_additional_amount(conn: asyncpg.Connection, item_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow("DELETE FROM additional_amounts WHERE id = $1 RETURNING pawn_id", item_id)
    if not row:
        return None
    return await get_pawn_by_id(conn, row["pawn_id"])


async def delete_prepayment(conn: asyncpg.Connection, item_id: UUID) -> Optional[SimpleNamespace]:
    row = await conn.fetchrow("DELETE FROM prepayments WHERE id = $1 RETURNING pawn_id", item_id)
    if not row:
        return None
    return await get_pawn_by_id(conn, row["pawn_id"])
