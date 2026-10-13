"""The three email providers, each exercised against the real thing it talks to.

Resend runs over an in-process httpx `MockTransport` (no network, real request building), SMTP
against a real `aiosmtpd` server on localhost, and the console provider against a real file.
"""

import asyncio
import email.policy
import json
import socket
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage as MimeMessage
from pathlib import Path
from typing import Any

import httpx
import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, Envelope, LoginPassword
from pydantic import SecretStr
from structlog.testing import capture_logs

from app.config import Settings
from app.email.message import EmailDeliveryError, EmailMessage
from app.email.providers.console import ConsoleEmailSender
from app.email.providers.resend import ResendEmailSender
from app.email.providers.smtp import SmtpEmailSender
from app.email.sender import get_email_sender

TOKEN = "SECRET-TOKEN-0123456789"
MESSAGE = EmailMessage(
    to="ada@example.com",
    subject="Reset your Spanlight password",
    text=f"Open https://app.example.com/reset-password#token={TOKEN} to choose a new password.",
    html=f'<p><a href="https://app.example.com/reset-password#token={TOKEN}">Reset</a></p>',
)
TEXT_ONLY = EmailMessage(to="grace@example.com", subject="Welcome", text="Hello Grace", html=None)

# --- Resend -----------------------------------------------------------------------------------

API_KEY = "re_test_0123456789abcdef"
FROM = "Spanlight <noreply@example.com>"


def resend_sender(
    handler: Callable[[httpx.Request], httpx.Response],
) -> ResendEmailSender:
    return ResendEmailSender(SecretStr(API_KEY), FROM, transport=httpx.MockTransport(handler))


