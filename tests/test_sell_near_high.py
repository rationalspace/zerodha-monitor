"""Tests for the SellNearHighRule."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from india_monitor.config_loader import AppConfig, SellNearHighConfig
from india_monitor.holdings_loader import Holding
from india_monitor.market_data import PriceSnapshot
from india_monitor.rules.sell_near_high import SellNearHighRule


def _config(threshold: float = 0.85, long_term_only: bool = True) -> AppConfig:
    cfg = AppConfig()
    cfg.sell_near_high = SellNearHighConfig(
        enabled=True,
        ath_threshold_pct=threshold,
        long_term_only=long_term_only,
    )
    return cfg


def _holding(symbol="INFY", qty=100, avg=900.0, long_term=True) -> Holding:
    return Holding(symbol=symbol, quantity=qty, average_cost=avg,
                   sector="IT", long_term=long_term)


def _snap(symbol="INFY", price=1000.0, ath=1100.0, high_52w=1050.0,
          history_days: int = 2000) -> PriceSnapshot:
    ath_pct = price / ath if ath else None
    h52_pct = price / high_52w if high_52w else None
    return PriceSnapshot(
        symbol=symbol, yf_symbol=f"{symbol}.NS",
        price=price, ath=ath, high_52w=high_52w,
        ath_pct=ath_pct, high_52w_pct=h52_pct,
        history_days=history_days,
    )


def _market(snaps: dict[str, PriceSnapshot | None]) -> MagicMock:
    m = MagicMock()
    m.snapshot.side_effect = lambda sym: snaps.get(sym.upper())
    return m


class TestSellNearHighRule:
    def test_fires_at_threshold(self):
        # price = 935, ath = 1100 → 85% exactly
        snap = _snap(price=935.0, ath=1100.0)
        rule = SellNearHighRule(_config(threshold=0.85))
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert len(alerts) == 1
        assert alerts[0].symbol == "INFY"
        assert alerts[0].rule == "sell_near_high"

    def test_does_not_fire_below_threshold(self):
        snap = _snap(price=800.0, ath=1100.0)   # 72.7% — below 85%
        rule = SellNearHighRule(_config(threshold=0.85))
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert alerts == []

    def test_fires_at_ath(self):
        snap = _snap(price=1100.0, ath=1100.0)   # 100% = at ATH
        rule = SellNearHighRule(_config(threshold=0.85))
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert len(alerts) == 1

    def test_skips_non_long_term_when_flag_set(self):
        snap = _snap(price=1000.0, ath=1100.0)   # 91% — would fire
        rule = SellNearHighRule(_config(long_term_only=True))
        holding = _holding(long_term=False)
        alerts = rule.evaluate([holding], _market({"INFY": snap}))
        assert alerts == []

    def test_fires_for_non_long_term_when_flag_off(self):
        snap = _snap(price=1000.0, ath=1100.0)
        rule = SellNearHighRule(_config(long_term_only=False))
        holding = _holding(long_term=False)
        alerts = rule.evaluate([holding], _market({"INFY": snap}))
        assert len(alerts) == 1

    def test_skips_when_no_market_data(self):
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding()], _market({"INFY": None}))
        assert alerts == []

    def test_skips_when_price_is_nan(self):
        import math
        snap = PriceSnapshot(
            symbol="NIFTYBEES", yf_symbol="NIFTYBEES.NS",
            price=float("nan"), ath=297.55, high_52w=297.55,
            ath_pct=float("nan"), high_52w_pct=float("nan"),
            history_days=2000,
        )
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding("NIFTYBEES")], _market({"NIFTYBEES": snap}))
        assert alerts == []

    def test_skips_when_no_ath(self):
        snap = PriceSnapshot(
            symbol="INFY", yf_symbol="INFY.NS",
            price=1000.0, ath=None, high_52w=None,
            ath_pct=None, high_52w_pct=None,
            history_days=2000,
        )
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert alerts == []

    def test_disabled_rule_returns_empty(self):
        cfg = _config()
        cfg.sell_near_high.enabled = False
        snap = _snap(price=1100.0, ath=1100.0)
        rule = SellNearHighRule(cfg)
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert alerts == []

    def test_payload_fields(self):
        snap = _snap(price=1000.0, ath=1100.0, high_52w=1050.0)
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding(qty=100, avg=900.0)], _market({"INFY": snap}))
        p = alerts[0].payload
        assert p["price"] == pytest.approx(1000.0)
        assert p["ath"] == pytest.approx(1100.0)
        assert p["ath_pct"] == pytest.approx(1000 / 1100)
        assert p["quantity"] == 100
        assert p["average_cost"] == pytest.approx(900.0)
        assert p["current_value"] == pytest.approx(100 * 1000.0)
        assert p["cost_basis"] == pytest.approx(100 * 900.0)
        assert p["unrealized_pl"] == pytest.approx(100 * 1000 - 100 * 900)
        assert p["unrealized_pl_pct"] == pytest.approx((100_000 - 90_000) / 90_000)

    def test_multiple_holdings_independent(self):
        snaps = {
            "INFY": _snap("INFY", price=1000.0, ath=1100.0),   # 91% — fires
            "SBIN": _snap("SBIN", price=700.0, ath=1000.0),    # 70% — no fire
            "TCS":  _snap("TCS",  price=3500.0, ath=4000.0),   # 87.5% — fires
        }
        holdings = [
            _holding("INFY"), _holding("SBIN"), _holding("TCS"),
        ]
        rule = SellNearHighRule(_config(threshold=0.85))
        alerts = rule.evaluate(holdings, _market(snaps))
        fired = {a.symbol for a in alerts}
        assert fired == {"INFY", "TCS"}

    def test_alert_title_contains_symbol_and_pct(self):
        snap = _snap(price=935.0, ath=1100.0)  # 85%
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert "INFY" in alerts[0].title
        assert "85%" in alerts[0].title

    def test_severity_is_high(self):
        snap = _snap(price=1000.0, ath=1100.0)
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        from india_monitor.rules.sell_near_high import Severity
        assert alerts[0].severity == Severity.HIGH

    def test_skips_insufficient_history(self):
        """KWIL-style: only 3 months of data → ATH unreliable → skip."""
        snap = _snap(price=29.0, ath=31.0, history_days=90)  # 90 days < 365
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding("KWIL", avg=49.0)], _market({"KWIL": snap}))
        assert alerts == []

    def test_fires_when_history_meets_minimum(self):
        """Exactly at the minimum threshold should fire."""
        snap = _snap(price=1000.0, ath=1100.0, history_days=365)
        rule = SellNearHighRule(_config())
        alerts = rule.evaluate([_holding()], _market({"INFY": snap}))
        assert len(alerts) == 1

    def test_skips_when_underwater(self):
        """Stock at 91% of ATH but price < avg cost → skip (selling at a loss)."""
        snap = _snap(price=1000.0, ath=1100.0)   # 91% — would fire
        rule = SellNearHighRule(_config())
        holding = _holding(avg=1100.0)            # avg cost higher than current price
        alerts = rule.evaluate([holding], _market({"INFY": snap}))
        assert alerts == []

    def test_fires_when_profitable(self):
        """Price > avg cost → should fire normally."""
        snap = _snap(price=1000.0, ath=1100.0)
        rule = SellNearHighRule(_config())
        holding = _holding(avg=800.0)             # price well above avg cost
        alerts = rule.evaluate([holding], _market({"INFY": snap}))
        assert len(alerts) == 1

    def test_profitable_only_flag_off_allows_underwater(self):
        """When profitable_only=False, even underwater positions can alert."""
        cfg = _config()
        cfg.sell_near_high.profitable_only = False
        snap = _snap(price=1000.0, ath=1100.0)
        holding = _holding(avg=1200.0)            # underwater
        rule = SellNearHighRule(cfg)
        alerts = rule.evaluate([holding], _market({"INFY": snap}))
        assert len(alerts) == 1


class TestStoreIntegration:
    def test_cooldown_prevents_second_alert(self, tmp_path):
        from india_monitor.store import Store
        store = Store(db_path=tmp_path / "state.db")
        assert not store.in_cooldown("INFY", "sell_near_high", 7)
        store.record(symbol="INFY", rule="sell_near_high", severity="high",
                     title="test", payload={})
        assert store.in_cooldown("INFY", "sell_near_high", 7)
        assert not store.in_cooldown("TCS", "sell_near_high", 7)
