"""Extra email headers (the digest's RFC 8058 `List-Unsubscribe` pair) survive the outbox.

A row is stored as JSON and turned back into an `EmailMessage` when it is sent, possibly on a
retry; the headers must come out exactly as they went in, and a row whose headers are not a map of
single-line text is refused by name rather than sent.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.email.deliverer import EmailDeliverer
from app.email.message import EmailMessage
from app.notifications import NotificationKind, enqueue
from app.notifications.jobs import deliver_due
from app.notifications.registry import DelivererRegistry, PermanentDeliveryError
from tests.email.test_deliverer import PAYLOAD, TARGET, RecordingSender

SessionFactory = async_sessionmaker[AsyncSession]

HEADERS = {
    "List-Unsubscribe": "<https://app.example.com/api/v1/email/unsubscribe?token=abc.def>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}


async def test_headers_survive_the_outbox_round_trip(session_factory: SessionFactory) -> None:
    sender = RecordingSender()
    registry = DelivererRegistry()
    registry.register(NotificationKind.EMAIL, EmailDeliverer(sender))
    async with session_factory() as db:
        await enqueue(db, NotificationKind.EMAIL, TARGET, {**PAYLOAD, "headers": HEADERS})
        await db.commit()

    run = await deliver_due(session_factory, registry)

    assert run.sent == 1
    (message,) = sender.sent
    assert message.headers == HEADERS


async def test_a_row_without_headers_sends_none() -> None:
    sender = RecordingSender()

    await EmailDeliverer(sender).deliver(TARGET, PAYLOAD)

    assert sender.sent == [EmailMessage(to="ada@example.com", **PAYLOAD)]
    assert sender.sent[0].headers is None


@pytest.mark.parametrize(
    "headers",
    [
        "List-Unsubscribe: <x>",
        {"List-Unsubscribe": 1},
        {"List-Unsubscribe": "<x>\r\nBcc: eve@example.com"},
        {"Bad\nName": "x"},
        {"": "x"},
    ],
)
async def test_malformed_headers_are_refused_by_name(headers: Any) -> None:
    # Permanent, like every row that cannot become a message: no retry can mend it.
    with pytest.raises(PermanentDeliveryError, match="'headers'") as raised:
        await EmailDeliverer(RecordingSender()).deliver(TARGET, {**PAYLOAD, "headers": headers})

    assert "eve@example.com" not in str(raised.value)
