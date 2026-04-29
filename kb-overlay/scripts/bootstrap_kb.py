#!/usr/bin/env python3
"""Bootstrap — seed CSV → SQLite alias dictionary (+ opsiyonel Neo4j sync).

CSV formatı (UTF-8, başlık satırı zorunlu):

    name,type,aliases,metadata
    "ABC Bilişim Teknolojileri A.Ş.",company,"ABC Bilişim|ABC BT|abc bilisim","{""vergi_no"":""1234""}"
    "Mehmet Yılmaz",person,"M. Yılmaz|Mehmed Yılmaz",
    "Türkiye İş Bankası A.Ş.",company,"İş Bankası|İşbank|TEB",

Sütunlar:
  - name      (zorunlu) canonical isim
  - type      (zorunlu) company | person | organization | location | product
  - aliases   (opsiyonel) `|` ile ayrılmış ek alias'lar
  - metadata  (opsiyonel) JSON string

Otomatik türetilenler:
  - canonical_name kendisi seed alias olarak eklenir
  - Şirket suffix'leri olmadan bir alias daha üretilir (örn. "ABC Bilişim")
  - norm_strict / norm_loose otomatik

Kullanım:
    python -m scripts.bootstrap_kb --csv data/seed_entities.csv --db data/aliases.db
    python -m scripts.bootstrap_kb --csv data/seed.csv --db data/aliases.db --neo4j
        --neo4j-uri bolt://localhost:7687 --neo4j-pass <password>
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Proje kökü PYTHONPATH'e dahil edilirse `python scripts/bootstrap_kb.py` da çalışır.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_overlay.dictionary import AliasSource, AliasStore, EntityStatus, EntityType  # noqa: E402
from kb_overlay.normalize import normalize  # noqa: E402

logger = logging.getLogger("bootstrap_kb")


# Şirket suffix'siz varyant üretmek için (canonical_name'den çıkar)
_SUFFIX_STRIPPER_RE_PARTS = [
    r"\bA\.?\s*Ş\.?\b",
    r"\bAnonim\s+Şirket(?:i)?\b",
    r"\bLtd\.?\s*Şti\.?\b",
    r"\bLimited\s+Şirket(?:i)?\b",
    r"\bSan(?:ayi)?\.?\s*(?:ve\s+)?Tic(?:aret)?\.?\b",
    r"\bSanayi\s+ve\s+Ticaret\b",
    r"\bSanayi\b",
    r"\bTicaret\b",
    r"\bHolding\b",
    r"\bGrup\b",
    r"\bInc\.?\b",
    r"\bCorp(?:oration)?\.?\b",
    r"\bCompany\b",
    r"\bCo\.?\b",
    r"\bLLC\b",
    r"\bLtd\.?\b",
    r"\bGmbH\b",
    r"\bAG\b",
    r"\bS\.?A\.?\b",
    r"\bS\.?A\.?R\.?L\.?\b",
    r"\bB\.?V\.?\b",
    r"\bN\.?V\.?\b",
]


def _stripped_company_variant(name: str) -> Optional[str]:
    import re

    s = name
    for pat in _SUFFIX_STRIPPER_RE_PARTS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,-")
    if not s or s.lower() == name.lower():
        return None
    if len(s) < 2:
        return None
    return s


def _parse_aliases(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split("|")]
    return [p for p in parts if p]


def _parse_metadata(raw: str) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Metadata JSON parse edilemedi, atlanıyor: %s", raw[:80])
        return {}


def _coerce_entity_type(raw: str) -> EntityType:
    raw = (raw or "").strip().lower()
    try:
        return EntityType(raw)
    except ValueError:
        logger.warning("Bilinmeyen entity_type=%r, OTHER olarak işleniyor", raw)
        return EntityType.OTHER


def bootstrap_from_csv(
    csv_path: Path,
    *,
    store: AliasStore,
    status: EntityStatus = EntityStatus.VERIFIED,
    auto_strip_suffix: bool = True,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> dict:
    """CSV'yi okuyup AliasStore'a yazar. Sayım istatistiği döndürür."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV bulunamadı: {csv_path}")

    counts = {"rows": 0, "entities_created": 0, "aliases_seeded": 0, "skipped": 0}

    with csv_path.open("r", encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        required = {"name", "type"}
        missing = required - set(c.strip() for c in (reader.fieldnames or []))
        if missing:
            raise ValueError(f"CSV başlık satırında eksik sütun(lar): {missing}")

        for row in reader:
            counts["rows"] += 1
            name = (row.get("name") or "").strip()
            etype_raw = (row.get("type") or "").strip()
            if not name or not etype_raw:
                logger.warning("Satır %d: boş name veya type, atlanıyor", counts["rows"])
                counts["skipped"] += 1
                continue

            etype = _coerce_entity_type(etype_raw)
            metadata = _parse_metadata(row.get("metadata", "") or "")

            # Aliases listesi
            aliases = _parse_aliases(row.get("aliases", "") or "")
            if auto_strip_suffix and etype == EntityType.COMPANY:
                stripped = _stripped_company_variant(name)
                if stripped and stripped not in aliases:
                    aliases.append(stripped)

            cid = store.create_entity(
                canonical_name=name,
                entity_type=etype,
                status=status,
                source="seed_list",
                metadata=metadata,
                seed_aliases=aliases,
            )
            counts["entities_created"] += 1
            counts["aliases_seeded"] += 1 + len(aliases)  # canonical_name + aliases

            logger.debug("Seeded %s → %s (%d aliases)", cid, name, 1 + len(aliases))

    return counts


def maybe_sync_neo4j(
    store: AliasStore,
    *,
    uri: str,
    user: str,
    password: str,
    database: str,
    embed: bool,
) -> dict:
    """Neo4j'ye senkronize et. embed=True ise sentence-transformers kullanır."""
    from kb_overlay.neo4j_store import Neo4jConfig, Neo4jStore  # lazy import

    embed_fn = None
    vector_dim = 768
    if embed:
        try:
            from sentence_transformers import SentenceTransformer

            model_name = os.environ.get(
                "KBO_EMBED_MODEL",
                "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
            )
            logger.info("Loading embedding model: %s", model_name)
            model = SentenceTransformer(model_name)
            vector_dim = int(model.get_sentence_embedding_dimension())

            def embed_fn(text: str) -> list[float]:
                vec = model.encode(text, normalize_embeddings=True)
                return [float(x) for x in vec]
        except ImportError as exc:
            logger.warning(
                "sentence-transformers yok (%s). Embedding atlanıyor — sadece graph senkronize edilecek.",
                exc,
            )

    cfg = Neo4jConfig(
        uri=uri,
        user=user,
        password=password,
        database=database,
        vector_dim=vector_dim,
        create_vector_index=embed_fn is not None,
    )
    with Neo4jStore(cfg) as graph:
        graph.init_schema()
        return graph.sync_from_alias_store(store, embed_fn=embed_fn)


def main() -> None:
    parser = argparse.ArgumentParser(description="kb-overlay bootstrap")
    parser.add_argument("--csv", required=True, type=Path, help="Seed entity CSV")
    parser.add_argument("--db", required=True, type=Path, help="SQLite alias DB path")
    parser.add_argument(
        "--status",
        default="verified",
        choices=[s.value for s in EntityStatus],
        help="Seed entity'lerin başlangıç status'u",
    )
    parser.add_argument(
        "--no-strip-suffix",
        action="store_true",
        help="Şirket suffix'siz varyant ekleme",
    )
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--encoding", default="utf-8")

    parser.add_argument("--neo4j", action="store_true", help="Neo4j'ye senkronize et")
    parser.add_argument("--neo4j-uri", default=os.environ.get("KBO_NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.environ.get("KBO_NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.environ.get("KBO_NEO4J_PASS", "neo4j"))
    parser.add_argument("--neo4j-db", default=os.environ.get("KBO_NEO4J_DB", "neo4j"))
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Neo4j sync sırasında her canonical için embedding hesapla",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = AliasStore(args.db)
    store.init_schema()

    counts = bootstrap_from_csv(
        args.csv,
        store=store,
        status=EntityStatus(args.status),
        auto_strip_suffix=not args.no_strip_suffix,
        delimiter=args.delimiter,
        encoding=args.encoding,
    )
    print(f"\n[SQLite] {counts}")

    if args.neo4j:
        sync = maybe_sync_neo4j(
            store,
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_pass,
            database=args.neo4j_db,
            embed=args.embed,
        )
        print(f"[Neo4j ] {sync}")


if __name__ == "__main__":
    main()
