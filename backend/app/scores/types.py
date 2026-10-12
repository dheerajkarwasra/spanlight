"""The enums scores and score configs are stored with (migration 0502)."""

from enum import StrEnum


class ScoreDataType(StrEnum):
    """What a score's value is: a number, one of a list of strings, or yes/no stored as 1/0."""

    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"


class ScoreSource(StrEnum):
    """Who wrote a score: an API key, a person in the dashboard, or an evaluator."""

    API = "api"
    HUMAN = "human"
    EVAL = "eval"
