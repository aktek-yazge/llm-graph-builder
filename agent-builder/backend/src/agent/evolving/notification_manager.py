"""
Notification Manager
====================

Proaktif bildirim sistemi.
Celery callback'lerinden gelen olaylari SSE stream ve polling ile iletir.

Bildirim turleri:
- batch_complete: Batch isleme tamamlandi
- batch_failed: Batch isleme basarisiz
- document_complete: Tek belge islendi
- document_failed: Tek belge basarisiz
- quality_alert: Kalite sorunu tespit edildi
- review_needed: Kullanici incelemesi gereken belge
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

MAX_HISTORY = 200
SSE_HEARTBEAT_SEC = 15


@dataclass
class Notification:
    agent_id: str
    event_type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class NotificationManager:
    """In-memory notification hub with SSE fan-out and polling history."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Notification | None]]] = defaultdict(list)
        self._history: dict[str, deque[Notification]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
        self._lock = asyncio.Lock()

    async def notify(self, agent_id: str, event_type: str, data: dict[str, Any]) -> None:
        notif = Notification(agent_id=agent_id, event_type=event_type, data=data)

        self._history[agent_id].append(notif)

        async with self._lock:
            queues = self._subscribers.get(agent_id, [])
            for q in queues:
                try:
                    q.put_nowait(notif)
                except asyncio.QueueFull:
                    logger.warning("SSE queue full for agent %s, dropping notification", agent_id)

        logger.debug("Notification sent: agent=%s type=%s", agent_id, event_type)

    async def subscribe(self, agent_id: str) -> AsyncIterator[dict[str, Any]]:
        """SSE stream generator. Yields notification dicts until client disconnects."""
        q: asyncio.Queue[Notification | None] = asyncio.Queue(maxsize=256)

        async with self._lock:
            self._subscribers[agent_id].append(q)

        try:
            while True:
                try:
                    notif = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SEC)
                    if notif is None:
                        break
                    yield notif.to_dict()
                except asyncio.TimeoutError:
                    yield {"event_type": "heartbeat", "timestamp": time.time()}
        finally:
            async with self._lock:
                try:
                    self._subscribers[agent_id].remove(q)
                except ValueError:
                    pass

    def get_recent(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        history = self._history.get(agent_id, deque())
        items = list(history)[-limit:]
        return [n.to_dict() for n in items]

    async def close_agent(self, agent_id: str) -> None:
        """Signal all SSE subscribers for an agent to disconnect."""
        async with self._lock:
            queues = self._subscribers.pop(agent_id, [])
            for q in queues:
                q.put_nowait(None)
        self._history.pop(agent_id, None)
