"""Projects and their onboarding status."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import Access, DbSession, client_ip, require
from app.api.schemas import (
    ActivationOut,
    OnboardingOut,
    ProjectCreateIn,
    ProjectDeleteIn,
    ProjectOut,
    ProjectUpdateIn,
)
from app.core.errors import confirmation_mismatch, conflict, forbidden, not_found
from app.core.permissions import Permission
from app.db.models import AuditAction, Project, Trace
from app.insights.channels import check_insight_channels
from app.services.activation import activation_steps
from app.services.audit import record_audit
from app.services.deletion import (
    delete_project_rows,
    lock_organization,
    lock_project,
    lock_project_for_write,
)
from app.services.slugs import slugify, with_random_suffix

router = APIRouter(tags=["projects"])

ProjectReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
ProjectWriter = Annotated[Access, Depends(require(Permission.PROJECT_WRITE))]
ProjectDeleter = Annotated[Access, Depends(require(Permission.PROJECT_DELETE))]


@router.get("/orgs/{org_id}/projects", response_model=list[ProjectOut])
async def list_projects(org_id: uuid.UUID, access: ProjectReader, db: DbSession) -> list[Project]:
    projects = await db.scalars(
        select(Project).where(Project.org_id == access.org.id).order_by(Project.created_at)
    )
    return list(projects.all())


@router.post(
    "/orgs/{org_id}/projects", status_code=status.HTTP_201_CREATED, response_model=ProjectOut
)
async def create_project(
    org_id: uuid.UUID,
    body: ProjectCreateIn,
    request: Request,
    access: ProjectWriter,
    db: DbSession,
) -> Project:
    project = await _insert_project(db, access.org.id, body.name)
    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.PROJECT_CREATE,
        target_type="project",
        target_id=project.id,
        ip=client_ip(request),
        metadata={"name": project.name},
    )
    await db.commit()
    await db.refresh(project)
    return project


async def _insert_project(db: DbSession, org_id: uuid.UUID, name: str) -> Project:
    slug = slugify(name, fallback="project")
    for attempt in range(5):
        candidate = slug if attempt == 0 else with_random_suffix(slug)
        project = Project(org_id=org_id, name=name, slug=candidate)
        try:
            async with db.begin_nested():
                db.add(project)
        except IntegrityError:
            continue
        return project
    raise conflict("SLUG_UNAVAILABLE", "Could not allocate a unique slug; try another name.")


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, access: ProjectReader) -> Project:
    return access.require_project()


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdateIn,
    request: Request,
    access: ProjectWriter,
    db: DbSession,
) -> Project:
    project = access.require_project()
    # The organization is locked before the project row changes, the order deletions use.
    if not await lock_project_for_write(db, access.org.id, project.id):
        raise not_found()
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "insight_channel_ids" in changes:
        await check_insight_channels(db, access.org.id, changes["insight_channel_ids"])
    audit = _update_audit(project, body)
    for field, value in changes.items():
        setattr(project, field, value)
    if changes:
        await record_audit(
            db,
            org_id=access.org.id,
            actor_user_id=access.user_id,
            action=AuditAction.PROJECT_UPDATE,
            target_type="project",
            target_id=project.id,
            ip=client_ip(request),
            metadata=audit,
        )
    await db.commit()
    return project


def _update_audit(project: Project, body: ProjectUpdateIn) -> dict[str, object]:
    """The `project.update` metadata, read before the project changes.

    `changes` is what was sent (JSON mode: channel ids are stored as strings). When the promoted
    keys change, `previous` holds the list they replace: key names only, never metadata values.
    """
    audit: dict[str, object] = {
        "changes": body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    }
    keys = body.promoted_metadata_keys
    if keys is not None and keys != list(project.promoted_metadata_keys):
        audit["previous"] = {"promoted_metadata_keys": list(project.promoted_metadata_keys)}
    return audit


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    body: ProjectDeleteIn,
    request: Request,
    access: ProjectDeleter,
    db: DbSession,
) -> None:
    """Delete the project with its traces, spans and API keys.

    Admins and owners may, by typing the project's slug as `confirm`. The org's audit log keeps a
    `project.delete` event with the name and slug, written in the same transaction.
    """
    if access.org.is_demo:
        raise forbidden("The demo organization cannot be changed.")
    # The organization first, then the project: the order every deletion locks in (see
    # `app.services.deletion`), so this cannot deadlock with deleting the organization.
    if await lock_organization(db, access.org.id) is None:
        raise not_found()
    project = await lock_project(db, project_id)
    if project is None:
        raise not_found()
    if body.confirm != project.slug:
        raise confirmation_mismatch("`confirm` must be the project's slug.")

    await record_audit(
        db,
        org_id=access.org.id,
        actor_user_id=access.user_id,
        action=AuditAction.PROJECT_DELETE,
        target_type="project",
        target_id=project.id,
        ip=client_ip(request),
        metadata={"name": project.name, "slug": project.slug},
    )
    await delete_project_rows(db, project.id)
    await db.commit()
    # The keys went with the project; this process stops accepting them now, not after the TTL.
    request.app.state.key_cache.clear()


@router.get("/projects/{project_id}/onboarding", response_model=OnboardingOut)
async def onboarding_status(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession
) -> OnboardingOut:
    project = access.require_project()
    first_trace_at = await db.scalar(
        select(func.min(Trace.started_at)).where(Trace.project_id == project.id)
    )
    return OnboardingOut(has_traces=first_trace_at is not None, first_trace_at=first_trace_at)


@router.get("/projects/{project_id}/activation", response_model=ActivationOut)
async def activation_status(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession
) -> ActivationOut:
    """Which setup checklist steps are done: a first trace (not the setup test trace), a trace
    with a session, a budget, and a score from an evaluator or a person."""
    return ActivationOut(**await activation_steps(db, access.require_project().id))
