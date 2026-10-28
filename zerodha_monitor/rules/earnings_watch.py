"""Rule: Earnings Watch — track quarterly results dates for held India positions.

Momentum rules tell you what the price is doing; this rule tells you why it
might be about to move. For positions held through a catalyst (e.g. a `hold`
tier stock waiting on quarterly results), the earnings print matters more
than the daily tape.

Per earnings event the digest gets:

  Countdown   — a DIGEST-severity row on each session within `days_ahead`
                days of results ("INFY — Results in 3 days (Thu Jul 23)")
  Report day  — results due today (MEDIUM)
  Reaction    — the first trading session after the print, with the day's
                move (MEDIUM; HIGH when the move is ≥ big_reaction_pct)

Earnings dates come from Yahoo Finance and can be missing or stale for NSE
tickers — absence of an alert means "no data", not "no earnings due".
"""

from __future__ import annotations

import logging

from ..config_loader import AppConfig
from ..holdings_loader import Holding
from ..market_data import IndiaMarketData
from .sell_near_high import Alert, Severity

log = logging.getLogger(__name__)


class EarningsWatchRule:
    name = "earnings_watch"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        cfg = getattr(self.config, "earnings_watch", None)
        return cfg.enabled if cfg else True

    def evaluate(self, holdings: list[Holding], market: IndiaMarketData) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.earnings_watch
        alerts: list[Alert] = []

        for holding in holdings:
            symbol = holding.symbol
            try:
                dates = market.earnings_dates(symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("Earnings dates fetch failed for %s: %s", symbol, exc)
                continue
            if not dates:
                continue

            snap = market.snapshot(symbol)
            if snap is None or snap.data_date is None:
                continue
            as_of = snap.data_date

            next_e = min((d for d in dates if d >= as_of), default=None)
            last_e = max((d for d in dates if d < as_of), default=None)

            triggers: list[str] = []
            severity = Severity.DIGEST

            if next_e is not None:
                days_until = (next_e - as_of).days
                if days_until == 0:
                    triggers.append(f"Results due today ({next_e:%b %d}) — watch the print")
                    severity = Severity.MEDIUM
                elif days_until <= cfg.days_ahead:
                    plural = "s" if days_until > 1 else ""
                    triggers.append(f"Results in {days_until} day{plural} ({next_e:%a %b %d})")

            # Reaction: the first trading session after the print. days_since == 1
            # covers Mon–Thu announcements; the Monday clause covers Fri/weekend
            # prints. Market holidays after results can cause a rare miss.
            if last_e is not None:
                days_since = (as_of - last_e).days
                first_session_after = days_since == 1 or (
                    as_of.weekday() == 0 and days_since in (2, 3)
                )
                if first_session_after:
                    move = snap.day_change_pct
                    if move is not None:
                        triggers.append(
                            f"Post-earnings session: {move:+.1%} ({last_e:%b %d} results)"
                        )
                        severity = (
                            Severity.HIGH if abs(move) >= cfg.big_reaction_pct
                            else Severity.MEDIUM
                        )
                    else:
                        triggers.append(f"First session after {last_e:%b %d} results")
                        severity = Severity.MEDIUM

            if not triggers:
                continue

            price = snap.price
            cost_basis = holding.cost_basis_total
            current_value = holding.quantity * price
            unrealized_pl = current_value - cost_basis
            unrealized_pl_pct = (unrealized_pl / cost_basis) if cost_basis else None
            ltcg_tax = unrealized_pl * 0.125 if unrealized_pl > 0 else 0.0

            try:
                fund = market.fundamentals(symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("Fundamentals fetch failed for %s: %s", symbol, exc)
                fund = None

            analyst_upside = None
            if fund and fund.analyst_target_mean and price:
                analyst_upside = (fund.analyst_target_mean - price) / price

            alerts.append(Alert(
                symbol=symbol,
                rule=self.name,
                severity=severity,
                title=f"{symbol} — {triggers[0]}",
                body="; ".join(triggers),
                payload={
                    "symbol":    symbol,
                    "exit_tier": holding.exit_tier,
                    "exit_note": holding.exit_note,
                    "price":     price,
                    "earnings_next": str(next_e) if next_e else None,
                    "earnings_last": str(last_e) if last_e else None,
                    # Fields the digest template expects on every alert row:
                    "cost_basis": cost_basis,
                    "unrealized_pl": unrealized_pl,
                    "unrealized_pl_pct": unrealized_pl_pct,
                    "day_change_pct": snap.day_change_pct,
                    "ath_pct": snap.ath_pct,
                    "ltcg_tax": ltcg_tax,
                    "analyst_target": fund.analyst_target_mean if fund else None,
                    "analyst_upside": analyst_upside,
                    "analyst_recommendation": fund.analyst_recommendation if fund else None,
                    "analyst_count": fund.analyst_count if fund else None,
                    "triggers": triggers,
                    "bounce_quality": None,
                },
            ))
            log.info("ALERT %s [earnings_watch]: %s", symbol, "; ".join(triggers))

        return alerts
