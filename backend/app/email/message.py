"""What an email is, and how sending one fails."""

from collections.abc import Mapping
from dataclasses import dataclass

from app.notifications.registry import DeliveryError


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One email to one recipient. `html` is the optional rich alternative to `text`.

    `headers` are extra header fields, such as the weekly digest's `List-Unsubscribe` pair.
    """

    to: str
    subject: str
    text: str
    html: str | None = None
    headers: Mapping[str, str] | None = None


class EmailDeliveryError(DeliveryError):
    """A message could not be sent. A row that cannot become a message is not retried:
    `app.email.deliverer` raises `PermanentDeliveryError` for it.

    The outbox stores this text in `last_error` and logs it (it is a `DeliveryError`, so its
    message is kept), and retries the row. Providers therefore keep it short and never put the
    request body, a credential, an address or a provider's free-text reply in it.
    """
