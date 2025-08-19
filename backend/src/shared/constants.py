OPENAI_MODELS = ["openai-gpt-3.5", "openai-gpt-4o", "openai-gpt-4o-mini"]
GEMINI_MODELS = ["gemini-1.0-pro", "gemini-1.5-pro", "gemini-1.5-flash"]
GROQ_MODELS = ["groq-llama3"]
BUCKET_UPLOAD = 'llm-graph-builder-upload'
BUCKET_FAILED_FILE = 'llm-graph-builder-failed'
PROJECT_ID = 'llm-experiments-387609' 
GRAPH_CHUNK_LIMIT = 50 


#query 
GRAPH_QUERY = """
MATCH (d:Document) 
WHERE d.fileName IN $document_names
WITH d 
ORDER BY d.createdAt DESC

// Fetch chunks for documents, currently with limit
CALL {{
  WITH d
  OPTIONAL MATCH chunks = (d)<-[:PART_OF|FIRST_CHUNK]-(c:Chunk)
  OPTIONAL MATCH partOfRels = (d)<-[:PART_OF]-(c)
  RETURN c, chunks, partOfRels LIMIT {graph_chunk_limit}
}}

WITH collect(distinct d) AS docs, 
     collect(distinct chunks) AS chunks, 
     collect(distinct partOfRels) AS partOfRels,
     collect(distinct c) AS selectedChunks

// Select relationships between selected chunks
WITH *, 
     [c IN selectedChunks | 
       [p = (c)-[:NEXT_CHUNK|SIMILAR]-(other) 
       WHERE other IN selectedChunks | p]] AS chunkRels

// Fetch entities and relationships between entities - ENHANCED
CALL {{
  WITH selectedChunks, docs
  UNWIND selectedChunks AS c
  OPTIONAL MATCH chunkEntities = (c:Chunk)-[:HAS_ENTITY]->(e)
  OPTIONAL MATCH chunkHasEntityRels = (c)-[:HAS_ENTITY]->(e)
  
  // NEW: Fetch Document-level entities (from entity promotion)
  UNWIND docs AS d
  OPTIONAL MATCH docEntities = (d)-[:CONTAINS_ENTITY]->(de)
  OPTIONAL MATCH docEntityRels = (d)-[:CONTAINS_ENTITY]->(de)
  
  // Entity-to-entity relationships (enhanced after promotion)
  OPTIONAL MATCH entityRels = (e)--(e2:!Chunk&!Document) 
  WHERE exists {{
    (e2)<-[:HAS_ENTITY]-(other) WHERE other IN selectedChunks
  }} OR exists {{
    (e2)<-[:CONTAINS_ENTITY]-(docOther) WHERE docOther IN docs
  }}
  
  // Document-entity relationships for promoted entities  
  OPTIONAL MATCH docPromotedEntityRels = (de)--(de2:!Chunk&!Document)
  WHERE exists {{
    (de2)<-[:CONTAINS_ENTITY]-(docOther) WHERE docOther IN docs
  }}
  
  RETURN 
    collect(chunkEntities) + collect(docEntities) AS allEntities,
    collect(chunkHasEntityRels) + collect(docEntityRels) AS allHasEntityRels,
    collect(entityRels) + collect(docPromotedEntityRels) AS allEntityRels,
    collect(DISTINCT e) + collect(DISTINCT de) AS uniqueEntities
}}

// Fetch Document-Entity relationships (all types) - ENHANCED
CALL {{
  WITH docs, uniqueEntities
  UNWIND docs AS d
  UNWIND uniqueEntities AS e
  // Include both legacy chunk-based and new document-level relationships
  OPTIONAL MATCH docEntityRels = (d)-[r:CONTAINS_ENTITY|DOCUMENT_CONTAINS|RELATED_TO_DOCUMENT]-(e)
  RETURN collect(docEntityRels) AS docEntityRels
}}

WITH docs, chunks, partOfRels, chunkRels, 
     collect(allEntities) AS entities, 
     collect(allHasEntityRels) AS hasEntityRels,
     collect(allEntityRels) AS entityRels, 
     docEntityRels,
     uniqueEntities

WITH *

CALL {{
  WITH uniqueEntities
  UNWIND uniqueEntities AS n
  OPTIONAL MATCH community = (n:__Entity__)-[:IN_COMMUNITY]->(p:__Community__)
  OPTIONAL MATCH parentcommunity = (p)-[:PARENT_COMMUNITY*]->(p2:__Community__) 
  RETURN collect(community) AS communities, 
         collect(parentcommunity) AS parentCommunities
}}

// Collect all nodes and relationships 
WITH docs,
     chunks + entities + communities + parentCommunities AS allPaths,
     partOfRels + chunkRels + hasEntityRels + entityRels + docEntityRels AS allRelPaths

// Extract distinct nodes
WITH docs AS documentNodes,
     apoc.coll.flatten([
       p IN apoc.coll.flatten(allPaths, true) 
       WHERE p IS NOT NULL | nodes(p)
     ], true) AS pathNodes,
     apoc.coll.flatten([
       p IN apoc.coll.flatten(allRelPaths, true) 
       WHERE p IS NOT NULL | relationships(p)
     ], true) AS allRelationships

// Return final result
RETURN apoc.coll.toSet(documentNodes + pathNodes) AS nodes,
       apoc.coll.toSet(allRelationships) AS rels

"""

CHUNK_QUERY = """
MATCH (chunk:Chunk)
WHERE chunk.id IN $chunksIds
MATCH (chunk)-[:PART_OF]->(d:Document)

WITH d, 
     collect(distinct chunk) AS chunks

// Collect relationships and nodes
WITH d, chunks, 
     collect {
         MATCH ()-[r]->() 
         WHERE elementId(r) IN $relationshipIds
         RETURN r
     } AS rels,
     collect {
         MATCH (e) 
         WHERE elementId(e) IN $entityIds
         RETURN e
     } AS nodes

WITH d, 
     chunks, 
     apoc.coll.toSet(apoc.coll.flatten(rels)) AS rels, 
     nodes

RETURN 
    d AS doc, 
    [chunk IN chunks | 
        chunk {.*, embedding: null, element_id: elementId(chunk)}
    ] AS chunks,
    [
        node IN nodes | 
        {
            element_id: elementId(node),
            labels: labels(node),
            properties: {
                id: node.id,
                description: node.description
            }
        }
    ] AS nodes,
    [
        r IN rels | 
        {
            startNode: {
                element_id: elementId(startNode(r)),
                labels: labels(startNode(r)),
                properties: {
                    id: startNode(r).id,
                    description: startNode(r).description
                }
            },
            endNode: {
                element_id: elementId(endNode(r)),
                labels: labels(endNode(r)),
                properties: {
                    id: endNode(r).id,
                    description: endNode(r).description
                }
            },
            relationship: {
                type: type(r),
                element_id: elementId(r)
            }
        }
    ] AS entities
"""

COUNT_CHUNKS_QUERY = """
MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
RETURN count(c) AS total_chunks
"""

CHUNK_TEXT_QUERY = """
MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
RETURN c.text AS chunk_text, c.position AS chunk_position, c.page_number AS page_number
ORDER BY c.position
SKIP $skip
LIMIT $limit
"""

