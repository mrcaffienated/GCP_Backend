from typing import Optional
from uuid import UUID
from datetime import date as _date
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
import asyncpg

from db.database import get_db
from utils.jwt_utils import get_current_user, require_boss
from models.schemas import PurchaseCreate, PurchaseUpdate, PurchaseOut
from services import purchase_service

router = APIRouter()


@router.get("/")
async def list_purchases(
    response: Response,
    search: Optional[str] = Query(None),
    metal_type: Optional[str] = Query(None),      # gold|silver
    date_from: Optional[_date] = Query(None),
    date_to: Optional[_date] = Query(None),
    per_page: int = Query(1000000),
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    result = await purchase_service.list_purchases(
        db, user, search, metal_type, date_from, date_to, per_page
    )
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(result)


@router.get("/next-serial")
async def next_serial(
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    from repo.purchase_repo import get_next_serial
    serial = await get_next_serial(db)
    return {"next_serial": serial}


@router.post("/", response_model=PurchaseOut, status_code=201)
async def create_purchase(
    data: PurchaseCreate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),   # employees can add too
):
    return await purchase_service.add_purchase(db, data, user)


@router.patch("/{purchase_id}", response_model=PurchaseOut)
async def update_purchase(
    purchase_id: UUID,
    data: PurchaseUpdate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await purchase_service.update_purchase(db, purchase_id, data, user)


@router.delete("/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase(
    purchase_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),       # deleting is boss-only
):
    await purchase_service.delete_purchase(db, purchase_id, boss)
