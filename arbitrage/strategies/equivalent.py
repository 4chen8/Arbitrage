"""Equivalent-claim arbitrage: share classes of one company and twin ETFs.

Two listings with the same (or nearly the same) economic claim should trade
at a stable price ratio. We model the log ratio ``ln(a) - ln(b)`` and trade
its deviation from a rolling mean, measured in z-scores.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import settings
from ..models import Leg, Opportunity, round_trip_cost_bps, spread_signal, tail_history

STRATEGY = "equivalent"


def symbols(cfg: dict) -> list[str]:
    return [s for p in cfg["equivalent_pairs"]["pairs"] for s in (p["a"], p["b"])]


def _pair_id(a: str, b: str) -> str:
    return f"{STRATEGY}:{a}/{b}"


def scan(closes: pd.DataFrame, cfg: dict, as_of: str) -> list[Opportunity]:
    out = []
    w = settings.ZSCORE_WINDOW
    for p in cfg["equivalent_pairs"]["pairs"]:
        a, b, k = p["a"], p["b"], float(p.get("ratio", 1))
        if a not in closes or b not in closes:
            continue
        df = closes[[a, b]].dropna()
        if len(df) < w + 5:
            continue
        r = np.log(df[a]) - np.log(df[b])
        mean = r.rolling(w).mean()
        std = r.rolling(w).std()
        z = (r - mean) / std
        params = {"mean": float(mean.iloc[-1]), "std": float(std.iloc[-1]), "ratio": k}
        opp = Opportunity(
            id=_pair_id(a, b), strategy=STRATEGY, name=f"{a} vs {b}",
            legs=[Leg(a, "", 1.0), Leg(b, "", 0.0)],
            signal="NONE", active=False, score=0.0, zscore=None, edge_bps=0.0, net_edge_bps=0.0,
            params=params, history=tail_history(z, digits=3),
            notes=p.get("note", ""),
            metrics={"kind": p.get("kind", "share_class"),
                     "corr_1y": round(float(np.log(df).diff().corr().iloc[0, 1]), 4),
                     "ratio_std_bps": round(params["std"] * 1e4, 1)},
            updated=as_of,
        )
        evaluate(opp, {a: float(df[a].iloc[-1]), b: float(df[b].iloc[-1])})
        out.append(opp)
    return out


def evaluate(opp: Opportunity, px: dict[str, float]) -> Opportunity:
    """Recompute z-score / signal from frozen calibration and current prices."""
    a, b = opp.legs[0].symbol, opp.legs[1].symbol
    pa, pb = px.get(a), px.get(b)
    if not pa or not pb:
        return opp
    mean, std, k = opp.params["mean"], opp.params["std"], opp.params["ratio"]
    r = math.log(pa) - math.log(pb)
    z = (r - mean) / std if std and std > 0 else float("nan")
    dev = r - mean
    edge = abs(dev) * 1e4 / 2  # capture on gross notional of a dollar-neutral pair
    signal, active = spread_signal(z, f"SELL {a} / BUY {b}", f"BUY {a} / SELL {b}")
    opp.zscore = round(z, 3) if not math.isnan(z) else None
    opp.edge_bps = round(edge, 1)
    opp.net_edge_bps = round(edge - round_trip_cost_bps(2), 1)
    opp.active = active and opp.net_edge_bps > 0
    if active and not opp.active:
        signal += " (below cost)"
    opp.signal = signal
    opp.score = abs(z) if not math.isnan(z) else 0.0
    rich = z > 0
    opp.legs[0].side = "SELL" if rich else "BUY"
    opp.legs[1].side = "BUY" if rich else "SELL"
    opp.legs[1].weight = round(pa / pb, 4)  # shares of b per share of a (dollar neutral)
    opp.prices = {a: pa, b: pb}
    opp.metrics["dev_pct"] = round(math.expm1(dev) * 100, 3)
    opp.metrics["premium_pct"] = round((pa / (k * pb) - 1) * 100, 3)
    opp.metrics["mean_premium_pct"] = round((math.exp(mean) / k - 1) * 100, 3)
    return opp
