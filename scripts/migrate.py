"""One-off, idempotent schema setup + boss-account seeding.

This used to run automatically in main.py's FastAPI lifespan on every
process start. Cloudflare Worker isolates can spin up on every request, so
that's no longer appropriate — this script now runs manually, at deploy
time, against the database directly (NOT through Hyperdrive/the Worker):

    cd backend
    uv run python scripts/migrate.py

It always connects to DATABASE_URL from the environment (backend/.env for
local dev, whatever direct Supabase URL you use in CI/deploy) — never
through Hyperdrive, since Hyperdrive only exists inside a Worker's `env`.

Safe to re-run: every statement is idempotent (IF NOT EXISTS / DO blocks),
and existing rows/users are never touched or reset.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from db.database import parse_local_dsn  # noqa: E402
from services.auth_service import hash_password  # noqa: E402

load_dotenv()

_CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        username VARCHAR(100) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        name VARCHAR(255) NOT NULL,
        role VARCHAR(20) NOT NULL CHECK (role IN ('boss', 'employee')),
        is_active BOOLEAN NOT NULL DEFAULT true,
        pwd_change_status VARCHAR(20) CHECK (pwd_change_status IN ('pending', 'approved')),
        pwd_change_requested_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS pawns (
        id UUID PRIMARY KEY,
        serial_no INTEGER NOT NULL,
        series VARCHAR(2),
        entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
        borrower_name VARCHAR(255) NOT NULL,
        relative_name VARCHAR(255) NOT NULL,
        phone TEXT,
        aadhar VARCHAR(20),
        address TEXT,
        item_description TEXT NOT NULL,
        item_weight NUMERIC(8,3),
        item_weight_gold NUMERIC(8,3),
        item_weight_silver NUMERIC(8,3),
        collateral_type VARCHAR(10) NOT NULL CHECK (collateral_type IN ('gold', 'silver', 'both')),
        loan_amount NUMERIC(12,2) NOT NULL,
        interest_rate NUMERIC(5,2),
        loan_amount_gold NUMERIC(12,2),
        interest_rate_gold NUMERIC(5,2),
        loan_amount_silver NUMERIC(12,2),
        interest_rate_silver NUMERIC(5,2),
        is_released BOOLEAN NOT NULL DEFAULT false,
        released_date DATE,
        actual_release_amount NUMERIC(14,2),
        is_sold BOOLEAN NOT NULL DEFAULT false,
        sold_date DATE,
        is_cancelled BOOLEAN NOT NULL DEFAULT false,
        renewed BOOLEAN NOT NULL DEFAULT false,
        renewed_from UUID,
        renewed_to UUID,
        edit_history JSON DEFAULT '[]',
        created_by_username VARCHAR(100) NOT NULL DEFAULT '',
        created_by_name VARCHAR(255) NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT now(),
        updated_at TIMESTAMP DEFAULT now(),
        CONSTRAINT uq_pawn_serial_series UNIQUE (serial_no, series)
    )""",
    """CREATE TABLE IF NOT EXISTS additional_amounts (
        id UUID PRIMARY KEY,
        pawn_id UUID NOT NULL REFERENCES pawns(id) ON DELETE CASCADE,
        amount NUMERIC(12,2) NOT NULL,
        date DATE NOT NULL,
        interest_rate NUMERIC(5,2) NOT NULL,
        note VARCHAR(500),
        created_at TIMESTAMP DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS prepayments (
        id UUID PRIMARY KEY,
        pawn_id UUID NOT NULL REFERENCES pawns(id) ON DELETE CASCADE,
        amount NUMERIC(12,2) NOT NULL,
        date DATE NOT NULL,
        note VARCHAR(500),
        created_at TIMESTAMP DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS interest_payments (
        id UUID PRIMARY KEY,
        pawn_id UUID NOT NULL REFERENCES pawns(id) ON DELETE CASCADE,
        amount NUMERIC(12,2) NOT NULL,
        date DATE NOT NULL,
        note VARCHAR(500),
        created_at TIMESTAMP DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS purchases (
        id UUID PRIMARY KEY,
        serial_no INTEGER NOT NULL,
        series VARCHAR(2),
        purchase_date DATE NOT NULL DEFAULT CURRENT_DATE,
        seller_name VARCHAR(255) NOT NULL,
        relative_name VARCHAR(255),
        phone TEXT,
        aadhar VARCHAR(20),
        address TEXT,
        item_description TEXT NOT NULL,
        item_weight NUMERIC(8,3),
        metal_type VARCHAR(10) NOT NULL DEFAULT 'gold' CHECK (metal_type IN ('gold', 'silver', 'both')),
        amount_paid NUMERIC(12,2) NOT NULL,
        notes TEXT,
        created_by_username VARCHAR(100) NOT NULL DEFAULT '',
        created_by_name VARCHAR(255) NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT now(),
        updated_at TIMESTAMP DEFAULT now(),
        CONSTRAINT uq_purchase_serial_series UNIQUE (serial_no, series)
    )""",
    """CREATE TABLE IF NOT EXISTS boss_notes (
        id UUID PRIMARY KEY,
        note_date DATE NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS audit_logs (
        id UUID PRIMARY KEY,
        timestamp TIMESTAMP NOT NULL DEFAULT now(),
        action_type VARCHAR(100) NOT NULL,
        user_id VARCHAR(255),
        details TEXT
    )""",
]

