"""The Integration Lab: fault profiles that make gateway calls fail the way providers do.

Members list profiles (`project:read`); owners and admins create, edit and delete them
(`gateway:write`). A profile does nothing until it is attached to a key (`PATCH
.../gateway/keys/{key_id}` with `fault_profile_id`), and never attaches to a production key.
Deleting a profile detaches its keys.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas import FaultProfileCreate, FaultProfileOut, FaultProfileUpdate, UserOut
from app.core.errors import ProblemError, conflict, not_found
from app.core.permissions import Permission
from app.db.models import FaultProfile, User
from app.gateway import fault_queries, fault_service
from app.gateway.fault_errors import (
    FaultProfileNameTakenError,
    FaultProfileNotFoundError,
    InvalidFaultProfileError,
)
from app.gateway.fault_params import load_stored_params
from app.gateway.fault_schemas import is_active

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/gateway/fault-profiles", tags=["gateway"])

LabReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
LabWriter = Annotated[Access, Depends(require(Permission.GATEWAY_WRITE))]

LAB_ERRORS = (FaultProfileNotFoundError, FaultProfileNameTakenError, InvalidFaultProfileError)


def _profile_out(
    profile: FaultProfile,
    creator: User | None,
    attached_key_ids: list[uuid.UUID],
    now: ClockDep,
) -> FaultProfileOut:
    return FaultProfileOut(
        id=profile.id,
        name=profile.name,
        scenario=profile.scenario,
        params=load_stored_params(profile.scenario, profile.params),
        probability=float(profile.probability),
        enabled=profile.enabled,
        expires_at=profile.expires_at,
        active=is_active(profile.enabled, profile.expires_at, now()),
        attached_key_ids=attached_key_ids,
        created_by=UserOut.model_validate(creator) if creator else None,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _lab_problem(error: Exception) -> ProblemError:
    """The problem response for an error the fault profile service raises."""
    if isinstance(error, FaultProfileNotFoundError):
        return not_found()
    if isinstance(error, FaultProfileNameTakenError):
        return conflict(
            "FAULT_PROFILE_NAME_TAKEN", "A fault profile with this name already exists."
        )
    if isinstance(error, InvalidFaultProfileError):
        return ProblemError(422, "VALIDATION_ERROR", "The request is invalid.", errors=error.errors)
    raise error


async def _found_profile(
    db: DbSession, project_id: uuid.UUID, profile_id: uuid.UUID, clock: ClockDep
) -> FaultProfileOut:
    found = await fault_queries.get_profile_with_creator(db, project_id, profile_id)
    if found is None:
        raise not_found()
    attached = await fault_queries.attached_key_ids(db, project_id)
    return _profile_out(*found, attached.get(profile_id, []), clock)


def _log(event: str, access: Access, profile_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        fault_profile_id=str(profile_id),
        **fields,
    )


@router.get("", response_model=list[FaultProfileOut], summary="List fault profiles")
async def list_profiles(
    project_id: uuid.UUID, access: LabReader, db: DbSession, clock: ClockDep
) -> list[FaultProfileOut]:
    """By name. `active` says whether a profile can fire now: enabled and not expired."""
    project = access.require_project()
    rows = await fault_queries.list_profiles(db, project.id)
    attached = await fault_queries.attached_key_ids(db, project.id)
    return [
        _profile_out(profile, creator, attached.get(profile.id, []), clock)
        for profile, creator in rows
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=FaultProfileOut,
    summary="Create a fault profile",
)
async def create_profile(
    project_id: uuid.UUID,
    body: FaultProfileCreate,
    request: Request,
    access: LabWriter,
    db: DbSession,
    clock: ClockDep,
) -> FaultProfileOut:
    """Omitted `params` are the scenario's defaults; a param that does not belong to the scenario
    is `422` on `params.<name>`. `409 FAULT_PROFILE_NAME_TAKEN` for a name already used in the
    project, and `422` on `expires_at` for a time that is not in the future.
    """
    project_id = await lock_project_of(access, db)
    try:
        profile = await fault_service.create_profile(
            db,
            access.org.id,
            project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except LAB_ERRORS as error:
        raise _lab_problem(error) from None
    # Built before the commit, which ends the project binding that row-level security needs.
    created = _profile_out(profile, access.auth.user, [], clock)
    await db.commit()
    _log("fault_profile_created", access, profile.id, scenario=profile.scenario.value)
    return created


@router.patch("/{profile_id}", response_model=FaultProfileOut, summary="Edit a fault profile")
async def update_profile(
    project_id: uuid.UUID,
    profile_id: uuid.UUID,
    body: FaultProfileUpdate,
    request: Request,
    access: LabWriter,
    db: DbSession,
    clock: ClockDep,
) -> FaultProfileOut:
    """Change the fields sent; send `null` for `expires_at` to remove the expiry.

    `params` must be sent with `scenario`; a `scenario` sent alone resets the params to that
    scenario's defaults.
    """
    project_id = await lock_project_of(access, db)
    try:
        await fault_service.update_profile(
            db,
            access.org.id,
            project_id,
            profile_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except LAB_ERRORS as error:
        raise _lab_problem(error) from None
    updated = await _found_profile(db, project_id, profile_id, clock)
    await db.commit()
    # This process's gateway calls read the change from the next call, not after the key cache TTL.
    request.app.state.gateway_key_cache.clear()
    _log("fault_profile_updated", access, profile_id, fields=sorted(body.model_fields_set))
    return updated


@router.delete(
    "/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a fault profile"
)
async def delete_profile(
    project_id: uuid.UUID,
    profile_id: uuid.UUID,
    request: Request,
    access: LabWriter,
    db: DbSession,
) -> None:
    """Delete the profile. Keys that ran it are detached and run no fault."""
    project_id = await lock_project_of(access, db)
    try:
        await fault_service.delete_profile(
            db, access.org.id, project_id, profile_id, access.user_id, ip=client_ip(request)
        )
    except LAB_ERRORS as error:
        raise _lab_problem(error) from None
    await db.commit()
    request.app.state.gateway_key_cache.clear()
    _log("fault_profile_deleted", access, profile_id)
