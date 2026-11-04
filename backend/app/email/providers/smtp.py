"""The SMTP provider: submit each message to a mail server over `smtplib`.

`smtplib` blocks, so a send runs in a worker thread. The connection is opened per message:
sends are rare, and a connection kept open would need its own health checks.
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from email.utils import getaddresses

from pydantic import SecretStr

from app.core.thread_ownership import owned_thread
from app.email.message import EmailDeliveryError, EmailMessage

TIMEOUT_SECONDS = 10.0
# The SMTPS port: the connection is TLS from the start instead of upgraded with STARTTLS.
IMPLICIT_TLS_PORT = 465


class SmtpEmailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: SecretStr | None = None,
        starttls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._from_address = from_address
        self._username = username
        self._password = password
        self._starttls = starttls

    async def send(self, message: EmailMessage) -> None:
        mime, recipient = self._build(message)
        try:
            await owned_thread(asyncio.to_thread(self._submit, mime, recipient))
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError(self._describe(exc)) from exc

    def _build(self, message: EmailMessage) -> tuple[MimeMessage, str]:
        """The MIME message and its one validated recipient address."""
        # A line break in a header value would let a subject or address smuggle in headers such
        # as Bcc. `email.message` refuses them too; checking first gives the outbox a clear error.
        extra = dict(message.headers or {})
        for name, value in (("to", message.to), ("subject", message.subject), *extra.items()):
            if "\r" in value or "\n" in value:
                raise EmailDeliveryError(f"email '{name}' must not contain a line break")
        # Exactly one mailbox: "a@x.com, b@y.com" would otherwise address both recipients.
        mailboxes = [address for _, address in getaddresses([message.to]) if address]
        if len(mailboxes) != 1:
            raise EmailDeliveryError("email 'to' must be exactly one address")
        mime = MimeMessage()
        mime["From"] = self._from_address
        mime["To"] = message.to
        mime["Subject"] = message.subject
        for name, value in extra.items():
            mime[name] = value
        mime.set_content(message.text)
        if message.html is not None:
            mime.add_alternative(message.html, subtype="html")
        return mime, mailboxes[0]

    def _submit(self, mime: MimeMessage, recipient: str) -> None:
        with self._connect() as connection:
            if self._starttls and not self._implicit_tls:
                # An explicit context: smtplib's default one does not check the certificate.
                connection.starttls(context=ssl.create_default_context())
            if self._username is not None:
                password = self._password.get_secret_value() if self._password else ""
                connection.login(self._username, password)
            # The envelope recipient is the validated address, not whatever the headers parse to.
            connection.send_message(mime, to_addrs=[recipient])

    @property
    def _implicit_tls(self) -> bool:
        return self._port == IMPLICIT_TLS_PORT

    def _connect(self) -> smtplib.SMTP:
        # Port 465 (SMTPS) speaks TLS from the first byte; some networks block 587 and leave it
        # as the only way out. The certificate is checked the same way as with STARTTLS.
        if self._implicit_tls:
            return smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(self._host, self._port, timeout=TIMEOUT_SECONDS)

    def _describe(self, exc: Exception) -> str:
        # The class and, when the server gave one, its numeric code. The server's reply text is
        # left out: it can quote the address or the credentials that were refused.
        code = getattr(exc, "smtp_code", None)
        detail = f"{type(exc).__name__} {code}" if isinstance(code, int) else type(exc).__name__
        return f"SMTP delivery to {self._host}:{self._port} failed: {detail}"
