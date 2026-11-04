"""The organization, owner, project and API keys the load-test seeder works in.

Built from the application's own models and helpers (`generate_key`, `slugify`), because no
service function creates a workspace: the API handlers do it inline.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scopes import KeyScope
from app.core.security import API_KEY_PREFIX, UNUSABLE_PASSWORD_HASH, generate_key
from app.db.models import ApiKey, Membership, MembershipRole, Organization, Project, User
from app.pricing.cost import sync_seed_prices
from app.rollups.activation import NEW_PROJECT_BOUNDARY
from app.services.slugs import slugify, with_random_suffix
from seed_data import SPREAD

READ_KEY_COUNT = 10  # a key may read 20 times a second; the k6 scripts spread over ten
RETENTION_MARGIN = timedelta(days=1)


class WorkspaceError(Exception):
    """The workspace cannot be used for seeding."""


@dataclass(frozen=True)
class Workspace:
    project_id: uuid.UUID
    org_id: uuid.UUID
    owner_id: uuid.UUID
    ingest_key: str
    read_keys: list[str]


def _add_key(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, name: str, scope: KeyScope
) -> str:
    """Add an API key to the session and return its plaintext, the only time it exists."""
    generated = generate_key(API_KEY_PREFIX)
    session.add(
        ApiKey(
            project_id=project_id,
            name=name,
            prefix=generated.prefix,
            secret_hash=generated.secret_hash,
            created_by=user_id,
            scopes=[scope.value],
        )
    )
    return generated.plaintext


def _check_retention(project: Project) -> None:
    """The retention job deletes spans older than the project's retention; the seeded ones must
    stay well inside it."""
    if timedelta(days=project.retention_days) < SPREAD + RETENTION_MARGIN:
        raise WorkspaceError(
            f"the project keeps spans for {project.retention_days} days, which is too "
            f"close to the {SPREAD.days}-day spread: the retention job could delete the data"
        )


async def create_workspace(session_factory: async_sessionmaker[AsyncSession]) -> Workspace:
    async with session_factory() as session:
        org = Organization(
            name="Load test", slug=with_random_suffix(slugify("Load test", fallback="org"))
        )
        user = User(
            email=f"load-{secrets.token_hex(4)}@example.invalid",
            password_hash=UNUSABLE_PASSWORD_HASH,
            name="Load test",
        )
        session.add_all([org, user])
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=MembershipRole.OWNER))
        # A new project has no history from before this release, so its aggregate coverage
        # starts at the boundary `activate-aggregate-coverage` gives new projects. Without it a
        # stack that only ran migrations leaves coverage unverified and the backfill writes nothing.
        project = Project(
            org_id=org.id,
            name="Load test",
            slug="load-test",
            aggregate_history_unverified_before=datetime.fromisoformat(NEW_PROJECT_BOUNDARY),
        )
        session.add(project)
        await session.flush()
        await session.refresh(project)  # retention_days is a server default
        _check_retention(project)
        ingest_key = _add_key(session, project.id, user.id, "ingest", KeyScope.INGEST_WRITE)
        read_keys = [
            _add_key(session, project.id, user.id, f"read-{number}", KeyScope.TRACES_READ)
            for number in range(1, READ_KEY_COUNT + 1)
        ]
        await sync_seed_prices(session)  # without prices every cost would be unknown
        await session.commit()
        return Workspace(
            project_id=project.id,
            org_id=org.id,
            owner_id=user.id,
            ingest_key=ingest_key,
            read_keys=read_keys,
        )
