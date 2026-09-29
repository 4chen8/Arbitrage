"""Intraday re-evaluation of the daily snapshot against live prices."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .data import PriceProvider, is_market_open
from .models import from_dict
from .scanner import summarize
from .strategies import EVALUATORS

log = logging.getLogger(__name__)


def live_symbols(snapshot: dict) -> list[str]:
    return sorted({leg["symbol"] for o in snapshot["opportunities"] for leg in o["legs"]})


def refresh(snapshot: dict, provider: PriceProvider | None = None,
            prices: dict[str, float] | None = None) -> dict:
    """Return a copy of ``snapshot`` with every opportunity re-scored at current prices."""
    provider = provider or PriceProvider()
    if prices is None:
        prices = provider.latest(live_symbols(snapshot))
    updated = []
    for d in snapshot["opportunities"]:
        opp = from_dict(d)
        # Keep the calibrated close for any leg without a fresh print (e.g. closed foreign market).
        px = {**opp.prices, **{leg.symbol: prices[leg.symbol] for leg in opp.legs if leg.symbol in prices}}
        try:
            EVALUATORS[opp.strategy](opp, px)
        except Exception:
            log.exception("Live evaluation failed for %s", opp.id)
        stale = [leg.symbol for leg in opp.legs if leg.symbol not in prices]
        out = opp.to_dict()
        out["stale_legs"] = stale
        updated.append(out)
    updated.sort(key=lambda o: (-o["active"], -o["score"]))
    return {**snapshot, "opportunities": updated, "summary": summarize(updated),
            "live_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "market_open": is_market_open(), "live_quotes": len(prices)}
