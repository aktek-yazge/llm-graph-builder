"""
Agent Creator Assistant
=======================

Meta-agent that helps users create expert agents through a conversational interface.
Analyzes workspace KBs, generates system prompts, suggests tools, and deploys agents.
"""

import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..chat_agent_repository import ChatAgentRepository
from ..comms_repository import CommsRepository
from ..event_store.postgres_client import PostgresClient, get_postgres_client
from ..gateway import get_gateway_client
from ..workspace_repository import WorkspaceRepository

logger = logging.getLogger(__name__)

CREATOR_SYSTEM_PROMPT = """Sen bir Agent Yaratma Asistanisin. Kullanicilarin uzman agentlar olusturmasina yardimci oluyorsun.

Gorevlerin:
1. Kullanicinin sectiği workspace'leri analiz et (entity type'lar, relationship'ler, belge istatistikleri)
2. Domain-specific system prompt oner
3. Uygun skill ve tool set'i oner
4. Kullanicinin degisiklik yapmesina izin ver
5. Agent'i olustur ve deploy et

Onemli kurallar:
- Her zaman Turkce yanit ver (teknik terimler haric)
- Workspace analizi sirasinda entity type'lari ve relationship'leri acikla
- System prompt onerisini acik ve net yap
- Kullanicinin her asamada mudahale edebilecegini hatirla
- Oneri yaparken kisa ve ozenli ol"""


