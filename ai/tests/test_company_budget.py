"""Pure-logic test for app.services.company_budget's Bangkok month-boundary math - the
one piece of this module that needs no DB/Redis connection, so it can run standalone
unlike the rest of this feature (check_monthly_budget_not_exceeded itself needs a real
Postgres connection via get_company_session, same as every other services/*.py test in
this suite)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.company_budget import _bangkok_month_bounds

_BANGKOK = ZoneInfo("Asia/Bangkok")


def test_month_bounds_mid_month(monkeypatch):
    fixed_now = datetime(2026, 10, 19, 15, 30, tzinfo=_BANGKOK)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr("app.services.company_budget.datetime", _FixedDatetime)

    month_start, next_month_start = _bangkok_month_bounds()

    assert month_start == datetime(2026, 10, 1, tzinfo=_BANGKOK)
    assert next_month_start == datetime(2026, 11, 1, tzinfo=_BANGKOK)


def test_month_bounds_december_rolls_over_to_next_january(monkeypatch):
    fixed_now = datetime(2026, 12, 31, 23, 59, tzinfo=_BANGKOK)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr("app.services.company_budget.datetime", _FixedDatetime)

    month_start, next_month_start = _bangkok_month_bounds()

    assert month_start == datetime(2026, 12, 1, tzinfo=_BANGKOK)
    assert next_month_start == datetime(2027, 1, 1, tzinfo=_BANGKOK)