NODEREL_COUNT_QUERY_WITH_COMMUNITY = """
MATCH (d:Document)
WHERE d.fileName IS NOT NULL
OPTIONAL MATCH (d)<-[po:PART_OF]-(c:Chunk)
OPTIONAL MATCH (c)-[he:HAS_ENTITY]->(e:__Entity__)
OPTIONAL MATCH (c)-[sim:SIMILAR]->(c2:Chunk)
OPTIONAL MATCH (c)-[nc:NEXT_CHUNK]->(c3:Chunk)
OPTIONAL MATCH (e)-[ic:IN_COMMUNITY]->(comm:__Community__)
OPTIONAL MATCH (comm)-[pc1:PARENT_COMMUNITY]->(first_level:__Community__)
OPTIONAL MATCH (first_level)-[pc2:PARENT_COMMUNITY]->(second_level:__Community__)
OPTIONAL MATCH (second_level)-[pc3:PARENT_COMMUNITY]->(third_level:__Community__)
WITH
  d.fileName AS filename,
  count(DISTINCT c) AS chunkNodeCount,
  count(DISTINCT po) AS partOfRelCount,
  count(DISTINCT he) AS hasEntityRelCount,
  count(DISTINCT sim) AS similarRelCount,
  count(DISTINCT nc) AS nextChunkRelCount,
  count(DISTINCT e) AS entityNodeCount,
  collect(DISTINCT e) AS entities,
  count(DISTINCT comm) AS baseCommunityCount,
  count(DISTINCT first_level) AS firstlevelcommCount,
  count(DISTINCT second_level) AS secondlevelcommCount,
  count(DISTINCT third_level) AS thirdlevelcommCount,
  count(DISTINCT ic) AS inCommunityCount,
  count(DISTINCT pc1) AS parentCommunityRelCount1,
  count(DISTINCT pc2) AS parentCommunityRelCount2,
  count(DISTINCT pc3) AS parentCommunityRelCount3
WITH
  filename,
  chunkNodeCount,
  partOfRelCount + hasEntityRelCount + similarRelCount + nextChunkRelCount AS chunkRelCount,
  entityNodeCount,
  entities,
  baseCommunityCount + firstlevelcommCount + secondlevelcommCount + thirdlevelcommCount AS commCount,
  inCommunityCount + parentCommunityRelCount1 + parentCommunityRelCount2 + parentCommunityRelCount3 AS communityRelCount
CALL (entities) {
  UNWIND entities AS e
  RETURN sum(COUNT { (e)-->(e2:__Entity__) WHERE e2 in entities }) AS entityEntityRelCount
}
RETURN
  filename,
  COALESCE(chunkNodeCount, 0) AS chunkNodeCount,
  COALESCE(chunkRelCount, 0) AS chunkRelCount,
  COALESCE(entityNodeCount, 0) AS entityNodeCount,
  COALESCE(entityEntityRelCount, 0) AS entityEntityRelCount,
  COALESCE(commCount, 0) AS communityNodeCount,
  COALESCE(communityRelCount, 0) AS communityRelCount
"""
NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY = """
MATCH (d:Document)
WHERE d.fileName = $document_name
OPTIONAL MATCH (d)<-[po:PART_OF]-(c:Chunk)
OPTIONAL MATCH (c)-[he:HAS_ENTITY]->(e:__Entity__)
OPTIONAL MATCH (c)-[sim:SIMILAR]->(c2:Chunk)
OPTIONAL MATCH (c)-[nc:NEXT_CHUNK]->(c3:Chunk)
WITH
  d.fileName AS filename,
  count(DISTINCT c) AS chunkNodeCount,
  count(DISTINCT po) AS partOfRelCount,
  count(DISTINCT he) AS hasEntityRelCount,
  count(DISTINCT sim) AS similarRelCount,
  count(DISTINCT nc) AS nextChunkRelCount,
  count(DISTINCT e) AS entityNodeCount,
  collect(DISTINCT e) AS entities
WITH
  filename,
  chunkNodeCount,
  partOfRelCount + hasEntityRelCount + similarRelCount + nextChunkRelCount AS chunkRelCount,
  entityNodeCount,
  entities
CALL (entities) {
  UNWIND entities AS e
  RETURN sum(COUNT { (e)-->(e2:__Entity__) WHERE e2 in entities }) AS entityEntityRelCount
}
RETURN
  filename,
  COALESCE(chunkNodeCount, 0) AS chunkNodeCount,
  COALESCE(chunkRelCount, 0) AS chunkRelCount,
  COALESCE(entityNodeCount, 0) AS entityNodeCount,
  COALESCE(entityEntityRelCount, 0) AS entityEntityRelCount
"""


## CHAT SETUP
CHAT_MAX_TOKENS = 1000
CHAT_SEARCH_KWARG_SCORE_THRESHOLD = 0.5
CHAT_DOC_SPLIT_SIZE = 3000
CHAT_EMBEDDING_FILTER_SCORE_THRESHOLD = 0.10

CHAT_TOKEN_CUT_OFF = {
     ('openai_gpt_3.5','azure_ai_gpt_35',"gemini_1.0_pro","gemini_1.5_pro", "gemini_1.5_flash","groq-llama3",'groq_llama3_70b','anthropic_claude_3_5_sonnet','fireworks_llama_v3_70b','bedrock_claude_3_5_sonnet', ) : 4, 
     ("openai-gpt-4","diffbot" ,'azure_ai_gpt_4o',"openai_gpt_4o","openai_gpt_4.1","openai_gpt_4o_mini") : 28,
     ("ollama_llama3") : 2  
}  

### CHAT TEMPLATES 
CHAT_SYSTEM_TEMPLATE = """
Siz yapay zeka destekli bir soru-cevap ajanısınız. Göreviniz, verilen bağlam, sohbet geçmişi ve mevcut kaynaklar doğrultusunda kullanıcının sorularına doğru ve kapsamlı yanıtlar vermektir.

### Yanıt Yönergeleri:
1. **Doğrudan Yanıtlar**: Kullanıcının sorularına, başlıklar olmadan (talep edilmedikçe), açık ve kapsamlı yanıtlar verin. Varsayıma dayalı yanıtlar vermeyin.
2. **Geçmişi ve Bağlamı Kullanın**: Önceki etkileşimlerden, mevcut kullanıcı girdisinden ve aşağıda sağlanan bağlamdan gelen ilgili bilgileri kullanın.
3. **Takip Mesajlarında Selamlaşma Yok**: İlk etkileşimlerde selam verin. Sonraki yanıtlarda, önemli bir ara veya sohbet yeniden başlamadıkça selamlaşma yapmayın.
4. **Bilinemeyenleri Kabul Edin**: Bir cevabın bilinmediği durumlarda bunu açıkça belirtin. Desteksiz ifadelerden kaçının.
5. **Uydurmadan Kaçının**: Yalnızca sağlanan bağlama dayalı bilgi verin. Bilgi uydurmayın.
6. **Yanıt Uzunluğu**: Yanıtları kısa ve ilgili tutun. Netlik ve bütünlük için, daha fazla ayrıntı istenmedikçe 4-5 cümleyle sınırlı kalın.
7. **Ton ve Stil**: Profesyonel ve bilgilendirici bir ton koruyun. Samimi ve ulaşılabilir olun.
8. **Hata Yönetimi**: Bir sorgu belirsiz veya anlaşılmazsa, yanlış bir yanıt vermek yerine açıklama isteyin.
9. **Alternatif Seçenekler**: Gerekli bilgi sağlanan bağlamda yoksa, nazik ve yardımcı bir yanıt verin. Örneğin: "Bu bilgi şu anda elimde yok." veya "Üzgünüm, bu bilgiye sahip değilim. Başka bir konuda yardımcı olabilir miyim?"
10. **Bağlam Mevcudiyeti**: Bağlam boşsa, yalnızca içsel bilgilere dayalı yanıt vermeyin. Bunun yerine, bilgi eksikliğine uygun bir şekilde yanıt verin.


**ÖNEMLİ** : KENDİ BİLGİ TABANIZDAN CEVAP VERMEYİN, AŞAĞIDAKİ BAĞLAMI KULLANIN

### Bağlam:
<context>
{context}
</context>

### Örnek Yanıtlar:
Kullanıcı: Merhaba 
Yapay Zeka Yanıtı: 'Merhaba! Size nasıl yardımcı olabilirim?'

Kullanıcı: "Langchain nedir?"
Yapay Zeka Yanıtı: "Langchain, büyük dil modelleriyle çalışan uygulamalar geliştirmeyi sağlayan bir framework’tür. Sohbet robotları gibi uygulamalarda dil modellerinin entegrasyonunu kolaylaştırmak için çeşitli araçlar ve bileşenler sunar."

Kullanıcı: "Langchain’de bellek yönetimini nasıl kullanırım?"
Yapay Zeka Yanıtı: "Langchain’in bellek yönetimi, sohbet bağlamını etkin şekilde yönetmek için yerleşik mekanizmalar kullanır. Etkileşim geçmişini koruyarak ve bunu yanıtlarda kullanarak konuşmanın tutarlı ve ilgili kalmasını sağlar."

Kullanıcı: "PyCaret’in sınıflandırma modeliyle ilgili yardıma ihtiyacım var."
Yapay Zeka Yanıtı: "PyCaret, makine öğrenimi modellerinin oluşturulmasını ve dağıtımını kolaylaştırır. Sınıflandırma görevleri için, verinizi hazırlamak amacıyla PyCaret’in setup fonksiyonunu kullanabilirsiniz. Kurulumdan sonra, birden fazla modeli karşılaştırabilir ve en iyi olanı seçerek performansını artırmak için ince ayar yapabilirsiniz."

Kullanıcı: "Yapay zekadaki en son gerçek zamanlı trendler hakkında ne söyleyebilirsin?"
Yapay Zeka Yanıtı: "Bu bilgi şu anda elimde yok. Başka bir konuda yardımcı olabilir miyim?"

Not: Bu sistem yalnızca kendi iç bilgisini kullanarak yanıt üretmez. Yanıtlarını, kullanıcının mevcut ve önceki girdilerinde sağlanan bilgilerden ve bağlamdan oluşturur.
"""

