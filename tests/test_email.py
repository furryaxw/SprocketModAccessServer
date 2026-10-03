from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sprocket_access_server.infrastructure.messaging.email import EmailSender, EmailSettings


class FakeSMTP:
    def __init__(self, host, port, *, timeout):
        self.args = (host, port, timeout)
        self.calls = []
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, *, context):
        self.calls.append("tls")

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.message = message
        self.calls.append("send")


class EmailTests(unittest.TestCase):
    def test_environment_requires_host_and_sender(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SMAS_SMTP_HOST"):
                EmailSettings.from_environment()

    def test_sender_uses_tls_auth_and_headers(self):
        smtp = FakeSMTP
        with patch.dict(os.environ, {"SMAS_SMTP_HOST": "smtp.test", "SMAS_EMAIL_FROM": "noreply@test"}, clear=True):
            settings = EmailSettings.from_environment()
        instance = None

        def factory(*args, **kwargs):
            nonlocal instance
            instance = smtp(*args, **kwargs)
            return instance

        EmailSender(settings, smtp_factory=factory).send(to="user@test", subject="Invite", body="Hello")
        self.assertEqual(instance.calls, ["tls", "send"])
        self.assertEqual(instance.message["From"], "noreply@test")
        self.assertEqual(instance.message["To"], "user@test")
        self.assertEqual(instance.message["Subject"], "Invite")

    def test_sender_can_skip_tls_and_authenticate(self):
        instance = None

        def factory(*args, **kwargs):
            nonlocal instance
            instance = FakeSMTP(*args, **kwargs)
            return instance

        settings = EmailSettings("smtp.test", sender="from@test", username="u", password="p", use_tls=False)
        EmailSender(settings, smtp_factory=factory).send(to="to@test", subject="Subject", body="Body")
        self.assertEqual(instance.calls, [("login", "u", "p"), "send"])


if __name__ == "__main__":
    unittest.main()
