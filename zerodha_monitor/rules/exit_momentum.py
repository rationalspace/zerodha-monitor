"""Rule: Exit Momentum.

Three distinct paths:

HOLD tier (any P&L)
  - Silent until price reaches avg_cost × (1 + break_even_buffer)
  - Then fire a break-even recovery alert — regardless of analyst upside

ACTIVE tier + PROFITABLE (price ≥ avg cost)
  - Fire only when momentum signal exists AND analyst upside ≤ profitable_max_analyst_upside (7%)
  - Prevents alerting on stocks that still have a lot of runway left

ACTIVE tier + LOSS (price < avg cost)
  - Silent until price reaches avg_cost × (1 + break_even_buffer)
  - Then fire a break-even recovery alert — no momentum required, no analyst gate
  - Philosophy: sell into the recovery, don't be greedy
"""

from __future__ import annotations

import logging

from ..config_loader import AppConfig
from ..holdings_loader import Holding
from ..market_data import IndiaMarketData
from .sell_near_high import Alert, Severity

log = logging.getLogger(__name__)

_ACTIVE_TIERS = {"exit_now", "exit_soon", "sell_on_rally"}


def _payload(holding, snap, rsi, fund, unrealized_pl, unrealized_pl_pct, triggers, analyst_upside, tier):
    ltcg_tax = unrealized_pl * 0.125 if unrealized_pl > 0 else 0.0
    return {
        "symbol": holding.symbol,
        "sector": holding.sector,
        "long_term": holding.long_term,
        "exit_tier": tier,
        "exit_note": holding.exit_note,
        "price": snap.price,
        "average_cost": holding.average_cost,
        "quantity": holding.quantity,
        "current_value": holding.quantity * snap.price,
        "cost_basis": holding.cost_basis_total,
        "unrealized_pl": unrealized_pl,
        "unrealized_pl_pct": unrealized_pl_pct,
        "day_change_pct": snap.day_change_pct,
        "day_change_abs": snap.day_change_abs,
        "day_value_change": (snap.day_change_abs * holding.quantity
                             if snap.day_change_abs is not None else None),
        "five_day_return_pct": snap.five_day_return_pct,
        "consecutive_up_days": snap.consecutive_up_days,
        "ath": snap.ath,
        "ath_pct": snap.ath_pct,
        "high_52w": snap.high_52w,
        "high_52w_pct": snap.high_52w_pct,
        "rsi_14": rsi,
        "triggers": triggers,
        "analyst_target": fund.analyst_target_mean if fund else None,
        "analyst_target_high": fund.analyst_target_high if fund else None,
        "analyst_target_low": fund.analyst_target_low if fund else None,
        "analyst_recommendation": fund.analyst_recommendation if fund else None,
        "analyst_count": fund.analyst_count if fund else None,
        "analyst_upside": analyst_upside,
        "ltcg_tax": ltcg_tax,
        "trailing_pe": fund.trailing_pe if fund else None,
        "forward_pe": fund.forward_pe if fund else None,
        "revenue_yoy": fund.revenue_yoy if fund else None,
        "op_margin": fund.op_margin if fund else None,
        "eps_history": fund.eps_history if fund else [],
    }


def _breakeven_alert(holding, snap, cfg, market, tier) -> Alert | None:
    """Build a break-even recovery alert. Returns None if below threshold."""
    avg_cost = holding.average_cost
    threshold = avg_cost * (1 + cfg.break_even_buffer)
    if snap.price < threshold:
        return None

    pct_above = (snap.price - avg_cost) / avg_cost
    rsi = market.rsi_14(holding.symbol)
    try:
        fund = market.fundamentals(holding.symbol)
    except Exception as exc:  # noqa: BLE001
        log.warning("Fundamentals fetch failed for %s: %s", holding.symbol, exc)
        fund = None

    analyst_upside = None
    if fund and fund.analyst_target_mean and snap.price:
        analyst_upside = (fund.analyst_target_mean - snap.price) / snap.price

    cost_basis    = holding.cost_basis_total
    current_value = holding.quantity * snap.price
    unrealized_pl = current_value - cost_basis
    unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis else 0.0

    data_date_str = snap.data_date.strftime("%b %d") if snap.data_date else "?"
    triggers = [f"Break-even recovery: ₹{snap.price:,.0f} ≥ avg cost ₹{avg_cost:,.0f} ({pct_above:+.1%})"]
    if snap.day_change_pct and snap.day_change_pct > 0:
        triggers.append(f"day +{snap.day_change_pct:.1%}")
    if snap.five_day_return_pct and snap.five_day_return_pct > 0:
        triggers.append(f"5d +{snap.five_day_return_pct:.1%}")

    title = (f"{holding.symbol} — break-even recovery ({data_date_str}): "
             f"₹{snap.price:,.0f} vs avg ₹{avg_cost:,.0f}")
    body  = (f"⚠️ Data as of {data_date_str}. "
             f"{holding.symbol} recovered to ₹{snap.price:,.2f} ({pct_above:+.1%} vs avg ₹{avg_cost:,.2f}). "
             f"Sell into momentum — don't wait for more upside.")

    log.info("BREAK-EVEN ALERT %s: ₹%.2f vs avg ₹%.2f (%+.1f%%)",
             holding.symbol, snap.price, avg_cost, pct_above * 100)

    return Alert(
        symbol=holding.symbol, rule=ExitMomentumRule.name,
        severity=Severity.HIGH, title=title, body=body,
        payload=_payload(holding, snap, rsi, fund, unrealized_pl,
                         unrealized_pl_pct, triggers, analyst_upside, tier),
    )


