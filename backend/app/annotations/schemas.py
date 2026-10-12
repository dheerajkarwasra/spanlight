"""What annotation queues are created and worked from. Pure: no database, no I/O."""

import uuid
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ingest.schemas import TraceId
from app.scores.schemas import Comment, ScoreValue, SpanRef, StringValue

MAX_TRACES_PER_ADD = 200
MAX_CONFIGS_PER_QUEUE = 20

QueueName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Instructions = Annotated[str, StringConstraints(max_length=4000)]
ScoreName = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class QueueSpec(BaseModel):
    """The body of a queue create."""

    model_config = ConfigDict(extra="forbid")

    name: QueueName
    instructions: Instructions | None = None
    score_config_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_CONFIGS_PER_QUEUE)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.score_config_ids)) != len(self.score_config_ids):
            raise ValueError("score_config_ids must be distinct")
        return self


class ItemsIn(BaseModel):
    """Traces to queue; `span_id` queues that span of each trace instead of the whole trace."""

    model_config = ConfigDict(extra="forbid")

    trace_ids: list[TraceId] = Field(min_length=1, max_length=MAX_TRACES_PER_ADD)
    span_id: SpanRef | None = None


class CompletedScore(BaseModel):
    """One score a reviewer gives; exactly one of `value` and `string_value`."""

    model_config = ConfigDict(extra="forbid")

    name: ScoreName
    value: ScoreValue | None = None
    string_value: StringValue | None = None
    comment: Comment | None = None


class Completion(BaseModel):
    """The body of a complete: one score per config of the queue."""

    model_config = ConfigDict(extra="forbid")

    scores: list[CompletedScore] = Field(min_length=1, max_length=MAX_CONFIGS_PER_QUEUE)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        names = [score.name for score in self.scores]
        if len(set(names)) != len(names):
            raise ValueError("each score name may appear once")
        return self
