from __future__ import annotations

import asyncio

from .email import EmailSender
from .email_outbox import SQLiteEmailOutbox


async def run_email_worker(
        outbox: SQLiteEmailOutbox,
        sender: EmailSender,
        *,
        interval: float = 15.0,
        stop: asyncio.Event | None = None,
) -> None:
    if interval <= 0:
        raise ValueError("email worker interval must be positive")
    stop_event = stop or asyncio.Event()
    while not stop_event.is_set():
        await asyncio.to_thread(outbox.deliver_once, sender)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            if stop_event.is_set():
                return
            continue
