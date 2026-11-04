"""Delivers `email` outbox rows through an `EmailSender`."""

from typing import Any

from app.email.message import EmailMessage
from app.email.sender import EmailSender
from app.notifications.registry import PermanentDeliveryError


class EmailDeliverer:
    """The outbox `Deliverer` for the `email` kind.

    A row's target is `{"to": address}` and its payload `{"subject", "text", "html", "headers"}`,
    with `html` and `headers` (a map of extra header fields) optional. A sender's failure
    propagates, which is how the outbox learns to retry. A row that cannot be turned into a
    message (no `text`, because it was never rendered) fails permanently: no attempt can mend it.
    """

    def __init__(self, sender: EmailSender) -> None:
        self._sender = sender

    async def deliver(self, target: dict[str, Any], payload: dict[str, Any]) -> None:
        await self._sender.send(
            EmailMessage(
                to=_required_text(target, "to"),
                subject=_required_text(payload, "subject"),
                text=_required_text(payload, "text"),
                html=_optional_text(payload, "html"),
                headers=_optional_headers(payload),
            )
        )


# The errors below name the field and never quote the value, which can be an address or the
# body of the email.


def _required_text(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise PermanentDeliveryError(f"email notification needs a non-empty text '{key}'")
    return value


def _optional_text(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise PermanentDeliveryError(f"email notification '{key}' must be text")
    return value


def _optional_headers(payload: dict[str, Any]) -> dict[str, str] | None:
    headers = payload.get("headers")
    if headers is None:
        return None
    # A line break in a name or value would let a row smuggle in a header such as Bcc.
    if not isinstance(headers, dict) or not all(
        isinstance(name, str)
        and isinstance(value, str)
        and name
        and not any(c in name + value for c in "\r\n")
        for name, value in headers.items()
    ):
        raise PermanentDeliveryError("email notification 'headers' must map names to one-line text")
    return headers
