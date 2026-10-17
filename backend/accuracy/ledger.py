"""Hard spend cap for the pilot harnesses.

Every paid Anthropic call the harness makes goes through `Ledger.guard()`. Before the call it
reserves the worst case the request could cost (estimated prompt priced at the 1-hour
cache-write rate, which is the highest input rate, plus `max_tokens` at the output rate). It
refuses the call when spent + open reservations + that worst case would pass the cap. After the
call it settles the reservation with the cost of the usage the provider returned; a call that
returned no usage (error, cut stream) settles at the worst case, so the ledger never
under-counts.

The ledger is a JSON file guarded by an exclusive file lock, so parallel scenario processes share
one cap. It lives beside the harness state (`accuracy.paths.harness_paths().ledger`), outside the
repository. Prices are Anthropic's published rates, read 2026-10-14 from
https://platform.claude.com/docs/en/about-claude/pricing, kept here on purpose instead of being
imported from Spanlight: the pilot checks Spanlight's arithmetic against them.
"""

from __future__ import annotations

import fcntl
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from accuracy.paths import harness_paths

CAP_USD = Decimal("15.00")
# Stop new work here; the gap covers anything the provider bills that no response reported.
SOFT_CAP_USD = Decimal("14.25")

MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class Rates:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal


def _r(i: str, o: str, cr: str, w5: str, w1: str) -> Rates:
    return Rates(Decimal(i), Decimal(o), Decimal(cr), Decimal(w5), Decimal(w1))


# Haiku 5.5 is priced by prompt length: over 100,000 prompt tokens (input + cache reads + cache
# writes) every class uses the higher row.
HAIKU_55_THRESHOLD = 100_000
RATES: dict[str, Rates] = {
    "claude-haiku-4-5": _r("1.00", "5.00", "0.10", "1.25", "2.00"),
    "claude-sonnet-4-5": _r("3.00", "15.00", "0.30", "3.75", "6.00"),
    "claude-sonnet-4-6": _r("3.00", "15.00", "0.30", "3.75", "6.00"),
    "claude-haiku-5-5": _r("0.10", "0.50", "0.01", "0.125", "0.20"),
    "claude-haiku-5-5>100k": _r("0.50", "2.50", "0.05", "0.625", "1.00"),
}


def base_model(model: str) -> str:
    """`claude-haiku-4-5-20251001` → `claude-haiku-4-5`; unknown models are refused."""
    for name in RATES:
        if ">" not in name and (model == name or model.startswith(name + "-")):
            return name
    raise ValueError(f"no published rate for {model!r}; refusing to spend on it")


def rates_for(model: str, prompt_tokens: int) -> Rates:
    name = base_model(model)
    if name == "claude-haiku-5-5" and prompt_tokens > HAIKU_55_THRESHOLD:
        return RATES["claude-haiku-5-5>100k"]
    return RATES[name]


@dataclass(frozen=True)
class Usage:
    """Anthropic usage, non-overlapping classes as the Messages API reports them."""

    input_tokens: int = 0  # uncached input only
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )

    @classmethod
    def from_anthropic(cls, usage: Any) -> Usage:
        """Read an Anthropic `usage` object or dict, including the per-TTL cache-write split."""

        def get(obj: Any, key: str) -> Any:
            return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

        creation = get(usage, "cache_creation")
        write_5m = get(creation, "ephemeral_5m_input_tokens") if creation is not None else None
        write_1h = get(creation, "ephemeral_1h_input_tokens") if creation is not None else None
        total_write = get(usage, "cache_creation_input_tokens") or 0
        if write_5m is None and write_1h is None:
            write_5m, write_1h = total_write, 0  # no split reported: 5m is the API default TTL
        return cls(
            input_tokens=get(usage, "input_tokens") or 0,
            output_tokens=get(usage, "output_tokens") or 0,
            cache_read_input_tokens=get(usage, "cache_read_input_tokens") or 0,
            cache_write_5m_tokens=write_5m or 0,
            cache_write_1h_tokens=write_1h or 0,
        )


