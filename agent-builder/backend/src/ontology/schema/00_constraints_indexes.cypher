// =============================================================================
// AGENT BUILDER - NEO4J ONTOLOGY SCHEMA
// =============================================================================
// Bu dosya Neo4j Ontology DB için constraint ve index tanımlarını içerir.
// Çalıştırma: Neo4j Browser veya cypher-shell ile
// 
// IMPORTANT: Bu schema Documents DB'den AYRI bir Neo4j instance'ında çalışır!
// - Documents DB: Belge verileri (Document, Chunk, Entity - tenant-specific)
// - Ontology DB: Agent metadata (Goal, Skill, Schema - shared + tenant agents)
// =============================================================================

// =============================================================================
// CONSTRAINTS - Uniqueness garantisi
// =============================================================================

// Goal constraints
CREATE CONSTRAINT goal_id_unique IF NOT EXISTS
FOR (g:Goal) REQUIRE g.id IS UNIQUE;

// Skill constraints  
CREATE CONSTRAINT skill_id_unique IF NOT EXISTS
FOR (s:Skill) REQUIRE s.id IS UNIQUE;

// EntitySchema constraints
CREATE CONSTRAINT entity_schema_id_unique IF NOT EXISTS
FOR (e:EntitySchema) REQUIRE e.id IS UNIQUE;

// RelationshipSchema constraints
CREATE CONSTRAINT rel_schema_id_unique IF NOT EXISTS
FOR (r:RelationshipSchema) REQUIRE r.id IS UNIQUE;

// AgentDefinition constraints
CREATE CONSTRAINT agent_id_unique IF NOT EXISTS
FOR (a:AgentDefinition) REQUIRE a.id IS UNIQUE;

// Context constraints
CREATE CONSTRAINT context_id_unique IF NOT EXISTS
FOR (c:Context) REQUIRE c.id IS UNIQUE;

// Learning constraints
CREATE CONSTRAINT learning_id_unique IF NOT EXISTS
FOR (l:Learning) REQUIRE l.id IS UNIQUE;

// BuilderSession constraints (conversation state)
CREATE CONSTRAINT session_id_unique IF NOT EXISTS
FOR (bs:BuilderSession) REQUIRE bs.id IS UNIQUE;

// =============================================================================
// INDEXES - Sorgu performansı için
// =============================================================================

// Goal indexes
CREATE INDEX goal_tenant_idx IF NOT EXISTS
FOR (g:Goal) ON (g.tenant_id);

CREATE INDEX goal_type_idx IF NOT EXISTS
FOR (g:Goal) ON (g.goal_type);

CREATE INDEX goal_status_idx IF NOT EXISTS
FOR (g:Goal) ON (g.status);

// Skill indexes
CREATE INDEX skill_tenant_idx IF NOT EXISTS
FOR (s:Skill) ON (s.tenant_id);

CREATE INDEX skill_category_idx IF NOT EXISTS
FOR (s:Skill) ON (s.skill_category);

CREATE INDEX skill_global_idx IF NOT EXISTS
FOR (s:Skill) ON (s.is_global);

CREATE INDEX skill_effectiveness_idx IF NOT EXISTS
FOR (s:Skill) ON (s.effectiveness_score);

// EntitySchema indexes
CREATE INDEX entity_schema_type_idx IF NOT EXISTS
FOR (e:EntitySchema) ON (e.entity_type);

CREATE INDEX entity_schema_context_idx IF NOT EXISTS
FOR (e:EntitySchema) ON (e.context);

// AgentDefinition indexes
CREATE INDEX agent_tenant_idx IF NOT EXISTS
FOR (a:AgentDefinition) ON (a.tenant_id);

CREATE INDEX agent_status_idx IF NOT EXISTS
FOR (a:AgentDefinition) ON (a.status);

// Context indexes
CREATE INDEX context_name_idx IF NOT EXISTS
FOR (c:Context) ON (c.name);

// Learning indexes - tenant isolation için kritik
CREATE INDEX learning_tenant_idx IF NOT EXISTS
FOR (l:Learning) ON (l.tenant_id);

CREATE INDEX learning_type_idx IF NOT EXISTS
FOR (l:Learning) ON (l.learning_type);

// BuilderSession indexes
CREATE INDEX session_tenant_idx IF NOT EXISTS
FOR (bs:BuilderSession) ON (bs.tenant_id);

CREATE INDEX session_status_idx IF NOT EXISTS
FOR (bs:BuilderSession) ON (bs.status);

// =============================================================================
// FULLTEXT INDEXES - Semantic search için
// =============================================================================

// Goal description search
CREATE FULLTEXT INDEX goal_description_fulltext IF NOT EXISTS
FOR (g:Goal) ON EACH [g.name, g.description, g.natural_language_query];

// Skill description search
CREATE FULLTEXT INDEX skill_description_fulltext IF NOT EXISTS
FOR (s:Skill) ON EACH [s.name, s.description];

// EntitySchema search
CREATE FULLTEXT INDEX entity_schema_fulltext IF NOT EXISTS
FOR (e:EntitySchema) ON EACH [e.entity_type, e.description];

// =============================================================================
// VECTOR INDEXES - Embedding-based similarity için (Neo4j 5.15+)
// =============================================================================

// Goal embedding index - semantic goal matching
CREATE VECTOR INDEX goal_embedding_idx IF NOT EXISTS
FOR (g:Goal) ON (g.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}};

// Skill embedding index - semantic skill search
CREATE VECTOR INDEX skill_embedding_idx IF NOT EXISTS
FOR (s:Skill) ON (s.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}};

// EntitySchema embedding index - similar schema discovery
CREATE VECTOR INDEX entity_schema_embedding_idx IF NOT EXISTS
FOR (e:EntitySchema) ON (e.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}};
