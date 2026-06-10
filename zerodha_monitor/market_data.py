"""yfinance wrapper for Indian NSE stocks.

All symbols are auto-suffixed with .NS (NSE). If a symbol fails on .NS,
we try .BO (BSE) as a fallback so obscure small-caps still resolve.

Caches history and info for the duration of one run.

Technical indicators in PriceSnapshot (Phase 1):
  ma50, ma200      — computed from price history (tail-50 / tail-200 of valid closes)
  bb_upper/lower   — 20-day Bollinger Bands (2σ) from price history
  bb_pct_b         — %B: 0.0=at lower band, 0.5=mid, 1.0=at upper band
  above_ma50/200   — boolean convenience flags
  All are computed via _compute_technicals() — no extra API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
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
    history_days: int = 0                      # Calendar days spanned by the full history
    data_date: date | None = None              # Date of the latest close (IST)
    day_change_pct: float | None = None        # (price - prev_close) / prev_close
    day_change_abs: float | None = None        # price - prev_close  (₹ per share)
    five_day_return_pct: float | None = None   # 5-trading-day cumulative return
    consecutive_up_days: int | None = None     # # of consecutive positive closes (most recent)

    # ── Phase 1: Technical indicators ─────────────────────────────────────────
    # Computed from price history via _compute_technicals() — no extra API calls.
    ma50: float | None = None          # 50-day simple moving average (₹)
    ma200: float | None = None         # 200-day simple moving average (₹)
    bb_upper: float | None = None      # Bollinger upper band  (20d MA + 2σ)
    bb_lower: float | None = None      # Bollinger lower band  (20d MA − 2σ)
    bb_pct_b: float | None = None      # %B: 0=lower band, 0.5=mid, 1=upper band
    above_ma50: bool | None = None     # price > ma50
    above_ma200: bool | None = None    # price > ma200


@dataclass(frozen=True)
class FundamentalsSnapshot:
    """Fundamentals + analyst data for an NSE-listed stock."""

    symbol: str
    trailing_pe: float | None
    forward_pe: float | None
    revenue_yoy: float | None
    op_margin: float | None
    analyst_target_mean: float | None
    analyst_target_high: float | None
    analyst_target_low: float | None
    analyst_recommendation: str | None   # e.g. "buy", "hold", "sell"
    analyst_count: int | None
    rsi_14: float | None
    eps_history: list[dict]              # last 4Q: [{period, estimate, actual, surprise_pct}]
    raw: dict = field(default_factory=dict, repr=False)


def _safe_float(x: Any) -> float | None:
    """Coerce to float, returning None for NaN/Inf/None."""
    try:
        if x is None:
            return None
        f = float(x)
        return f if not (np.isnan(f) or np.isinf(f)) else None
    except (TypeError, ValueError):
        return None


_SUFFIXES = [".NS", ".BO"]


class IndiaMarketData:
    def __init__(self, ath_period: str = "max") -> None:
        self.ath_period = ath_period
        self._history_cache: dict[str, pd.DataFrame] = {}
        self._resolved: dict[str, str] = {}   # symbol → yf_symbol
        self._info_cache: dict[str, dict[str, Any]] = {}  # yf_symbol → info dict

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

        # Drop any trailing NaN rows (yfinance emits a partial/empty row for
        # the current trading day before the session fully settles in their data)
        valid_closes = hist_max["Close"].dropna()
        if valid_closes.empty:
            return None

        price = float(valid_closes.iloc[-1])
        ath = float(valid_closes.max())
        high_52w = float(hist_1y["Close"].dropna().max()) if not hist_1y.empty else None
        ath_pct = price / ath if ath else None
        high_52w_pct = price / high_52w if high_52w else None

        # Date of the latest valid close (strip tz-aware index to plain date)
        latest_idx = valid_closes.index[-1]
        try:
            data_date = latest_idx.date()
        except AttributeError:
            data_date = None

        # Calendar days spanned by available history (first → last valid date)
        valid_index = valid_closes.index
        if len(valid_index) >= 2:
            history_days = (valid_index[-1] - valid_index[0]).days
        else:
            history_days = 0

        # Day change vs previous close
        if len(valid_closes) >= 2:
            prev_close = float(valid_closes.iloc[-2])
            day_change_abs = price - prev_close
            day_change_pct = day_change_abs / prev_close if prev_close else None
        else:
            day_change_abs = None
            day_change_pct = None

        # 5-trading-day return (close[−1] vs close[−6])
        if len(valid_closes) >= 6:
            base = float(valid_closes.iloc[-6])
            five_day_return_pct = (price - base) / base if base else None
        else:
            five_day_return_pct = None

        # Consecutive up days — count back from the most recent valid session
        closes = valid_closes.tolist()
        consecutive_up_days = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                consecutive_up_days += 1
            else:
                break

        tech = self._compute_technicals(valid_closes, price)

        return PriceSnapshot(
            symbol=symbol.upper(),
            yf_symbol=yf_sym,
            price=price,
            ath=ath,
            high_52w=high_52w,
            ath_pct=ath_pct,
            high_52w_pct=high_52w_pct,
            history_days=history_days,
            data_date=data_date,
            day_change_pct=day_change_pct,
            day_change_abs=day_change_abs,
            five_day_return_pct=five_day_return_pct,
            consecutive_up_days=consecutive_up_days,
            **tech,
        )

    def batch_snapshots(self, symbols: list[str]) -> dict[str, PriceSnapshot | None]:
        """Fetch snapshots for all symbols. Returns {symbol: snapshot}."""
        return {sym: self.snapshot(sym) for sym in symbols}

    def available_dates(self, symbol: str, n: int = 7) -> list[date]:
        """Return the last n trading dates with valid close data (oldest first)."""
        from datetime import date as _date
        yf_sym = self._resolve(symbol)
        if yf_sym is None:
            return []
        hist = self._history(yf_sym, "max")
        valid = hist["Close"].dropna()
        if valid.empty:
            return []
        return [idx.date() for idx in valid.index[-n:]]

    def snapshot_for_date(self, symbol: str, target_date: date) -> PriceSnapshot | None:
        """Return PriceSnapshot computed as of close on target_date.
        Returns None if target_date was not a trading day."""
        yf_sym = self._resolve(symbol)
        if yf_sym is None:
            return None

        hist_max = self._history(yf_sym, self.ath_period)
        hist_1y  = self._history(yf_sym, "1y")
        if hist_max.empty:
            return None

        valid_closes = hist_max["Close"].dropna()
        if valid_closes.empty:
            return None

        # Slice up through target_date
        idx_dates = pd.Series(
            [ts.date() for ts in valid_closes.index],
            index=valid_closes.index,
        )
        hist_through = valid_closes[idx_dates <= target_date]
        if hist_through.empty:
            return None

        # Require the last row to actually be target_date (not a gap day)
        actual_date = hist_through.index[-1].date()
        if actual_date != target_date:
            # Distinguish: does yfinance have a row for target_date (NaN not yet
            # published) vs. target_date simply not being a trading day?
            raw_dates = {ts.date() for ts in hist_max.index}
            if target_date in raw_dates:
                log.debug(
                    "%s: close data not yet published by yfinance for %s "
                    "(row present, all NaN) — will retry in tomorrow's backfill",
                    symbol, target_date,
                )
            return None

        price     = float(hist_through.iloc[-1])
        ath       = float(hist_through.max())

        if not hist_1y.empty:
            v1y = hist_1y["Close"].dropna()
            d1y = pd.Series([ts.date() for ts in v1y.index], index=v1y.index)
            high_52w_ser = v1y[d1y <= target_date]
            high_52w = float(high_52w_ser.max()) if not high_52w_ser.empty else None
        else:
            high_52w = None

        ath_pct      = price / ath if ath else None
        high_52w_pct = price / high_52w if high_52w else None

        valid_index  = hist_through.index
        history_days = (valid_index[-1] - valid_index[0]).days if len(valid_index) >= 2 else 0

        if len(hist_through) >= 2:
            prev_close     = float(hist_through.iloc[-2])
            day_change_abs = price - prev_close
            day_change_pct = day_change_abs / prev_close if prev_close else None
        else:
            day_change_abs = None
            day_change_pct = None

        if len(hist_through) >= 6:
            base               = float(hist_through.iloc[-6])
            five_day_return_pct = (price - base) / base if base else None
        else:
            five_day_return_pct = None

        closes_list = hist_through.tolist()
        consecutive_up_days = 0
        for i in range(len(closes_list) - 1, 0, -1):
            if closes_list[i] > closes_list[i - 1]:
                consecutive_up_days += 1
            else:
                break

        # Technical indicators computed from the history slice up through target_date,
        # so they correctly reflect the state on that date (not today's values).
        tech = self._compute_technicals(hist_through, price)

        return PriceSnapshot(
            symbol=symbol.upper(),
            yf_symbol=yf_sym,
            price=price,
            ath=ath,
            high_52w=high_52w,
            ath_pct=ath_pct,
            high_52w_pct=high_52w_pct,
            history_days=history_days,
            data_date=actual_date,
            day_change_pct=day_change_pct,
            day_change_abs=day_change_abs,
            five_day_return_pct=five_day_return_pct,
            consecutive_up_days=consecutive_up_days,
            **tech,
        )

    def rsi_14(self, symbol: str) -> float | None:
        """Compute 14-period RSI from 6-month daily history. Returns None if insufficient data."""
        yf_sym = self._resolve(symbol)
        if yf_sym is None:
            return None
        df = self._history(yf_sym, "6mo")
        if len(df) < 20:
            return None
        return self._compute_rsi(df["Close"].dropna())

    def ma_crossover(self, symbol: str) -> dict:
        """Detect a golden or death MA cross firing today for an NSE symbol.

        Compares yesterday's MA50/MA200 with today's to find the crossing moment.
        Requires at least 201 days of history; returns all-False otherwise.

        Returns dict keys:
          golden (bool), death (bool),
          ma50 (float | None), ma200 (float | None),
          gap_pct (float | None)  — positive = MA50 above MA200
        """
        yf_sym = self._resolve(symbol)
        if yf_sym is None:
            return {"golden": False, "death": False, "ma50": None, "ma200": None, "gap_pct": None}
        hist = self._history(yf_sym, "max")
        closes = hist["Close"].dropna()
        if len(closes) < 201:
            return {"golden": False, "death": False, "ma50": None, "ma200": None, "gap_pct": None}

        ma50_today  = float(closes.tail(50).mean())
        ma50_yest   = float(closes.iloc[:-1].tail(50).mean())
        ma200_today = float(closes.tail(200).mean())
        ma200_yest  = float(closes.iloc[:-1].tail(200).mean())

        golden  = ma50_yest < ma200_yest and ma50_today >= ma200_today
        death   = ma50_yest > ma200_yest and ma50_today <= ma200_today
        gap_pct = (ma50_today - ma200_today) / ma200_today if ma200_today else None

        return {
            "golden":  golden,
            "death":   death,
            "ma50":    ma50_today,
            "ma200":   ma200_today,
            "gap_pct": gap_pct,
        }

    def rsi_14_for_date(self, symbol: str, target_date: date) -> float | None:
        """Compute RSI-14 using only data up through target_date."""
        yf_sym = self._resolve(symbol)
        if yf_sym is None:
            return None
        df = self._history(yf_sym, "6mo")
        if df.empty:
            return None
        valid = df["Close"].dropna()
        idx_dates = pd.Series([ts.date() for ts in valid.index], index=valid.index)
        hist_through = valid[idx_dates <= target_date]
        if len(hist_through) < 20:
            return None
        return self._compute_rsi(hist_through)

    @staticmethod
    def _compute_technicals(closes: pd.Series, price: float) -> dict:
        """Compute MA50, MA200, and 20-day Bollinger Bands from a closes series.

        Works for both current snapshots and historical (snapshot_for_date) slices —
        just pass the appropriately-sliced Series. No API calls; pure pandas math.

        Returns a dict with keys: ma50, ma200, bb_upper, bb_lower, bb_pct_b,
        above_ma50, above_ma200.  Values are None when there is insufficient history.
        """
        ma50  = float(closes.tail(50).mean())  if len(closes) >= 50  else None
        ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None

        bb_mid_s = closes.rolling(20).mean()
        bb_std_s = closes.rolling(20).std(ddof=1)
        bb_u = _safe_float((bb_mid_s + 2 * bb_std_s).iloc[-1])
        bb_l = _safe_float((bb_mid_s - 2 * bb_std_s).iloc[-1])
        bb_pct_b: float | None = None
        if bb_u is not None and bb_l is not None and (bb_u - bb_l) > 0:
            bb_pct_b = (price - bb_l) / (bb_u - bb_l)

        return {
            "ma50":        ma50,
            "ma200":       ma200,
            "bb_upper":    bb_u,
            "bb_lower":    bb_l,
            "bb_pct_b":    bb_pct_b,
            "above_ma50":  (price > ma50)  if ma50  is not None else None,
            "above_ma200": (price > ma200) if ma200 is not None else None,
        }

    @staticmethod
    def _compute_rsi(closes: pd.Series) -> float | None:
        delta  = closes.diff()
        gains  = delta.clip(lower=0).rolling(14).mean()
        losses = (-delta.clip(upper=0)).rolling(14).mean()
        rs     = gains / losses.replace(0, np.nan)
        rsi    = 100 - (100 / (1 + rs))
        latest = rsi.iloc[-1]
        return float(latest) if not pd.isna(latest) else None

    def fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        """Fetch fundamentals + analyst data via yfinance. Silences yfinance ERROR logs."""
        yf_sym = self._resolve(symbol) or f"{symbol.upper()}.NS"
        info = self._info(yf_sym)
        ticker = yf.Ticker(yf_sym)

        _yf_log = logging.getLogger("yfinance")
        _saved = _yf_log.level
        _yf_log.setLevel(logging.CRITICAL)

        revenue_yoy: float | None = None
        op_margin: float | None = None
        try:
            qf = ticker.quarterly_financials
            if qf is not None and not qf.empty:
                if "Total Revenue" in qf.index:
                    rev = qf.loc["Total Revenue"].dropna()
                    if len(rev) >= 5:
                        revenue_yoy = float(rev.iloc[0] - rev.iloc[4]) / abs(float(rev.iloc[4]))
                if "Operating Income" in qf.index and "Total Revenue" in qf.index:
                    op_inc = qf.loc["Operating Income"].dropna()
                    rev_q = qf.loc["Total Revenue"].dropna()
                    margins = (op_inc / rev_q).dropna()
                    if len(margins) >= 1:
                        op_margin = float(margins.iloc[0])
        except Exception as exc:  # noqa: BLE001
            log.debug("Quarterly financials partial failure for %s: %s", symbol, exc)
        finally:
            _yf_log.setLevel(_saved)

        # EPS history — last 4 quarters, newest first
        eps_history: list[dict] = []
        _yf_log.setLevel(logging.CRITICAL)
        try:
            eh = ticker.earnings_history
            if eh is not None and not eh.empty:
                for idx, row in eh.tail(4).iloc[::-1].iterrows():
                    est = _safe_float(row.get("epsEstimate"))
                    actual = _safe_float(row.get("epsActual"))
                    surprise: float | None = None
                    if est is not None and actual is not None and est != 0:
                        surprise = (actual - est) / abs(est) * 100
                    eps_history.append({
                        "period": str(idx)[:10],
                        "estimate": est,
                        "actual": actual,
                        "surprise_pct": surprise,
                    })
        except Exception:  # noqa: BLE001
            pass
        finally:
            _yf_log.setLevel(_saved)

        rsi = self.rsi_14(symbol)

        return FundamentalsSnapshot(
            symbol=symbol.upper(),
            trailing_pe=_safe_float(info.get("trailingPE")),
            forward_pe=_safe_float(info.get("forwardPE")),
            revenue_yoy=revenue_yoy,
            op_margin=op_margin,
            analyst_target_mean=_safe_float(info.get("targetMeanPrice")),
            analyst_target_high=_safe_float(info.get("targetHighPrice")),
            analyst_target_low=_safe_float(info.get("targetLowPrice")),
            analyst_recommendation=info.get("recommendationKey"),
            analyst_count=info.get("numberOfAnalystOpinions"),
            rsi_14=rsi,
            eps_history=eps_history,
            raw=info,
        )

    # --------------------------------------------------------------- internals

    def _info(self, yf_sym: str) -> dict[str, Any]:
        """Fetch yfinance info dict for a resolved symbol, with caching."""
        if yf_sym not in self._info_cache:
            _yf_log = logging.getLogger("yfinance")
            _saved = _yf_log.level
            _yf_log.setLevel(logging.CRITICAL)
            try:
                data = yf.Ticker(yf_sym).info or {}
            except Exception as exc:  # noqa: BLE001
                log.warning("Info fetch failed for %s: %s", yf_sym, exc)
                data = {}
            finally:
                _yf_log.setLevel(_saved)
            self._info_cache[yf_sym] = data
        return self._info_cache[yf_sym]

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


class MarketDataForDate:
    """Thin wrapper that routes snapshot() and rsi_14() to date-specific methods.

    Pass an instance of this to rule.evaluate() to evaluate rules as-of a
    specific historical close date. Fundamentals are always current (no
    meaningful historical fundamentals in yfinance for daily cadence).
    """

    def __init__(self, market: IndiaMarketData, data_date: date) -> None:
        self._market = market
        self._date   = data_date

    def snapshot(self, symbol: str) -> PriceSnapshot | None:
        return self._market.snapshot_for_date(symbol, self._date)

    def rsi_14(self, symbol: str) -> float | None:
        return self._market.rsi_14_for_date(symbol, self._date)

    def fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        return self._market.fundamentals(symbol)

    def batch_snapshots(self, symbols: list[str]) -> dict[str, PriceSnapshot | None]:
        return {sym: self.snapshot(sym) for sym in symbols}
