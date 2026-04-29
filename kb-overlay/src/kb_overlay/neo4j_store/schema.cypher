// kb-overlay Neo4j şeması.
// İdempotent: tüm CREATE'ler IF NOT EXISTS ile çalışır.
// Neo4j 5.13+ varsayar (vector index için).
//
// Tasarım:
//   :Entity     — tüm canonical'ların ortak base label'ı (global lookup için)
//   :Company    — şirketler için ek label
//   :Person     — kişiler için ek label
//   :Organization, :Location, :Product — diğer tipler ek label
//   :Document   — provenance kaynağı
//
// Bu sayede `MATCH (c:Company)` aynı zamanda `MATCH (e:Entity)` global aramalar
// için hala çalışır ve fulltext/vector index'leri tek bir Entity label'ı üzerine
// kurulur (Neo4j multi-label fulltext'i destekler ama tek label daha temiz).

// ------------------------------------------------------------------ constraints

CREATE CONSTRAINT entity_canonical_id_unique IF NOT EXISTS
  FOR (e:Entity) REQUIRE e.canonical_id IS UNIQUE;

CREATE CONSTRAINT document_doc_id_unique IF NOT EXISTS
  FOR (d:Document) REQUIRE d.doc_id IS UNIQUE;

// ------------------------------------------------------------------ btree indexes

CREATE INDEX entity_norm_strict IF NOT EXISTS
  FOR (e:Entity) ON (e.norm_strict);

CREATE INDEX entity_norm_loose IF NOT EXISTS
  FOR (e:Entity) ON (e.norm_loose);

CREATE INDEX entity_type IF NOT EXISTS
  FOR (e:Entity) ON (e.entity_type);

CREATE INDEX entity_status IF NOT EXISTS
  FOR (e:Entity) ON (e.status);

// ------------------------------------------------------------------ fulltext

// Tek bir Entity label'ı üzerinde — canonical_name + normalize edilmiş anahtarlar
CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
  FOR (e:Entity) ON EACH [e.canonical_name, e.norm_strict, e.norm_loose];

// ------------------------------------------------------------------ vector

// Embedding boyutu modele göre — varsayılan paraphrase-multilingual-mpnet-base-v2 = 768
// Boyut farklıysa init_schema(vector_dim=...) ile override edilir.
// NOT: CREATE VECTOR INDEX dinamik vector.dimensions parametresi kabul etmez,
// bu yüzden Python tarafında string interpolasyonla oluşturulur (init_schema).
