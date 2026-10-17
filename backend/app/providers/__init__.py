"""Provider health: calls and failures per provider, the error timeseries and sample failures.

The routes in `app.api.v1.providers` call `service`; `queries` holds the raw-span and rollup
reads, `series` the pure folding of both halves of a window into the answers.
"""
