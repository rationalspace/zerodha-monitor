"""yfinance wrapper for Indian NSE stocks.

All symbols are auto-suffixed with .NS (NSE). If a symbol fails on .NS,
we try .BO (BSE) as a fallback so obscure small-caps still resolve.

Caches history and info for the duration of one run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceSnapshot:
    symbol: str          # NSE symbol without suffix
    yf_symbol: str       # Symbol actually resolved (e.g. INFY.NS or INFY.BO)
    price: float         # Latest closing price (₹)
    ath: float | None    # All-time high closing price (₹)
    high_52w: float | None
    ath_pct: float | None   # price / ath  (1.0 = at ATH, 0.85 = 15% below)
    high_52w_pct: float | None
    history_days: int = 0         # Calendar days spanned by the full history (0 = unknown)
    day_change_pct: float | None = None   # (price - prev_close) / prev_close
    day_change_abs: float | None = None   # price - prev_close  (₹ per share)


_SUFFIXES = [".NS", ".BO"]


class IndiaMarketData:
    def __init__(self, ath_period: str = "max") -> None:
        self.ath_period = ath_period
        self._history_cache: dict[str, pd.DataFrame] = {}
        self._resolved: dict[str, str] = {}   # symbol → yf_symbol

    # ------------------------------------------------------------------ public

    def snapshot(self, symbol: str) -> PriceSnapshot | None:
        """Return price + ATH metrics for a single NSE symbol.

        Returns None if yfinance cannot find the symbol on NSE or BSE.
        """
        yf_sym = self._resolve(symbol)
        if yf_sym is None:
            log.warning("No price data found for %s (tried %s)", symbol,
                        ", ".join(symbol + s for s in _SUFFIXES))
            return None

        hist_max = self._history(yf_sym, self.ath_period)
        hist_1y = self._history(yf_sym, "1y")

        if hist_max.empty:
            return None

        price = float(hist_max["Close"].iloc[-1])
        ath = float(hist_max["Close"].max()) if not hist_max.empty else None
        high_52w = float(hist_1y["Close"].max()) if not hist_1y.empty else None
        ath_pct = price / ath if ath else None
        high_52w_pct = price / high_52w if high_52w else None

        # Calendar days spanned by available history (first → last date)
        if len(hist_max) >= 2:
            history_days = (hist_max.index[-1] - hist_max.index[0]).days
        else:
            history_days = 0

        # Day change vs previous close
        if len(hist_max) >= 2:
            prev_close = float(hist_max["Close"].iloc[-2])
            day_change_abs = price - prev_close
            day_change_pct = day_change_abs / prev_close if prev_close else None
        else:
            day_change_abs = None
            day_change_pct = None

        return PriceSnapshot(
            symbol=symbol.upper(),
            yf_symbol=yf_sym,
            price=price,
            ath=ath,
            high_52w=high_52w,
            ath_pct=ath_pct,
            high_52w_pct=high_52w_pct,
            history_days=history_days,
            day_change_pct=day_change_pct,
            day_change_abs=day_change_abs,
        )

    def batch_snapshots(self, symbols: list[str]) -> dict[str, PriceSnapshot | None]:
        """Fetch snapshots for all symbols. Returns {symbol: snapshot}."""
        return {sym: self.snapshot(sym) for sym in symbols}

    # --------------------------------------------------------------- internals

    def _resolve(self, symbol: str) -> str | None:
        """Return the first yfinance suffix that returns data."""
        s = symbol.upper()
        if s in self._resolved:
            return self._resolved[s]

        # Silence yfinance noise during resolution probing
        _yf_log = logging.getLogger("yfinance")
        _saved = _yf_log.level
        _yf_log.setLevel(logging.CRITICAL)
        try:
            for suffix in _SUFFIXES:
                yf_sym = s + suffix
                df = yf.Ticker(yf_sym).history(period="5d")
                if not df.empty:
                    self._resolved[s] = yf_sym
                    return yf_sym
        finally:
            _yf_log.setLevel(_saved)

        self._resolved[s] = None
        return None

    def _history(self, yf_sym: str, period: str) -> pd.DataFrame:
        key = f"{yf_sym}::{period}"
        if key not in self._history_cache:
            _yf_log = logging.getLogger("yfinance")
            _saved = _yf_log.level
            _yf_log.setLevel(logging.CRITICAL)
            try:
                df = yf.Ticker(yf_sym).history(period=period, auto_adjust=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("History fetch failed for %s (%s): %s", yf_sym, period, exc)
                df = pd.DataFrame()
            finally:
                _yf_log.setLevel(_saved)
            self._history_cache[key] = df
        return self._history_cache[key]
