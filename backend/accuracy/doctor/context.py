"""What a scenario's run or baseline function works with: the run context, its result, the
token calibration and two small concurrency helpers."""

import secrets
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from accuracy.doctor.clients import direct_client, gateway_client
from accuracy.setup import HOST

# fmt: off
_WORDS = (
    "ledger", "entry", "balance", "invoice", "shipment", "warehouse", "customer", "order",
    "region", "quarter", "forecast", "margin", "supplier", "contract", "renewal", "audit",
    "variance", "inventory", "backlog", "pipeline", "revenue", "cohort", "policy", "claim",
    "premium", "adjuster", "appraisal", "district", "branch", "courier", "manifest", "pallet",
    "dock",
)
# fmt: on


def filler(words: int, seed: str = "") -> str:
    """Deterministic, readable filler of `words` words; `seed` varies the text."""
    base = sum(ord(c) for c in seed) if seed else 0
    chunks = []
    for i in range(words):
        word = _WORDS[(i * 7 + base) % len(_WORDS)]
        if i % 13 == 12:
            word += "."
        chunks.append(word)
    return " ".join(chunks)


class TokenCalibration:
    """Words per token for one model, measured once with the free `count_tokens` endpoint."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._ratio: dict[str, float] = {}
        self._lock = threading.Lock()

    def ratio(self, model: str) -> float:
        with self._lock:
            if model not in self._ratio:
                sample = filler(2000, "calibration")
                counted = self.client.messages.count_tokens(
                    model=model, messages=[{"role": "user", "content": sample}]
                )
                self._ratio[model] = 2000 / max(int(counted.input_tokens), 1)
            return self._ratio[model]

    def text(self, model: str, tokens: int, seed: str = "") -> str:
        """About `tokens` tokens of filler for `model` (within a few percent)."""
        words = max(int(tokens * self.ratio(model)), 1)
        return f"[{seed}] {filler(words, seed)}" if seed else filler(words, seed)


SEEDED_INCIDENT_TITLE = "seeded_incident_title"
"""The run fact a scenario that seeds a status-page incident records its title under."""


@dataclass
class RunResult:
    """What one scenario run produced: the traces it created, timing, and facts the collect
    step checks the scenario's expectation against (such as a seeded incident's title)."""

    trace_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    calls: int = 0
    failures: int = 0


@dataclass
class RunContext:
    """Everything a scenario's run or baseline function may use."""

    scenario_id: str
    environment: str
    projects: dict[str, dict[str, Any]]  # project name -> state entry
    models: Mapping[str, str]  # requested model -> the name the provider answers with
    result: RunResult
    host: str = HOST
    nonce: str = field(default_factory=lambda: secrets.token_hex(4))
    """Per-run nonce. Prompt caches are shared across projects on one Anthropic key, so every
    repeated or cacheable text starts with it; a run never hits a cache another run warmed."""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seen: set[str] = field(default_factory=set, repr=False)
    _direct: Any = field(default=None, repr=False)
    _calibration: TokenCalibration | None = field(default=None, repr=False)

    @property
    def project(self) -> dict[str, Any]:
        """The scenario's first (usually only) project entry."""
        return next(iter(self.projects.values()))

    def model(self, requested: str) -> str:
        return self.models.get(requested, requested)

    def record(self, trace_id: str | None) -> None:
        """Remember a trace this run created (once, in first-seen order)."""
        if trace_id:
            with self._lock:
                if trace_id not in self._seen:
                    self._seen.add(trace_id)
                    self.result.trace_ids.append(trace_id)

    def record_spans(self, spans: Iterable[Mapping[str, Any]]) -> None:
        for span in spans:
            self.record(str(span.get("trace_id") or ""))

    def note(self, text: str) -> None:
        with self._lock:
            self.result.notes.append(text)

    def fact(self, name: str, value: str) -> None:
        with self._lock:
            self.result.facts[name] = value

    def count(self, *, failed: bool = False) -> None:
        with self._lock:
            self.result.calls += 1
            if failed:
                self.result.failures += 1

    def direct(self, timeout: float = 120.0) -> Any:
        if timeout != 120.0:
            return direct_client(timeout)
        if self._direct is None:
            self._direct = direct_client()
        return self._direct

    def calibration(self) -> TokenCalibration:
        if self._calibration is None:
            self._calibration = TokenCalibration(self.direct())
        return self._calibration

    def sdk(self) -> Any:
        """Initialise the Spanlight SDK for this scenario's project and environment."""
        import spanlight  # noqa: PLC0415 - the SDK is installed only for live runs

        entry = self.project
        spanlight.init(
            api_key=entry["ingest_key"],
            host=self.host,
            environment=self.environment,
            flush_interval=1.0,
            batch_size=50,
        )
        return spanlight

    def gateway(self, *, session_id: str | None = None, timeout: float = 120.0) -> Any:
        return gateway_client(
            self.project["gateway_key"],
            session_id=session_id,
            tags=(f"doctor:{self.scenario_id}",),
            timeout=timeout,
            host=self.host,
        )


def parallel[T](fn: Callable[[T], Any], items: Iterable[T], workers: int = 6) -> list[Any]:
    """Run `fn` over `items` on a small thread pool, keeping the input order."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def flush_sdk(timeout: float = 60.0) -> None:
    import spanlight  # noqa: PLC0415 - the SDK is installed only for live runs

    spanlight.flush(timeout=timeout)
