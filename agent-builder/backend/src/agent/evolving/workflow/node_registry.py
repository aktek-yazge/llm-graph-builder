"""
Node Type Registry
==================

Central catalog of available workflow node types.  Each node type is a Python
class that subclasses ``NodeType`` and is registered via the ``@register``
decorator.  The registry is queried by:

- The **compiler** (to build a LangGraph StateGraph)
- The **builder agent** (``list_node_types`` tool)
- The **frontend** (``GET /workflow/node-types`` endpoint)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .models import PortDef, PortDirection

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type["NodeType"]] = {}


@dataclass
class NodeTypeMetadata:
    """Serializable descriptor returned by ``get_catalog()``."""
    type_id: str
    label: str
    description: str
    category: str
    icon: str
    color: str
    params_schema: dict[str, Any]
    input_ports: list[dict[str, Any]]
    output_ports: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type_id": self.type_id,
            "label": self.label,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "color": self.color,
            "params_schema": self.params_schema,
            "input_ports": self.input_ports,
            "output_ports": self.output_ports,
        }


class ExecutionContext:
    """Runtime context passed to every node's ``execute`` method."""

    def __init__(
        self,
        agent_id: str,
        pg,
        run_id: str,
        notification_mgr=None,
        celery_app=None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.pg = pg
        self.run_id = run_id
        self.notification_mgr = notification_mgr
        self.celery_app = celery_app
        self.extra = extra or {}


class NodeType(ABC):
    """Base class for all workflow node types.

    Subclasses MUST define class-level attributes ``type_id``, ``label``,
    ``description``, ``category``, ``icon``, ``color`` and implement
    ``execute``, ``input_ports``, ``output_ports``, ``params_schema``.
    """

    type_id: str = ""
    label: str = ""
    description: str = ""
    category: str = "general"
    icon: str = "settings"
    color: str = "#718096"

    @classmethod
    @abstractmethod
    def params_schema(cls) -> dict[str, Any]:
        """JSONSchema for user-configurable parameters."""
        ...

    @classmethod
    @abstractmethod
    def input_ports(cls) -> list[PortDef]:
        ...

    @classmethod
    @abstractmethod
    def output_ports(cls) -> list[PortDef]:
        ...

    @abstractmethod
    async def execute(
        self,
        ctx: ExecutionContext,
        params: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the node. Return dict keyed by output port names."""
        ...

    @classmethod
    def metadata(cls) -> NodeTypeMetadata:
        return NodeTypeMetadata(
            type_id=cls.type_id,
            label=cls.label,
            description=cls.description,
            category=cls.category,
            icon=cls.icon,
            color=cls.color,
            params_schema=cls.params_schema(),
            input_ports=[p.model_dump() for p in cls.input_ports()],
            output_ports=[p.model_dump() for p in cls.output_ports()],
        )


def register(cls: type[NodeType]) -> type[NodeType]:
    """Class decorator — registers a NodeType subclass in the global catalog."""
    if not cls.type_id:
        raise ValueError(f"{cls.__name__} must define type_id")
    if cls.type_id in _REGISTRY:
        logger.warning("Node type '%s' re-registered (was %s)", cls.type_id, _REGISTRY[cls.type_id].__name__)
    _REGISTRY[cls.type_id] = cls
    return cls


def get_catalog() -> list[NodeTypeMetadata]:
    """Return metadata for every registered node type."""
    return [cls.metadata() for cls in _REGISTRY.values()]


def get_executor(type_id: str) -> NodeType:
    """Instantiate a node executor by type_id. Raises ``KeyError`` if unknown."""
    cls = _REGISTRY[type_id]
    return cls()


def get_node_class(type_id: str) -> type[NodeType]:
    return _REGISTRY[type_id]


def registered_type_ids() -> list[str]:
    return list(_REGISTRY.keys())
