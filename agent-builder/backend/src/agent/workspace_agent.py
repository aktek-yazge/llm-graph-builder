"""
Workspace Agent
===============

Workspace'e yuklenen ornek belgelerden schema kesfeden LLM agent.

Akis:
1. Ornek belgeler yuklenir
2. Agent OCR + extraction yapar (ocr_bridge kullanarak)
3. Bulunan entity/relationship pattern'lerini analiz eder
4. Schema onerisi olusturur ve Ontology DB'ye kaydeder
5. Kullaniciya onay icin gosterir

Builder Agent'tan farki: Konusmaz, sadece analiz yapar ve sonuc dondurur.
Workspace UI'daki schema tablosu uzerinden kullanici onaylar.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from .ocr_bridge import OCRBridge
from ..ontology.neo4j_client import OntologyDBClient

logger = logging.getLogger(__name__)


SCHEMA_DISCOVERY_PROMPT = """Sen bir belge analiz asistanisin. Sana verilen OCR sonuclarindan:

1. Belgede bulunan entity (varlik) tiplerini tespit et
2. Entity'ler arasi iliskileri belirle
3. Her entity icin property'leri (ozellikler) cikar
4. Turkce karakter normalizasyonu uygula (ö->o, ü->u, ş->s, ç->c, ğ->g, ı->i)

CIKTINI ASAGIDAKI JSON FORMATINDA VER:

{{
  "entity_schemas": [
    {{
      "entity_type": "Policy",
      "description": "Sigorta policesi",
      "properties": {{
        "policy_no": {{"type": "string", "required": true, "description": "Police numarasi"}},
        "start_date": {{"type": "date", "required": true, "description": "Baslangic tarihi"}}
      }}
    }}
  ],
  "relationship_schemas": [
    {{
      "relationship_type": "HAS_POLICY",
      "source_entity": "Customer",
      "target_entity": "Policy",
      "description": "Musteri police sahibi",
      "cardinality": "one_to_many"
    }}
  ],
  "summary": "Kisa ozet: ne tur belgeler, kac farkli entity tipi bulundu"
}}

{additional_context}

