"""Python port of frontend/src/utils/interest.js — the single source of truth
for payable-amount math in the app. The chatbot uses THIS (not SQL) for money
questions, so its answers always match what the app's screens show.

Keep in sync with interest.js if the business rules ever change.
"""
from datetime import date
from typing import Optional


def effective_months(entry: date, end: date) -> float:
    """Billing months between two dates.
    < 15 days total → 0.5 month; remainder days: <15 → 0.5, else 1."""
    total_days = (end - entry).days
    if total_days < 15:
        return 0.5

    months = (end.year - entry.year) * 12 + (end.month - entry.month)

    def add_months(d: date, m: int) -> date:
        y, mo = d.year + (d.month - 1 + m) // 12, (d.month - 1 + m) % 12 + 1
        # clamp day (e.g. Jan 31 + 1 month → Feb 28)
        import calendar
        return date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))

    if add_months(entry, months) > end:
        months -= 1
    months = max(0, months)

    rem_days = (end - add_months(entry, months)).days
    extra = 0.0 if rem_days <= 0 else 0.5 if rem_days < 15 else 1.0
    return months + extra


def calc_release_amount(loan: float, rate: float, entry: date, end: date) -> float:
    """Compound annual + simple monthly remainder — matches the app."""
    em = effective_months(entry, end)
    monthly = rate / 100.0
    annual = monthly * 12.0
    full_years = int(em // 12)
    rem_months = em % 12
    after_years = loan * (1 + annual) ** full_years
    return after_years * (1 + monthly * rem_months)


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_payable(pawn, as_of: Optional[date] = None) -> float:
    """Final payable for one pawn ORM object (relations must be loaded).
    Mirrors computePayable() in interest.js exactly."""
    metal = getattr(pawn.collateral_type, "value", pawn.collateral_type)
    is_both = metal == "both"
    is_gold = metal == "gold"
    is_cancelled = bool(pawn.is_cancelled)

    end = as_of or (pawn.released_date if pawn.is_released else date.today()) or date.today()

    prepaid = sum(_f(p.amount) for p in (pawn.prepayments or []))
    interest_paid = sum(_f(p.amount) for p in (pawn.interest_payments or []))

    base = 0.0
    if is_cancelled:
        principal = (_f(pawn.loan_amount_gold) + _f(pawn.loan_amount_silver)) if is_both else _f(pawn.loan_amount)
        base = principal - prepaid
    elif is_both:
        gold = (calc_release_amount(_f(pawn.loan_amount_gold), _f(pawn.interest_rate_gold), pawn.entry_date, end)
                if pawn.loan_amount_gold and pawn.interest_rate_gold else 0.0)
        silver = (calc_release_amount(_f(pawn.loan_amount_silver), _f(pawn.interest_rate_silver), pawn.entry_date, end)
                  if pawn.loan_amount_silver and pawn.interest_rate_silver else 0.0)
        base = gold + silver - prepaid
    elif pawn.loan_amount and pawn.interest_rate and pawn.entry_date:
        base = calc_release_amount(_f(pawn.loan_amount), _f(pawn.interest_rate), pawn.entry_date, end) - prepaid

    def dhafa_rate(a) -> float:
        if a.interest_rate:
            return _f(a.interest_rate)
        if is_both:
            return _f(pawn.interest_rate_silver)
        if is_gold:
            return _f(pawn.interest_rate_gold)
        return _f(pawn.interest_rate)

    additional = 0.0
    for a in (pawn.additional_amounts or []):
        if not a.amount:
            continue
        if is_cancelled:
            additional += _f(a.amount)
            continue
        rate = dhafa_rate(a)
        if rate and a.date:
            additional += calc_release_amount(_f(a.amount), rate, a.date, end)

    total_due = base + additional
    residual_principal = max(0.0, _f(pawn.loan_amount) - prepaid)
    return max(residual_principal, total_due - interest_paid)
