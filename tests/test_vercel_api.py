import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def load_api(monkeypatch, snapshot):
    spec = importlib.util.spec_from_file_location("vercel_index", ROOT / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._cache["base"] = snapshot
    monkeypatch.setattr(mod, "refresh", lambda snap: {**snap, "live_at": "now", "live_quotes": 2})
    return mod


SNAP = {
    "as_of": "t", "data_through": "2026-09-29", "summary": {"total": 2, "active": 1},
    "opportunities": [
        {"id": "a", "strategy": "pairs", "active": True},
        {"id": "b", "strategy": "merger", "active": False},
    ],
}


def test_serverless_endpoints(monkeypatch):
    mod = load_api(monkeypatch, SNAP)
    c = TestClient(mod.app)
    s = c.get("/api/status").json()
    assert s["mode"] == "serverless" and s["data_through"] == "2026-09-29"
    r = c.get("/api/opportunities", params={"active_only": True})
    assert [o["id"] for o in r.json()["opportunities"]] == ["a"]
    assert "s-maxage" in r.headers["cache-control"]
    r = c.post("/api/refresh")
    assert r.json()["live_at"] == "now" and r.headers["cache-control"] == "no-store"
    assert c.post("/api/scan").status_code == 501


def test_falls_back_to_snapshot_when_quotes_fail(monkeypatch):
    mod = load_api(monkeypatch, SNAP)

    def boom(_):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(mod, "refresh", boom)
    c = TestClient(mod.app)
    body = c.post("/api/refresh").json()
    assert body["as_of"] == "t" and "yahoo down" in body["error"]
