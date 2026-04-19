"""
Quality Tools
=============

KG kalite kontrol, sampling, celisiki tespiti ve anomali analizi.

Uc katmanli kalite kontrol:
1. SAMPLING: Her N belgeden 1'ini rastgele sec, extraction sonucunu denetle
2. CONFLICT: Ayni entity farkli isimlerle cikarilmis mi? Celiskili iliskiler var mi?
3. ANOMALY: Beklenmeyen pattern, eksik zorunlu alanlar, outlier'lar

Agent bu tool'lari kullanarak:
- Batch isleme sirasinda veya sonrasinda kalite raporu olusturur
- Sorunlu alanları kullaniciya sunar
- Ontoloji iyilestirme onerileri yapar
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_quality_tools(agent_id: str, pg=None) -> list:
    """Kalite kontrol tool'larini olustur."""

    @tool
    async def sample_and_review(
        batch_id: str = "",
        sample_size: int = 5,
        strategy: str = "random",
    ) -> str:
        """Batch'ten ornekleme yap ve extraction sonuclarini incele.
        Kalite kontrolunun ilk adimi.

        Args:
            batch_id: Batch ID (bos ise son batch)
            sample_size: Orneklenecek belge sayisi
            strategy: random | lowest_confidence | highest_confidence
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            bid = batch_id or await _get_last_batch_id()
            if not bid:
                return "Batch bulunamadi."

            if strategy == "lowest_confidence":
                order = "confidence_score ASC NULLS FIRST"
            elif strategy == "highest_confidence":
                order = "confidence_score DESC NULLS LAST"
            else:
                order = "RANDOM()"

            docs = await pg.fetch(
                f"""
                SELECT id, file_name, status, confidence_score, extraction_result
                FROM workspace_documents
                WHERE batch_job_id = $1 AND status IN ('completed', 'low_confidence', 'needs_review')
                ORDER BY {order}
                LIMIT $2
                """,
                bid, sample_size,
            )

            samples = []
            for d in docs:
                result = _parse_extraction(d.get("extraction_result"))
                nodes = result.get("nodes", [])
                edges = result.get("relationships", result.get("edges", []))

                entity_types = Counter(n.get("class", n.get("type", "unknown")) for n in nodes)

                samples.append({
                    "doc_id": d["id"],
                    "file_name": d["file_name"],
                    "status": d["status"],
                    "confidence": float(d["confidence_score"]) if d["confidence_score"] else 0,
                    "node_count": len(nodes),
                    "relationship_count": len(edges),
                    "entity_types": dict(entity_types),
                    "sample_nodes": nodes[:5],
                    "sample_edges": edges[:3],
                })

            return json.dumps({
                "batch_id": bid,
                "sample_count": len(samples),
                "strategy": strategy,
                "samples": samples,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("sample_and_review error: %s", e)
            return f"Ornekleme hatasi: {e}"

    @tool
    async def detect_conflicts(batch_id: str = "") -> str:
        """Extraction sonuclarinda celiskileri tespit et.
        - Ayni entity farkli isimlerle cikarilmis mi?
        - Celiskili iliskiler var mi?
        - Ayni belge icinde tutarsiz veriler var mi?

        Args:
            batch_id: Batch ID (bos ise son batch)
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            bid = batch_id or await _get_last_batch_id()
            if not bid:
                return "Batch bulunamadi."

            docs = await pg.fetch(
                """
                SELECT id, file_name, extraction_result
                FROM workspace_documents
                WHERE batch_job_id = $1 AND status IN ('completed', 'low_confidence', 'needs_review')
                  AND extraction_result IS NOT NULL
                """,
                bid,
            )

            entity_names: dict[str, list[dict]] = defaultdict(list)
            entity_by_type: dict[str, set] = defaultdict(set)
            relationship_pairs: list[dict] = []

            for d in docs:
                result = _parse_extraction(d.get("extraction_result"))
                nodes = result.get("nodes", [])
                edges = result.get("relationships", result.get("edges", []))

                for n in nodes:
                    name = n.get("properties", {}).get("name", n.get("id", ""))
                    entity_type = n.get("class", n.get("type", "unknown"))
                    if name:
                        normalized = name.strip().lower()
                        entity_names[normalized].append({
                            "original": name,
                            "type": entity_type,
                            "doc": d["file_name"],
                            "node_id": n.get("id", ""),
                        })
                        entity_by_type[entity_type].add(normalized)

                for e in edges:
                    relationship_pairs.append({
                        "source": e.get("source", ""),
                        "predicate": e.get("predicate", e.get("type", "")),
                        "target": e.get("target", ""),
                        "doc": d["file_name"],
                    })

            conflicts = []

            for name, occurrences in entity_names.items():
                types = set(o["type"] for o in occurrences)
                if len(types) > 1:
                    conflicts.append({
                        "type": "type_mismatch",
                        "entity": name,
                        "found_as": list(types),
                        "in_documents": list(set(o["doc"] for o in occurrences)),
                        "severity": "high",
                    })

            similar_groups = _find_similar_names(list(entity_names.keys()))
            for group in similar_groups:
                conflicts.append({
                    "type": "possible_duplicate",
                    "entities": group,
                    "details": [
                        {"name": entity_names[n][0]["original"], "type": entity_names[n][0]["type"]}
                        for n in group if n in entity_names
                    ],
                    "severity": "medium",
                })

            rel_counter = Counter(
                (r["source"], r["predicate"], r["target"]) for r in relationship_pairs
            )
            contradictions = []
            for r in relationship_pairs:
                reverse_key = (r["target"], r["predicate"], r["source"])
                if rel_counter.get(reverse_key, 0) > 0 and r["predicate"] not in ("RELATED_TO",):
                    contradictions.append({
                        "type": "bidirectional_relationship",
                        "predicate": r["predicate"],
                        "pair": [r["source"], r["target"]],
                        "doc": r["doc"],
                        "severity": "low",
                    })

            all_issues = conflicts + contradictions

            return json.dumps({
                "batch_id": bid,
                "total_documents_analyzed": len(docs),
                "conflict_count": len(all_issues),
                "conflicts": all_issues[:30],
                "entity_type_distribution": {k: len(v) for k, v in entity_by_type.items()},
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("detect_conflicts error: %s", e)
            return f"Celisiki tespiti hatasi: {e}"

    @tool
    async def detect_anomalies(batch_id: str = "") -> str:
        """Extraction sonuclarinda anomalileri tespit et.
        - Beklenmeyen entity tipleri
        - Eksik zorunlu alanlar
        - Cok az/cok fazla node uretilen belgeler (outlier)
        - Bos extraction sonuclari

        Args:
            batch_id: Batch ID (bos ise son batch)
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            bid = batch_id or await _get_last_batch_id()
            if not bid:
                return "Batch bulunamadi."

            from ..knowledge_store import KnowledgeStore
            store = KnowledgeStore(pg)
            ontology = await store.load_ontology(agent_id)
            expected_types = {e.name for e in ontology.entity_classes}
            required_fields: dict[str, list[str]] = {}
            for ec in ontology.entity_classes:
                req = [p.name for p in ec.properties if p.constraint == "required"]
                if req:
                    required_fields[ec.name] = req

            docs = await pg.fetch(
                """
                SELECT id, file_name, confidence_score, extraction_result
                FROM workspace_documents
                WHERE batch_job_id = $1 AND extraction_result IS NOT NULL
                """,
                bid,
            )

            node_counts = []
            anomalies = []

            for d in docs:
                result = _parse_extraction(d.get("extraction_result"))
                nodes = result.get("nodes", [])
                edges = result.get("relationships", result.get("edges", []))
                node_counts.append(len(nodes))

                if not nodes:
                    anomalies.append({
                        "type": "empty_extraction",
                        "doc_id": d["id"],
                        "file_name": d["file_name"],
                        "severity": "high",
                    })
                    continue

                for n in nodes:
                    ntype = n.get("class", n.get("type", ""))
                    if expected_types and ntype and ntype not in expected_types:
                        anomalies.append({
                            "type": "unexpected_entity_type",
                            "entity_type": ntype,
                            "doc_id": d["id"],
                            "file_name": d["file_name"],
                            "severity": "medium",
                        })

                    if ntype in required_fields:
                        props = n.get("properties", {})
                        missing = [f for f in required_fields[ntype] if not props.get(f)]
                        if missing:
                            anomalies.append({
                                "type": "missing_required_fields",
                                "entity_type": ntype,
                                "missing_fields": missing,
                                "doc_id": d["id"],
                                "file_name": d["file_name"],
                                "severity": "high",
                            })

            if len(node_counts) > 5:
                avg = sum(node_counts) / len(node_counts)
                std = (sum((x - avg) ** 2 for x in node_counts) / len(node_counts)) ** 0.5
                if std > 0:
                    for i, d in enumerate(docs):
                        if i < len(node_counts):
                            z = abs(node_counts[i] - avg) / std
                            if z > 2.0:
                                anomalies.append({
                                    "type": "outlier_node_count",
                                    "doc_id": d["id"],
                                    "file_name": d["file_name"],
                                    "node_count": node_counts[i],
                                    "avg_node_count": round(avg, 1),
                                    "z_score": round(z, 2),
                                    "severity": "medium",
                                })

            high = [a for a in anomalies if a["severity"] == "high"]
            medium = [a for a in anomalies if a["severity"] == "medium"]

            return json.dumps({
                "batch_id": bid,
                "documents_analyzed": len(docs),
                "anomaly_count": len(anomalies),
                "high_severity": len(high),
                "medium_severity": len(medium),
                "anomalies": anomalies[:30],
                "stats": {
                    "avg_nodes_per_doc": round(sum(node_counts) / len(node_counts), 1) if node_counts else 0,
                    "min_nodes": min(node_counts) if node_counts else 0,
                    "max_nodes": max(node_counts) if node_counts else 0,
                },
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("detect_anomalies error: %s", e)
            return f"Anomali tespiti hatasi: {e}"

    @tool
    async def generate_quality_report(batch_id: str = "") -> str:
        """Batch islemenin kapsamli kalite raporunu olustur.
        Ilerleme + sorunlar + anomaliler + celiskiler tek raporda.
        Kullaniciya gostermek icin ozet uretir.

        Args:
            batch_id: Batch ID (bos ise son batch)
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            bid = batch_id or await _get_last_batch_id()
            if not bid:
                return "Batch bulunamadi."

            job = await pg.fetchrow("SELECT * FROM batch_jobs WHERE id = $1", bid)
            if not job:
                return f"Batch bulunamadi: {bid}"

            stats = await pg.fetchrow(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'completed') as successful,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    COUNT(*) FILTER (WHERE status IN ('low_confidence', 'needs_review')) as review_needed,
                    COUNT(*) FILTER (WHERE status = 'queued') as queued,
                    AVG(confidence_score) FILTER (WHERE confidence_score > 0) as avg_confidence,
                    MIN(confidence_score) FILTER (WHERE confidence_score > 0) as min_confidence,
                    MAX(confidence_score) FILTER (WHERE confidence_score > 0) as max_confidence
                FROM workspace_documents
                WHERE batch_job_id = $1
                """,
                bid,
            )

            confidence_distribution = await pg.fetch(
                """
                SELECT
                    CASE
                        WHEN confidence_score >= 0.8 THEN 'high (>=0.8)'
                        WHEN confidence_score >= 0.5 THEN 'medium (0.5-0.8)'
                        WHEN confidence_score > 0 THEN 'low (<0.5)'
                        ELSE 'none (0)'
                    END as band,
                    COUNT(*) as count
                FROM workspace_documents
                WHERE batch_job_id = $1
                GROUP BY band ORDER BY band
                """,
                bid,
            )

            error_types = await pg.fetch(
                """
                SELECT error_message, COUNT(*) as count
                FROM workspace_documents
                WHERE batch_job_id = $1 AND status = 'failed' AND error_message IS NOT NULL
                GROUP BY error_message
                ORDER BY count DESC LIMIT 5
                """,
                bid,
            )

            total = stats["total"]
            processed = stats["successful"] + stats["failed"] + stats["review_needed"]
            percent = round((processed / total * 100), 1) if total > 0 else 0

            report = {
                "batch_id": bid,
                "status": job["status"],
                "progress": {
                    "total": total,
                    "processed": processed,
                    "percent": percent,
                    "successful": stats["successful"],
                    "failed": stats["failed"],
                    "needs_review": stats["review_needed"],
                    "queued": stats["queued"],
                },
                "quality": {
                    "avg_confidence": round(float(stats["avg_confidence"] or 0), 3),
                    "min_confidence": round(float(stats["min_confidence"] or 0), 3),
                    "max_confidence": round(float(stats["max_confidence"] or 0), 3),
                    "confidence_distribution": {r["band"]: r["count"] for r in confidence_distribution},
                },
                "top_errors": [{"error": r["error_message"][:100], "count": r["count"]} for r in error_types],
            }

            summary_lines = [f"## Kalite Raporu: {bid}"]
            summary_lines.append(f"- Ilerleme: {processed}/{total} (%{percent})")
            summary_lines.append(f"- Basarili: {stats['successful']}, Basarisiz: {stats['failed']}, Inceleme: {stats['review_needed']}")
            summary_lines.append(f"- Ortalama guven: {report['quality']['avg_confidence']}")

            if stats["failed"] > 0:
                summary_lines.append(f"\n**DIKKAT:** {stats['failed']} belge basarisiz. Hata detaylari icin `list_problem_documents` kullanin.")
            if stats["review_needed"] > 0:
                summary_lines.append(f"\n**INCELEME:** {stats['review_needed']} belge dusuk guvenle islendi. `get_review_queue` ile inceleyin.")

            report["summary"] = "\n".join(summary_lines)

            return json.dumps(report, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("generate_quality_report error: %s", e)
            return f"Kalite raporu hatasi: {e}"

    # ─── HELPERS ──────────────────────────────────────────────────

    async def _get_last_batch_id() -> str | None:
        row = await pg.fetchrow(
            "SELECT id FROM batch_jobs WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT 1",
            f"ws-{agent_id}",
        )
        return row["id"] if row else None

    def _parse_extraction(raw: Any) -> dict:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw.get("data", raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed.get("data", parsed) if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _find_similar_names(names: list[str], threshold: float = 0.85) -> list[list[str]]:
        """Basit Jaccard benzerligi ile benzer isimleri grupla."""
        groups = []
        used = set()

        for i, a in enumerate(names):
            if a in used:
                continue
            group = [a]
            a_tokens = set(a.split())

            for j in range(i + 1, len(names)):
                b = names[j]
                if b in used:
                    continue
                b_tokens = set(b.split())
                if not a_tokens or not b_tokens:
                    continue
                jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
                if jaccard >= threshold and a != b:
                    group.append(b)
                    used.add(b)

            if len(group) > 1:
                groups.append(group)
                used.add(a)

        return groups

    return [
        sample_and_review,
        detect_conflicts,
        detect_anomalies,
        generate_quality_report,
    ]
