"""Domain enums shared by schemas.py and the repo layer.

The SQLAlchemy ORM table classes that used to live here are gone — the
runtime data layer is raw asyncpg now (Cloudflare Python Workers don't
support async SQLAlchemy). Table DDL lives in backend/scripts/migrate.py.
These plain enums are the only thing other modules still import from here.
"""
import enum


class CollateralType(str, enum.Enum):
    gold = "gold"
    silver = "silver"
    both = "both"


class UserRole(str, enum.Enum):
    boss = "boss"
    employee = "employee"


class PwdChangeStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
