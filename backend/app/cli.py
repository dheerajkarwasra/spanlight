"""`spanlight`: administrative commands for operators.

spanlight create-user --email ada@example.com --name "Ada" [--org acme --role owner]
spanlight reset-password --email ada@example.com
spanlight reset-2fa --email ada@example.com
spanlight sync-prices
spanlight reseal-credentials
spanlight migrate
spanlight ensure-app-role --role spanlight_app   # password from APP_DB_PASSWORD
spanlight rollups backfill --project <uuid> --from 2026-09-01 --to 2026-10-01
spanlight alerts evaluate [--dry-run] [--json]
spanlight demo seed-evals
spanlight backup now
spanlight backup list
spanlight restore --key backups/2026/10/09/spanlight-20261009T030000Z.dump   # RESTORE_TARGET_URL
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import totp_service
from app.backups.retention import BACKUP_PREFIX
from app.backups.service import BackupError, RestoreError, restore_backup, run_backup
from app.cli_alerts import alerts_app
from app.cli_demo import demo_app
from app.config import get_settings
from app.core.crypto import CryptoNotConfigured
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.db.migrations import upgrade_to_head
from app.db.models import AuditAction, Membership, MembershipRole, Organization, Project, User
from app.db.roles import ensure_app_role
from app.db.session import create_engine, create_session_factory
from app.gateway.reseal import ResealResult, reseal_credentials
from app.pricing.cost import sync_seed_prices
from app.rollups.compute import backfill_rollups
from app.rollups.jobs import MAX_BACKFILL_DAYS
from app.services.audit import record_audit
from app.services.credentials import replace_password
from app.storage.object_store import ObjectStore, get_object_store

app = typer.Typer(help="Spanlight administration.", no_args_is_help=True)
rollups_app = typer.Typer(help="Hourly metric rollups.", no_args_is_help=True)
app.add_typer(rollups_app, name="rollups")
app.add_typer(alerts_app, name="alerts")
app.add_typer(demo_app, name="demo")
backup_app = typer.Typer(help="Database backups in object storage.", no_args_is_help=True)
app.add_typer(backup_app, name="backup")

EmailOption = Annotated[str, typer.Option(help="The user's email address.")]


def _run[ResultT](operation: Callable[[AsyncSession], Awaitable[ResultT]]) -> ResultT:
    async def runner() -> ResultT:
        engine = create_engine(get_settings().database_url, pool_size=1)
        try:
            async with create_session_factory(engine)() as session:
                return await operation(session)
        finally:
            await engine.dispose()

    return asyncio.run(runner())


def _validated_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise typer.BadParameter(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


def _prompt_password() -> str:
    password: str = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    return _validated_password(password)


@app.command("create-user")
def create_user(
    email: EmailOption,
    name: Annotated[str, typer.Option(help="Display name.")],
    org: Annotated[str | None, typer.Option(help="Slug of an org to add the user to.")] = None,
    role: Annotated[MembershipRole, typer.Option(help="Role in --org.")] = MembershipRole.MEMBER,
) -> None:
    """Create a user (prompting for a password), optionally adding them to an org."""
    password = _prompt_password()

    async def operation(db: AsyncSession) -> None:
        organization = None
        if org is not None:
            organization = await db.scalar(select(Organization).where(Organization.slug == org))
            if organization is None:
                raise typer.BadParameter(f"no organization with slug {org!r}", param_hint="--org")

        user = User(email=email, password_hash=hash_password(password), name=name)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise typer.BadParameter("a user with this email already exists") from exc

        if organization is not None:
            db.add(Membership(org_id=organization.id, user_id=user.id, role=role))
            await record_audit(
                db,
                org_id=organization.id,
                actor_user_id=None,
                action=AuditAction.MEMBER_ADD,
                target_type="user",
                target_id=user.id,
                metadata={"role": role.value, "via": "cli"},
            )
        await db.commit()
        typer.echo(f"Created user {user.id} <{email}>")

    _run(operation)


@app.command("reset-password")
def reset_password(email: EmailOption) -> None:
    """Set a new password and sign the user out everywhere."""
    password = _prompt_password()

    async def operation(db: AsyncSession) -> None:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            raise typer.BadParameter(f"no user with email {email!r}", param_hint="--email")

        await replace_password(db, user, hash_password(password))
        org_ids = (
            await db.scalars(select(Membership.org_id).where(Membership.user_id == user.id))
        ).all()
        for org_id in org_ids:
            await record_audit(
                db,
                org_id=org_id,
                actor_user_id=None,
                action=AuditAction.USER_PASSWORD_RESET,
                target_type="user",
                target_id=user.id,
                metadata={"via": "cli"},
            )
        await db.commit()
        typer.echo(f"Password reset for <{email}>; all sessions revoked.")

    _run(operation)


@app.command("reset-2fa")
def reset_two_factor(email: EmailOption) -> None:
    """Turn two-factor authentication off for a user who lost the app and the recovery codes.

    Deletes the secret and the recovery codes, as the user turning it off would. The password
    and the sessions are left as they are (`reset-password` signs the user out everywhere).
    """

    async def operation(db: AsyncSession) -> None:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            raise typer.BadParameter(f"no user with email {email!r}", param_hint="--email")

        if not await totp_service.reset(db, user):
            typer.echo(
                f"Two-factor authentication is not turned on for <{email}>; nothing changed."
            )
            return
        org_ids = (
            await db.scalars(select(Membership.org_id).where(Membership.user_id == user.id))
        ).all()
        for org_id in org_ids:
            await record_audit(
                db,
                org_id=org_id,
                actor_user_id=None,
                action=AuditAction.USER_TOTP_DISABLE,
                target_type="user",
                target_id=user.id,
                metadata={"via": "cli"},
            )
        await db.commit()
        typer.echo(f"Two-factor authentication turned off for <{email}>; recovery codes deleted.")

    _run(operation)


@app.command("sync-prices")
def sync_prices() -> None:
    """Insert the bundled model price snapshot (existing rows are kept)."""

    async def operation(db: AsyncSession) -> int:
        inserted = await sync_seed_prices(db)
        await db.commit()
        return inserted

    typer.echo(f"Inserted {_run(operation)} price rows.")


@app.command("reseal-credentials")
def reseal_credentials_command() -> None:
    """Re-seal every provider credential under the first key of CREDENTIALS_KEYS.

    Run it after putting a new key first. It recounts afterwards and exits non-zero while any
    provider credential is still sealed under an older key; once it succeeds, none needs the
    older keys any more. Prints counts only, never a key.
    """
    settings = get_settings()

    async def operation(db: AsyncSession) -> ResealResult:
        return await reseal_credentials(db, settings=settings)

    try:
        result = _run(operation)
    except CryptoNotConfigured:
        raise _fail("CREDENTIALS_KEYS is not set") from None
    typer.echo(f"Re-sealed {result.resealed} provider credentials.")
    if result.failed:
        typer.echo(
            f"{result.failed} provider credentials could not be opened with any key in "
            "CREDENTIALS_KEYS and were left as they were; their ids are in the log.",
            err=True,
        )
    if result.remaining:
        raise _fail(
            f"{result.remaining} provider credentials are still sealed under an older key; "
            "keep every key in CREDENTIALS_KEYS and run the command again."
        )


@app.command("migrate")
def migrate() -> None:
    """Apply database migrations (alembic upgrade head)."""
    upgrade_to_head(get_settings().database_url)
    typer.echo("Database is at the latest migration.")


@app.command("ensure-app-role")
def ensure_app_role_command(
    role: Annotated[
        str, typer.Option(help="Role the API and worker connect as.")
    ] = "spanlight_app",
) -> None:
    """Create or update the non-superuser app role and grant it data access.

    Run as the database owner after `spanlight migrate`. The password is read from
    APP_DB_PASSWORD so it never appears in the process list or shell history.
    """
    password = os.environ.get("APP_DB_PASSWORD", "")
    if len(password) < 16:
        raise typer.BadParameter("APP_DB_PASSWORD must be set to at least 16 characters")
    ensure_app_role(get_settings().database_url, role, password)
    typer.echo(f"Role {role} is ready (no superuser, no RLS bypass).")


def _parse_instant(value: str, param_hint: str) -> datetime:
    """An ISO 8601 date or datetime; a value without a UTC offset is read as UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{value!r} is not an ISO 8601 date", param_hint=param_hint
        ) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@rollups_app.command("backfill")
