"""Server-tool fees: what a provider bills per request for a built-in tool such as web search.

A fee is separate from token pricing. The fee book holds one row per provider, tool and
effective date; `FeeBook.find` picks the latest row in force at the span's start time, with the
span's provider and each row's folded through `canonical_provider` so a span sent as `claude`
finds the `anthropic` rate. `fee_cost` turns a request count into USD at the rate's per-1,000
price and quantizes it at eight decimals (`numeric(14, 8)`): that is the stored `fee_usd`.
`cost_usd` does not add two rounded figures: `PriceBook.cost` adds the unrounded fee to the
unrounded token cost and quantizes the sum once, as the cost explanation does.

A missing count or a missing rate gives `None`: unknown is never reported as zero. Zero requests
is a known zero fee.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.pricing.provider_alias import canonical_provider

# The one quantum for money in the price book (numeric(14, 8)); `app.pricing.cost` and
# `app.pricing.explain` import it.
FEE_QUANTUM = Decimal("0.00000001")

_PER_THOUSAND = Decimal(1000)


@dataclass(frozen=True)
class ServerToolFee:
    provider: str
    tool: str
    usd_per_1k_requests: Decimal
    effective_from: datetime
    version: str


class FeeBook:
    """An immutable set of server-tool fees loaded once per ingestion batch."""

    def __init__(self, fees: Iterable[ServerToolFee]) -> None:
        self._fees: Sequence[ServerToolFee] = tuple(fees)

    def find(self, provider: str | None, tool: str, at: datetime) -> ServerToolFee | None:
        """The fee for `tool` from `provider` in force at `at`, or None.

        Both providers are folded through `canonical_provider`; a missing provider finds nothing,
        because the same tool name is billed differently by each provider. Among the rows whose
        `effective_from` is not after `at`, the latest wins.
        """
        canonical = canonical_provider(provider)
        if canonical is None:
            return None
        candidates = [
            fee
            for fee in self._fees
            if canonical_provider(fee.provider) == canonical
            and fee.tool == tool
            and fee.effective_from <= at
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fee: fee.effective_from)


def fee_cost(requests: int | None, fee: ServerToolFee | None) -> Decimal | None:
    """`requests` billed at `fee`, in USD, quantized to eight decimals half-up.

    None when either the count or the rate is missing.
    """
    if requests is None or fee is None:
        return None
    total = requests * fee.usd_per_1k_requests / _PER_THOUSAND
    return total.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)
