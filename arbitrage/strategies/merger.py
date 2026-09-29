"""Merger (risk) arbitrage on announced deals.

Deal value per target share = cash + stock_ratio * acquirer price. The spread
to the target's price is the payoff if the deal closes on its terms; the
annualized figure uses the estimated close date from ``config/merger_deals.json``.
The spread compensates for deal-break risk, which this model does not price.
"""

from __future__ import annotations

import json
from datetime import date


from typing import TYPE_CHECKING

from .. import CONFIG_DIR, settings
from ..models import Leg, Opportunity, round_trip_cost_bps, tail_history

if TYPE_CHECKING:
    import pandas as pd

STRATEGY = "merger"


def load_deals(path=None) -> list[dict]:
    path = path or CONFIG_DIR / "merger_deals.json"
    with open(path) as fh:
        return json.load(fh)["deals"]


def symbols(deals: list[dict]) -> list[str]:
    return [s for d in deals for s in (d["target"], d.get("acquirer")) if s]


def deal_value(deal: dict, acq_px: float | None) -> float | None:
    ratio = float(deal.get("stock_ratio") or 0)
    if ratio and not acq_px:
        return None
    return float(deal.get("cash") or 0) + ratio * (acq_px or 0)


def scan(closes: pd.DataFrame, deals: list[dict], as_of: str) -> list[Opportunity]:
    import pandas as pd

    out = []
    for d in deals:
        t, acq = d["target"], d.get("acquirer")
        if t not in closes or closes[t].dropna().empty:
            continue
        stock = float(d.get("stock_ratio") or 0) > 0
        if stock and (not acq or acq not in closes):
            continue
        df = closes[[t] + ([acq] if stock else [])].dropna()
        if d.get("announced") and len(d["announced"]) == 10:
            df = df[df.index >= pd.Timestamp(d["announced"])]
        if df.empty:
            continue
        dv = d.get("cash", 0) + (d["stock_ratio"] * df[acq] if stock else 0)
        spread_hist = (dv / df[t] - 1) * 100
        legs = [Leg(t, "BUY", 1.0)]
        if stock:
            legs.append(Leg(acq, "SELL", float(d["stock_ratio"])))
        opp = Opportunity(
            id=f"{STRATEGY}:{t}", strategy=STRATEGY,
            name=f"{t} <- {acq or 'private buyer'}",
            legs=legs, signal="NONE", active=False, score=0.0, zscore=None,
            edge_bps=0.0, net_edge_bps=0.0,
            params={k: d.get(k) for k in ("cash", "stock_ratio", "expected_close", "acquirer")},
            history=tail_history(spread_hist, digits=3),
            notes=d.get("note", "") or (f"{d.get('cash', 0)} cash + {d.get('stock_ratio', 0)} {acq}"),
            metrics={"source": d.get("source", ""), "announced": d.get("announced", "")},
            updated=as_of,
        )
        px = {t: float(df[t].iloc[-1])}
        if stock:
            px[acq] = float(df[acq].iloc[-1])
        evaluate(opp, px)
        out.append(opp)
    return out


def evaluate(opp: Opportunity, px: dict[str, float], today: date | None = None) -> Opportunity:
    t = opp.legs[0].symbol
    acq = opp.params.get("acquirer")
    pt = px.get(t)
    dv = deal_value(opp.params, px.get(acq) if acq else None)
    if not pt or dv is None:
        return opp
    today = today or date.today()
    close = date.fromisoformat(opp.params["expected_close"])
    days = max((close - today).days, 7)
    gross = dv / pt - 1
    ann = (1 + gross) ** (365 / days) - 1 if gross > -1 else -1.0
    n_legs = len(opp.legs)
    # Merger arb is held to close: pay entry costs only.
    cost = n_legs * settings.COST_BPS_PER_LEG
    opp.edge_bps = round(gross * 1e4, 1)
    opp.net_edge_bps = round(gross * 1e4 - cost, 1)
    opp.score = round(ann, 4)
    opp.active = ann >= settings.MERGER_MIN_ANNUALIZED and opp.net_edge_bps > 0
    if gross < 0:
        opp.signal = "TRADING ABOVE TERMS (bump expected?)"
    elif opp.active:
        opp.signal = f"BUY {t}" + (f" / SELL {opp.legs[1].weight:g}x {acq}" if n_legs > 1 else "")
    else:
        opp.signal = "WATCH (below hurdle)"
    opp.prices = {t: pt, **({acq: px[acq]} if acq and acq in px else {})}
    opp.metrics.update({"deal_value": round(dv, 4), "gross_spread_pct": round(gross * 100, 3),
                        "annualized_pct": round(ann * 100, 2), "days_to_close": days})
    return opp
