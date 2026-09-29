"""Build the self-contained snapshot page published as the claude.ai artifact.

    python tools/build_artifact.py [--out PATH] [--live-url URL]

Embeds data/opportunities.json into tools/artifact_template.html. The page has
no network access, so it shows the latest end-of-day scan and links to the
live app.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_URL = "https://arbitrage-4chen8.vercel.app"


def build(out: Path, live_url: str) -> dict:
    snap = json.loads((ROOT / "data" / "opportunities.json").read_text())
    raw = json.dumps(snap, separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "tools" / "artifact_template.html").read_text()
    html = html.replace("__DATA__", raw).replace("__LIVE_URL__", live_url)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return snap


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "arbitrage-monitor.html")
    ap.add_argument("--live-url", default=LIVE_URL)
    args = ap.parse_args()
    snap = build(args.out, args.live_url)
    print(f"Wrote {args.out} (data through {snap['data_through']}, "
          f"{snap['summary']['active']} active of {snap['summary']['total']})")


if __name__ == "__main__":
    main()
