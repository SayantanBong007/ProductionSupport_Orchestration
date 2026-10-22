"""Server-sent events: fan-out of agent progress to every connected UI."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Callable


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        # Fields stamped on every event (the app sets this to the current phase).
        self.extra: Callable[[], dict] = dict

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(self, event: str, data: dict):
        body = {**data, "time": datetime.now().strftime("%H:%M:%S"), **self.extra()}
        payload = f"event: {event}\ndata: {json.dumps(body, default=str)}\n\n"
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:      # a stalled client — drop it
                self.unsubscribe(q)
