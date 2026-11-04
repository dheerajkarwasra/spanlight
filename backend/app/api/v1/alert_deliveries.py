"""The delivery log of an alert channel, and retrying a delivery that failed.

Members read it (`org:read`); admins and owners retry (`alerts:write`). The log lists the
channel's outbox rows newest first, one per email recipient for an email channel. It never
returns a row's target or payload, only a summary of the alert and the last error.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request

from app.alerts import deliveries
from app.alerts.channels.errors import ChannelNotFoundError
from app.alerts.deliveries import (
    DeliveryNotFoundError,
    DeliveryNotRetryableError,
    NotRetryableReason,
)
from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.schemas import DeliveryOut, Page
from app.api.schemas.alert_deliveries import delivery_out
from app.core.errors import conflict, not_found
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.permissions import Permission
from app.db.models import NotificationStatus

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs/{org_id}/alert-channels/{channel_id}/deliveries", tags=["alerts"])

NOT_RETRYABLE_DETAILS: dict[NotRetryableReason, str] = {
    "not_failed": "Only a delivery that failed can be retried.",
    "never_rendered": "This delivery could not be rendered and cannot be retried.",
    "quarantined": "This delivery was quarantined by restore-reconcile and is replayed there.",
    "recipient_left": "The recipient is no longer a verified member of this organization.",
}

DeliveryReader = Annotated[Access, Depends(require(Permission.ORG_READ))]
DeliveryRetrier = Annotated[Access, Depends(require(Permission.ALERTS_WRITE))]


@router.get("", response_model=Page[DeliveryOut], summary="List a channel's deliveries")
async def list_deliveries(
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    access: DeliveryReader,
    db: DbSession,
    status: Annotated[NotificationStatus | None, Query()] = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[DeliveryOut]:
    """Newest first. `status` keeps `pending`, `sent` or `failed` deliveries."""
    try:
        rows, next_cursor = await deliveries.list_deliveries(
            db, access.org.id, channel_id, status=status, cursor=cursor, limit=limit
        )
    except ChannelNotFoundError:
        raise not_found() from None
    return Page(items=[delivery_out(row) for row in rows], next_cursor=next_cursor)


@router.post("/{delivery_id}/retry", response_model=DeliveryOut, summary="Retry a failed delivery")
async def retry_delivery(
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    delivery_id: uuid.UUID,
    request: Request,
    access: DeliveryRetrier,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> DeliveryOut:
    """Queue a failed delivery again, with the same id, so receivers still deduplicate on it.

    Its attempts start over and the worker picks it up at once; the last error stays until the
    next attempt. `409 NOT_RETRYABLE` unless the delivery is `failed` and `retryable`.
    """
    try:
        row = await deliveries.retry_delivery(
            db,
            access.org.id,
            channel_id,
            delivery_id,
            access.user_id,
            settings=settings,
            now=clock(),
            ip=client_ip(request),
        )
    except (ChannelNotFoundError, DeliveryNotFoundError):
        raise not_found() from None
    except DeliveryNotRetryableError as error:
        raise conflict("NOT_RETRYABLE", NOT_RETRYABLE_DETAILS[error.reason]) from None
    retried = delivery_out(row)
    await db.commit()
    logger.info(
        "alert_delivery_retried",
        org_id=str(access.org.id),
        channel_id=str(channel_id),
        delivery_id=str(delivery_id),
    )
    return retried
