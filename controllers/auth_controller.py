from fastapi import APIRouter, Depends
import asyncpg
from db.database import get_db
from models.schemas import (
    LoginRequest, TokenResponse,
    ChangePasswordRequest, SetNewPasswordRequest, EmployeeOut,
)
from services.auth_service import authenticate_user, create_access_token
from utils.jwt_utils import get_current_user
from repo.audit_repo import log_action

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: asyncpg.Connection = Depends(get_db)):
    user = await authenticate_user(db, payload.username, payload.password)
    token = create_access_token(user)
    await log_action(db, action_type="LOGIN", user_id=user.username, metadata="success")
    return TokenResponse(
        access_token=token,
        role=user.role.value,
        name=user.name,
        user_id=str(user.id),
    )


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    from services import user_service
    return await user_service.boss_change_password(
        db, user, body.current_password, body.new_password
    )


@router.post("/request-password-change", response_model=EmployeeOut)
async def request_password_change(
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    from services import user_service
    return await user_service.request_own_password_change(db, user)


@router.delete("/password-request", response_model=EmployeeOut)
async def cancel_password_request(
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    from services import user_service
    return await user_service.cancel_own_password_request(db, user)


@router.post("/set-new-password", response_model=EmployeeOut)
async def set_new_password(
    body: SetNewPasswordRequest,
    db: asyncpg.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    from services import user_service
    return await user_service.set_employee_new_password(db, user, body.new_password)
