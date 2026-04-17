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
- chat_message_chunk / chat_message_injected: arka plandan enjekte edilen
  agent yanitlarinin parcali / final hali (chat UI tarafindan dogrudan
  mesaj listesine alinir).

Durability:
- In-memory deque (SSE fan-out + son N kayit icin O(1) erisim)
- Optional PostgreSQL ``notifications`` tablosu (kalici tarihce, restart
  sonrasi geri yukleme). PG yoksa veya yazma hatasi varsa silently in-memory
  modda devam eder.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

MAX_HISTORY = 200
SSE_HEARTBEAT_SEC = 15


@dataclass
class Notification:
    agent_id: str
    event_type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    db_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        if self.db_id is not None:
            out["id"] = self.db_id
        return out


class NotificationManager:
    """In-memory notification hub with SSE fan-out and optional PG persistence."""

    # Event types that are extremely high-frequency / streamed and should NOT
    # be persisted to PG (history would explode and they're useless after
    # the live stream ends anyway).
    EPHEMERAL_EVENT_TYPES = frozenset({
        "heartbeat",
        "chat_message_chunk",
        "tool_call",
        "tool_result",
    })

    def __init__(self, pg=None) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Notification | None]]] = defaultdict(list)
        self._history: dict[str, deque[Notification]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
        self._lock = asyncio.Lock()
        self._pg = pg

    def attach_pg(self, pg) -> None:
        """Attach a postgres client after construction (used when registry has it later)."""
        self._pg = pg

    async def notify(
        self,
        agent_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> Notification:
        notif = Notification(agent_id=agent_id, event_type=event_type, data=data)

        if self._pg is not None and event_type not in self.EPHEMERAL_EVENT_TYPES:
            try:
                row = await self._pg.fetchrow(
                    """
                    INSERT INTO notifications (agent_id, event_type, data)
                    VALUES ($1, $2, $3::jsonb)
                    RETURNING id, created_at
                    """,
                    agent_id,
                    event_type,
                    json.dumps(data, ensure_ascii=False, default=str),
                )
                if row:
                    notif.db_id = int(row["id"])
            except Exception as exc:
                logger.debug(
                    "notifications PG insert skipped (agent=%s, type=%s): %s",
                    agent_id, event_type, exc,
                )

        self._history[agent_id].append(notif)

        async with self._lock:
            queues = self._subscribers.get(agent_id, [])
            for q in queues:
                try:
                    q.put_nowait(notif)
                except asyncio.QueueFull:
                    logger.warning("SSE queue full for agent %s, dropping notification", agent_id)

        logger.debug("Notification sent: agent=%s type=%s", agent_id, event_type)
        return notif

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
        """Return most-recent N notifications from in-memory deque."""
        history = self._history.get(agent_id, deque())
        items = list(history)[-limit:]
        return [n.to_dict() for n in items]

    async def get_history(
        self,
        agent_id: str,
        limit: int = 100,
        since_id: Optional[int] = None,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Load durable notification history from PostgreSQL.

        Falls back to ``get_recent`` (in-memory) when PG is unavailable so the
        endpoint always returns something usable.
        """
        if self._pg is None:
            return self.get_recent(agent_id, limit=limit)

        sql = "SELECT id, agent_id, event_type, data, read_at, created_at FROM notifications WHERE agent_id=$1"
        args: list[Any] = [agent_id]
        if since_id is not None:
            sql += f" AND id > ${len(args) + 1}"
            args.append(since_id)
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += f" ORDER BY id DESC LIMIT ${len(args) + 1}"
        args.append(limit)

        try:
            rows = await self._pg.fetch(sql, *args)
        except Exception as exc:
            logger.warning("notifications PG fetch failed (agent=%s): %s", agent_id, exc)
            return self.get_recent(agent_id, limit=limit)

        result: list[dict[str, Any]] = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (ValueError, TypeError):
                    data = {"raw": data}
            result.append({
                "id": int(row["id"]),
                "agent_id": row["agent_id"],
                "event_type": row["event_type"],
                "data": data or {},
                "read_at": row["read_at"].isoformat() if row["read_at"] else None,
                "timestamp": row["created_at"].timestamp() if row["created_at"] else 0.0,
            })
        return result

    async def mark_read(
        self,
        agent_id: str,
        notification_ids: Optional[list[int]] = None,
    ) -> int:
        """Mark notifications as read. If ``notification_ids`` is None, marks all
        unread for the agent. Returns the number of rows updated.
        """
        if self._pg is None:
            return 0
        try:
            if notification_ids:
                result = await self._pg.execute(
                    """
                    UPDATE notifications SET read_at=NOW()
                    WHERE agent_id=$1 AND id = ANY($2::bigint[]) AND read_at IS NULL
                    """,
                    agent_id, notification_ids,
                )
            else:
                result = await self._pg.execute(
                    "UPDATE notifications SET read_at=NOW() WHERE agent_id=$1 AND read_at IS NULL",
                    agent_id,
                )
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0
        except Exception as exc:
            logger.warning("notifications mark_read failed (agent=%s): %s", agent_id, exc)
            return 0

    async def close_agent(self, agent_id: str) -> None:
        """Signal all SSE subscribers for an agent to disconnect."""
        async with self._lock:
            queues = self._subscribers.pop(agent_id, [])
            for q in queues:
                q.put_nowait(None)
        self._history.pop(agent_id, None)
