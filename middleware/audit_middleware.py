import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from utils.logger import get_logger

logger = get_logger("audit")


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 2)
        logger.info(
            "%s %s | status=%s | duration=%sms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
