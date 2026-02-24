// =============================================================================
// AGENT BUILDER - NODE DEFINITIONS
// =============================================================================
// Bu dosya Ontology DB'deki node yapılarını ve örnek verileri içerir.
// Gerçek node'lar runtime'da API üzerinden oluşturulur.
// Bu script referans ve test amaçlıdır.
// =============================================================================

// =============================================================================
// GOAL NODE
// =============================================================================
// Kullanıcı hedeflerini temsil eder.
// Goal-driven yaklaşımın temel yapı taşı.
// 
// Properties:
// - id: Unique identifier (UUID)
// - name: İnsan okunabilir isim
// - description: Detaylı açıklama
// - goal_type: Hedef kategorisi (extraction, analysis, search, transformation)
// - natural_language_query: Kullanıcının orijinal sorgusu
// - success_criteria: Başarı kriterleri (JSON)
// - status: Hedef durumu (active, achieved, abandoned)
// - tenant_id: Tenant izolasyonu için (nullable - global goals için)
// - embedding: Semantic search için vector (1536 dim)
// - created_by: Oluşturan kullanıcı ID
// - created_at: Oluşturulma zamanı
// =============================================================================

// Örnek Goal oluşturma template'i
// MERGE (g:Goal {id: $id})
// SET g.name = $name,
//     g.description = $description,
//     g.goal_type = $goal_type,
//     g.natural_language_query = $natural_language_query,
//     g.success_criteria = $success_criteria,
//     g.status = 'active',
//     g.tenant_id = $tenant_id,
//     g.embedding = $embedding,
//     g.created_by = $created_by,
//     g.created_at = datetime()
// RETURN g;

// =============================================================================
// SKILL NODE
// =============================================================================
// Goal'leri gerçekleştirmek için kullanılan yetenekler.
// Agentic OCR, entity extraction, query templates vb.
//
// Properties:
// - id: Unique identifier (UUID)
// - name: Skill adı
// - description: Ne yaptığının açıklaması
// - skill_category: Kategori (ocr, extraction, relationship_mapping, query, workflow)
// - prompt_template: LLM için prompt şablonu (Jinja2 format)
// - input_schema: Beklenen input formatı (JSON Schema)
// - output_schema: Üretilen output formatı (JSON Schema)
// - version: Skill versiyonu (int)
// - effectiveness_score: Başarı oranı (0.0-1.0)
// - usage_count: Kullanım sayısı
// - is_global: Global skill mi (tüm tenant'lar kullanabilir)
// - tenant_id: Oluşturan tenant (global=false ise)
// - embedding: Semantic search için vector
// - created_at: Oluşturulma zamanı
// - updated_at: Son güncelleme
// =============================================================================

// Örnek Skill oluşturma template'i
// MERGE (s:Skill {id: $id})
// SET s.name = $name,
//     s.description = $description,
//     s.skill_category = $skill_category,
//     s.prompt_template = $prompt_template,
//     s.input_schema = $input_schema,
//     s.output_schema = $output_schema,
//     s.version = 1,
//     s.effectiveness_score = 0.5,
//     s.usage_count = 0,
//     s.is_global = $is_global,
//     s.tenant_id = $tenant_id,
//     s.embedding = $embedding,
//     s.created_at = datetime(),
//     s.updated_at = datetime()
// RETURN s;

// =============================================================================
// ENTITY_SCHEMA NODE
// =============================================================================
// Domain-specific entity tanımları.
// Extraction işlemlerinde çıkarılacak entity tiplerini tanımlar.
//
// Properties:
// - id: Unique identifier (UUID)
// - entity_type: Entity tipi adı (Policy, Customer, Coverage vb.)
// - description: Entity'nin ne olduğu
// - properties: Property tanımları (JSON - name, type, required, description)
// - validation_rules: Doğrulama kuralları (regex, range vb.)
// - examples: Örnek değerler (JSON array)
// - context: Hangi domain'de geçerli (insurance, maintenance vb.)
// - embedding: Semantic search için vector
// - created_at: Oluşturulma zamanı
// =============================================================================

