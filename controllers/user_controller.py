from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends
import asyncpg

from db.database import get_db
from models.schemas import EmployeeCreate, EmployeeOut
from services import user_service
from utils.jwt_utils import get_current_user, require_boss

router = APIRouter()


@router.get("/employees", response_model=List[EmployeeOut])
async def list_employees(
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    return await user_service.list_employees(db, boss.get("sub"))


@router.post("/employees", response_model=EmployeeOut, status_code=201)
async def create_employee(
    data: EmployeeCreate,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    return await user_service.create_employee(db, data, boss)


@router.patch("/employees/{emp_id}/deactivate", response_model=EmployeeOut)
async def deactivate_employee(
    emp_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    return await user_service.deactivate_employee(db, emp_id, boss)


@router.patch("/employees/{emp_id}/approve-password", response_model=EmployeeOut)
async def approve_password_change(
    emp_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    return await user_service.approve_password_change(db, emp_id, boss)


@router.patch("/employees/{emp_id}/deny-password", response_model=EmployeeOut)
async def deny_password_change(
    emp_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    return await user_service.deny_password_change(db, emp_id, boss)