def backfill_rollups_command(
    project: Annotated[uuid.UUID, typer.Option(help="Project id.")],
    from_: Annotated[str, typer.Option("--from", help="Start (inclusive), ISO 8601; UTC if bare.")],
    to: Annotated[str, typer.Option(help="End (exclusive), ISO 8601; UTC if bare.")],
) -> None:
    """Recompute the hourly rollups of one project over [--from, --to), at most 90 days.

    Both ends are widened to whole hours. Use it for spans that arrived more than 48 hours
    after they started, which the scheduled job no longer revisits.
    """
    start = _parse_instant(from_, "--from")
    end = _parse_instant(to, "--to")
    if start >= end:
        raise typer.BadParameter("--from must be earlier than --to", param_hint="--from")
    if end - start > timedelta(days=MAX_BACKFILL_DAYS):
        raise typer.BadParameter(
            f"the range may not be longer than {MAX_BACKFILL_DAYS} days", param_hint="--to"
        )

    async def operation(db: AsyncSession) -> int:
        if await db.get(Project, project) is None:
            raise typer.BadParameter(f"no project with id {project}", param_hint="--project")
        return await backfill_rollups(db, project, start, end)

    typer.echo(f"Wrote {_run(operation)} rollup rows.")


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code=1)


def _require_object_store() -> ObjectStore:
    store = get_object_store(get_settings())
    if store is None:
        raise _fail(
            "object storage is not configured (set S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY)"
        )
    return store