// Örnek EntitySchema
// MERGE (e:EntitySchema {id: $id})
// SET e.entity_type = 'Policy',
//     e.description = 'Sigorta poliçesi',
//     e.properties = '{"police_no": {"type": "string", "required": true}, 
//                      "baslangic_tarihi": {"type": "date", "required": true},
//                      "bitis_tarihi": {"type": "date", "required": true},
//                      "prim_tutari": {"type": "float", "required": false}}',
//     e.validation_rules = '{"police_no": "^[A-Z]{2}[0-9]{10}$"}',
//     e.examples = '[{"police_no": "PL1234567890", "baslangic_tarihi": "2024-01-01"}]',
//     e.context = 'insurance',
//     e.created_at = datetime()
// RETURN e;

// =============================================================================
// RELATIONSHIP_SCHEMA NODE
// =============================================================================
// Entity'ler arası ilişki tanımları.
//
// Properties:
// - id: Unique identifier (UUID)
// - relationship_type: İlişki tipi adı (HAS_POLICY, PROVIDES_COVERAGE vb.)
// - description: İlişkinin ne anlama geldiği
// - source_entity: Kaynak EntitySchema.entity_type
// - target_entity: Hedef EntitySchema.entity_type
// - properties: İlişki property'leri (JSON)
// - cardinality: Kardinalite (1:1, 1:N, N:M)
// - bidirectional: Çift yönlü mü
// - created_at: Oluşturulma zamanı
// =============================================================================

// Örnek RelationshipSchema
// MERGE (r:RelationshipSchema {id: $id})
// SET r.relationship_type = 'HAS_POLICY',
//     r.description = 'Müşterinin sahip olduğu poliçe',
//     r.source_entity = 'Customer',
//     r.target_entity = 'Policy',
//     r.properties = '{"since_date": {"type": "date"}}',
//     r.cardinality = '1:N',
//     r.bidirectional = false,
//     r.created_at = datetime()
// RETURN r;

// =============================================================================
// AGENT_DEFINITION NODE
// =============================================================================
// Kullanıcı tarafından oluşturulan agent tanımları.
// Agent Builder conversation sonucunda oluşur.
//
// Properties:
// - id: Unique identifier (UUID)
// - name: Agent adı
// - description: Agent'ın ne yaptığı
// - purpose: Detaylı amaç açıklaması
// - status: Durum (draft, active, archived)
// - tenant_id: Sahibi olan tenant
// - mcp_virtual_server_id: MCP Gateway'deki virtual server ID
// - config: Ek konfigürasyon (JSON)
// - created_by: Oluşturan kullanıcı
// - created_at: Oluşturulma zamanı
// - deployed_at: Deploy edilme zamanı (nullable)
// =============================================================================

// =============================================================================
// CONTEXT NODE
// =============================================================================
// Skill ve Goal'lerin geçerli olduğu domain/bağlam.
//
// Properties:
// - id: Unique identifier
// - name: Context adı (insurance, maintenance, legal vb.)
// - description: Context açıklaması
// - domain_keywords: İlgili anahtar kelimeler (JSON array)
// - parent_context: Üst context (nullable - hierarchy için)
// =============================================================================

// =============================================================================
// LEARNING NODE
// =============================================================================
// Agent'ın öğrendiği pattern'ler ve düzeltmeler.
// TENANT-ISOLATED: Her tenant sadece kendi learning'lerini görür.
//
// Properties:
// - id: Unique identifier (UUID)
// - learning_type: Öğrenme tipi (success_pattern, failure_pattern, correction, edge_case)
// - description: Öğrenilen şeyin açıklaması
// - example_input: Örnek input
// - expected_output: Beklenen output
// - actual_output: Gerçek output (hata durumunda)
// - correction: Düzeltme açıklaması
// - confidence: Güven skoru (0.0-1.0)
// - tenant_id: REQUIRED - Tenant izolasyonu için
// - skill_id: İlgili skill (denormalized for query performance)
// - learned_at: Öğrenilme zamanı
// =============================================================================

// =============================================================================
// BUILDER_SESSION NODE
// =============================================================================
// Agent Builder conversation session'ları.
//
// Properties:
// - id: Session ID (UUID)
// - tenant_id: Tenant
// - user_id: Kullanıcı
// - status: Session durumu (active, completed, abandoned)
// - current_state: State machine state
// - state_data: State-specific veriler (JSON)
// - messages: Conversation history (JSON array)
// - created_at: Başlangıç zamanı
// - updated_at: Son güncelleme
// - completed_at: Tamamlanma zamanı (nullable)
// =============================================================================