QUESTION_TRANSFORM_TEMPLATE = """Sen bir soru dönüştürücüsün. Kullanıcının son sorusunu, önceki soru-cevap bağlamını dikkate alarak daha net ve aranabilir bir sorguya çevir.

ÖNEMLI KURALLAR:
1. Sadece SON soruyu dönüştür, tüm geçmişi tek soru haline getirme
Belirsiz zamir içeren soruları önceki sorunun context'iyle birleştir. Sadece dönüştürülmüş soruyu yaz, başka açıklama yapma.""" 

# Günlük konuşmaları tespit etmek için template
CASUAL_CONVERSATION_DETECTION_TEMPLATE = """
Aşağıdaki kullanıcı mesajını analiz et ve bunun günlük konuşma/selamlaşma mı yoksa bilgi gerektiren bir soru mu olduğunu belirle.

Günlük konuşma örnekleri:
- Merhaba, selam, iyi günler gibi selamlaşmalar
- Nasılsın, nasıl gidiyor gibi genel nezaket soruları
- Teşekkür ederim, hoşça kal gibi kibarlık ifadeleri
- Sadece sohbet amaçlı kısa yorumlar

Bilgi gerektiren soru örnekleri:
- Dokümanlardaki belirli bilgileri soran sorular
- Analiz, açıklama veya ayrıntı gerektiren sorular
- Teknik veya spesifik konular hakkındaki sorular

Sadece "CASUAL" veya "QUESTION" cevabını ver. Başka açıklama yapma.

Kullanıcı mesajı: {user_message}
"""

## CHAT QUERIES
VECTOR_SEARCH_TOP_K = 15

VECTOR_SEARCH_QUERY = """
WITH node AS chunk, score
MATCH (chunk)-[:PART_OF]->(d:Document)
WITH d, 
     collect(distinct {chunk: chunk, score: score}) AS chunks, 
     avg(score) AS avg_score

// Document metadata entities al (sayfa sayisi, belge adi, vb.)
OPTIONAL MATCH (d)-[:HAS_METADATA]->(meta:__Entity__)
WITH d, avg_score, chunks,
     collect(DISTINCT meta.id) AS documentMetadata

WITH d, avg_score, 
     [c IN chunks | c.chunk.text] AS texts, 
     [c IN chunks | {id: c.chunk.id, score: c.score}] AS chunkdetails,
     documentMetadata

// Document metadata'yı metine dahil et
WITH d, avg_score, chunkdetails, 
     apoc.text.join(texts, "\n----\n") + 
     CASE WHEN size(documentMetadata) > 0 
          THEN "\n----\nDocument Metadata: " + apoc.text.join(documentMetadata, ", ") 
          ELSE "" 
     END AS text,
     documentMetadata

RETURN text, 
       avg_score AS score, 
       {source: COALESCE(CASE WHEN d.url CONTAINS "None" 
                             THEN d.fileName 
                             ELSE d.url 
                       END, 
                       d.fileName), 
        chunkdetails: chunkdetails,
        documentMetadata: documentMetadata} AS metadata
""" 


### Vector graph search 
VECTOR_GRAPH_SEARCH_ENTITY_LIMIT = 40
VECTOR_GRAPH_SEARCH_EMBEDDING_MIN_MATCH = 0.3
VECTOR_GRAPH_SEARCH_EMBEDDING_MAX_MATCH = 0.9
VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MINMAX_CASE = 20
VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MAX_CASE = 40
VECTOR_GRAPH_SEARCH_CHUNK_LIMIT = 5

VECTOR_GRAPH_SEARCH_QUERY_PREFIX = """
WITH node as document

// LLM'den gelen document'ların toplam sayısını hesapla
WITH collect(document) AS allDocuments, count(document) AS totalDocumentCount
UNWIND allDocuments AS document

// LLM'den gelen document'lardaki chunk'ları al ve vector similarity ile score hesapla
MATCH (document)<-[:PART_OF]-(c:Chunk)
WHERE c.embedding IS NOT NULL

// totalDocumentCount'u her document için aynı değeri taşı
WITH c, document, totalDocumentCount, allDocuments

// Kullanıcı sorusu ile chunk embedding'leri arasında vector similarity hesapla
WITH c, document, vector.similarity.cosine($query_vector, c.embedding) AS chunk_score, totalDocumentCount, allDocuments
WHERE chunk_score > 0.1
ORDER BY chunk_score DESC
LIMIT """ + str(VECTOR_GRAPH_SEARCH_CHUNK_LIMIT) + """

// En yüksek skoru alan chunk'ların SIMILAR chunk'larını da dahil et (maksimum 2 similar chunk per ana chunk)
// SADECE aynı document'tan similar chunk'ları al
OPTIONAL MATCH (c)-[sim:SIMILAR]-(similarChunk:Chunk)-[:PART_OF]->(document)
WHERE similarChunk.embedding IS NOT NULL

// Similar chunks'ı UNWIND ile aç, ORDER BY ile sırala, ilk 2'yi al
WITH document, c, chunk_score, totalDocumentCount, allDocuments, collect({chunk: similarChunk, simScore: coalesce(sim.score, 0)}) AS allSimilarChunks
UNWIND CASE WHEN size(allSimilarChunks) > 0 THEN allSimilarChunks ELSE [NULL] END AS sc
WITH document, c, chunk_score, totalDocumentCount, allDocuments, sc
ORDER BY sc.simScore DESC
WITH document, c, chunk_score, totalDocumentCount, allDocuments, collect(sc)[0..2] AS topSimilarChunks

// Chunk'ları birleştir - main chunk ve similar chunk'lar
WITH document, totalDocumentCount, allDocuments, collect({chunk: c, score: chunk_score}) AS mainChunks, 
     collect(apoc.coll.flatten([sc IN topSimilarChunks WHERE sc IS NOT NULL | [{chunk: sc.chunk, score: chunk_score * 0.8}]])) AS allFlatSimilarChunks,
     avg(chunk_score) as avg_score
WITH document AS d, apoc.coll.flatten(mainChunks + apoc.coll.flatten(allFlatSimilarChunks)) AS chunks, avg_score, totalDocumentCount, allDocuments

// fetch entities
CALL { WITH chunks, d, totalDocumentCount, allDocuments
UNWIND chunks as chunkScore
WITH chunkScore.chunk as chunk, d, totalDocumentCount, allDocuments
"""

