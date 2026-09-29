"""Chatbot data access.

Two capabilities, both strictly read-only:

  run_readonly_query(sql)   — executes an ALREADY-GUARDRAILED query inside a
                              READ ONLY transaction with a statement timeout
                              and a hard row cap. Even if a write slipped past
                              the guard, Postgres itself rejects it here.

  customer_summary(name)    — loads a customer's bills (with sub-items) and
                              computes exact payable amounts using the same
                              math as the app's screens.

Both take the current request so they can resolve the right connection
(Cloudflare Hyperdrive in production, direct Supabase URL locally) — the
chat endpoints don't go through the get_db() FastAPI dependency, since they
manage their own short-lived connections per call, same as before.
"""
from datetime import date
from typing import Optional
import asyncpg
from fastapi import Request

from db.database import open_connection
from repo.pawn_repo import row_to_pawn_ns
from utils.interest_calc import compute_payable
from utils.chat_logger import chat_logger

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000


async def run_readonly_query(sql: str, request: Optional[Request] = None) -> dict:
    """Execute a validated SELECT inside a READ ONLY transaction.
    Returns {"columns": [...], "rows": [[...], ...], "row_count": n, "truncated": bool}.
    Always rolls back — a READ ONLY transaction never commits writes anyway.
    """
    async with open_connection(request) as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}'")
            stmt = await conn.prepare(sql)
            columns = [a.name for a in stmt.get_attributes()]
            fetched = await stmt.fetch()
            truncated = len(fetched) > MAX_ROWS
            rows = [[_cell(v) for v in r.values()] for r in fetched[:MAX_ROWS]]
            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }


def _cell(v):
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


async def customer_summary(name: str, request: Optional[Request] = None) -> dict:
    """All bills for customers whose name matches borrower_name OR relative_name
    (covers S/O, D/O, W/O lookups), with exact payable amounts."""
    pattern = f"%{name}%"
    async with open_connection(request) as conn:
        rows = await conn.fetch(
            """SELECT * FROM pawns WHERE borrower_name ILIKE $1 OR relative_name ILIKE $1
               ORDER BY serial_no DESC LIMIT 50""",
            pattern,
        )
        ids = [r["id"] for r in rows]
        aa_rows, pp_rows, ip_rows = [], [], []
        if ids:
            aa_rows = await conn.fetch(
                "SELECT * FROM additional_amounts WHERE pawn_id = ANY($1::uuid[])", ids
            )
            pp_rows = await conn.fetch(
                "SELECT * FROM prepayments WHERE pawn_id = ANY($1::uuid[])", ids
            )
            ip_rows = await conn.fetch(
                "SELECT * FROM interest_payments WHERE pawn_id = ANY($1::uuid[])", ids
            )

    def _group(items):
        out: dict = {}
        for r in items:
            out.setdefault(r["pawn_id"], []).append(r)
        return out

    aa_by_pawn, pp_by_pawn, ip_by_pawn = _group(aa_rows), _group(pp_rows), _group(ip_rows)
    pawns = [
        row_to_pawn_ns(r, aa_by_pawn.get(r["id"], []), pp_by_pawn.get(r["id"], []), ip_by_pawn.get(r["id"], []))
        for r in rows
    ]

    bills = []
    for p in pawns:
        metal = getattr(p.collateral_type, "value", p.collateral_type)
        status = (
            "cancelled" if p.is_cancelled
            else "sold" if p.is_sold
            else "released" if p.is_released
            else "active"
        )
        bill = {
            "bill_no": f"{p.series}{p.serial_no}" if p.series else str(p.serial_no),
            "borrower_name": p.borrower_name,
            "entry_date": str(p.entry_date),
            "metal": metal,
            "status": status,
            "loan_amount": float(p.loan_amount or 0),
            "interest_rate_pct_per_month": float(p.interest_rate) if p.interest_rate else None,
            "dhafa_count": len(p.additional_amounts or []),
            "dhafa_total": sum(float(a.amount or 0) for a in (p.additional_amounts or [])),
            "prepaid_total": sum(float(x.amount or 0) for x in (p.prepayments or [])),
        }
        if status == "active":
            bill["payable_today_with_interest"] = round(compute_payable(p), 2)
        elif status == "released":
            bill["released_date"] = str(p.released_date) if p.released_date else None
            bill["amount_collected"] = (
                float(p.actual_release_amount) if p.actual_release_amount
                else round(compute_payable(p), 2)
            )
        bills.append(bill)

    active = [b for b in bills if b["status"] == "active"]
    summary = {
        "matched_customers": sorted({b["borrower_name"] for b in bills}),
        "total_bills": len(bills),
        "active_bills": len(active),
        "active_principal_total": round(sum(b["loan_amount"] for b in active), 2),
        "active_payable_today_total": round(
            sum(b.get("payable_today_with_interest", 0) for b in active), 2
        ),
        "active_by_metal": {
            m: {
                "bills": len([b for b in active if b["metal"] == m]),
                "principal": round(sum(b["loan_amount"] for b in active if b["metal"] == m), 2),
            }
            for m in ("gold", "silver", "both")
            if any(b["metal"] == m for b in active)
        },
        "as_of": str(date.today()),
        "bills": bills,
    }
    chat_logger.info("customer_summary name=%r matched=%d bills=%d", name, len(summary["matched_customers"]), len(bills))
    return summary
