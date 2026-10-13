"""The gateway key prefix policy shows one key, for reading only, and only while it is set.

A gateway key is found by its prefix before its project is known (`app.gateway.key_context`).
The `gateway_keys_prefix_lookup` policy (migration 0202) is the narrow door for that one read.
"""

import uuid
from dataclasses import dataclass
from typing import Any, cast

import pytest
from sqlalchemy import CursorResult, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from app.gateway.key_context import find_key, load_key_context
from tests.rls.factories import Row, create, create_parents, create_project, insert

Sessions = async_sessionmaker[AsyncSession]

_SET_PREFIX = text("SELECT set_config('app.gateway_key_prefix', :prefix, true)")


@dataclass(frozen=True)
class TwoKeys:
    project_a: uuid.UUID
    project_b: uuid.UUID
    key_a: Row
    key_b: Row


@pytest.fixture
async def two_keys(session_factory: Sessions) -> TwoKeys:
    async with session_factory() as db:
        await bypass_rls(db)
        project_a, project_b = await create_project(db), await create_project(db)
        key_a = await create(db, "gateway_keys", project_a)
        key_b = await create(db, "gateway_keys", project_b)
        await db.commit()
    return TwoKeys(project_a=project_a, project_b=project_b, key_a=key_a, key_b=key_b)


async def _new_key_params(session_factory: Sessions, project_id: uuid.UUID) -> Row:
    """The INSERT parameters for one more key of the project, its route created as the worker."""
    async with session_factory() as db:
        await bypass_rls(db)
        params = await create_parents(db, "gateway_keys", project_id)
        await db.commit()
    return params


async def _rowcount(db: AsyncSession, statement: str) -> int:
    return cast(CursorResult[Any], await db.execute(text(statement))).rowcount


async def test_gateway_key_prefix_lookup_is_narrow(
    session_factory: Sessions, two_keys: TwoKeys
) -> None:
    params = await _new_key_params(session_factory, two_keys.project_a)
    async with session_factory() as db:
        await db.execute(_SET_PREFIX, {"prefix": two_keys.key_a["prefix"]})
        visible = (await db.scalars(text("SELECT id FROM gateway_keys"))).all()
        assert visible == [two_keys.key_a["id"]]  # two keys exist; the prefix shows one
        # The policy is FOR SELECT: the row it shows cannot be changed or removed through it.
        assert await _rowcount(db, "UPDATE gateway_keys SET name = name") == 0
        assert await _rowcount(db, "DELETE FROM gateway_keys") == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await insert(db, "gateway_keys", params)


async def test_find_key_clears_the_prefix_after_reading(
    session_factory: Sessions, two_keys: TwoKeys
) -> None:
    async with session_factory() as db:
        key = await find_key(db, two_keys.key_a["prefix"])
        assert key is not None and key.id == two_keys.key_a["id"]
        assert await db.scalar(text("SELECT count(*) FROM gateway_keys")) == 0


async def test_an_unknown_prefix_shows_no_key(session_factory: Sessions, two_keys: TwoKeys) -> None:
    async with session_factory() as db:
        assert await find_key(db, "spl_gw_" + "a" * 12) is None


async def test_a_key_of_another_project_loads_its_own_project_not_the_bound_one(
    session_factory: Sessions, two_keys: TwoKeys
) -> None:
    async with session_factory() as db:
        # A request already bound to project A presents project B's key.
        await bind_project(db, two_keys.project_a)
        key = await find_key(db, two_keys.key_b["prefix"])
        assert key is not None
        context = await load_key_context(db, key)
        assert context is not None
        assert context.project_id == two_keys.project_b
        assert context.route_id == two_keys.key_b["route_id"]
        # From here on the transaction sees project B's rows and none of project A's.
        keys = (await db.scalars(text("SELECT id FROM gateway_keys"))).all()
        assert keys == [two_keys.key_b["id"]]
        routes = (await db.scalars(text("SELECT id FROM gateway_routes"))).all()
        assert routes == [two_keys.key_b["route_id"]]
