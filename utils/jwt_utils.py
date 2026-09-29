from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from services.auth_service import decode_token

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Returns the full token payload: {sub, role, name, uid}.
    """
    token = credentials.credentials
    payload = decode_token(token)
    if not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate token",
        )
    return payload


def require_boss(payload: dict = Depends(get_current_user)) -> dict:
    if payload.get("role") != "boss":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Boss access required",
        )
    return payload
