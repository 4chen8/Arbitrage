import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from arbitrage import live, scanner
from arbitrage.models import from_dict
from arbitrage.strategies import adr, equivalent, merger, pairs

IDX = pd.bdate_range("2025-06-01", periods=300)


def walk(seed, start=100.0, vol=0.01):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(0, vol, len(IDX))))


def ou(seed, theta=0.3, sigma=0.01):
    rng = np.random.default_rng(seed)
    x = np.zeros(len(IDX))
    for i in range(1, len(IDX)):
        x[i] = x[i - 1] - theta * x[i - 1] + rng.normal(0, sigma)
    return x


@pytest.fixture
def closes():
    base = walk(1)
    noise = ou(2, sigma=0.002)
    a = base
    b = base * np.exp(noise)
    b[-1] = a[-1] * 0.97          # final day: B cheap by 3% -> A rich
    x = walk(3, 50)
    y = np.exp(0.5 + 1.2 * np.log(x) + ou(4, theta=0.2, sigma=0.01))
    y[-1] = math.exp(0.5 + 1.2 * math.log(x[-1]) + 0.08)   # large positive residual
    fx = 31 + ou(5, sigma=0.05)
    home = walk(6, 1000)
    us = home * 5 / fx * 1.10 * np.exp(ou(7, sigma=0.003))
    tgt = np.full(len(IDX), 9.5)
    acq = walk(8, 40)
    return pd.DataFrame({"AAA": a, "BBB": b, "YYY": y, "XXX": x, "USX": us, "HOME.TW": home,
                         "TWD=X": fx, "TGT": tgt, "ACQ": acq}, index=IDX)


UNIVERSE = {
    "equivalent_pairs": {"pairs": [{"a": "AAA", "b": "BBB", "ratio": 1}]},
    "adr_pairs": {"pairs": [{"us": "USX", "home": "HOME.TW", "ratio": 5, "fx": "TWD=X", "scale": 1}]},
    "pairs_groups": {"groups": {"g": ["YYY", "XXX", "AAA"]}},
}


def test_equivalent_flags_rich_leg(closes):
    [o] = equivalent.scan(closes, UNIVERSE, "t")
    assert o.zscore > 2
    assert o.active and o.signal == "SELL AAA / BUY BBB"
    assert [leg.side for leg in o.legs] == ["SELL", "BUY"]
    assert o.net_edge_bps > 0


def test_equivalent_live_update_reverts(closes):
    [o] = equivalent.scan(closes, UNIVERSE, "t")
    px = {"AAA": 100.0, "BBB": 100.0 / math.exp(o.params["mean"])}
    equivalent.evaluate(o, px)
    assert abs(o.zscore) < 0.01 and not o.active


def test_adr_structural_premium_is_not_a_signal(closes):
    [o] = adr.scan(closes, UNIVERSE, "t")
    assert not o.warnings                     # configured ratio 5 is within tolerance of implied 5.5
    assert 9 < o.metrics["premium_pct"] < 11  # ~10% structural premium
    assert abs(o.zscore) < 3


def test_adr_warns_on_wrong_ratio(closes):
    u = {"adr_pairs": {"pairs": [{"us": "USX", "home": "HOME.TW", "ratio": 1, "fx": "TWD=X"}]}}
    [o] = adr.scan(closes, u, "t")
    assert o.warnings and abs(o.params["ratio"] - 5.5) < 0.2


def test_pairs_finds_cointegrated_pair_only(closes):
    found = pairs.scan(closes, UNIVERSE, "t")
    ids = {o.id for o in found}
    assert any({"YYY", "XXX"} == {leg.symbol for leg in o.legs} for o in found), ids
    o = next(o for o in found if {"YYY", "XXX"} == {leg.symbol for leg in o.legs})
    assert abs(o.zscore) > 2 and o.active
    assert 0.8 < o.params["beta"] < 1.6 or 0.6 < 1 / o.params["beta"] < 1.3


def test_merger_cash_and_stock():
    deal = {"cash": 3.5, "stock_ratio": 0.15, "expected_close": "2027-01-01", "acquirer": "ACQ"}
    closes = pd.DataFrame({"TGT": [9.0] * 5, "ACQ": [40.0] * 5}, index=IDX[:5])
    deals = [{**deal, "target": "TGT"}]
    [o] = merger.scan(closes, deals, "t")
    merger.evaluate(o, {"TGT": 9.0, "ACQ": 40.0}, today=date(2026, 10, 1))
    assert o.metrics["deal_value"] == pytest.approx(9.5)
    assert o.metrics["gross_spread_pct"] == pytest.approx(5.556, abs=1e-3)
    assert o.active and o.signal.startswith("BUY TGT / SELL 0.15x ACQ")
    merger.evaluate(o, {"TGT": 10.0, "ACQ": 40.0}, today=date(2026, 10, 1))
    assert not o.active and "ABOVE TERMS" in o.signal


class FakeProvider:
    def __init__(self, closes):
        self.closes = closes

    def history(self, symbols, days=400):
        return self.closes[[s for s in set(symbols) if s in self.closes]]

    def latest(self, symbols):
        return {s: float(self.closes[s].iloc[-1]) for s in symbols if s in self.closes}


def test_scan_and_live_roundtrip(closes, tmp_path, monkeypatch):
    deals = [{"target": "TGT", "acquirer": "ACQ", "cash": 3.5, "stock_ratio": 0.15,
              "expected_close": "2099-01-01"}]
    snap = scanner.run_scan(FakeProvider(closes), UNIVERSE, deals, write=False)
    assert snap["summary"]["total"] >= 4
    # Snapshot must be JSON-serialisable and round-trip through from_dict.
    import json
    json.dumps(snap)
    for d in snap["opportunities"]:
        from_dict(d)
    view = live.refresh(snap, prices={"AAA": float(closes["AAA"].iloc[-1])})
    assert view["live_quotes"] == 1
    eq = next(o for o in view["opportunities"] if o["strategy"] == "equivalent")
    assert eq["stale_legs"] == ["BBB"]
    # A refreshed view can itself be refreshed (extra keys are ignored).
    live.refresh(view, prices={})
