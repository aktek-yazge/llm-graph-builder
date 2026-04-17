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
    celery_task_count INT NOT NULL DEFAULT 0,
    skill_id VARCHAR(256),
    ocr_mode VARCHAR(32) DEFAULT 'hybrid',
    description TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_batch_agent ON batch_jobs(agent_id);
CREATE INDEX IF NOT EXISTS idx_batch_status ON batch_jobs(status);

-- Idempotent column adds for legacy installations.
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS celery_task_count INT NOT NULL DEFAULT 0;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

-- =============================================================================
-- WORKSPACE DOCUMENTS - individual document processing status
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspace_documents (
    doc_id VARCHAR(128) PRIMARY KEY,
    batch_id VARCHAR(128) REFERENCES batch_jobs(batch_id),
    agent_id VARCHAR(128) NOT NULL,
    file_path TEXT NOT NULL,
    file_name VARCHAR(512),
    sequence INT,
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

-- Idempotent column adds for legacy installations.
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS file_name VARCHAR(512);
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS sequence INT;

-- =============================================================================
-- PENDING RESUMES - async batch durability: which thread waits which batch
-- =============================================================================
CREATE TABLE IF NOT EXISTS pending_resumes (
    batch_id VARCHAR(128) PRIMARY KEY REFERENCES batch_jobs(batch_id) ON DELETE CASCADE,
    agent_id VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    thread_id VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'waiting',
    event_text_template TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resumed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pending_resumes_thread ON pending_resumes(thread_id);
CREATE INDEX IF NOT EXISTS idx_pending_resumes_status ON pending_resumes(status);
CREATE INDEX IF NOT EXISTS idx_pending_resumes_agent ON pending_resumes(agent_id);

-- =============================================================================
-- NOTIFICATIONS - durable in-app notification history
-- =============================================================================
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    read_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_agent_unread ON notifications(agent_id, read_at);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);

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
