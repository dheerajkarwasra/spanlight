"""Price overrides: an organization's own rates, applied to every span its projects ingest.

Members read the list; owners and admins add and delete (`gateway:write`). An override beats the
seed price of the same provider and pattern, and applies to spans that start at or after its
`effective_from`; spans already stored keep the cost they were priced with, until a replay of
the same span re-prices it with the overrides in force at replay time.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.schemas import PriceOverrideCreate, PriceOverrideOut, UserOut
from app.core.errors import conflict, not_found
from app.core.permissions import Permission
from app.db.models import PriceOverride, User
from app.pricing import overrides_service, queries
from app.pricing.overrides_service import PriceOverrideExistsError, PriceOverrideNotFoundError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs/{org_id}/price-overrides", tags=["prices"])

OverrideReader = Annotated[Access, Depends(require(Permission.ORG_READ))]
OverrideWriter = Annotated[Access, Depends(require(Permission.GATEWAY_WRITE))]


def _override_out(override: PriceOverride, creator: User | None) -> PriceOverrideOut:
    return PriceOverrideOut(
        id=override.id,
        provider=override.provider,
        model_pattern=override.model_pattern,
        input_per_mtok=override.input_per_mtok,
        output_per_mtok=override.output_per_mtok,
        cached_input_per_mtok=override.cached_input_per_mtok,
        cache_write_5m_per_mtok=override.cache_write_5m_per_mtok,
        cache_write_1h_per_mtok=override.cache_write_1h_per_mtok,
        min_prompt_tokens=override.min_prompt_tokens,
        effective_from=override.effective_from,
        created_by=UserOut.model_validate(creator) if creator else None,
        created_at=override.created_at,
    )


@router.get("", response_model=list[PriceOverrideOut], summary="List price overrides")
async def list_price_overrides(
    org_id: uuid.UUID, access: OverrideReader, db: DbSession
) -> list[PriceOverrideOut]:
    """Ordered by provider, pattern, start time and tier."""
    rows = await queries.list_overrides(db, access.org.id)
    return [_override_out(override, creator) for override, creator in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PriceOverrideOut,
    summary="Add a price override",
)
async def create_price_override(
    org_id: uuid.UUID,
    body: PriceOverrideCreate,
    request: Request,
    access: OverrideWriter,
    db: DbSession,
    clock: ClockDep,
) -> PriceOverrideOut:
    """Rates are USD per million tokens. `effective_from` defaults to now.

    The pattern matches a model exactly or with a snapshot suffix, like the seed prices. The
    optional cache-write rates fall back to the input rate; `min_prompt_tokens` (default 0) makes
    the override one tier of the model, applied to prompts of at least that many tokens.
    `409 PRICE_OVERRIDE_EXISTS` when the organization already has one for the same provider,
    pattern, start time and tier.
    """
    values = body.model_dump()
    values["effective_from"] = body.effective_from or clock()
    try:
        override = await overrides_service.create_override(
            db, access.org.id, access.user_id, values, ip=client_ip(request)
        )
    except PriceOverrideNotFoundError:
        raise not_found() from None
    except PriceOverrideExistsError:
        raise conflict(
            "PRICE_OVERRIDE_EXISTS",
            "This organization already has a price override for this provider, model pattern, "
            "start time and min_prompt_tokens.",
        ) from None
    await db.commit()
    await db.refresh(override)
    logger.info(
        "price_override_created", org_id=str(access.org.id), price_override_id=str(override.id)
    )
    return _override_out(override, access.auth.user)


@router.delete(
    "/{override_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a price override",
)
async def delete_price_override(
    org_id: uuid.UUID,
    override_id: uuid.UUID,
    request: Request,
    access: OverrideWriter,
    db: DbSession,
) -> None:
    """Spans ingested from now on fall back to the next price; stored costs are not rewritten."""
    try:
        await overrides_service.delete_override(
            db, access.org.id, override_id, access.user_id, ip=client_ip(request)
        )
    except PriceOverrideNotFoundError:
        raise not_found() from None
    await db.commit()
    logger.info(
        "price_override_deleted", org_id=str(access.org.id), price_override_id=str(override_id)
    )
