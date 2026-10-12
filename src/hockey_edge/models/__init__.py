"""Models for the regulation 1X2 target (docs/MODEL.md, docs/ELO_HARNESS_PLAN.md).

Nothing here reads odds or writes to a database.
"""

# Order of the probability columns everywhere: home, draw, away.
CLASSES = ("H", "D", "A")
