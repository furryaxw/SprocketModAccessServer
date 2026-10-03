from __future__ import annotations

import time
import uuid

from .email import EmailSender
from ..database import SQLiteDatabase


class SQLiteEmailOutbox:
    def __init__(self, database: SQLiteDatabase, audit=None):
        self.database = database
        self.audit = audit
        self.database.initialize()

    def enqueue(self, *, recipient: str, subject: str, body: str, now: int | None = None) -> str | None:
        recipient = recipient.strip()
        if not recipient:
            return None
        timestamp = int(time.time()) if now is None or now <= 0 else now
        message_id = uuid.uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO email_outbox(message_id,recipient,subject,body,available_at,created_at) VALUES (?,?,?,?,?,?)",
                (message_id, recipient, subject, body, timestamp, timestamp),
            )
        if self.audit is not None:
            self.audit.record(actor="system", action="email.enqueue", target=message_id,
                              metadata={"recipient": recipient, "subject": subject}, now=timestamp)
        return message_id

    def deliver_once(self, sender: EmailSender, *, now: int | None = None, limit: int = 25) -> dict[str, int]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        sent = failed = 0
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM email_outbox WHERE status IN ('pending','failed') AND available_at <= ? ORDER BY created_at LIMIT ?",
                (timestamp, limit),
            ).fetchall()
        for row in rows:
            try:
                sender.send(to=row["recipient"], subject=row["subject"], body=row["body"])
            except Exception as exc:
                failed += 1
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE email_outbox SET status='failed', attempts=attempts+1, last_error=?, available_at=? WHERE message_id=?",
                        (str(exc)[:500], timestamp + min(3600, 2 ** min(int(row["attempts"]), 10)), row["message_id"]),
                    )
                if self.audit is not None:
                    self.audit.record(actor="system", action="email.failed", target=row["message_id"],
                                      metadata={"error": str(exc)[:200], "attempts": int(row["attempts"]) + 1},
                                      now=timestamp)
            else:
                sent += 1
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE email_outbox SET status='sent', attempts=attempts+1, sent_at=? WHERE message_id=?",
                        (timestamp, row["message_id"]),
                    )
                if self.audit is not None:
                    self.audit.record(actor="system", action="email.sent", target=row["message_id"],
                                      metadata={"recipient": row["recipient"]}, now=timestamp)
        return {"sent": sent, "failed": failed}