@backup_app.command("now")
def backup_now() -> None:
    """Dump the database to object storage now and print the key and size.

    Needs BACKUP_DATABASE_URL and object storage. BACKUPS_ENABLED only governs the nightly
    job, so it does not stop a backup you ask for. Old backups are not pruned; the nightly job
    does that.
    """
    settings = get_settings()
    if settings.backup_database_url is None:
        raise _fail("BACKUP_DATABASE_URL is not set")
    store = _require_object_store()
    try:
        result = asyncio.run(run_backup(settings, store, datetime.now(UTC)))
    except BackupError as error:
        raise _fail(str(error)) from None
    typer.echo(f"Backed up to {result.key} ({result.size_bytes} bytes).")


@backup_app.command("list")
def backup_list() -> None:
    """List the stored backups, oldest first, with their size in bytes."""
    store = _require_object_store()
    objects = asyncio.run(store.list(BACKUP_PREFIX))
    if not objects:
        typer.echo("No backups found.")
        return
    for info in objects:
        typer.echo(f"{info.key}  {info.size}  {info.last_modified.isoformat()}")


@app.command("restore")
def restore(
    key: Annotated[str, typer.Option(help="Object key from `spanlight backup list`.")],
    target_url: Annotated[
        str,
        typer.Option(
            envvar="RESTORE_TARGET_URL",
            help="URL of a new, empty database. Prefer the environment variable: a URL on the "
            "command line is visible in the process list and the shell history.",
        ),
    ],
) -> None:
    """Restore a backup into an empty database.

    Refuses a database that already has tables. Afterwards run `spanlight ensure-app-role`
    against the restored database so the API and worker can connect.
    """
    store = _require_object_store()
    try:
        asyncio.run(restore_backup(store, key, target_url))
    except RestoreError as error:
        raise _fail(str(error)) from None
    typer.echo(f"Restored {key}.")
    typer.echo("Next: run `spanlight ensure-app-role` against the restored database.")


if __name__ == "__main__":
    app()
