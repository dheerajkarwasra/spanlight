"""Shared budget vocabulary. Pure: no database or framework imports."""

from enum import StrEnum


class BudgetScope(StrEnum):
    """Whose spend a budget caps. Every scope but `project` names one value in `scope_id`."""

    PROJECT = "project"
    GATEWAY_KEY = "gateway_key"
    USER = "user"
    MODEL = "model"
    METADATA = "metadata"
    TRACE_NAME = "trace_name"


class BudgetPeriod(StrEnum):
    """The UTC calendar period a budget's spend is summed over before it starts again."""

    DAILY = "daily"
    MONTHLY = "monthly"


class BudgetAction(StrEnum):
    """What an exhausted budget does: notify its channels, or also block gateway calls."""

    NOTIFY = "notify"
    BLOCK = "block"
