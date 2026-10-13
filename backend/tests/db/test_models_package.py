"""The ORM models live in a package split by domain, with one public import path."""

import importlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import Base

# Created by Alembic itself, not by a migration of ours: it has no ORM model.
MIGRATION_BOOKKEEPING = {"alembic_version"}

DOMAIN_MODULES = (
    "alerts",
    "annotations",
    "auth",
    "base",
    "budgets",
    "datasets",
    "end_users",
    "evals",
    "experiments",
    "exports",
    "gateway",
    "identity",
    "insights",
    "notifications",
    "platform",
    "pricing",
    "projects",
    "prompts",
    "rollups",
    "scores",
    "telemetry",
)


async def test_models_package_exports_every_table(engine: AsyncEngine) -> None:
    """The ORM models and the migrated database describe the same tables, in both directions."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
    migrated = {row[0] for row in rows} - MIGRATION_BOOKKEEPING
    modelled = set(Base.metadata.tables)

    assert modelled, "no ORM model is registered: the models package imports nothing"
    assert sorted(migrated - modelled) == [], "tables with no ORM model"
    assert sorted(modelled - migrated) == [], "ORM models with no migrated table"


def test_domain_modules_exist() -> None:
    for module in DOMAIN_MODULES:
        assert importlib.import_module(f"app.db.models.{module}")
