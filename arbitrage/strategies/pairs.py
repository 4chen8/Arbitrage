"""Statistical arbitrage on cointegrated pairs within an industry group.

For each pair (y, x) we fit ``ln y = alpha + beta ln x + e`` over the formation
window, keep pairs whose residual is cointegrated (Engle-Granger), mean-reverts
fast enough (half-life) and has a stable hedge ratio, then trade the residual's
z-score. Testing hundreds of pairs means some pass by chance; the stability and
half-life filters reduce, but do not remove, that risk.
"""

from __future__ import annotations

import itertools
import math
import warnings


from typing import TYPE_CHECKING

from .. import settings
from ..models import Leg, Opportunity, round_trip_cost_bps, spread_signal, tail_history

if TYPE_CHECKING:
    import pandas as pd

STRATEGY = "pairs"


def symbols(cfg: dict) -> list[str]:
    return [s for g in cfg["pairs_groups"]["groups"].values() for s in g]


def _ols(y, x) -> tuple[float, float]:
    import numpy as np

    beta, alpha = np.polyfit(x, y, 1)
    return float(alpha), float(beta)


def half_life(resid: pd.Series) -> float:
    import numpy as np

    lag = resid.shift(1).dropna()
    delta = resid.diff().dropna()
    lam = np.polyfit(lag.values, delta.values, 1)[0]
    return -math.log(2) / lam if lam < 0 else float("inf")


def test_pair(logp: pd.DataFrame, y: str, x: str) -> dict | None:
    from statsmodels.tsa.stattools import coint

    df = logp[[y, x]].dropna()
    if len(df) < 120:
        return None
    if df[y].diff().corr(df[x].diff()) < 0.3:
        return None
    if df[y].corr(df[x]) < settings.PAIRS_MIN_CORR:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pval = float(coint(df[y], df[x])[1])
    if pval > settings.PAIRS_MAX_PVALUE:
        return None
    alpha, beta = _ols(df[y].values, df[x].values)
    if beta <= 0:
        return None
    half = len(df) // 2
    _, b1 = _ols(df[y].values[:half], df[x].values[:half])
    _, b2 = _ols(df[y].values[half:], df[x].values[half:])
    stability = abs(b1 - b2) / beta
    if stability > 0.5:
        return None
    resid = df[y] - alpha - beta * df[x]
    hl = half_life(resid)
    if not settings.PAIRS_MIN_HALF_LIFE <= hl <= settings.PAIRS_MAX_HALF_LIFE:
        return None
    return {"y": y, "x": x, "alpha": alpha, "beta": beta, "pvalue": pval, "half_life": hl,
            "beta_drift": stability, "resid": resid,
            "corr": float(df[y].diff().corr(df[x].diff()))}


def scan(closes: pd.DataFrame, cfg: dict, as_of: str) -> list[Opportunity]:
    import numpy as np

    logp = np.log(closes.iloc[-settings.FORMATION_DAYS:])
    out: list[Opportunity] = []
    seen: set[frozenset] = set()
    for group, members in cfg["pairs_groups"]["groups"].items():
        members = [m for m in members if m in logp and logp[m].notna().mean() > 0.9]
        for a, b in itertools.combinations(members, 2):
            key = frozenset((a, b))
            if key in seen:
                continue
            seen.add(key)
            # Engle-Granger is asymmetric: keep the direction with the lower p-value.
            fits = [f for f in (test_pair(logp, a, b), test_pair(logp, b, a)) if f]
            if not fits:
                continue
            fit = min(fits, key=lambda f: f["pvalue"])
            out.append(_make(fit, group, closes, as_of))
    return out


def _make(fit: dict, group: str, closes: pd.DataFrame, as_of: str) -> Opportunity:
    y, x, resid = fit["y"], fit["x"], fit["resid"]
    mean, std = float(resid.mean()), float(resid.std())
    opp = Opportunity(
        id=f"{STRATEGY}:{y}/{x}", strategy=STRATEGY, name=f"{y} ~ {x}",
        legs=[Leg(y, "", 1.0), Leg(x, "", 0.0)],
        signal="NONE", active=False, score=0.0, zscore=None, edge_bps=0.0, net_edge_bps=0.0,
        params={"alpha": fit["alpha"], "beta": fit["beta"], "mean": mean, "std": std},
        history=tail_history((resid - mean) / std, digits=3),
        notes=f"{group}: ln {y} = {fit['alpha']:.3f} + {fit['beta']:.3f} ln {x}",
        metrics={"group": group, "coint_pvalue": round(fit["pvalue"], 4),
                 "half_life_days": round(fit["half_life"], 1), "beta": round(fit["beta"], 4),
                 "beta_drift": round(fit["beta_drift"], 3), "return_corr": round(fit["corr"], 3)},
        updated=as_of,
    )
    evaluate(opp, {y: float(closes[y].dropna().iloc[-1]), x: float(closes[x].dropna().iloc[-1])})
    return opp


def evaluate(opp: Opportunity, px: dict[str, float]) -> Opportunity:
    y, x = opp.legs[0].symbol, opp.legs[1].symbol
    py, pxx = px.get(y), px.get(x)
    if not py or not pxx:
        return opp
    p = opp.params
    e = math.log(py) - p["alpha"] - p["beta"] * math.log(pxx)
    z = (e - p["mean"]) / p["std"] if p["std"] > 0 else float("nan")
    # Residual is a return on the y leg; express capture per unit gross notional.
    edge = abs(e - p["mean"]) * 1e4 / (1 + p["beta"])
    signal, active = spread_signal(z, f"SELL {y} / BUY {x}", f"BUY {y} / SELL {x}")
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
    # $beta of x per $1 of y  ->  shares of x per share of y
    opp.legs[1].weight = round(p["beta"] * py / pxx, 4)
    opp.prices = {y: py, x: pxx}
    opp.metrics["dev_pct"] = round(math.expm1(e - p["mean"]) * 100, 3)
    return opp
