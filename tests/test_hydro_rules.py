from app.modules.m5_months.hydro import monthly, reconcile, select_daily


def test_zero_rain_is_kept_as_measurement():
    result = select_daily([{"station": "0012", "date": "2024-02-29", "rainfall_mm": "0.0", "version": 1}])
    assert result["selected"][0]["rainfall_mm"] == "0.0"


def test_higher_version_wins_not_last_row():
    rows = [{"station": "0012", "date": "2024-02-29", "rainfall_mm": "3.0", "version": 2}, {"station": "0012", "date": "2024-02-29", "rainfall_mm": "8.0", "version": 1}]
    assert select_daily(rows)["selected"][0]["rainfall_mm"] == "3.0"


def test_same_version_conflict_is_isolated():
    rows = [{"station": "0012", "date": "2024-02-29", "rainfall_mm": "3.0", "version": 2}, {"station": "0012", "date": "2024-02-29", "rainfall_mm": "8.0", "version": 2}]
    assert select_daily(rows)["conflicts"][0]["reason"] == "VERSION_CONFLICT"


def test_input_order_does_not_change_selection():
    rows = [{"station": "0012", "date": "2024-02-29", "rainfall_mm": "3.0", "version": 2}, {"station": "0012", "date": "2024-02-29", "rainfall_mm": "8.0", "version": 1}]
    assert select_daily(rows) == select_daily(list(reversed(rows)))


def test_monthly_uses_decimal_sum():
    rows = [{"station": "0012", "date": "2024-02-28", "rainfall_mm": "0.1"}, {"station": "0012", "date": "2024-02-29", "rainfall_mm": "0.2"}]
    assert monthly(rows, {"2024-02": 2})[0]["rainfall_mm"] == "0.3"


def test_leap_month_coverage_is_explicit():
    rows = [{"station": "0012", "date": "2024-02-29", "rainfall_mm": "0.0"}]
    assert monthly(rows, {"2024-02": 29})[0]["missing_days"] == 28


def test_complete_month_is_marked_complete():
    rows = [{"station": "0012", "date": "2024-02-01", "rainfall_mm": "1.0"}]
    assert monthly(rows, {"2024-02": 1})[0]["state"] == "complete"


def test_four_account_reconciliation_balances():
    assert reconcile(31, 28, 2, 1)["balanced"] is True


def test_reconciliation_exposes_delta():
    assert reconcile(31, 28, 1, 1)["delta"] == 1
