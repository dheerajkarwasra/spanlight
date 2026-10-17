"""The enterprise edition: code under the Spanlight Enterprise License (`LICENSE` in this folder).

Everything outside directories named `ee` is MIT. Core code never imports this package (ruff
TID251 enforces it); only the composition roots do, through `app.ee.hooks`. Migrations,
permissions, row-level security and ingest hooks stay in core; this package holds only the
gated logic. Nothing is imported here, because `app.config` imports `app.ee.license` while it
loads.
"""
