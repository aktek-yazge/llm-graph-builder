-- Event-Sourced Knowledge Graph - PostgreSQL Schema
-- Immutable event log for graph mutations

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- GRAPH EVENTS - Immutable append-only event log
-- =============================================================================
CREATE TABLE IF NOT EXISTS graph_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sequence_no     BIGSERIAL UNIQUE NOT NULL,
    tenant_id       VARCHAR(128) NOT NULL,
    user_id         VARCHAR(128),
    event_type      VARCHAR(64) NOT NULL,
    entity_type     VARCHAR(128) NOT NULL,
    entity_id       VARCHAR(256) NOT NULL,
    before_state    JSONB,
    after_state     JSONB,
    metadata        JSONB DEFAULT '{}',
    llm_prompt      TEXT,
    llm_model       VARCHAR(128),
    session_id      VARCHAR(256),
    is_compensation BOOLEAN DEFAULT FALSE,
    compensation_of UUID REFERENCES graph_events(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_tenant
    ON graph_events (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_entity
    ON graph_events (entity_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_entity_type
    ON graph_events (entity_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_sequence
    ON graph_events (sequence_no DESC);

CREATE INDEX IF NOT EXISTS idx_events_session
    ON graph_events (session_id, created_at DESC)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_events_user
    ON graph_events (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;

-- =============================================================================
-- NAMED SNAPSHOTS - Bookmarks for rollback points
-- =============================================================================
CREATE TABLE IF NOT EXISTS named_snapshots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(128) NOT NULL,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    sequence_no     BIGINT NOT NULL,
    created_by      VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_tenant
    ON named_snapshots (tenant_id, created_at DESC);

-- =============================================================================
-- RESOURCES - Belge koleksiyonlari
-- =============================================================================
CREATE TABLE IF NOT EXISTS resources (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(256) NOT NULL,
    type            VARCHAR(32) NOT NULL DEFAULT 'minio',
    description     TEXT,
    workspace_id    VARCHAR(256),
    tenant_id       VARCHAR(128) NOT NULL DEFAULT 'default',
    minio_bucket    VARCHAR(256) DEFAULT 'resources',
    minio_prefix    VARCHAR(512),
    total_documents INT NOT NULL DEFAULT 0,
    extracted_docs  INT NOT NULL DEFAULT 0,
    status          VARCHAR(32) NOT NULL DEFAULT 'created',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resources_tenant
    ON resources (tenant_id);
CREATE INDEX IF NOT EXISTS idx_resources_workspace
    ON resources (workspace_id) WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_resources_status
    ON resources (status);

-- =============================================================================
-- RESOURCE DOCUMENTS - Tek tek belgeler ve durumlari
-- =============================================================================
CREATE TABLE IF NOT EXISTS resource_documents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_id         UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    file_name           VARCHAR(512) NOT NULL,
    minio_key           VARCHAR(1024),
    file_size           BIGINT DEFAULT 0,
    file_type           VARCHAR(64),
    page_count          INT DEFAULT 0,
    image_count         INT DEFAULT 0,
    extraction_status   VARCHAR(32) NOT NULL DEFAULT 'pending',
    processing_status   VARCHAR(32) NOT NULL DEFAULT 'pending',
    confidence_score    REAL DEFAULT 0.0,
    extraction_result   JSONB,
    error_message       TEXT,
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resdoc_resource
    ON resource_documents (resource_id);
CREATE INDEX IF NOT EXISTS idx_resdoc_extraction
    ON resource_documents (extraction_status);
CREATE INDEX IF NOT EXISTS idx_resdoc_processing
    ON resource_documents (processing_status);
CREATE INDEX IF NOT EXISTS idx_resdoc_resource_status
    ON resource_documents (resource_id, processing_status);

-- =============================================================================
-- CONTEXTS - Domain context'leri
-- =============================================================================
CREATE TABLE IF NOT EXISTS contexts (
    id              VARCHAR(256) PRIMARY KEY,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    domain_keywords TEXT[],
    parent_context  VARCHAR(256) REFERENCES contexts(id),
    tenant_id       VARCHAR(128) NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- GOALS
-- =============================================================================
CREATE TABLE IF NOT EXISTS goals (
    id                      VARCHAR(256) PRIMARY KEY,
    name                    VARCHAR(256) NOT NULL,
    description             TEXT,
    goal_type               VARCHAR(64) NOT NULL DEFAULT 'extraction',
    natural_language_query   TEXT,
    success_criteria        TEXT,
    status                  VARCHAR(32) NOT NULL DEFAULT 'active',
    tenant_id               VARCHAR(128) NOT NULL DEFAULT 'default',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_goals_tenant ON goals (tenant_id);

-- =============================================================================
-- ENTITY SCHEMAS
-- =============================================================================
CREATE TABLE IF NOT EXISTS entity_schemas (
    id              VARCHAR(256) PRIMARY KEY,
    entity_type     VARCHAR(256) NOT NULL,
    description     TEXT,
    properties      JSONB DEFAULT '{}',
    validation_rules JSONB DEFAULT '{}',
    examples        TEXT,
    context         VARCHAR(256),
    tenant_id       VARCHAR(128) NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_es_tenant ON entity_schemas (tenant_id);

-- =============================================================================
-- RELATIONSHIP SCHEMAS
-- =============================================================================
CREATE TABLE IF NOT EXISTS relationship_schemas (
    id                  VARCHAR(256) PRIMARY KEY,
    relationship_type   VARCHAR(256) NOT NULL,
    description         TEXT,
    source_entity       VARCHAR(256),
    target_entity       VARCHAR(256),
    properties          JSONB DEFAULT '{}',
    cardinality         VARCHAR(32) DEFAULT 'many-to-many',
    bidirectional       BOOLEAN DEFAULT FALSE,
    tenant_id           VARCHAR(128) NOT NULL DEFAULT 'default',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rs_tenant ON relationship_schemas (tenant_id);

-- =============================================================================
-- SKILLS
-- =============================================================================
CREATE TABLE IF NOT EXISTS skills (
    id                  VARCHAR(256) PRIMARY KEY,
    name                VARCHAR(256) NOT NULL,
    description         TEXT,
    skill_category      VARCHAR(64) DEFAULT 'extraction',
    prompt_template     TEXT,
    input_schema        JSONB DEFAULT '{}',
    output_schema       JSONB DEFAULT '{}',
    version             INT DEFAULT 1,
    effectiveness_score REAL DEFAULT 0.0,
    usage_count         INT DEFAULT 0,
    is_global           BOOLEAN DEFAULT FALSE,
    tenant_id           VARCHAR(128) NOT NULL DEFAULT 'default',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_skills_tenant ON skills (tenant_id);

-- =============================================================================
-- SKILL <-> SCHEMA JUNCTION TABLES
-- =============================================================================
CREATE TABLE IF NOT EXISTS skill_entity_schemas (
    skill_id    VARCHAR(256) NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    schema_id   VARCHAR(256) NOT NULL REFERENCES entity_schemas(id) ON DELETE CASCADE,
    PRIMARY KEY (skill_id, schema_id)
);

CREATE TABLE IF NOT EXISTS skill_relationship_schemas (
    skill_id    VARCHAR(256) NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    schema_id   VARCHAR(256) NOT NULL REFERENCES relationship_schemas(id) ON DELETE CASCADE,
    PRIMARY KEY (skill_id, schema_id)
);

-- =============================================================================
-- AGENT DEFINITIONS
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_definitions (
    id                      VARCHAR(256) PRIMARY KEY,
    name                    VARCHAR(256) NOT NULL,
    description             TEXT,
    purpose                 TEXT,
    agent_type              VARCHAR(64) DEFAULT 'workspace_agent',
    status                  VARCHAR(32) NOT NULL DEFAULT 'draft',
    tenant_id               VARCHAR(128) NOT NULL DEFAULT 'default',
    workspace_id            VARCHAR(256),
    config                  JSONB DEFAULT '{}',
    mcp_virtual_server_id   VARCHAR(256),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agents_tenant ON agent_definitions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agent_definitions (workspace_id)
    WHERE workspace_id IS NOT NULL;

-- =============================================================================
-- AGENT <-> SKILL JUNCTION
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_skills (
    agent_id    VARCHAR(256) NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
    skill_id    VARCHAR(256) NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    priority    INT DEFAULT 0,
    PRIMARY KEY (agent_id, skill_id)
);

-- =============================================================================
-- AGENT <-> GOAL JUNCTION
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_goals (
    agent_id    VARCHAR(256) NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
    goal_id     VARCHAR(256) NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, goal_id)
);

-- =============================================================================
-- WORKSPACES
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspaces (
    id                  VARCHAR(256) PRIMARY KEY,
    name                VARCHAR(256) NOT NULL,
    description         TEXT,
    tenant_id           VARCHAR(128) NOT NULL DEFAULT 'default',
    status              VARCHAR(32) NOT NULL DEFAULT 'created',
    ocr_mode            VARCHAR(32) DEFAULT 'hybrid',
    batch_size          INT DEFAULT 100,
    agent_id            VARCHAR(256),
    skill_id            VARCHAR(256),
    resource_id         VARCHAR(256),
    extraction_config   JSONB DEFAULT '{}',
    schema_source       VARCHAR(64),
    minio_bucket        VARCHAR(256),
    minio_prefix        VARCHAR(512),
    document_count      INT DEFAULT 0,
    sample_count        INT DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workspaces_tenant ON workspaces (tenant_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces (status);

-- =============================================================================
-- WORKSPACE <-> SCHEMA JUNCTION
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspace_entity_schemas (
    workspace_id VARCHAR(256) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    schema_id    VARCHAR(256) NOT NULL REFERENCES entity_schemas(id) ON DELETE CASCADE,
    PRIMARY KEY (workspace_id, schema_id)
);

CREATE TABLE IF NOT EXISTS workspace_relationship_schemas (
    workspace_id VARCHAR(256) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    schema_id    VARCHAR(256) NOT NULL REFERENCES relationship_schemas(id) ON DELETE CASCADE,
    PRIMARY KEY (workspace_id, schema_id)
);

-- =============================================================================
-- WORKSPACE DOCUMENTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspace_documents (
    id                  VARCHAR(256) PRIMARY KEY,
    workspace_id        VARCHAR(256) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    batch_job_id        VARCHAR(256),
    file_path           TEXT,
    file_name           VARCHAR(512),
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
    is_sample           BOOLEAN DEFAULT FALSE,
    sequence            INT DEFAULT 0,
    ocr_text            TEXT,
    ocr_chars           INT DEFAULT 0,
    ocr_pages           INT DEFAULT 0,
    confidence_score    REAL DEFAULT 0.0,
    extraction_result   JSONB,
    error_message       TEXT,
    minio_key           VARCHAR(1024),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wsdoc_workspace ON workspace_documents (workspace_id);
CREATE INDEX IF NOT EXISTS idx_wsdoc_batch ON workspace_documents (batch_job_id)
    WHERE batch_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wsdoc_status ON workspace_documents (workspace_id, status);

-- =============================================================================
-- BATCH JOBS
-- =============================================================================
CREATE TABLE IF NOT EXISTS batch_jobs (
    id                      VARCHAR(256) PRIMARY KEY,
    workspace_id            VARCHAR(256) NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status                  VARCHAR(32) NOT NULL DEFAULT 'created',
    total_documents         INT DEFAULT 0,
    processed_documents     INT DEFAULT 0,
    successful_documents    INT DEFAULT 0,
    failed_documents        INT DEFAULT 0,
    low_confidence_documents INT DEFAULT 0,
    celery_task_count       INT DEFAULT 0,
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bj_workspace ON batch_jobs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_bj_status ON batch_jobs (status);

-- =============================================================================
-- ELICITATION REQUESTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS elicitation_requests (
    id                  VARCHAR(256) PRIMARY KEY,
    doc_id              VARCHAR(256),
    workspace_id        VARCHAR(256) NOT NULL,
    batch_job_id        VARCHAR(256),
    extraction_result   JSONB,
    confidence_score    REAL DEFAULT 0.0,
    file_name           VARCHAR(512),
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_elicit_workspace ON elicitation_requests (workspace_id);
CREATE INDEX IF NOT EXISTS idx_elicit_status ON elicitation_requests (workspace_id, status);

-- =============================================================================
-- CHAT SESSIONS (was RuntimeSession in Neo4j)
-- =============================================================================
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              VARCHAR(256) PRIMARY KEY,
    agent_id        VARCHAR(256) NOT NULL,
    tenant_id       VARCHAR(128) NOT NULL DEFAULT 'default',
    workspace_id    VARCHAR(256),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chatsess_agent ON chat_sessions (agent_id);
CREATE INDEX IF NOT EXISTS idx_chatsess_workspace ON chat_sessions (workspace_id)
    WHERE workspace_id IS NOT NULL;

-- =============================================================================
-- CHAT MESSAGES (was AgentChatMessage in Neo4j)
-- =============================================================================
CREATE TABLE IF NOT EXISTS chat_messages (
    id              VARCHAR(256) PRIMARY KEY,
    session_id      VARCHAR(256) NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            VARCHAR(16) NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chatmsg_session ON chat_messages (session_id, created_at);

-- =============================================================================
-- BUILDER SESSIONS (was BuilderSession in Neo4j)
-- =============================================================================
CREATE TABLE IF NOT EXISTS builder_sessions (
    id              VARCHAR(256) PRIMARY KEY,
    tenant_id       VARCHAR(128) NOT NULL DEFAULT 'default',
    user_id         VARCHAR(128),
    status          VARCHAR(32) NOT NULL DEFAULT 'active',
    current_state   VARCHAR(64) DEFAULT 'initial',
    state_data      JSONB DEFAULT '{}',
    messages        JSONB DEFAULT '[]',
    created_agent_id VARCHAR(256),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsess_tenant ON builder_sessions (tenant_id, created_at DESC);

-- =============================================================================
-- BLACKBOARD ENTRIES
-- =============================================================================
CREATE TABLE IF NOT EXISTS blackboard_entries (
    id              VARCHAR(256) PRIMARY KEY,
    topic           VARCHAR(256) NOT NULL,
    content         TEXT NOT NULL,
    agent_id        VARCHAR(256),
    workspace_id    VARCHAR(256),
    entry_type      VARCHAR(32) DEFAULT 'info',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bb_topic ON blackboard_entries (topic, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bb_workspace ON blackboard_entries (workspace_id)
    WHERE workspace_id IS NOT NULL;

-- =============================================================================
-- AGENT MESSAGES (inter-agent communication)
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_messages (
    id              VARCHAR(256) PRIMARY KEY,
    from_agent      VARCHAR(256) NOT NULL,
    to_agent        VARCHAR(256) NOT NULL,
    message         TEXT NOT NULL,
    workspace_id    VARCHAR(256),
    message_type    VARCHAR(32) DEFAULT 'direct',
    metadata        JSONB DEFAULT '{}',
    status          VARCHAR(16) NOT NULL DEFAULT 'unread',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_amsg_to ON agent_messages (to_agent, status, created_at DESC);

-- =============================================================================
-- CHAT AGENTS (Virtual Server references on MCP Gateway)
-- =============================================================================
CREATE TABLE IF NOT EXISTS chat_agents (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                    VARCHAR(256) NOT NULL,
    description             TEXT DEFAULT '',
    tenant_id               VARCHAR(128) NOT NULL DEFAULT 'default',
    workspace_id            VARCHAR(256),
    gateway_server_id       VARCHAR(256),
    status                  VARCHAR(32) NOT NULL DEFAULT 'draft',
    system_prompt           TEXT,
    associated_tools        JSONB DEFAULT '[]',
    associated_prompts      JSONB DEFAULT '[]',
    associated_resources    JSONB DEFAULT '[]',
    kb_resource_id          UUID REFERENCES resources(id) ON DELETE SET NULL,
    tags                    TEXT[] DEFAULT '{}',
    config                  JSONB DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_agents_tenant ON chat_agents (tenant_id);
CREATE INDEX IF NOT EXISTS idx_chat_agents_workspace ON chat_agents (workspace_id)
    WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_agents_gateway ON chat_agents (gateway_server_id)
    WHERE gateway_server_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_agents_status ON chat_agents (status);

-- =============================================================================
-- Prevent mutation of events (immutability guard)
-- =============================================================================
CREATE OR REPLACE FUNCTION prevent_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'graph_events table is immutable: UPDATE and DELETE are not allowed';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_event_update ON graph_events;
CREATE TRIGGER trg_prevent_event_update
    BEFORE UPDATE OR DELETE ON graph_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_event_mutation();