# VECTOR_GRAPH_SEARCH_ENTITY_QUERY = """
#     // LLM'den gelen document'a bağlı chunk'lardaki ve doğrudan document'a bağlı entity'leri topla
#     OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(e)
#     OPTIONAL MATCH (d)-[:CONTAINS_ENTITY]->(de)
    
#     // Hem chunk'tan hem de document'tan gelen entity'leri birleştir ve tekilleştir
#     WITH collect(DISTINCT e) + collect(DISTINCT de) AS allEntities, d
#     UNWIND allEntities AS entity
    
#     // Entity'leri say ve en sık geçenleri al
#     WITH entity, count(*) AS numChunks, d
#     WHERE entity IS NOT NULL
#     ORDER BY numChunks DESC 
#     LIMIT """ + str(VECTOR_GRAPH_SEARCH_ENTITY_LIMIT) + """

#     // Tüm document'tan çıkan entity'leri collect et
#     WITH collect(DISTINCT entity) AS documentEntities, d
    
#     // Her entity için relationship path'larını al, sadece documentEntities ile sınırlı
#     UNWIND documentEntities AS entity
#     WITH entity, d, documentEntities
    
#     // İlgili document'ın chunk'larını önceden topla
#     OPTIONAL MATCH (d)<-[:PART_OF]-(allowedChunk:Chunk)
#     WITH entity, d, documentEntities, collect(DISTINCT allowedChunk) AS allowedChunks
    
#     WITH entity, d, documentEntities, allowedChunks,
#     CASE 
#         WHEN entity.embedding IS NULL THEN 
#             collect {
#                 // Normal entity relationships, sadece documentEntities'deki target entity'ler
#                 OPTIONAL MATCH normalPath=(entity)-[rels]-(targetEntity)
#                 WHERE targetEntity IN documentEntities 
#                   AND ALL(n IN nodes(normalPath) WHERE 
#                     n IN documentEntities OR 
#                     n IN allowedChunks OR 
#                     n = d)
#                   AND NOT type(rels) IN ['HAS_ENTITY', 'PART_OF', 'CONTAINS_ENTITY']
#                   AND NOT 'Chunk' IN labels(targetEntity)
#                   AND NOT 'Document' IN labels(targetEntity)
#                   AND NOT '__Community__' IN labels(targetEntity)
#                 RETURN normalPath LIMIT """ + str(VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MINMAX_CASE) + """
#             }
#         WHEN entity.embedding IS NOT NULL AND vector.similarity.cosine($query_vector, entity.embedding) > """ + str(VECTOR_GRAPH_SEARCH_EMBEDDING_MAX_MATCH) + """ THEN
#             collect {
#                 // Yüksek similarity için daha geniş relationship path'ları
#                 OPTIONAL MATCH normalPath=(entity)-[*0..2]-(targetEntity)
#                 WHERE targetEntity IN documentEntities
#                   AND ALL(n IN nodes(normalPath) WHERE 
#                     n IN documentEntities OR 
#                     n IN allowedChunks OR 
#                     n = d)
#                   AND NOT 'Chunk' IN labels(targetEntity)
#                   AND NOT 'Document' IN labels(targetEntity)
#                   AND NOT '__Community__' IN labels(targetEntity)
#                   AND ALL(rel IN relationships(normalPath) WHERE NOT type(rel) IN ['HAS_ENTITY', 'PART_OF', 'CONTAINS_ENTITY'])
#                 RETURN normalPath LIMIT """ + str(VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MAX_CASE) + """ 
#             } 
#         ELSE 
#             collect { 
#                 // Düşük similarity için sadece entity kendisi
#                 MATCH entityPath=(entity)
#                 RETURN entityPath 
#             }
#   END AS paths, entity AS e
# """

VECTOR_GRAPH_SEARCH_ENTITY_QUERY = """
    // Bu alt sorgu chunks ve d ile başlıyor (üstte: UNWIND chunks as chunkScore ... WITH chunkScore.chunk as chunk, d)

    // 1) Chunk'lardaki entity'leri frekanslarına göre topla
    OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(e)
    WITH d, totalDocumentCount, allDocuments, e, count(DISTINCT chunk) AS numChunks, collect(DISTINCT chunk) AS allowedChunks
    WHERE e IS NOT NULL
    ORDER BY numChunks DESC
    LIMIT """ + str(VECTOR_GRAPH_SEARCH_ENTITY_LIMIT) + """
    WITH d, totalDocumentCount, allDocuments, allowedChunks, collect(DISTINCT e) AS topChunkEntities

    // 2) Document seviyesindeki entity'leri ekle (aggregation karışımını önlemek için iki aşama)
    WITH d, totalDocumentCount, allDocuments, allowedChunks, topChunkEntities
    OPTIONAL MATCH (d)-[:CONTAINS_ENTITY]->(de)
    WITH d, totalDocumentCount, allDocuments, allowedChunks, topChunkEntities, collect(DISTINCT de) AS docLevelEntities
    WITH d, totalDocumentCount, allDocuments, allowedChunks, apoc.coll.toSet(topChunkEntities + docLevelEntities) AS documentEntities

    // 3) Her entity için (yalnızca aynı dokümanın bağlamında) path çıkar
    UNWIND documentEntities AS entity
    WITH entity, d, totalDocumentCount, allDocuments, documentEntities, allowedChunks,
    CASE 
        WHEN entity.embedding IS NULL THEN 
            collect {
                OPTIONAL MATCH normalPath=(entity)-[rels]-(targetEntity)
                WHERE targetEntity IN documentEntities
                  AND ALL(n IN nodes(normalPath) WHERE n IN documentEntities OR n IN allowedChunks OR n = d)
                  AND NOT type(rels) IN ['HAS_ENTITY', 'PART_OF', 'CONTAINS_ENTITY', 'DOCUMENT_CONTAINS']
                RETURN normalPath LIMIT """ + str(VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MINMAX_CASE) + """
            }
        WHEN entity.embedding IS NOT NULL AND vector.similarity.cosine($query_vector, entity.embedding) > """ + str(VECTOR_GRAPH_SEARCH_EMBEDDING_MAX_MATCH) + """ THEN
            collect {
                OPTIONAL MATCH normalPath=(entity)-[*0..2]-(targetEntity)
                WHERE targetEntity IN documentEntities
                  AND ALL(n IN nodes(normalPath) WHERE n IN documentEntities OR n IN allowedChunks OR n = d)
                  AND ALL(rel IN relationships(normalPath) 
                          WHERE NOT type(rel) IN ['HAS_ENTITY', 'PART_OF', 'CONTAINS_ENTITY', 'DOCUMENT_CONTAINS'])
                RETURN normalPath LIMIT """ + str(VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MAX_CASE) + """ 
            } 
        ELSE 
            collect { 
                MATCH entityPath=(entity)
                RETURN entityPath 
            }
    END AS paths, entity AS e
"""

