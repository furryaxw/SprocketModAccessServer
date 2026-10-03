from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.messaging.email import EmailSettings
from sprocket_access_server.infrastructure.messaging.email_outbox import SQLiteEmailOutbox
from sprocket_access_server.modules.audit.store import SQLiteAuditStore


class Sender:
    def __init__(self, fail=False): self.fail, self.calls = fail, []
    def send(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail: raise RuntimeError("smtp down")


class EmailOutboxTests(unittest.TestCase):
    def test_enqueue_delivers_and_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "db.sqlite")
            audit = SQLiteAuditStore(database)
            outbox = SQLiteEmailOutbox(database, audit)
            outbox.enqueue(recipient="a@example.test", subject="Hi", body="Body", now=10)
            sender = Sender()
            self.assertEqual(outbox.deliver_once(sender, now=10), {"sent": 1, "failed": 0})
            self.assertEqual(len(sender.calls), 1)
            self.assertEqual(audit.recent(limit=2)[0]["action"], "email.sent")
            failed = Sender(fail=True)
            outbox.enqueue(recipient="b@example.test", subject="Hi", body="Body", now=10)
            self.assertEqual(outbox.deliver_once(failed, now=10), {"sent": 0, "failed": 1})
            self.assertEqual(outbox.deliver_once(Sender(), now=10), {"sent": 0, "failed": 0})


if __name__ == "__main__": unittest.main()
