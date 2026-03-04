// =============================================================================
// COMMUNICATION LAYER - Neo4j Schema
// =============================================================================
// Agent iletisim katmani icin BlackboardEntry ve AgentMessage node'lari.
// Blackboard: Agent'lar arasi paylasilan bilgi deposu
// AgentMessage: Agent'lar arasi dogrudan mesajlasma
// =============================================================================

// =============================================================================
// CONSTRAINTS
// =============================================================================

CREATE CONSTRAINT bb_entry_id_unique IF NOT EXISTS
FOR (b:BlackboardEntry) REQUIRE b.id IS UNIQUE;

CREATE CONSTRAINT agent_msg_id_unique IF NOT EXISTS
FOR (m:AgentMessage) REQUIRE m.id IS UNIQUE;

// =============================================================================
// INDEXES
// =============================================================================

CREATE INDEX bb_topic_idx IF NOT EXISTS
FOR (b:BlackboardEntry) ON (b.topic);

CREATE INDEX bb_workspace_idx IF NOT EXISTS
FOR (b:BlackboardEntry) ON (b.workspace_id);

CREATE INDEX bb_agent_idx IF NOT EXISTS
FOR (b:BlackboardEntry) ON (b.agent_id);

CREATE INDEX bb_type_idx IF NOT EXISTS
FOR (b:BlackboardEntry) ON (b.entry_type);

CREATE INDEX msg_to_agent_idx IF NOT EXISTS
FOR (m:AgentMessage) ON (m.to_agent);

CREATE INDEX msg_from_agent_idx IF NOT EXISTS
FOR (m:AgentMessage) ON (m.from_agent);

CREATE INDEX msg_status_idx IF NOT EXISTS
FOR (m:AgentMessage) ON (m.status);

CREATE INDEX msg_workspace_idx IF NOT EXISTS
FOR (m:AgentMessage) ON (m.workspace_id);
