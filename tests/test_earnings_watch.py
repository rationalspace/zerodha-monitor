"""Tests for the EarningsWatch rule (Phase 5).

Uses a stub market so no yfinance calls happen. Date fixtures:
2026-07-23 is a Thursday (Infosys Q1 FY27 results date), 2026-07-24 Friday,
2026-07-27 Monday.
"""

from __future__ import annotations

from datetime import date

from zerodha_monitor.config_loader import AppConfig
from zerodha_monitor.holdings_loader import Holding
from zerodha_monitor.market_data import PriceSnapshot
from zerodha_monitor.rules.earnings_watch import EarningsWatchRule
from zerodha_monitor.rules.sell_near_high import Severity


EARNINGS = date(2026, 7, 23)   # Thursday


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="INFY", quantity=1000, average_cost=918.71,
        sector="SOFTWARE SERVICES", long_term=True,
        exit_tier="hold", exit_note="holding through Q1 earnings",
    )
    base.update(overrides)
    return Holding(**base)


class FakeMarket:
    def __init__(self, as_of: date, earnings: list[date], day_change_pct: float | None = 0.002):
        self._as_of = as_of
        self._earnings = earnings
        self._day_change_pct = day_change_pct

    def earnings_dates(self, symbol: str) -> list[date]:
        return self._earnings

    def snapshot(self, symbol: str) -> PriceSnapshot:
        return PriceSnapshot(
            symbol=symbol, yf_symbol=f"{symbol}.NS", price=1450.0,
            ath=2960.0, high_52w=2550.0, ath_pct=0.49, high_52w_pct=0.57,
            data_date=self._as_of, day_change_pct=self._day_change_pct,
        )

    def fundamentals(self, symbol: str):
        return None


def _run(as_of: date, earnings: list[date], day_change_pct: float | None = 0.002):
    rule = EarningsWatchRule(AppConfig())
    return rule.evaluate([_holding()], FakeMarket(as_of, earnings, day_change_pct))


class TestEarningsWatch:
    def test_no_alert_outside_window(self):
        assert _run(date(2026, 7, 10), [EARNINGS]) == []

    def test_countdown_within_days_ahead(self):
        alerts = _run(date(2026, 7, 21), [EARNINGS])   # Tuesday, 2 days before
        assert len(alerts) == 1
        assert alerts[0].severity == Severity.DIGEST
        assert "Results in 2 days" in alerts[0].title

    def test_report_day_is_medium(self):
        alerts = _run(EARNINGS, [EARNINGS])
        assert len(alerts) == 1
        assert alerts[0].severity == Severity.MEDIUM
        assert "Results due today" in alerts[0].title

    def test_reaction_day_big_move_is_high(self):
        alerts = _run(date(2026, 7, 24), [EARNINGS], day_change_pct=-0.062)
        assert len(alerts) == 1
        assert alerts[0].severity == Severity.HIGH
        assert "Post-earnings session: -6.2%" in alerts[0].title

    def test_reaction_day_small_move_is_medium(self):
        alerts = _run(date(2026, 7, 24), [EARNINGS], day_change_pct=0.012)
        assert len(alerts) == 1
        assert alerts[0].severity == Severity.MEDIUM

    def test_monday_reaction_after_friday_results(self):
        friday_results = date(2026, 7, 24)
        alerts = _run(date(2026, 7, 27), [friday_results])   # Monday, days_since=3
        assert len(alerts) == 1
        assert "Post-earnings session" in alerts[0].title

    def test_no_double_reaction_two_sessions_later(self):
        wednesday_results = date(2026, 7, 22)
        assert _run(date(2026, 7, 24), [wednesday_results]) == []   # Friday, days_since=2

    def test_no_earnings_data_no_alert(self):
        assert _run(date(2026, 7, 21), []) == []

    def test_disabled_rule_returns_nothing(self):
        cfg = AppConfig()
        cfg.earnings_watch.enabled = False
        rule = EarningsWatchRule(cfg)
        assert rule.evaluate([_holding()], FakeMarket(EARNINGS, [EARNINGS])) == []

    def test_payload_renders_in_digest_template(self):
        """Real rule output through the real digest template — the same
        regression path test_digest_template.py guards for the other rules."""
        from tests.test_digest_template import _render_digest

        alerts = _run(date(2026, 7, 21), [EARNINGS])
        assert len(alerts) == 1
        html = _render_digest([alerts[0].payload])
        assert "INFY" in html
