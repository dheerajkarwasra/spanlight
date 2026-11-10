"""The contract every detector follows.

A detector is a pure function over a loaded context: it reads the rows of its window and
returns findings. The engine owns everything else (queries, the clock, persistence,
notification), which is why a detector can be tested with a handful of fixture rows.

Rules every detector follows:

- Pure over its sliced context: no queries, no clock (use `ctx.now`), no LLM, no randomness.
  Below `min_samples` it returns `[]`.
- Fault exclusion. Spans with `spanlight.fault.scenario` set are dropped by detectors that judge
  traffic or provider health (`error_spike`, `latency_regression`, `cost_spike`,
  `rate_limit_pressure`, `provider_incident`, `unpriced_spend`) before counting, `min_samples`
  included. Detectors that judge client behaviour use them.
- Cache hits (`gateway.cache == "hit"`) are excluded from `retry_storm`, `cache_opportunity`
  and `unpriced_spend`.
- Evidence trace ids: up to 20, the most recent first, always ones that contributed to the
  finding.
- Decimal metrics are rounded half-up: rates and shares to 4 places, milliseconds and seconds
  to 1, money to 6 (see `app.insights.format`).
- A detector writes no copy. `Finding.params` holds the formatted values the catalogue
  templates need, and `Finding.severity` follows the thresholds of its kind.
"""

from datetime import timedelta
from typing import Protocol

from app.insights.context import DetectorContext
from app.insights.schemas import Finding


class Detector(Protocol):
    """A module exposes one instance as `detector`; its `kind` has an entry in `KIND_INFO`.

    The three attributes are read-only members, so a plain attribute, a `ClassVar` or a frozen
    dataclass field all satisfy the protocol.
    """

    @property
    def kind(self) -> str: ...

    @property
    def window(self) -> timedelta: ...

    @property
    def min_samples(self) -> int: ...

    def run(self, ctx: DetectorContext) -> list[Finding]: ...
