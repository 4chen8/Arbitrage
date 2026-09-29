"""Dependency-free latest-price fetcher (Yahoo Finance).

Used by the live monitor so it can run where pandas/numpy are unavailable
(e.g. a Vercel serverless function). Quotes are fetched in batches of 20 from
the ``spark`` endpoint; symbols it misses are retried one by one via ``chart``.
Yahoo rate-limits bursts (HTTP 429), so concurrency is kept low.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

log = logging.getLogger(__name__)
SPARK = "https://query1.finance.yahoo.com/v7/finance/spark?"
CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{}?interval=1m&range=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (arbitrage-monitor)"}
BATCH = 20


def _get(url: str, timeout: float) -> dict:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 2:
                raise
            time.sleep(0.5 * 2 ** attempt)
    raise RuntimeError("unreachable")


def _price(meta: dict) -> float | None:
    px = meta.get("regularMarketPrice")
    return float(px) if px else None


def _spark(chunk: list[str], timeout: float) -> dict[str, float]:
    qs = urllib.parse.urlencode({"symbols": ",".join(chunk), "range": "1d", "interval": "5m"})
    try:
        data = _get(SPARK + qs, timeout)
    except Exception as exc:
        log.warning("spark batch failed (%s): %s", ",".join(chunk[:3]), exc)
        return {}
    out = {}
    for res in data.get("spark", {}).get("result") or []:
        try:
            px = _price(res["response"][0]["meta"])
        except (KeyError, IndexError, TypeError):
            continue
        if px:
            out[res["symbol"]] = px
    return out


def _chart(symbol: str, timeout: float) -> tuple[str, float | None]:
    try:
        data = _get(CHART.format(urllib.parse.quote(symbol)), timeout)
        return symbol, _price(data["chart"]["result"][0]["meta"])
    except Exception as exc:  # one bad symbol must not fail the batch
        log.debug("quote %s failed: %s", symbol, exc)
        return symbol, None


def fetch_latest(symbols: Iterable[str], timeout: float = 8.0) -> dict[str, float]:
    syms = sorted({s for s in symbols if s})
    if not syms:
        return {}
    chunks = [syms[i:i + BATCH] for i in range(0, len(syms), BATCH)]
    out: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for part in ex.map(lambda c: _spark(c, timeout), chunks):
            out.update(part)
    missing = [s for s in syms if s not in out]
    if missing:
        with ThreadPoolExecutor(max_workers=4) as ex:
            out.update({s: p for s, p in ex.map(lambda s: _chart(s, timeout), missing) if p})
    return out
