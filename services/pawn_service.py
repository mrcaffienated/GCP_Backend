from typing import Optional
from uuid import UUID
from datetime import date
from io import BytesIO
import asyncpg
import pandas as pd

from models.schemas import (
    PawnCreate, PawnUpdate, PawnOut, ReleaseRequest, RenewRequest,
    AdditionalAmountCreate, PrepaymentCreate, InterestPaymentCreate,
)
from repo import pawn_repo, audit_repo


async def add_pawn(
    db: asyncpg.Connection,
    data: PawnCreate,
    user_payload: dict,
) -> PawnOut:
    pawn = await pawn_repo.create_pawn(
        db, data,
        created_by_username=user_payload.get("sub", ""),
        created_by_name=user_payload.get("name", ""),
    )
    audit_repo.log_action_bg(
        "NEW_ENTRY",
        user_id=user_payload.get("sub"),
        metadata=f"serial={pawn.serial_no} borrower={pawn.borrower_name}",
    )
    # New pawn has no sub-items — light=True avoids accessing unloaded relations.
    return PawnOut.from_orm(pawn, all_pawns={}, light=True)


async def list_pawns(
    db: asyncpg.Connection,
    user_payload: dict,
    search: Optional[str] = None,
    collateral_type: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    serial_from: Optional[int] = None,
    serial_to: Optional[int] = None,
    per_page: int = 500,
) -> dict:
    # light=False: include sub-items (dhafa/prepayments/interest) so the table can
    # compute the live payable amount, and so the JSON backup stays complete.
    # Sub-items are sparse + FK-indexed, so this adds negligible time.
    pawns, total = await pawn_repo.get_all_pawns(
        db, search, collateral_type, status, date_from, date_to,
        serial_from=serial_from, serial_to=serial_to,
        skip=0, limit=per_page, light=False,
    )

    # Both dashboard stat counts in ONE query (FILTER aggregation), on the SAME
    # connection — no extra connection. Supabase's session pooler caps total clients
    # at 15, so we keep each request to a single connection.
    row = await db.fetchrow(
        "SELECT COUNT(*) AS total_all, "
        "COUNT(*) FILTER (WHERE is_released = false AND is_cancelled = false) AS active_count "
        "FROM pawns"
    )
    total_all, active_count = int(row["total_all"] or 0), int(row["active_count"] or 0)

    pawn_map = {str(p.id): p for p in pawns}
    items = [PawnOut.from_orm(p, all_pawns=pawn_map, light=False) for p in pawns]

    return {
        "items": items,
        "total": total,
        "total_all": total_all,
        "active_count": active_count,
        "per_page": per_page,
    }


async def get_pawn(db: asyncpg.Connection, pawn_id: UUID, user_payload: dict) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.get_pawn_by_id(db, pawn_id)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    return PawnOut.from_orm(pawn)


async def update_pawn_entry(
    db: asyncpg.Connection,
    pawn_id: UUID,
    data: PawnUpdate,
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.update_pawn(db, pawn_id, data)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "UPDATE",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id} changes={data.model_dump(exclude_unset=True)}",
    )
    return PawnOut.from_orm(pawn)


async def release_pawn(
    db: asyncpg.Connection,
    pawn_id: UUID,
    released_date: Optional[date],
    user_payload: dict,
    actual_release_amount: Optional[float] = None,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.release_pawn(db, pawn_id, released_date, actual_release_amount)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "RELEASED",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id} date={released_date} amount={actual_release_amount}",
    )
    return PawnOut.from_orm(pawn)


async def mark_sold(
    db: asyncpg.Connection,
    pawn_id: UUID,
    sold_date: Optional[date],
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.mark_sold(db, pawn_id, sold_date)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "SOLD",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id} date={sold_date}",
    )
    return PawnOut.from_orm(pawn)


async def mark_active(
    db: asyncpg.Connection,
    pawn_id: UUID,
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.mark_active(db, pawn_id)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "REACTIVATED",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id}",
    )
    return PawnOut.from_orm(pawn)


async def cancel_pawn(
    db: asyncpg.Connection,
    pawn_id: UUID,
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.cancel_pawn(db, pawn_id)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "CANCELLED",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id}",
    )
    return PawnOut.from_orm(pawn)


