"""Tunable parameters. Every value can be overridden with an environment variable."""

import os


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# Signal thresholds (z-score units)
ENTRY_Z = _f("ARB_ENTRY_Z", 2.0)
EXIT_Z = _f("ARB_EXIT_Z", 0.5)

# Estimated round-trip cost per leg in basis points (commission + half-spread + borrow)
COST_BPS_PER_LEG = _f("ARB_COST_BPS_PER_LEG", 5.0)

# History windows (trading days)
FORMATION_DAYS = int(_f("ARB_FORMATION_DAYS", 252))
ZSCORE_WINDOW = int(_f("ARB_ZSCORE_WINDOW", 60))
HISTORY_POINTS = int(_f("ARB_HISTORY_POINTS", 120))

# Statistical pairs filters
PAIRS_MAX_PVALUE = _f("ARB_PAIRS_MAX_PVALUE", 0.05)
PAIRS_MIN_HALF_LIFE = _f("ARB_PAIRS_MIN_HALF_LIFE", 1.0)
PAIRS_MAX_HALF_LIFE = _f("ARB_PAIRS_MAX_HALF_LIFE", 40.0)
PAIRS_MIN_CORR = _f("ARB_PAIRS_MIN_CORR", 0.7)

# Merger arb: flag deals whose annualized spread beats this hurdle
MERGER_MIN_ANNUALIZED = _f("ARB_MERGER_MIN_ANNUALIZED", 0.08)

# ADR: flag when the configured ratio differs from the market-implied one by more than this
ADR_RATIO_TOLERANCE = _f("ARB_ADR_RATIO_TOLERANCE", 0.15)

# Live refresh
LIVE_INTERVAL_SECONDS = int(_f("ARB_LIVE_INTERVAL", 60))
LIVE_OUTSIDE_MARKET_HOURS = os.environ.get("ARB_LIVE_ALWAYS", "0") == "1"
