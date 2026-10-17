"""What a detector reads: rows loaded once per project run, and the baselines. Pure."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from uuid import UUID

from app.insights.schemas import Window
from app.provider_status.types import ProviderIncidentRow


@dataclass(frozen=True, slots=True)
class GatewayAttrs:
    """The `spanlight.gateway.*`, `spanlight.cache` and `spanlight.fault.*` attributes of a span."""

    route: str | None = None
    target: str | None = None
    attempts: int | None = None
    cache: str | None = None  # "hit", "miss", "off" or "bypass"
    upstream_status: int | None = None
    retry_after_s: Decimal | None = None
    client_disconnected: bool = False
    fault_scenario: str | None = None
    fault_profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class LlmSpanRow:
    """One stored llm span with the fields of its trace. Unknown values are `None`, never 0."""

    trace_id: str
    span_id: str
    session_id: str | None
    external_user_id: str | None
    environment: str | None
    release: str | None
    started_at: datetime
    duration_ms: int | None
    status: str
    error_class: str | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cost_usd: Decimal | None
    request_hash: str | None
    finish_reason: str | None
    status_message: str | None
    gateway: GatewayAttrs | None = None
    # `gateway` when Spanlight's own key limiter refused the call: no provider was asked.
    rate_limit_kind: str | None = None

    @property
    def fault_scenario(self) -> str | None:
        return self.gateway.fault_scenario if self.gateway else None

    @property
    def is_cache_hit(self) -> bool:
        return self.gateway is not None and self.gateway.cache == "hit"


@dataclass(frozen=True, slots=True)
class ToolSpanRow:
    """One stored tool span. `input_hash` is 32 hex of the SHA-256 of the stored input's jsonb
    text, computed by the query; `None` when the input was not stored."""

    trace_id: str
    span_id: str
    session_id: str | None
    started_at: datetime
    name: str
    input_hash: str | None
    status: str


@dataclass(frozen=True, slots=True)
class OrgProviderRow:
    """A provider's last 30 minutes across the organization, fault-injected spans excluded."""

    project_id: UUID
    provider: str
    calls: int
    provider_5xx: int


class _AnyValue(Enum):
    ANY = "any"


ANY = _AnyValue.ANY
"""Baseline dimension meaning "all values of this dimension" (no grouping on it)."""

Dimension = str | None | _AnyValue
BaselineKey = tuple[str, Dimension, Dimension, Dimension]


@dataclass(frozen=True, slots=True)
class Baselines:
    """Previous-7-day values per slice, filled by the engine; read-only to detectors.

    Metrics: `error_rate`, `p95_ms`, `cost_usd_per_hour`, `llm_calls_per_hour`. Keys are
    `(metric, environment, provider, model)` and each dimension is exact: a value (`"production"`),
    `None` for the NULL value (a trace without an environment), or `ANY` for all values of that
    dimension. The engine builds the mapping from grouped rollup reads, so the NULL-environment
    slice and the all-environments slice are distinct entries. A detector asks for the slice it
    grouped by and passes `None` only for a group whose value really is NULL. A slice with too
    little history is absent, and `get` returns `None` for it.
    """

    values: Mapping[BaselineKey, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def get(
        self,
        metric: str,
        *,
        environment: Dimension = ANY,
        provider: Dimension = ANY,
        model: Dimension = ANY,
    ) -> Decimal | None:
        return self.values.get((metric, environment, provider, model))


@dataclass(frozen=True, slots=True)
class DetectorContext:
    """Everything a detector may look at. The engine builds it; detectors never query."""

    project_id: UUID
    org_id: UUID
    now: datetime
    window: Window
    llm_spans: Sequence[LlmSpanRow]
    tool_spans: Sequence[ToolSpanRow]
    baselines: Baselines
    org_provider_errors: Sequence[OrgProviderRow]
    truncated: bool = False
    # The status-page incidents of the providers in `org_provider_errors` that overlap their
    # 30-minute window (with `DETECTOR_GRACE`). They only explain a finding, never open one.
    provider_incidents: Sequence[ProviderIncidentRow] = ()

    def __post_init__(self) -> None:
        # Tuples, so a detector cannot mutate lists shared by the other detectors.
        object.__setattr__(self, "llm_spans", tuple(self.llm_spans))
        object.__setattr__(self, "tool_spans", tuple(self.tool_spans))
        object.__setattr__(self, "org_provider_errors", tuple(self.org_provider_errors))
        object.__setattr__(self, "provider_incidents", tuple(self.provider_incidents))

    def sliced(self, window: Window) -> "DetectorContext":
        """The same context with both span lists cut to `window` (`start <= started_at < end`)."""
        return replace(
            self,
            window=window,
            llm_spans=tuple(s for s in self.llm_spans if window.contains(s.started_at)),
            tool_spans=tuple(s for s in self.tool_spans if window.contains(s.started_at)),
        )
