"""Market data access.

The default provider is Yahoo Finance via ``yfinance`` (free, no key). US quotes
from Yahoo are near real time for NYSE/Nasdaq; foreign lines and some OTC
symbols can be delayed. Swap ``PriceProvider`` for a broker feed (Alpaca,
Polygon, IBKR) for execution-grade latency.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

# NYSE full-day holidays (observed dates). Extend each year.
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def now_ny() -> datetime:
    return datetime.now(NY)


def is_trading_day(ts: datetime) -> bool:
    return ts.weekday() < 5 and ts.strftime("%Y-%m-%d") not in NYSE_HOLIDAYS


def is_market_open(ts: datetime | None = None) -> bool:
    ts = ts or now_ny()
    return is_trading_day(ts) and time(9, 30) <= ts.time() <= time(16, 0)


def _unique(symbols: Iterable[str]) -> list[str]:
    return sorted({s for s in symbols if s})


class PriceProvider:
    """Thin wrapper around yfinance so tests can inject fake data."""

    def history(self, symbols: Iterable[str], days: int = 400) -> pd.DataFrame:
        """Daily closes (dividend/split adjusted), one column per symbol."""
        import yfinance as yf

        syms = _unique(symbols)
        if not syms:
            return pd.DataFrame()
        period = f"{max(days, 30)}d"
        raw = yf.download(syms, period=period, interval="1d", auto_adjust=True,
                          progress=False, threads=True, group_by="column")
        closes = _extract_close(raw, syms)
        missing = [s for s in syms if s not in closes or closes[s].dropna().empty]
        if missing:
            log.warning("No history for: %s", ", ".join(missing))
        return closes

    def latest(self, symbols: Iterable[str]) -> dict[str, float]:
        """Most recent trade price per symbol (1-minute bars, today)."""
        import yfinance as yf

        syms = _unique(symbols)
        if not syms:
            return {}
        raw = yf.download(syms, period="5d", interval="1m", auto_adjust=False,
                          progress=False, threads=True, group_by="column", prepost=False)
        closes = _extract_close(raw, syms)
        out: dict[str, float] = {}
        for s in syms:
            if s in closes:
                col = closes[s].dropna()
                if not col.empty:
                    out[s] = float(col.iloc[-1])
        return out


def _extract_close(raw: pd.DataFrame, syms: list[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:  # single symbol
        closes = raw[["Close"]].rename(columns={"Close": syms[0]})
    closes = closes.copy()
    closes.index = pd.to_datetime(closes.index)
    if closes.index.tz is not None:
        closes.index = closes.index.tz_localize(None)
    return closes.sort_index()
