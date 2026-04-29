-- kb-overlay alias dictionary schema (UTF-8).
-- Foreign key + WAL pragma uygulama tarafından açılır.

CREATE TABLE IF NOT EXISTS entities (
    canonical_id   TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    entity_type    TEXT NOT NULL,                       -- 'company' | 'person' | 'organization' | ...
    norm_strict    TEXT NOT NULL,                       -- canonical_name'in normalize edilmiş hali
    norm_loose     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'auto_added',  -- 'verified' | 'pending' | 'auto_added' | 'rejected'
    source         TEXT NOT NULL,                       -- 'document_extraction' | 'manual' | 'seed'
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json  TEXT                                 -- ekstra alanlar (vergi_no, akronim vb.)
);

CREATE INDEX IF NOT EXISTS idx_entity_type        ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entity_norm_strict ON entities(norm_strict);
CREATE INDEX IF NOT EXISTS idx_entity_norm_loose  ON entities(norm_loose);
CREATE INDEX IF NOT EXISTS idx_entity_status      ON entities(status);

CREATE TABLE IF NOT EXISTS aliases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    surface_form      TEXT NOT NULL,                    -- belgede görülen orijinal yazım
    norm_strict       TEXT NOT NULL,
    norm_loose        TEXT NOT NULL,
    canonical_id      TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 1.0,
    source            TEXT NOT NULL,                    -- 'ner_first_observed' | 'fuzzy_match' | 'human' | 'gazetteer_seed' | 'embedding'
    added_at          TEXT NOT NULL DEFAULT (datetime('now')),
    source_doc_id     TEXT,                             -- hangi belgede ilk görüldü
    source_span_start INTEGER,
    source_span_end   INTEGER,
    FOREIGN KEY (canonical_id) REFERENCES entities(canonical_id) ON DELETE CASCADE,
    -- Aynı canonical'a ait farklı yazım varyantlarını saklı tutmak için
    -- benzersizlik anahtarı surface_form üzerinde. UPSERT aynı yazımın
    -- yeniden görülmesinde provenance'i COALESCE ile günceller.
    UNIQUE (surface_form, canonical_id)
);

CREATE INDEX IF NOT EXISTS idx_alias_strict    ON aliases(norm_strict);
CREATE INDEX IF NOT EXISTS idx_alias_loose     ON aliases(norm_loose);
CREATE INDEX IF NOT EXISTS idx_alias_canonical ON aliases(canonical_id);
CREATE INDEX IF NOT EXISTS idx_alias_source    ON aliases(source);

-- Belgelerin provenance'ı (hangi belge ne zaman işlendi)
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT,
    sha256       TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT
);

-- Schema versiyon
CREATE TABLE IF NOT EXISTS _schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO _schema_meta (key, value) VALUES ('version', '2');

-- Phase 3: Ingest cache — graphify-adopted content-hash skip.
-- (doc_id, content_hash, extractor_version) tek satır = bu kombinasyonun
-- daha önce işlendiğini söyler. CLI ingest 'has_ingest_cache' lookup'ı ile
-- LLM'e yeniden gitmeyi atlayabilir. extractor_version değişimi (NER backend
-- veya LLM model güncellemesi) cache invalidation tetikler.
CREATE TABLE IF NOT EXISTS ingest_cache (
    doc_id            TEXT NOT NULL,
    content_hash      TEXT NOT NULL,         -- SHA-256 (hex, ilk 16 karakter yeterli)
    extractor_version TEXT NOT NULL,         -- "ner=naive;llm=stub;schema=2" gibi
    ingested_at       TEXT NOT NULL DEFAULT (datetime('now')),
    relations_count   INTEGER DEFAULT 0,
    mentions_count    INTEGER DEFAULT 0,
    PRIMARY KEY (doc_id, content_hash, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_ingest_cache_doc ON ingest_cache(doc_id);
