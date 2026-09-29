import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from middleware.audit_middleware import AuditMiddleware
from controllers.auth_controller import router as auth_router
from controllers.pawn_controller import router as pawn_router
from controllers.user_controller import router as user_router
from controllers.reports_controller import router as reports_router
from controllers.notes_controller import router as notes_router
from controllers.purchase_controller import router as purchase_router
from routers.chat_router import router as chat_router

log = logging.getLogger("pawnpro")

# No lifespan / startup DB work here on purpose: a Cloudflare Worker isolate
# can spin up on every request, so anything that ran once at app startup
# before (connectivity check, init_db/CREATE TABLE, ALTER TABLE + index DDL,
# boss-account seeding, connection warming) would instead run on every
# isolate boot. That's moved to backend/scripts/migrate.py, which you run
# manually at deploy time — see that file's docstring.

app = FastAPI(
    title="PawnPro API",
    description="Gold & Silver Pawn Register — FastAPI backend",
    version="2.0.0",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Origins come from the environment — never a wildcard with credentials.
# FRONTEND_URL is the deployed frontend (e.g. https://example.pages.dev);
# localhost:5173 (Vite's default) is always allowed for local development.
_origins = {"http://localhost:5173"}
_frontend_url = os.getenv("FRONTEND_URL", "").strip()
if _frontend_url:
    _origins.add(_frontend_url)
_extra_origins = os.getenv("CORS_EXTRA_ORIGINS", "").strip()
if _extra_origins:
    _origins.update(o.strip() for o in _extra_origins.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress responses >500 bytes. The 500-row list is ~507KB raw but ~48KB gzipped
# — a ~90% transfer reduction, the single biggest win over a remote DB link.
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(AuditMiddleware)

app.include_router(auth_router,    prefix="/api/auth",    tags=["Auth"])
app.include_router(pawn_router,    prefix="/api/pawns",   tags=["Pawns"])
app.include_router(user_router,    prefix="/api/users",   tags=["Users"])
app.include_router(reports_router, prefix="/api/reports", tags=["Reports"])
app.include_router(notes_router,   prefix="/api/notes",   tags=["Notes"])
app.include_router(purchase_router, prefix="/api/purchases", tags=["Purchases"])
app.include_router(chat_router,    prefix="/api/chat",     tags=["Chatbot"])


@app.get("/health")
async def health():
    # No DB round-trip — this must stay cheap.
    return {"status": "ok", "version": "2.0.0"}


# ── Cloudflare Python Worker entrypoint ─────────────────────────────────────
# The `workers` package only exists inside the pywrangler-managed Worker
# runtime, so this is a no-op (and doesn't break local `uvicorn main:app`)
# everywhere else.
try:
    from workers import asgi
    Default = asgi.entrypoint(app)
except ImportError:
    pass
