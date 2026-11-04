"""`api_keys.scopes` and `api_keys.expires_at`, and migration 0007."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scopes import KeyScope
from app.db.models import ApiKey, Organization, Project
from tests.db.migration_support import downgrade_to, upgrade_to_target

SessionFactory = async_sessionmaker[AsyncSession]


async def _project(db: AsyncSession) -> Project:
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    await db.flush()
    project = Project(org_id=org.id, name="Chatbot", slug="chatbot")
    db.add(project)
    await db.flush()
    return project


def _key(project: Project, prefix: str, **columns: object) -> ApiKey:
    return ApiKey(
        project_id=project.id, name=prefix, prefix=prefix, secret_hash=b"\x01" * 32, **columns
    )


async def test_a_key_defaults_to_ingest_write_and_no_expiry(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        key = _key(await _project(db), "spl_live_default")
        db.add(key)
        await db.flush()
        await db.refresh(key)

        assert key.scopes == ["ingest:write"]
        assert key.expires_at is None


def test_the_scope_enum_lists_exactly_the_four_documented_scopes() -> None:
    assert {scope.value for scope in KeyScope} == {
        "ingest:write",
        "traces:read",
        "scores:write",
        "prompts:read",
    }


@pytest.mark.parametrize(
    "scopes",
    [[scope.value] for scope in KeyScope] + [[scope.value for scope in KeyScope]],
)
async def test_the_schema_accepts_every_scope_the_enum_defines(
    session_factory: SessionFactory, scopes: list[str]
) -> None:
    async with session_factory() as db:
        db.add(_key(await _project(db), "spl_live_valid", scopes=scopes))
        await db.flush()


@pytest.mark.parametrize(
    "scopes",
    [[], ["bogus"], ["ingest:write", "bogus"], ["INGEST:WRITE"], ["traces:write"], [""]],
)
async def test_the_schema_refuses_empty_and_unknown_scopes(
    session_factory: SessionFactory, scopes: list[str]
) -> None:
    async with session_factory() as db:
        db.add(_key(await _project(db), "spl_live_invalid", scopes=scopes))
        with pytest.raises(IntegrityError, match="api_keys_scopes_check"):
            await db.flush()


async def test_the_schema_refuses_null_scopes(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        project = await _project(db)
        statement = text(
            "INSERT INTO api_keys (id, project_id, name, prefix, secret_hash, scopes) "
            "VALUES (gen_random_uuid(), :project_id, 'k', 'spl_live_null', '\\x01', NULL)"
        )
        with pytest.raises(IntegrityError, match="scopes"):
            await db.execute(statement, {"project_id": project.id})


def _api_key_rows(database_url: str, sql: str) -> list[tuple[object, ...]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql))]
    finally:
        engine.dispose()


async def _historical_project(db: AsyncSession) -> UUID:
    project = uuid4()
    await db.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Acme', 'acme')"),
        {"id": project},
    )
    await db.execute(
        text("INSERT INTO projects (id, org_id, name, slug) VALUES (:id, :id, 'P', 'p')"),
        {"id": project},
    )
    return project


async def _historical_key(
    db: AsyncSession,
    project: UUID,
    prefix: str,
    *,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO api_keys (id, project_id, name, prefix, secret_hash, scopes, expires_at) "
            "VALUES (:id, :project, :prefix, :prefix, :secret, :scopes, :expires)"
        ),
        {
            "id": uuid4(),
            "project": project,
            "prefix": prefix,
            "secret": b"x" * 32,
            "scopes": scopes if scopes is not None else ["ingest:write"],
            "expires": expires_at,
        },
    )


@pytest.mark.migration_revision("0007")
async def test_downgrading_revokes_the_keys_the_old_schema_would_misread(
    migration_database_url: str, migration_session_factory: SessionFactory
) -> None:
    """Scopes and expiry disappear on downgrade, so a key that relied on them must not survive.

    A key without `ingest:write` would start to ingest, and an expired key would start to work.
    """
    future = datetime.now(UTC) + timedelta(days=30)
    past = datetime.now(UTC) - timedelta(days=1)
    async with migration_session_factory() as db:
        project = await _historical_project(db)
        await _historical_key(db, project, "spl_live_plain")
        await _historical_key(db, project, "spl_live_readonly", scopes=["traces:read"])
        await _historical_key(db, project, "spl_live_both", scopes=["ingest:write", "traces:read"])
        await _historical_key(db, project, "spl_live_expired", expires_at=past)
        await _historical_key(db, project, "spl_live_later", expires_at=future)
        await db.commit()

    downgrade_to(migration_database_url, "0006")
    try:
        rows = _api_key_rows(migration_database_url, "SELECT prefix, revoked_at FROM api_keys")
        revoked = {prefix: revoked_at is not None for prefix, revoked_at in rows}
        assert revoked == {
            "spl_live_plain": False,
            "spl_live_readonly": True,
            "spl_live_both": False,
            "spl_live_expired": True,
            "spl_live_later": False,
        }
    finally:
        upgrade_to_target(migration_database_url)

    # Upgrading again gives the surviving keys the default, as it does for every existing key.
    upgraded = _api_key_rows(
        migration_database_url, "SELECT prefix, scopes, expires_at FROM api_keys ORDER BY prefix"
    )
    assert {prefix: (scopes, expires_at) for prefix, scopes, expires_at in upgraded} == {
        prefix: (["ingest:write"], None)
        for prefix in (
            "spl_live_plain",
            "spl_live_readonly",
            "spl_live_both",
            "spl_live_expired",
            "spl_live_later",
        )
    }


@pytest.mark.migration_revision("0007")
async def test_existing_keys_get_the_ingest_scope_when_upgrading(
    migration_database_url: str, migration_session_factory: SessionFactory
) -> None:
    async with migration_session_factory() as db:
        project = await _historical_project(db)
        await _historical_key(db, project, "spl_live_before")
        await db.commit()

    downgrade_to(migration_database_url, "0006")
    try:
        _api_key_rows(
            migration_database_url, "SELECT prefix FROM api_keys"
        )  # still there, no scopes
    finally:
        upgrade_to_target(migration_database_url)

    rows = _api_key_rows(migration_database_url, "SELECT scopes, expires_at FROM api_keys")
    assert rows == [(["ingest:write"], None)]
