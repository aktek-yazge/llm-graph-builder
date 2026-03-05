"""
Agent Event Bus
================

Agent'lar arasi asenkron mesajlasma ve event dagitimi.

Uc mod desteklenir:
1. In-process (asyncio queue) - Tek process ici hizli iletisim
2. RabbitMQ pub/sub - Celery broker uzerinden dagitik iletisim
3. SSE stream - Frontend'e real-time push

Kullanim:
    bus = AgentEventBus.get_instance()
    await bus.publish("workspace.schema_approved", {"workspace_id": "ws-1", ...})
    bus.subscribe("workspace.*", callback)
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

EventHandler = Callable[["AgentEvent"], Awaitable[None]]


@dataclass
class AgentEvent:
    """Agent event bus'indeki tek bir event."""
    event_type: str
    payload: Dict[str, Any]
    source_agent: str = ""
    workspace_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    event_id: str = field(default_factory=lambda: f"evt-{int(time.time()*1000)}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "source_agent": self.source_agent,
            "workspace_id": self.workspace_id,
            "timestamp": self.timestamp,
        }

    def to_sse(self) -> str:
        """SSE format: event: type\ndata: json\n\n"""
        return f"event: {self.event_type}\ndata: {json.dumps(self.to_dict(), ensure_ascii=False, default=str)}\n\n"


@dataclass
class _Subscription:
    pattern: str
    handler: EventHandler
    _compiled: Optional[re.Pattern] = field(init=False, default=None)

    def __post_init__(self):
        regex = self.pattern.replace(".", r"\.").replace("*", "[^.]+")
        self._compiled = re.compile(f"^{regex}$")

    def matches(self, event_type: str) -> bool:
        return bool(self._compiled and self._compiled.match(event_type))


class AgentEventBus:
    """
    Lightweight in-process event bus with optional RabbitMQ fanout.
    Singleton pattern - get_instance() ile eris.
    """

    _instance: Optional["AgentEventBus"] = None

    def __init__(self):
        self._subscriptions: List[_Subscription] = []
        self._history: List[AgentEvent] = []
        self._max_history = 500
        self._sse_queues: Dict[str, asyncio.Queue] = {}
        self._rabbitmq_channel = None

    @classmethod
    def get_instance(cls) -> "AgentEventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Test veya restart icin singleton sifirla."""
        cls._instance = None

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        """
        Event pattern'ine abone ol.
        Pattern ornekleri:
            "workspace.schema_approved" - tam eslesme
            "workspace.*" - workspace.* event'leri
            "agent.*" - agent.* event'leri
        """
        self._subscriptions.append(_Subscription(pattern=pattern, handler=handler))
        logger.debug("Subscribed to pattern: %s", pattern)

    def unsubscribe(self, pattern: str) -> int:
        """Pattern'e uyan abonelikleri kaldir. Kaldirilan abone sayisini don."""
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.pattern != pattern]
        removed = before - len(self._subscriptions)
        if removed:
            logger.debug("Unsubscribed %d handlers from pattern: %s", removed, pattern)
        return removed

    async def publish(self, event_type: str, payload: Dict[str, Any],
                      source_agent: str = "", workspace_id: str = "") -> AgentEvent:
        """
        Event yayinla. Tum eslesen abonelere async dispatch edilir.
        """
        event = AgentEvent(
            event_type=event_type,
            payload=payload,
            source_agent=source_agent,
            workspace_id=workspace_id,
        )

        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        matching = [s for s in self._subscriptions if s.matches(event_type)]
        if matching:
            tasks = [s.handler(event) for s in matching]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "Event handler error for %s (pattern=%s): %s",
                        event_type, matching[i].pattern, result,
                    )

        for q in self._sse_queues.values():
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        logger.debug("Published event: %s (dispatched to %d handlers)", event_type, len(matching))
        return event

    def get_history(
        self,
        event_type: str = "",
        workspace_id: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Event gecmisini sorgula."""
        filtered = self._history
        if event_type:
            sub = _Subscription(pattern=event_type, handler=lambda e: None)
            filtered = [e for e in filtered if sub.matches(e.event_type)]
        if workspace_id:
            filtered = [e for e in filtered if e.workspace_id == workspace_id]
        return [e.to_dict() for e in filtered[-limit:]]

    # -------------------------------------------------------------------------
    # SSE streaming
    # -------------------------------------------------------------------------

    def create_sse_stream(self, stream_id: str) -> asyncio.Queue:
        """SSE stream icin queue olustur."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._sse_queues[stream_id] = q
        logger.debug("SSE stream created: %s", stream_id)
        return q

    def remove_sse_stream(self, stream_id: str) -> None:
        """SSE stream'i kaldir."""
        self._sse_queues.pop(stream_id, None)
        logger.debug("SSE stream removed: %s", stream_id)

    # -------------------------------------------------------------------------
    # RabbitMQ fanout (optional)
    # -------------------------------------------------------------------------

    async def connect_rabbitmq(self, url: Optional[str] = None) -> bool:
        """
        RabbitMQ'ya baglan - dagitik event dagitimi icin.
        Opsiyonel: baglanmazsa sadece in-process calismaya devam eder.
        """
        try:
            import aio_pika
        except ImportError:
            logger.warning("aio_pika not installed; RabbitMQ fanout disabled")
            return False

        broker_url = url or os.getenv("RABBITMQ_URL", "amqp://rabbitmq:RabbitMQ!654*@localhost:5672/")
        try:
            connection = await aio_pika.connect_robust(broker_url)
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                "agent_events", aio_pika.ExchangeType.FANOUT, durable=True,
            )
            self._rabbitmq_channel = channel
            self._rabbitmq_exchange = exchange

            queue = await channel.declare_queue("", exclusive=True)
            await queue.bind(exchange)

            async def _on_rmq_message(message: aio_pika.IncomingMessage):
                async with message.process():
                    data = json.loads(message.body.decode())
                    if data.get("_source_pid") == os.getpid():
                        return
                    event = AgentEvent(**{k: v for k, v in data.items() if k != "_source_pid"})
                    matching = [s for s in self._subscriptions if s.matches(event.event_type)]
                    for s in matching:
                        try:
                            await s.handler(event)
                        except Exception as exc:
                            logger.error("RMQ handler error: %s", exc)

            await queue.consume(_on_rmq_message)
            logger.info("Connected to RabbitMQ event fanout: %s", broker_url)
            return True
        except Exception as e:
            logger.warning("RabbitMQ connection failed (in-process only): %s", e)
            return False

    async def _publish_to_rabbitmq(self, event: AgentEvent):
        if not self._rabbitmq_channel:
            return
        try:
            import aio_pika
            data = event.to_dict()
            data["_source_pid"] = os.getpid()
            await self._rabbitmq_exchange.publish(
                aio_pika.Message(body=json.dumps(data, ensure_ascii=False, default=str).encode()),
                routing_key="",
            )
        except Exception as e:
            logger.error("RabbitMQ publish failed: %s", e)


