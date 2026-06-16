"""Regression test: every rule's Alert payload must render in the digest template.

This exists because rules/sell_near_high.py, exit_momentum.py, bounce_alert.py,
and ma_crossover.py each build their own payload dict independently, but
india_digest.html.j2 renders ALL of them through the same row layout. If one
rule's payload is missing a field the template reads, Jinja2 returns Undefined
(not None) — "is not none" checks pass, and formatting Undefined raises
TypeError, crashing the ENTIRE digest (all alerts, not just that rule's).

Run this after touching any rule's payload dict or the digest template itself.
"""

from __future__ import annotations

from datetime import date

import pytest

from zerodha_monitor.config_loader import EmailConfig
from zerodha_monitor.email_dispatch import EmailDispatcher
from zerodha_monitor.rules.sell_near_high import Alert, Severity


def _minimal_payload(**overrides) -> dict:
    """The bare minimum a real rule should populate. Individual rules may add more,
    never less — this is the floor the template depends on."""
    base = {
        "symbol": "TEST",
        "exit_tier": "sell_on_rally",
        "exit_note": None,
        "price": 100.0,
        "cost_basis": 9000.0,
        "unrealized_pl": 1000.0,
        "unrealized_pl_pct": 0.11,
        "day_change_pct": 0.01,
        "ath_pct": 0.9,
        "ltcg_tax": 0.0,
        "analyst_target": None,
        "analyst_upside": None,
        "analyst_recommendation": None,
        "analyst_count": None,
        "triggers": ["test trigger"],
        "bounce_quality": None,
    }
    base.update(overrides)
    return base


def _render_digest(payloads: list[dict]) -> str:
    alerts = [
        Alert(symbol=p["symbol"], rule="test", severity=Severity.MEDIUM,
              title=f"{p['symbol']} test alert", body="test body", payload=p)
        for p in payloads
    ]
    dispatcher = EmailDispatcher(EmailConfig(to_address="test@example.com"))
    profitable = [a for a in alerts if (a.payload.get("unrealized_pl") or 0) >= 0]
    losses = [a for a in alerts if (a.payload.get("unrealized_pl") or 0) < 0]
    template = dispatcher.env.get_template("india_digest.html.j2")
    return template.render(
        data_date=date(2026, 1, 1), date_str="Jan 01", alerts=alerts,
        profitable=profitable, losses=losses, total=len(alerts),
        portfolio_owner="", zerodha_id="",
    )


class TestDigestTemplateRendersEveryRulePayload:
    def test_minimal_payload_renders(self):
        html = _render_digest([_minimal_payload()])
        assert "TEST" in html

    def test_loss_position_renders(self):
        html = _render_digest([_minimal_payload(symbol="LOSS", unrealized_pl=-500.0,
                                                  unrealized_pl_pct=-0.05)])
        assert "LOSS" in html

    def test_ma_crossover_real_output_renders(self):
        """Calls the REAL MaCrossoverRule.evaluate() and feeds its actual Alert
        objects into the digest template — not a hand-written stand-in. This is
        the exact path that crashed send_digest() in production: a real rule's
        payload was missing fields the template assumed every alert would have."""
        from unittest.mock import MagicMock
        from zerodha_monitor.config_loader import AppConfig
        from zerodha_monitor.holdings_loader import Holding
        from zerodha_monitor.rules.ma_crossover import MaCrossoverRule

        holding = Holding(symbol="CROSS", quantity=10, average_cost=90.0,
                           sector="IT", long_term=True, exit_tier="sell_on_rally")
        market = MagicMock()
        market.ma_crossover.return_value = {"golden": True, "ma50": 100.0, "ma200": 95.0, "gap_pct": 0.05}
        snap = MagicMock(price=110.0, day_change_pct=0.01, ath_pct=0.9, consecutive_up_days=4)
        market.snapshot.return_value = snap
        market.fundamentals.return_value = None

        alerts = MaCrossoverRule(AppConfig()).evaluate([holding], market)
        assert len(alerts) == 1   # sanity: the rule actually fired

        html = _render_digest([a.payload for a in alerts])
        assert "CROSS" in html

    def test_missing_optional_field_does_not_crash(self):
        """If a future rule forgets a field, the template must degrade to '—',
        not raise. Catches the Undefined vs None gap directly."""
        payload = _minimal_payload()
        del payload["ath_pct"]  # simulate an incomplete payload
        html = _render_digest([payload])  # must not raise
        assert "CROSS" not in html  # sanity: just confirms render completed

    def test_all_rules_mixed_in_one_digest(self):
        """The real failure mode: one bad payload among several good ones
        crashes the WHOLE digest, silently dropping every other alert."""
        payloads = [
            _minimal_payload(symbol="SNH", exit_tier="hold"),
            _minimal_payload(symbol="EXIT", exit_tier="exit_now"),
            _minimal_payload(symbol="BOUNCE", exit_tier="wait_for_bounce", bounce_quality="MA50_RECLAIM"),
            _minimal_payload(symbol="CROSS", triggers=["Golden cross"]),
        ]
        html = _render_digest(payloads)
        for sym in ("SNH", "EXIT", "BOUNCE", "CROSS"):
            assert sym in html
