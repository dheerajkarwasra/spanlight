"""Locking and unlocking a dataset. Callers commit. Each change takes the dataset's row lock and
is audited once; a repeat changes nothing and records nothing."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets.service import _locked
from app.db.models import AuditAction, Dataset
from app.services.audit import record_audit


async def lock_dataset(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    now: datetime,
    ip: str | None = None,
) -> Dataset:
    """Lock the dataset. Locking a locked dataset keeps its `locked_at` and records nothing.
    Raises `DatasetNotFoundError`."""
    return await _set_lock(db, org_id, project_id, dataset_id, actor_id, now, ip=ip)


async def unlock_dataset(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    now: datetime,
    ip: str | None = None,
) -> Dataset:
    """Unlock the dataset. Unlocking an unlocked dataset records nothing. Raises
    `DatasetNotFoundError`."""
    return await _set_lock(db, org_id, project_id, dataset_id, actor_id, None, ip=ip)


async def _set_lock(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    actor_id: uuid.UUID,
    locked_at: datetime | None,
    *,
    ip: str | None,
) -> Dataset:
    dataset = await _locked(db, project_id, dataset_id)
    if (dataset.locked_at is None) == (locked_at is None):
        return dataset
    dataset.locked_at = locked_at
    await db.flush()
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.DATASET_LOCK if locked_at else AuditAction.DATASET_UNLOCK,
        target_type="dataset",
        target_id=dataset.id,
        ip=ip,
        metadata={"name": dataset.name},
    )
    return dataset
