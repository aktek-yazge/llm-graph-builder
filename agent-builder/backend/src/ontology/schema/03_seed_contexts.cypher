// =============================================================================
// AGENT BUILDER - SEED DATA: CONTEXTS
// =============================================================================
// Bu dosya başlangıç Context verilerini içerir.
// Context'ler skill ve goal'lerin geçerli olduğu domain'leri tanımlar.
// =============================================================================

// =============================================================================
// ROOT CONTEXT
// =============================================================================
MERGE (c:Context {id: 'ctx-root'})
SET c.name = 'root',
    c.description = "Tum contextlerin kok nodeu",
    c.domain_keywords = '[]',
    c.is_root = true,
    c.created_at = datetime()
RETURN c;

// =============================================================================
// DOCUMENT PROCESSING CONTEXTS
// =============================================================================

// Genel belge isleme
MERGE (c:Context {id: 'ctx-document-processing'})
SET c.name = 'document_processing',
    c.description = "Genel belge isleme ve OCR operasyonlari",
    c.domain_keywords = '["belge", "dokuman", "pdf", "ocr", "metin", "sayfa", "tablo"]',
    c.created_at = datetime();

// Sigortacilik
MERGE (c:Context {id: 'ctx-insurance'})
SET c.name = 'insurance',
    c.description = "Sigorta sektoru belgeleri - policeler, teminatlar, hasarlar",
    c.domain_keywords = '["sigorta", "police", "teminat", "prim", "hasar", "riziko", "musteri", "acente", "zeyilname"]',
    c.created_at = datetime();

// Bakim ve Teknik
MERGE (c:Context {id: 'ctx-maintenance'})
SET c.name = 'maintenance',
    c.description = "Bakim ve teknik dokumantasyon",
    c.domain_keywords = '["bakim", "ariza", "ekipman", "parca", "servis", "teknik", "manuel", "prosedur"]',
    c.created_at = datetime();

// Hukuki
MERGE (c:Context {id: 'ctx-legal'})
SET c.name = 'legal',
    c.description = "Hukuki belgeler ve sozlesmeler",
    c.domain_keywords = '["sozlesme", "hukuk", "madde", "taraf", "yukumluluk", "hak", "mahkeme", "dava"]',
    c.created_at = datetime();

// Finansal
MERGE (c:Context {id: 'ctx-financial'})
SET c.name = 'financial',
    c.description = "Finansal belgeler ve raporlar",
    c.domain_keywords = '["fatura", "odeme", "tutar", "vergi", "bilanco", "gelir", "gider", "banka"]',
    c.created_at = datetime();

// =============================================================================
// CONTEXT HIERARCHY
// =============================================================================

// Document processing altındaki context'ler
MATCH (parent:Context {id: 'ctx-document-processing'})
MATCH (child:Context) WHERE child.id IN ['ctx-insurance', 'ctx-maintenance', 'ctx-legal', 'ctx-financial']
MERGE (child)-[:CHILD_OF]->(parent);

// Root'a bağla
MATCH (root:Context {id: 'ctx-root'})
MATCH (child:Context {id: 'ctx-document-processing'})
MERGE (child)-[:CHILD_OF]->(root);