# ---- Predefined event types -------------------------------------------------

class EventTypes:
    """Standart agent event tipleri."""
    SCHEMA_PROPOSED = "workspace.schema_proposed"
    SCHEMA_APPROVED = "workspace.schema_approved"
    SCHEMA_REJECTED = "workspace.schema_rejected"
    SCHEMA_CHANGED = "workspace.schema_changed"
    KB_AGENT_CREATED = "agent.kb_agent_created"
    PROCESSING_STARTED = "processing.started"
    PROCESSING_PROGRESS = "processing.progress"
    PROCESSING_COMPLETED = "processing.completed"
    PROCESSING_FAILED = "processing.failed"
    ELICITATION_REQUESTED = "elicitation.requested"
    ELICITATION_RESOLVED = "elicitation.resolved"
    AGENT_MESSAGE = "agent.message"
    AGENT_STATUS_CHANGED = "agent.status_changed"
    DELEGATION_REQUESTED = "delegation.requested"
    DELEGATION_COMPLETED = "delegation.completed"
    DELEGATION_FAILED = "delegation.failed"


async def register_schema_change_notifier():
    """
    Register an event handler that notifies connected agents
    when a workspace's schema changes.
    """
    bus = AgentEventBus.get_instance()

    async def _on_schema_change(event: AgentEvent):
        workspace_id = event.workspace_id
        if not workspace_id:
            return

        try:
            from ..event_store.postgres_client import get_postgres_client
            from ..chat_agent_repository import ChatAgentRepository
            from ..comms_repository import CommsRepository

            pg = await get_postgres_client()
            repo = ChatAgentRepository(pg)
            comms = CommsRepository(pg)

            agents = await repo.list_by_workspace(workspace_id)
            for agent in agents:
                agent_id = str(agent["id"])
                await comms.send_message(
                    from_agent="system",
                    to_agent=agent_id,
                    message=f"Workspace {workspace_id} schemasi degisti: {event.payload.get('change_type', 'update')}",
                    message_type="schema_notification",
                    metadata={
                        "workspace_id": workspace_id,
                        "event_type": event.event_type,
                        "change_type": event.payload.get("change_type", ""),
                    },
                )
            logger.info(
                "Schema change notification sent to %d agents for workspace %s",
                len(agents), workspace_id,
            )
        except Exception as e:
            logger.error("Schema change notification failed: %s", e)

    bus.subscribe(EventTypes.SCHEMA_CHANGED, _on_schema_change)
    bus.subscribe(EventTypes.SCHEMA_APPROVED, _on_schema_change)
    logger.info("Schema change notifier registered")