def cost(model: str, usage: Usage) -> Decimal:
    """Published-rate cost of one request, before discounts, tax and data-residency multipliers."""
    r = rates_for(model, usage.prompt_tokens)
    total = (
        usage.input_tokens * r.input
        + usage.output_tokens * r.output
        + usage.cache_read_input_tokens * r.cache_read
        + usage.cache_write_5m_tokens * r.cache_write_5m
        + usage.cache_write_1h_tokens * r.cache_write_1h
    )
    return total / MTOK


def worst_case(model: str, est_prompt_tokens: int, max_tokens: int) -> Decimal:
    """Upper bound for one request: the whole prompt at the highest input rate, all output used."""
    # Pad the estimate 20% and price above any threshold the padding could cross.
    padded = int(est_prompt_tokens * 1.2) + 50
    r = rates_for(model, max(padded, HAIKU_55_THRESHOLD + 1))
    return (padded * r.cache_write_1h + max_tokens * r.output) / MTOK


class BudgetExceededError(RuntimeError):
    """The call would pass the soft cap; it was not made."""


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else harness_paths().ledger

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        with self.path.open("r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            raw = fh.read()
            state = json.loads(raw) if raw.strip() else {"open": {}, "calls": []}
            yield state
            fh.seek(0)
            fh.truncate()
            json.dump(state, fh, indent=1)
            fh.flush()

    @staticmethod
    def _spent(state: dict[str, Any]) -> Decimal:
        return sum((Decimal(c["cost_usd"]) for c in state["calls"]), Decimal(0))

    @staticmethod
    def _reserved(state: dict[str, Any]) -> Decimal:
        return sum((Decimal(v["worst_usd"]) for v in state["open"].values()), Decimal(0))

    def totals(self) -> dict[str, str]:
        with self._locked() as state:
            return {
                "spent_usd": str(self._spent(state)),
                "reserved_usd": str(self._reserved(state)),
                "calls": str(len(state["calls"])),
            }

    def reserve(self, *, scenario: str, model: str, est_prompt_tokens: int, max_tokens: int) -> str:
        worst = worst_case(model, est_prompt_tokens, max_tokens)
        with self._locked() as state:
            committed = self._spent(state) + self._reserved(state)
            if committed + worst > SOFT_CAP_USD:
                raise BudgetExceededError(
                    f"refused {scenario}: committed ${committed:.4f} + worst ${worst:.4f} "
                    f"> soft cap ${SOFT_CAP_USD}"
                )
            rid = uuid.uuid4().hex
            state["open"][rid] = {
                "scenario": scenario,
                "model": model,
                "worst_usd": str(worst),
                "at": time.time(),
            }
        return rid

    def settle(
        self, rid: str, *, usage: Usage | None, path: str, note: str = "", **extra: Any
    ) -> Decimal:
        with self._locked() as state:
            opened = state["open"].pop(rid)
            model = opened["model"]
            charged = cost(model, usage) if usage is not None else Decimal(opened["worst_usd"])
            state["calls"].append(
                {
                    "scenario": opened["scenario"],
                    "model": model,
                    "path": path,
                    "usage": None if usage is None else usage.__dict__,
                    "cost_usd": str(charged),
                    "usage_known": usage is not None,
                    "note": note,
                    "at": time.time(),
                    **extra,
                }
            )
        return charged

    @contextmanager
    def guard(
        self, *, scenario: str, model: str, est_prompt_tokens: int, max_tokens: int, path: str
    ) -> Iterator[dict[str, Any]]:
        """Reserve, run the block, settle. The block sets `slot["usage"]` (and optional extras).

        If the block raises before setting usage, the call settles at the worst case.
        """
        rid = self.reserve(
            scenario=scenario,
            model=model,
            est_prompt_tokens=est_prompt_tokens,
            max_tokens=max_tokens,
        )
        slot: dict[str, Any] = {"usage": None, "extra": {}}
        try:
            yield slot
        except BaseException as exc:
            self.settle(
                rid,
                usage=slot["usage"],
                path=path,
                note=f"raised {type(exc).__name__}",
                **slot["extra"],
            )
            raise
        self.settle(rid, usage=slot["usage"], path=path, **slot["extra"])
