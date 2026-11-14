"""In-process pub/sub -> Server-Sent Events."""
import asyncio
import json
import time
from collections import deque
from typing import AsyncIterator, Optional


class EventBus:
    def __init__(self):
        self.subs: set[asyncio.Queue] = set()
        self.history: deque = deque(maxlen=300)

    def publish(self, type_: str, data: dict, session_id: Optional[str] = None):
        ev = {"type": type_, "session_id": session_id, "ts": time.time(), "data": data}
        self.history.append(ev)
        for q in list(self.subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    async def stream(self, session_id: Optional[str] = None) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subs.add(q)
        try:
            yield "retry: 1500\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if session_id and ev["session_id"] not in (None, session_id):
                    continue
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
        finally:
            self.subs.discard(q)


bus = EventBus()
