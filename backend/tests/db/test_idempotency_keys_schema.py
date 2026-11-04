"""`idempotency_keys` and migration 0010: the invariants the table enforces itself."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import IdempotencyKey
from tests.db.migration_support import downgrade_to, upgrade_to_target

SessionFactory = async_sessionmaker[AsyncSession]

HASH = b"\x01" * 32


def _row(**columns: Any) -> IdempotencyKey:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "principal_id": "user:1",
        "key": "order-42",
        "request_hash": HASH,
        "expires_at": now + timedelta(hours=24),
    }
    return IdempotencyKey(**{**values, **columns})


async def test_a_reserved_row_has_no_outcome_yet(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        row = _row()
        db.add(row)
        await db.flush()
        await db.refresh(row)

        assert row.created_at is not None
        assert (row.status, row.body, row.content_type) == (None, None, None)


async def test_a_completed_row_keeps_its_status_body_and_content_type(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        db.add(
            _row(
                status=201,
                body={"id": 7, "tags": ["a"]},
                content_type="application/json",
            )
        )
        await db.commit()
    async with session_factory() as db:
        row = await db.get(IdempotencyKey, ("user:1", "order-42"))
        assert row is not None
        assert (row.status, row.body, row.content_type) == (
            201,
            {"id": 7, "tags": ["a"]},
            "application/json",
        )


async def test_a_principal_holds_a_key_once_and_two_principals_may_share_one(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        db.add_all([_row(principal_id="user:1"), _row(principal_id="key:1")])
        await db.flush()
        db.add(_row(principal_id="user:1"))
        with pytest.raises(IntegrityError, match="idempotency_keys_pkey"):
            await db.flush()


@pytest.mark.parametrize("key", ["", "k" * 129])
async def test_a_key_is_1_to_128_characters(session_factory: SessionFactory, key: str) -> None:
    async with session_factory() as db:
        db.add(_row(key=key))
        with pytest.raises(IntegrityError, match="idempotency_keys_key_check"):
            await db.flush()


@pytest.mark.parametrize("request_hash", [b"", b"\x01" * 31, b"\x01" * 33])
async def test_the_request_hash_is_a_sha256_digest(
    session_factory: SessionFactory, request_hash: bytes
) -> None:
    async with session_factory() as db:
        db.add(_row(request_hash=request_hash))
        with pytest.raises(IntegrityError, match="idempotency_keys_request_hash_check"):
            await db.flush()


@pytest.mark.parametrize("status", [100, 199, 500, 503])
async def test_only_answers_that_are_kept_can_be_stored(
    session_factory: SessionFactory, status: int
) -> None:
    async with session_factory() as db:
        db.add(_row(status=status, body={}, content_type="application/json"))
        with pytest.raises(IntegrityError, match="idempotency_keys_status_check"):
            await db.flush()


async def test_a_row_without_a_status_cannot_carry_a_body_or_a_content_type(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        db.add(_row(body={"half": "done"}))
        with pytest.raises(IntegrityError, match="idempotency_keys_outcome_check"):
            await db.flush()


async def test_a_body_may_be_missing_from_a_stored_answer(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        db.add(_row(status=204))
        await db.flush()


async def test_a_row_cannot_expire_before_it_was_created(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        db.add(_row(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        with pytest.raises(IntegrityError, match="idempotency_keys_expiry_check"):
            await db.flush()


async def test_none_is_stored_as_sql_null_not_as_json_null(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        db.add(_row(status=204, body=None))
        await db.commit()
        stored = (
            await db.execute(
                text("SELECT body IS NULL, body = 'null'::jsonb FROM idempotency_keys")
            )
        ).one()
    assert tuple(stored) == (True, None)


async def test_expired_rows_are_found_through_an_index(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        indexes = (
            (
                await db.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = 'idempotency_keys'")
                )
            )
            .scalars()
            .all()
        )
    assert any("(expires_at)" in definition for definition in indexes)


@pytest.mark.migration_revision("0010")
def test_downgrading_past_0010_drops_the_table_and_upgrading_brings_it_back(
    migration_database_url: str,
) -> None:
    engine = create_engine(migration_database_url)

    def table_exists() -> bool:
        with engine.connect() as connection:
            return bool(
                connection.execute(text("SELECT to_regclass('public.idempotency_keys')")).scalar()
            )

    try:
        assert table_exists()
        downgrade_to(migration_database_url, "0009")
        try:
            assert not table_exists()
        finally:
            upgrade_to_target(migration_database_url)
        assert table_exists()
    finally:
        engine.dispose()
