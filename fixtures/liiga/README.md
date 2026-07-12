# liiga.fi fixtures

One real sample response per verified endpoint, checked into git (unlike
`data/raw/`, which is the full gitignored ingest cache). Used to write the
SQLite schema and parsers against actual payloads instead of guesses.

Path convention: `fixtures/liiga/<endpoint_name>/<season>.json` — one file per
season an endpoint has been verified against, since historical seasons may use
different endpoints or response shapes. `<endpoint_name>` matches the `name`
field of the corresponding `Endpoint` in
`src/hockey_edge/ingest/liiga/endpoints.py`.
