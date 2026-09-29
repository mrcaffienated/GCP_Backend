import os
import bcrypt
from types import SimpleNamespace
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import HTTPException, status
import asyncpg

from models.pawn_model import UserRole

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def authenticate_user(conn: asyncpg.Connection, username: str, password: str) -> SimpleNamespace:
    """Returns a user object (attribute access, like the old ORM object) on success."""
    row = await conn.fetchrow("SELECT * FROM users WHERE username = $1", username)
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    data = dict(row)
    data["role"] = UserRole(data["role"])
    return SimpleNamespace(**data)


def create_access_token(user: SimpleNamespace) -> str:
    payload = {
        "sub": user.username,
        "role": user.role.value,
        "name": user.name,
        "uid": str(user.id),
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalid or expired",
        )