class ResendInbox:
    """Records every request and answers with a fixed response."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response or httpx.Response(200, json={"id": "email_1"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response


async def test_resend_posts_message_with_bearer_key() -> None:
    inbox = ResendInbox()

    await resend_sender(inbox).send(MESSAGE)

    (request,) = inbox.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "from": FROM,
        "to": "ada@example.com",
        "subject": MESSAGE.subject,
        "text": MESSAGE.text,
        "html": MESSAGE.html,
    }


async def test_resend_omits_html_when_there_is_none() -> None:
    inbox = ResendInbox()

    await resend_sender(inbox).send(TEXT_ONLY)

    assert "html" not in json.loads(inbox.requests[0].content)


async def test_resend_times_out_after_ten_seconds() -> None:
    inbox = ResendInbox()

    await resend_sender(inbox).send(MESSAGE)

    timeout = inbox.requests[0].extensions["timeout"]
    assert timeout == {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}


async def test_resend_non_2xx_raises_so_the_outbox_retries() -> None:
    body = {
        "statusCode": 422,
        "name": "validation_error",
        "message": f"The `to` field ada@example.com is invalid ({TOKEN})",
    }
    sender = resend_sender(ResendInbox(httpx.Response(422, json=body)))

    with pytest.raises(EmailDeliveryError) as raised:
        await sender.send(MESSAGE)

    text = str(raised.value)
    assert "422" in text and "validation_error" in text
    # The reply's free-text message can echo the request; the key and the body never appear.
    assert "ada@example.com" not in text and TOKEN not in text and API_KEY not in text


async def test_resend_error_never_contains_the_api_key() -> None:
    reply = httpx.Response(401, json={"name": "restricted_api_key", "message": API_KEY})

    with pytest.raises(EmailDeliveryError) as raised:
        await resend_sender(ResendInbox(reply)).send(MESSAGE)

    assert "401" in str(raised.value)
    assert API_KEY not in str(raised.value)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(502, text="<html>Bad gateway</html>"),
        httpx.Response(500, json=["not", "an", "object"]),
        httpx.Response(500, json={"name": "x" * 500}),
        httpx.Response(500, json={"name": "line one\nline two"}),
        httpx.Response(500, json={"name": 7}),
    ],
)
async def test_resend_keeps_only_a_short_clean_error_name(reply: httpx.Response) -> None:
    with pytest.raises(EmailDeliveryError) as raised:
        await resend_sender(ResendInbox(reply)).send(MESSAGE)

    text = str(raised.value)
    assert str(reply.status_code) in text
    assert len(text) < 120
    assert "\n" not in text


async def test_resend_network_failure_raises_without_the_key() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect with {API_KEY}", request=request)

    with pytest.raises(EmailDeliveryError) as raised:
        await resend_sender(refuse).send(MESSAGE)

    assert "ConnectError" in str(raised.value)
    assert API_KEY not in str(raised.value)


# --- SMTP -------------------------------------------------------------------------------------

LOGIN = "mailer"
PASSWORD = "s3cret-smtp-password"


@dataclass
class SmtpInbox:
    """What a local SMTP server received."""

    host: str
    port: int
    envelopes: list[Envelope] = field(default_factory=list)

    def parsed(self) -> list[MimeMessage]:
        messages = []
        for envelope in self.envelopes:
            assert isinstance(envelope.content, bytes)
            parsed = message_from_bytes(envelope.content, policy=email.policy.default)
            assert isinstance(parsed, MimeMessage)
            messages.append(parsed)
        return messages


class _Collector:
    def __init__(self, inbox: SmtpInbox) -> None:
        self._inbox = inbox

    async def handle_DATA(self, server: Any, session: Any, envelope: Envelope) -> str:  # noqa: N802
        self._inbox.envelopes.append(envelope)
        return "250 Message accepted"


def _authenticate(
    server: Any, session: Any, envelope: Any, mechanism: str, data: Any
) -> AuthResult:
    accepted = isinstance(data, LoginPassword) and (data.login, data.password) == (
        LOGIN.encode(),
        PASSWORD.encode(),
    )
    return AuthResult(success=accepted, handled=False)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(**options: Any) -> Iterator[SmtpInbox]:
    # aiosmtpd cannot report a port it picked itself, so reserve one first. STARTTLS is not
    # offered: the sender is told not to use it in these tests.
    inbox = SmtpInbox(host="127.0.0.1", port=free_port())
    controller = Controller(_Collector(inbox), hostname=inbox.host, port=inbox.port, **options)
    controller.start()
    try:
        yield inbox
    finally:
        controller.stop()


# aiosmtpd warns when a server offers AUTH without TLS. That is exactly the setup these tests
# need (a local server, no certificate), so the warning is allowed for the tests that log in,
# and only that one: every other warning still fails the test.
allow_auth_without_tls = pytest.mark.filterwarnings(
    "ignore:Requiring AUTH while not requiring TLS:UserWarning"
)


@pytest.fixture
def smtp_inbox() -> Iterator[SmtpInbox]:
    yield from _serve()


@pytest.fixture
def smtp_inbox_with_login() -> Iterator[SmtpInbox]:
    yield from _serve(authenticator=_authenticate, auth_required=True, auth_require_tls=False)


def smtp_sender(inbox: SmtpInbox, **options: Any) -> SmtpEmailSender:
    options.setdefault("starttls", False)
    return SmtpEmailSender(host=inbox.host, port=inbox.port, from_address=FROM, **options)


async def test_smtp_delivers_text_and_html_alternative(smtp_inbox: SmtpInbox) -> None:
    await smtp_sender(smtp_inbox).send(MESSAGE)

    (envelope,) = smtp_inbox.envelopes
    assert envelope.mail_from == "noreply@example.com"
    assert envelope.rcpt_tos == ["ada@example.com"]
    (received,) = smtp_inbox.parsed()
    assert received["From"] == FROM
    assert received["To"] == "ada@example.com"
    assert received["Subject"] == MESSAGE.subject
    assert received.is_multipart()
    text_part = received.get_body(preferencelist=("plain",))
    html_part = received.get_body(preferencelist=("html",))
    assert text_part is not None and text_part.get_content().strip() == MESSAGE.text
    assert html_part is not None and html_part.get_content().strip() == MESSAGE.html


async def test_smtp_text_only_message_is_not_multipart(smtp_inbox: SmtpInbox) -> None:
    await smtp_sender(smtp_inbox).send(TEXT_ONLY)

    (received,) = smtp_inbox.parsed()
    assert not received.is_multipart()
    assert received.get_content().strip() == "Hello Grace"


async def test_smtp_keeps_non_ascii_text_and_subject(smtp_inbox: SmtpInbox) -> None:
    message = EmailMessage(to="ada@example.com", subject="Grüße aus Zürich", text="Ünïcode ✓")

    await smtp_sender(smtp_inbox).send(message)

    (received,) = smtp_inbox.parsed()
    assert received["Subject"] == "Grüße aus Zürich"
    assert received.get_content().strip() == "Ünïcode ✓"


@allow_auth_without_tls
async def test_smtp_logs_in_when_a_username_is_set(smtp_inbox_with_login: SmtpInbox) -> None:
    sender = smtp_sender(smtp_inbox_with_login, username=LOGIN, password=SecretStr(PASSWORD))

    await sender.send(TEXT_ONLY)

    assert len(smtp_inbox_with_login.envelopes) == 1


@allow_auth_without_tls
async def test_smtp_rejected_login_raises_without_the_password(
    smtp_inbox_with_login: SmtpInbox,
) -> None:
    sender = smtp_sender(smtp_inbox_with_login, username=LOGIN, password=SecretStr("wrong"))

    with pytest.raises(EmailDeliveryError) as raised:
        await sender.send(TEXT_ONLY)

    assert "SMTPAuthenticationError" in str(raised.value) and "535" in str(raised.value)
    assert "wrong" not in str(raised.value) and PASSWORD not in str(raised.value)
    assert smtp_inbox_with_login.envelopes == []


@allow_auth_without_tls
async def test_smtp_does_not_log_in_without_a_username(smtp_inbox_with_login: SmtpInbox) -> None:
    # The server insists on authentication, so an anonymous send must fail instead of being
    # quietly accepted: the sender must not invent credentials.
    with pytest.raises(EmailDeliveryError):
        await smtp_sender(smtp_inbox_with_login).send(TEXT_ONLY)


async def test_smtp_requires_starttls_when_enabled(smtp_inbox: SmtpInbox) -> None:
    # Credentials and mail must not silently travel in clear text when the operator asked for
    # STARTTLS and the server cannot provide it.
    sender = smtp_sender(smtp_inbox, starttls=True)

    with pytest.raises(EmailDeliveryError) as raised:
        await sender.send(TEXT_ONLY)

    assert "SMTPNotSupportedError" in str(raised.value)
    assert smtp_inbox.envelopes == []


async def test_smtp_unreachable_server_raises() -> None:
    sender = SmtpEmailSender(host="127.0.0.1", port=free_port(), from_address=FROM, starttls=False)

    with pytest.raises(EmailDeliveryError) as raised:
        await sender.send(TEXT_ONLY)

    assert "127.0.0.1" in str(raised.value) and "ConnectionRefusedError" in str(raised.value)


async def test_smtp_refused_recipient_raises() -> None:
    class Refuse:
        async def handle_RCPT(  # noqa: N802
            self, server: Any, session: Any, envelope: Any, address: str, rcpt_options: Any
        ) -> str:
            return "550 5.1.1 No such user"

    controller = Controller(Refuse(), hostname="127.0.0.1", port=free_port())
    controller.start()
    try:
        sender = SmtpEmailSender(
            host="127.0.0.1", port=controller.port, from_address=FROM, starttls=False
        )
        with pytest.raises(EmailDeliveryError) as raised:
            await sender.send(TEXT_ONLY)
    finally:
        controller.stop()

    assert "SMTPRecipientsRefused" in str(raised.value)
    assert "grace@example.com" not in str(raised.value)


async def test_smtp_rejects_header_injection(smtp_inbox: SmtpInbox) -> None:
    message = EmailMessage(
        to="ada@example.com", subject="Hi\r\nBcc: eve@example.com", text="Hello", html=None
    )
    address = EmailMessage(to="ada@example.com\nBcc: eve@example.com", subject="Hi", text="Hello")

    for unsafe in (message, address):
        with pytest.raises(EmailDeliveryError, match="line break"):
            await smtp_sender(smtp_inbox).send(unsafe)

    assert smtp_inbox.envelopes == []


# --- console ----------------------------------------------------------------------------------


async def test_console_appends_one_json_line_per_message(tmp_path: Path) -> None:
    sink = tmp_path / "outbox.jsonl"
    sender = ConsoleEmailSender(sink)

    await sender.send(MESSAGE)
    await sender.send(TEXT_ONLY)

    lines = sink.read_text(encoding="utf-8").splitlines()
    first, second = (json.loads(line) for line in lines)
    assert {key: first[key] for key in ("to", "subject", "text", "html")} == {
        "to": "ada@example.com",
        "subject": MESSAGE.subject,
        "text": MESSAGE.text,
        "html": MESSAGE.html,
    }
    assert second["to"] == "grace@example.com" and second["html"] is None
    assert first["sent_at"].endswith("+00:00")


async def test_console_appends_to_an_existing_file(tmp_path: Path) -> None:
    sink = tmp_path / "outbox.jsonl"
    sink.write_text('{"earlier": true}\n', encoding="utf-8")

    await ConsoleEmailSender(sink).send(TEXT_ONLY)

    lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0]) == {"earlier": True}


async def test_console_concurrent_sends_do_not_interleave(tmp_path: Path) -> None:
    sink = tmp_path / "outbox.jsonl"
    sender = ConsoleEmailSender(sink)
    # Long lines, so that a write which is not kept whole would show up as a broken line.
    messages = [
        EmailMessage(to=f"user{n}@example.com", subject=f"n{n}", text=f"{n}:" + "x" * 300_000)
        for n in range(20)
    ]

    await asyncio.gather(*(sender.send(message) for message in messages))

    lines = sink.read_text(encoding="utf-8").splitlines()
    recipients = sorted(json.loads(line)["to"] for line in lines)  # every line parses on its own
    assert recipients == sorted(message.to for message in messages)


async def test_console_file_is_private_to_the_owner(tmp_path: Path) -> None:
    # The file holds verification and reset links, so it must not be world-readable.
    sink = tmp_path / "outbox.jsonl"

    await ConsoleEmailSender(sink).send(MESSAGE)

    assert stat.S_IMODE(sink.stat().st_mode) == 0o600


async def test_console_fails_loudly_when_the_directory_is_missing(tmp_path: Path) -> None:
    sender = ConsoleEmailSender(tmp_path / "missing" / "outbox.jsonl")

    with pytest.raises(FileNotFoundError):
        await sender.send(MESSAGE)


@pytest.mark.parametrize("with_file", [True, False])
async def test_console_provider_never_logs_body(tmp_path: Path, with_file: bool) -> None:
    sender = ConsoleEmailSender(tmp_path / "outbox.jsonl" if with_file else None)

    with capture_logs() as logs:
        await sender.send(MESSAGE)

    assert logs, "the console provider should log that it sent something"
    rendered = json.dumps(logs, default=str)
    assert MESSAGE.subject in rendered and MESSAGE.to in rendered
    assert TOKEN not in rendered and "reset-password" not in rendered


async def test_console_without_a_file_only_logs() -> None:
    with capture_logs() as logs:
        await ConsoleEmailSender(None).send(MESSAGE)

    assert [(entry["to"], entry["written"]) for entry in logs] == [(MESSAGE.to, False)]


# --- choosing a sender ------------------------------------------------------------------------


def build_settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


def test_get_email_sender_returns_the_configured_provider(tmp_path: Path) -> None:
    assert isinstance(get_email_sender(build_settings()), ConsoleEmailSender)
    assert isinstance(
        get_email_sender(build_settings(email_console_file=tmp_path / "sink.jsonl")),
        ConsoleEmailSender,
    )
    resend = build_settings(email_provider="resend", resend_api_key="re_x", email_from=FROM)
    assert isinstance(get_email_sender(resend), ResendEmailSender)
    smtp = build_settings(email_provider="smtp", smtp_host="localhost", email_from=FROM)
    assert isinstance(get_email_sender(smtp), SmtpEmailSender)


async def test_get_email_sender_passes_the_smtp_settings_through(smtp_inbox: SmtpInbox) -> None:
    settings = build_settings(
        email_provider="smtp",
        smtp_host=smtp_inbox.host,
        smtp_port=smtp_inbox.port,
        smtp_starttls=False,
        email_from=FROM,
    )

    await get_email_sender(settings).send(TEXT_ONLY)

    assert len(smtp_inbox.envelopes) == 1
