"""What the dataset routers share: permissions, paging and the structured log line."""

import uuid
from typing import Annotated

import structlog
from fastapi import Depends

from app.api.deps import Access, require
from app.core.permissions import Permission

logger = structlog.get_logger("app.api.v1.datasets")

DatasetReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
DatasetWriter = Annotated[Access, Depends(require(Permission.DATASETS_WRITE))]

DEFAULT_PAGE = 50


def log_dataset_event(event: str, access: Access, dataset_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        dataset_id=str(dataset_id),
        **fields,
    )


def page_of[ItemT](rows: list[ItemT], limit: int) -> tuple[list[ItemT], bool]:
    """The first `limit` rows and whether a further page exists (the query fetched one extra)."""
    return rows[:limit], len(rows) > limit