class ExitMomentumRule:
    name = "exit_momentum"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.exit_momentum.enabled

    def evaluate(self, holdings: list[Holding], market: IndiaMarketData) -> list[Alert]:
        if not self.enabled:
            return []

        cfg    = self.config.exit_momentum
        alerts: list[Alert] = []

        for holding in holdings:
            snap = market.snapshot(holding.symbol)
            if snap is None:
                log.warning("No market data for %s — skipping", holding.symbol)
                continue

            avg_cost      = holding.average_cost
            cost_basis    = holding.cost_basis_total
            current_value = holding.quantity * snap.price
            unrealized_pl = current_value - cost_basis
            unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis else 0.0
            tier = holding.exit_tier

            # ── PATH 1: HOLD tier — only break-even recovery ─────────────────
            if tier == "hold" or tier not in _ACTIVE_TIERS:
                if tier == "hold":
                    alert = _breakeven_alert(holding, snap, cfg, market, tier)
                    if alert:
                        alerts.append(alert)
                # Any unrecognised tier → silently skip
                continue

            # ── PATH 2: ACTIVE tier + PROFITABLE ─────────────────────────────
            # Momentum signal required; suppressed if analyst upside > 7% gate.
            if snap.price >= avg_cost:
                rsi = market.rsi_14(holding.symbol)

                is_exit_now      = (tier == "exit_now")
                rsi_thr   = cfg.exit_now_rsi_threshold  if is_exit_now else cfg.rally_rsi_threshold
                day_thr   = cfg.exit_now_day_pop_pct    if is_exit_now else cfg.rally_day_pop_pct
                five_thr  = cfg.exit_now_five_day_pct   if is_exit_now else cfg.rally_five_day_pct
                con_thr   = cfg.exit_now_consecutive_days if is_exit_now else cfg.rally_consecutive_days

                triggers: list[str] = []
                if rsi is not None and rsi >= rsi_thr:
                    triggers.append(f"RSI {rsi:.1f} ≥ {rsi_thr:.0f}")
                if snap.consecutive_up_days is not None and snap.consecutive_up_days >= con_thr:
                    triggers.append(f"{snap.consecutive_up_days} consecutive up days")
                if snap.day_change_pct is not None and snap.day_change_pct >= day_thr:
                    triggers.append(f"day +{snap.day_change_pct:.1%}")
                if snap.five_day_return_pct is not None and snap.five_day_return_pct >= five_thr:
                    triggers.append(f"5d +{snap.five_day_return_pct:.1%}")

                if not triggers:
                    continue

                try:
                    fund = market.fundamentals(holding.symbol)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Fundamentals fetch failed for %s: %s", holding.symbol, exc)
                    fund = None

                analyst_upside: float | None = None
                if fund and fund.analyst_target_mean and snap.price:
                    analyst_upside = (fund.analyst_target_mean - snap.price) / snap.price

                gate = cfg.profitable_max_analyst_upside
                if gate is not None and analyst_upside is not None and analyst_upside > gate:
                    log.info("GATE %s: profitable, %.1f%% analyst upside > %.0f%% gate",
                             holding.symbol, analyst_upside * 100, gate * 100)
                    continue

                triggers_str  = ", ".join(triggers)
                data_date_str = snap.data_date.strftime("%b %d") if snap.data_date else "?"
                title = f"{holding.symbol} [{tier}] — exit window ({data_date_str}): {triggers_str}"
                body  = (
                    f"⚠️ Data as of {data_date_str} — verify in Zerodha before acting. "
                    f"Price ₹{snap.price:,.2f} | Gain: {unrealized_pl_pct:+.1%} (₹{unrealized_pl:+,.0f}). "
                    + (f"Analyst upside: {analyst_upside:+.1%}." if analyst_upside is not None else "")
                )

                alerts.append(Alert(
                    symbol=holding.symbol, rule=self.name,
                    severity=Severity.HIGH if is_exit_now else Severity.MEDIUM,
                    title=title, body=body,
                    payload=_payload(holding, snap, rsi, fund, unrealized_pl,
                                     unrealized_pl_pct, triggers, analyst_upside, tier),
                ))
                log.info("ALERT %s [%s]: triggers=%s", holding.symbol, tier, triggers_str)

            # ── PATH 3: ACTIVE tier + LOSS — break-even gate ─────────────────
            # No momentum required. Fire the moment price clears avg_cost.
            else:
                alert = _breakeven_alert(holding, snap, cfg, market, tier)
                if alert:
                    alerts.append(alert)

        return alerts