class AgentCreatorAssistant:
    """Helps users create agents through workspace analysis and conversational guidance."""

    def __init__(self, pg: PostgresClient):
        self._pg = pg
        self._ws_repo = WorkspaceRepository(pg)
        self._agent_repo = ChatAgentRepository(pg)

    @classmethod
    async def create(cls) -> "AgentCreatorAssistant":
        pg = await get_postgres_client()
        return cls(pg)

    async def analyze_workspace(self, workspace_id: str) -> Dict[str, Any]:
        """Analyze a workspace's KB: entities, relationships, documents."""
        ws = await self._ws_repo.get_workspace(workspace_id)
        if not ws:
            return {"error": f"Workspace '{workspace_id}' not found"}

        entities = await self._ws_repo.get_workspace_entity_schemas(workspace_id)
        relationships = await self._ws_repo.get_workspace_relationship_schemas(workspace_id)

        doc_stats = await self._ws_repo.get_document_stats(workspace_id)

        return {
            "workspace": {
                "id": ws["id"],
                "name": ws["name"],
                "description": ws.get("description", ""),
                "status": ws["status"],
                "document_count": ws.get("document_count", 0),
                "sample_count": ws.get("sample_count", 0),
            },
            "entities": [
                {
                    "type": e.get("entity_type", ""),
                    "description": e.get("description", ""),
                    "properties": e.get("properties") or {},
                    "examples": e.get("examples", ""),
                }
                for e in entities
            ],
            "relationships": [
                {
                    "type": r.get("relationship_type", ""),
                    "description": r.get("description", ""),
                    "source": r.get("source_entity", ""),
                    "target": r.get("target_entity", ""),
                    "cardinality": r.get("cardinality", ""),
                }
                for r in relationships
            ],
            "document_stats": doc_stats,
        }

    async def analyze_multiple_workspaces(
        self, workspace_ids: List[str]
    ) -> Dict[str, Any]:
        """Analyze multiple workspaces for a cross-domain agent."""
        analyses = {}
        all_entities = []
        all_relationships = []

        for ws_id in workspace_ids:
            analysis = await self.analyze_workspace(ws_id)
            if "error" not in analysis:
                analyses[ws_id] = analysis
                all_entities.extend(analysis["entities"])
                all_relationships.extend(analysis["relationships"])

        return {
            "workspace_count": len(analyses),
            "workspaces": analyses,
            "total_entities": len(all_entities),
            "total_relationships": len(all_relationships),
            "entity_types": list(set(e["type"] for e in all_entities)),
            "relationship_types": list(set(r["type"] for r in all_relationships)),
        }

    def generate_system_prompt(
        self,
        agent_name: str,
        domain: str,
        capabilities: List[str],
        entity_types: List[str],
        tone: str = "professional",
    ) -> str:
        """Generate a domain-specific system prompt with deep research methodology."""
        entities_str = ", ".join(entity_types[:20]) if entity_types else "genel bilgi"
        caps_str = "\n".join(f"- {c}" for c in capabilities) if capabilities else "- Genel sorgulama"

        tone_map = {
            "professional": "resmi ve profesyonel bir dille",
            "friendly": "samimi ve anlasilir bir dille",
            "technical": "teknik ve detayli bir dille",
            "concise": "kisa ve ozenli bir dille",
        }
        tone_desc = tone_map.get(tone, tone_map["professional"])

        base_prompt = f"""Sen {agent_name} adinda bir uzman agentsin. {domain} alaninda derinlemesine bilgi sahibisin.

Bilgi tabanindaki entity tipleri: {entities_str}

Yeteneklerin:
{caps_str}

Yanitlama kurallari:
- Her zaman {tone_desc} yanit ver
- Yanitlarini bilgi tabanindaki verilere dayandir
- Emin olmadigin konularda bunu belirt
- Kaynaklarini belirt (hangi belgeden, hangi entity'den geldi)"""

        from .deep_research_prompts import build_deep_research_prompt
        deep_research = build_deep_research_prompt(
            domain_context=base_prompt,
            entity_types=entity_types,
        )
        return deep_research

    def suggest_tools(
        self, entity_types: List[str], relationship_types: List[str]
    ) -> List[str]:
        """Suggest appropriate tools based on workspace content."""
        tools = [
            "neo4j_read_cypher",
        ]

        if entity_types:
            tools.append("search_entities")

        if relationship_types:
            tools.append("explore_relationships")

        tools.extend([
            "storage_browse_files",
            "storage_read_file",
        ])

        return tools

    async def create_and_deploy_agent(
        self,
        name: str,
        description: str,
        workspace_ids: List[str],
        system_prompt: str,
        agent_type: str = "expert",
        tenant_id: str = "default",
        tags: Optional[List[str]] = None,
        connected_agent_ids: Optional[List[str]] = None,
        auto_deploy: bool = True,
        auto_register_a2a: bool = True,
    ) -> Dict[str, Any]:
        """Create agent, deploy to ContextForge, and optionally register as A2A."""
        agent_row = await self._agent_repo.create(
            name=name,
            description=description,
            tenant_id=tenant_id,
            workspace_ids=workspace_ids,
            workspace_id=workspace_ids[0] if workspace_ids else None,
            agent_type=agent_type,
            system_prompt=system_prompt,
            tags=tags or [],
            connected_agent_ids=connected_agent_ids or [],
        )
        agent_id = str(agent_row["id"])
        result = {"agent_id": agent_id, "name": name, "status": "created"}

        if auto_deploy:
            try:
                gw = await get_gateway_client()
                vs_result = await gw.create_virtual_server(
                    name=name,
                    description=description,
                    tags=tags or [],
                )
                gw_id = vs_result.get("id", vs_result.get("server", {}).get("id", ""))
                await self._agent_repo.set_gateway_server_id(agent_id, gw_id)
                result["gateway_server_id"] = gw_id
                result["status"] = "active"
                result["mcp_endpoint"] = gw.get_server_mcp_endpoint(gw_id)

                if auto_register_a2a:
                    try:
                        a2a_endpoint = f"http://localhost:8000/api/v2/chat-agents/{agent_id}/a2a"
                        a2a_result = await gw.register_a2a_agent(
                            name=name,
                            endpoint_url=a2a_endpoint,
                            agent_type=agent_type,
                            description=description,
                            tags=tags or [],
                        )
                        a2a_id = a2a_result.get("id", "")
                        await self._agent_repo.set_a2a_agent_id(agent_id, a2a_id)
                        result["a2a_agent_id"] = a2a_id
                    except Exception as e:
                        logger.warning("A2A registration failed: %s", e)
                        result["a2a_error"] = str(e)

            except Exception as e:
                logger.error("Deploy failed for agent %s: %s", agent_id, e)
                await self._agent_repo.update(agent_id, status="error")
                result["status"] = "error"
                result["deploy_error"] = str(e)

        return result

    async def stream_creator_chat(
        self,
        message: str,
        session_state: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process a message in the agent creation conversation.
        Uses session_state to track the wizard progress.
        """
        step = session_state.get("step", "init")
        workspace_ids = session_state.get("workspace_ids", [])

        if step == "init" and workspace_ids:
            yield {"type": "status", "content": "Workspace'ler analiz ediliyor..."}
            analysis = await self.analyze_multiple_workspaces(workspace_ids)
            session_state["analysis"] = analysis

            summary_parts = []
            for ws_id, ws_data in analysis.get("workspaces", {}).items():
                ws_info = ws_data["workspace"]
                summary_parts.append(
                    f"**{ws_info['name']}**: {len(ws_data['entities'])} entity type, "
                    f"{len(ws_data['relationships'])} relationship type, "
                    f"{ws_info['document_count']} belge"
                )

            entity_types = analysis.get("entity_types", [])
            rel_types = analysis.get("relationship_types", [])

            response = f"""Workspace analizi tamamlandi:

{chr(10).join(f"- {p}" for p in summary_parts)}

**Entity type'lari**: {', '.join(entity_types[:15]) if entity_types else 'Henuz tanimlanmamis'}
**Relationship type'lari**: {', '.join(rel_types[:10]) if rel_types else 'Henuz tanimlanmamis'}

Simdi agent'iniz icin su bilgilere ihtiyacim var:
1. Agent adi
2. Hangi yeteneklere sahip olmali? (ornegin: analiz, raporlama, karsilastirma)
3. Hangi tonda yanitlamali? (professional, friendly, technical, concise)

Bunlari yazabilir veya onerilerimi kullanabilirsiniz."""

            session_state["step"] = "configure"
            yield {"type": "message", "content": response}
            return

        if step == "configure":
            agent_name = session_state.get("agent_name", "")
            if not agent_name:
                if any(kw in message.lower() for kw in ["agent", "adi", "ismi", "adı", "isim"]):
                    words = message.split(":")
                    if len(words) > 1:
                        agent_name = words[-1].strip().strip('"\'')
                    else:
                        agent_name = message.strip().strip('"\'')
                    session_state["agent_name"] = agent_name

            analysis = session_state.get("analysis", {})
            entity_types = analysis.get("entity_types", [])
            rel_types = analysis.get("relationship_types", [])

            capabilities = []
            for kw in ["analiz", "rapor", "karsilastir", "ozetle", "sorgula", "arama", "hesapla"]:
                if kw in message.lower():
                    capabilities.append(kw + " yapma")
            if not capabilities:
                capabilities = ["bilgi sorgulama", "veri analizi", "raporlama"]

            tone = "professional"
            for t in ["friendly", "technical", "concise", "samimi", "teknik", "kisa"]:
                if t in message.lower():
                    tone = {"samimi": "friendly", "teknik": "technical", "kisa": "concise"}.get(t, t)

            if not agent_name:
                ws_names = [
                    wd["workspace"]["name"]
                    for wd in analysis.get("workspaces", {}).values()
                ]
                agent_name = f"{ws_names[0]} Uzmani" if ws_names else "Uzman Agent"
                session_state["agent_name"] = agent_name

            system_prompt = self.generate_system_prompt(
                agent_name=agent_name,
                domain=", ".join(
                    wd["workspace"]["name"]
                    for wd in analysis.get("workspaces", {}).values()
                ),
                capabilities=capabilities,
                entity_types=entity_types,
                tone=tone,
            )
            suggested_tools = self.suggest_tools(entity_types, rel_types)

            session_state["system_prompt"] = system_prompt
            session_state["capabilities"] = capabilities
            session_state["tools"] = suggested_tools
            session_state["tone"] = tone
            session_state["step"] = "review"

            response = f"""Agent konfigurasyonu hazir:

**Agent Adi**: {agent_name}
**Tipi**: expert
**Yetenekler**: {', '.join(capabilities)}
**Ton**: {tone}
**Onerilen Tool'lar**: {', '.join(suggested_tools)}

**System Prompt Onerisi**:
```
{system_prompt}
```

Degisiklik yapmak ister misiniz? Yoksa "onayla" veya "deploy et" diyerek agent'i olusturabiliriz."""

            yield {"type": "message", "content": response}
            return

        if step == "review":
            if any(kw in message.lower() for kw in ["onayla", "deploy", "olustur", "evet", "tamam"]):
                yield {"type": "status", "content": "Agent olusturuluyor ve deploy ediliyor..."}

                try:
                    result = await self.create_and_deploy_agent(
                        name=session_state.get("agent_name", "Uzman Agent"),
                        description=f"Workspace-bagli uzman agent: {', '.join(workspace_ids)}",
                        workspace_ids=workspace_ids,
                        system_prompt=session_state.get("system_prompt", ""),
                        agent_type="expert",
                        tags=session_state.get("capabilities", []),
                    )

                    status_msg = "basariyla deploy edildi" if result["status"] == "active" else f"olusturuldu (durum: {result['status']})"
                    a2a_msg = ""
                    if result.get("a2a_agent_id"):
                        a2a_msg = f"\nContextForge A2A kaydi: {result['a2a_agent_id']}"

                    response = f"""Agent {status_msg}!

**Agent ID**: {result['agent_id']}
**Durum**: {result['status']}{a2a_msg}

Agent detay sayfasina giderek test edebilir, tool'lar ekleyebilir ve delegasyon ayarlarini yapabilirsiniz."""

                    session_state["step"] = "done"
                    session_state["agent_id"] = result["agent_id"]
                    yield {"type": "agent_created", "content": response, "agent_id": result["agent_id"]}

                except Exception as e:
                    yield {"type": "error", "content": f"Agent olusturma hatasi: {e}"}
                return

            session_state["step"] = "configure"
            yield {
                "type": "message",
                "content": "Tamam, degisikliklerinizi belirtin. Agent adi, yetenekler, ton veya system prompt'u degistirebiliriz.",
            }
            return

        yield {
            "type": "message",
            "content": "Agent yaratma islemi icin once workspace secimi yapilmali. Wizard'daki Step 1'den workspace secin.",
        }