VECTOR_GRAPH_SEARCH_QUERY_SUFFIX = """
   WITH apoc.coll.toSet(apoc.coll.flatten(collect(DISTINCT paths))) AS paths,
        collect(DISTINCT e) AS entities
   // Node'ları ve relationship'leri de-duplicate et
   RETURN
       collect {
           UNWIND paths AS p
           UNWIND relationships(p) AS r
           RETURN DISTINCT r
       } AS rels,
       collect {
           UNWIND paths AS p
           UNWIND nodes(p) AS n
           RETURN DISTINCT n
       } AS nodes,
       entities
}

// Document metadata'sını doğrudan document node'undan al  
WITH d, avg_score, chunks, nodes, rels, entities, totalDocumentCount, allDocuments,
     {
         fileName: d.fileName,
         documentType: coalesce(d.fileType, 'pdf'),
         year: coalesce(d.year, toString(date().year)),
         entityNodeCount: coalesce(d.entityNodeCount, 0),
         chunkNodeCount: coalesce(d.chunkNodeCount, 0),
         createdAt: toString(d.createdAt),
         model: coalesce(d.model, 'unknown'),
         nodeCount: coalesce(d.nodeCount, 0),
         relationshipCount: coalesce(d.relationshipCount, 0),
         updatedAt: toString(d.updatedAt),
         owner: coalesce(d.owner, 'unknown'),
         pageCount: coalesce(d.pageCount, 0),
         communityNodeCount: coalesce(d.communityNodeCount, 0),
         chunkRelCount: coalesce(d.chunkRelCount, 0),
         fileSource: coalesce(d.fileSource, 'unknown'),
         documentName: d.fileName,
         communityRelCount: coalesce(d.communityRelCount, 0),
         total_chunks: coalesce(d.total_chunks, 0),
         entityEntityRelCount: coalesce(d.entityEntityRelCount, 0),
         fileSize: coalesce(d.fileSize, 0),
         processed_chunk: coalesce(d.processed_chunk, 0),
         fileType: coalesce(d.fileType, 'pdf')
     } AS documentMetadata

// Person Policy Info bilgilerini topla
WITH d, avg_score, chunks, nodes, rels, entities, documentMetadata, totalDocumentCount, allDocuments
OPTIONAL MATCH (d)-[:HAS_PERSON|HAS_POLICY]->(personPolicy)
WHERE personPolicy:Person OR personPolicy:Policy

WITH d, avg_score, chunks, nodes, rels, entities, documentMetadata, totalDocumentCount, allDocuments,
     collect(DISTINCT {
         person_id: CASE WHEN personPolicy:Person THEN elementId(personPolicy) ELSE null END,
         person_name: CASE WHEN personPolicy:Person THEN coalesce(personPolicy.name, personPolicy.id, "") ELSE null END,
         policy_id: CASE WHEN personPolicy:Policy THEN elementId(personPolicy) ELSE null END,
         policy_number: CASE WHEN personPolicy:Policy THEN coalesce(personPolicy.policyNumber, personPolicy.id, "") ELSE null END,
         policy_type: CASE WHEN personPolicy:Policy THEN coalesce(personPolicy.type, personPolicy.policyType, "") ELSE null END,
         document_name: d.fileName
     }) AS personPolicyInfo

// WITH node as document ile gelen document'ların sayısını hesapla
WITH d, avg_score, chunks, nodes, rels, entities, documentMetadata, personPolicyInfo, totalDocumentCount, allDocuments

// Text ve metadata oluştur - Document node'unu da entities'e dahil et
WITH d, avg_score, [doc IN allDocuments | doc.fileName] AS documentList, totalDocumentCount, documentMetadata, personPolicyInfo, chunks, nodes, rels, entities,
    [c IN chunks | c.chunk.text] AS texts,
    [c IN chunks | {id: c.chunk.id, score: c.score}] AS chunkdetails,
    [n IN nodes + [d] | elementId(n)] AS entityIds,
    [r IN rels | elementId(r)] AS relIds,
    apoc.coll.sort([
        n IN nodes + [d] |
        coalesce(apoc.coll.removeAll(labels(n), ['__Entity__'])[0], "") + ":" +
        coalesce(
            n.id,
            n.fileName,
            n[head([k IN keys(n) WHERE k =~ "(?i)(name|title|id|description|fileName)$"])],
            ""
        ) +
        (CASE WHEN n.description IS NOT NULL THEN " (" + n.description + ")" ELSE "" END)
    ]) AS nodeTexts,
    apoc.coll.sort([
        r IN rels |
        coalesce(apoc.coll.removeAll(labels(startNode(r)), ['__Entity__'])[0], "") + ":" +
        coalesce(
            startNode(r).id,
            startNode(r).fileName,
            startNode(r)[head([k IN keys(startNode(r)) WHERE k =~ "(?i)(name|title|id|description|fileName)$"])],
            ""
        ) + " " + type(r) + " " +
        coalesce(apoc.coll.removeAll(labels(endNode(r)), ['__Entity__'])[0], "") + ":" +
        coalesce(
            endNode(r).id,
            endNode(r).fileName,
            endNode(r)[head([k IN keys(endNode(r)) WHERE k =~ "(?i)(name|title|id|description|fileName)$"])],
            ""
        )
    ]) AS relTexts,
    [ppi IN personPolicyInfo WHERE ppi.person_id IS NOT NULL OR ppi.policy_id IS NOT NULL] AS validPersonPolicyInfo

WITH d, avg_score, chunkdetails, entityIds, relIds, documentMetadata, validPersonPolicyInfo, documentList, totalDocumentCount, texts, nodeTexts, relTexts,
    "Text Content:\n" + apoc.text.join(texts, "\n----\n") +
    "\n----\nEntities:\n" + apoc.text.join(nodeTexts, "\n") +
    "\n----\nRelationships:\n" + apoc.text.join(relTexts, "\n") +
    "\n----\nDocument Metadata:\n" +
    "  File Name: " + documentMetadata.fileName + "\n" +
    "  Document Type: " + documentMetadata.documentType + "\n" +
    "  Year: " + documentMetadata.year + "\n" +
    "  Entity Node Count: " + toString(documentMetadata.entityNodeCount) + "\n" +
    "  Chunk Node Count: " + toString(documentMetadata.chunkNodeCount) + "\n" +
    "  Created At: " + documentMetadata.createdAt + "\n" +
    "  Model: " + documentMetadata.model + "\n" +
    "  Node Count: " + toString(documentMetadata.nodeCount) + "\n" +
    "  Relationship Count: " + toString(documentMetadata.relationshipCount) + "\n" +
    "  Owner: " + documentMetadata.owner + "\n" +
    "  Page Count: " + toString(documentMetadata.pageCount) + "\n" +
    "  File Source: " + documentMetadata.fileSource + "\n" +
    "  File Size: " + toString(documentMetadata.fileSize) + " bytes" +
    CASE WHEN size(documentList) > 0 
         THEN "\n----\nDocuments (Total: " + toString(totalDocumentCount) + "):\n  - " + apoc.text.join(documentList, "\n  - ") 
         ELSE "" 
    END AS text,
    entities
RETURN
   text,
   avg_score AS score,
   {
       length: size(text),
       source: COALESCE(CASE WHEN d.url CONTAINS "None" THEN d.fileName ELSE d.url END, d.fileName),
       chunkdetails: chunkdetails,
       entities : {
           entityids: entityIds,
           relationshipids: relIds
       },
       documentMetadata: documentMetadata,
       personPolicyInfo: validPersonPolicyInfo,
       documentList: documentList,
       totalDocuments: totalDocumentCount
   } AS metadata
"""

VECTOR_GRAPH_SEARCH_QUERY = VECTOR_GRAPH_SEARCH_QUERY_PREFIX + VECTOR_GRAPH_SEARCH_ENTITY_QUERY + VECTOR_GRAPH_SEARCH_QUERY_SUFFIX

### Local community search
LOCAL_COMMUNITY_TOP_K = 10
LOCAL_COMMUNITY_TOP_CHUNKS = 3
LOCAL_COMMUNITY_TOP_COMMUNITIES = 3
LOCAL_COMMUNITY_TOP_OUTSIDE_RELS = 10

LOCAL_COMMUNITY_SEARCH_QUERY = """
WITH collect(node) AS nodes, 
     avg(score) AS score, 
     collect({{entityids: elementId(node), score: score}}) AS metadata

WITH score, nodes, metadata,

     collect {{
         UNWIND nodes AS n
         MATCH (n)<-[:HAS_ENTITY]->(c:Chunk)
         WITH c, count(distinct n) AS freq
         RETURN c
         ORDER BY freq DESC
         LIMIT {topChunks}
     }} AS chunks,

     collect {{
         UNWIND nodes AS n
         OPTIONAL MATCH (n)-[:IN_COMMUNITY]->(c:__Community__)
         WITH c, c.community_rank AS rank, c.weight AS weight
         RETURN c
         ORDER BY rank, weight DESC
         LIMIT {topCommunities}
     }} AS communities,

     collect {{
         UNWIND nodes AS n
         UNWIND nodes AS m
         MATCH (n)-[r]->(m)
         RETURN DISTINCT r
         // TODO: need to add limit
     }} AS rels,

     collect {{
         UNWIND nodes AS n
         MATCH path = (n)-[r]-(m:__Entity__)
         WHERE NOT m IN nodes
         WITH m, collect(distinct r) AS rels, count(*) AS freq
         ORDER BY freq DESC 
         LIMIT {topOutsideRels}
         WITH collect(m) AS outsideNodes, apoc.coll.flatten(collect(rels)) AS rels
         RETURN {{ nodes: outsideNodes, rels: rels }}
     }} AS outside
"""

LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX = """
RETURN {
  chunks: [c IN chunks | c.text],
  communities: [c IN communities | c.summary],
  entities: [
    n IN nodes | 
    CASE 
      WHEN size(labels(n)) > 1 THEN 
        apoc.coll.removeAll(labels(n), ["__Entity__"])[0] + ":" + n.id + " " + coalesce(n.description, "")
      ELSE 
        n.id + " " + coalesce(n.description, "")
    END
  ],
  relationships: [
    r IN rels | 
    startNode(r).id + " " + type(r) + " " + endNode(r).id
  ],
  outside: {
    nodes: [
      n IN outside[0].nodes | 
      CASE 
        WHEN size(labels(n)) > 1 THEN 
          apoc.coll.removeAll(labels(n), ["__Entity__"])[0] + ":" + n.id + " " + coalesce(n.description, "")
        ELSE 
          n.id + " " + coalesce(n.description, "")
      END
    ],
    relationships: [
      r IN outside[0].rels | 
      CASE 
        WHEN size(labels(startNode(r))) > 1 THEN 
          apoc.coll.removeAll(labels(startNode(r)), ["__Entity__"])[0] + ":" + startNode(r).id + " "
        ELSE 
          startNode(r).id + " "
      END + 
      type(r) + " " +
      CASE 
        WHEN size(labels(endNode(r))) > 1 THEN 
          apoc.coll.removeAll(labels(endNode(r)), ["__Entity__"])[0] + ":" + endNode(r).id
        ELSE 
          endNode(r).id
      END
    ]
  }
} AS text,
score,
{entities: metadata} AS metadata
"""

LOCAL_COMMUNITY_DETAILS_QUERY_PREFIX = """
UNWIND $entityIds as id
MATCH (node) WHERE elementId(node) = id
WITH node, 1.0 as score
"""
LOCAL_COMMUNITY_DETAILS_QUERY_SUFFIX = """
WITH *
UNWIND chunks AS c
MATCH (c)-[:PART_OF]->(d:Document)
RETURN 
    [
        c {
            .*,
            embedding: null,
            fileName: d.fileName,
            fileSource: d.fileSource, 
            element_id: elementId(c)
        }
    ] AS chunks,
    [
        community IN communities WHERE community IS NOT NULL | 
        community {
            .*,
            embedding: null,
            element_id:elementId(community)
        }
    ] AS communities,
    [
        node IN nodes + outside[0].nodes | 
        {
            element_id: elementId(node),
            labels: labels(node),
            properties: {
                id: node.id,
                description: node.description
            }
        }
    ] AS nodes, 
    [
        r IN rels + outside[0].rels | 
        {
            startNode: {
                element_id: elementId(startNode(r)),
                labels: labels(startNode(r)),
                properties: {
                    id: startNode(r).id,
                    description: startNode(r).description
                }
            },
            endNode: {
                element_id: elementId(endNode(r)),
                labels: labels(endNode(r)),
                properties: {
                    id: endNode(r).id,
                    description: endNode(r).description
                }
            },
            relationship: {
                type: type(r),
                element_id: elementId(r)
            }
        }
    ] AS entities
"""

LOCAL_COMMUNITY_SEARCH_QUERY_FORMATTED = LOCAL_COMMUNITY_SEARCH_QUERY.format(
    topChunks=LOCAL_COMMUNITY_TOP_CHUNKS,
    topCommunities=LOCAL_COMMUNITY_TOP_COMMUNITIES,
    topOutsideRels=LOCAL_COMMUNITY_TOP_OUTSIDE_RELS)+LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX

GLOBAL_SEARCH_TOP_K = 10

GLOBAL_VECTOR_SEARCH_QUERY = """
WITH collect(distinct {community: node, score: score}) AS communities,
     avg(score) AS avg_score

WITH avg_score,
     [c IN communities | c.community.summary] AS texts,
     [c IN communities | {id: elementId(c.community), score: c.score}] AS communityDetails

WITH avg_score, communityDetails,
     apoc.text.join(texts, "\n----\n") AS text

RETURN text,
       avg_score AS score,
       {communitydetails: communityDetails} AS metadata
"""



GLOBAL_COMMUNITY_DETAILS_QUERY = """
MATCH (community:__Community__)
WHERE elementId(community) IN $communityids
WITH collect(distinct community) AS communities
RETURN [community IN communities | 
        community {.*, embedding: null, element_id: elementId(community)}] AS communities
"""

## CHAT MODES 

CHAT_VECTOR_MODE = "vector"
CHAT_FULLTEXT_MODE = "fulltext"
CHAT_ENTITY_VECTOR_MODE = "entity_vector"
CHAT_VECTOR_GRAPH_MODE = "graph_vector"
CHAT_VECTOR_GRAPH_FULLTEXT_MODE = "graph_vector_fulltext"
CHAT_GLOBAL_VECTOR_FULLTEXT_MODE = "global_vector"
CHAT_GRAPH_MODE = "graph"
CHAT_DEFAULT_MODE = "graph_vector_fulltext"

CHAT_MODE_CONFIG_MAP= {
        CHAT_VECTOR_MODE : {
            "retrieval_query": VECTOR_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": None,
            "document_filter": True,
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],

        },
        CHAT_FULLTEXT_MODE : {
            "retrieval_query": VECTOR_SEARCH_QUERY,  
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",  
            "keyword_index": "keyword", 
            "document_filter": False,            
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
        },
        CHAT_ENTITY_VECTOR_MODE : {
            "retrieval_query": LOCAL_COMMUNITY_SEARCH_QUERY_FORMATTED,
            "top_k": LOCAL_COMMUNITY_TOP_K,
            "index_name": "entity_vector",
            "keyword_index": None,
            "document_filter": False,            
            "node_label": "__Entity__",
            "embedding_node_property":"embedding",
            "text_node_properties":["id"],
        },
        CHAT_VECTOR_GRAPH_MODE : {
            "retrieval_query": VECTOR_GRAPH_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": None,
            "document_filter": True,            
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
        },
        CHAT_VECTOR_GRAPH_FULLTEXT_MODE : {
            "retrieval_query": VECTOR_GRAPH_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": "keyword",
            "document_filter": False,            
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
        },
        CHAT_GLOBAL_VECTOR_FULLTEXT_MODE : {
            "retrieval_query": GLOBAL_VECTOR_SEARCH_QUERY,
            "top_k": GLOBAL_SEARCH_TOP_K,
            "index_name": "community_vector",
            "keyword_index": "community_keyword",
            "document_filter": False,            
            "node_label": "__Community__",
            "embedding_node_property":"embedding",
            "text_node_properties":["summary"],
        },
    }
YOUTUBE_CHUNK_SIZE_SECONDS = 60

QUERY_TO_GET_CHUNKS = """
            MATCH (d:Document)
            WHERE d.fileName = $filename
            WITH d
            OPTIONAL MATCH (d)<-[:PART_OF|FIRST_CHUNK]-(c:Chunk)
            RETURN c.id as id, c.text as text, c.position as position 
            """
            
