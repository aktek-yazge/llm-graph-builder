"""
Pydantic models for Workflow DSL.

The DSL is a directed acyclic graph of typed nodes connected by edges.
It lives as JSONB in PostgreSQL (``workflows.dsl_json``) and is compiled
to a LangGraph ``StateGraph`` at runtime.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Port definitions ─────────────────────────────────────────────────

class PortDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


class PortDef(BaseModel):
    """Single input/output port on a node type."""
    name: str
    direction: PortDirection
    data_type: str = "any"
    required: bool = True


# ── Node DSL ─────────────────────────────────────────────────────────

class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class NodeDSL(BaseModel):
    """One node in the workflow graph."""
    id: str = Field(..., min_length=1, max_length=128)
    type: str = Field(..., min_length=1, max_length=64)
    label: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)

    @field_validator("id")
    @classmethod
    def _slug_id(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Node id must be alphanumeric (dashes/underscores ok)")
        return v


# ── Edge DSL ─────────────────────────────────────────────────────────

class EdgeDSL(BaseModel):
    """Directed edge between two nodes."""
    from_node: str
    from_port: str = "out"
    to_node: str
    to_port: str = "in"
    condition: Optional[str] = None


# ── Workflow DSL (top-level) ─────────────────────────────────────────

class WorkflowDSL(BaseModel):
    """Complete workflow definition — stored as ``workflows.dsl_json``."""
    nodes: list[NodeDSL] = Field(default_factory=list)
    edges: list[EdgeDSL] = Field(default_factory=list)

    # ── Helpers ──────────────────────────────────────────────────────

    @property
    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}

    def get_node(self, node_id: str) -> Optional[NodeDSL]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def root_nodes(self) -> list[NodeDSL]:
        """Nodes with no incoming edges."""
        targets = {e.to_node for e in self.edges}
        return [n for n in self.nodes if n.id not in targets]

    def successors(self, node_id: str) -> list[str]:
        return [e.to_node for e in self.edges if e.from_node == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.from_node for e in self.edges if e.to_node == node_id]

    # ── Validation ───────────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_graph(self) -> "WorkflowDSL":
        ids = self.node_ids

        for edge in self.edges:
            if edge.from_node not in ids:
                raise ValueError(f"Edge source '{edge.from_node}' not in nodes")
            if edge.to_node not in ids:
                raise ValueError(f"Edge target '{edge.to_node}' not in nodes")
            if edge.from_node == edge.to_node:
                raise ValueError(f"Self-loop on '{edge.from_node}'")

        if self.nodes and not self._is_acyclic():
            raise ValueError("Workflow contains a cycle")

        return self

    def _is_acyclic(self) -> bool:
        """Kahn's algorithm for topological sort / cycle detection."""
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            adj[e.from_node].append(e.to_node)
            in_degree[e.to_node] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            nid = queue.pop(0)
            visited += 1
            for child in adj[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        return visited == len(self.nodes)

    def topological_order(self) -> list[str]:
        """Return node IDs in topological order. Raises if cyclic."""
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            adj[e.from_node].append(e.to_node)
            in_degree[e.to_node] += 1

        queue = sorted([nid for nid, deg in in_degree.items() if deg == 0])
        order: list[str] = []
        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for child in sorted(adj[nid]):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self.nodes):
            raise ValueError("Cycle detected during topological sort")
        return order


# ── Workflow row (from PG) ───────────────────────────────────────────

class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class WorkflowRow(BaseModel):
    """Python representation of a ``workflows`` PG row."""
    workflow_id: str
    agent_id: str | None = None
    name: str = "Default"
    description: str = ""
    version: int = 1
    status: WorkflowStatus = WorkflowStatus.DRAFT
    dsl: WorkflowDSL = Field(default_factory=WorkflowDSL)
    is_template: bool = False
    source_template_id: Optional[str] = None
    published_at: Optional[Any] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None
