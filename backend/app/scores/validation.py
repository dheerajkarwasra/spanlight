"""Whether one score fits its name's config. Pure: no database, no I/O.

`validate_score` returns the config the score is stored under: the one given, or, when the name
has none yet, a new implicit config with the type the score implies (a string is categorical,
JSON `true`/`false` boolean, any other number numeric). A failure carries a reason written as
`<field>: <message>`, which the API returns per score.

`stored_value` is the value column a valid score is written with: booleans become 1/0 and
numbers keep four decimals.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from app.db.models import ScoreConfig
from app.scores.schemas import NAME_PATTERN, ScoreIn
from app.scores.types import ScoreDataType

VALUE_LIMIT = Decimal(1_000_000_000)
QUANTUM = Decimal("0.0001")
_NAME = re.compile(NAME_PATTERN)


@dataclass(frozen=True)
class ValidationFailure:
    reason: str


def validate_score(score: ScoreIn, config: ScoreConfig | None) -> ScoreConfig | ValidationFailure:
    """The config `score` is stored under, or why it cannot be stored."""
    if not _NAME.fullmatch(score.name):
        return ValidationFailure(
            "name: must be 1 to 64 lowercase letters, digits, '_', '.' or '-', "
            "starting with a letter or digit"
        )
    if (score.value is None) == (score.string_value is None):
        return ValidationFailure("exactly one of value and string_value is required")
    if _out_of_limits(score.value):
        return ValidationFailure(
            f"value: must be between {_text(-VALUE_LIMIT)} and {_text(VALUE_LIMIT)}"
        )
    if config is None:
        return _implicit_config(score)
    failure = _check_against(score, config)
    return config if failure is None else failure


def stored_value(score: ScoreIn) -> Decimal | None:
    """The `value` column of a valid score: 1/0 for a boolean, four decimals for a number."""
    if score.value is None:
        return None
    if isinstance(score.value, bool):
        return Decimal(1) if score.value else Decimal(0)
    return score.value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def _out_of_limits(value: Decimal | bool | None) -> bool:
    if value is None or isinstance(value, bool):
        return False
    return not value.is_finite() or abs(value) > VALUE_LIMIT


def _implicit_config(score: ScoreIn) -> ScoreConfig:
    if score.string_value is not None:
        data_type = ScoreDataType.CATEGORICAL
    elif isinstance(score.value, bool):
        data_type = ScoreDataType.BOOLEAN
    else:
        data_type = ScoreDataType.NUMERIC
    return ScoreConfig(
        name=score.name,
        data_type=data_type,
        min_value=None,
        max_value=None,
        categories=[],
        description=None,
        implicit=True,
    )


def _check_against(score: ScoreIn, config: ScoreConfig) -> ValidationFailure | None:
    mismatch = ValidationFailure(f'data_type: score "{config.name}" is {config.data_type.value}')
    if config.data_type is ScoreDataType.CATEGORICAL:
        if score.string_value is None:
            return mismatch
        if config.categories and score.string_value not in config.categories:
            return ValidationFailure(f"string_value: must be one of {', '.join(config.categories)}")
        return None
    if score.value is None:
        return mismatch
    if config.data_type is ScoreDataType.BOOLEAN:
        # Checked before rounding, so 1.00001 is not taken for 1.
        if not isinstance(score.value, bool) and score.value not in (Decimal(0), Decimal(1)):
            return ValidationFailure("value: must be 0, 1, true or false")
        return None
    return _check_range(stored_value(score), config)


def _check_range(value: Decimal | None, config: ScoreConfig) -> ValidationFailure | None:
    low, high = config.min_value, config.max_value
    if value is None or ((low is None or value >= low) and (high is None or value <= high)):
        return None
    if low is not None and high is not None:
        return ValidationFailure(f"value: must be between {_text(low)} and {_text(high)}")
    if low is not None:
        return ValidationFailure(f"value: must be at least {_text(low)}")
    return ValidationFailure(f"value: must be at most {_text(high)}")


def _text(number: Decimal | None) -> str:
    """A number as people write it: `1`, `0.5`, `-1000000000`."""
    if number is None:
        return ""
    text = format(number.normalize(), "f")
    return "0" if text == "-0" else text
