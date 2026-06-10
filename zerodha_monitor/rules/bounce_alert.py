"""Rule: Bounce Alert.

Fires for wait_for_bounce tier when the stock shows recovery signals
after being oversold. This signals that the exit window is opening —
the position should be exited on this bounce.

Phase 3 additions:
  - Bounce quality tier: MA50_RECLAIM / ABOVE_MA200 / BELOW_BOTH_MAS
  - MA50 reclaim as an explicit trigger (configurable via ma50_reclaim_as_trigger)
  - Human-friendly alert titles reflecting how strong the recovery is
  - bounce_quality + bounce_summary added to payload for the digest template
"""

from __future__ import annotations

import logging

from ..config_loader import AppConfig
from ..holdings_loader import Holding
from ..market_data import IndiaMarketData
from .sell_near_high import Alert, Severity

log = logging.getLogger(__name__)


# ── Bounce quality tier helpers (Phase 3) ────────────────────────────────────

def _bounce_quality_info(snap, rsi: float | None) -> tuple[str, str]:
    """Return (quality_code, human_summary) based on where price sits relative to MAs.

    Quality codes and what they mean:
      MA50_RECLAIM  — price above 50-day average: meaningful recovery milestone
      ABOVE_MA200   — above long-term average but not yet at 50-day: healthy bounce
      BELOW_BOTH_MAS — below both averages: early signals only, needs confirmation
    """
    if snap.above_ma50 is True:
        code = "MA50_RECLAIM"
        ma50_str  = f"₹{snap.ma50:,.0f}" if snap.ma50 else "the 50-day average"
        ma200_str = f"₹{snap.ma200:,.0f}" if snap.ma200 else "the 200-day average"
        summary = (
            f"Recovery gaining strength — price is now above its 50-day average ({ma50_str}). "
            "This is a meaningful milestone, not just a brief bounce. "
            f"The long-term trend is also intact (above {ma200_str}). "
            "This is a good moment to exit the position into this strength."
            if snap.above_ma200 else
            f"Recovery has reached the 50-day average ({ma50_str}) — a key milestone. "
            "However, the longer-term trend is still under pressure. "
            "Consider exiting into this bounce before momentum fades."
        )
    elif snap.above_ma200 is True:
        code = "ABOVE_MA200"
        ma50_str  = f"₹{snap.ma50:,.0f}" if snap.ma50 else "the 50-day average"
        ma200_str = f"₹{snap.ma200:,.0f}" if snap.ma200 else "the 200-day average"
        summary = (
            f"Recovery underway with the long-term trend still intact "
            f"(above {ma200_str}). "
            "Healthy bounce — the stock hasn't lost its long-term footing. "
            f"Next milestone to watch: a close above {ma50_str} (50-day average) "
            "would confirm the recovery is sustained."
        )
    else:
        code = "BELOW_BOTH_MAS"
        parts = []
        if snap.ma50:
            parts.append(f"₹{snap.ma50:,.0f} (50-day)")
        if snap.ma200:
            parts.append(f"₹{snap.ma200:,.0f} (200-day)")
        levels = " and ".join(parts) if parts else "key moving averages"
        summary = (
            f"Early recovery signals are present, but price is still below {levels}. "
            "The bounce is encouraging but needs more confirmation before calling it a true recovery. "
            f"Watch for a close above {parts[0] if parts else 'the 50-day average'} as the first confirmation."
        )

    return code, summary


_QUALITY_TITLES = {
    "MA50_RECLAIM":   "{symbol} — Recovery confirmed: past the 50-day average — exit into this strength",
    "ABOVE_MA200":    "{symbol} — Bounce underway, long-term trend intact — exit window opening",
    "BELOW_BOTH_MAS": "{symbol} — Recovery starting, still below key averages — watch for confirmation",
}


