"""Daily end-of-day scan: calibrate every strategy and write a snapshot.

Run:  python -m arbitrage.scanner
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

from . import CONFIG_DIR, DATA_DIR
from .data import PriceProvider, now_ny
from .strategies import adr, equivalent, merger, pairs

log = logging.getLogger(__name__)
SNAPSHOT = DATA_DIR / "opportunities.json"
ARCHIVE_DIR = DATA_DIR / "archive"


def load_universe(path=None) -> dict:
    with open(path or CONFIG_DIR / "universe.json") as fh:
        return json.load(fh)


def run_scan(provider: PriceProvider | None = None, universe: dict | None = None,
             deals: list[dict] | None = None, write: bool = True) -> dict:
    provider = provider or PriceProvider()
    universe = universe or load_universe()
    deals = merger.load_deals() if deals is None else deals

    syms = (equivalent.symbols(universe) + adr.symbols(universe)
            + pairs.symbols(universe) + merger.symbols(deals))
    log.info("Downloading daily history for %d symbols", len(set(syms)))
    closes = provider.history(syms, days=400)
    as_of = now_ny().strftime("%Y-%m-%d %H:%M %Z")

    opps = []
    for name, fn, arg in (("equivalent", equivalent.scan, universe), ("adr", adr.scan, universe),
                          ("pairs", pairs.scan, universe), ("merger", merger.scan, deals)):
        try:
            found = fn(closes, arg, as_of)
            log.info("%s: %d candidates, %d active", name, len(found), sum(o.active for o in found))
            opps.extend(found)
        except Exception:  # one broken strategy must not kill the daily run
            log.exception("Strategy %s failed", name)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "data_through": closes.index.max().strftime("%Y-%m-%d") if not closes.empty else None,
        "opportunities": [o.to_dict() for o in sorted(opps, key=lambda o: (-o.active, -o.score))],
    }
    snapshot["summary"] = summarize(snapshot["opportunities"])
    if write:
        save(snapshot)
    return snapshot


def summarize(opps: list[dict]) -> dict:
    by = {}
    for o in opps:
        s = by.setdefault(o["strategy"], {"candidates": 0, "active": 0})
        s["candidates"] += 1
        s["active"] += int(o["active"])
    return {"total": len(opps), "active": sum(o["active"] for o in opps), "by_strategy": by}


def save(snapshot: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snapshot, indent=1))
    # Compact daily record of the signals, for tracking how they evolve.
    day = (snapshot.get("data_through") or snapshot["generated_at"][:10])
    compact = [{k: o[k] for k in ("id", "signal", "active", "zscore", "edge_bps", "net_edge_bps", "score")}
               for o in snapshot["opportunities"]]
    (ARCHIVE_DIR / f"{day}.json").write_text(json.dumps(
        {"as_of": snapshot["as_of"], "summary": snapshot["summary"], "opportunities": compact}, indent=1))


def load_snapshot() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    return json.loads(SNAPSHOT.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the daily arbitrage scan")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    snap = run_scan()
    print(f"Scan as of {snap['as_of']} (data through {snap['data_through']}): "
          f"{snap['summary']['active']} active of {snap['summary']['total']} candidates")
    for o in snap["opportunities"]:
        if o["active"]:
            z = f"z={o['zscore']:+.2f}" if o["zscore"] is not None else f"ann={o['metrics'].get('annualized_pct')}%"
            print(f"  [{o['strategy']:>10}] {o['name']:<22} {o['signal']:<34} {z:<12} net edge {o['net_edge_bps']} bps")


if __name__ == "__main__":
    main()
