#!/usr/bin/env python3
"""
Alternative Intelligent Agent

Kurallar:
- "Kaç adet" türü sayma soruları için yalnızca graph (Document/Policy) sorguları çalıştırır ve CHUNK içinde vector araması yapmaz.
- Kişi+yıl+poliçe gibi detaylı sorgularda (ör: "Ayça hanım 2020 D4 poliçesi taksitleri neler") LLM kararına göre chunk (vector) araması yapar.
- LLM kararına göre filtreleri çıkarır (name, year, policy_type) ve buna göre işlem yapar.

Yeni dosya olarak eklendi; mevcut `intelligent_agent.py` içindeki yardımcı fikirlerden bağımsız, bağımsız bir implementasyondur.
"""

import logging
import re
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from langchain.schema import HumanMessage, SystemMessage
from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from langchain_neo4j import Neo4jGraph
from src.utf8_utils import normalize_unicode_text
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class SimpleFilters:
    name: Optional[str] = None
    year: Optional[str] = None
    policy_type: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """JSON serialization için dict'e çevir ve unicode normalize et"""
        from src.utf8_utils import normalize_unicode_text
        
        return {
            'name': normalize_unicode_text(self.name) if self.name else None,
            'year': normalize_unicode_text(self.year) if self.year else None,
            'policy_type': normalize_unicode_text(self.policy_type) if self.policy_type else None
        }


