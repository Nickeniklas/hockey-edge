# liiga.fi fixtures

One real sample response per verified endpoint, checked into git (unlike
`data/raw/`, which is the full gitignored ingest cache). Used to write the
SQLite schema and parsers against actual payloads instead of guesses.

Path convention: `fixtures/liiga/<endpoint_name>/<season>.json` — one file per
season an endpoint has been verified against, since historical seasons may use
different endpoints or response shapes. `<endpoint_name>` matches the `name`
field of the corresponding `Endpoint` in
`src/hockey_edge/ingest/liiga/endpoints.py`.

Regression pairs are the exception: two real responses for the same game,
named `<season>_game<id>_<what>.json`, kept because the second fetch
differs in a way a test must pin down.

- `game_detail/2024_game1_original.json` / `…_refetch_goalkeeper_regression.json`:
  the refetch lost goalkeeper events (2026-08-24).
- `game_stats/2024_game2_original.json` / `…_refetch_stripped.json`: the
  refetch (2026-09-25) has the same period-row counts but stripped values
  (time on ice 0, faceoffs null, power-play lists empty) and two more
  puck-control periods. Used by `tests/test_repair_game_stats.py`.
