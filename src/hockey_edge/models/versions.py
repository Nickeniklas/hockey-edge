"""Tagged model versions. A prediction records the version it was made under.

elo-1 is the last fold of the walk-forward backtest (docs/ELO_RESULTS.md):
the config chosen and the link fitted on the 2016-2026 regular seasons,
4,975 games, with the ratings run over all history from 2015. The home edge
is fixed at 0, not tuned: the link's cutpoints carry it.

Frozen. A retune is a new version, not an edit here.
"""

from hockey_edge.models.elo import EloParams
from hockey_edge.models.link import LinkParams

ELO_1_VERSION = "elo-1"
ELO_1_PARAMS = EloParams(k=10, hfa=0.0, r=0.5, margin=True, s_ot=0.15, cold_m=2.0, cold_n=15)
ELO_1_LINK = LinkParams(c1=-0.7341928726427005, c2=0.27399476649705745, beta=3.0804204715883925)
