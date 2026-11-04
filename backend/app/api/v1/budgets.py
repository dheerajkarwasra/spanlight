"""Budgets: a project's spend caps per project, gateway key, end user, model, metadata pair or
trace name.

Members list and read budgets (`project:read`); owners and admins create, edit and delete them
(`alerts:write`). Each budget owns a hidden alert rule that measures its spend; the two change
together, and the rule never appears on the alert-rule routes. Responses are built before the
commit, which ends the project binding that row-level security needs.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.alerts.rule_errors import UnknownChannelError
from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas import BudgetBurnOut, BudgetOut, BudgetPatch, BudgetSpec
from app.budgets import queries, service
from app.budgets.burn import budget_burn
from app.budgets.errors import (
    BudgetHistoryRetentionError,
    BudgetLimitError,
    BudgetNameTakenError,
    BudgetNotFoundError,
    BudgetReservationsPendingError,
    UnknownScopeError,
)
from app.budgets.types import BudgetScope
from app.core.db_errors import is_lock_timeout
from app.core.errors import FieldError, ProblemError, conflict, not_found
from app.core.observability import BUDGET_BUSY, feature_used
from app.core.permissions import Permission
from app.db.rls import bind_project

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/budgets", tags=["budgets"])

BudgetReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
BudgetWriter = Annotated[Access, Depends(require(Permission.ALERTS_WRITE))]

# The feature a create counts (program design §3.5), for the scopes Bet 3 added.
SCOPE_FEATURES = {
    BudgetScope.METADATA: "budget_metadata_scope",
    BudgetScope.TRACE_NAME: "budget_trace_name_scope",
}

# An edit or delete locks the budget `FOR NO KEY UPDATE`, which waits for every gateway admission
# holding it `FOR SHARE`, and admissions that arrive meanwhile do not queue behind the edit
# (Postgres grants a compatible share lock at once). Under a sustained burst of calls the edit
# could wait up to the statement timeout and fail as a plain 503; instead it waits a short
# `lock_timeout` a few times and then says what is busy.
BUSY_ATTEMPTS = 3
BUSY_LOCK_TIMEOUT_MS = 2000

BUDGET_ERRORS = (
    BudgetNotFoundError,
    BudgetHistoryRetentionError,
    BudgetLimitError,
    BudgetReservationsPendingError,
    BudgetNameTakenError,
    UnknownChannelError,
    UnknownScopeError,
)


def _budget_problem(error: Exception) -> ProblemError:
    """The problem response for an error the budget service raises."""
    if isinstance(error, BudgetReservationsPendingError):
        return conflict(
            "BUDGET_RESERVATIONS_PENDING",
            "Scope and period cannot change while calls are pending "
            "or existing charges cannot be reconstructed. "
            "Wait for settlement or for uncertain "
            "charges to leave the relevant period.",
        )
    if isinstance(error, BudgetHistoryRetentionError):
        return ProblemError(
            422,
            "BUDGET_HISTORY_RETENTION",
            f"An enabled block budget requires at least {error.days} days of project retention.",
        )
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


def _budget_busy() -> ProblemError:
    return ProblemError(
        503,
        "BUDGET_BUSY",
        "The budget is busy with other requests right now. Try the change again in a few seconds.",
        headers={"Retry-After": "5"},
    )


async def _write_with_retries[T](
    db: DbSession,
    access: Access,
    budget_id: uuid.UUID,
    attempt: Callable[[uuid.UUID], Awaitable[T]],
) -> T:
    """Lock the project of `access` and run `attempt` (a budget write, given the locked
    project's id) in a transaction whose lock waits are bounded, up to `BUSY_ATTEMPTS` times; a
    try that times out on any of its locks, the organization's and project's included, is rolled
    back and repeated, and the last one answers `503 BUDGET_BUSY`. A statement timeout is not
    retried: it is a slow query, not a busy budget, and answers the usual 503."""
    project = access.require_project()
    # Read before any rollback, which expires the rows.
    ids = {"org_id": str(access.org.id), "project_id": str(project.id), "budget_id": str(budget_id)}
    for tries_left in reversed(range(BUSY_ATTEMPTS)):
        await db.execute(
            text("SELECT set_config('lock_timeout', :ms, true)"), {"ms": str(BUSY_LOCK_TIMEOUT_MS)}
        )
        try:
            return await attempt(await lock_project_of(access, db))
        except OperationalError as error:
            if not is_lock_timeout(error.orig):
                raise
            await db.rollback()
            outcome = "retried" if tries_left else "refused"
            BUDGET_BUSY.labels(outcome=outcome).inc()
            logger.warning(
                "budget_busy", **ids, attempt=BUSY_ATTEMPTS - tries_left, outcome=outcome
            )
            if not tries_left:
                raise _budget_busy() from None
            # The rollback expired the rows the request already holds and ended the project
            # binding (`SET LOCAL`): load them here, where awaiting is possible, and bind again.
            for row in (access.auth.user, access.org, project):
                await db.refresh(row)
            await bind_project(db, project.id)
    raise AssertionError("unreachable: every try returns or raises")


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
    project = access.require_project().id
    rows = await queries.list_budgets(db, project)
    now = clock()
    counters = await queries.period_counters(db, project, now)
    return [BudgetOut.of(budget, state, now, counters) for budget, state in rows]


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
    now = clock()
    counters = await queries.period_counters(db, budget.project_id, now)
    return BudgetOut.of(budget, state, now, counters)


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

    async def attempt(locked_project_id: uuid.UUID) -> BudgetOut:
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
        state = await queries.get_state(db, budget.rule_id)
        counters = await queries.period_counters(db, locked_project_id, now)
        return BudgetOut.of(budget, state, now, counters)

    updated = await _write_with_retries(db, access, budget_id, attempt)
    await db.commit()
    _log("budget_updated", access, budget_id)
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

    async def attempt(locked_project_id: uuid.UUID) -> None:
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

    await _write_with_retries(db, access, budget_id, attempt)
    await db.commit()
    _log("budget_deleted", access, budget_id)