BELGELER:
{documents_text}"""


class WorkspaceAgent:
    """
    Workspace icin schema kesif agent'i.

    Ornek belgelerden entity/relationship schema'larini
    otomatik kesfeder ve Ontology DB'ye kaydeder.
    """

    def __init__(self, db: OntologyDBClient, ocr_bridge: Optional[OCRBridge] = None, gateway=None):
        self.db = db
        self.ocr_bridge = ocr_bridge or OCRBridge()
        self._gateway = gateway

    async def _gw(self, tenant_id: str = "default-tenant"):
        if self._gateway:
            return self._gateway
        from ..dependencies import get_mutation_gateway_for_tenant
        return await get_mutation_gateway_for_tenant(tenant_id, self.db)

    async def _resolve_resource_id(self, workspace_id: str, pg) -> Optional[str]:
        from ..workspace_repository import WorkspaceRepository
        ws_repo = WorkspaceRepository(pg)
        ws = await ws_repo.get_workspace(workspace_id)
        rid = ws.get("resource_id", "") if ws else ""
        if rid:
            return rid
        row = await pg.fetchrow(
            "SELECT id FROM resources WHERE workspace_id = $1 ORDER BY updated_at DESC LIMIT 1",
            workspace_id,
        )
        return str(row["id"]) if row else None

    def _create_llm(self):
        provider = os.getenv("BUILDER_LLM_PROVIDER", "google").lower()
        model = os.getenv("BUILDER_LLM_MODEL", "gemini-2.5-flash")

        if provider in ("google", "gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=model, temperature=0.1)
        elif provider in ("openai", "gpt"):
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model, temperature=0.1)
        elif provider in ("anthropic", "claude"):
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model, temperature=0.1, max_tokens=8192)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    # =========================================================================
    # PHASE 1: EXTRACT SAMPLE TEXT (OCR only, no schema discovery)
    # =========================================================================

    async def extract_sample_text(
        self,
        workspace_id: str,
        sample_doc_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        LEGACY: Sample belgeleri OCR ile isle.
        Yeni akis mcp_infer_schema kullanir (MinIO + Gemini).
        """
        from ..event_store.postgres_client import get_postgres_client
        from ..resource_repository import ResourceRepository
        from ..workspace_repository import WorkspaceRepository

        pg = await get_postgres_client()
        repo = ResourceRepository(pg)
        ws_repo = WorkspaceRepository(pg)

        ws = await ws_repo.get_workspace(workspace_id)
        resource_id = ws.get("resource_id", "") if ws else ""
        if not resource_id:
            res_row = await pg.fetchrow(
                "SELECT id FROM resources WHERE workspace_id = $1 LIMIT 1", workspace_id,
            )
            resource_id = str(res_row["id"]) if res_row else ""

        if not resource_id:
            return {"error": "No resource found for workspace"}

        docs = await repo.list_documents(resource_id, limit=10)
        if not docs:
            return {"error": "No sample documents found"}

        results = []
        for doc in docs:
            fname = doc.get("file_name", "unknown")
            minio_key = doc.get("minio_key", "")
            doc_id = str(doc.get("id", ""))

            if not minio_key:
                results.append({"id": doc_id, "status": "no_minio_key", "chars": 0})
                continue

            try:
                from ..minio_client import download_file
                data = download_file(minio_key)
                import fitz
                pdf = fitz.open(stream=data, filetype="pdf")
                text = ""
                for page in pdf:
                    text += page.get_text()
                    if len(text) > 5000:
                        break
                pdf.close()
                results.append({
                    "id": doc_id, "file_name": fname,
                    "status": "ocr_completed", "chars": len(text),
                })
            except Exception as ex:
                logger.warning("PDF read failed for %s: %s", fname, ex)
                results.append({"id": doc_id, "status": "ocr_failed", "chars": 0})

        await ws_repo.update_workspace(workspace_id, {"status": "samples_ready"})

        return {
            "workspace_id": workspace_id,
            "status": "samples_ready",
            "documents": results,
            "total_chars": sum(r.get("chars", 0) for r in results),
        }

    # =========================================================================
    # PHASE 1.5: GET SAMPLE SUMMARY (for agent to show user)
    # =========================================================================

    async def get_sample_summary(
        self,
        workspace_id: str,
        max_chars_per_doc: int = 2000,
    ) -> Dict[str, Any]:
        """
        LEGACY: Sample belgelerinin metinlerinden ozet cikar.
        Yeni akis mcp_infer_schema kullanir.
        """
        from ..event_store.postgres_client import get_postgres_client
        from ..resource_repository import ResourceRepository

        pg = await get_postgres_client()
        repo = ResourceRepository(pg)

        resource_id = await self._resolve_resource_id(workspace_id, pg)
        if not resource_id:
            return {"error": "No resource found for workspace"}

        db_docs = await repo.list_documents(resource_id, limit=10)
        if not db_docs:
            return {"error": "No documents found. Upload documents first."}

        docs = []
        for d in db_docs:
            minio_key = d.get("minio_key", "")
            fname = d.get("file_name", "unknown")
            text = ""
            if minio_key:
                try:
                    from ..minio_client import download_file
                    data = download_file(minio_key)
                    import fitz
                    pdf = fitz.open(stream=data, filetype="pdf")
                    for page in pdf:
                        text += page.get_text()
                        if len(text) > max_chars_per_doc:
                            break
                    pdf.close()
                except Exception:
                    pass
            docs.append({"id": str(d["id"]), "file_name": fname, "ocr_text": text, "chars": len(text)})

        if not any(d["chars"] > 0 for d in docs):
            return {"error": "No readable text found in documents."}

        samples_text = "\n\n".join(
            f"--- {d['file_name']} ({d.get('chars', 0)} karakter) ---\n"
            f"{d['ocr_text'][:max_chars_per_doc]}"
            for d in docs
        )

        llm = self._create_llm()
        summary_prompt = (
            "Asagidaki belge(ler)in kisa bir ozetini cikar. "
            "Belgede ne tur bilgiler var, kac sayfa, hangi konular var? "
            "Turkce yaz. 3-5 cumle yeterli.\n\n"
            f"{samples_text[:6000]}"
        )
        response = await llm.ainvoke([HumanMessage(content=summary_prompt)])

        return {
            "workspace_id": workspace_id,
            "document_count": len(docs),
            "total_chars": sum(d.get("chars", 0) for d in docs),
            "summary": response.content,
            "documents": [
                {"id": d["id"], "file_name": d["file_name"], "chars": d.get("chars", 0)}
                for d in docs
            ],
        }

    # =========================================================================
    # PHASE 2: CREATE SKILL FROM CONVERSATION
    # =========================================================================

    async def create_skill_from_intent(
        self,
        workspace_id: str,
        user_intent: str,
        extraction_rules: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Kullanicinin amacina (intent) ve yonlendirmelerine gore
        skill + schema olusturur.

        Args:
            workspace_id: Workspace ID
            user_intent: Kullanicinin ne istedigini aciklayan metin
                         orn: "Aksa firmasi ile ilgili genel kurul kararlari"
            extraction_rules: Ek kurallar listesi
                         orn: ["Sadece Aksa ile ilgili haberler", "Tarih ve karar detaylari"]
        """
        from ..event_store.postgres_client import get_postgres_client as _pg
        from ..resource_repository import ResourceRepository as _RR
        _p = await _pg()
        _r = _RR(_p)
        _rid = await self._resolve_resource_id(workspace_id, _p)
        if not _rid:
            return {"error": "No resource found for workspace"}
        _db_docs = await _r.list_documents(_rid, limit=10)
        if not _db_docs:
            return {"error": "No documents found. Upload documents first."}

        docs = []
        for dd in _db_docs:
            mk = dd.get("minio_key", "")
            fn = dd.get("file_name", "unknown")
            txt = ""
            if mk:
                try:
                    from ..minio_client import download_file
                    data = download_file(mk)
                    import fitz
                    pdf = fitz.open(stream=data, filetype="pdf")
                    for page in pdf:
                        txt += page.get_text()
                        if len(txt) > 3000:
                            break
                    pdf.close()
                except Exception:
                    pass
            if txt:
                docs.append({"file_name": fn, "ocr_text": txt})

        if not docs:
            return {"error": "No readable text found in documents."}

        sample_text = "\n\n".join(
            f"--- {d['file_name']} ---\n{d['ocr_text'][:3000]}"
            for d in docs
        )

        rules_text = ""
        if extraction_rules:
            rules_text = "\n\nKULLANICI KURALLARI:\n" + "\n".join(f"- {r}" for r in extraction_rules)

        prompt = SCHEMA_DISCOVERY_PROMPT.format(
            documents_text=sample_text,
            additional_context=(
                f"\nKULLANICININ AMACI: {user_intent}\n"
                f"{rules_text}\n\n"
                "ONEMLI: Schema'yi SADECE kullanicinin amacina uygun entity ve "
                "relationship'ler icin olustur. Belgedeki tum bilgileri degil, "
                "sadece kullanicinin istedigi bilgileri hedefle.\n"
                + await self._get_existing_schema_context()
            ),
        )

        llm = self._create_llm()
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        schema_proposal = self._parse_schema_response(response.content)

        if "error" in schema_proposal:
            return schema_proposal

        saved = await self._save_schemas(workspace_id, schema_proposal)

        from ..event_store.postgres_client import get_postgres_client
        from ..workspace_repository import WorkspaceRepository
        pg = await get_postgres_client()
        ws_repo = WorkspaceRepository(pg)
        extraction_config = {
            "intent": user_intent,
            "extraction_rules": extraction_rules or [],
            "target_entities": [e["entity_type"] for e in saved["entity_schemas"]],
        }
        await ws_repo.update_workspace(workspace_id, {"status": "schema_review", "extraction_config": extraction_config})

        return {
            "workspace_id": workspace_id,
            "status": "schema_review",
            "intent": user_intent,
            "entity_schemas": saved["entity_schemas"],
            "relationship_schemas": saved["relationship_schemas"],
            "summary": schema_proposal.get("summary", ""),
        }

    # =========================================================================
    # PHASE 3: MCP-BACKED ANALYSIS (Sampling + Elicitation)
    # =========================================================================

    async def analyze_via_mcp(
        self,
        workspace_id: str,
        resource_id: str = "",
        domain_hint: str = "",
        max_samples: int = 5,
        auto_approve: bool = False,
    ) -> Dict[str, Any]:
        """
        Resource'taki ornek belgeleri LLM ile analiz ederek schema onerisi cikarir.

        Args:
            workspace_id: Workspace ID
            resource_id: Resource ID (yoksa workspace'in resource'u kullanilir)
            domain_hint: Domain ipucu (orn: 'sigorta policeleri')
            max_samples: Analiz edilecek ornek belge sayisi
            auto_approve: True ise kullanici onayini atla
        """
        from ..event_store.postgres_client import get_postgres_client
        from ..resource_repository import ResourceRepository

        pg = await get_postgres_client()
        repo = ResourceRepository(pg)

        if not resource_id:
            from ..workspace_repository import WorkspaceRepository
            ws_repo = WorkspaceRepository(pg)
            ws_data = await ws_repo.get_workspace(workspace_id)
            if ws_data and ws_data.get("resource_id"):
                resource_id = str(ws_data["resource_id"])

        if not resource_id:
            res_row = await pg.fetchrow(
                "SELECT id FROM resources WHERE workspace_id = $1 ORDER BY updated_at DESC LIMIT 1",
                workspace_id,
            )
            if res_row:
                resource_id = str(res_row["id"])

        if not resource_id:
            return {"error": "Workspace'e bagli resource bulunamadi"}

        docs = await repo.list_documents(resource_id, limit=max_samples)
        if not docs:
            return {"error": "Resource'ta belge bulunamadi. Lutfen once belge yukleyin."}

        sample_texts = []
        sample_names = []
        for doc in docs[:max_samples]:
            minio_path = doc.get("minio_key", "")
            fname = doc.get("file_name", "unknown")
            sample_names.append(fname)
            if minio_path:
                try:
                    from ..minio_client import download_file
                    data = download_file(minio_path)
                    import fitz
                    pdf = fitz.open(stream=data, filetype="pdf")
                    text = ""
                    for page in pdf:
                        text += page.get_text()
                        if len(text) > 3000:
                            break
                    pdf.close()
                    sample_texts.append(f"--- {fname} ---\n{text[:3000]}")
                except Exception as ex:
                    logger.warning("PDF read failed for %s: %s", fname, ex)
                    sample_texts.append(f"--- {fname} ---\n[Okunamadi: {ex}]")
            else:
                sample_texts.append(f"--- {fname} ---\n[MinIO yolu yok]")

        if not sample_texts:
            return {"error": "Hicbir belge okunamadi"}

        combined = "\n\n".join(sample_texts)

        prompt = f"""Asagidaki {len(sample_texts)} ornek belgeyi analiz et.
Domain ipucu: {domain_hint or 'Belirtilmedi'}

Bu belgelerden cikarilabilecek bir Knowledge Graph schema'si oner.

JSON formatinda donus yap:
{{
  "entity_schemas": [
    {{"entity_type": "...", "description": "...", "properties": {{"prop1": "string", "prop2": "date"}}}}
  ],
  "relationship_schemas": [
    {{"relationship_type": "...", "source_entity": "EntityA", "target_entity": "EntityB", "description": "...", "cardinality": "one_to_many"}}
  ],
  "domain": "...",
  "confidence": 0.0-1.0,
  "reasoning": "..."
}}

BELGELER:
{combined}"""

        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.1)
            response = await llm.ainvoke(prompt)
            content = response.content
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
        except Exception as e:
            logger.error("LLM schema inference failed: %s", e)
            return {"error": f"Schema analizi basarisiz: {e}"}

        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            try:
                proposal = json.loads(json_match.group())
            except json.JSONDecodeError:
                proposal = {}
        else:
            proposal = {}

        entity_schemas = proposal.get("entity_schemas", [])
        rel_schemas = proposal.get("relationship_schemas", [])

        if entity_schemas or rel_schemas:
            normalized = {
                "entity_schemas": [
                    {
                        "entity_type": e.get("entity_type", e.get("name", "")),
                        "description": e.get("description", ""),
                        "properties": e.get("properties", {}),
                    }
                    for e in entity_schemas
                ],
                "relationship_schemas": [
                    {
                        "relationship_type": r.get("relationship_type", r.get("name", "")),
                        "source_entity": r.get("source_entity", r.get("source", "")),
                        "target_entity": r.get("target_entity", r.get("target", "")),
                        "description": r.get("description", ""),
                        "cardinality": r.get("cardinality", "one_to_many"),
                    }
                    for r in rel_schemas
                ],
            }

            saved = await self._save_schemas(workspace_id, normalized)

            return {
                "workspace_id": workspace_id,
                "status": "schema_proposed" if not auto_approve else "schema_approved",
                "source": "local_llm",
                "entity_schemas": saved["entity_schemas"],
                "relationship_schemas": saved["relationship_schemas"],
                "domain": proposal.get("domain", domain_hint),
                "confidence": proposal.get("confidence", 0.0),
                "reasoning": proposal.get("reasoning", ""),
                "samples_analyzed": len(sample_texts),
                "sample_files": sample_names,
            }

        return {
            "workspace_id": workspace_id,
            "status": "no_schema_found",
            "raw_proposal": content,
        }

    # =========================================================================
    # PHASE 4: CREATE KB AGENT FROM APPROVED SCHEMA
    # =========================================================================

    async def create_kb_agent(
        self,
        workspace_id: str,
        tenant_id: str = "default-tenant",
        auto_deploy: bool = False,
        custom_name: str = "",
        custom_purpose: str = "",
        minio_bucket: str = "",
        minio_prefix: str = "",
    ) -> Dict[str, Any]:
        """
        Onaylanmis workspace schema'sindan KB Agent olustur.

        Schema onaylandiktan sonra cagrilir. Otomatik olarak:
        - Extraction skill olusturur (schema bilgisi ile)
        - Goal olusturur
        - AgentDefinition olusturur
        - Hepsini birbirine baglar
        - Opsiyonel: Gateway'e deploy eder

        Args:
            workspace_id: Workspace ID
            tenant_id: Tenant ID
            auto_deploy: True ise Gateway'e otomatik deploy et
            custom_name: Ozel agent adi (bos ise otomatik olusturulur)
            custom_purpose: Ozel amac aciklamasi
            minio_bucket: Belgelerin bulundugu MinIO bucket
            minio_prefix: MinIO prefix
        """
        from .kb_agent_factory import KBAgentFactory
        from ..event_store.postgres_client import get_postgres_client
        from ..agent_repository import AgentRepository
        from ..workspace_repository import WorkspaceRepository
        pg = await get_postgres_client()
        agent_repo = AgentRepository(pg)
        ws_repo = WorkspaceRepository(pg)
        factory = KBAgentFactory(agent_repo, ws_repo)
        return await factory.create_from_workspace(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            auto_deploy=auto_deploy,
            custom_name=custom_name,
            custom_purpose=custom_purpose,
            minio_bucket=minio_bucket,
            minio_prefix=minio_prefix,
        )

    # =========================================================================
    # LEGACY: analyze_samples (backward compat - calls extract + create)
    # =========================================================================

    async def analyze_samples(
        self,
        workspace_id: str,
        sample_doc_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Legacy: tek adimda OCR + schema kesfet (geriye uyumluluk)."""
        extract_result = await self.extract_sample_text(workspace_id, sample_doc_ids)
        if "error" in extract_result:
            return extract_result

        return await self.create_skill_from_intent(
            workspace_id,
            user_intent="Belgedeki tum entity ve relationship'leri kesfet",
        )

    # =========================================================================
    # INTERNALS
    # =========================================================================

    async def _get_existing_schema_context(self) -> str:
        """Mevcut Knowledge DB semasini context olarak hazirla."""
        try:
            labels = await self.db.execute_query("""
                CALL db.labels() YIELD label
                RETURN collect(label) AS labels
            """)
            rel_types = await self.db.execute_query("""
                CALL db.relationshipTypes() YIELD relationshipType
                RETURN collect(relationshipType) AS types
            """)

            existing_labels = labels[0]["labels"] if labels else []
            existing_rels = rel_types[0]["types"] if rel_types else []

            if existing_labels or existing_rels:
                return (
                    f"\nMEVCUT KNOWLEDGE DB SEMASI (duplikasyondan kacin):\n"
                    f"Label'lar: {', '.join(existing_labels[:30])}\n"
                    f"Iliski tipleri: {', '.join(existing_rels[:30])}\n"
                )
        except Exception:
            pass
        return ""

    def _parse_schema_response(self, content: str) -> Dict[str, Any]:
        """LLM yanitindan JSON schema cikar."""
        text = content.strip()

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass

            return {"error": "Could not parse schema from LLM response", "raw": content[:500]}

    async def _save_schemas(
        self,
        workspace_id: str,
        proposal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Schema'lari Ontology DB'ye kaydet ve workspace'e bagla (MutationGateway ile)."""
        gw = await self._gw()
        saved_entities = []
        saved_relationships = []

        for es in proposal.get("entity_schemas", []):
            es_id = f"es-{uuid.uuid4().hex[:12]}"
            entity_type = es.get("entity_type", "Unknown")
            description = es.get("description", "")
            properties = es.get("properties", {})

            await gw.create_node("EntitySchema", {
                "id": es_id,
                "entity_type": entity_type,
                "description": description,
                "properties": json.dumps(properties, ensure_ascii=False),
                "context": "workspace",
            }, metadata={"source": "workspace_agent", "workspace_id": workspace_id})

            await gw.create_relationship(
                "Workspace", workspace_id, "EntitySchema", es_id, "HAS_SCHEMA",
                metadata={"source": "workspace_agent"},
            )

            saved_entities.append({
                "id": es_id,
                "entity_type": entity_type,
                "description": description,
                "properties": properties,
            })

        for rs in proposal.get("relationship_schemas", []):
            rs_id = f"rs-{uuid.uuid4().hex[:12]}"
            rel_type = rs.get("relationship_type", "RELATED_TO")

            await gw.create_node("RelationshipSchema", {
                "id": rs_id,
                "relationship_type": rel_type,
                "source_entity": rs.get("source_entity", ""),
                "target_entity": rs.get("target_entity", ""),
                "description": rs.get("description", ""),
                "cardinality": rs.get("cardinality", "one_to_many"),
            }, metadata={"source": "workspace_agent", "workspace_id": workspace_id})

            await gw.create_relationship(
                "Workspace", workspace_id, "RelationshipSchema", rs_id, "HAS_SCHEMA",
                metadata={"source": "workspace_agent"},
            )

            saved_relationships.append({
                "id": rs_id,
                "relationship_type": rel_type,
                "source_entity": rs.get("source_entity", ""),
                "target_entity": rs.get("target_entity", ""),
            })

        return {
            "entity_schemas": saved_entities,
            "relationship_schemas": saved_relationships,
        }
