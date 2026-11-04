"""Deleting an organization or a project, with everything that belongs to it.

Both lean on the foreign keys: a project takes its traces, spans and API keys with it, and an
organization takes its memberships, invites and audit events. The organization's `projects` key
is `RESTRICT`, so its projects go first. Row-level security does not get in the way: the
referential actions of a foreign key are not subject to it, so a handler that has bound no project
(an organization route) still removes all of the telemetry.

The callers commit. Everything here happens in the caller's transaction, which is what makes a
deletion all or nothing. That includes queueing the `purge_project_objects` job, which deletes the
project's export files from object storage, so the files go exactly when the rows do.

Lock order: the organization, then the project. Every deleter takes `lock_organization` first,
and `lock_project` only after it, because the two deletions need each other's rows: deleting a
project writes an audit event, which needs a shared lock on the organization row, and deleting an
organization deletes its projects, which needs their rows. Taking them in opposite orders deadlocks
two such requests (one of them fails with a 500), so a new deleter must follow this order too.

The same rule covers writers, not only deleters: any path that touches a project row, or a row
that cascades from it such as an API key, and then writes an audit event must take the
organization first, because the audit insert needs a shared lock on the organization row.
`lock_project_for_write` does that for a handler that changes a project or something under it,
and `lock_org_for_write` for one that changes a row the organization owns (a provider credential,
a price override).
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, Project
from app.db.timeouts import lift_statement_limit
from app.exports.purge import enqueue_project_purge


async def lock_organization(db: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    """The organization, locked until the transaction ends; None if it is already gone.

    Locking it first also orders deletion against a project being created in it: that insert holds
    a shared lock on this row, so it either finishes before the delete sees the row's projects or
    waits for the delete and finds no organization.
    """
    return await db.scalar(
        select(Organization)
        .where(Organization.id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def lock_project(db: AsyncSession, project_id: uuid.UUID) -> Project | None:
    """The project, locked until the transaction ends; None if it is already gone.

    Two people deleting the same project at once cannot both succeed, so only one writes the
    audit event. Take `lock_organization` first (see the lock order in the module docstring).
    """
    return await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def lock_org_for_write(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """Lock the organization row `FOR NO KEY UPDATE` for a write under it that is not a deletion.

    Returns False when it is already gone (a concurrent deletion won), and the caller answers
    404. Take it before the row the write changes and before the audit event, so the write
    follows the lock order above: the lock conflicts with the exclusive one an organization
    deletion takes, so either waits for the other instead of deadlocking.

    `with_for_update(key_share=True)` renders `FOR NO KEY UPDATE` (only `read=True` as well would
    make it `FOR KEY SHARE`). That lock conflicts with itself, so the writers of one organization
    run one after the other, while inserts that merely reference the row (an audit event's
    foreign key) are not blocked. Count limits rely on that: the alert channel limit
    (`app.alerts.service`) counts after taking this lock, so two creates cannot both pass it.
    Weakening it to `FOR KEY SHARE` would make such limits racy.
    """
    org = await db.scalar(
        select(Organization.id).where(Organization.id == org_id).with_for_update(key_share=True)
    )
    return org is not None


async def lock_project_for_write(
    db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID
) -> bool:
    """Lock the organization, then the project, `FOR NO KEY UPDATE` for a non-deleting write.

    Returns False when either is already gone (a concurrent deletion won), and the caller answers
    404. These locks conflict with the exclusive ones the deleters take, so a deletion waits
    for this transaction, and this one waits for a deletion that is already running; taking the
    organization first keeps both in the order described in the module docstring.
    """
    if not await lock_org_for_write(db, org_id):
        return False
    project = await db.scalar(
        select(Project.id).where(Project.id == project_id).with_for_update(key_share=True)
    )
    return project is not None


async def delete_project_rows(db: AsyncSession, project_id: uuid.UUID) -> None:
    # The foreign keys remove every trace and span in one statement, which on a large project
    # takes far longer than the limit that protects the other requests.
    await lift_statement_limit(db)
    await db.execute(
        delete(Project).where(Project.id == project_id).execution_options(synchronize_session=False)
    )
    await enqueue_project_purge(db, project_id)


async def delete_organization(db: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    """Delete the organization's projects, then the organization; return the projects' ids.

    The caller has locked the organization (`lock_organization`).
    """
    await lift_statement_limit(db)  # as in `delete_project_rows`
    # In id order, as the budget reaper takes them; the DELETE alone locks them in scan order.
    await db.execute(
        select(Project.id).where(Project.org_id == org_id).order_by(Project.id).with_for_update()
    )
    project_ids = await db.scalars(
        delete(Project)
        .where(Project.org_id == org_id)
        .returning(Project.id)
        .execution_options(synchronize_session=False)
    )
    deleted = list(project_ids.all())
    for project_id in deleted:
        await enqueue_project_purge(db, project_id)
    await db.execute(
        delete(Organization)
        .where(Organization.id == org_id)
        .execution_options(synchronize_session=False)
    )
    return deleted
