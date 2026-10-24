"""Budgets: a project's spend caps per project, gateway key, end user, model, metadata pair or
trace name.

Members list and read budgets (`project:read`); owners and admins create, edit and delete them
(`alerts:write`). Each budget owns a hidden alert rule that measures its spend; the two change
together, and the rule never appears on the alert-rule routes. Responses are built before the
commit, which ends the project binding that row-level security needs.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status

from app.alerts.rule_errors import UnknownChannelError
from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas import BudgetBurnOut, BudgetOut, BudgetPatch, BudgetSpec
from app.budgets import queries, service
from app.budgets.burn import budget_burn
from app.budgets.errors import (
    BudgetLimitError,
    BudgetNameTakenError,
    BudgetNotFoundError,
    UnknownScopeError,
)
from app.budgets.types import BudgetScope
from app.core.errors import FieldError, ProblemError, conflict, not_found
from app.core.observability import feature_used
from app.core.permissions import Permission

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/budgets", tags=["budgets"])

BudgetReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
BudgetWriter = Annotated[Access, Depends(require(Permission.ALERTS_WRITE))]

# The feature a create counts (program design §3.5), for the scopes Bet 3 added.
SCOPE_FEATURES = {
    BudgetScope.METADATA: "budget_metadata_scope",
    BudgetScope.TRACE_NAME: "budget_trace_name_scope",
}

BUDGET_ERRORS = (
    BudgetNotFoundError,
    BudgetLimitError,
    BudgetNameTakenError,
    UnknownChannelError,
    UnknownScopeError,
)


def _budget_problem(error: Exception) -> ProblemError:
    """The problem response for an error the budget service raises."""
    if isinstance(error, BudgetNotFoundError):
        return not_found()
    if isinstance(error, BudgetLimitError):
        return ProblemError(
            422, "LIMIT_EXCEEDED", f"A project can have at most {error.limit} budgets."
        )
    if isinstance(error, BudgetNameTakenError):
        return conflict("BUDGET_NAME_TAKEN", "A budget with this name already exists here.")
    if isinstance(error, UnknownChannelError):
        listed = ", ".join(str(channel_id) for channel_id in error.channel_ids)
        return ProblemError(
            422,
            "UNKNOWN_CHANNEL",
            "A budget can only notify channels of this organization.",
            errors=[FieldError(field="channel_ids", message=f"Not a channel here: {listed}")],
        )
    if isinstance(error, UnknownScopeError):
        return ProblemError(
            422,
            "UNKNOWN_SCOPE",
            "The budget's scope_id does not name anything its scope can measure.",
            errors=[FieldError(field="scope_id", message=error.message)],
        )
    raise error


def _log(event: str, access: Access, budget_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        budget_id=str(budget_id),
        **fields,
    )


@router.get("", response_model=list[BudgetOut], summary="List budgets")
async def list_budgets(
    project_id: uuid.UUID, access: BudgetReader, db: DbSession, clock: ClockDep
) -> list[BudgetOut]:
    """By name. Unpaginated: a project has at most 50 budgets."""
    rows = await queries.list_budgets(db, access.require_project().id)
    now = clock()
    return [BudgetOut.of(budget, state, now) for budget, state in rows]


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=BudgetOut, summary="Add a budget"
)
async def create_budget(
    project_id: uuid.UUID,
    body: BudgetSpec,
    request: Request,
    access: BudgetWriter,
    db: DbSession,
    clock: ClockDep,
) -> BudgetOut:
    """Store a budget. `409 BUDGET_NAME_TAKEN`; `422 UNKNOWN_SCOPE` for a gateway key outside
    the project, a `metadata` scope that is not `key=value` with a valid key, or a blank trace
    name; `422 UNKNOWN_CHANNEL`; `422 LIMIT_EXCEEDED` past 50 budgets."""
    locked_project_id = await lock_project_of(access, db)
    now = clock()
    try:
        budget = await service.create_budget(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=now,
            ip=client_ip(request),
        )
    except BUDGET_ERRORS as error:
        raise _budget_problem(error) from None
    created = BudgetOut.of(budget, None, now)
    await db.commit()
    _log("budget_created", access, budget.id, scope=budget.scope.value, action=budget.action.value)
    feature = SCOPE_FEATURES.get(budget.scope)
    if feature is not None:
        feature_used(feature)
    return created


@router.get("/{budget_id}", response_model=BudgetOut, summary="Get a budget")
async def get_budget(
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    access: BudgetReader,
    db: DbSession,
    clock: ClockDep,
) -> BudgetOut:
    found = await queries.get_budget(db, access.require_project().id, budget_id)
    if found is None:
        raise not_found()
    budget, state = found
    return BudgetOut.of(budget, state, clock())


@router.get("/{budget_id}/burn", response_model=BudgetBurnOut, summary="Get a budget's burn")
async def get_budget_burn(
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    access: BudgetReader,
    db: DbSession,
    clock: ClockDep,
) -> BudgetBurnOut:
    """Spend per UTC day of the current period up to now, with the running total. A day whose
    spans were all unpriced is `null`; the running total counts the known days only."""
    found = await queries.get_budget(db, access.require_project().id, budget_id)
    if found is None:
        raise not_found()
    return await budget_burn(db, found[0], clock())


@router.patch("/{budget_id}", response_model=BudgetOut, summary="Edit a budget")
async def update_budget(
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    body: BudgetPatch,
    request: Request,
    access: BudgetWriter,
    db: DbSession,
    clock: ClockDep,
    settings: SettingsDep,
) -> BudgetOut:
    """Change the fields sent; send `scope_id` together with `scope`. Errors as for the create.

    Changing the amount, scope or period of a firing budget, or disabling it, resolves its alert
    first and sends `alert.resolved`. A new scope or period clears the spend shown until the next
    evaluation, about a minute later.
    """
    locked_project_id = await lock_project_of(access, db)
    now = clock()
    try:
        budget = await service.update_budget(
            db,
            access.org.id,
            locked_project_id,
            budget_id,
            access.user_id,
            body,
            now=now,
            ip=client_ip(request),
            settings=settings,
        )
    except BUDGET_ERRORS as error:
        raise _budget_problem(error) from None
    updated = BudgetOut.of(budget, await queries.get_state(db, budget.rule_id), now)
    await db.commit()
    _log("budget_updated", access, budget.id)
    return updated


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a budget")
async def delete_budget(
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    request: Request,
    access: BudgetWriter,
    db: DbSession,
    clock: ClockDep,
    settings: SettingsDep,
) -> None:
    """Delete the budget with its alert history. A firing budget is resolved first and sends
    `alert.resolved`; a blocking one stops blocking at once."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_budget(
            db,
            access.org.id,
            locked_project_id,
            budget_id,
            access.user_id,
            now=clock(),
            ip=client_ip(request),
            settings=settings,
        )
    except BUDGET_ERRORS as error:
        raise _budget_problem(error) from None
    await db.commit()
    _log("budget_deleted", access, budget_id)