# Ported verbatim from the old main.py lifespan's _DDL list — same migrations,
# same partial-unique-index duplicate protection, same indexes.
_DDL = [
    "ALTER TABLE pawns ALTER COLUMN phone TYPE TEXT USING phone::text",
    """DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_pawn_serial_series'
        ) THEN
            ALTER TABLE pawns ADD CONSTRAINT uq_pawn_serial_series
                UNIQUE (serial_no, series);
        END IF;
    END $$""",
    "ALTER TABLE pawns ADD COLUMN IF NOT EXISTS is_sold      BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE pawns ADD COLUMN IF NOT EXISTS sold_date    DATE",
    "ALTER TABLE pawns ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_pawns_serial_no_series ON pawns (serial_no) WHERE series IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_pawns_serial   ON pawns (serial_no DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pawns_status   ON pawns (is_released, is_cancelled)",
    "CREATE INDEX IF NOT EXISTS idx_pawns_date     ON pawns (entry_date)",
    "CREATE INDEX IF NOT EXISTS idx_pawns_type     ON pawns (collateral_type)",
    "CREATE INDEX IF NOT EXISTS idx_aa_pawn_id     ON additional_amounts (pawn_id)",
    "CREATE INDEX IF NOT EXISTS idx_pp_pawn_id     ON prepayments (pawn_id)",
    "CREATE INDEX IF NOT EXISTS idx_ip_pawn_id     ON interest_payments (pawn_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_purchases_serial_no_series ON purchases (serial_no) WHERE series IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_purchases_serial ON purchases (serial_no DESC)",
    "CREATE INDEX IF NOT EXISTS idx_purchases_date   ON purchases (purchase_date)",
]


async def create_tables(conn: asyncpg.Connection) -> None:
    for ddl in _CREATE_TABLES:
        await conn.execute(ddl)
    print("  ✅  Tables ready")


async def run_ddl(conn: asyncpg.Connection) -> None:
    async with conn.transaction():
        for ddl in _DDL:
            try:
                await conn.execute("SAVEPOINT m")
                await conn.execute(ddl)
                await conn.execute("RELEASE SAVEPOINT m")
            except Exception as e:
                await conn.execute("ROLLBACK TO SAVEPOINT m")
                print(f"  ⚠️   skipped (already applied or n/a): {e}")
    print("  ✅  Migrations & indexes verified")


async def seed_boss_if_missing(conn: asyncpg.Connection) -> None:
    """Seeds boss accounts from the BOSS_ACCOUNTS env var (never from source code).

    Format: "username:password:Display Name;username2:password2:Name2"
    Only creates accounts that don't exist yet — existing users (and their
    current passwords) are never touched, so changing this var later does NOT
    reset anyone's password. Change passwords in-app instead.
    """
    from uuid import uuid4

    raw = os.getenv("BOSS_ACCOUNTS", "").strip()
    if not raw:
        print("  ℹ️   BOSS_ACCOUNTS not set — skipping boss seeding")
        return

    created = 0
    for chunk in raw.split(";"):
        parts = chunk.strip().split(":", 2)
        if len(parts) != 3:
            continue
        username, password, name = (p.strip() for p in parts)
        if not username or not password:
            continue
        exists = await conn.fetchval("SELECT 1 FROM users WHERE username = $1", username)
        if exists:
            continue
        await conn.execute(
            """INSERT INTO users (id, username, password_hash, name, role, is_active, created_at)
               VALUES ($1, $2, $3, $4, 'boss', true, now())""",
            uuid4(), username, hash_password(password), name or username,
        )
        created += 1
    print(f"  ✅  Boss accounts ready ({created} created)")


async def main() -> None:
    dsn = parse_local_dsn()
    print("\n" + "─" * 52)
    print("  PawnPro — running migrations…")
    print("─" * 52)
    conn = await asyncpg.connect(**dsn)
    try:
        await conn.execute("SELECT 1")
        print("  ✅  Database connected")
        await create_tables(conn)
        await run_ddl(conn)
        await seed_boss_if_missing(conn)
    finally:
        await conn.close()
    print("─" * 52 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
