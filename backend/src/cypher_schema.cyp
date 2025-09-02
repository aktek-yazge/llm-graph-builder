// Neo4j Database Schema - Cypher CREATE Statements
// Generated automatically from existing data

// === NODE EXAMPLES ===
CREATE (:Chunk {position: 1, id: "ad82dcdb0d35d20745b6eec1ebd47f07a833fd75", text: "## YAT SİGORTA POLİÇESİ

|   ACENTE NO | POLİÇE NO       | ÜRÜN KODU   | BAŞLANGIÇ TARİHİ   | BİTİŞ ...", content_offset: 0, fileName: "Ahmet Cemal Dördüncü - KİRAZ TEKNE POLİÇESİ - TÜRKÇE 2020.pdf"})
CREATE (:Customer {policyCount: 1, createdAt: "2025-09-02T21:38:19.218000000+00:00", name: "Ahmet Cemal Dördüncü", fullName: "Ahmet Cemal Dördüncü"})
CREATE (:Document {errorMessage: "", model: "openai_gpt_4o_mini", fileType: "pdf", communityNodeCount: 0, docType: "MAIN_POLICY"})
CREATE (:InsuredItem {policyCount: 1, createdAt: "2025-09-02T21:38:19.232000000+00:00", description: "KİRAZ", name: "KİRAZ"})
CREATE (:Policy {id: "Ahmet Cemal Dördüncü - KİRAZ TEKNE POLİÇESİ - TÜRKÇE 2020", insuredItem: "KİRAZ", source_file: "Ahmet Cemal Dördüncü - KİRAZ TEKNE POLİÇESİ - TÜRKÇE 2020.pdf", createdAt: "2025-09-02T21:38:19.168000000+00:00", name: "Ahmet Cemal Dördüncü - KİRAZ TEKNE POLİÇESİ - TÜRKÇE 2020"})
CREATE (:PolicyType {typeName: "Tekne Sigortası", policyCount: 1, createdAt: "2025-09-02T21:38:19.236000000+00:00", name: "Tekne Sigortası"})
CREATE (:PolicyYear {policyCount: 1, createdAt: "2025-09-02T21:38:19.228000000+00:00", name: "2020", year: 2020})

// === RELATIONSHIP PATTERNS ===
// Frequency: 41
MATCH (a:Chunk), (b:Document)
CREATE (a)-[:PART_OF]->(b)

// Frequency: 40
MATCH (a:Chunk), (b:Chunk)
CREATE (a)-[:NEXT_CHUNK]->(b)

// Frequency: 1
MATCH (a:Policy), (b:Document)
CREATE (a)-[:DOCUMENTED_IN]->(b)

// Frequency: 1
MATCH (a:Customer), (b:Document)
CREATE (a)-[:HAS_DOC]->(b)

// Frequency: 1
MATCH (a:Customer), (b:Policy)
CREATE (a)-[:HAS_POLICY]->(b)

// Frequency: 1
MATCH (a:Policy), (b:PolicyYear)
CREATE (a)-[:HAS_YEAR]->(b)

// Frequency: 1
MATCH (a:Policy), (b:InsuredItem)
CREATE (a)-[:HAS_INSURED_ITEM]->(b)

// Frequency: 1
MATCH (a:Policy), (b:PolicyType)
CREATE (a)-[:HAS_TYPE]->(b)

// Frequency: 1
MATCH (a:Document), (b:Chunk)
CREATE (a)-[:FIRST_CHUNK]->(b)
