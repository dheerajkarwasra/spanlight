"""The shape of a row factory for one row-level-security table (see `tests/rls/factories.py`)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

# Fresh values computed by Postgres, so a factory's INSERT can run twice without a clash.
HEX32 = "md5(random()::text)"
HEX16 = "left(md5(random()::text), 16)"
SLUG = "'t' || left(md5(random()::text), 10)"


@dataclass(frozen=True)
class Parent:
    """A parent row the factory needs: `params` maps a bind parameter to a column of that row."""

    table: str
    params: Mapping[str, str]


def parent(table: str, **params: str) -> Parent:
    return Parent(table=table, params=params)


@dataclass(frozen=True)
class RowFactory:
    """How to insert the minimum row of one table for a project.

    `sql` is an `INSERT … RETURNING *` that binds `:project_id` (tables that carry it) and one
    parameter per parent column. `touch` is a column an `UPDATE` can set to itself, so an update
    changes nothing even when it does reach a row.
    """

    table: str
    sql: str
    touch: str
    parents: tuple[Parent, ...] = field(default=())
