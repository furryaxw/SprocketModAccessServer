from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.messaging.email_outbox import SQLiteEmailOutbox
from sprocket_access_server.infrastructure.messaging.email_worker import run_email_worker


class Sender:
    def __init__(self):
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)


class EmailWorkerTests(unittest.TestCase):
    def test_worker_delivers_and_stops_cleanly(self) -> None:
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                outbox = SQLiteEmailOutbox(SQLiteDatabase(Path(directory) / "db.sqlite"))
                outbox.enqueue(recipient="a@example.test", subject="Hi", body="Body", now=1)
                sender = Sender()
                stop = asyncio.Event()
                task = asyncio.create_task(run_email_worker(outbox, sender, interval=0.01, stop=stop))
                for _ in range(100):
                    if sender.calls:
                        break
                    await asyncio.sleep(0.005)
                stop.set()
                await asyncio.wait_for(task, timeout=1)
                self.assertEqual(len(sender.calls), 1)

        asyncio.run(scenario())

    def test_worker_can_be_cancelled_during_poll_wait(self) -> None:
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                outbox = SQLiteEmailOutbox(SQLiteDatabase(Path(directory) / "db.sqlite"))
                task = asyncio.create_task(run_email_worker(outbox, Sender(), interval=60, stop=asyncio.Event()))
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(scenario())

    def test_worker_returns_cleanly_when_stop_event_is_set_during_wait(self) -> None:
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                outbox = SQLiteEmailOutbox(SQLiteDatabase(Path(directory) / "db.sqlite"))
                stop = asyncio.Event()
                task = asyncio.create_task(run_email_worker(outbox, Sender(), interval=60, stop=stop))
                await asyncio.sleep(0)
                stop.set()
                await asyncio.wait_for(task, timeout=1)

        asyncio.run(scenario())

    def test_worker_rejects_invalid_interval(self) -> None:
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                outbox = SQLiteEmailOutbox(SQLiteDatabase(Path(directory) / "db.sqlite"))
                with self.assertRaisesRegex(ValueError, "positive"):
                    await run_email_worker(outbox, Sender(), interval=0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
