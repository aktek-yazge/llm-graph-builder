"""
Workflow Compiler
=================

Transforms a ``WorkflowDSL`` into a LangGraph ``StateGraph`` that can be
executed with ``astream`` / ``ainvoke``.  Each DSL node becomes a graph node
whose function delegates to the matching ``NodeType.execute``.  DSL edges
are used for both data flow (port wiring) and execution ordering.

Execution follows topological order. Before running each node, the compiler
checks if any upstream dependency (per DSL edges) failed; if so, the node
is skipped. ``quality_gate`` nodes use conditional routing for pass/fail.
"""

from __future__ import annotations

import logging
from typing import Any, Annotated

from langgraph.graph import StateGraph, START, END

from .models import WorkflowDSL, EdgeDSL
from .node_registry import get_executor, ExecutionContext

logger = logging.getLogger(__name__)

_SKIP_SENTINEL = "__skipped__"


class WorkflowState(dict):
    """Minimal state passed between graph nodes.

    Keys are ``{node_id}.{port_name}`` — populated by each node's return
    dict. The initial state contains ``_ctx`` (ExecutionContext) plus any
    caller-provided inputs.
    """
    pass


def compile(dsl: WorkflowDSL, ctx: ExecutionContext) -> Any:
    """Compile DSL into a runnable LangGraph StateGraph.

    Nodes are chained sequentially in topological order (to avoid LangGraph's
    parallel-branch merge limitations). Each node gathers its inputs from the
    accumulated state dict using the DSL edge definitions.

    Before executing, each node checks whether any of its DSL predecessors
    failed or was skipped. If so, the node is skipped automatically and a
    ``{node_id}._skipped`` marker is written to state.

    Returns a compiled graph (``graph.compile()``).
    """
    if not dsl.nodes:
        raise ValueError("Workflow has no nodes")

    order = dsl.topological_order()
    node_map = {n.id: n for n in dsl.nodes}

    predecessors_map: dict[str, set[str]] = {n.id: set() for n in dsl.nodes}
    for edge in dsl.edges:
        predecessors_map[edge.to_node].add(edge.from_node)

    builder = StateGraph(dict)

    for node_dsl in dsl.nodes:
        executor = get_executor(node_dsl.type)
        node_params = node_dsl.params.copy()
        node_type_str = node_dsl.type
        node_predecessors = predecessors_map.get(node_dsl.id, set())

        async def _node_fn(
            state: dict,
            _executor=executor,
            _params=node_params,
            _nid=node_dsl.id,
            _ntype=node_type_str,
            _preds=frozenset(node_predecessors),
        ) -> dict:
            failed_preds = _check_upstream_failures(state, _preds)
            if failed_preds:
                logger.info(
                    "Skipping node %s (%s): upstream failed/skipped: %s",
                    _nid, _ntype, ", ".join(failed_preds),
                )
                if ctx.notification_mgr:
                    await ctx.notification_mgr.notify(
                        agent_id=ctx.agent_id,
                        event_type="run_step_skipped",
                        data={
                            "run_id": ctx.run_id,
                            "node_id": _nid,
                            "node_type": _ntype,
                            "reason": f"upstream failed: {', '.join(failed_preds)}",
                        },
                    )
                return {**state, f"{_nid}._skipped": True}

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

    for i, nid in enumerate(order):
        if i == 0:
            builder.add_edge(START, nid)
        else:
            prev = order[i - 1]
            prev_node = node_map.get(prev)
            if prev_node and prev_node.type == "quality_gate":
                _add_conditional_edges(builder, dsl, prev, fallback=nid)
            else:
                builder.add_edge(prev, nid)

    last = order[-1]
    last_node = node_map.get(last)
    if last_node and last_node.type == "quality_gate":
        _add_conditional_edges(builder, dsl, last)
    else:
        builder.add_edge(last, END)

    return builder.compile()


def _check_upstream_failures(state: dict, predecessors: frozenset[str]) -> list[str]:
    """Return list of predecessor node IDs that failed or were skipped."""
    failed = []
    for pred_id in predecessors:
        if state.get(f"{pred_id}._error") or state.get(f"{pred_id}._skipped"):
            failed.append(pred_id)
    return failed


def _gather_inputs(state: dict, dsl: WorkflowDSL, node_id: str) -> dict[str, Any]:
    """Collect inputs for a node from predecessor outputs in state."""
    inputs: dict[str, Any] = {}
    for edge in dsl.edges:
        if edge.to_node == node_id:
            state_key = f"{edge.from_node}.{edge.from_port}"
            if state_key in state:
                inputs[edge.to_port] = state[state_key]
    return inputs


def _add_conditional_edges(builder, dsl: WorkflowDSL, gate_node_id: str, fallback: str | None = None) -> None:
    """Wire a quality_gate node's pass/fail outputs to different successors."""
    edge_map: dict[str, str] = {}
    for edge in dsl.edges:
        if edge.from_node == gate_node_id:
            if edge.condition == "pass" or edge.from_port == "pass":
                edge_map["pass"] = edge.to_node
            elif edge.condition == "fail" or edge.from_port == "fail":
                edge_map["fail"] = edge.to_node

    if not edge_map:
        if fallback:
            builder.add_edge(gate_node_id, fallback)
        else:
            builder.add_edge(gate_node_id, END)
        return

    def _route(state: dict) -> str:
        result = state.get(f"{gate_node_id}._gate_result", "pass")
        if result in edge_map:
            return edge_map[result]
        return fallback or END

    destinations = list(edge_map.values())
    if fallback and fallback not in destinations:
        destinations.append(fallback)
    if END not in destinations:
        destinations.append(END)

    builder.add_conditional_edges(gate_node_id, _route, destinations)
