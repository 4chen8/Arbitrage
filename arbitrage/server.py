"""Web app: REST + Server-Sent Events + static dashboard, with built-in schedules.

Run:  uvicorn arbitrage.server:app --host 0.0.0.0 --port 8000

Schedules (America/New_York):
  * full daily scan at 16:35 on trading days (after the close);
  * live re-scoring every ARB_LIVE_INTERVAL seconds while the market is open.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import ROOT, settings
from .data import NY, is_market_open, is_trading_day, now_ny
from .live import refresh
from .scanner import load_snapshot, run_scan

log = logging.getLogger(__name__)
WEB_DIR = ROOT / "web"


class State:
    def __init__(self) -> None:
        self.base: dict | None = load_snapshot()   # calibrated daily snapshot
        self.view: dict | None = self.base          # snapshot re-scored at live prices
        self.scanning = False
        self.last_error: str | None = None
        self.version = 0
        self.changed = asyncio.Event()

    def publish(self, view: dict) -> None:
        self.view = view
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()


state: State


async def do_scan() -> None:
    if state.scanning:
        return
    state.scanning = True
    try:
        snap = await asyncio.to_thread(run_scan)
        state.base = snap
        state.last_error = None
        state.publish(snap)
    except Exception as exc:
        log.exception("Daily scan failed")
        state.last_error = f"scan: {exc}"
    finally:
        state.scanning = False


async def do_live(force: bool = False) -> None:
    if not state.base or state.scanning:
        return
    if not force and not (is_market_open() or settings.LIVE_OUTSIDE_MARKET_HOURS):
        return
    try:
        view = await asyncio.to_thread(refresh, state.base)
        state.last_error = None
        state.publish(view)
    except Exception as exc:
        log.exception("Live refresh failed")
        state.last_error = f"live: {exc}"


def snapshot_is_stale() -> bool:
    """True when the snapshot predates the most recent completed trading session."""
    if not state.base:
        return True
    through = state.base.get("data_through")
    now = now_ny()
    day = now
    if not is_trading_day(now) or now.hour < 16:
        # The latest *completed* session is an earlier day.
        day -= timedelta(days=1)
        while not is_trading_day(day):
            day -= timedelta(days=1)
    last_session = day.date()
    return through is None or through < last_session.isoformat()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global state
    state = State()
    sched = AsyncIOScheduler(timezone=NY)
    sched.add_job(do_scan, CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=NY),
                  id="daily-scan", misfire_grace_time=3600)
    sched.add_job(do_live, "interval", seconds=settings.LIVE_INTERVAL_SECONDS, id="live",
                  max_instances=1, coalesce=True)
    sched.start()
    if snapshot_is_stale():
        asyncio.create_task(do_scan())
    elif is_market_open():
        asyncio.create_task(do_live())
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="Equity Arbitrage Monitor", lifespan=lifespan)


@app.get("/api/opportunities")
async def opportunities(strategy: str | None = None, active_only: bool = False):
    if not state.view:
        raise HTTPException(503, "No snapshot yet - a scan is running" if state.scanning else "No snapshot")
    opps = state.view["opportunities"]
    if strategy:
        opps = [o for o in opps if o["strategy"] == strategy]
    if active_only:
        opps = [o for o in opps if o["active"]]
    return {**state.view, "opportunities": opps}


@app.get("/api/status")
async def status():
    v = state.view or {}
    return {
        "market_open": is_market_open(),
        "scanning": state.scanning,
        "as_of": v.get("as_of"),
        "data_through": v.get("data_through"),
        "live_at": v.get("live_at"),
        "summary": v.get("summary"),
        "last_error": state.last_error,
        "server_time": datetime.now(NY).isoformat(timespec="seconds"),
        "live_interval": settings.LIVE_INTERVAL_SECONDS,
    }


@app.post("/api/scan")
async def trigger_scan():
    if state.scanning:
        return {"started": False, "reason": "scan already running"}
    asyncio.create_task(do_scan())
    return {"started": True}


@app.post("/api/refresh")
async def trigger_refresh():
    await do_live(force=True)
    return {"live_at": (state.view or {}).get("live_at"), "error": state.last_error}


@app.get("/api/stream")
async def stream():
    """Server-Sent Events: pushes the full view whenever it changes."""

    async def gen():
        sent = -1
        while True:
            if state.view and state.version != sent:
                sent = state.version
                yield f"event: snapshot\ndata: {json.dumps(state.view)}\n\n"
            try:
                await asyncio.wait_for(state.changed.wait(), timeout=25)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
