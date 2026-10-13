"""How a configured model name matches the model a span or a request carries.

One rule everywhere (prices, alert and budget filters, the budget guard): the names are equal, or
the actual name is the configured one followed by a snapshot suffix, `-YYYYMMDD`, `-YYYY-MM-DD`
or `-latest`. So `gpt-4o` matches `gpt-4o-2024-08-06` but `gpt-4.1` does not match
`gpt-4.1-nano`. This module holds the Python predicate and the SQL for filtering a column by a
configured name; the budget guard builds the opposite direction (the configured name in a
column) next to its own query.
"""

import re

# POSIX form of the suffix, for the `~` operator.
SNAPSHOT_SUFFIX_REGEX = "^-([0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}|latest)$"
_SNAPSHOT_SUFFIX = re.compile(SNAPSHOT_SUFFIX_REGEX)


def matches_model(configured: str, actual: str) -> bool:
    """True when `actual` is `configured` itself or `configured` plus a snapshot suffix."""
    if actual == configured:
        return True
    if not actual.startswith(configured):
        return False
    return _SNAPSHOT_SUFFIX.fullmatch(actual[len(configured) :]) is not None


def like_prefix(name: str) -> str:
    """`name` as a LIKE pattern that matches any value starting with it (`\\` is the escape)."""
    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def model_filter_sql(column: str, param: str = "model") -> str:
    """A SQL condition: `column` matches the configured name bound as `:param`.

    The caller binds `param` to the name and `{param}_like` to `like_prefix(name)`. `column`
    must be a trusted identifier, never user input. The condition is not an index condition: a
    plain btree cannot serve a prefix `LIKE` under a non-C collation, and the `OR` rules out the
    equality alone. Callers bound the read by an indexed range first (a project's spans by
    `started_at`), and this runs as a filter on the rows in that range.
    """
    like = f"{param}_like"
    return (
        f"({column} = :{param} OR ({column} LIKE :{like} ESCAPE '\\' "
        f"AND substr({column}, length(CAST(:{param} AS text)) + 1) ~ '{SNAPSHOT_SUFFIX_REGEX}'))"
    )
