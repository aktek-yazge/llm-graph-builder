-- Agent Builder Schema
-- Tables for Self-Evolving Agent system

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- AGENT KNOWLEDGE - versioned agent knowledge store
-- Created by knowledge_store.py ensure_table() but also here for completeness
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(128) NOT NULL,
    knowledge_type VARCHAR(64) NOT NULL,
    key VARCHAR(256) NOT NULL,
    value JSONB NOT NULL DEFAULT '{}',
    version INT NOT NULL DEFAULT 1,
    confidence FLOAT NOT NULL DEFAULT 0.5,
    source VARCHAR(64) NOT NULL DEFAULT 'conversation',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, knowledge_type, key, version)
);

CREATE INDEX IF NOT EXISTS idx_ak_agent ON agent_knowledge(agent_id);
CREATE INDEX IF NOT EXISTS idx_ak_type ON agent_knowledge(agent_id, knowledge_type);

-- =============================================================================
-- BATCH JOBS - batch processing tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS batch_jobs (
    batch_id VARCHAR(128) PRIMARY KEY,
    agent_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    total_documents INT NOT NULL DEFAULT 0,
    skill_id VARCHAR(256),
    ocr_mode VARCHAR(32) DEFAULT 'hybrid',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_batch_agent ON batch_jobs(agent_id);

-- =============================================================================
-- WORKSPACE DOCUMENTS - individual document processing status
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspace_documents (
    doc_id VARCHAR(128) PRIMARY KEY,
    batch_id VARCHAR(128) REFERENCES batch_jobs(batch_id),
    agent_id VARCHAR(128) NOT NULL,
    file_path TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    confidence_score FLOAT DEFAULT 0.0,
    extraction_result JSONB,
    error_message TEXT,
    celery_task_id VARCHAR(256),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wdoc_batch ON workspace_documents(batch_id);
CREATE INDEX IF NOT EXISTS idx_wdoc_agent ON workspace_documents(agent_id);
CREATE INDEX IF NOT EXISTS idx_wdoc_status ON workspace_documents(status);

-- =============================================================================
-- ONTOLOGY DISCOVERIES - auto-discovered entity/relationship types from documents
-- =============================================================================
CREATE TABLE IF NOT EXISTS ontology_discoveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(128) NOT NULL,
    discovery_type VARCHAR(32) NOT NULL,   -- 'entity' | 'relationship'
    name VARCHAR(256) NOT NULL,
    sample_count INT NOT NULL DEFAULT 1,
    first_seen_doc VARCHAR(256),
    sample_properties JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, discovery_type, name)
);

CREATE INDEX IF NOT EXISTS idx_disc_agent ON ontology_discoveries(agent_id);
CREATE INDEX IF NOT EXISTS idx_disc_status ON ontology_discoveries(agent_id, status);