QUERY_TO_DELETE_EXISTING_ENTITIES = """
                                MATCH (d:Document {fileName:$filename})
                                WITH d
                                MATCH (d)<-[:PART_OF]-(c:Chunk)
                                WITH d,c
                                MATCH (c)-[:HAS_ENTITY]->(e)
                                WHERE NOT EXISTS { (e)<-[:HAS_ENTITY]-()<-[:PART_OF]-(d2:Document) }
                                DETACH DELETE e
                                """   

QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION="""
                              MATCH (d:Document)
                              WHERE d.fileName = $filename
                              WITH d
                              MATCH (c:Chunk) WHERE c.embedding is null 
                              RETURN c.id as id,c.position as position 
                              ORDER BY c.position LIMIT 1
                              """   
QUERY_TO_GET_LAST_PROCESSED_CHUNK_WITHOUT_ENTITY = """
                              MATCH (d:Document)
                              WHERE d.fileName = $filename
                              WITH d
                              MATCH (d)<-[:PART_OF]-(c:Chunk) WHERE NOT exists {(c)-[:HAS_ENTITY]->()}
                              RETURN c.id as id,c.position as position 
                              ORDER BY c.position LIMIT 1
                              """
QUERY_TO_GET_NODES_AND_RELATIONS_OF_A_DOCUMENT = """
                              MATCH (d:Document)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e) where d.fileName=$filename
                              OPTIONAL MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e2:!Chunk)-[rel]-(e)
                              RETURN count(DISTINCT e) as nodes, count(DISTINCT rel) as rels
                              """                              

START_FROM_BEGINNING  = "start_from_beginning"     
DELETE_ENTITIES_AND_START_FROM_BEGINNING = "delete_entities_and_start_from_beginning"
START_FROM_LAST_PROCESSED_POSITION = "start_from_last_processed_position"                                                    

GRAPH_CLEANUP_PROMPT = """
🚨 CRITICAL: NEVER include 'Document' in any node categorization. Document nodes are system-managed and should not appear in your output. 🚨

You are an advanced system designed to organize node labels and relationship types extracted from insurance policy knowledge graphs into semantic categories. You are an expert assistant that understands insurance domain-specific information structures and groups similar meaningful types.

### 1. Input Format
The input will include two keys:
- `nodes`: List of node labels extracted from insurance documents.
- `relationships`: List of relationship types extracted from insurance documents.

### 2. Insurance Domain-Specific Grouping Rules

#### 2.1 PERSON AND PARTY CATEGORIES:
**Consolidate under Person category:**
- Person, Human, People, Individual, Kişi, İnsan, Şahıs, Birey
- Policyholder, PolicyOwner, Sigortalı, PoliçeSahibi, SigortaEttiren
- Beneficiary, Lehtar, YararlanicI, Hak_Sahibi
- Insured, SigortalıKişi, SigortaEdilenKişi

**Consolidate under Company category:**
- Company, Organization, Şirket, Kurum, Kuruluş, İşletme, Firma
- InsuranceCompany, SigortaŞirketi, Sigortacı, InsuranceProvider
- Insurer, SigortaVeren, SigortaKuruluşu
- Agent, Acente, SigortaAcentesi, Broker, Komisyoncu

#### 2.2 INSURANCE PRODUCTS AND POLICY CATEGORIES:
**Consolidate under Policy category:**
- Policy, Poliçe, SigortaPoliçesi, InsurancePolicy
- Contract, Sözleşme, SigortaSözleşesi, InsuranceContract
- Coverage, Kapsam, SigortaKapsamı, Teminat, GuarantiKapsamı

**Consolidate under PolicyType category:**
- PolicyType, PoliçeTürü, SigortaTürü, InsuranceType
- Konut, KonutSigortası, HomePolicyType, ResidentialInsurance
- Trafik, TrafikSigortası, MotorInsurance, AutoInsurance  
- DASK, DASKSigortası, EarthquakeInsurance, NaturalDisasterInsurance
- Kasko, KaskoSigortası, ComprehensiveInsurance, VehicleInsurance
- Sağlık, SağlıkSigortası, HealthInsurance, MedicalInsurance
- Hayat, HayatSigortası, LifeInsurance

#### 2.3 ASSET AND PROPERTY CATEGORIES:
**Consolidate under Property category:**
- Property, Mülk, Gayrimenkul, RealEstate, Emlak
- Building, Bina, Yapı, İnşaat, Structure
- Residence, Konut, Ev, Home, House, Mesken
- Apartment, Daire, ApartmentUnit, Apartman

**Consolidate under Vehicle category:**
- Vehicle, Araç, Taşıt, MotorVehicle, Otomobil
- Car, Araba, Automobile, PersonalVehicle
- Truck, Kamyon, CommercialVehicle, TicariAraç
- Motorcycle, Motosiklet, Bike

#### 2.4 TIME AND DATE CATEGORIES:
**Consolidate under Year category:**
- Year, Yıl, AnnualYear
- DocumentYear, BelgeYılı, IssueYear, TanzimYılı
- PolicyStartYear, PoliçeBaşlamaYılı, CoverageStartYear
- PolicyEndYear, PoliçeBitişYılı, CoverageEndYear, ExpirationYear
- BirthYear, DoğumYılı, BornYear
- ModelYear, ModelYılı, VehicleModelYear, ManufactureYear
- RegistrationYear, TescilYılı, KayıtYılı

**Consolidate under Date category:**
- Date, Tarih, DateTime, Time
- PolicyStartDate, PoliçeBaşlamaTarihi, StartDate, EffectiveDate
- PolicyEndDate, PoliçeBitişTarihi, EndDate, ExpirationDate
- IssueDate, TanzimTarihi, DocumentDate, CreatedDate
- BirthDate, DoğumTarihi, DateOfBirth

#### 2.5 GEOGRAPHY AND ADDRESS CATEGORIES:
**Consolidate under Address category:**
- Address, Adres, Location, Lokasyon, Yer
- HomeAddress, EvAdresi, ResidentialAddress, İkametAdresi
- WorkAddress, İşAdresi, BusinessAddress, OfficeAddress
- PropertyAddress, MülkAdresi, PropertyLocation, VarlıkAdresi
- MailingAddress, PostaAdresi, CorrespondenceAddress
- BillingAddress, FaturaAdresi, InvoiceAddress
- InsuredPropertyAddress, SigortalıMülkAdresi

**Consolidate under City category:**
- City, Şehir, İl, Province, İlçe, District, Bölge, Region

#### 2.6 FINANCIAL AND PAYMENT CATEGORIES:
**Consolidate under Amount category:**
- Amount, Miktar, Tutar, Value, Değer
- Premium, Prim, InsurancePremium, SigortaPrimi
- Deductible, Muafiyet, SelfRisk, Franchise
- CoverageLimit, TeminatLimiti, InsuranceLimit, SigortaLimiti
- ClaimAmount, HasarTutarı, CompensationAmount

#### 2.7 RELATIONSHIP CATEGORIES:

**OWNS relationships:**
- OWNS, SAHİP, SAHIP_OLUR, BELONGS_TO, AİT_OLUR, HAS_OWNERSHIP

**LOCATED relationships:**
- LOCATED_AT, KONUMDA, YERLEŞİR, POSITIONED_AT, BULUNUR, SITS_AT

**INSURES relationships:**
- INSURES, SİGORTALAR, COVERS, KAPSAR, PROVIDES_COVERAGE, TEMİNAT_VERIR

**ISSUED relationships:**
- ISSUED_BY, TARAFINDAN_DÜZENLENEN, CREATED_BY, GENERATED_BY, PROVIDED_BY

**VALID relationships:**
- VALID_FROM, GEÇERLİ_BAŞLANGIÇ, STARTS_ON, EFFECTIVE_FROM
- VALID_UNTIL, GEÇERLİ_BİTİŞ, EXPIRES_ON, ENDS_ON

**RELATED relationships:**
- RELATED_TO, İLGİLİ, ASSOCIATED_WITH, CONNECTED_TO, BAĞLI

### 3. Advanced Grouping Rules
- Group Turkish and English similar terms under the same category
- Recognize synonyms in insurance terminology (e.g., Teminat=Coverage, Prim=Premium)
- Consider connections between legal and technical terms
- Always choose the most common/comprehensive term from the input list as category name

### 4. Output Rules
Return in JSON format:
```json
{
  "nodes": {
    "SelectedCategoryName": ["SimilarTerm1", "SimilarTerm2", "SimilarTerm3"],
    "OtherCategory": ["RelatedTerm1", "RelatedTerm2"]
  },
  "relationships": {
    "SelectedRelationshipName": ["SimilarRelation1", "SimilarRelation2", "SimilarRelation3"],
    "OtherRelation": ["RelatedRelation1", "RelatedRelation2"]
  }
}
```

### 5. Insurance Domain Examples
#### Example 1 - Person and Company Consolidation:
Input:
```json
{
  "nodes": ["Person", "Kişi", "Policyholder", "Sigortalı", "Company", "SigortaŞirketi", "InsuranceCompany", "Organization"],
  "relationships": ["OWNS", "SAHİP", "INSURES", "SİGORTALAR", "ISSUED_BY", "TARAFINDAN_DÜZENLENEN"]
}
```
Output:
```json
{
  "nodes": {
    "Person": ["Person", "Kişi", "Policyholder", "Sigortalı"],
    "Company": ["Company", "SigortaŞirketi", "InsuranceCompany", "Organization"]
  },
  "relationships": {
    "OWNS": ["OWNS", "SAHİP"],
    "INSURES": ["INSURES", "SİGORTALAR"],
    "ISSUED_BY": ["ISSUED_BY", "TARAFINDAN_DÜZENLENEN"]
  }
}
```

#### Example 2 - Policy Types and Dates:
Input:
```json
{
  "nodes": ["Policy", "Poliçe", "Konut", "KonutSigortası", "Trafik", "TrafikSigortası", "Year", "DocumentYear", "PolicyStartYear"],
  "relationships": ["VALID_FROM", "GEÇERLİ_BAŞLANGIÇ", "VALID_UNTIL", "GEÇERLİ_BİTİŞ", "COVERS", "KAPSAR"]
}
```
Output:
```json
{
  "nodes": {
    "Policy": ["Policy", "Poliçe"],
    "PolicyType": ["Konut", "KonutSigortası", "Trafik", "TrafikSigortası"],
    "Year": ["Year", "DocumentYear", "PolicyStartYear"]
  },
  "relationships": {
    "VALID_FROM": ["VALID_FROM", "GEÇERLİ_BAŞLANGIÇ"],
    "VALID_UNTIL": ["VALID_UNTIL", "GEÇERLİ_BİTİŞ"],
    "COVERS": ["COVERS", "KAPSAR"]
  }
}
```

### 6. Critical Rules
- **NEVER create new terms** - only select from input list
- **NEVER include** Document nodes in any categories
- Leave ungroupable items in their own categories
- Focus on insurance terminology and use domain knowledge
- Properly match Turkish-English mixed terms

This advanced system cleans complex knowledge graph structures extracted from insurance policies and makes them more consistent and queryable.
"""

