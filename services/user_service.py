from typing import List
from uuid import UUID
import asyncpg
from fastapi import HTTPException, status

from models.schemas import EmployeeCreate, EmployeeOut, SetNewPasswordRequest
from repo import user_repo, audit_repo
from services.auth_service import verify_password


async def list_employees(db: asyncpg.Connection, boss_id: str) -> List[EmployeeOut]:
    employees = await user_repo.get_all_employees(db)
    return [EmployeeOut.from_orm_user(e) for e in employees]


async def create_employee(
    db: asyncpg.Connection, data: EmployeeCreate, boss_payload: dict
) -> EmployeeOut:
    # Check username is not already taken
    existing = await user_repo.get_user_by_username(db, data.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{data.username}' is already taken",
        )
    emp = await user_repo.create_employee(db, data.username, data.password, data.name)
    await audit_repo.log_action(
        db,
        action_type="CREATE_EMPLOYEE",
        user_id=boss_payload.get("sub"),
        metadata=f"new_employee={data.username}",
    )
    return EmployeeOut.from_orm_user(emp)


async def deactivate_employee(
    db: asyncpg.Connection, emp_id: UUID, boss_payload: dict
) -> EmployeeOut:
    emp = await user_repo.deactivate_employee(db, emp_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    await audit_repo.log_action(
        db,
        action_type="DEACTIVATE_EMPLOYEE",
        user_id=boss_payload.get("sub"),
        metadata=f"employee_id={emp_id}",
    )
    return EmployeeOut.from_orm_user(emp)


async def approve_password_change(
    db: asyncpg.Connection, emp_id: UUID, boss_payload: dict
) -> EmployeeOut:
    emp = await user_repo.approve_password_change(db, emp_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    await audit_repo.log_action(
        db,
        action_type="APPROVE_PWD_CHANGE",
        user_id=boss_payload.get("sub"),
        metadata=f"employee_id={emp_id}",
    )
    return EmployeeOut.from_orm_user(emp)


async def deny_password_change(
    db: asyncpg.Connection, emp_id: UUID, boss_payload: dict
) -> EmployeeOut:
    emp = await user_repo.deny_password_change(db, emp_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    await audit_repo.log_action(
        db,
        action_type="DENY_PWD_CHANGE",
        user_id=boss_payload.get("sub"),
        metadata=f"employee_id={emp_id}",
    )
    return EmployeeOut.from_orm_user(emp)


# ── Password-change flows (self-service) ───────────────────────────────────────

async def request_own_password_change(
    db: asyncpg.Connection, user_payload: dict
) -> EmployeeOut:
    from uuid import UUID
    uid = UUID(user_payload["uid"])
    emp = await user_repo.request_password_change(db, uid)
    if not emp:
        raise HTTPException(status_code=404, detail="User not found")
    return EmployeeOut.from_orm_user(emp)


async def cancel_own_password_request(
    db: asyncpg.Connection, user_payload: dict
) -> EmployeeOut:
    from uuid import UUID
    uid = UUID(user_payload["uid"])
    emp = await user_repo.cancel_password_request(db, uid)
    if not emp:
        raise HTTPException(status_code=404, detail="User not found")
    return EmployeeOut.from_orm_user(emp)


async def set_employee_new_password(
    db: asyncpg.Connection, user_payload: dict, new_password: str
) -> EmployeeOut:
    from uuid import UUID
    uid = UUID(user_payload["uid"])
    emp_row = await user_repo.get_user_by_id(db, uid)
    if not emp_row:
        raise HTTPException(status_code=404, detail="User not found")
    if emp_row.pwd_change_status is None or emp_row.pwd_change_status.value != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change not approved by boss",
        )
    emp = await user_repo.set_new_password(db, uid, new_password)
    await audit_repo.log_action(
        db,
        action_type="EMPLOYEE_PWD_CHANGED",
        user_id=user_payload.get("sub"),
        metadata="self-service after approval",
    )
    return EmployeeOut.from_orm_user(emp)


async def boss_change_password(
    db: asyncpg.Connection, user_payload: dict, current_password: str, new_password: str
) -> dict:
    from uuid import UUID
    uid = UUID(user_payload["uid"])
    boss = await user_repo.get_user_by_id(db, uid)
    if not boss:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(current_password, boss.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    await user_repo.change_own_password(db, uid, new_password)
    await audit_repo.log_action(
        db,
        action_type="BOSS_PWD_CHANGED",
        user_id=user_payload.get("sub"),
        metadata="boss changed own password",
    )
    return {"detail": "Password updated"}
