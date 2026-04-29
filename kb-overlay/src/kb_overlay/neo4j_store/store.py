"""Neo4jStore — graph yazıcı/okuyucu.

Tasarım kararları:
  * Canonical entity'ler tek :Entity base label + (Company|Person|...) ek label
    olarak yazılır. Lookup/index :Entity üzerine kurulur, agent sorguları
    :Company gibi spesifik label kullanabilir.
  * İlişkilerin tipi (predicate) Cypher'da rel-type olarak yazılır; ama izinli
    bir tip listesi (whitelist) tutulur. Whitelist dışındaki predicate'ler
    `RELATED_TO` rel-type'ı altında `predicate` propertysi olarak yazılır.
  * Her ilişki en az şu provenance'ı taşır: source_doc_id, evidence_text,
    confidence, span_start, span_end, extracted_at.
  * `sync_from_alias_store(store)` ile SQLite master'dan tek yönlü senkronizasyon
    yapılır (canonical entity'ler + opsiyonel embedding).
  * `neo4j` paketi import zamanında değil, runtime'da yüklenir; opsiyonel kalır.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from ..confidence import ConfidenceLabel
from ..dictionary import AliasStore, EntityRecord

if TYPE_CHECKING:  # pragma: no cover
    from neo4j import Driver, Session

logger = logging.getLogger(__name__)


# Whitelist: bu predicate'ler kendi rel-type'ı olarak yazılır.
# Diğerleri `RELATED_TO {predicate: "..."}` olarak yazılır.
ALLOWED_REL_TYPES: frozenset[str] = frozenset({
    "WORKS_AT", "CEO_OF", "CFO_OF", "CTO_OF", "CHAIRMAN_OF", "BOARD_MEMBER_OF",
    "FOUNDER_OF", "OWNER_OF", "SHAREHOLDER_OF",
    "SUBSIDIARY_OF", "PARENT_OF", "PARTNER_OF", "ACQUIRED_BY", "MERGED_WITH",
    "LOCATED_IN", "HEADQUARTERED_IN",
    "MENTIONED_IN",  # entity → document
})


def _safe_rel_type(predicate: str) -> tuple[str, Optional[str]]:
    """Predicate → (rel_type, original_predicate_or_None).

    Whitelist'teyse direkt kullanılır; değilse RELATED_TO altına gizlenir
    ve original predicate property olarak döner.
    """
    norm = predicate.strip().upper().replace(" ", "_").replace("-", "_")
    if norm in ALLOWED_REL_TYPES:
        return norm, None
    return "RELATED_TO", predicate.strip()


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "neo4j"
    database: str = "neo4j"
    vector_dim: int = 768
    create_vector_index: bool = True


@dataclass(slots=True)
class GraphRelation:
    """Bir ilişki kaydı — extractor → writer arası taşıyıcı.

    ``confidence_label`` numeric ``confidence`` ile birlikte kullanılır;
    Neo4j edge property olarak yazılır (downstream agent / reporting bu label'a
    göre filtre yapabilir).
    """

    subject_id: str
    predicate: str
    object_id: str
    source_doc_id: str
    evidence_text: str
    confidence: float = 0.6
    confidence_label: ConfidenceLabel = ConfidenceLabel.INFERRED
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------


def _load_schema_cypher() -> str:
    return Path(__file__).with_name("schema.cypher").read_text(encoding="utf-8")


def _label_for_type(entity_type: str) -> str:
    return {
        "company": "Company",
        "person": "Person",
        "organization": "Organization",
        "location": "Location",
        "product": "Product",
    }.get(entity_type, "Other")


class Neo4jStore:
    """Canonical entity + relation graph writer/reader.

    Kullanım:
        cfg = Neo4jConfig(uri="bolt://localhost:7687", password="...")
        with Neo4jStore(cfg) as graph:
            graph.init_schema()
            graph.upsert_entity(entity_record)
            graph.upsert_relation(GraphRelation(...))
    """

    def __init__(self, config: Neo4jConfig):
        try:
            from neo4j import GraphDatabase  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "neo4j driver yok. `pip install 'kb-overlay[neo4j]'` veya `pip install neo4j`."
            ) from exc
        from neo4j import GraphDatabase

        self.config = config
        self._driver: "Driver" = GraphDatabase.driver(
            config.uri, auth=(config.user, config.password)
        )

    # ------------------------------------------------------------------ infra

    def __enter__(self) -> "Neo4jStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()

    def _session(self) -> "Session":
        return self._driver.session(database=self.config.database)

    def ping(self) -> bool:
        with self._session() as s:
            return s.run("RETURN 1 AS ok").single()["ok"] == 1

    # ------------------------------------------------------------------ schema

    def init_schema(self) -> None:
        """Constraint + index'leri uygular. Vector index dinamik boyut alır."""
        cypher = _load_schema_cypher()
        with self._session() as s:
            for stmt in self._split_statements(cypher):
                if not stmt.strip():
                    continue
                s.run(stmt)

            if self.config.create_vector_index:
                # Vector index dinamik boyutla oluşturulur (string format)
                vec_stmt = f"""
                CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
                  FOR (e:Entity) ON (e.embedding)
                  OPTIONS {{indexConfig: {{
                    `vector.dimensions`: {int(self.config.vector_dim)},
                    `vector.similarity_function`: 'cosine'
                  }}}}
                """
                try:
                    s.run(vec_stmt)
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "Vector index oluşturulamadı (Neo4j 5.13+ gerekli olabilir): %s", exc
                    )

    @staticmethod
    def _split_statements(cypher: str) -> list[str]:
        """Çoklu Cypher statement'ları noktalı virgülle ayır (yorumları atla)."""
        out: list[str] = []
        buf: list[str] = []
        for raw_line in cypher.splitlines():
            line = raw_line.split("//", 1)[0].rstrip()
            if not line.strip():
                if buf and "".join(buf).strip().endswith(";"):
                    stmt = "\n".join(buf).strip().rstrip(";").strip()
                    if stmt:
                        out.append(stmt)
                    buf = []
                continue
            buf.append(line)
            if line.rstrip().endswith(";"):
                stmt = "\n".join(buf).strip().rstrip(";").strip()
                if stmt:
                    out.append(stmt)
                buf = []
        if buf:
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
        return out

    # ------------------------------------------------------------------ entity upsert

    def upsert_entity(
        self,
        entity: EntityRecord,
        *,
        embedding: Optional[list[float]] = None,
        aliases: Optional[Iterable[str]] = None,
    ) -> None:
        """Canonical entity MERGE eder. Aliases array property olarak yazılır
        (Neo4j ana property index için), gerçek master ise SQLite'tadır.
        """
        label = _label_for_type(entity.entity_type)
        params = {
            "canonical_id": entity.canonical_id,
            "canonical_name": entity.canonical_name,
            "entity_type": entity.entity_type,
            "norm_strict": entity.norm_strict,
            "norm_loose": entity.norm_loose,
            "status": entity.status,
            "source": entity.source,
            "metadata_json": json.dumps(entity.metadata or {}, ensure_ascii=False),
            "aliases": list(aliases or []),
            "embedding": embedding,
        }
        # f-string ile label inject; canonical_id sayesinde idempotent
        cypher = f"""
        MERGE (e:Entity {{canonical_id: $canonical_id}})
        ON CREATE SET e.created_at = datetime()
        SET e:{label},
            e.canonical_name = $canonical_name,
            e.entity_type    = $entity_type,
            e.norm_strict    = $norm_strict,
            e.norm_loose     = $norm_loose,
            e.status         = $status,
            e.source         = $source,
            e.metadata_json  = $metadata_json,
            e.aliases        = $aliases,
            e.updated_at     = datetime()
        FOREACH (_ IN CASE WHEN $embedding IS NULL THEN [] ELSE [1] END |
            SET e.embedding = $embedding)
        """
        with self._session() as s:
            s.run(cypher, **params)

    # ------------------------------------------------------------------ document

    def upsert_document(
        self,
        doc_id: str,
        *,
        title: Optional[str] = None,
        sha256: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        cypher = """
        MERGE (d:Document {doc_id: $doc_id})
        ON CREATE SET d.created_at = datetime()
        SET d.title = $title,
            d.sha256 = COALESCE($sha256, d.sha256),
            d.metadata_json = $metadata_json,
            d.updated_at = datetime()
        """
        with self._session() as s:
            s.run(
                cypher,
                doc_id=doc_id,
                title=title,
                sha256=sha256,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            )

    def link_mention(
        self,
        *,
        canonical_id: str,
        doc_id: str,
        span_start: int,
        span_end: int,
        surface: str,
        confidence: float = 1.0,
    ) -> None:
        """Entity → Document MENTIONED_IN ilişkisi (provenance)."""
        cypher = """
        MATCH (e:Entity {canonical_id: $canonical_id})
        MERGE (d:Document {doc_id: $doc_id})
          ON CREATE SET d.created_at = datetime()
        MERGE (e)-[r:MENTIONED_IN {doc_id: $doc_id, span_start: $span_start, span_end: $span_end}]->(d)
        SET r.surface = $surface,
            r.confidence = $confidence,
            r.created_at = COALESCE(r.created_at, datetime())
        """
        with self._session() as s:
            s.run(
                cypher,
                canonical_id=canonical_id,
                doc_id=doc_id,
                span_start=span_start,
                span_end=span_end,
                surface=surface,
                confidence=confidence,
            )

    # ------------------------------------------------------------------ relation

    def upsert_relation(self, rel: GraphRelation) -> None:
        """Subj → Obj ilişkisini MERGE eder (idempotent: aynı doc + span'de
        ikinci kez yazılırsa güncellenir).

        confidence_label edge property olarak yazılır; downstream sorgular
        ``WHERE r.confidence_label = 'AMBIGUOUS'`` ile filtre yapabilir.
        """
        rel_type, original_predicate = _safe_rel_type(rel.predicate)
        cypher = f"""
        MATCH (s:Entity {{canonical_id: $subject_id}})
        MATCH (o:Entity {{canonical_id: $object_id}})
        MERGE (s)-[r:{rel_type} {{source_doc_id: $source_doc_id,
                                  span_start: $span_start,
                                  span_end:   $span_end}}]->(o)
        ON CREATE SET r.created_at = datetime()
        SET r.evidence_text     = $evidence_text,
            r.confidence        = $confidence,
            r.confidence_label  = $confidence_label,
            r.original_predicate = $original_predicate,
            r.extra_json        = $extra_json,
            r.updated_at        = datetime()
        """
        # Enum.value ile string'e dön — Neo4j Python driver enum'u serialize
        # etmez, sadece primitive tipleri kabul eder.
        label_value = (
            rel.confidence_label.value
            if isinstance(rel.confidence_label, ConfidenceLabel)
            else str(rel.confidence_label)
        )
        params = {
            "subject_id": rel.subject_id,
            "object_id": rel.object_id,
            "source_doc_id": rel.source_doc_id,
            "span_start": rel.span_start,
            "span_end": rel.span_end,
            "evidence_text": rel.evidence_text,
            "confidence": rel.confidence,
            "confidence_label": label_value,
            "original_predicate": original_predicate,
            "extra_json": json.dumps(rel.extra or {}, ensure_ascii=False),
        }
        with self._session() as s:
            s.run(cypher, **params)

    # ------------------------------------------------------------------ sync

    def sync_from_alias_store(
        self,
        store: AliasStore,
        *,
        embed_fn: Optional[Callable[[str], list[float]]] = None,
        only_status: Optional[str] = None,
        batch_size: int = 100,
    ) -> dict[str, int]:
        """SQLite master'daki tüm canonical entity'leri Neo4j'ye yansıtır.

        ``embed_fn`` verilirse her entity'nin canonical_name'inden embedding
        hesaplanır ve vector index'e yazılır. ``only_status`` verilirse sadece
        o status'tekiler senkronize edilir (örn. 'verified').
        """
        synced = 0
        with_embed = 0
        with_aliases = 0

        entities = store.list_entities(status=only_status, limit=10**6)
        for ent in entities:
            aliases = [a.surface_form for a in store.get_aliases_for(ent.canonical_id)]
            embedding = None
            if embed_fn is not None:
                try:
                    embedding = embed_fn(ent.canonical_name)
                except Exception as exc:  # pragma: no cover
                    logger.warning("Embedding compute failed for %s: %s", ent.canonical_id, exc)

            self.upsert_entity(ent, embedding=embedding, aliases=aliases)
            synced += 1
            if embedding is not None:
                with_embed += 1
            if aliases:
                with_aliases += 1

        return {
            "synced": synced,
            "with_embedding": with_embed,
            "with_aliases": with_aliases,
        }

    # ------------------------------------------------------------------ read

    def find_by_canonical_id(self, canonical_id: str) -> Optional[dict]:
        with self._session() as s:
            rec = s.run(
                "MATCH (e:Entity {canonical_id: $cid}) RETURN e",
                cid=canonical_id,
            ).single()
            return dict(rec["e"]) if rec else None

    def fulltext_search(
        self,
        query: str,
        *,
        top_k: int = 5,
        entity_type: Optional[str] = None,
    ) -> list[dict]:
        """Neo4j fulltext index üzerinden arama (apoc gerektirmez).

        Lucene escape: özel karakterler escape edilir; sonuçlar score ile döner.
        """
        escaped = _escape_lucene(query)
        cypher = """
        CALL db.index.fulltext.queryNodes('entity_fulltext', $q) YIELD node, score
        WHERE $entity_type IS NULL OR node.entity_type = $entity_type
        RETURN node.canonical_id   AS canonical_id,
               node.canonical_name AS canonical_name,
               node.entity_type    AS entity_type,
               score
        ORDER BY score DESC
        LIMIT $top_k
        """
        with self._session() as s:
            rows = s.run(
                cypher,
                q=escaped,
                top_k=int(top_k),
                entity_type=entity_type,
            ).data()
        return rows

    def vector_search(
        self,
        embedding: list[float],
        *,
        top_k: int = 5,
        entity_type: Optional[str] = None,
    ) -> list[dict]:
        cypher = """
        CALL db.index.vector.queryNodes('entity_embedding', $top_k, $embedding)
            YIELD node, score
        WHERE $entity_type IS NULL OR node.entity_type = $entity_type
        RETURN node.canonical_id   AS canonical_id,
               node.canonical_name AS canonical_name,
               node.entity_type    AS entity_type,
               score
        ORDER BY score DESC
        LIMIT $top_k
        """
        with self._session() as s:
            rows = s.run(
                cypher,
                top_k=int(top_k),
                embedding=list(embedding),
                entity_type=entity_type,
            ).data()
        return rows

    def execute_cypher(
        self,
        cypher: str,
        params: Optional[dict] = None,
    ) -> list[dict]:
        """Genel-amaçlı Cypher çalıştırıcı (agent için).

        Sadece Read-only sorgular için tasarlanmıştır; agent prompt guardrail'ı
        zaten bunu enforce ediyor olmalı, ama burada da defansif kontrol var:
        WRITE/CREATE/DELETE/MERGE/SET içeren sorguları reddet.
        """
        params = params or {}
        upper = cypher.upper()
        for forbidden in ("CREATE ", "DELETE ", "MERGE ", "SET ", "REMOVE ", "DROP "):
            if forbidden in upper:
                raise ValueError(
                    f"execute_cypher salt-okunur sorgular içindir; '{forbidden.strip()}' algılandı"
                )
        with self._session() as s:
            return s.run(cypher, **params).data()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _escape_lucene(text: str) -> str:
    """Lucene query-string özel karakterlerini escape eder."""
    specials = r'+-&|!(){}[]^"~*?:\/'
    out: list[str] = []
    for ch in text:
        if ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)
