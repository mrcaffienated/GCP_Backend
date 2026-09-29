from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from fastapi.encoders import jsonable_encoder
import asyncpg

from db.database import get_db
from utils.jwt_utils import get_current_user
from models.schemas import (
    PawnCreate, PawnUpdate, PawnOut,
    ReleaseRequest, RenewRequest,
    AdditionalAmountCreate, PrepaymentCreate, InterestPaymentCreate,
)
from datetime import date as _date
from typing import Optional as _Optional
from pydantic import BaseModel as _BaseModel

class SoldRequest(_BaseModel):
    sold_date: _Optional[_date] = None

class LinkRenewalRequest(_BaseModel):
    old_serial_no: int
    old_series: _Optional[str] = None
from services import pawn_service

router = APIRouter()


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_pawns(
    response: Response,
    search: Optional[str] = Query(None),
    collateral_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),         # active|released|sold|cancelled
    date_from: Optional[_date] = Query(None),
    date_to: Optional[_date] = Query(None),
    serial_from: Optional[int] = Query(None),
    serial_to: Optional[int] = Query(None),
    per_page: int = Query(500),
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    result = await pawn_service.list_pawns(
        db, user, search, collateral_type, status, date_from, date_to,
        serial_from, serial_to, per_page,
    )
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(result)


@router.post("/", response_model=PawnOut, status_code=201)
async def create_pawn(
    data: PawnCreate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.add_pawn(db, data, user)


@router.get("/next-serial")
async def next_serial(
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    from repo.pawn_repo import get_next_serial
    serial = await get_next_serial(db)
    return {"next_serial": serial}


@router.get("/{pawn_id}", response_model=PawnOut)
async def get_pawn(
    pawn_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.get_pawn(db, pawn_id, user)


@router.patch("/{pawn_id}", response_model=PawnOut)
async def update_pawn(
    pawn_id: UUID,
    data: PawnUpdate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.update_pawn_entry(db, pawn_id, data, user)


# ── Status changes ─────────────────────────────────────────────────────────────

@router.patch("/{pawn_id}/release", response_model=PawnOut)
async def release_pawn(
    pawn_id: UUID,
    body: ReleaseRequest = ReleaseRequest(),
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.release_pawn(db, pawn_id, body.released_date, user, body.actual_release_amount)


@router.patch("/{pawn_id}/sold", response_model=PawnOut)
async def mark_sold(
    pawn_id: UUID,
    body: SoldRequest = SoldRequest(),
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.mark_sold(db, pawn_id, body.sold_date, user)


@router.patch("/{pawn_id}/mark-active", response_model=PawnOut)
async def mark_active(
    pawn_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.mark_active(db, pawn_id, user)


@router.patch("/{pawn_id}/cancel", response_model=PawnOut)
async def cancel_pawn(
    pawn_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.cancel_pawn(db, pawn_id, user)


@router.post("/{pawn_id}/link-renewal")
async def link_renewal(
    pawn_id: UUID,
    data: LinkRenewalRequest,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Links this pawn as renewed-from an old serial. Marks old as released+renewed."""
    return await pawn_service.link_renewal(db, pawn_id, data.old_serial_no, data.old_series, user)


@router.post("/{pawn_id}/renew")
async def renew_loan(
    pawn_id: UUID,
    data: RenewRequest,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Closes the old pawn and opens a new one. Returns {old, new}."""
    return await pawn_service.renew_loan(db, pawn_id, data, user)


# ── Sub-items ──────────────────────────────────────────────────────────────────

@router.post("/{pawn_id}/additional-amounts", response_model=PawnOut)
async def add_dhafa(
    pawn_id: UUID,
    data: AdditionalAmountCreate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.add_dhafa(db, pawn_id, data, user)


@router.post("/{pawn_id}/prepayments", response_model=PawnOut)
async def add_prepayment(
    pawn_id: UUID,
    data: PrepaymentCreate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.add_prepayment(db, pawn_id, data, user)


@router.post("/{pawn_id}/interest-payments", response_model=PawnOut)
async def add_interest_payment(
    pawn_id: UUID,
    data: InterestPaymentCreate,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.add_interest_payment(db, pawn_id, data, user)


@router.delete("/additional-amounts/{item_id}", response_model=PawnOut)
async def delete_additional_amount(
    item_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.delete_additional_amount(db, item_id, user)


@router.delete("/prepayments/{item_id}", response_model=PawnOut)
async def delete_prepayment(
    item_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await pawn_service.delete_prepayment(db, item_id, user)


# ── Export ─────────────────────────────────────────────────────────────────────

@router.get("/export/excel")
async def export_excel(
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    buffer = await pawn_service.export_excel(db, user)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=gupthas_export.xlsx"},
    )
