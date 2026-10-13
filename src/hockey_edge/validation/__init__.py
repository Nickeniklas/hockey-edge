"""Walk-forward validation (docs/ELO_HARNESS_PLAN.md).

Output is data/backtest.db: reproducible, rebuildable, and not the
prediction log.
"""

# Recorded in backtest_run. Bump when the harness or a model changes what a
# stored prediction means.
BACKTEST_VERSION = "e2"
