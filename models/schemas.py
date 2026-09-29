from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, field_validator
from models.pawn_model import CollateralType, UserRole, PwdChangeStatus


# ── Auth ───────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str
    user_id: str



class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class SetNewPasswordRequest(BaseModel):
    new_password: str


# ── Users ──────────────────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    username: str
    password: str
    name: str


class PwdChangeRequestOut(BaseModel):
    status: str
    requested_at: Optional[datetime] = None


class EmployeeOut(BaseModel):
    id: UUID
    username: str
    name: str
    role: UserRole
    is_active: bool
    pwd_change_request: Optional[PwdChangeRequestOut] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_user(cls, u) -> "EmployeeOut":
        req = None
        if u.pwd_change_status is not None:
            req = PwdChangeRequestOut(
                status=u.pwd_change_status.value,
                requested_at=u.pwd_change_requested_at,
            )
        return cls(
            id=u.id,
            username=u.username,
            name=u.name,
            role=u.role,
            is_active=u.is_active,
            pwd_change_request=req,
        )


# ── Sub-items ──────────────────────────────────────────────────────────────────

class AdditionalAmountCreate(BaseModel):
    amount: Decimal
    date: date
    interest_rate: Decimal
    note: Optional[str] = ""


class AdditionalAmountOut(BaseModel):
    id: str
    amount: str
    date: date
    interest_rate: str
    note: Optional[str] = ""

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, obj) -> "AdditionalAmountOut":
        return cls(
            id=str(obj.id),
            amount=str(obj.amount),
            date=obj.date,
            interest_rate=str(obj.interest_rate),
            note=obj.note or "",
        )


class PrepaymentCreate(BaseModel):
    amount: Decimal
    date: date
    note: Optional[str] = ""


class PrepaymentOut(BaseModel):
    id: str
    amount: str
    date: date
    note: Optional[str] = ""

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, obj) -> "PrepaymentOut":
        return cls(
            id=str(obj.id),
            amount=str(obj.amount),
            date=obj.date,
            note=obj.note or "",
        )


class InterestPaymentCreate(BaseModel):
    amount: Decimal
    date: date
    note: Optional[str] = ""


class InterestPaymentOut(BaseModel):
    id: str
    amount: str
    date: date
    note: Optional[str] = ""

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, obj) -> "InterestPaymentOut":
        return cls(
            id=str(obj.id),
            amount=str(obj.amount),
            date=obj.date,
            note=obj.note or "",
        )


# ── Created-by sub-object ──────────────────────────────────────────────────────

class CreatedByOut(BaseModel):
    username: str
    name: str


# ── Pawn ───────────────────────────────────────────────────────────────────────

class PawnCreate(BaseModel):
    serial_no: int
    series: Optional[str] = None
    entry_date: Optional[date] = None
    borrower_name: str
    relative_name: str
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: str
    item_weight: Optional[str] = None
    item_weight_gold: Optional[str] = None
    item_weight_silver: Optional[str] = None
    collateral_type: CollateralType
    loan_amount: str
    interest_rate: Optional[str] = None
    loan_amount_gold: Optional[str] = None
    interest_rate_gold: Optional[str] = None
    loan_amount_silver: Optional[str] = None
    interest_rate_silver: Optional[str] = None
    is_cancelled: Optional[bool] = False   # create a bill already voided/cancelled


class PawnUpdate(BaseModel):
    entry_date: Optional[date] = None
    borrower_name: Optional[str] = None
    relative_name: Optional[str] = None
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: Optional[str] = None
    item_weight: Optional[str] = None
    item_weight_gold: Optional[str] = None
    item_weight_silver: Optional[str] = None
    collateral_type: Optional[CollateralType] = None
    loan_amount: Optional[str] = None
    interest_rate: Optional[str] = None
    loan_amount_gold: Optional[str] = None
    interest_rate_gold: Optional[str] = None
    loan_amount_silver: Optional[str] = None
    interest_rate_silver: Optional[str] = None
    is_released: Optional[bool] = None
    released_date: Optional[date] = None
    is_sold: Optional[bool] = None
    sold_date: Optional[date] = None
    edit_entry: Optional[dict] = None   # appended to edit_history


class ReleaseRequest(BaseModel):
    released_date: Optional[date] = None
    actual_release_amount: Optional[float] = None


class RenewRequest(BaseModel):
    """New loan data for the renewed pawn entry."""
    serial_no: int
    series: Optional[str] = None
    entry_date: Optional[date] = None
    borrower_name: str
    relative_name: str
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: str
    item_weight: Optional[str] = None
    collateral_type: CollateralType
    loan_amount: str
    interest_rate: Optional[str] = None
    loan_amount_gold: Optional[str] = None
    interest_rate_gold: Optional[str] = None
    loan_amount_silver: Optional[str] = None
    interest_rate_silver: Optional[str] = None


