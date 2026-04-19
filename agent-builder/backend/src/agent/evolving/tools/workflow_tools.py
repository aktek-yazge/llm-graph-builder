"""
Workflow Tools
==============

Builder agent'in kullandigi tool'lar.  Workflow DSL'i mutate eder,
valide eder, test calistirir, yayinlar.  Her mutation SSE uzerinden
frontend'e bildirilir (``workflow_changed`` event).

Multi-workflow destegi: her tool opsiyonel ``workflow_id`` parametresi
alir; verilmezse active workflow uzerinde calisir.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _gen_node_id(type_id: str) -> str:
    return f"{type_id}-{uuid.uuid4().hex[:6]}"


def create_workflow_tools(agent_id: str, pg, notification_mgr=None, celery_app=None) -> list:
    """Factory that returns all workflow-related tools bound to a specific agent."""

    async def _get_store():
        from ..workflow.store import WorkflowStore
        return WorkflowStore(pg)

    async def _get_knowledge():
        from ..knowledge_store import KnowledgeStore
        return KnowledgeStore(pg)

    async def _notify(event_type: str, data: dict[str, Any]) -> None:
        if notification_mgr:
            await notification_mgr.notify(
                agent_id=agent_id, event_type=event_type, data=data,
            )

    async def _resolve_workflow(workflow_id: str | None = None):
        """Return the target WorkflowRow: explicit id, active, or create Default."""
        store = await _get_store()
        if workflow_id:
            wf = await store.get(workflow_id)
            if wf and wf.agent_id == agent_id:
                return wf
        ks = await _get_knowledge()
        active_id = await ks.load_active_workflow(agent_id)
        return await store.get_or_create(agent_id, active_workflow_id=active_id)

    # ── Multi-workflow management tools ──────────────────────────

    @tool
    async def create_workflow(name: str = "", description: str = "") -> str:
        """Yeni bir workflow olustur ve aktif olarak ayarla.

        Args:
            name: Workflow ismi (bos birakildiysa otomatik isim verilir)
            description: Workflow aciklamasi (opsiyonel)
        """
        store = await _get_store()
        ks = await _get_knowledge()

        if not name:
            existing = await store.list_workflows(agent_id)
            name = f"Workflow {len(existing) + 1}"

        wf = await store.create_workflow(agent_id, name, description)
        await ks.save_active_workflow(agent_id, wf.workflow_id)

        await _notify("workflow_created", {
            "workflow_id": wf.workflow_id,
            "name": wf.name,
        })

        return json.dumps({
            "workflow_id": wf.workflow_id,
            "name": wf.name,
            "status": "created",
            "message": f"'{wf.name}' olusturuldu ve aktif olarak ayarlandi.",
        }, ensure_ascii=False)

    @tool
    async def list_workflows() -> str:
        """Agent'in tum workflow'larini listele (id, isim, durum, node sayisi)."""
        store = await _get_store()
        ks = await _get_knowledge()
        workflows = await store.list_workflows(agent_id)
        active_id = await ks.load_active_workflow(agent_id)

        items = []
        for wf in workflows:
            items.append({
                "workflow_id": wf.workflow_id,
                "name": wf.name,
                "status": wf.status.value if hasattr(wf.status, "value") else wf.status,
                "version": wf.version,
                "node_count": len(wf.dsl.nodes),
                "is_active": wf.workflow_id == active_id,
                "is_template": wf.is_template,
            })

        return json.dumps({
            "workflows": items,
            "active_workflow_id": active_id,
            "total": len(items),
        }, ensure_ascii=False, indent=2)

    @tool
    async def switch_workflow(workflow_id: str) -> str:
        """Aktif workflow'u degistir.

        Args:
            workflow_id: Gecilecek workflow'un ID'si
        """
        store = await _get_store()
        ks = await _get_knowledge()
        wf = await store.get(workflow_id)
        if not wf or wf.agent_id != agent_id:
            return json.dumps({"error": f"Workflow '{workflow_id}' bulunamadi"})

        await ks.save_active_workflow(agent_id, workflow_id)

        await _notify("active_workflow_changed", {
            "workflow_id": workflow_id,
        })

        return json.dumps({
            "status": "switched",
            "workflow_id": workflow_id,
            "name": wf.name,
            "message": f"Aktif workflow '{wf.name}' olarak degistirildi.",
        }, ensure_ascii=False)

    @tool
    async def rename_workflow(workflow_id: str, name: str) -> str:
        """Workflow ismini degistir.

        Args:
            workflow_id: Workflow ID
            name: Yeni isim
        """
        store = await _get_store()
        wf = await store.get(workflow_id)
        if not wf or wf.agent_id != agent_id:
            return json.dumps({"error": f"Workflow '{workflow_id}' bulunamadi"})

        await store.rename_workflow(workflow_id, name)
        await _notify("workflow_changed", {
            "workflow_id": workflow_id,
            "source": "rename",
        })

        return json.dumps({
            "status": "renamed",
            "workflow_id": workflow_id,
            "name": name,
        }, ensure_ascii=False)

    @tool
    async def delete_workflow(workflow_id: str) -> str:
        """Bir workflow'u ve ilgili run gecmisini sil.

        Args:
            workflow_id: Silinecek workflow ID
        """
        store = await _get_store()
        ks = await _get_knowledge()
        wf = await store.get(workflow_id)
        if not wf or wf.agent_id != agent_id:
            return json.dumps({"error": f"Workflow '{workflow_id}' bulunamadi"})

        await store.delete_workflow(workflow_id)

        active_id = await ks.load_active_workflow(agent_id)
        if active_id == workflow_id:
            remaining = await store.list_workflows(agent_id)
            if remaining:
                await ks.save_active_workflow(agent_id, remaining[0].workflow_id)

        await _notify("workflow_deleted", {"workflow_id": workflow_id})

        return json.dumps({
            "status": "deleted",
            "workflow_id": workflow_id,
            "message": f"Workflow silindi.",
        }, ensure_ascii=False)

    @tool
    async def share_as_template(workflow_id: str = "", description: str = "") -> str:
        """Workflow'u template olarak paylas -- baska agent'lar import edebilir.

        Args:
            workflow_id: Paylasilacak workflow ID (bos birakildiysa aktif workflow)
            description: Template aciklamasi
        """
        wf = await _resolve_workflow(workflow_id or None)
        store = await _get_store()
        await store.set_template(wf.workflow_id, True, description or wf.description)

        return json.dumps({
            "status": "shared",
            "workflow_id": wf.workflow_id,
            "name": wf.name,
            "message": f"'{wf.name}' template olarak paylasildi.",
        }, ensure_ascii=False)

    @tool
    async def import_template(template_workflow_id: str, name: str = "") -> str:
        """Paylasilan bir template'i bu agent'a import et (kopyala).

        Args:
            template_workflow_id: Import edilecek template workflow ID
            name: Yeni workflow icin isim (bos birakildiysa orijinal isim + kopya)
        """
        store = await _get_store()
        ks = await _get_knowledge()
        new_wf = await store.clone_workflow(
            workflow_id=template_workflow_id,
            target_agent_id=agent_id,
            new_name=name or None,
        )
        await ks.save_active_workflow(agent_id, new_wf.workflow_id)

        await _notify("workflow_created", {
            "workflow_id": new_wf.workflow_id,
            "name": new_wf.name,
            "source": "template",
        })

        return json.dumps({
            "workflow_id": new_wf.workflow_id,
            "name": new_wf.name,
            "status": "imported",
            "message": f"Template import edildi: '{new_wf.name}'",
        }, ensure_ascii=False)

    # ── Existing tools (updated with optional workflow_id) ───────

    @tool
    async def list_node_types() -> str:
        """Mevcut workflow node tiplerini listele. Her tip icin: id, label, aciklama, parametreler, portlar."""
        from ..workflow import nodes as _  # noqa: F401
        from ..workflow.node_registry import get_catalog
        catalog = get_catalog()
        items = [m.to_dict() for m in catalog]
        return json.dumps(items, ensure_ascii=False, indent=2)

    @tool
    async def get_workflow(workflow_id: str = "") -> str:
        """Workflow DSL'ini getir (node'lar, edge'ler, durum).

        Args:
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        wf = await _resolve_workflow(workflow_id or None)
        return json.dumps({
            "workflow_id": wf.workflow_id,
            "name": wf.name,
            "version": wf.version,
            "status": wf.status.value if hasattr(wf.status, 'value') else wf.status,
            "dsl": wf.dsl.model_dump(),
        }, ensure_ascii=False, indent=2)

    @tool
    async def add_node(type_id: str, params: dict | None = None, label: str = "", position_x: float = 0, position_y: float = 0, workflow_id: str = "") -> str:
        """Workflow'a yeni bir node ekle.

        Args:
            type_id: Node tipi (list_node_types'tan alinir)
            params: Node parametreleri (opsiyonel)
            label: Goruntuleme etiketi (opsiyonel)
            position_x: Canvas X pozisyonu
            position_y: Canvas Y pozisyonu
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        from ..workflow.models import NodeDSL, Position, WorkflowDSL
        from ..workflow.node_registry import get_node_class

        try:
            get_node_class(type_id)
        except KeyError:
            return json.dumps({"error": f"Bilinmeyen node tipi: {type_id}. list_node_types ile mevcut tipleri kontrol edin."})

        wf = await _resolve_workflow(workflow_id or None)
        store = await _get_store()
        dsl = wf.dsl

        node_id = _gen_node_id(type_id)
        new_node = NodeDSL(
            id=node_id,
            type=type_id,
            label=label or type_id,
            params=params or {},
            position=Position(x=position_x, y=position_y),
        )

        nodes = list(dsl.nodes) + [new_node]
        new_dsl = WorkflowDSL(nodes=nodes, edges=list(dsl.edges))

        await store.save_dsl(wf.workflow_id, new_dsl)
        await _notify("workflow_changed", {
            "workflow_id": wf.workflow_id,
            "action": "add_node",
            "node_id": node_id,
            "type_id": type_id,
        })

        return json.dumps({
            "node_id": node_id,
            "type": type_id,
            "status": "added",
            "total_nodes": len(nodes),
        }, ensure_ascii=False)

    @tool
    async def connect_nodes(from_node: str, to_node: str, from_port: str = "out", to_port: str = "in", condition: str = "", workflow_id: str = "") -> str:
        """Iki node'u bir edge ile bagla.

        Args:
            from_node: Kaynak node ID
            to_node: Hedef node ID
            from_port: Cikis portu adi (varsayilan: node tipine gore ilk cikis)
            to_port: Giris portu adi (varsayilan: node tipine gore ilk giris)
            condition: Kosullu edge (ornegin quality_gate icin 'pass' veya 'fail')
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        from ..workflow.models import EdgeDSL, WorkflowDSL

        wf = await _resolve_workflow(workflow_id or None)
        store = await _get_store()
        dsl = wf.dsl

        if from_node not in dsl.node_ids:
            return json.dumps({"error": f"Kaynak node '{from_node}' bulunamadi"})
        if to_node not in dsl.node_ids:
            return json.dumps({"error": f"Hedef node '{to_node}' bulunamadi"})

        src_node = dsl.get_node(from_node)
        dst_node = dsl.get_node(to_node)

        from ..workflow.node_registry import get_node_class
        if from_port == "out" and src_node:
            src_cls = get_node_class(src_node.type)
            ports = src_cls.output_ports()
            if ports and ports[0].name != "out":
                from_port = ports[0].name

        if to_port == "in" and dst_node:
            dst_cls = get_node_class(dst_node.type)
            ports = dst_cls.input_ports()
            if ports and ports[0].name != "in":
                to_port = ports[0].name

        new_edge = EdgeDSL(
            from_node=from_node,
            from_port=from_port,
            to_node=to_node,
            to_port=to_port,
            condition=condition or None,
        )

        edges = list(dsl.edges) + [new_edge]
        try:
            new_dsl = WorkflowDSL(nodes=list(dsl.nodes), edges=edges)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        await store.save_dsl(wf.workflow_id, new_dsl)
        await _notify("workflow_changed", {
            "workflow_id": wf.workflow_id,
            "action": "connect",
            "from_node": from_node,
            "to_node": to_node,
        })

        return json.dumps({
            "status": "connected",
            "from": from_node,
            "to": to_node,
            "total_edges": len(edges),
        }, ensure_ascii=False)

    @tool
    async def configure_node(node_id: str, params: dict, workflow_id: str = "") -> str:
        """Bir node'un parametrelerini guncelle.

        Args:
            node_id: Guncellenecek node ID
            params: Yeni parametre degerleri (mevcut parametrelerle merge edilir)
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        wf = await _resolve_workflow(workflow_id or None)
        store = await _get_store()
        dsl = wf.dsl

        node = dsl.get_node(node_id)
        if not node:
            return json.dumps({"error": f"Node '{node_id}' bulunamadi"})

        node.params.update(params)
        await store.save_dsl(wf.workflow_id, dsl)
        await _notify("workflow_changed", {
            "workflow_id": wf.workflow_id,
            "action": "configure",
            "node_id": node_id,
        })

        return json.dumps({
            "node_id": node_id,
            "status": "configured",
            "params": node.params,
        }, ensure_ascii=False)

    @tool
    async def remove_node(node_id: str, workflow_id: str = "") -> str:
        """Workflow'dan bir node'u ve ilgili edge'leri kaldir.

        Args:
            node_id: Kaldirilacak node ID
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        from ..workflow.models import WorkflowDSL

        wf = await _resolve_workflow(workflow_id or None)
        store = await _get_store()
        dsl = wf.dsl

        if node_id not in dsl.node_ids:
            return json.dumps({"error": f"Node '{node_id}' bulunamadi"})

        nodes = [n for n in dsl.nodes if n.id != node_id]
        edges = [e for e in dsl.edges if e.from_node != node_id and e.to_node != node_id]
        new_dsl = WorkflowDSL(nodes=nodes, edges=edges)

        await store.save_dsl(wf.workflow_id, new_dsl)
        await _notify("workflow_changed", {
            "workflow_id": wf.workflow_id,
            "action": "remove_node",
            "node_id": node_id,
        })

        return json.dumps({
            "status": "removed",
            "node_id": node_id,
            "remaining_nodes": len(nodes),
        }, ensure_ascii=False)

    @tool
    async def validate_workflow(workflow_id: str = "") -> str:
        """Workflow'un yapisal ve semantik gecerliligini kontrol et.

        Args:
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        from ..workflow.node_registry import get_node_class

        wf = await _resolve_workflow(workflow_id or None)
        dsl = wf.dsl

        issues: list[str] = []

        if not dsl.nodes:
            issues.append("Workflow bos — en az 1 node ekleyin.")
            return json.dumps({"valid": False, "issues": issues}, ensure_ascii=False)

        for node in dsl.nodes:
            try:
                get_node_class(node.type)
            except KeyError:
                issues.append(f"Node '{node.id}': bilinmeyen tip '{node.type}'")

        try:
            dsl.topological_order()
        except ValueError as exc:
            issues.append(f"Dongu tespit edildi: {exc}")

        for node in dsl.nodes:
            try:
                cls = get_node_class(node.type)
                required_inputs = [p for p in cls.input_ports() if p.required]
                connected_inputs = {e.to_port for e in dsl.edges if e.to_node == node.id}
                for port in required_inputs:
                    if port.name not in connected_inputs:
                        issues.append(f"Node '{node.id}' ({node.type}): zorunlu giris '{port.name}' baglanmamis")
            except KeyError:
                pass

        valid = len(issues) == 0
        return json.dumps({
            "valid": valid,
            "node_count": len(dsl.nodes),
            "edge_count": len(dsl.edges),
            "issues": issues,
        }, ensure_ascii=False)

    @tool
    async def run_test_workflow(file_path: str = "", workflow_id: str = "") -> str:
        """Workflow'u tek bir dosya uzerinde test et (hizli dogrulama).

        Args:
            file_path: Test edilecek dosya yolu (bos birakildiysa ilk resource kullanilir)
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        from ..workflow.runtime import WorkflowRuntime

        wf = await _resolve_workflow(workflow_id or None)

        rt = WorkflowRuntime(pg=pg, notification_mgr=notification_mgr, celery_app=celery_app)
        inputs: dict[str, Any] = {}
        if file_path:
            inputs["test_file"] = file_path

        try:
            summary = await rt.start_run(
                agent_id=agent_id,
                workflow_id=wf.workflow_id,
                mode="test_one",
                inputs=inputs,
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

        return json.dumps(summary, ensure_ascii=False, default=str)

    @tool
    async def run_full_workflow(file_paths: list[str] | None = None, workflow_id: str = "") -> str:
        """Workflow'u tum dosyalar icin calistir (batch mode).

        Args:
            file_paths: Islenecek dosya yollari (bos birakildiysa tum kaynaklar kullanilir)
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        from ..workflow.runtime import WorkflowRuntime

        wf = await _resolve_workflow(workflow_id or None)

        rt = WorkflowRuntime(pg=pg, notification_mgr=notification_mgr, celery_app=celery_app)
        inputs: dict[str, Any] = {}
        if file_paths:
            inputs["file_paths"] = file_paths

        try:
            summary = await rt.start_run(
                agent_id=agent_id,
                workflow_id=wf.workflow_id,
                mode="full_batch",
                inputs=inputs,
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

        return json.dumps(summary, ensure_ascii=False, default=str)

    @tool
    async def publish_workflow(workflow_id: str = "") -> str:
        """Workflow'u yayinla — versiyonu dondur, GraphRAG endpoint'i olustur.

        Args:
            workflow_id: Hedef workflow ID (bos birakildiysa aktif workflow)
        """
        wf = await _resolve_workflow(workflow_id or None)
        store = await _get_store()

        issues_str = await validate_workflow.ainvoke({"workflow_id": wf.workflow_id})
        issues_data = json.loads(issues_str)
        if not issues_data.get("valid"):
            return json.dumps({
                "error": "Workflow gecerli degil, once sorunlari cozun.",
                "issues": issues_data.get("issues", []),
            }, ensure_ascii=False)

        new_version = await store.publish(wf.workflow_id)

        await _notify("workflow_published", {
            "workflow_id": wf.workflow_id,
            "version": new_version,
        })

        return json.dumps({
            "status": "published",
            "workflow_id": wf.workflow_id,
            "version": new_version,
            "message": f"Workflow v{new_version} olarak yayinlandi.",
        }, ensure_ascii=False)

    return [
        # Multi-workflow management
        create_workflow,
        list_workflows,
        switch_workflow,
        rename_workflow,
        delete_workflow,
        share_as_template,
        import_template,
        # Node/edge operations
        list_node_types,
        get_workflow,
        add_node,
        connect_nodes,
        configure_node,
        remove_node,
        validate_workflow,
        run_test_workflow,
        run_full_workflow,
        publish_workflow,
    ]