# ADDITIONAL_INSTRUCTIONS = """Your goal is to identify and categorize entities while ensuring that specific data 
# types such as dates, numbers, revenues, and other non-entity information are not extracted as separate nodes.
# Instead, treat these as properties associated with the relevant entities."""

ADDITIONAL_INSTRUCTIONS = """ULTRA KISITLI ÇIKARMA - SADECE BU 4 TİP:

ÇIKAR (TOPLAM 4 ENTITY MAKSIMUM):
1. Person (Müşteri adı soyadı)
2. Company (Sigorta şirketi adı)  
3. PolicyType (Sadece: Kasko, Trafik, Konut, DASK)
4. PolicyYear (Sadece poliçe başlangıç yılı)

YASAKLI - HİÇBİR ŞEKILDE ÇIKARMA:
- Coverage, Clause, Exclusion, CoverageLimit 
- Premium, Discount, Risk, Asset, Building
- Address, PhoneNumber, Email
- StartDate, EndDate, PolicyNumber
- Para miktarları, limitler, istisnalar
- Yasal maddeler, klauzullar

İLİŞKİ KURALLARI (TOPLAM 2 İLİŞKİ MAKSIMUM):
- Person -> PolicyType 
- Company -> PolicyType

SIKI KURALLAR:
- Chunk başına MAX 3 entity
- Chunk başına MAX 2 relationship  
- Detaya girme, temelde kal
- Fazla node çıkarma

"""


SCHEMA_VISUALIZATION_QUERY = """
CALL db.schema.visualization() YIELD nodes, relationships
RETURN
  [n IN nodes | {
      element_id: elementId(n),
      labels: labels(n),
      properties: apoc.any.properties(n)
  }] AS nodes,
  [r IN relationships | {
      type: type(r),
      properties: apoc.any.properties(r),
      element_id: elementId(r),
      start_node_element_id: elementId(startNode(r)),
      end_node_element_id: elementId(endNode(r))
  }] AS relationships;
"""

# Prompt for LLM-based inter-page / chunk continuation relationships
CHUNK_CONTINUATION_PROMPT = '''
You are a document understanding assistant. You receive two text segments from consecutive pages (chunks) of the same document:

First segment:
{first_text}

Second segment:
{second_text}

Determine if the second segment logically continues or references content from the first segment. If it does, return a JSON object with a key "relations" containing a list of relationship triplets in the following format:
["<source_chunk_id>-CONTINUES-><target_chunk_id>"]
Use only the literal chunk IDs and the relationship type 'CONTINUES'.
If no continuation exists, return:
{"relations": []}
'''

POST_PROCESSING_PROMPT = '''


KURALLAR:
- Chunk'a bağlı Year entity'lerini Document nodeuna taşı

DOCUMENT NODE ID: {document_id}

SADECE JSON döndür:
{{
  "entities": [...],
  "relationships": [...]
}}

Data:
'''

# Document-to-Document Relationship Tasks
DOCUMENT_RELATIONSHIP_TASKS = [
    "connect_documents_by_entities",
    "materialize_text_chunk_similarities", 
    "enable_hybrid_search_and_fulltext_search_in_bloom",
    "materialize_entity_similarities",
    "enable_communities"
]

# Document Analysis Queries - ENHANCED for Entity Promotion
PERSON_POLICY_COUNT_QUERY = """
// ENHANCED: Both chunk-based and document-based relationships
MATCH (person:Person)
OPTIONAL MATCH (person)-[r:HAS_POLICY]->(d:Document)
OPTIONAL MATCH (d:Document)-[:CONTAINS_ENTITY]->(person)
WITH person, collect(DISTINCT d) AS policies, collect(r) AS relationships
WHERE size(policies) > 0
RETURN 
    person.id AS person_name,
    size(policies) AS total_policies,
    reduce(total = 0, r IN relationships | total + coalesce(r.chunk_count, 1)) AS total_chunk_mentions,
    toFloat(reduce(total = 0, r IN relationships | total + coalesce(r.chunk_count, 1))) / size(policies) AS avg_mentions_per_policy,
    [d.fileName FOR d IN policies] AS policy_files,
    [r.confidence FOR r IN relationships WHERE r IS NOT NULL] AS confidence_levels
ORDER BY total_policies DESC
"""

COMPANY_ANALYSIS_QUERY = """
// ENHANCED: Both chunk-based and document-based relationships
MATCH (company:Company)
WITH company
OPTIONAL MATCH (company)<-[:HAS_ENTITY]-(c:Chunk)-[:PART_OF]->(d1:Document)
OPTIONAL MATCH (d2:Document)-[:CONTAINS_ENTITY]->(company)
WITH company, collect(DISTINCT d1) + collect(DISTINCT d2) AS all_docs
WITH company, [d IN all_docs WHERE d IS NOT NULL] AS policies
WHERE size(policies) > 0
RETURN company.id AS company_name, size(policies) AS total_policies
ORDER BY total_policies DESC
"""
