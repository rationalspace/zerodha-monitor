"""Rule: MA Crossover (Golden Cross / Death Cross) for India holdings.

Detects when the 50-day moving average crosses the 200-day moving average
for any held Indian position and fires a human-friendly alert explaining
what the crossing means for that specific holding.

  Golden Cross — MA50 crosses ABOVE MA200
    For wait_for_bounce: strongest possible exit signal — recovery confirmed.
    For other holdings: informational, recovery momentum confirmed.

  Death Cross — MA50 crosses BELOW MA200
    For exit-tier holdings: reason to act sooner rather than waiting.
    For other holdings: watch signal, trend deteriorating.

No jargon in alert titles — plain English that tells you what to do.
"""

from __future__ import annotations

import logging

from ..config_loader import AppConfig
from ..holdings_loader import Holding
from ..market_data import IndiaMarketData
from .sell_near_high import Alert, Severity

log = logging.getLogger(__name__)

_EXIT_TIERS = {"exit_now", "exit_soon", "sell_on_rally", "wait_for_bounce"}


class MaCrossoverRule:
    name = "ma_crossover"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        cfg = getattr(self.config, "ma_crossover", None)
        return cfg.enabled if cfg else True

    def evaluate(self, holdings: list[Holding], market: IndiaMarketData) -> list[Alert]:
        if not self.enabled:
            return []

        alerts: list[Alert] = []

        for holding in holdings:
            symbol = holding.symbol

            try:
                cross = market.ma_crossover(symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("MA crossover check failed for %s: %s", symbol, exc)
                continue

            if not cross.get("golden") and not cross.get("death"):
                continue

            ma50   = cross.get("ma50")
            ma200  = cross.get("ma200")
            gap    = cross.get("gap_pct")
            is_exit   = holding.exit_tier in _EXIT_TIERS
            is_bounce = holding.exit_tier == "wait_for_bounce"
            snap   = market.snapshot(symbol)
            price  = snap.price if snap else None

            # ── Recovery gates for golden cross on loss positions ────────────
            # A golden cross on a stock still deeply underwater is noise.
            # Only fire if the same gates as bounce_alert pass:
            #   require_consecutive_days: 3+ consecutive up days
            #   require_near_breakeven:   price within threshold of avg cost
            if cross.get("golden") and snap is not None and price is not None:
                at_loss = price < holding.average_cost
                if at_loss:
                    ba_cfg = self.config.bounce_alert
                    if ba_cfg.require_consecutive_days:
                        has_consec = (
                            snap.consecutive_up_days is not None
                            and snap.consecutive_up_days >= ba_cfg.consecutive_up_days
                        )
                        if not has_consec:
                            log.debug(
                                "%s golden cross suppressed: require_consecutive_days not met (%s days)",
                                symbol, snap.consecutive_up_days,
                            )
                            continue
                    if ba_cfg.require_near_breakeven:
                        floor = holding.average_cost * (1 - ba_cfg.near_breakeven_threshold)
                        if price < floor:
                            log.debug(
                                "%s golden cross suppressed: price ₹%.0f still below breakeven floor ₹%.0f",
                                symbol, price, floor,
                            )
                            continue

            ma50_str  = f"₹{ma50:,.0f}"  if ma50  else "—"
            ma200_str = f"₹{ma200:,.0f}" if ma200 else "—"
            gap_abs   = abs((gap or 0) * (ma200 or 0))

            if cross.get("golden"):
                if is_bounce:
                    title   = f"{symbol} — Recovery fully confirmed: above both averages — exit into this strength"
                    summary = (
                        f"{symbol}'s short-term average ({ma50_str}) has crossed above the long-term average "
                        f"({ma200_str}). For a 'wait for bounce' position, this is the strongest confirmation "
                        "you can get that the recovery is real and sustained, not a dead-cat bounce. "
                        "This is the exit window you were waiting for. Consider selling into this strength "
                        "rather than holding for a higher price."
                    )
                    severity = Severity.HIGH
                elif is_exit:
                    title   = f"{symbol} — Recovery confirmed — good moment to exit"
                    summary = (
                        f"{symbol}'s short-term average ({ma50_str}) has crossed above the long-term "
                        f"average ({ma200_str}) — recovery momentum is confirmed. "
                        "For a position you're planning to exit, this is a favorable window."
                    )
                    severity = Severity.MEDIUM
                else:
                    title   = f"{symbol} — Recovery momentum confirmed (long-term trend reclaimed)"
                    summary = (
                        f"{symbol}'s 50-day average ({ma50_str}) has crossed back above the 200-day "
                        f"average ({ma200_str}). Short-term momentum has reclaimed the long-term trend — "
                        "a positive signal for your ongoing holding."
                    )
                    severity = Severity.MEDIUM

                body = (
                    f"50-day avg ({ma50_str}) crossed above 200-day avg ({ma200_str}) — "
                    "recovery confirmed"
                )

            else:  # death cross
                if is_exit:
                    title   = f"{symbol} — Trend worsening — consider acting sooner"
                    summary = (
                        f"{symbol}'s 50-day average ({ma50_str}) has dropped below the 200-day average "
                        f"({ma200_str}). For a position you're already planning to exit, this is a signal "
                        "to act sooner rather than waiting for a recovery that may take longer to materialise. "
                        "Selling pressure is becoming a sustained trend, not just a short-term dip."
                    )
                    severity = Severity.HIGH
                else:
                    title   = f"{symbol} — Selling pressure building (short-term trend weakened)"
                    summary = (
                        f"{symbol}'s 50-day average ({ma50_str}) has dropped below the 200-day average "
                        f"({ma200_str}). Selling has been sustained long enough to pull the short-term "
                        "trend below the long-term — worth monitoring closely. "
                        f"The gap is now ₹{gap_abs:,.0f}."
                    )
                    severity = Severity.MEDIUM

                body = (
                    f"50-day avg ({ma50_str}) dropped below 200-day avg ({ma200_str}) — "
                    "selling pressure building"
                )

            alerts.append(Alert(
                symbol=symbol,
                rule=self.name,
                severity=severity,
                title=title,
                body=body,
                payload={
                    "symbol":    symbol,
                    "exit_tier": holding.exit_tier,
                    "price":     price,
                    "crossover_type": "GOLDEN" if cross.get("golden") else "DEATH",
                    "ma50":      ma50,
                    "ma200":     ma200,
                    "gap_pct":   gap,
                    "crossover_summary": summary,
                },
            ))
            log.info(
                "ALERT %s cross (India): %s (MA50=%s, MA200=%s)",
                "golden" if cross.get("golden") else "death",
                symbol, ma50, ma200,
            )

        return alerts
