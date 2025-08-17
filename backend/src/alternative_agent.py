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
""")
        human = HumanMessage(content=(
            "Soru: " + question + "\n\n" +
            "Karar verme kriterleri: eğer soru belge içi metin detayları (taksit detayı, sözleşme metni, ifadeler) istiyorsa use_vector:true;" 
            "eğer sadece sayısal özet ya da 'kaç adet' gibi aggregate bilgi isteniyorsa use_vector:false."
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
            filter_query = f"""
            MATCH (node:Chunk)-[:PART_OF]->(d:Document)
            {where_clause}
            AND node.embedding IS NOT NULL 
            AND size(node.embedding) > 0
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

    def format_chunk_response(self, chunks: List[Dict[str, Any]], filters: SimpleFilters, raw_question: str) -> str:
        """Chunk temelli arama sonrası sayfa bazlı tam veri raporu döndürür."""
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

        return header + body

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
    def answer_question(self, question: str) -> Dict[str, Any]:
        """Kullanıcı sorusunu alır, kurallara göre işlem yapar ve sonuç döner.

        Dönen sözlükte en azından: { 'mode': 'count'|'vector'|'cypher_fallback', 'response_text': str, 'meta': {...} }
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

        # 2) Diğer durumlarda LLM'e sor: vector arama yapmalı mı?
        decision = self.ask_llm_for_decision(question)
        use_vector = bool(decision.get('use_vector')) if isinstance(decision, dict) else False
        filters = SimpleFilters()
        if isinstance(decision, dict):
            f = decision.get('filters', {}) or {}
            name = normalize_unicode_text(f.get('name')) if f.get('name') else None
            year = f.get('year')
            policy_type = f.get('policy_type')
            filters = SimpleFilters(name=name, year=year, policy_type=policy_type)
        else:
            filters = self.extract_filters_heuristic(question)

        logger.info(f"LLM decision: use_vector={use_vector}, filters={filters}")

        if use_vector:
            # LLM kararına göre chunk vector araması yap
            # Tercihen kullanıcı sorusunu ve filtreleri birleştir
            query_text = question
            if filters.name:
                query_text += f" owner:{filters.name}"
            if filters.year:
                query_text += f" year:{filters.year}"
            if filters.policy_type:
                query_text += f" policy:{filters.policy_type}"

            chunks = self.vector_search_chunks(query_text, filters, limit=10)
            text = self.format_chunk_response(chunks, filters, question)
            return {'mode': 'vector', 'response_text': text, 'meta': {'chunks': chunks, 'filters': filters.to_dict(), 'decision_reason': decision.get('reason')}}

        # 3) Eğer LLM vector demediyse, fallback olarak graph query ile belge listesi veya sayma yap
        logger.info("LLM chose not to use vector. Falling back to graph aggregate/list query.")
        filters = filters or self.extract_filters_heuristic(question)
        count_result = self.count_and_list_documents(filters)
        text = self.format_count_prompt(count_result, filters, question)
        return {
            'mode': 'cypher_fallback', 
            'response_text': text, 
            'meta': {
                'count': count_result.get('count', 0),
                'documents': count_result.get('documents', []),
                'filters': filters.to_dict(), 
                'decision': decision
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
        # "Ayça hanımın 2020 yılında kaç adet poliçesi var?",
        "Ayça hanım 2020 D4 poliçesi taksitleri neler?",
        # "2020 yılında kaç DASK poliçesi var?"
    ]

    for q in qs:
        print('\n' + '=' * 60)
        print('SORU:', q)
        r = agent.answer_question(q)
        print('MODE:', r['mode'])
        print(r['response_text'])  # Tüm metni göster


if __name__ == '__main__':
    test_alternative_agent()