async def renew_loan(
    db: asyncpg.Connection,
    old_pawn_id: UUID,
    new_data: RenewRequest,
    user_payload: dict,
) -> dict:
    from fastapi import HTTPException
    create_data = PawnCreate(**new_data.model_dump())
    old, new = await pawn_repo.renew_pawn(
        db,
        old_pawn_id,
        create_data,
        created_by_username=user_payload.get("sub", ""),
        created_by_name=user_payload.get("name", ""),
    )
    if not old:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "RENEWED",
        user_id=user_payload.get("sub"),
        metadata=f"old={old_pawn_id} new={new.id}",
    )
    return {"old": PawnOut.from_orm(old), "new": PawnOut.from_orm(new)}


async def link_renewal(
    db: asyncpg.Connection,
    current_pawn_id: UUID,
    old_serial_no: int,
    old_series,
    user_payload: dict,
) -> dict:
    from fastapi import HTTPException
    old, current = await pawn_repo.link_renewal(db, current_pawn_id, old_serial_no, old_series)
    if old is None:
        raise HTTPException(status_code=404, detail="Old serial number not found")
    if current is None:
        raise HTTPException(status_code=404, detail="Current pawn not found")
    audit_repo.log_action_bg(
        "LINK_RENEWAL",
        user_id=user_payload.get("sub"),
        metadata=f"current={current_pawn_id} old_serial={old_serial_no}",
    )
    return {"old": PawnOut.from_orm(old), "current": PawnOut.from_orm(current)}


async def add_dhafa(
    db: asyncpg.Connection,
    pawn_id: UUID,
    data: AdditionalAmountCreate,
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.add_additional_amount(db, pawn_id, data)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "ADD_DHAFA",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id} amount={data.amount}",
    )
    return PawnOut.from_orm(pawn)


async def add_prepayment(
    db: asyncpg.Connection,
    pawn_id: UUID,
    data: PrepaymentCreate,
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.add_prepayment(db, pawn_id, data)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "ADD_PREPAYMENT",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id} amount={data.amount}",
    )
    return PawnOut.from_orm(pawn)


async def delete_additional_amount(
    db: asyncpg.Connection, item_id: UUID, user_payload: dict
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.delete_additional_amount(db, item_id)
    if not pawn:
        raise HTTPException(status_code=404, detail="Dhafa entry not found")
    audit_repo.log_action_bg(
        "DELETE_DHAFA",
        user_id=user_payload.get("sub"),
        metadata=f"item_id={item_id}",
    )
    return PawnOut.from_orm(pawn)


async def delete_prepayment(
    db: asyncpg.Connection, item_id: UUID, user_payload: dict
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.delete_prepayment(db, item_id)
    if not pawn:
        raise HTTPException(status_code=404, detail="Prepayment not found")
    audit_repo.log_action_bg(
        "DELETE_PREPAYMENT",
        user_id=user_payload.get("sub"),
        metadata=f"item_id={item_id}",
    )
    return PawnOut.from_orm(pawn)


async def add_interest_payment(
    db: asyncpg.Connection,
    pawn_id: UUID,
    data: InterestPaymentCreate,
    user_payload: dict,
) -> PawnOut:
    from fastapi import HTTPException
    pawn = await pawn_repo.add_interest_payment(db, pawn_id, data)
    if not pawn:
        raise HTTPException(status_code=404, detail="Pawn not found")
    audit_repo.log_action_bg(
        "ADD_INTEREST_PAYMENT",
        user_id=user_payload.get("sub"),
        metadata=f"pawn_id={pawn_id} amount={data.amount}",
    )
    return PawnOut.from_orm(pawn)


async def export_excel(db: asyncpg.Connection, user_payload: dict) -> BytesIO:
    pawns, _ = await pawn_repo.get_all_pawns(db, light=True)
    records = [
        {
            "Entry No": p.serial_no,
            "Series": p.series or "",
            "Date": p.entry_date,
            "Borrower": p.borrower_name,
            "Father/Spouse": p.relative_name,
            "Phone": p.phone or "",
            "Item": p.item_description,
            "Weight (g)": float(p.item_weight) if p.item_weight else "",
            "Type": p.collateral_type.value.title(),
            "Loan Amount (₹)": float(p.loan_amount),
            "Interest Rate (% /mo)": float(p.interest_rate) if p.interest_rate else "",
            "Released": "Yes" if p.is_released else "No",
            "Released Date": str(p.released_date) if p.released_date else "",
        }
        for p in pawns
    ]
    df = pd.DataFrame(records)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Pawns")
    buffer.seek(0)
    audit_repo.log_action_bg(
        "EXPORT_EXCEL",
        user_id=user_payload.get("sub"),
        metadata=f"rows={len(records)}",
    )
    return buffer
