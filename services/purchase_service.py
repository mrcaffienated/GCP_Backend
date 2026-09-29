from typing import Optional
from uuid import UUID
from datetime import date
import asyncpg

from models.schemas import PurchaseCreate, PurchaseUpdate, PurchaseOut
from repo import purchase_repo, audit_repo


async def list_purchases(
    db: asyncpg.Connection,
    user_payload: dict,
    search: Optional[str] = None,
    metal_type: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    per_page: int = 1000000,
) -> dict:
    purchases, total = await purchase_repo.get_all_purchases(
        db, search, metal_type, date_from, date_to, limit=per_page,
    )
    items = [PurchaseOut.from_orm(p) for p in purchases]

    # Unfiltered stats for the section's cards — one round-trip.
    row = await db.fetchrow(
        "SELECT COUNT(*) AS total_all, COALESCE(SUM(amount_paid), 0) AS total_paid FROM purchases"
    )
    total_all, total_paid = int(row["total_all"] or 0), float(row["total_paid"] or 0)

    return {
        "items": items,
        "total": total,
        "total_all": total_all,
        "total_paid": total_paid,
    }


async def add_purchase(
    db: asyncpg.Connection,
    data: PurchaseCreate,
    user_payload: dict,
) -> PurchaseOut:
    purchase = await purchase_repo.create_purchase(
        db, data,
        created_by_username=user_payload.get("sub", ""),
        created_by_name=user_payload.get("name", ""),
    )
    audit_repo.log_action_bg(
        "NEW_PURCHASE",
        user_id=user_payload.get("sub"),
        metadata=f"serial={purchase.serial_no} seller={purchase.seller_name}",
    )
    return PurchaseOut.from_orm(purchase)


async def update_purchase(
    db: asyncpg.Connection,
    purchase_id: UUID,
    data: PurchaseUpdate,
    user_payload: dict,
) -> PurchaseOut:
    from fastapi import HTTPException
    purchase = await purchase_repo.update_purchase(db, purchase_id, data)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    audit_repo.log_action_bg(
        "UPDATE_PURCHASE",
        user_id=user_payload.get("sub"),
        metadata=f"purchase_id={purchase_id} changes={data.model_dump(exclude_unset=True)}",
    )
    return PurchaseOut.from_orm(purchase)


async def delete_purchase(
    db: asyncpg.Connection,
    purchase_id: UUID,
    user_payload: dict,
) -> None:
    from fastapi import HTTPException
    ok = await purchase_repo.delete_purchase(db, purchase_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Purchase not found")
    audit_repo.log_action_bg(
        "DELETE_PURCHASE",
        user_id=user_payload.get("sub"),
        metadata=f"purchase_id={purchase_id}",
    )
