"""
Workflow Runtime
================

Orchestrates a single workflow run: compiles DSL, executes the graph,
persists step-level progress to PG, and sends SSE notifications.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .compiler import compile as compile_dsl
from .models import WorkflowDSL
from .node_registry import ExecutionContext
from .store import WorkflowStore

logger = logging.getLogger(__name__)


class WorkflowRuntime:
    """Execute a workflow from DSL → compiled StateGraph → results."""

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

        start_time = time.monotonic()
        errors: list[str] = []

        try:
            final_state = await graph.ainvoke(initial_state)
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
            node_error = final_state.get(error_key)

            if node_error:
                await store.upsert_step(
                    run_id, nid, node_dsl.type, "failed", error=str(node_error),
                )
            elif node_out is not None:
                await store.upsert_step(
                    run_id, nid, node_dsl.type, "completed", outputs=node_out,
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
