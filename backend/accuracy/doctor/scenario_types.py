"""What a labelled scenario is: its detector, label, mode, cost, window and traffic."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from accuracy.doctor.context import RunContext

MODES = frozenset({"real", "lab", "synthetic", "mixed"})
"""Where a scenario's spans come from:

* `real`: Anthropic calls through the Python SDK (or the gateway without a fault profile);
* `lab`: Anthropic calls through the gateway with an Integration Lab fault profile attached
  (faults answered by the gateway itself, such as a 429 or a 5xx, cost nothing);
* `synthetic`: spans posted to `POST /v1/traces` with realistic payloads, used only where no
  real traffic can produce the condition (baselines, real 429 shares, an unknown model, an
  organization-wide 5xx storm);
* `mixed`: real traffic plus synthetic spans.
"""


@dataclass(frozen=True)
class Fault:
    """An Integration Lab fault profile attached to the scenario's gateway key."""

    scenario: str
    params: dict[str, Any]
    probability: float = 1.0


@dataclass(frozen=True)
class Expectation:
    """What a fired finding must also show, beyond firing, for the scenario to pass."""

    rate_limit_kind: str | None = None
    """Every failed llm span of the finding's evidence traces carries this `rate_limit_kind`."""
    incident_cited: bool = False
    """The finding cites the status-page incident the scenario seeded (`incident_id` set and
    the seeded title in `incident_title`)."""


@dataclass(frozen=True)
class Scenario:
    id: str
    detector: str
    label: str  # "pos" or "neg"
    variant: str  # what the case shows
    project: str
    mode: str  # real | lab | synthetic | mixed
    est_cost_usd: Decimal
    window: timedelta
    run: Callable[[RunContext], None]
    baseline: Callable[[RunContext], None] | None = None
    fault: Fault | None = None
    gateway: bool = False  # needs a route and a gateway key (with or without a fault)
    org: str = "main"  # "main" or "incident-<case>"
    extra_projects: tuple[str, ...] = ()
    key_rpm_limit: int | None = None  # the gateway key's requests-per-minute limit
    expect: Expectation | None = None

    @property
    def needs_baseline(self) -> bool:
        return self.baseline is not None

    @property
    def case(self) -> str:
        """`pos`, `neg-nearmiss`, ...: the project name after `dr-<detector>-`."""
        return self.project.removeprefix(f"dr-{self.detector}-")

    @property
    def environment(self) -> str:
        return f"lab-{self.detector}-{self.case}"

    @property
    def project_names(self) -> tuple[str, ...]:
        return (self.project, *self.extra_projects)


def scenario(
    detector: str,
    case: str,
    label: str,
    mode: str,
    cost: str,
    window: timedelta,
    variant: str,
    run: Callable[[RunContext], None],
    *,
    suffix: str = "",
    **kw: Any,
) -> Scenario:
    """One scenario; its project is `dr-<detector>-<case><suffix>`."""
    return Scenario(
        id=f"{detector}:{case}",
        detector=detector,
        label=label,
        variant=variant,
        project=f"dr-{detector}-{case}{suffix}",
        mode=mode,
        est_cost_usd=Decimal(cost),
        window=window,
        run=run,
        **kw,
    )
