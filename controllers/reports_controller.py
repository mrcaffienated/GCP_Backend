from fastapi import APIRouter, Depends
import asyncpg

from db.database import get_db
from utils.jwt_utils import require_boss
from models.schemas import ReportsSummary

router = APIRouter()


def _effective_months(start, end) -> float:
    from dateutil.relativedelta import relativedelta
    months = (end.year - start.year) * 12 + (end.month - start.month)
    check = start + relativedelta(months=months)
    if check > end:
        months -= 1
    months = max(0, months)
    after = start + relativedelta(months=months)
    rem_days = (end - after).days
    extra = 0.0 if rem_days <= 0 else 0.5 if rem_days <= 15 else 1.0
    return months + extra


def _calc_release_amount(loan: float, rate: float, em: float) -> float:
    full_years = int(em) // 12
    rem_months = em % 12
    annual_rate = rate / 100 * 12
    after_years = loan * (1 + annual_rate) ** full_years
    return after_years * (1 + (rate / 100) * rem_months)


@router.get("/summary", response_model=ReportsSummary)
async def get_summary(
    db: asyncpg.Connection = Depends(get_db),
    boss: dict = Depends(require_boss),
):
    active_filter = "is_released = false AND is_cancelled = false"

    # SQL aggregates replace full Python-side table scans.
    total_entries = await db.fetchval("SELECT COUNT(*) FROM pawns")

    active_loans = await db.fetchval(f"SELECT COUNT(*) FROM pawns WHERE {active_filter}")

    total_loan_amount = await db.fetchval(
        f"SELECT COALESCE(SUM(loan_amount), 0) FROM pawns WHERE {active_filter}"
    )

    monthly_projected = await db.fetchval(
        f"SELECT COALESCE(SUM(loan_amount * interest_rate / 100), 0) FROM pawns "
        f"WHERE {active_filter} AND interest_rate IS NOT NULL"
    )

    # Interest earned still requires Python date math — load only released
    # pawns and only the 4 columns needed.
    released_rows = await db.fetch(
        """SELECT loan_amount, interest_rate, entry_date, released_date FROM pawns
           WHERE is_released = true AND is_cancelled = false
             AND released_date IS NOT NULL AND entry_date IS NOT NULL
             AND interest_rate IS NOT NULL"""
    )

    interest_earned = 0.0
    for row in released_rows:
        loan_amount, interest_rate, entry_date, released_date = (
            row["loan_amount"], row["interest_rate"], row["entry_date"], row["released_date"]
        )
        em = _effective_months(entry_date, released_date)
        total = _calc_release_amount(float(loan_amount), float(interest_rate), em)
        interest_earned += total - float(loan_amount)

    return ReportsSummary(
        total_entries=total_entries or 0,
        active_loans=active_loans or 0,
        total_loan_amount=round(float(total_loan_amount or 0), 2),
        interest_earned=round(interest_earned, 2),
        monthly_projected=round(float(monthly_projected or 0), 2),
    )
