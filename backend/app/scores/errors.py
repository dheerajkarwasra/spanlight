"""What the score services raise. The API maps each to a problem response (`api/v1/scores.py`)."""


class ScoreNotFoundError(Exception):
    """No score with this id in the project."""


class ScoreDeleteForbiddenError(Exception):
    """The caller may not delete this score: it is someone else's, or not a person's."""


class ScoreConfigNotFoundError(Exception):
    """No score config with this id in the project."""


class ScoreConfigNameTakenError(Exception):
    """Another config of the project has this name."""


class ScoreConfigInUseError(Exception):
    """Scores carry this config's name, so it cannot be deleted or change type."""


class ScoreConfigLimitError(Exception):
    """The project already has the maximum number of score configs."""
