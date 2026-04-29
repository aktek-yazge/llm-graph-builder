"""
Workflow Runtime
================

Orchestrates a single workflow run: compiles DSL, executes the graph,
persists step-level progress to PG, and sends SSE notifications.

Supports HITL (Human-in-the-Loop) via ``HumanReviewPending``:
when a ``human_review`` node raises that exception, the run is
persisted with status ``awaiting_approval`` and graph execution stops.
``resume_run()`` picks up from where it left off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .compiler import compile as compile_dsl
from .models import WorkflowDSL
from .node_registry import ExecutionContext
from .store import WorkflowStore
from . import nodes as _nodes  # noqa: F401  — trigger @register decorators

logger = logging.getLogger(__name__)


class WorkflowRuntime:
    """Execute a workflow from DSL -> compiled StateGraph -> results."""

    def __init__(self, pg, notification_mgr=None, celery_app=None) -> None:
        self._pg = pg
        self._notification_mgr = notification_mgr
        self._celery_app = celery_app

    async def start_run(
        self,
        agent_id: str,
        workflow_id: str,
        mode: str = "test_one",
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compile and run the workflow. Returns summary dict.

        ``mode`` can be:
        - ``test_one``: single file test (sync, blocking)
        - ``full_batch``: batch run (persists run, may be long)
        - ``preview``: validation only, no execution
        """
        store = WorkflowStore(self._pg)
        wf = await store.get(workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")

        dsl = wf.dsl
        run_id = await store.create_run(workflow_id, agent_id, mode, inputs)

        if self._notification_mgr:
            await self._notification_mgr.notify(
                agent_id=agent_id,
                event_type="run_started",
                data={"run_id": run_id, "workflow_id": workflow_id, "mode": mode},
            )

        if mode == "preview":
            await store.finish_run(run_id, "completed", {"validated": True})
            return {"run_id": run_id, "status": "completed", "validated": True}

        ctx = ExecutionContext(
            agent_id=agent_id,
            pg=self._pg,
            run_id=run_id,
            notification_mgr=self._notification_mgr,
            celery_app=self._celery_app,
        )

        try:
            graph = compile_dsl(dsl, ctx)
        except Exception as exc:
            await store.finish_run(run_id, "failed", {"error": str(exc)})
            raise

        initial_state: dict[str, Any] = {"_ctx": ctx}
        if inputs:
            initial_state.update(inputs)

        return await self._execute_graph(graph, initial_state, ctx, dsl, store, run_id, agent_id)

    async def resume_run(self, run_id: str, decision: str = "approve") -> dict[str, Any]:
        """Resume a run that is ``awaiting_approval``.

        ``decision`` can be ``approve`` (continue) or ``reject`` (fail the run).
        """
        store = WorkflowStore(self._pg)
        run = await store.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run["status"] != "awaiting_approval":
            raise ValueError(f"Run {run_id} is not awaiting approval (status={run['status']})")

        agent_id = run["agent_id"]
        workflow_id = run["workflow_id"]

        if decision == "reject":
            await store.finish_run(run_id, "rejected", {"reason": "User rejected"})
            if self._notification_mgr:
                await self._notification_mgr.notify(
                    agent_id=agent_id,
                    event_type="run_rejected",
                    data={"run_id": run_id},
                )
            return {"run_id": run_id, "status": "rejected"}

        wf = await store.get(workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")

        ctx = ExecutionContext(
            agent_id=agent_id,
            pg=self._pg,
            run_id=run_id,
            notification_mgr=self._notification_mgr,
            celery_app=self._celery_app,
        )

        saved_state = {}
        try:
            summary_json = run.get("summary_json")
            if isinstance(summary_json, str):
                saved_state = json.loads(summary_json)
            elif isinstance(summary_json, dict):
                saved_state = summary_json
        except (json.JSONDecodeError, TypeError):
            pass

        paused_node = saved_state.get("_paused_at_node", "")
        partial_outputs = saved_state.get("_partial_state", {})

        if paused_node and partial_outputs:
            partial_outputs[f"{paused_node}.out"] = partial_outputs.get(f"{paused_node}._pending_input", {})
            partial_outputs.pop(f"{paused_node}._pending_input", None)

        await store.finish_run(run_id, "running", None)

        graph = compile_dsl(wf.dsl, ctx)
        initial_state: dict[str, Any] = {"_ctx": ctx}
        initial_state.update(partial_outputs)

        if self._notification_mgr:
            await self._notification_mgr.notify(
                agent_id=agent_id,
                event_type="run_resumed",
                data={"run_id": run_id, "decision": decision},
            )

        return await self._execute_graph(graph, initial_state, ctx, wf.dsl, store, run_id, agent_id)

    async def _execute_graph(
        self,
        graph,
        initial_state: dict[str, Any],
        ctx: ExecutionContext,
        dsl: WorkflowDSL,
        store: WorkflowStore,
        run_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Shared execution logic for both start_run and resume_run."""
        from .nodes.human_review import HumanReviewPending

        start_time = time.monotonic()
        errors: list[str] = []
        awaiting_approval = False
        paused_node = ""

        try:
            final_state = await graph.ainvoke(initial_state)
        except HumanReviewPending as hrp:
            elapsed = time.monotonic() - start_time
            partial_state = {k: v for k, v in initial_state.items() if not k.startswith("_")}
            await store.finish_run(run_id, "awaiting_approval", {
                "_paused_at_node": hrp.run_id,
                "_partial_state": partial_state,
                "message": hrp.message,
                "auto_approve_sec": hrp.auto_approve_sec,
                "elapsed_before_pause": round(elapsed, 2),
            })

            if hrp.auto_approve_sec > 0:
                asyncio.get_event_loop().call_later(
                    hrp.auto_approve_sec,
                    lambda: asyncio.ensure_future(self.resume_run(run_id, "approve")),
                )

            return {
                "run_id": run_id,
                "status": "awaiting_approval",
                "message": hrp.message,
                "auto_approve_sec": hrp.auto_approve_sec,
            }
        except Exception as exc:
            logger.error("Workflow run %s failed: %s", run_id, exc)
            errors.append(str(exc))
            final_state = {}

        elapsed = time.monotonic() - start_time
        status = "failed" if errors else "completed"

        node_outputs: dict[str, Any] = {}
        for key, val in final_state.items():
            if "." in key and not key.startswith("_"):
                node_id, port = key.split(".", 1)
                node_outputs.setdefault(node_id, {})[port] = val

        for node_dsl in dsl.nodes:
            nid = node_dsl.id
            node_out = node_outputs.get(nid)
            error_key = f"{nid}._error"
            skipped_key = f"{nid}._skipped"
            node_error = final_state.get(error_key)
            node_skipped = final_state.get(skipped_key)

            db_outputs = _summarize_outputs(node_out) if node_out else None

            if node_error:
                await store.upsert_step(
                    run_id, nid, node_dsl.type, "failed", error=str(node_error),
                )
            elif node_skipped:
                await store.upsert_step(
                    run_id, nid, node_dsl.type, "skipped",
                )
            elif node_out is not None:
                await store.upsert_step(
                    run_id, nid, node_dsl.type, "completed", outputs=db_outputs,
                )
            else:
                await store.upsert_step(
                    run_id, nid, node_dsl.type, "skipped",
                )

        summary = {
            "run_id": run_id,
            "status": status,
            "elapsed_sec": round(elapsed, 2),
            "node_count": len(dsl.nodes),
            "errors": errors,
            "node_outputs": {k: list(v.keys()) for k, v in node_outputs.items()},
        }

        await store.finish_run(run_id, status, summary)

        if self._notification_mgr:
            await self._notification_mgr.notify(
                agent_id=agent_id,
                event_type="run_complete",
                data=summary,
            )

        return summary


MAX_TEXT_LEN = 500


def _summarize_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    """Trim large text fields so step outputs fit in DB without bloat."""
    summary: dict[str, Any] = {}
    for key, val in outputs.items():
        if isinstance(val, str) and len(val) > MAX_TEXT_LEN:
            summary[key] = val[:MAX_TEXT_LEN] + f"... ({len(val)} chars)"
        elif isinstance(val, list):
            items = []
            for item in val:
                if isinstance(item, dict):
                    trimmed = {}
                    for k, v in item.items():
                        if isinstance(v, str) and len(v) > MAX_TEXT_LEN:
                            trimmed[k] = v[:MAX_TEXT_LEN] + f"... ({len(v)} chars)"
                        else:
                            trimmed[k] = v
                    items.append(trimmed)
                else:
                    items.append(item)
            summary[key] = items
        else:
            summary[key] = val
    return summary
