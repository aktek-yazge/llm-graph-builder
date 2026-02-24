// =============================================================================
// AGENT BUILDER - RELATIONSHIP DEFINITIONS
// =============================================================================
// Bu dosya Ontology DB'deki relationship tanımlarını içerir.
// Relationship'ler Goal-driven ve Ontology-driven yaklaşımın temelini oluşturur.
// =============================================================================

// =============================================================================
// GOAL RELATIONSHIPS
// =============================================================================

// Goal -> Skill: Hangi skill'ler bu goal'ü gerçekleştirebilir
// ACHIEVABLE_BY relationship - Goal'den Skill'e
// Properties:
// - priority: Tercih sırası (düşük = daha öncelikli)
// - confidence: Bu skill'in goal'ü başarıyla tamamlama olasılığı (0.0-1.0)
// - conditions: Geçerlilik koşulları (JSON - optional)
//
// Örnek:
// MATCH (g:Goal {id: $goal_id}), (s:Skill {id: $skill_id})
// MERGE (g)-[r:ACHIEVABLE_BY]->(s)
// SET r.priority = $priority,
//     r.confidence = $confidence,
//     r.conditions = $conditions
// RETURN r;

// Goal -> SubGoal: Goal hiyerarşisi (decomposition)
// REQUIRES relationship - Goal'den alt Goal'e
// Properties:
// - order: Sıralama (sequential execution için)
// - optional: Zorunlu mu opsiyonel mi
//
// Örnek:
// MATCH (g:Goal {id: $parent_goal_id}), (sg:Goal {id: $sub_goal_id})
// MERGE (g)-[r:REQUIRES]->(sg)
// SET r.order = $order,
//     r.optional = $optional
// RETURN r;

// Goal -> Context: Goal hangi bağlamda geçerli
// APPLIED_IN relationship
//
// Örnek:
// MATCH (g:Goal {id: $goal_id}), (c:Context {id: $context_id})
// MERGE (g)-[r:APPLIED_IN]->(c)
// RETURN r;

// =============================================================================
// SKILL RELATIONSHIPS
// =============================================================================

// Skill -> EntitySchema: Bu skill hangi entity'leri çıkarır
// EXTRACTS relationship
// Properties:
// - extraction_order: Çıkarım sırası
// - required: Zorunlu entity mi
//
// Örnek:
// MATCH (s:Skill {id: $skill_id}), (e:EntitySchema {id: $entity_id})
// MERGE (s)-[r:EXTRACTS]->(e)
// SET r.extraction_order = $order,
//     r.required = $required
// RETURN r;

// Skill -> RelationshipSchema: Bu skill hangi relationship'leri oluşturur
// CREATES relationship
//
// Örnek:
// MATCH (s:Skill {id: $skill_id}), (rs:RelationshipSchema {id: $rel_schema_id})
// MERGE (s)-[r:CREATES]->(rs)
// RETURN r;

// Skill -> Skill: Skill bağımlılıkları
// DEPENDS_ON relationship - Bu skill çalışmadan önce diğerinin çalışması gerekir
// Properties:
// - dependency_type: Bağımlılık tipi (required, optional, alternative)
//
// Örnek:
// MATCH (s1:Skill {id: $skill_id}), (s2:Skill {id: $depends_on_skill_id})
// MERGE (s1)-[r:DEPENDS_ON]->(s2)
// SET r.dependency_type = $type
// RETURN r;

// Skill -> Skill: Skill versiyonlama
// REFINED_FROM relationship - Bu skill önceki versiyondan türetildi
// Properties:
// - refinement_reason: Neden refine edildi
// - refinement_date: Ne zaman
//
// Örnek:
// MATCH (s_new:Skill {id: $new_skill_id}), (s_old:Skill {id: $old_skill_id})
// MERGE (s_new)-[r:REFINED_FROM]->(s_old)
// SET r.refinement_reason = $reason,
//     r.refinement_date = datetime()
// RETURN r;

// Skill -> Context: Skill hangi bağlamlarda geçerli
// APPLICABLE_IN relationship
//
// Örnek:
// MATCH (s:Skill {id: $skill_id}), (c:Context {id: $context_id})
// MERGE (s)-[r:APPLICABLE_IN]->(c)
// RETURN r;

// =============================================================================
// ENTITY/RELATIONSHIP SCHEMA RELATIONSHIPS
// =============================================================================

