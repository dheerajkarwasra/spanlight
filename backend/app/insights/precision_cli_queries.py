"""The precision table across every project, or every project of one organization, for
`spanlight doctor precision`.

It reads votes of many projects, so it runs with row-level security bypassed, in a transaction of
its own that it ends itself (the bypass is local to it). It returns counts per detector kind and
nothing that names a project, an insight or a user. Only the CLI imports it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Insight, Organization, Project
from app.db.rls import bypass_rls
from app.insights.precision import PrecisionRow
from app.insights.precision_queries import precision_counts, precision_rows_of


class UnknownOrganizationError(LookupError):
    """`--org` names no organization."""


async def precision_rows_all(db: AsyncSession, *, org_id: uuid.UUID | None) -> list[PrecisionRow]:
    """One row per detector kind with an insight anywhere (in the organization), by kind."""
    async with db.begin():
        await bypass_rls(db)
        if org_id is None:
            statement = precision_counts()
        else:
            if await db.get(Organization, org_id) is None:
                raise UnknownOrganizationError(org_id)
            in_org = select(Project.id).where(Project.org_id == org_id)
            statement = precision_counts(Insight.project_id.in_(in_org))
        rows = await db.execute(statement)
        return precision_rows_of(rows.all())
