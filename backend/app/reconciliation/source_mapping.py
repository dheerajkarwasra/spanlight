"""A billing source's project mapping: which projects it may cover, and writing them (§6.5).

A project is compared with one account per provider: `UNIQUE (project_id, provider)` on
`billing_source_projects`. `free_projects` checks that before a write so the answer names the
other source; `conflict_of` reads a unique violation that a concurrent write caused anyway.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import violated_constraint
from app.db.models.billing import BillingProvider, BillingSource, BillingSourceProject
from app.reconciliation import queries
from app.reconciliation.errors import NameTaken, ProjectNotInOrg, ProjectTaken

NAME_CONSTRAINT = "billing_sources_org_id_name_key"
PROJECT_CONSTRAINT = "billing_source_projects_project_id_provider_key"


async def free_projects(
    db: AsyncSession,
    org_id: uuid.UUID,
    provider: BillingProvider,
    sent: Sequence[uuid.UUID],
    *,
    except_source: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """`sent` without repeats, once each is a project of the organization that no other source
    of `provider` covers. Raises `ProjectNotInOrg` and `ProjectTaken`."""
    project_ids = list(dict.fromkeys(sent))
    found = await queries.org_project_ids(db, org_id, project_ids)
    if missing := [project_id for project_id in project_ids if project_id not in found]:
        raise ProjectNotInOrg(missing)
    taken = await queries.source_names_taking_projects(
        db, org_id, provider, project_ids, except_source=except_source
    )
    if taken:
        raise ProjectTaken(min(taken.values()), sorted(taken))
    return project_ids


async def conflict_of(
    db: AsyncSession,
    error: IntegrityError,
    *,
    org_id: uuid.UUID,
    provider: BillingProvider,
    source_id: uuid.UUID,
    project_ids: list[uuid.UUID],
) -> Exception | None:
    """What a unique violation of a write means: a name or a project another write just took
    (None for any other violation). Takes plain ids, captured before the savepoint: its
    rollback expires the source, which must not be read again here."""
    constraint = violated_constraint(error)
    if constraint == NAME_CONSTRAINT:
        return NameTaken()
    if constraint == PROJECT_CONSTRAINT:
        taken = await queries.source_names_taking_projects(
            db, org_id, provider, project_ids, except_source=source_id
        )
        return ProjectTaken(min(taken.values(), default="another source"), sorted(taken))
    return None


async def map_projects(
    db: AsyncSession, source: BillingSource, project_ids: list[uuid.UUID]
) -> None:
    await db.execute(
        delete(BillingSourceProject).where(
            BillingSourceProject.org_id == source.org_id,
            BillingSourceProject.source_id == source.id,
        )
    )
    db.add_all(
        BillingSourceProject(
            source_id=source.id,
            project_id=project_id,
            org_id=source.org_id,
            provider=source.provider,
        )
        for project_id in project_ids
    )
    await db.flush()
