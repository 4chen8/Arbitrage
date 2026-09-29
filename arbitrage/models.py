"""Shared opportunity record and signal helpers."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from . import settings


@dataclass
class Leg:
    symbol: str
    side: str          # "BUY" or "SELL" when the spread signal is active
    weight: float      # shares of this leg per 1 share of the first leg (hedge ratio)


@dataclass
class Opportunity:
    id: str
    strategy: str                  # equivalent | adr | pairs | merger
    name: str
    legs: list[Leg]
    signal: str                    # e.g. "LONG a / SHORT b" or "NONE"
    active: bool                   # passes entry threshold and cost filter
    score: float                   # ranking key (|z| or annualized return)
    zscore: float | None
    edge_bps: float                # expected gross capture if the spread reverts to its mean
    net_edge_bps: float            # edge minus estimated round-trip costs
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)   # frozen calibration for live updates
    history: dict[str, list] = field(default_factory=dict)  # {"dates": [...], "values": [...]}
    notes: str = ""
    warnings: list[str] = field(default_factory=list)
    prices: dict[str, float] = field(default_factory=dict)
    updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


def _clean(obj: Any) -> Any:
    """Make a structure JSON-safe (NaN/inf -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def round_trip_cost_bps(n_legs: int = 2) -> float:
    # Enter and exit each leg once.
    return 2 * n_legs * settings.COST_BPS_PER_LEG


def spread_signal(z: float | None, rich_label: str, cheap_label: str) -> tuple[str, bool]:
    """Map a z-score to a trade direction.

    z > +ENTRY: spread is rich -> sell it (``rich_label``).
    z < -ENTRY: spread is cheap -> buy it (``cheap_label``).
    """
    if z is None or math.isnan(z):
        return "NONE", False
    if z >= settings.ENTRY_Z:
        return rich_label, True
    if z <= -settings.ENTRY_Z:
        return cheap_label, True
    if abs(z) <= settings.EXIT_Z:
        return "FLAT (exit zone)", False
    return "WATCH", False


def tail_history(series, n: int | None = None, digits: int = 4) -> dict[str, list]:
    n = n or settings.HISTORY_POINTS
    s = series.dropna().iloc[-n:]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in s.index],
        "values": [round(float(v), digits) for v in s.values],
    }


def from_dict(d: dict[str, Any]) -> Opportunity:
    names = {f.name for f in dataclasses.fields(Opportunity)}
    d = {k: v for k, v in d.items() if k in names}
    d["legs"] = [Leg(**leg) for leg in d.get("legs", [])]
    return Opportunity(**d)


def summarize(opps: list[dict]) -> dict:
    by = {}
    for o in opps:
        s = by.setdefault(o["strategy"], {"candidates": 0, "active": 0})
        s["candidates"] += 1
        s["active"] += int(o["active"])
    return {"total": len(opps), "active": sum(o["active"] for o in opps), "by_strategy": by}
