"""Reconciliation: provider invoices against Spanlight's own cost figures (spec §7.3-7.6).

Mostly pure modules with no I/O: `types` (the shared row shapes), `line_items` (what every line
shares), `anthropic_lines`, `openai_lines`, `csv_rows` and `csv_import` (one input shape each →
`MappedLine`), `usage_merge` (token counts and priority usage from the usage reports), and the
report's arithmetic (`arithmetic`, `explained`, `token_totals`). `retention` is the one module that
touches the database: the retention job's deletes of provider cost lines and fetch runs. The fetch
jobs, the CSV route and the report service are the imperative shells around the pure modules.
"""