class AlternativeAgent:
    """Kural-tabanlı alternatif agent.

    - count-case: graph-only sayma sorgusu çalıştırır ve sonuçları belirli bir prompt şablonunda döndürür.
    - diğer sorularda: önce LLM'e sor ve LLM'in JSON çıktısına göre vector search yapmaya karar ver.
    """

    def __init__(self, graph: Neo4jGraph, model_name: str = "openai_gpt_4o"):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        # embedding model sadece vector arama yapılacaksa kullanılır
        self.embedding_model, _ = load_embedding_model("openai")

    # -------------------- Heuristics --------------------
    def is_count_query(self, question: str) -> bool:
        """Basit kurallar ile "kaç adet / kaç tane" gibi sayma sorgularını tespit et.

        Eğer soru 'kaç' içeriyor ve içinde bir yıl (4 haneli) veya 'yıl'/'yılında' gibi kelimeler varsa
        büyük olasılıkla sayma sorgusudur ve CHUNK vector araması yapılmamalıdır.
        """
        q = question.lower()
        if not re.search(r"\bkaç\b|\bkaç adet\b|\bkaç tane\b", q):
            return False

        # yıl var mı? 4 basamaklı yıl veya 'yıl' kelimesi
        if re.search(r"\b(19|20)\d{2}\b", q) or "yıl" in q or "yılında" in q:
            return True

        # ayrıca poliçe/adet gibi terimler varsa sayma ihtimali yüksek
        if "poliçe" in q or "adet" in q or "tane" in q:
            return True

        return False

    # -------------------- LLM yardımcıları --------------------
    def ask_llm_for_decision(self, question: str) -> Dict[str, Any]:
        """LLM'den JSON formatında karar alır: use_vector (bool), reason, filters.

        Beklenen JSON örneği:
        {"use_vector": true, "reason": "user asks for payment details inside document text", "filters": {"name":"Ayça", "year":"2020", "policy_type":"D4"}}
        """
        system = SystemMessage(content="""Sen bir analizci/karar vericisin. Gelen soruyu değerlendir ve yalnızca JSON olarak cevapla: 
{"use_vector": true/false, "reason": "...", "filters": {"name":..., "year":..., "policy_type":...}}.

ÖNEMLİ KURALLAR:
- name alanı: Sadece kişinin adını yaz, "hanım", "bey" gibi unvanları dahil etme
- year alanı: 4 haneli yıl numarası (string olarak)
- policy_type alanı: Poliçe tipi kodları ve poliçe türleri:
  * Kodlar: D4, D7, DASK gibi
  * Türler: konut, trafik, kasko, dask, yangın gibi poliçe türleri
- Eğer bir bilgi soruda yoksa o alanı null yap

POLIÇE TİPİ ÖRNEKLERİ:
- "konut poliçesi" → "konut"
- "trafik poliçesi" → "trafik" 
- "kasko poliçesi" → "kasko"
- "DASK poliçesi" → "DASK"
- "D4 konut" → "D4"
- "yangın sigortası" → "yangın"

VECTOR ARAMA KARAR KRİTERLERİ:
- "Kaç adet", "kaç tane", "kaç poliçe" gibi SAYMA soruları → use_vector: false
- Diğer tüm sorular (detay bilgi, prim tutarı, taksit, sözleşme metni, vb.) → use_vector: true

ÖRNEKLER:
- "Ahmet Bey'in kiraz teknesi için sigorta poliçesi primi ne kadar?" → use_vector: true (prim detayı istiyor)
- "2020 yılında kaç adet konut poliçesi var?" → use_vector: false (sayma sorusu)
- "Ayça hanım 2020 D4 poliçesi taksitleri neler?" → use_vector: true (taksit detayı istiyor)
- "Mehmet'in araç sigortası prim ödemesi ne zaman?" → use_vector: true (ödeme detayı istiyor)
""")
        human = HumanMessage(content=(
            "Soru: " + question + "\n\n" +
            "Karar ver: Bu soru sayma sorusu mu (kaç adet/tane) yoksa detay bilgi gerektiren bir soru mu?"
        ))

        try:
            response = self.llm.invoke([system, human])
            text = response.content.strip()
            # Try to extract JSON from the response
            m = re.search(r"(\{.*\})", text, flags=re.S)
            if m:
                jtext = m.group(1)
                try:
                    data = json.loads(jtext)
                    return data
                except Exception:
                    logger.warning("LLM'den dönen JSON parse edilemedi, raw text kullanılıyor")
                    return {"use_vector": False, "reason": text, "filters": {}}
            else:
                logger.warning("LLM JSON içermeyen cevap verdi; fallback karar: use_vector=False")
                return {"use_vector": False, "reason": text, "filters": {}}
        except Exception as e:
            logger.error(f"LLM karar hatası: {e}")
            return {"use_vector": False, "reason": str(e), "filters": {}}

    def extract_filters_heuristic(self, question: str) -> SimpleFilters:
        """Basit regex'lerle name, year, policy_type çıkarımı (LLM başarısız olursa kullanılabilir)."""
        name = None
        year = None
        policy_type = None
        q = question.lower()

        # Yıl çıkarımı
        y = re.search(r"\b(19|20)\d{2}\b", q)
        if y:
            year = y.group(0)

        # Basit: 'Hanım', 'Bey' gibi ekleri kullanarak isim yakala (örn. 'Ayça hanım')
        m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü]+)\s+(hanım|bey|hanim|beyefendi)", question)
        if m:
            name = normalize_unicode_text(m.group(1))

        # Poliçe tipi çıkarımı - genişletilmiş
        # 1) Kod formatları: D4, D7, DASK gibi
        pt_code = re.search(r"\b([A-ZÇĞİÖŞÜ0-9]{1,6})\b", question)
        if pt_code and (pt_code.group(1).upper().startswith('D') or pt_code.group(1).upper() == 'DASK'):
            policy_type = pt_code.group(1).upper()
        
        # 2) Poliçe türleri: konut, trafik, kasko, yangın, dask vb.
        policy_types = {
            'konut': 'konut',
            'trafik': 'trafik', 
            'kasko': 'kasko',
            'dask': 'DASK',
            'yangın': 'yangın',
            'yangin': 'yangın',
            'depo': 'depo',
            'koleksiyon': 'koleksiyon'
        }
        
        for keyword, ptype in policy_types.items():
            if keyword in q:
                policy_type = ptype
                break

        return SimpleFilters(name=name, year=year, policy_type=policy_type)

    # -------------------- Graph interactions --------------------
    def count_and_list_documents(self, filters: SimpleFilters) -> Dict[str, Any]:
        """Document node'larını fileName alanındaki filtrelere göre sayar ve listeler. CHUNK araması yapmaz."""
        try:
            # WHERE koşullarını filtrelere göre dinamik olarak oluştur
            where_conditions = []
            
            if filters.name:
                where_conditions.append("apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($name)")
            
            if filters.year:
                where_conditions.append("apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($year)")
                
            if filters.policy_type:
                where_conditions.append("apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($policy_type)")

            # WHERE clause'u oluştur
            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)

            # Count ve dosya listesi sorgusu
            cypher = f"""
            MATCH (d:Document)
            {where_clause}
            RETURN count(DISTINCT d) as count, collect(DISTINCT d.fileName) as documents
            """

            # Parametreleri hazırla - sadece mevcut filtreleri ekle
            params = {}
            if filters.name:
                params['name'] = str(filters.name)
            if filters.year:
                params['year'] = str(filters.year)
            if filters.policy_type:
                params['policy_type'] = str(filters.policy_type)

            logger.info(f"Count and list query params: {params}")
            logger.info(f"Count and list query cypher:\n{cypher}")
            
            # Execute query
            res = self.graph.query(cypher, params)
            if res and isinstance(res, list) and len(res) > 0:
                row = res[0]
                count = row.get('count') if isinstance(row, dict) else 0
                documents = row.get('documents', []) if isinstance(row, dict) else []
                
                try:
                    count_int = int(count) if count is not None else 0
                    doc_list = [doc for doc in documents if doc] if documents else []
                    
                    return {
                        'count': count_int,
                        'documents': doc_list
                    }
                except Exception:
                    return {'count': 0, 'documents': []}
            return {'count': 0, 'documents': []}
            
        except Exception as e:
            logger.error(f"Count and list query failed: {e}")
            return {'count': 0, 'documents': []}

    # -------------------- Vector search (chunk) --------------------
    def vector_search_chunks(self, user_query: str, filters: SimpleFilters, limit: int = 10) -> List[Dict[str, Any]]:
        """İki aşamalı arama: 1) Önce filtrelere uyan chunk'ları bul, 2) Sonra SADECE bunlar içinde vector similarity yap."""
        try:
            # 1. AŞAMA: Önce filtrelere uyan chunk'ları ve embedding'lerini bul
            where_conditions = []
            
            # Filtreler varsa fileName'de arama yap
            if filters.name:
                where_conditions.append(f"apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($name)")
            
            if filters.year:
                where_conditions.append(f"apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($year)")
                
            if filters.policy_type:
                where_conditions.append(f"apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($policy_type)")

            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)
            
            # Filtrelenmiş chunk'ları embedding'leri ile birlikte al
            embedding_condition = "node.embedding IS NOT NULL AND size(node.embedding) > 0"
            
            if where_clause:
                full_where = f"{where_clause} AND {embedding_condition}"
            else:
                full_where = f"WHERE {embedding_condition}"
                
            filter_query = f"""
            MATCH (node:Chunk)-[:PART_OF]->(d:Document)
            {full_where}
            RETURN node.chunkId as chunk_id,
                   node.text as text,
                   node.embedding as embedding,
                   d.fileName as document_name,
                   node.page_number as page
            """

            # Parametreleri hazırla
            params = {}
            if filters.name:
                params['name'] = str(filters.name)
            if filters.year:
                params['year'] = str(filters.year)
            if filters.policy_type:
                params['policy_type'] = str(filters.policy_type)

            logger.info(f"Step 1 - Getting filtered chunks with embeddings")
            logger.info(f"Step 1 - Filter params: {params}")
            logger.info(f"Step 1 - Neo4j query: {filter_query}")
            
            filter_res = self.graph.query(filter_query, params)
            
            if not filter_res:
                logger.info("No chunks with embeddings found matching filters")
                return []
            
            total_chunks = len(filter_res)
            logger.info(f"Step 1 - Found {total_chunks} chunks with embeddings matching filters")
            
            # İlk birkaç chunk'ın detaylarını logla
            for i, chunk in enumerate(filter_res[:3]):
                chunk_id = chunk.get('chunk_id', 'unknown')
                doc_name = chunk.get('document_name', 'unknown')
                has_embedding = chunk.get('embedding') is not None
                emb_length = len(chunk.get('embedding', [])) if chunk.get('embedding') else 0
                emb_type = type(chunk.get('embedding', None)).__name__
                logger.info(f"Step 1 - Sample chunk {i+1}: ID={chunk_id}, Doc={doc_name}, HasEmb={has_embedding}, EmbLen={emb_length}, Type={emb_type}")
                print(f"Step 1 - Sample chunk {i+1}: ID={chunk_id}, Doc={doc_name}, HasEmb={has_embedding}, EmbLen={emb_length}, Type={emb_type}")
                
            # Eğer tüm chunk'lar embedding'siz geliyorsa, database'de gerçekten embedding var mı kontrol et
            if filter_res and not any(chunk.get('embedding') for chunk in filter_res):
                logger.warning("All chunks have no embeddings despite Neo4j filter. Checking database status...")
                print("WARNING: All chunks have no embeddings despite Neo4j filter!")
                
                # Database'de gerçekten embedding'li chunk var mı kontrol et
                emb_check_query = """
                MATCH (c:Chunk) 
                WHERE c.embedding IS NOT NULL AND size(c.embedding) > 0
                RETURN count(c) as emb_count
                """
                emb_check_result = self.graph.query(emb_check_query)
                emb_count = emb_check_result[0]['emb_count'] if emb_check_result else 0
                logger.info(f"Database has {emb_count} chunks with valid embeddings")
                print(f"Database has {emb_count} chunks with valid embeddings")
                
                # FALLBACK: Embedding olmayan chunk'ları da dahil et
                logger.info("Attempting fallback: getting chunks without embedding filter")
                print("FALLBACK: Getting chunks without embedding requirement...")
                
                fallback_query = f"""
                MATCH (node:Chunk)-[:PART_OF]->(d:Document)
                {where_clause}
                AND node.text IS NOT NULL
                RETURN node.chunkId as chunk_id,
                       node.text as text,
                       d.fileName as document_name,
                       node.page_number as page
                LIMIT 20
                """
                
                fallback_res = self.graph.query(fallback_query, params)
                if fallback_res:
                    logger.info(f"Fallback found {len(fallback_res)} chunks without embedding filter")
                    print(f"Fallback found {len(fallback_res)} chunks without embedding filter")
                    
                    # Text-based relevance scoring (basit keyword matching)
                    normalized_query = normalize_unicode_text(user_query).lower()
                    query_keywords = set(normalized_query.split())
                    
                    chunk_scores = []
                    for chunk in fallback_res:
                        text = chunk.get('text', '').lower()
                        text_keywords = set(text.split())
                        
                        # Basit keyword overlap score
                        overlap = len(query_keywords.intersection(text_keywords))
                        score = overlap / len(query_keywords) if query_keywords else 0
                        
                        chunk_scores.append({
                            'chunk_id': chunk['chunk_id'],
                            'text': chunk['text'] if chunk['text'] else '',
                            'document': chunk['document_name'],
                            'page': chunk['page'],
                            'score': float(score)
                        })
                    
                    # Score'a göre sırala
                    chunk_scores.sort(key=lambda x: x['score'], reverse=True)
                    results = chunk_scores[:limit]
                    
                    logger.info(f"Fallback text matching returned {len(results)} chunks")
                    print(f"Fallback text matching returned {len(results)} chunks")
                    if results:
                        logger.info(f"Top fallback score: {results[0]['score']:.3f}")
                        print(f"Top fallback score: {results[0]['score']:.3f}")
                    
                    return results
                else:
                    logger.warning("Fallback also returned no results")
                    print("Fallback also returned no results")

            # 2. AŞAMA: Manual vector similarity hesaplama (sadece filtrelenmiş chunk'lar için)
            normalized = normalize_unicode_text(user_query)
            q_emb = self.embedding_model.embed_query(normalized)

            # Her chunk için similarity hesapla
            chunk_similarities = []
            chunks_with_embedding = 0
            chunks_without_embedding = 0
            
            for chunk in filter_res:
                try:
                    chunk_emb = chunk.get('embedding')
                    if chunk_emb is None or len(chunk_emb) == 0:
                        chunks_without_embedding += 1
                        logger.debug(f"Chunk {chunk.get('chunk_id')} has no embedding, skipping")
                        continue
                    
                    chunks_with_embedding += 1
                    # Cosine similarity hesapla
                    similarity = self._cosine_similarity(q_emb, chunk_emb)
                    
                    chunk_similarities.append({
                        'chunk_id': chunk['chunk_id'],
                        'text': chunk['text'] if chunk['text'] else '',  # Tam text'i al, kırpma
                        'document': chunk['document_name'],
                        'page': chunk['page'],
                        'score': float(similarity)
                    })
                except Exception as e:
                    chunks_without_embedding += 1
                    logger.warning(f"Failed to calculate similarity for chunk {chunk.get('chunk_id')}: {e}")
                    continue
            
            logger.info(f"Step 2 - Processed {chunks_with_embedding} chunks with embeddings, {chunks_without_embedding} chunks without embeddings")

            # Similarity'e göre sırala ve limit uygula
            chunk_similarities.sort(key=lambda x: x['score'], reverse=True)
            results = chunk_similarities[:limit]
            
            logger.info(f"Step 2 - Manual similarity calculation completed, returning top {len(results)} chunks")
            if results:
                logger.info(f"Step 2 - Top score: {results[0]['score']:.3f}, Bottom score: {results[-1]['score']:.3f}")

            return results
        except Exception as e:
            logger.error(f"Filtered vector search failed: {e}")
            return []

    def _cosine_similarity(self, vec1, vec2):
        """İki vector arasındaki cosine similarity hesapla."""
        import numpy as np
        
        # Vector'leri numpy array'e çevir
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        
        # Cosine similarity hesapla
        dot_product = np.dot(v1, v2)
        norm_v1 = np.linalg.norm(v1)
        norm_v2 = np.linalg.norm(v2)
        
        if norm_v1 == 0 or norm_v2 == 0:
            return 0.0
            
        return dot_product / (norm_v1 * norm_v2)

    # -------------------- Response formatting --------------------
    def format_count_prompt(self, count_result: Dict[str, Any], filters: SimpleFilters, raw_question: str) -> str:
        """Count sorgusu için temiz ve doğrudan cevap formatı"""
        f_name = filters.name or "belirtilmemiş"
        f_year = filters.year or "belirtilmemiş"
        f_policy = filters.policy_type or "belirtilmemiş"
        
        count = count_result.get('count', 0) if isinstance(count_result, dict) else (count_result if isinstance(count_result, int) else 0)
        documents = count_result.get('documents', []) if isinstance(count_result, dict) else []

        # Temiz ve doğrudan cevap formatı
        answer = f"**Sorgu Sonucu:**\n\n"
        answer += f"**Arama Kriterleri:**\n"
        answer += f"• Kişi: {f_name}\n"
        answer += f"• Yıl: {f_year}\n"
        answer += f"• Poliçe Tipi: {f_policy}\n\n"

        if count == 0:
            answer += "❌ Bu kriterlere uygun hiçbir poliçe bulunamadı.\n"
        else:
            answer += f"✅ **Toplam {count} adet poliçe bulundu.**\n\n"
            
            # TÜM dosyaları listele (limit yok)
            if documents:
                answer += "**Bulunan Poliçeler:**\n"
                for i, doc in enumerate(documents, 1):
                    # Dosya adını daha temiz hale getir
                    clean_doc_name = doc.replace('.pdf', '').replace('Ayça Dinçkök ', '')
                    answer += f"{i}. {clean_doc_name}\n"

        return answer

    def format_chunk_response(self, chunks: List[Dict[str, Any]], filters: SimpleFilters, raw_question: str, mode: str = "vector") -> str:
        """Chunk temelli arama sonrası sayfa bazlı tam veri raporu döndürür."""
        
        # RETRIEVER VERİSİNİ DETAYLI LOGLA
        logger.info("="*60)
        logger.info("🔍 RETRIEVER VERİSİ DETAYLI ANALIZ")
        logger.info("="*60)
        logger.info(f"📝 Soru: {raw_question}")
        logger.info(f"🔧 Mode: {mode}")
        logger.info(f"📊 Toplam chunk sayısı: {len(chunks)}")
        logger.info(f"🎯 Filtreler: {filters.to_dict() if hasattr(filters, 'to_dict') else str(filters)}")
        
        # Her chunk'ın detaylarını logla
        for i, chunk in enumerate(chunks[:10], 1):  # İlk 10 chunk'ı detaylı logla
            logger.info(f"\n📋 CHUNK {i}:")
            logger.info(f"  ├─ ID: {chunk.get('chunk_id', 'unknown')}")
            logger.info(f"  ├─ Score: {chunk.get('score', 0):.3f}")
            logger.info(f"  ├─ Document: {chunk.get('document', 'unknown')}")
            logger.info(f"  ├─ Page: {chunk.get('page', '?')}")
            if 'customer_name' in chunk:
                logger.info(f"  ├─ Customer: {chunk.get('customer_name', 'N/A')}")
            if 'policy_type' in chunk:
                logger.info(f"  ├─ Policy Type: {chunk.get('policy_type', 'N/A')}")
            if 'policy_year' in chunk:
                logger.info(f"  ├─ Policy Year: {chunk.get('policy_year', 'N/A')}")
            if 'filter_boost' in chunk:
                logger.info(f"  ├─ Filter Boost: {chunk.get('filter_boost', 1.0):.1f}")
            
            text_preview = chunk.get('text', '')[:200] + "..." if len(chunk.get('text', '')) > 200 else chunk.get('text', '')
            logger.info(f"  └─ Text: {text_preview}")
        
        if len(chunks) > 10:
            logger.info(f"\n... ve {len(chunks) - 10} chunk daha var")
        
        logger.info("="*60)
        
        if mode == "intelligent_graph":
            header = f"Soru: {raw_question}\nAkıllı Graf Arama Kullanıldı\n\n"
            if not chunks:
                return header + "Akıllı graf araması yapıldı ama ilgili chunk bulunamadı."
            
            # Intelligent search için daha detaylı bilgi ver
            body = f"Bulunan {len(chunks)} chunk (akıllı graf araması):\n\n"
            
            for i, chunk in enumerate(chunks[:5], 1):
                score = chunk.get('score', 0)
                customer = chunk.get('customer_name', 'Bilinmiyor')
                policy_type = chunk.get('policy_type', 'Bilinmiyor')
                policy_year = chunk.get('policy_year', 'Bilinmiyor')
                filter_boost = chunk.get('filter_boost', 1.0)
                
                body += f"📋 **Chunk {i}** (Score: {score:.3f}, Boost: {filter_boost:.1f})\n"
                body += f"👤 Müşteri: {customer} | 📅 Yıl: {policy_year} | 📋 Tip: {policy_type}\n"
                body += f"📄 {chunk.get('document', 'unknown')} - Sayfa {chunk.get('page', '?')}\n"
                body += f"📝 {chunk.get('text', '')[:300]}...\n"
                body += "-" * 80 + "\n\n"
            
            final_response = header + body
            
            # INTELLIGENT GRAPH MODE İÇİN LLM PROMPT LOGLA
            logger.info("="*60)
            logger.info("🤖 LLM'E GÖNDERİLECEK PROMPT (INTELLIGENT GRAPH)")
            logger.info("="*60)
            logger.info(f"📝 Prompt uzunluğu: {len(final_response)} karakter")
            logger.info(f"📄 Header:\n{header}")
            logger.info(f"🔍 Body özeti: {len(chunks)} chunk, {len(body)} karakter")
            logger.info(f"📋 Full Prompt Preview (ilk 1000 karakter):\n{final_response[:1000]}")
            if len(final_response) > 1000:
                logger.info(f"📋 Full Prompt Preview (son 500 karakter):\n...{final_response[-500:]}")
            logger.info("="*60)
            
            return final_response
        
        # Normal vector search formatı
        header = f"Soru: {raw_question}\nFiltreler: name={filters.name or '-'}, year={filters.year or '-'}, policy_type={filters.policy_type or '-'}\n\n"
        if not chunks:
            return header + "Vector arama yapıldı ama ilgili chunk bulunamadı."

        # Önce bulunan chunk'ların sayfa numaralarını ve belgelerini topla
        relevant_pages = {}  # {document_name: {page_number: True}}
        for c in chunks[:5]:  # İlk 5 relevanta chunk'tan sayfa numaralarını al
            doc_name = c.get('document')
            page_num = c.get('page')
            if doc_name and page_num is not None:
                if doc_name not in relevant_pages:
                    relevant_pages[doc_name] = set()
                relevant_pages[doc_name].add(page_num)

        if not relevant_pages:
            return header + "Relevant sayfalar bulunamadı."

        # Her belge ve sayfa için tüm chunk'ları position sırasına göre al
        full_page_content = []
        
        for doc_name, page_numbers in relevant_pages.items():
            for page_num in sorted(page_numbers):
                page_chunks = self._get_all_chunks_for_page(doc_name, page_num, filters)
                if page_chunks:
                    full_content = self._merge_chunks_by_position(page_chunks)
                    full_page_content.append({
                        'document': doc_name,
                        'page': page_num,
                        'content': full_content
                    })

        # Sonuçları formatla
        body = f"Bulunan {len(chunks)} relevanta chunk'tan çıkarılan tam sayfa içerikleri:\n\n"
        
        for page_data in full_page_content:
            body += f"📄 **{page_data['document']}** - Sayfa {page_data['page']}:\n"
            body += f"{page_data['content']}\n"
            body += "-" * 80 + "\n\n"

        final_response = header + body
        
        # LLM'E GÖNDERİLECEK PROMPT'U LOGLA
        logger.info("="*60)
        logger.info("🤖 LLM'E GÖNDERİLECEK PROMPT")
        logger.info("="*60)
        logger.info(f"📝 Prompt uzunluğu: {len(final_response)} karakter")
        logger.info(f"📄 Header:\n{header}")
        logger.info(f"🔍 Body özeti: {len(full_page_content)} sayfa, {len(body)} karakter")
        logger.info(f"📋 Full Prompt Preview (ilk 1000 karakter):\n{final_response[:1000]}")
        if len(final_response) > 1000:
            logger.info(f"📋 Full Prompt Preview (son 500 karakter):\n...{final_response[-500:]}")
        logger.info("="*60)

        return final_response

    def _get_all_chunks_for_page(self, document_name: str, page_number: int, filters: SimpleFilters) -> List[Dict[str, Any]]:
        """Belirli bir belgenin belirli bir sayfasındaki tüm chunk'ları position sırasına göre getirir."""
        try:
            # Sadece bu belge ve sayfa için tüm chunk'ları al
            page_query = """
            MATCH (node:Chunk)-[:PART_OF]->(d:Document)
            WHERE d.fileName = $document_name 
              AND node.page_number = $page_number
              AND node.text IS NOT NULL
            RETURN node.chunkId as chunk_id,
                   node.text as text,
                   node.position as position,
                   node.page_number as page
            ORDER BY COALESCE(node.position, 0) ASC
            """
            
            params = {
                'document_name': document_name,
                'page_number': page_number
            }
            
            logger.info(f"Getting all chunks for {document_name} page {page_number}")
            res = self.graph.query(page_query, params)
            
            logger.info(f"Found {len(res) if res else 0} chunks for page {page_number}")
            if res:
                logger.info(f"First chunk preview: {res[0].get('text', '')[:100]}...")
            
            return res if res else []
            
        except Exception as e:
            logger.error(f"Failed to get page chunks: {e}")
            return []

    def _merge_chunks_by_position(self, chunks: List[Dict[str, Any]]) -> str:
        """Chunk'ları position sırasına göre birleştirip tek bir metin oluşturur."""
        if not chunks:
            logger.warning("No chunks to merge")
            return ""
        
        logger.info(f"Merging {len(chunks)} chunks by position")
        
        # Position'a göre sırala (position None ise 0 kabul et)
        sorted_chunks = sorted(chunks, key=lambda x: x.get('position') or 0)
        
        # Debug: İlk birkaç chunk'ın position'larını logla
        for i, chunk in enumerate(sorted_chunks[:3]):
            logger.info(f"Chunk {i}: position={chunk.get('position')}, text_preview={chunk.get('text', '')[:50]}...")
        
        # Metinleri birleştir
        full_text = []
        for chunk in sorted_chunks:
            text = chunk.get('text', '').strip()
            if text:
                full_text.append(text)
        
        logger.info(f"Collected {len(full_text)} non-empty text chunks")
        
        # Metinleri birleştir (chunk'lar arası boşluk bırak)
        result = ' '.join(full_text)
        
        logger.info(f"Merged text length: {len(result)}")
        
        # Metin kesme işlemi kaldırıldı - tam içerik döndürülüyor
        return result

    # -------------------- Ana akış --------------------
    def extract_filters_from_question(self, question: str) -> Dict[str, Any]:
        """LLM ile sorudan customer, year, policy_type, document_type, insured_item, document_name filtrelerini çıkar"""
        system = SystemMessage(content="""Sen bir filtre çıkarma uzmanısın. Gelen soruyu analiz et ve şu filtreleri çıkar:
- customer_filter: Müşteri/kişi adı (örn: "Ayça", "Mehmet", "ASUDE SİTESİ YÖNETİMİ")
- year_filter: Yıl bilgisi (örn: "2020", "2021", "2023")  
- policy_type_filter: Poliçe tipi (örn: "Yangın Sigortası", "DASK", "Konut", "Trafik")
- document_type_filter: Doküman tipi (örn: "MAIN_POLICY", "ENDORSEMENT", "RENEWAL", "CANCELLATION")
- insured_item_filter: Sigortalı eşya/varlık (örn: "kiraz teknesi", "ev", "araç", "bina")
- document_name_filter: Belge adı/dosya adı (örn: "Ayça Dinçkök Galata Residance")

SADECE JSON döndür:
{"customer_filter": "...", "year_filter": "...", "policy_type_filter": "...", "document_type_filter": "...", "insured_item_filter": "...", "document_name_filter": "..."}

Eğer bir filtre bulunamazsa null yap.

DOCUMENT TİPLERİ:
- "zeyilname", "endorsement" → "ENDORSEMENT" (Ana poliçenin zeyilnameleri)
- "ana poliçe", "main policy" → "MAIN_POLICY" 
- "yenileme", "renewal" → "RENEWAL"
- "iptal", "cancellation" → "CANCELLATION"

ÖRNEKLER:
"Ayça hanımın 2020 yılındaki dask poliçe zeyilnamelerini listele" 
→ {"customer_filter": "Ayça", "year_filter": "2020", "policy_type_filter": "dask", "document_type_filter": "ENDORSEMENT", "insured_item_filter": null, "document_name_filter": null}

"2020 yılında kaç adet konut ana poliçesi var?"
→ {"customer_filter": null, "year_filter": "2020", "policy_type_filter": "konut", "document_type_filter": "MAIN_POLICY", "insured_item_filter": null, "document_name_filter": null}

"Mehmet'in kiraz teknesi için sigorta belgeleri"
→ {"customer_filter": "Mehmet", "year_filter": null, "policy_type_filter": null, "document_type_filter": null, "insured_item_filter": "kiraz teknesi", "document_name_filter": null}

"Ayça Dinçkök Galata Residance belgesindeki bilgiler"
→ {"customer_filter": null, "year_filter": null, "policy_type_filter": null, "document_type_filter": null, "insured_item_filter": null, "document_name_filter": "Ayça Dinçkök Galata Residance"}
""")
        
        human = HumanMessage(content=f"Soru: {question}")
        
        try:
            response = self.llm.invoke([system, human])
            text = response.content.strip()
            # JSON çıkarımı
            m = re.search(r"(\{.*\})", text, flags=re.S)
            if m:
                jtext = m.group(1)
                try:
                    data = json.loads(jtext)
                    # Unicode normalize et
                    result = {}
                    for key, value in data.items():
                        if value and isinstance(value, str):
                            result[key] = normalize_unicode_text(value)
                        else:
                            result[key] = value
                    return result
                except Exception:
                    logger.warning("LLM'den dönen JSON parse edilemedi")
                    return {
                        "customer_filter": None, "year_filter": None, "policy_type_filter": None,
                        "document_type_filter": None, "insured_item_filter": None, "document_name_filter": None
                    }
            else:
                logger.warning("LLM JSON içermeyen cevap verdi")
                return {
                    "customer_filter": None, "year_filter": None, "policy_type_filter": None,
                    "document_type_filter": None, "insured_item_filter": None, "document_name_filter": None
                }
        except Exception as e:
            logger.error(f"Filter extraction hatası: {e}")
            return {
                "customer_filter": None, "year_filter": None, "policy_type_filter": None,
                "document_type_filter": None, "insured_item_filter": None, "document_name_filter": None
            }

    def intelligent_graph_search(self, question: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Yeni intelligent graph search query'sini kullan"""
        try:
            # Question embedding oluştur
            normalized_query = normalize_unicode_text(question)
            question_embedding = self.embedding_model.embed_query(normalized_query)
            
            # Cypher query parametreleri
            params = {
                'question': question,
                'question_embedding': question_embedding,
                'customer_filter': filters.get('customer_filter'),
                'year_filter': filters.get('year_filter'),
                'policy_type_filter': filters.get('policy_type_filter'),
                'document_type_filter': filters.get('document_type_filter'),
                'insured_item_filter': filters.get('insured_item_filter'),
                'document_name_filter': filters.get('document_name_filter')
            }
            
            logger.info(f"Intelligent graph search params: {params}")
            
            # INTELLIGENT_GRAPH_SEARCH_QUERY'yi burada import et
            from src.shared.constants import INTELLIGENT_GRAPH_SEARCH_QUERY
            
            result = self.graph.query(INTELLIGENT_GRAPH_SEARCH_QUERY, params)
            
            # INTELLIGENT GRAPH SEARCH SONUÇLARINI DETAYLI LOGLA
            logger.info("="*60)
            logger.info("🎯 INTELLIGENT GRAPH SEARCH SONUÇLARI")
            logger.info("="*60)
            logger.info(f"📊 Neo4j sonuç sayısı: {len(result) if result else 0}")
            
            if not result:
                logger.info("❌ Intelligent graph search: no results")
                logger.info("="*60)
                return []
            
            # İlk birkaç raw sonucu logla
            for i, row in enumerate(result[:3], 1):
                logger.info(f"\n🔍 RAW NEO4J SONUÇ {i}:")
                logger.info(f"  ├─ ID: {row.get('id')}")
                logger.info(f"  ├─ Score: {row.get('score')}")
                logger.info(f"  ├─ Text preview: {str(row.get('text', ''))[:100]}...")
                logger.info(f"  └─ Metadata: {row.get('metadata')}")
            
            # Sonuçları format et
            chunks = []
            for row in result:
                chunk_data = {
                    'chunk_id': row.get('id'),
                    'text': row.get('text', ''),
                    'score': float(row.get('score', 0)),
                    'document': row.get('metadata', {}).get('document', ''),
                    'page': row.get('metadata', {}).get('page_number'),
                    'customer_name': row.get('metadata', {}).get('customer_name'),
                    'policy_type': row.get('metadata', {}).get('policy_type'),
                    'policy_year': row.get('metadata', {}).get('policy_year'),
                    'filter_boost': row.get('metadata', {}).get('filter_boost', 1.0)
                }
                chunks.append(chunk_data)
            
            logger.info(f"\n✅ {len(chunks)} chunk formatlandı")
            if chunks:
                logger.info(f"🏆 En yüksek score: {chunks[0]['score']:.3f}")
                logger.info(f"📉 En düşük score: {chunks[-1]['score']:.3f}")
            logger.info("="*60)
            
            logger.info(f"Intelligent graph search returned {len(chunks)} chunks")
            if chunks:
                logger.info(f"Top score: {chunks[0]['score']:.3f}")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Intelligent graph search failed: {e}")
            return []

    def answer_question(self, question: str) -> Dict[str, Any]:
        """Kullanıcı sorusunu alır, kurallara göre işlem yapar ve sonuç döner.

        Yeni basit kural: Count sorusu değilse vector araması yap, fallback yok.
        
        Dönen sözlükte en azından: { 'mode': 'count'|'vector', 'response_text': str, 'meta': {...} }
        """
        question = question.strip()
        logger.info(f"Answering question: {question}")

        # 1) Eğer kesin bir count query ise graph-only yolunu zorla
        if self.is_count_query(question):
            logger.info("Detected count query - using graph-only counting (no chunk vector search)")
            # İlk önce LLM ile filtre çıkarmaya çalış
            llm_decision = self.ask_llm_for_decision(question)
            filters = SimpleFilters()
            if isinstance(llm_decision, dict):
                f = llm_decision.get('filters', {}) or {}
                name = normalize_unicode_text(f.get('name')) if f.get('name') else None
                year = f.get('year')
                policy_type = f.get('policy_type')
                filters = SimpleFilters(name=name, year=year, policy_type=policy_type)
            else:
                filters = self.extract_filters_heuristic(question)

            count_result = self.count_and_list_documents(filters)
            text = self.format_count_prompt(count_result, filters, question)
            return {
                'mode': 'count', 
                'response_text': text, 
                'meta': {
                    'count': count_result.get('count', 0),
                    'documents': count_result.get('documents', []),
                    'filters': filters.to_dict()
                }
            }

        # 2) Intelligent graph search dene
        logger.info("Trying intelligent graph search first")
        filters_dict = self.extract_filters_from_question(question)
        
        # Eğer en az bir filtre varsa intelligent search kullan
        if any(filters_dict.values()):
            logger.info(f"Using intelligent graph search with filters: {filters_dict}")
            chunks = self.intelligent_graph_search(question, filters_dict)
            
            if chunks and len(chunks) >= 3:  # Yeterli sonuç varsa intelligent search kullan
                text = self.format_chunk_response(chunks, SimpleFilters(), question, mode="intelligent_graph")
                return {
                    'mode': 'intelligent_graph',
                    'response_text': text,
                    'meta': {
                        'chunks': chunks,
                        'filters': filters_dict,
                        'decision_reason': 'Used intelligent graph search based on detected filters'
                    }
                }
        
        # 3) Fallback: Normal vector search
        logger.info("Fallback to normal vector search")
        
        # LLM'den filtreleri al
        decision = self.ask_llm_for_decision(question)
        filters = SimpleFilters()
        if isinstance(decision, dict):
            f = decision.get('filters', {}) or {}
            name = normalize_unicode_text(f.get('name')) if f.get('name') else None
            year = f.get('year')
            policy_type = f.get('policy_type')
            filters = SimpleFilters(name=name, year=year, policy_type=policy_type)
        else:
            filters = self.extract_filters_heuristic(question)

        logger.info(f"Extracted filters for vector search: {filters}")

        # Vector araması yap
        query_text = question
        if filters.name:
            query_text += f" owner:{filters.name}"
        if filters.year:
            query_text += f" year:{filters.year}"
        if filters.policy_type:
            query_text += f" policy:{filters.policy_type}"

        chunks = self.vector_search_chunks(query_text, filters, limit=10)
        text = self.format_chunk_response(chunks, filters, question)
        
        return {
            'mode': 'vector', 
            'response_text': text, 
            'meta': {
                'chunks': chunks, 
                'filters': filters.to_dict(), 
                'decision_reason': decision.get('reason', 'Not a count query, using vector search')
            }
        }


def test_alternative_agent():
    # Basit test fonksiyonu (lokal Neo4j config'ine bağlıdır)
    # Endpoint ile aynı parametreleri kullan (sanitize=False artık)
    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        username="neo4j",
        password="qwerty5555",
        database="neo4j",
        sanitize=False,
        refresh_schema=False
    )

    agent = AlternativeAgent(graph)

    qs = [
        "ASUDE SİTESİ YÖNETİMİ 2020 yangın sigortası detayları neler?",  # intelligent graph test
        "2020 yılında kaç adet poliçe var?",  # count test
        "sigorta prim tutarı ne kadar?"  # normal vector test
    ]

    for q in qs:
        print('\n' + '=' * 60)
        print('SORU:', q)
        r = agent.answer_question(q)
        print('MODE:', r['mode'])
        print('META:', r.get('meta', {}))
        print(r['response_text'][:500])  # İlk 500 karakter


if __name__ == '__main__':
    test_alternative_agent()