class BounceAlertRule:
    name = "bounce_alert"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.bounce_alert.enabled

    def evaluate(self, holdings: list[Holding], market: IndiaMarketData) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.bounce_alert
        ma50_trigger_enabled = getattr(cfg, "ma50_reclaim_as_trigger", True)
        alerts: list[Alert] = []

        for holding in holdings:
            if holding.exit_tier != "wait_for_bounce":
                continue

            snap = market.snapshot(holding.symbol)
            if snap is None:
                log.warning("No market data for %s — skipping", holding.symbol)
                continue

            rsi = market.rsi_14(holding.symbol)

            # Collect which conditions triggered
            triggers: list[str] = []
            if rsi is not None and rsi >= cfg.rsi_recovery_threshold:
                triggers.append(f"RSI {rsi:.1f} ≥ {cfg.rsi_recovery_threshold:.0f} (recovering from oversold)")
            if snap.consecutive_up_days is not None and snap.consecutive_up_days >= cfg.consecutive_up_days:
                triggers.append(f"{snap.consecutive_up_days} consecutive up days")
            if snap.five_day_return_pct is not None and snap.five_day_return_pct >= cfg.five_day_pct:
                triggers.append(f"5d return +{snap.five_day_return_pct:.1%}")
            # Phase 3: MA50 reclaim as an explicit trigger
            if ma50_trigger_enabled and snap.ma50 is not None and snap.above_ma50 is True:
                triggers.append(
                    f"Above 50-day average (₹{snap.ma50:,.0f}) — key recovery milestone"
                )

            if not triggers:
                log.debug(
                    "%s [wait_for_bounce]: no bounce trigger met (RSI=%.1f, 5d=%.2f%%, consec=%d)",
                    holding.symbol,
                    rsi or 0.0,
                    (snap.five_day_return_pct or 0) * 100,
                    snap.consecutive_up_days or 0,
                )
                continue

            # ── Phase 3: Bounce quality tier ─────────────────────────────────
            bounce_quality, bounce_summary = _bounce_quality_info(snap, rsi)

            # Fetch fundamentals for enriched payload
            try:
                fund = market.fundamentals(holding.symbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("Fundamentals fetch failed for %s: %s", holding.symbol, exc)
                fund = None

            # Compute gain metrics
            current_value = holding.quantity * snap.price
            cost_basis = holding.cost_basis_total
            unrealized_pl = current_value - cost_basis
            unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis else 0.0

            day_value_change = (
                snap.day_change_abs * holding.quantity
                if snap.day_change_abs is not None else None
            )

            analyst_upside: float | None = None
            if fund and fund.analyst_target_mean and snap.price:
                analyst_upside = (fund.analyst_target_mean - snap.price) / snap.price

            ltcg_tax = unrealized_pl * 0.125 if unrealized_pl > 0 else 0.0

            payload: dict = {
                "symbol": holding.symbol,
                "sector": holding.sector,
                "long_term": holding.long_term,
                "exit_tier": "wait_for_bounce",
                "exit_note": holding.exit_note,
                "price": snap.price,
                "day_change_pct": snap.day_change_pct,
                "day_change_abs": snap.day_change_abs,
                "day_value_change": day_value_change,
                "five_day_return_pct": snap.five_day_return_pct,
                "consecutive_up_days": snap.consecutive_up_days,
                "ath": snap.ath,
                "ath_pct": snap.ath_pct,
                "high_52w": snap.high_52w,
                "high_52w_pct": snap.high_52w_pct,
                "quantity": holding.quantity,
                "average_cost": holding.average_cost,
                "current_value": current_value,
                "cost_basis": cost_basis,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                "rsi_14": rsi,
                "triggers": triggers,
                # Phase 3: bounce quality
                "bounce_quality": bounce_quality,
                "bounce_summary": bounce_summary,
                "ma50":        snap.ma50,
                "ma200":       snap.ma200,
                "bb_pct_b":    snap.bb_pct_b,
                "above_ma50":  snap.above_ma50,
                "above_ma200": snap.above_ma200,
                # Fundamentals
                "trailing_pe": fund.trailing_pe if fund else None,
                "forward_pe": fund.forward_pe if fund else None,
                "revenue_yoy": fund.revenue_yoy if fund else None,
                "op_margin": fund.op_margin if fund else None,
                "analyst_target": fund.analyst_target_mean if fund else None,
                "analyst_target_high": fund.analyst_target_high if fund else None,
                "analyst_target_low": fund.analyst_target_low if fund else None,
                "analyst_recommendation": fund.analyst_recommendation if fund else None,
                "analyst_count": fund.analyst_count if fund else None,
                "analyst_upside": analyst_upside,
                "ltcg_tax": ltcg_tax,
                "eps_history": fund.eps_history if fund else [],
            }

            # ── Human-friendly title (Phase 3) ────────────────────────────────
            title = _QUALITY_TITLES[bounce_quality].format(symbol=holding.symbol)

            triggers_str = ", ".join(triggers)
            data_date_str = snap.data_date.strftime("%b %d") if snap.data_date else "unknown date"
            body_parts = [
                f"⚠️ Data as of {data_date_str} — verify live price in Zerodha before acting.",
                bounce_summary,
                f"Price ₹{snap.price:,.2f} | Unrealized gain: {unrealized_pl_pct:+.1%} (₹{unrealized_pl:+,.0f}).",
                f"Signals: {triggers_str}.",
            ]
            if fund and fund.analyst_recommendation:
                body_parts.append(f"Analyst: {fund.analyst_recommendation}.")
            if analyst_upside is not None:
                direction = "upside" if analyst_upside >= 0 else "downside"
                body_parts.append(
                    f"Analyst target ₹{fund.analyst_target_mean:,.0f} "
                    f"({analyst_upside:+.1%} {direction})."
                )
            body = " ".join(body_parts)

            alerts.append(Alert(
                symbol=holding.symbol,
                rule=self.name,
                severity=Severity.HIGH,
                title=title,
                body=body,
                payload=payload,
            ))
            log.info(
                "ALERT %s [wait_for_bounce] quality=%s triggers=%s",
                holding.symbol, bounce_quality, triggers_str,
            )

        return alerts