// EntitySchema -> RelationshipSchema: Entity bu relationship'in parçası
// RELATES_VIA relationship
// Properties:
// - role: source veya target
//
// Örnek:
// MATCH (e:EntitySchema {id: $entity_id}), (rs:RelationshipSchema {id: $rel_schema_id})
// MERGE (e)-[r:RELATES_VIA]->(rs)
// SET r.role = 'source'
// RETURN r;

// RelationshipSchema -> EntitySchema: Relationship bu entity'ye bağlanır
// CONNECTS_TO relationship
//
// Örnek:
// MATCH (rs:RelationshipSchema {id: $rel_schema_id}), (e:EntitySchema {id: $entity_id})
// MERGE (rs)-[r:CONNECTS_TO]->(e)
// RETURN r;

// EntitySchema -> EntitySchema: Semantic benzerlik
// SIMILAR_TO relationship
// Properties:
// - similarity: Benzerlik skoru (0.0-1.0)
// - similarity_aspects: Hangi yönlerden benzer (JSON array)
//
// Örnek:
// MATCH (e1:EntitySchema {id: $entity1_id}), (e2:EntitySchema {id: $entity2_id})
// MERGE (e1)-[r:SIMILAR_TO]->(e2)
// SET r.similarity = $similarity,
//     r.similarity_aspects = $aspects
// RETURN r;

// =============================================================================
// AGENT RELATIONSHIPS
// =============================================================================

// Agent -> Goal: Agent'ın ana hedefi
// PURSUES relationship
// Properties:
// - is_primary: Ana hedef mi
//
// Örnek:
// MATCH (a:AgentDefinition {id: $agent_id}), (g:Goal {id: $goal_id})
// MERGE (a)-[r:PURSUES]->(g)
// SET r.is_primary = $is_primary
// RETURN r;

// Agent -> Skill: Agent'a atanan skill'ler
// HAS_SKILL relationship
// Properties:
// - assigned_at: Atanma zamanı
// - enabled: Aktif mi
//
// Örnek:
// MATCH (a:AgentDefinition {id: $agent_id}), (s:Skill {id: $skill_id})
// MERGE (a)-[r:HAS_SKILL]->(s)
// SET r.assigned_at = datetime(),
//     r.enabled = true
// RETURN r;

// Agent -> EntitySchema: Agent'ın kullandığı schema'lar
// USES_SCHEMA relationship
//
// Örnek:
// MATCH (a:AgentDefinition {id: $agent_id}), (e:EntitySchema {id: $schema_id})
// MERGE (a)-[r:USES_SCHEMA]->(e)
// RETURN r;

// Agent -> Context: Agent'ın çalıştığı bağlam
// OPERATES_IN relationship
//
// Örnek:
// MATCH (a:AgentDefinition {id: $agent_id}), (c:Context {id: $context_id})
// MERGE (a)-[r:OPERATES_IN]->(c)
// RETURN r;

// =============================================================================
// LEARNING RELATIONSHIPS
// =============================================================================

// Skill -> Learning: Skill bu öğrenmeden iyileştirildi
// IMPROVED_BY relationship
//
// Örnek:
// MATCH (s:Skill {id: $skill_id}), (l:Learning {id: $learning_id})
// MERGE (s)-[r:IMPROVED_BY]->(l)
// RETURN r;

// Learning -> Context: Öğrenme hangi bağlamda gerçekleşti
// OBSERVED_IN relationship
//
// Örnek:
// MATCH (l:Learning {id: $learning_id}), (c:Context {id: $context_id})
// MERGE (l)-[r:OBSERVED_IN]->(c)
// RETURN r;

// Agent -> Learning: Agent bu öğrenmeyi kaydetti
// LEARNED relationship (tenant-isolated - agent üzerinden erişim kontrolü)
//
// Örnek:
// MATCH (a:AgentDefinition {id: $agent_id}), (l:Learning {id: $learning_id})
// MERGE (a)-[r:LEARNED]->(l)
// RETURN r;

// =============================================================================
// SESSION RELATIONSHIPS
// =============================================================================

// BuilderSession -> AgentDefinition: Session'dan oluşturulan agent
// CREATED_AGENT relationship
//
// Örnek:
// MATCH (bs:BuilderSession {id: $session_id}), (a:AgentDefinition {id: $agent_id})
// MERGE (bs)-[r:CREATED_AGENT]->(a)
// SET r.created_at = datetime()
// RETURN r;

// BuilderSession -> Goal: Session sırasında tanımlanan goal
// DEFINED_GOAL relationship
//
// Örnek:
// MATCH (bs:BuilderSession {id: $session_id}), (g:Goal {id: $goal_id})
// MERGE (bs)-[r:DEFINED_GOAL]->(g)
// RETURN r;
