"""The Resend provider: one authenticated POST to the Resend HTTP API per message."""

import re

import httpx
from pydantic import SecretStr

from app.email.message import EmailDeliveryError, EmailMessage

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0

# Resend names its errors in snake case (`validation_error`, `rate_limit_exceeded`). Only a
# value of that shape is kept; the free-text `message` next to it can quote the request.
_ERROR_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ResendEmailSender:
    def __init__(
        self,
        api_key: SecretStr,
        from_address: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._from_address = from_address
        self._transport = transport  # tests pass httpx.MockTransport; production uses the default

    async def send(self, message: EmailMessage) -> None:
        body: dict[str, object] = {
            "from": self._from_address,
            "to": message.to,
            "subject": message.subject,
            "text": message.text,
        }
        if message.html is not None:
            body["html"] = message.html
        if message.headers:
            body["headers"] = dict(message.headers)
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(RESEND_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            # The exception's own text is left out; it is not ours to vouch for.
            raise EmailDeliveryError(f"Resend request failed: {type(exc).__name__}") from exc

        if not response.is_success:
            raise EmailDeliveryError(_describe_rejection(response))


def _describe_rejection(response: httpx.Response) -> str:
    text = f"Resend rejected the message with status {response.status_code}"
    name = _error_name(response)
    return f"{text} ({name})" if name else text


def _error_name(response: httpx.Response) -> str | None:
    try:
        reply = response.json()
    except ValueError:
        return None
    name = reply.get("name") if isinstance(reply, dict) else None
    return name if isinstance(name, str) and _ERROR_NAME.fullmatch(name) else None
