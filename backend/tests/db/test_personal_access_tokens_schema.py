"""`personal_access_tokens` and migration 0008."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PersonalAccessToken, TokenScope, User
from tests.db.migration_support import downgrade_to, upgrade_to_target

SessionFactory = async_sessionmaker[AsyncSession]

HASH = b"\x01" * 32


async def _user(db: AsyncSession, email: str = "ada@example.com") -> User:
    user = User(email=email, password_hash=None, name="Ada")
    db.add(user)
    await db.flush()
    return user


def _token(
    user: User, prefix: str = "spl_pat_aaaaaaaaaaaa", **columns: object
) -> PersonalAccessToken:
    values: dict[str, object] = {"name": "cli", "scope": TokenScope.READ, "secret_hash": HASH}
    return PersonalAccessToken(user_id=user.id, prefix=prefix, **{**values, **columns})


def test_the_scope_enum_lists_exactly_read_and_write() -> None:
    assert {scope.value for scope in TokenScope} == {"read", "write"}


async def test_a_new_token_is_unrevoked_unexpired_and_unused(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        token = _token(await _user(db))
        db.add(token)
        await db.flush()
        await db.refresh(token)

        assert token.created_at is not None
        assert token.expires_at is None
        assert token.last_used_at is None
        assert token.revoked_at is None
        assert token.scope is TokenScope.READ


async def test_the_prefix_is_unique(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        db.add_all([_token(user), _token(user)])
        with pytest.raises(IntegrityError, match="personal_access_tokens_prefix_key"):
            await db.flush()


async def test_the_schema_refuses_a_scope_other_than_read_and_write(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        user = await _user(db)
        statement = text(
            "INSERT INTO personal_access_tokens "
            "(id, user_id, name, prefix, secret_hash, scope) "
            "VALUES (gen_random_uuid(), :user_id, 'cli', 'spl_pat_bogus', '\\x01', 'admin')"
        )
        with pytest.raises(DataError, match="token_scope"):
            await db.execute(statement, {"user_id": user.id})


@pytest.mark.parametrize("secret_hash", [b"", b"\x01" * 31, b"\x01" * 33])
async def test_the_secret_hash_must_be_a_sha256_digest(
    session_factory: SessionFactory, secret_hash: bytes
) -> None:
    async with session_factory() as db:
        db.add(_token(await _user(db), secret_hash=secret_hash))
        with pytest.raises(IntegrityError, match="personal_access_tokens_secret_hash_check"):
            await db.flush()


@pytest.mark.parametrize("name", ["", "x" * 101])
async def test_the_name_is_one_to_a_hundred_characters(
    session_factory: SessionFactory, name: str
) -> None:
    async with session_factory() as db:
        db.add(_token(await _user(db), name=name))
        with pytest.raises(IntegrityError, match="personal_access_tokens_name_check"):
            await db.flush()


async def test_deleting_the_user_deletes_their_tokens(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        other = await _user(db, "grace@example.com")
        db.add_all([_token(user), _token(other, "spl_pat_bbbbbbbbbbbb")])
        await db.flush()

        await db.delete(user)
        await db.flush()

        remaining = (await db.execute(text("SELECT prefix FROM personal_access_tokens"))).all()
        assert [row[0] for row in remaining] == ["spl_pat_bbbbbbbbbbbb"]


def _scalar(database_url: str, sql: str) -> object:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text(sql)).scalar()
    finally:
        engine.dispose()


@pytest.mark.migration_revision("0008")
async def test_downgrading_removes_the_table_and_its_type_and_upgrading_restores_them(
    migration_database_url: str, migration_session_factory: SessionFactory
) -> None:
    """Tokens cannot survive a downgrade, and the old schema cannot honour them either way.

    Dropping the table ends every token, which is the safe direction: nothing gains access.
    """
    async with migration_session_factory() as db:
        user_id = uuid4()
        await db.execute(
            text("INSERT INTO users (id, email, name) VALUES (:id, 'old@example.com', 'Ada')"),
            {"id": user_id},
        )
        await db.execute(
            text(
                "INSERT INTO personal_access_tokens "
                "(id, user_id, name, prefix, secret_hash, scope) "
                "VALUES (:id, :user, 'CLI', 'spl_pat_historical', :secret, 'read')"
            ),
            {"id": uuid4(), "user": user_id, "secret": HASH},
        )
        await db.commit()

    downgrade_to(migration_database_url, "0007")
    try:
        assert (
            _scalar(migration_database_url, "SELECT to_regclass('personal_access_tokens')") is None
        )
        assert (
            _scalar(migration_database_url, "SELECT 1 FROM pg_type WHERE typname = 'token_scope'")
            is None
        )
    finally:
        upgrade_to_target(migration_database_url)

    assert _scalar(migration_database_url, "SELECT count(*) FROM personal_access_tokens") == 0
    assert (
        _scalar(
            migration_database_url,
            "SELECT count(*) FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'token_scope'",
        )
        == 2
    )
