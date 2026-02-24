// =============================================================================
// AGENT BUILDER - SEED DATA: BASE SKILLS
// =============================================================================
// Bu dosya temel/global skill'leri içerir.
// Bu skill'ler tüm tenant'lar tarafından kullanılabilir (is_global=true).
// =============================================================================

// =============================================================================
// OCR SKILLS - Temel görüntü işleme
// =============================================================================

// Turkce belge OCR skilli
MERGE (s:Skill {id: 'skill-ocr-turkish'})
SET s.name = 'Turkish Document OCR',
    s.description = "Turkce belgeler icin temel OCR islemi. Goruntulerden metin cikarir.",
    s.skill_category = 'ocr',
    s.prompt_template = "Bu belge goruntusundeki tum metni cikar. Turkce karakterlere dikkat et. Tablo yapilarini koru. Markdown formatinda dondur.",
    s.input_schema = '{"type": "object", "properties": {"image_path": {"type": "string"}, "language": {"type": "string", "default": "tr"}}}',
    s.output_schema = '{"type": "object", "properties": {"text": {"type": "string"}, "tables": {"type": "array"}, "confidence": {"type": "number"}}}',
    s.version = 1,
    s.effectiveness_score = 0.85,
    s.usage_count = 0,
    s.is_global = true,
    s.tenant_id = null,
    s.created_at = datetime(),
    s.updated_at = datetime();

// Tablo OCR skilli
MERGE (s:Skill {id: 'skill-ocr-table'})
SET s.name = 'Table Extraction OCR',
    s.description = "Belgelerden tablo yapilarini cikarir. Satir ve sutunlari korur.",
    s.skill_category = 'ocr',
    s.prompt_template = "Bu goruntudeki tablolari tespit et ve cikar. Her tablo icin: basliklar, satirlar ve hucre degerlerini JSON formatinda dondur. Birlesik hucreleri belirt.",
    s.input_schema = '{"type": "object", "properties": {"image_path": {"type": "string"}, "detect_merged_cells": {"type": "boolean", "default": true}}}',
    s.output_schema = '{"type": "object", "properties": {"tables": {"type": "array", "items": {"type": "object", "properties": {"headers": {"type": "array"}, "rows": {"type": "array"}}}}}}',
    s.version = 1,
    s.effectiveness_score = 0.75,
    s.usage_count = 0,
    s.is_global = true,
    s.tenant_id = null,
    s.created_at = datetime(),
    s.updated_at = datetime();

// =============================================================================
// EXTRACTION SKILLS - Entity çıkarım
// =============================================================================

// Genel entity extraction skilli
MERGE (s:Skill {id: 'skill-extract-generic'})
SET s.name = 'Generic Entity Extraction',
    s.description = "Metinden genel entity cikarimi yapar. Kisi, organizasyon, tarih, miktar gibi temel entity tipleri.",
    s.skill_category = 'extraction',
    s.prompt_template = "Bu metinden su entity tiplerini cikar: {entity_types}. Her entity icin: tip, deger, ve metindeki konum bilgisini dondur. JSON formatinda.",
    s.input_schema = '{"type": "object", "properties": {"text": {"type": "string"}, "entity_types": {"type": "array", "items": {"type": "string"}}}}',
    s.output_schema = '{"type": "object", "properties": {"entities": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}, "value": {"type": "string"}, "confidence": {"type": "number"}}}}}}',
    s.version = 1,
    s.effectiveness_score = 0.70,
    s.usage_count = 0,
    s.is_global = true,
    s.tenant_id = null,
    s.created_at = datetime(),
    s.updated_at = datetime();

// =============================================================================
// RELATIONSHIP MAPPING SKILLS
// =============================================================================

// Genel relationship mapping skilli
MERGE (s:Skill {id: 'skill-map-relationships'})
SET s.name = 'Generic Relationship Mapping',
    s.description = "Cikarilan entityler arasindaki iliskileri tespit eder.",
    s.skill_category = 'relationship_mapping',
    s.prompt_template = "Bu entity listesi verildiginde, aralarindaki iliskileri tespit et. Iliski tipleri: {relationship_types}. Her iliski icin: kaynak entity, hedef entity, iliski tipi ve guven skoru dondur.",
    s.input_schema = '{"type": "object", "properties": {"entities": {"type": "array"}, "relationship_types": {"type": "array", "items": {"type": "string"}}}}',
    s.output_schema = '{"type": "object", "properties": {"relationships": {"type": "array", "items": {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}, "type": {"type": "string"}, "confidence": {"type": "number"}}}}}}',
    s.version = 1,
    s.effectiveness_score = 0.65,
    s.usage_count = 0,
    s.is_global = true,
    s.tenant_id = null,
    s.created_at = datetime(),
    s.updated_at = datetime();

// =============================================================================
// SKILL DEPENDENCIES
// =============================================================================

// Generic extraction, OCR bagimli
MATCH (s1:Skill {id: 'skill-extract-generic'})
MATCH (s2:Skill {id: 'skill-ocr-turkish'})
MERGE (s1)-[r:DEPENDS_ON]->(s2)
SET r.dependency_type = 'optional';

// Relationship mapping, extraction bagimli
MATCH (s1:Skill {id: 'skill-map-relationships'})
MATCH (s2:Skill {id: 'skill-extract-generic'})
MERGE (s1)-[r:DEPENDS_ON]->(s2)
SET r.dependency_type = 'required';

// =============================================================================
// SKILL-CONTEXT ASSOCIATIONS
// =============================================================================

// OCR skilleri document-processing contextinde
MATCH (s:Skill) WHERE s.id IN ['skill-ocr-turkish', 'skill-ocr-table']
MATCH (c:Context {id: 'ctx-document-processing'})
MERGE (s)-[:APPLICABLE_IN]->(c);

// Extraction ve relationship skilleri tum alt contextlerde
MATCH (s:Skill) WHERE s.id IN ['skill-extract-generic', 'skill-map-relationships']
MATCH (c:Context) WHERE c.id IN ['ctx-insurance', 'ctx-maintenance', 'ctx-legal', 'ctx-financial']
MERGE (s)-[:APPLICABLE_IN]->(c);
