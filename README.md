# Equity Arbitrage Monitor (NYSE / Nasdaq)

Finds relative-value arbitrage opportunities across US-listed equities, recalibrates
them every trading day after the close, and re-prices every signal intraday on a live
web dashboard.

![strategies](https://img.shields.io/badge/strategies-4-blue) ![data](https://img.shields.io/badge/data-Yahoo%20Finance-lightgrey)

## Strategies

| Strategy | What is traded | Signal |
|---|---|---|
| **Share class & ETF twins** (`equivalent`) | Two listings with the same economic claim: GOOGL/GOOG, BRK-A/BRK-B, FOXA/FOX, NWSA/NWS, UAA/UA, LEN/LEN-B, HEI/HEI-A, … and twin ETFs (SPY/IVV, VOO/IVV, QQQ/QQQM, GLD/IAU, IWM/VTWO, …) | z-score of the log price ratio vs its 60-day mean |
| **ADR / interlisted** (`adr`) | US listing vs home line converted at live FX: TSM/2330.TW, BABA/9988.HK, INFY/INFY.NS, SHEL/SHEL.L, ASML/ASML.AS, TD/TD.TO, … | z-score of the ADR premium vs its 60-day mean (structural premia like TSM's ~12% are not signals). Configured ADR ratios are checked against the market-implied ratio. |
| **Statistical pairs** (`pairs`) | Every pair inside 16 industry groups (banks, oil, semis, payments, rails, utilities, REITs, …) | Engle-Granger cointegration (p < 0.05), half-life 1–40 days, stable hedge ratio; trade the residual's z-score |
| **Merger arbitrage** (`merger`) | Announced deals in `config/merger_deals.json` (cash, stock, or mixed) | Gross and annualized spread to deal value; active above an 8% annualized hurdle |

A signal is **active** when |z| ≥ 2 (or the merger hurdle is met) **and** the expected
capture exceeds estimated round-trip costs (5 bps per leg per side by default).
Exit zone is |z| ≤ 0.5.

## Quick start

```bash
pip install -r requirements.txt
python -m arbitrage.scanner                       # daily scan -> data/opportunities.json
uvicorn arbitrage.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

or with Docker:

```bash
docker build -t arb . && docker run -p 8000:8000 arb
```

## How it stays current

* **Intraday (real time):** while NYSE is open the server pulls the latest 1-minute prints
  for every monitored leg every 60 s (`ARB_LIVE_INTERVAL`), re-scores each opportunity from
  its frozen daily calibration, and pushes the result to the browser over Server-Sent
  Events. Legs without a fresh print (e.g. Tokyo or Hong Kong during US hours) keep their
  last close and are flagged with an amber dot.
* **Daily:** the server re-runs the full scan at 16:35 ET on every trading day, and also on
  start-up if the snapshot is older than the last completed session.
  `.github/workflows/daily-scan.yml` does the same on GitHub Actions at 22:05 UTC on weekdays
  and commits the refreshed `data/opportunities.json` plus a compact record in
  `data/archive/YYYY-MM-DD.json`, so you keep a history of every day's signals.
* **Manual:** the *Refresh prices* and *Run daily scan* buttons, or `POST /api/refresh` and
  `POST /api/scan`.

## API

| Endpoint | |
|---|---|
| `GET /api/opportunities?strategy=pairs&active_only=true` | Current view (calibration + live prices) |
| `GET /api/stream` | Server-Sent Events; a `snapshot` event on every update |
| `GET /api/status` | Market state, timestamps, last error |
| `POST /api/refresh` | Re-price now, even outside market hours |
| `POST /api/scan` | Start a full recalibration |

## Configuration

* `config/universe.json`: share-class/ETF pairs, ADR pairs (ratio, FX ticker, pence scale)
  and stat-arb groups. Add tickers freely.
* `config/merger_deals.json`: pending deals. **Keep this current.** Deal terms change
  (bumps, collars, go-shops, breaks), and the file includes a source link for each deal.
* Environment variables (see `arbitrage/settings.py`): `ARB_ENTRY_Z`, `ARB_EXIT_Z`,
  `ARB_COST_BPS_PER_LEG`, `ARB_ZSCORE_WINDOW`, `ARB_FORMATION_DAYS`, `ARB_PAIRS_MAX_PVALUE`,
  `ARB_PAIRS_MAX_HALF_LIFE`, `ARB_MERGER_MIN_ANNUALIZED`, `ARB_LIVE_INTERVAL`,
  `ARB_LIVE_ALWAYS=1` (keep re-pricing outside market hours).

## Layout

```
arbitrage/
  data.py            price provider (yfinance), NYSE calendar
  models.py          Opportunity record, signal + cost helpers
  strategies/        equivalent.py, adr.py, pairs.py, merger.py (scan + live evaluate)
  scanner.py         daily calibration -> data/opportunities.json (+ archive)
  live.py            intraday re-scoring
  server.py          FastAPI app, SSE stream, schedulers
web/                 dashboard (vanilla JS, no build step)
config/              universe + merger deals
tests/               synthetic-data tests (pytest)
```

## Limits (read before trading)

These are **statistical and event-driven** opportunities, not risk-free arbitrage.

* Yahoo Finance data is free but not execution grade. US quotes are near real time, and
  foreign lines can be delayed. For trading, replace `PriceProvider` in `arbitrage/data.py`
  with your broker's feed (Alpaca, Polygon, IBKR). The interface is two methods.
* Share-class spreads can persist (voting rights, index inclusion, liquidity). Pairs can
  stop cointegrating, and testing hundreds of pairs produces some false positives.
* ADR arbitrage needs conversion access and a foreign account. Some markets (Taiwan,
  India) restrict conversion, which is why their premia persist.
* Merger spreads price the risk that the deal breaks, and this model does not.
* Costs are a flat estimate and do not include borrow fees or hard-to-borrow constraints.
