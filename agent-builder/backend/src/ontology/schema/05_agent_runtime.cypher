// =============================================================================
// 05_agent_runtime.cypher
// Agent Runtime Schema Extensions
// =============================================================================
// Blackboard, ToolRegistration ve runtime iliskileri
// =============================================================================

// -- Blackboard: Konu bazli bilgi paylasimi ---------------------------------
CREATE CONSTRAINT blackboard_id_unique IF NOT EXISTS
FOR (b:Blackboard) REQUIRE b.id IS UNIQUE;

CREATE INDEX blackboard_topic_idx IF NOT EXISTS
FOR (b:Blackboard) ON (b.topic);

CREATE INDEX blackboard_agent_idx IF NOT EXISTS
FOR (b:Blackboard) ON (b.agent_id);

// -- ToolRegistration: Gateway tool kayitlari --------------------------------
CREATE CONSTRAINT tool_registration_id_unique IF NOT EXISTS
FOR (tr:ToolRegistration) REQUIRE tr.id IS UNIQUE;

CREATE INDEX tool_reg_gateway_id_idx IF NOT EXISTS
FOR (tr:ToolRegistration) ON (tr.gateway_tool_id);

CREATE INDEX tool_reg_server_idx IF NOT EXISTS
FOR (tr:ToolRegistration) ON (tr.virtual_server_id);

// -- AgentDefinition ek property'ler -----------------------------------------
// mcp_endpoint, runtime_config, gateway_tool_ids zaten SET ile ekleniyor
// Asagidaki index'ler sorgu performansi icin

CREATE INDEX agent_mcp_endpoint_idx IF NOT EXISTS
FOR (a:AgentDefinition) ON (a.mcp_endpoint);

CREATE INDEX agent_vs_id_idx IF NOT EXISTS
FOR (a:AgentDefinition) ON (a.mcp_virtual_server_id);

// -- RuntimeSession: Calisma zamani oturumlari --------------------------------
CREATE CONSTRAINT runtime_session_id_unique IF NOT EXISTS
FOR (rs:RuntimeSession) REQUIRE rs.id IS UNIQUE;

CREATE INDEX runtime_session_agent_idx IF NOT EXISTS
FOR (rs:RuntimeSession) ON (rs.agent_id);

CREATE INDEX runtime_session_tenant_idx IF NOT EXISTS
FOR (rs:RuntimeSession) ON (rs.tenant_id);

// -- Fulltext index'ler -------------------------------------------------------
CREATE FULLTEXT INDEX blackboard_fulltext IF NOT EXISTS
FOR (b:Blackboard) ON EACH [b.topic, b.content];
