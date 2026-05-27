"""Rule: Sell-near-high.

Fires when a long-term holding's current price is >= 85% of its all-time high
(threshold configurable in config.yaml). The idea: the Indian market has been
weak — when a stock bounces back toward its ATH it may be a good exit window,
especially for LTCG-eligible lots.

Alert includes:
- Current price vs ATH (₹ and %)
- Unrealized gain since average cost
- Position value at current price
- 52-week high for context
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..config_loader import AppConfig
from ..holdings_loader import Holding
from ..market_data import IndiaMarketData, PriceSnapshot

log = logging.getLogger(__name__)


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    DIGEST = "digest"


@dataclass
class Alert:
    symbol: str
    rule: str
    severity: Severity
    title: str
    body: str
    payload: dict[str, Any] = field(default_factory=dict)
    fired_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()


class SellNearHighRule:
    name = "sell_near_high"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.sell_near_high.enabled

    def evaluate(self, holdings: list[Holding], market: IndiaMarketData) -> list[Alert]:
        if not self.enabled:
            return []

        cfg = self.config.sell_near_high
        alerts: list[Alert] = []

        for holding in holdings:
            if cfg.long_term_only and not holding.long_term:
                log.debug("Skipping %s — not long-term", holding.symbol)
                continue

            snap = market.snapshot(holding.symbol)
            if snap is None:
                log.warning("No market data for %s — skipping", holding.symbol)
                continue

            if snap.ath is None or snap.ath_pct is None:
                log.debug("No ATH data for %s — skipping", holding.symbol)
                continue

            if math.isnan(snap.price) or math.isnan(snap.ath_pct):
                log.warning("NaN price/ATH for %s — yfinance data issue, skipping", holding.symbol)
                continue

            if snap.history_days < cfg.min_history_days:
                log.warning(
                    "Skipping %s — only %d days of history (need %d); ATH unreliable",
                    holding.symbol, snap.history_days, cfg.min_history_days,
                )
                continue

            if cfg.profitable_only and snap.price < holding.average_cost:
                log.debug(
                    "Skipping %s — underwater (price ₹%.2f < avg cost ₹%.2f)",
                    holding.symbol, snap.price, holding.average_cost,
                )
                continue

            if snap.ath_pct < cfg.ath_threshold_pct:
                log.debug(
                    "%s at %.1f%% of ATH — below %.0f%% threshold",
                    holding.symbol, snap.ath_pct * 100, cfg.ath_threshold_pct * 100,
                )
                continue

            # Compute gain metrics
            current_value = holding.quantity * snap.price
            cost_basis = holding.cost_basis_total
            unrealized_pl = current_value - cost_basis
            unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis else 0.0

            day_value_change = (
                snap.day_change_abs * holding.quantity
                if snap.day_change_abs is not None else None
            )

            payload = {
                "symbol": holding.symbol,
                "sector": holding.sector,
                "long_term": holding.long_term,
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
            }

            title = (
                f"{holding.symbol} at {snap.ath_pct:.0%} of ATH"
                f" — consider selling (LTCG eligible)"
            )
            body = (
                f"Price ₹{snap.price:,.2f} is {snap.ath_pct:.0%} of ATH ₹{snap.ath:,.2f}. "
                f"Unrealized gain: {unrealized_pl_pct:+.1%} (₹{unrealized_pl:+,.0f}). "
                f"Position value: ₹{current_value:,.0f}."
            )

            alerts.append(
                Alert(
                    symbol=holding.symbol,
                    rule=self.name,
                    severity=Severity.HIGH,
                    title=title,
                    body=body,
                    payload=payload,
                )
            )
            log.info(
                "ALERT %s: price ₹%.2f = %.1f%% of ATH ₹%.2f",
                holding.symbol, snap.price, snap.ath_pct * 100, snap.ath,
            )

        return alerts
