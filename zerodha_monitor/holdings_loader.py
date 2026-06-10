"""Load and validate holdings from holdings.yaml.

Each holding represents a Zerodha position. The file is the source of truth for
what you own — update it after any buy/sell, or re-run the import script.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Holding:
    symbol: str          # NSE symbol, e.g. "INFY"
    quantity: float
    average_cost: float  # ₹ per share (cost basis)
    sector: str
    long_term: bool      # True = acquired > 1 year ago (LTCG eligible)
    exit_tier: str = ""  # exit_now | exit_soon | sell_on_rally | wait_for_bounce | hold | ""
    exit_note: str = ""  # brief rationale for the exit decision
    exit_pop_threshold: float | None = None  # If set, only fire when day OR 5d move >= this (e.g. 0.05 = 5%)

    @property
    def ns_symbol(self) -> str:
        """Yahoo Finance ticker: appends .NS for NSE listings."""
        return f"{self.symbol}.NS"

    @property
    def cost_basis_total(self) -> float:
        return self.quantity * self.average_cost


def load_holdings(path: Path) -> list[Holding]:
    """Parse holdings.yaml and return a list of Holding objects.

    Raises ValueError on missing required fields or invalid data.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or "holdings" not in raw:
        raise ValueError(f"{path}: expected a top-level 'holdings' list")

    holdings: list[Holding] = []
    for i, item in enumerate(raw["holdings"]):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: item {i} is not a mapping")
        missing = [k for k in ("symbol", "quantity", "average_cost") if k not in item]
        if missing:
            raise ValueError(f"{path}: item {i} missing required fields: {missing}")
        holdings.append(
            Holding(
                symbol=str(item["symbol"]).upper().strip(),
                quantity=float(item["quantity"]),
                average_cost=float(item["average_cost"]),
                sector=str(item.get("sector", "")).strip(),
                long_term=bool(item.get("long_term", False)),
                exit_tier=str(item.get("exit_tier", "")).strip(),
                exit_note=str(item.get("exit_note", "")).strip(),
                exit_pop_threshold=float(item["exit_pop_threshold"]) if item.get("exit_pop_threshold") is not None else None,
            )
        )
    return holdings


def project_root() -> Path:
    """Resolve the project root (directory containing holdings.yaml)."""
    env_home = __import__("os").environ.get("INDIA_MONITOR_HOME")
    if env_home:
        return Path(env_home)
    # Walk up from this file until we find holdings.yaml.
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        if (candidate / "holdings.yaml").exists():
            return candidate
    raise RuntimeError(
        "Cannot find holdings.yaml. Set INDIA_MONITOR_HOME or run from the project root."
    )
