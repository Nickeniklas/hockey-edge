"""Layer 3 feature store (docs/FEATURE_STORE_PLAN.md).

Derived tables in data/features.db, fully rebuilt from the curated layer by
`python -m hockey_edge.features.build`. Nothing here writes to hockey.db or
snapshots.db.
"""

# Recorded in build_meta. Bump when a table's meaning changes.
FEATURE_VERSION = "f1"
