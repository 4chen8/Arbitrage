"""Vercel serverless entry point.

Vercel cannot run the long-lived server in ``arbitrage/server.py`` (no background
scheduler, no persistent SSE). Instead:

* the daily calibration runs in GitHub Actions and commits ``data/opportunities.json``,
  which triggers a Vercel redeploy;
* each request here re-prices that snapshot with live quotes. Responses are cached
  at Vercel's edge for ~1 minute, so many viewers share one quote fetch.

Only the standard library and FastAPI are needed at runtime.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arbitrage import settings  # noqa: E402
from arbitrage.data import is_market_open, now_ny  # noqa: E402
from arbitrage.live import refresh  # noqa: E402

SNAPSHOT = ROOT / "data" / "opportunities.json"
LIVE_TTL = 30  # seconds a warm instance reuses its last live view

app = FastAPI(title="Equity Arbitrage Monitor (serverless)")
_cache: dict = {"base": None, "view": None, "at": 0.0, "error": None}


def base() -> dict:
    if _cache["base"] is None:
        if not SNAPSHOT.exists():
            raise HTTPException(503, "No snapshot yet - run the daily scan workflow")
        _cache["base"] = json.loads(SNAPSHOT.read_text())
    return _cache["base"]


def current(force: bool = False) -> dict:
    snap = base()
    if not (force or is_market_open() or settings.LIVE_OUTSIDE_MARKET_HOURS):
        # Market closed: re-price once per instance so the page shows last prints.
        if _cache["view"] is not None:
            return _cache["view"]
    elif not force and _cache["view"] is not None and time.time() - _cache["at"] < LIVE_TTL:
        return _cache["view"]
    try:
        _cache["view"] = refresh(snap)
        _cache["at"] = time.time()
        _cache["error"] = None
    except Exception as exc:  # fall back to the end-of-day snapshot
        _cache["error"] = f"live: {exc}"
        if _cache["view"] is None:
            return snap
    return _cache["view"]


def cached(body: dict, fresh: bool = False) -> JSONResponse:
    ttl = 55 if is_market_open() else 600
    header = "no-store" if fresh else f"public, s-maxage={ttl}, stale-while-revalidate={ttl * 2}"
    return JSONResponse(body, headers={"Cache-Control": header})


@app.get("/api/opportunities")
def opportunities(strategy: str | None = None, active_only: bool = False):
    view = current()
    opps = view["opportunities"]
    if strategy:
        opps = [o for o in opps if o["strategy"] == strategy]
    if active_only:
        opps = [o for o in opps if o["active"]]
    return cached({**view, "opportunities": opps})


@app.get("/api/status")
def status():
    snap = base()
    return cached({
        "mode": "serverless",
        "market_open": is_market_open(),
        "scanning": False,
        "as_of": snap.get("as_of"),
        "data_through": snap.get("data_through"),
        "summary": snap.get("summary"),
        "last_error": _cache["error"],
        "server_time": now_ny().isoformat(timespec="seconds"),
        "live_interval": 60,
    })


@app.post("/api/refresh")
def force_refresh():
    view = current(force=True)
    return cached({**view, "error": _cache["error"]}, fresh=True)


@app.post("/api/scan")
def scan():
    return JSONResponse({"started": False, "reason": "On Vercel the daily scan runs in GitHub Actions "
                         "(.github/workflows/daily-scan.yml); trigger it from the Actions tab."}, status_code=501)
