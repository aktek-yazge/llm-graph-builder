"""
Workflow Compiler
=================

Transforms a ``WorkflowDSL`` into a LangGraph ``StateGraph`` that can be
executed with ``astream`` / ``ainvoke``.  Each DSL node becomes a graph node
whose function delegates to the matching ``NodeType.execute``.  DSL edges
become ``add_edge`` (unconditional) or ``add_conditional_edges``
(``quality_gate`` with pass/fail ports).
"""

from __future__ import annotations

import logging
from typing import Any, Annotated

from langgraph.graph import StateGraph, START, END

from .models import WorkflowDSL, EdgeDSL
from .node_registry import get_executor, ExecutionContext

logger = logging.getLogger(__name__)


class WorkflowState(dict):
    """Minimal state passed between graph nodes.

    Keys are ``{node_id}.{port_name}`` — populated by each node's return
    dict. The initial state contains ``_ctx`` (ExecutionContext) plus any
    caller-provided inputs.
    """
    pass


def compile(dsl: WorkflowDSL, ctx: ExecutionContext) -> Any:
    """Compile DSL into a runnable LangGraph StateGraph.

    Returns a compiled graph (``graph.compile()``).
    """
    if not dsl.nodes:
        raise ValueError("Workflow has no nodes")

    order = dsl.topological_order()

    builder = StateGraph(dict)

    conditional_sources: set[str] = set()
    for node_dsl in dsl.nodes:
        if node_dsl.type == "quality_gate":
            conditional_sources.add(node_dsl.id)

    for node_dsl in dsl.nodes:
        executor = get_executor(node_dsl.type)
        node_params = node_dsl.params.copy()
        node_type_str = node_dsl.type

        async def _node_fn(
            state: dict,
            _executor=executor,
            _params=node_params,
            _nid=node_dsl.id,
            _ntype=node_type_str,
        ) -> dict:
            inputs = _gather_inputs(state, dsl, _nid)

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="run_step_started",
                    data={"run_id": ctx.run_id, "node_id": _nid, "node_type": _ntype},
                )

            try:
                outputs = await _executor.execute(ctx, _params, inputs)
            except Exception as exc:
                logger.error("Node %s (%s) failed: %s", _nid, _ntype, exc)
                if ctx.notification_mgr:
                    await ctx.notification_mgr.notify(
                        agent_id=ctx.agent_id,
                        event_type="run_step_failed",
                        data={"run_id": ctx.run_id, "node_id": _nid, "error": str(exc)},
                    )
                return {**state, f"{_nid}._error": str(exc)}

            new_state = dict(state)
            for port_name, value in outputs.items():
                new_state[f"{_nid}.{port_name}"] = value

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="run_step_completed",
                    data={
                        "run_id": ctx.run_id,
                        "node_id": _nid,
                        "node_type": _ntype,
                        "output_keys": list(outputs.keys()),
                    },
                )

            return new_state

        builder.add_node(node_dsl.id, _node_fn)

    roots = dsl.root_nodes()
    for root in roots:
        builder.add_edge(START, root.id)

    for node_id in order:
        if node_id in conditional_sources:
            _add_conditional_edges(builder, dsl, node_id)
        else:
            successors = dsl.successors(node_id)
            if not successors:
                builder.add_edge(node_id, END)
            else:
                for succ in successors:
                    builder.add_edge(node_id, succ)

    return builder.compile()


def _gather_inputs(state: dict, dsl: WorkflowDSL, node_id: str) -> dict[str, Any]:
    """Collect inputs for a node from predecessor outputs in state."""
    inputs: dict[str, Any] = {}
    for edge in dsl.edges:
        if edge.to_node == node_id:
            state_key = f"{edge.from_node}.{edge.from_port}"
            if state_key in state:
                inputs[edge.to_port] = state[state_key]
    return inputs


def _add_conditional_edges(builder, dsl: WorkflowDSL, gate_node_id: str) -> None:
    """Wire a quality_gate node's pass/fail outputs to different successors."""
    edge_map: dict[str, str] = {}
    for edge in dsl.edges:
        if edge.from_node == gate_node_id:
            if edge.condition == "pass" or edge.from_port == "pass":
                edge_map["pass"] = edge.to_node
            elif edge.condition == "fail" or edge.from_port == "fail":
                edge_map["fail"] = edge.to_node

    if not edge_map:
        builder.add_edge(gate_node_id, END)
        return

    def _route(state: dict) -> str:
        result = state.get(f"{gate_node_id}._gate_result", "pass")
        if result in edge_map:
            return edge_map[result]
        return END

    destinations = list(edge_map.values())
    if END not in destinations:
        destinations.append(END)

    builder.add_conditional_edges(gate_node_id, _route, destinations)
