"""Mail backends: `resend` (HTTP API via httpx), `file` (dev outbox), `memory` (tests).

None of them logs the recipient, the subject's link or the body. `MailSendError` messages
carry the HTTP status only.
"""

from __future__ import annotations

import asyncio
import html
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

import httpx
import structlog

from app.config import Settings

RESEND_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10.0
LOGIN_SUBJECT = "Dein Anmeldelink für belegbot"

logger = structlog.stdlib.get_logger("app.auth.mail")


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str


class MailSendError(RuntimeError):
    """Sending failed. The message holds the HTTP status at most (no address, key or body)."""


class MailBackend(Protocol):
    async def send(self, mail: Mail) -> None: ...


def login_mail(to: str, link: str, ttl_minutes: int) -> Mail:
    """The German login mail: plain text + simple HTML, no images, no tracking."""
    text = (
        "Hallo,\n\n"
        "hier ist dein Anmeldelink für belegbot:\n\n"
        f"{link}\n\n"
        f"Der Link ist {ttl_minutes} Minuten gültig, nur einmal verwendbar.\n"
        "Öffne den Link am besten direkt im Browser.\n\n"
        "Falls du das nicht angefordert hast, ignoriere diese E-Mail.\n"
    )
    safe_link = html.escape(link, quote=True)
    body = (
        '<!doctype html><html lang="de"><body style="font-family:sans-serif">'
        "<p>Hallo,</p>"
        "<p>hier ist dein Anmeldelink für belegbot:</p>"
        f'<p><a href="{safe_link}">Bei belegbot anmelden</a></p>'
        f"<p>Der Link ist {ttl_minutes} Minuten gültig, nur einmal verwendbar.<br>"
        "Öffne den Link am besten direkt im Browser.</p>"
        f'<p style="font-size:small">Falls der Knopf nicht geht: {safe_link}</p>'
        "<p>Falls du das nicht angefordert hast, ignoriere diese E-Mail.</p>"
        "</body></html>"
    )
    return Mail(to=to, subject=LOGIN_SUBJECT, text=text, html=body)


class MemoryBackend:
    """Keeps mails in `outbox` (tests)."""

    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        self.outbox.append(mail)


class FileOutboxBackend:
    """Writes each mail as an `.eml` file (mode 0600) into `directory` (local dev only)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _write(self, mail: Mail) -> Path:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        msg = EmailMessage()
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        # 8bit (not quoted-printable), so the link in the file can be copied as is.
        msg.set_content(mail.text, cte="8bit")
        msg.add_alternative(mail.html, subtype="html", cte="8bit")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.directory / f"{stamp}-{uuid.uuid4().hex[:8]}.eml"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(msg.as_bytes())
        os.chmod(path, 0o600)  # independent of the umask
        return path

    async def send(self, mail: Mail) -> None:
        await asyncio.to_thread(self._write, mail)
        logger.info("mail written to dev outbox")


class ResendBackend:
    """`POST https://api.resend.com/emails` with a bearer key (no SDK)."""

    def __init__(
        self,
        api_key: str,
        from_email: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._from = from_email
        self._transport = transport

    def __repr__(self) -> str:  # never show the key
        return "<ResendBackend>"

    async def send(self, mail: Mail) -> None:
        async with httpx.AsyncClient(
            transport=self._transport, timeout=RESEND_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "from": self._from,
                    "to": [mail.to],
                    "subject": mail.subject,
                    "text": mail.text,
                    "html": mail.html,
                },
            )
        if not response.is_success:
            raise MailSendError(f"resend returned HTTP {response.status_code}")


def backend_from_settings(settings: Settings) -> MailBackend:
    kind = settings.resolved_mail_backend
    if kind == "memory":
        return MemoryBackend()
    if kind == "file":
        return FileOutboxBackend(settings.dev_mail_dir)
    if settings.resend_api_key is None or not settings.resend_from_email:
        # Production refuses to start without them (check_api_settings); elsewhere, sending
        # fails and is logged as auth.mail.failed.
        return _UnconfiguredBackend()
    return ResendBackend(settings.resend_api_key.get_secret_value(), settings.resend_from_email)


class _UnconfiguredBackend:
    async def send(self, mail: Mail) -> None:
        raise MailSendError("resend is not configured")


class MailDispatcher:
    """Sends mails in background tasks, after the HTTP response (no timing signal).

    Failures are logged as `auth.mail.failed error_kind=<class>`; the caller already answered.
    `drain()` waits for pending sends (tests, shutdown).
    """

    def __init__(self, backend: MailBackend) -> None:
        self.backend = backend
        self._tasks: set[asyncio.Task[None]] = set()

    def dispatch(self, mail: Mail) -> None:
        task = asyncio.create_task(self._send(mail))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, mail: Mail) -> None:
        try:
            await self.backend.send(mail)
        except Exception as exc:  # the user already got 202; log the class only
            logger.warning("auth.mail.failed", error_kind=type(exc).__name__)

    async def drain(self, timeout: float | None = None) -> None:
        pending = list(self._tasks)
        if pending:
            await asyncio.wait(pending, timeout=timeout)
