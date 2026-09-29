"""ADR / interlisted arbitrage.

Compares a US listing with its home-market line converted to USD:

    premium = ln( us_price / (ratio * home_price * scale / fx) )

where ``fx`` is home-currency units per USD. Many ADRs carry a structural
premium (capital controls, foreign-ownership caps), so the signal is the
deviation of the premium from its own rolling mean, not the level itself.
Home markets are often closed during US hours; the live monitor then uses
the last home close with a live FX rate, which is flagged as stale.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import settings
from ..models import Leg, Opportunity, round_trip_cost_bps, spread_signal, tail_history

STRATEGY = "adr"


def symbols(cfg: dict) -> list[str]:
    return [s for p in cfg["adr_pairs"]["pairs"] for s in (p["us"], p["home"], p["fx"])]


def scan(closes: pd.DataFrame, cfg: dict, as_of: str) -> list[Opportunity]:
    out = []
    w = settings.ZSCORE_WINDOW
    for p in cfg["adr_pairs"]["pairs"]:
        us, home, fx = p["us"], p["home"], p["fx"]
        if any(s not in closes for s in (us, home, fx)):
            continue
        us_px = closes[us].dropna()
        # Align foreign line and FX onto US trading days; carry at most 3 days.
        other = closes[[home, fx]].ffill(limit=3).reindex(us_px.index)
        df = pd.concat([us_px.rename("us"), other[home].rename("home"), other[fx].rename("fx")], axis=1).dropna()
        if len(df) < w + 5:
            continue
        scale = float(p.get("scale", 1))
        home_usd = df["home"] * scale / df["fx"]
        implied = float((df["us"] / home_usd).iloc[-w:].median())
        cfg_ratio = float(p["ratio"])
        warnings = []
        ratio = cfg_ratio
        if abs(implied / cfg_ratio - 1) > settings.ADR_RATIO_TOLERANCE:
            warnings.append(
                f"Configured ratio {cfg_ratio:g} disagrees with market-implied {implied:.3f}; "
                "using implied. Check the depositary ratio before trading.")
            ratio = implied
        prem = np.log(df["us"] / (ratio * home_usd))
        mean = prem.rolling(w).mean()
        std = prem.rolling(w).std()
        z = (prem - mean) / std
        opp = Opportunity(
            id=f"{STRATEGY}:{us}/{home}", strategy=STRATEGY, name=f"{us} vs {home}",
            legs=[Leg(us, "", 1.0), Leg(home, "", ratio), Leg(fx, "FX", 0.0)],
            signal="NONE", active=False, score=0.0, zscore=None, edge_bps=0.0, net_edge_bps=0.0,
            params={"mean": float(mean.iloc[-1]), "std": float(std.iloc[-1]), "ratio": ratio,
                    "scale": scale, "configured_ratio": cfg_ratio},
            history=tail_history(prem * 100, digits=3),
            notes=p.get("note", ""), warnings=warnings,
            metrics={"implied_ratio": round(implied, 4),
                     "home_last_date": df.index[-1].strftime("%Y-%m-%d")},
            updated=as_of,
        )
        evaluate(opp, {us: float(df["us"].iloc[-1]), home: float(df["home"].iloc[-1]),
                       fx: float(df["fx"].iloc[-1])})
        out.append(opp)
    return out


def evaluate(opp: Opportunity, px: dict[str, float]) -> Opportunity:
    us, home, fx = (leg.symbol for leg in opp.legs)
    pu, ph, pf = px.get(us), px.get(home), px.get(fx)
    if not pu or not ph or not pf:
        return opp
    prm = opp.params
    home_usd = ph * prm["scale"] / pf
    prem = math.log(pu / (prm["ratio"] * home_usd))
    std = prm["std"]
    z = (prem - prm["mean"]) / std if std and std > 0 else float("nan")
    edge = abs(prem - prm["mean"]) * 1e4 / 2
    # Three legs: US, home, and the FX hedge.
    signal, active = spread_signal(z, f"SELL {us} / BUY {home}", f"BUY {us} / SELL {home}")
    opp.zscore = round(z, 3) if not math.isnan(z) else None
    opp.edge_bps = round(edge, 1)
    opp.net_edge_bps = round(edge - round_trip_cost_bps(3), 1)
    opp.active = active and opp.net_edge_bps > 0
    if active and not opp.active:
        signal += " (below cost)"
    opp.signal = signal
    opp.score = abs(z) if not math.isnan(z) else 0.0
    rich = z > 0
    opp.legs[0].side = "SELL" if rich else "BUY"
    opp.legs[1].side = "BUY" if rich else "SELL"
    opp.prices = {us: pu, home: ph, fx: pf}
    opp.metrics["dev_pct"] = round(math.expm1(prem - prm["mean"]) * 100, 3)
    opp.metrics["premium_pct"] = round(prem * 100, 3)
    opp.metrics["mean_premium_pct"] = round(prm["mean"] * 100, 3)
    opp.metrics["home_in_usd"] = round(home_usd * prm["ratio"], 4)
    return opp
