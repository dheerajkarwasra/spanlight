"""The adapter between outbox rows and the email sender, and its registration at worker startup."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import NotificationOutbox, NotificationStatus
from app.email.deliverer import EmailDeliverer
from app.email.message import EmailMessage
from app.email.providers.resend import ResendEmailSender
from app.notifications import NotificationKind, enqueue
from app.notifications.defaults import register_default_deliverers
from app.notifications.jobs import deliver_due
from app.notifications.registry import DelivererRegistry, PermanentDeliveryError

SessionFactory = async_sessionmaker[AsyncSession]

TARGET = {"to": "ada@example.com"}
PAYLOAD = {
    "subject": "Verify your email",
    "text": "Open the link.",
    "html": "<p>Open the link.</p>",
}


class RecordingSender:
    """A real `EmailSender` that keeps what it was given."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def test_deliverer_turns_an_outbox_row_into_a_message() -> None:
    sender = RecordingSender()

    await EmailDeliverer(sender).deliver(TARGET, PAYLOAD)

    assert sender.sent == [
        EmailMessage(
            to="ada@example.com",
            subject="Verify your email",
            text="Open the link.",
            html="<p>Open the link.</p>",
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [{"subject": "Hi", "text": "Hello"}, {"subject": "Hi", "text": "Hello", "html": None}],
    ids=["html absent", "html null"],
)
async def test_deliverer_sends_text_only_when_there_is_no_html(payload: dict[str, Any]) -> None:
    sender = RecordingSender()

    await EmailDeliverer(sender).deliver(TARGET, payload)

    assert sender.sent == [
        EmailMessage(to="ada@example.com", subject="Hi", text="Hello", html=None)
    ]


@pytest.mark.parametrize(
    ("target", "payload", "missing"),
    [
        ({}, PAYLOAD, "to"),
        ({"to": ""}, PAYLOAD, "to"),
        ({"to": 7}, PAYLOAD, "to"),
        (TARGET, {"text": "Hello"}, "subject"),
        (TARGET, {"subject": "Hi"}, "text"),
        (TARGET, {"subject": "Hi", "text": None}, "text"),
        (TARGET, {"subject": "Hi", "text": "Hello", "html": 5}, "html"),
    ],
)
async def test_deliverer_rejects_a_malformed_row_by_name(
    target: dict[str, Any], payload: dict[str, Any], missing: str
) -> None:
    """A row that cannot become a message never will: the failure is permanent, so an operator
    replay of a text-less email does not spend every attempt."""
    sender = RecordingSender()

    with pytest.raises(PermanentDeliveryError, match=f"'{missing}'"):
        await EmailDeliverer(sender).deliver(target, payload)

    assert sender.sent == []


FROM = "Spanlight <noreply@example.com>"


def configured_settings(provider: str, tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "console": {"email_console_file": tmp_path / "outbox.jsonl"},
        "resend": {"email_provider": "resend", "resend_api_key": "re_x", "email_from": FROM},
        "smtp": {"email_provider": "smtp", "smtp_host": "smtp.example.com", "email_from": FROM},
    }[provider]
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("provider", ["console", "resend", "smtp"])
def test_register_default_deliverers_registers_email_when_configured(
    provider: str, tmp_path: Path
) -> None:
    registry = DelivererRegistry()
    settings = configured_settings(provider, tmp_path)
    assert settings.is_email_configured

    register_default_deliverers(settings, registry)

    assert registry.kinds() == ["email"]
    assert isinstance(registry.get(NotificationKind.EMAIL), EmailDeliverer)


def test_register_default_deliverers_skips_email_when_not_configured() -> None:
    # Console without a file only logs; a row it "delivered" would be marked sent for nothing.
    registry = DelivererRegistry()
    settings = Settings(_env_file=None)
    assert not settings.is_email_configured

    register_default_deliverers(settings, registry)

    assert registry.kinds() == []
    assert registry.get(NotificationKind.EMAIL) is None


async def test_an_email_row_fails_instead_of_being_faked_when_email_is_not_configured(
    session_factory: SessionFactory,
) -> None:
    registry = DelivererRegistry()
    register_default_deliverers(Settings(_env_file=None), registry)
    async with session_factory() as db:
        await enqueue(db, NotificationKind.EMAIL, TARGET, PAYLOAD)
        await db.commit()

    run = await deliver_due(session_factory, registry)

    assert (run.sent, run.retried, run.failed) == (0, 0, 1)
    async with session_factory() as db:
        row = (await db.execute(select(NotificationOutbox))).scalar_one()
    assert row.status is NotificationStatus.FAILED
    assert row.last_error is not None and "no deliverer registered" in row.last_error
    assert row.sent_at is None
    # It will never be sent, so the body (and any link in it) is dropped; the subject stays.
    assert row.payload == {"subject": PAYLOAD["subject"]}


def test_importing_the_email_modules_registers_nothing() -> None:
    # A fresh interpreter, so what other tests registered cannot hide a registration at import.
    code = (
        "import app.email, app.email.deliverer, app.notifications.defaults\n"
        "from app.notifications.registry import DELIVERERS\n"
        "assert DELIVERERS.kinds() == [], DELIVERERS.kinds()\n"
    )

    result = subprocess.run(  # noqa: S603 - fixed arguments, no user input
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


async def test_outbox_row_is_delivered_to_the_console_sink(
    session_factory: SessionFactory, tmp_path: Path
) -> None:
    sink = tmp_path / "outbox.jsonl"
    registry = DelivererRegistry()
    register_default_deliverers(Settings(_env_file=None, email_console_file=sink), registry)
    async with session_factory() as db:
        await enqueue(db, NotificationKind.EMAIL, TARGET, PAYLOAD)
        await db.commit()

    run = await deliver_due(session_factory, registry)

    assert (run.sent, run.retried, run.failed) == (1, 0, 0)
    (line,) = sink.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["to"] == "ada@example.com"
    assert json.loads(line)["text"] == "Open the link."


async def test_a_failing_provider_leaves_the_row_pending_for_a_retry(
    session_factory: SessionFactory,
) -> None:
    key = "re_test_0123456789abcdef"
    sender = ResendEmailSender(
        SecretStr(key),
        "noreply@example.com",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, json={"name": "rate_limit_exceeded"})
        ),
    )
    registry = DelivererRegistry()
    registry.register(NotificationKind.EMAIL, EmailDeliverer(sender))
    async with session_factory() as db:
        await enqueue(db, NotificationKind.EMAIL, TARGET, PAYLOAD)
        await db.commit()

    run = await deliver_due(session_factory, registry)

    assert (run.sent, run.retried, run.failed) == (0, 1, 0)
    async with session_factory() as db:
        row = (await db.execute(select(NotificationOutbox))).scalar_one()
    assert row.status is NotificationStatus.PENDING
    assert row.attempts == 1
    assert row.last_error is not None
    assert "429" in row.last_error and "rate_limit_exceeded" in row.last_error
    assert key not in row.last_error
    assert row.payload == PAYLOAD  # the body is kept until a send succeeds
