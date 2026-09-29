"""Cloudflare-Worker-compatible database layer.

Runtime is raw asyncpg (no SQLAlchemy) so this works inside a Cloudflare
Python Worker, where async SQLAlchemy's own connection pooling is not
supported. Connection pooling itself is handled OUTSIDE the app:

  - In production (Cloudflare Worker): Cloudflare Hyperdrive pools and
    proxies the connection. We open ONE short-lived asyncpg connection per
    request against the Hyperdrive binding (`request.scope["env"].HYPERDRIVE`)
    and close it when the request ends — never a local pool.
  - In local development (plain `uvicorn`/`pywrangler dev` without a
    Hyperdrive binding present): connect directly to the Supabase
    DATABASE_URL from the environment, same as before.

Postgres `json`/`jsonb` columns are decoded/encoded transparently via a
type codec registered on every connection, so callers get native Python
lists/dicts (matching the old SQLAlchemy JSON column behaviour) without any
special-casing in repo code.
"""
import json
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv
from fastapi import Request

load_dotenv()

# Cached once per process: Hyperdrive credentials are static for a given
# deployment, and the local DATABASE_URL never changes at runtime, so
# resolving this on every call is unnecessary. Refreshed on every request
# that goes through get_db(), so background tasks (audit log, chat) that
# can't carry a live Request always have a recent value to fall back on.
_CACHED_KWARGS: Optional[dict] = None


def parse_local_dsn() -> dict:
    raw = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:root%40123@localhost:5432/pawnpro",
    )
    # Accept the old SQLAlchemy-style "postgresql+asyncpg://" URLs too.
    raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(raw)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": (parsed.path or "/postgres").lstrip("/") or "postgres",
    }


def _hyperdrive_kwargs(request: Optional[Request]) -> Optional[dict]:
    """Extract Hyperdrive connection info from the Worker's env binding, if
    this request is running inside a Cloudflare Worker with one configured."""
    if request is None:
        return None
    env = request.scope.get("env")
    hd = getattr(env, "HYPERDRIVE", None) if env is not None else None
    if hd is None:
        return None
    return {
        "host": hd.host,
        "port": int(hd.port),
        "user": hd.user,
        "password": hd.password,
        "database": hd.database,
        "ssl": False,  # Hyperdrive terminates TLS; the Worker->Hyperdrive leg doesn't need it.
    }


def resolve_conn_kwargs(request: Optional[Request] = None) -> dict:
    """Hyperdrive in production, direct Supabase URL in local dev."""
    global _CACHED_KWARGS
    kwargs = _hyperdrive_kwargs(request) or parse_local_dsn()
    _CACHED_KWARGS = kwargs
    return kwargs


def cached_conn_kwargs() -> dict:
    """Best-effort connection kwargs for code paths with no live Request
    (fire-and-forget audit logging). Uses whatever the most recent request
    resolved; falls back to the local DSN if nothing has resolved yet."""
    return _CACHED_KWARGS or parse_local_dsn()


async def _register_codecs(conn: asyncpg.Connection) -> None:
    for typename in ("json", "jsonb"):
        try:
            await conn.set_type_codec(
                typename,
                schema="pg_catalog",
                encoder=json.dumps,
                decoder=json.loads,
            )
        except Exception:
            pass  # type may not exist / already registered — non-fatal


@asynccontextmanager
async def open_connection(request: Optional[Request] = None) -> AsyncIterator[asyncpg.Connection]:
    """Async context manager for code that needs a connection but isn't a
    FastAPI route (background audit writes, the chatbot's own session)."""
    kwargs = resolve_conn_kwargs(request) if request is not None else cached_conn_kwargs()
    conn = await asyncpg.connect(**kwargs)
    try:
        await _register_codecs(conn)
        yield conn
    finally:
        await conn.close()


async def get_db(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """FastAPI dependency — one connection per request, closed afterwards."""
    kwargs = resolve_conn_kwargs(request)
    conn = await asyncpg.connect(**kwargs)
    try:
        await _register_codecs(conn)
        yield conn
    finally:
        await conn.close()
