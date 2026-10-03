"""SMTP delivery adapter for transactional server mail."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    use_tls: bool = True
    timeout: float = 10.0

    @classmethod
    def from_environment(cls) -> "EmailSettings":
        host = os.environ.get("SMAS_SMTP_HOST", "").strip()
        sender = os.environ.get("SMAS_EMAIL_FROM", "").strip()
        if not host:
            raise ValueError("SMAS_SMTP_HOST is required")
        if not sender:
            raise ValueError("SMAS_EMAIL_FROM is required")
        try:
            port = int(os.environ.get("SMAS_SMTP_PORT", "587"))
            timeout = float(os.environ.get("SMAS_SMTP_TIMEOUT", "10"))
        except ValueError as exc:
            raise ValueError("SMTP port and timeout must be numeric") from exc
        if not 1 <= port <= 65535 or timeout <= 0:
            raise ValueError("SMTP port or timeout is out of range")
        use_tls = os.environ.get("SMAS_SMTP_USE_TLS", "1").strip().lower() not in {"0", "false", "no", "off"}
        return cls(host, port, os.environ.get("SMAS_SMTP_USERNAME", "").strip(),
                   os.environ.get("SMAS_SMTP_PASSWORD", ""), sender, use_tls, timeout)


SMTPFactory = Callable[..., smtplib.SMTP]


class EmailSender:
    def __init__(self, settings: EmailSettings, *, smtp_factory: SMTPFactory | None = None) -> None:
        self.settings = settings
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def send(self, *, to: str, subject: str, body: str) -> None:
        recipient = to.strip()
        if not recipient:
            raise ValueError("recipient is required")
        if not subject.strip():
            raise ValueError("subject is required")
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with self._smtp_factory(self.settings.host, self.settings.port, timeout=self.settings.timeout) as smtp:
            if self.settings.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.settings.username:
                smtp.login(self.settings.username, self.settings.password)
            smtp.send_message(message)


def build_email_sender() -> EmailSender:
    return EmailSender(EmailSettings.from_environment())