class PawnOut(BaseModel):
    id: str
    serial_no: int
    series: Optional[str] = None
    entry_date: date
    borrower_name: str
    relative_name: str
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: str
    item_weight: Optional[str] = None
    item_weight_gold: Optional[str] = None
    item_weight_silver: Optional[str] = None
    collateral_type: CollateralType
    loan_amount: str
    interest_rate: Optional[str] = None
    loan_amount_gold: Optional[str] = None
    interest_rate_gold: Optional[str] = None
    loan_amount_silver: Optional[str] = None
    interest_rate_silver: Optional[str] = None
    is_released: bool
    released_date: Optional[date] = None
    actual_release_amount: Optional[str] = None
    is_sold: bool = False
    sold_date: Optional[date] = None
    is_cancelled: bool = False
    renewed: bool = False
    renewed_from: Optional[str] = None
    renewed_to: Optional[str] = None
    # Renewal chain display helpers
    renewed_from_serial: Optional[int] = None
    renewed_from_series: Optional[str] = None
    renewed_to_serial: Optional[int] = None
    renewed_to_series: Optional[str] = None
    edit_history: List[dict] = []
    created_by: CreatedByOut
    created_at: datetime
    additional_amounts: List[AdditionalAmountOut] = []
    prepayments: List[PrepaymentOut] = []
    interest_payments: List[InterestPaymentOut] = []

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, p, all_pawns: dict = None, light: bool = False) -> "PawnOut":
        """
        all_pawns: optional {id_str: pawn} map to resolve renewal serial references.
        light: when True, skip sub-items (dhafa/prepayments/interest) entirely — the
        relations are not accessed, so no lazy-load is triggered for unloaded objects.
        """
        def _str(v) -> Optional[str]:
            return str(v) if v is not None else None

        # Resolve renewal chain serials if lookup map provided
        renewed_from_serial = renewed_from_series = None
        renewed_to_serial   = renewed_to_series   = None

        if p.renewed_from and all_pawns:
            src = all_pawns.get(str(p.renewed_from))
            if src:
                renewed_from_serial = src.serial_no
                renewed_from_series = src.series

        if p.renewed_to and all_pawns:
            dst = all_pawns.get(str(p.renewed_to))
            if dst:
                renewed_to_serial = dst.serial_no
                renewed_to_series = dst.series

        return cls(
            id=str(p.id),
            serial_no=p.serial_no,
            series=p.series,
            entry_date=p.entry_date,
            borrower_name=p.borrower_name,
            relative_name=p.relative_name,
            phone=p.phone,
            aadhar=p.aadhar,
            address=p.address,
            item_description=p.item_description,
            item_weight=_str(p.item_weight),
            item_weight_gold=_str(p.item_weight_gold),
            item_weight_silver=_str(p.item_weight_silver),
            collateral_type=p.collateral_type,
            loan_amount=_str(p.loan_amount) or "0",
            interest_rate=_str(p.interest_rate),
            loan_amount_gold=_str(p.loan_amount_gold),
            interest_rate_gold=_str(p.interest_rate_gold),
            loan_amount_silver=_str(p.loan_amount_silver),
            interest_rate_silver=_str(p.interest_rate_silver),
            is_released=p.is_released,
            released_date=p.released_date,
            actual_release_amount=_str(p.actual_release_amount),
            is_sold=p.is_sold or False,
            sold_date=p.sold_date,
            is_cancelled=p.is_cancelled or False,
            renewed=p.renewed or False,
            renewed_from=str(p.renewed_from) if p.renewed_from else None,
            renewed_to=str(p.renewed_to) if p.renewed_to else None,
            renewed_from_serial=renewed_from_serial,
            renewed_from_series=renewed_from_series,
            renewed_to_serial=renewed_to_serial,
            renewed_to_series=renewed_to_series,
            edit_history=p.edit_history or [],
            created_by=CreatedByOut(
                username=p.created_by_username or "",
                name=p.created_by_name or "",
            ),
            created_at=p.created_at,
            additional_amounts=[] if light else [AdditionalAmountOut.from_orm(a) for a in (p.additional_amounts or [])],
            prepayments=[] if light else [PrepaymentOut.from_orm(pp) for pp in (p.prepayments or [])],
            interest_payments=[] if light else [InterestPaymentOut.from_orm(ip) for ip in (p.interest_payments or [])],
        )


# ── Old-gold Purchases ─────────────────────────────────────────────────────────

class PurchaseCreate(BaseModel):
    serial_no: int
    series: Optional[str] = None
    purchase_date: Optional[date] = None
    seller_name: str
    relative_name: Optional[str] = None
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: str
    item_weight: Optional[str] = None
    metal_type: CollateralType
    amount_paid: str
    notes: Optional[str] = None


class PurchaseUpdate(BaseModel):
    purchase_date: Optional[date] = None
    seller_name: Optional[str] = None
    relative_name: Optional[str] = None
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: Optional[str] = None
    item_weight: Optional[str] = None
    metal_type: Optional[CollateralType] = None
    amount_paid: Optional[str] = None
    notes: Optional[str] = None


class PurchaseOut(BaseModel):
    id: str
    serial_no: int
    series: Optional[str] = None
    purchase_date: date
    seller_name: str
    relative_name: Optional[str] = None
    phone: Optional[str] = None
    aadhar: Optional[str] = None
    address: Optional[str] = None
    item_description: str
    item_weight: Optional[str] = None
    metal_type: CollateralType
    amount_paid: str
    notes: Optional[str] = None
    created_by: CreatedByOut
    created_at: datetime

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, p) -> "PurchaseOut":
        def _str(v) -> Optional[str]:
            return str(v) if v is not None else None
        return cls(
            id=str(p.id),
            serial_no=p.serial_no,
            series=p.series,
            purchase_date=p.purchase_date,
            seller_name=p.seller_name,
            relative_name=p.relative_name,
            phone=p.phone,
            aadhar=p.aadhar,
            address=p.address,
            item_description=p.item_description,
            item_weight=_str(p.item_weight),
            metal_type=p.metal_type,
            amount_paid=_str(p.amount_paid) or "0",
            notes=p.notes,
            created_by=CreatedByOut(
                username=p.created_by_username or "",
                name=p.created_by_name or "",
            ),
            created_at=p.created_at,
        )


# ── Reports ────────────────────────────────────────────────────────────────────

class ReportsSummary(BaseModel):
    total_entries: int
    active_loans: int
    total_loan_amount: float
    interest_earned: float
    monthly_projected: float
