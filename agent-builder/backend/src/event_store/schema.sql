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
-- Actual PK is `id`, references workspaces(id) via workspace_id.
-- =============================================================================
CREATE TABLE IF NOT EXISTS batch_jobs (
    id VARCHAR(256) PRIMARY KEY,
    workspace_id VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'created',
    total_documents INT DEFAULT 0,
    processed_documents INT DEFAULT 0,
    successful_documents INT DEFAULT 0,
    failed_documents INT DEFAULT 0,
    low_confidence_documents INT DEFAULT 0,
    celery_task_count INT DEFAULT 0,
    description TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bj_workspace ON batch_jobs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_batch_status ON batch_jobs(status);

-- Idempotent column adds for legacy installations.
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS celery_task_count INT DEFAULT 0;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS processed_documents INT DEFAULT 0;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS successful_documents INT DEFAULT 0;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS failed_documents INT DEFAULT 0;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS low_confidence_documents INT DEFAULT 0;

-- =============================================================================
-- WORKSPACE DOCUMENTS - individual document processing status
-- Actual PK is `id`, FK via batch_job_id -> batch_jobs(id).
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspace_documents (
    id VARCHAR(256) PRIMARY KEY,
    workspace_id VARCHAR(256) NOT NULL,
    batch_job_id VARCHAR(256),
    file_path TEXT,
    file_name VARCHAR(512),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    is_sample BOOLEAN DEFAULT false,
    sequence INT DEFAULT 0,
    ocr_text TEXT,
    ocr_chars INT DEFAULT 0,
    ocr_pages INT DEFAULT 0,
    confidence_score REAL DEFAULT 0.0,
    extraction_result JSONB,
    error_message TEXT,
    minio_key VARCHAR(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wsdoc_workspace ON workspace_documents(workspace_id);
CREATE INDEX IF NOT EXISTS idx_wsdoc_batch ON workspace_documents(batch_job_id) WHERE batch_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wdoc_status ON workspace_documents(status);

-- Idempotent column adds for legacy installations.
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS file_name VARCHAR(512);
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS sequence INT DEFAULT 0;
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS is_sample BOOLEAN DEFAULT false;
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS ocr_text TEXT;
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS ocr_chars INT DEFAULT 0;
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS ocr_pages INT DEFAULT 0;
ALTER TABLE workspace_documents ADD COLUMN IF NOT EXISTS minio_key VARCHAR(1024);

-- =============================================================================
-- PENDING RESUMES - async batch durability: which thread waits which batch
-- =============================================================================
CREATE TABLE IF NOT EXISTS pending_resumes (
    batch_job_id VARCHAR(256) PRIMARY KEY REFERENCES batch_jobs(id) ON DELETE CASCADE,
    workspace_id VARCHAR(256) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    thread_id VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'waiting',
    event_text_template TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resumed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pending_resumes_thread ON pending_resumes(thread_id);
CREATE INDEX IF NOT EXISTS idx_pending_resumes_status ON pending_resumes(status);
CREATE INDEX IF NOT EXISTS idx_pending_resumes_workspace ON pending_resumes(workspace_id);

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

-- =============================================================================
-- WORKFLOWS - KBG workflow DSL storage (multiple per agent, template sharing)
-- =============================================================================
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id VARCHAR(128) PRIMARY KEY,
    agent_id VARCHAR(128) NOT NULL,
    name VARCHAR(256) NOT NULL DEFAULT 'Default',
    description TEXT NOT NULL DEFAULT '',
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    dsl_json JSONB NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    is_template BOOLEAN NOT NULL DEFAULT FALSE,
    source_template_id VARCHAR(128),
    published_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workflows_agent ON workflows(agent_id);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(agent_id, status);

-- Idempotent column adds for existing installations.
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS is_template BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS source_template_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS idx_workflows_template ON workflows(is_template) WHERE is_template = TRUE;

-- Allow standalone workflows (not bound to an agent).
ALTER TABLE workflows ALTER COLUMN agent_id DROP NOT NULL;

-- =============================================================================
-- WORKFLOW RUNS - execution history
-- =============================================================================
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id VARCHAR(128) PRIMARY KEY,
    workflow_id VARCHAR(128) NOT NULL REFERENCES workflows(workflow_id),
    agent_id VARCHAR(128) NOT NULL,
    mode VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    inputs_json JSONB NOT NULL DEFAULT '{}',
    summary_json JSONB,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wfruns_workflow ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wfruns_agent ON workflow_runs(agent_id);

-- =============================================================================
-- WORKFLOW RUN STEPS - per-node execution log
-- =============================================================================
CREATE TABLE IF NOT EXISTS workflow_run_steps (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    node_id VARCHAR(128) NOT NULL,
    node_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    inputs_json JSONB,
    outputs_json JSONB,
    error TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_run_steps_run ON workflow_run_steps(run_id, started_at);

-- =============================================================================
-- GRAPHRAG ENDPOINTS - published KG endpoints for end-user agent
-- =============================================================================
CREATE TABLE IF NOT EXISTS graphrag_endpoints (
    endpoint_id VARCHAR(128) PRIMARY KEY,
    agent_id VARCHAR(128) NOT NULL,
    workflow_id VARCHAR(128) REFERENCES workflows(workflow_id),
    workflow_version INT NOT NULL DEFAULT 1,
    neo4j_uri TEXT NOT NULL DEFAULT '',
    neo4j_database VARCHAR(128) NOT NULL DEFAULT 'neo4j',
    ontology_snapshot JSONB NOT NULL DEFAULT '{}',
    schema_summary TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    published_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_graphrag_agent ON graphrag_endpoints(agent_id);

-- Idempotent column adds for KB features.
ALTER TABLE graphrag_endpoints ADD COLUMN IF NOT EXISTS name VARCHAR(256) NOT NULL DEFAULT '';
ALTER TABLE graphrag_endpoints ADD COLUMN IF NOT EXISTS source_documents JSONB NOT NULL DEFAULT '[]';

-- =============================================================================
-- MULTI-TENANT AUTH
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(256) NOT NULL,
    slug VARCHAR(128) UNIQUE NOT NULL,
    plan VARCHAR(32) NOT NULL DEFAULT 'free',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email VARCHAR(256) UNIQUE NOT NULL,
    password_hash VARCHAR(512) NOT NULL,
    full_name VARCHAR(256) NOT NULL DEFAULT '',
    role VARCHAR(32) NOT NULL DEFAULT 'member',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Idempotent tenant_id addition to existing tables
ALTER TABLE agent_knowledge ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE ontology_discoveries ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE graphrag_endpoints ADD COLUMN IF NOT EXISTS tenant_id UUID;

CREATE INDEX IF NOT EXISTS idx_ak_tenant ON agent_knowledge(tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wf_tenant ON workflows(tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notif_tenant ON notifications(tenant_id) WHERE tenant_id IS NOT NULL;

-- =============================================================================
-- LLM MODELS - model catalog with pricing and fine-tune config
-- =============================================================================

CREATE TABLE IF NOT EXISTS llm_models (
    model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    provider VARCHAR(64) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    display_name VARCHAR(256),
    description TEXT NOT NULL DEFAULT '',
    input_cost_per_1k DECIMAL(10,6),
    output_cost_per_1k DECIMAL(10,6),
    context_window INT,
    max_output_tokens INT,
    supports_vision BOOLEAN NOT NULL DEFAULT FALSE,
    supports_function_calling BOOLEAN NOT NULL DEFAULT TRUE,
    finetune_base_model VARCHAR(128),
    finetune_status VARCHAR(32),
    finetune_config JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_models_tenant ON llm_models(tenant_id);
CREATE INDEX IF NOT EXISTS idx_models_provider ON llm_models(provider);

-- =============================================================================
-- EVALUATIONS - agent performance test definitions and results
-- =============================================================================

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    agent_id VARCHAR(128),
    name VARCHAR(256) NOT NULL,
    eval_type VARCHAR(64) NOT NULL DEFAULT 'accuracy',
    config JSONB NOT NULL DEFAULT '{}',
    last_run_at TIMESTAMP,
    score DECIMAL(5,2),
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_tenant ON evaluations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_eval_agent ON evaluations(agent_id);

-- =============================================================================
-- GUARDRAILS - safety rules for agent outputs
-- =============================================================================

CREATE TABLE IF NOT EXISTS guardrails (
    guardrail_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name VARCHAR(256) NOT NULL,
    guardrail_type VARCHAR(64) NOT NULL DEFAULT 'content_filter',
    config JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    scope VARCHAR(32) NOT NULL DEFAULT 'global',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_guard_tenant ON guardrails(tenant_id);

-- =============================================================================
-- AGENT TOOLS - tools available for agents
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_tools (
    tool_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name VARCHAR(256) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tool_type VARCHAR(64) NOT NULL DEFAULT 'function',
    config JSONB NOT NULL DEFAULT '{}',
    schema_json JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tools_tenant ON agent_tools(tenant_id);
