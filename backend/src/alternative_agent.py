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
- policy_type alanı: D4, D7, DASK gibi poliçe tipi kodları
- Eğer bir bilgi soruda yoksa o alanı null yap""")
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
        q = question

        # Yıl çıkarımı
        y = re.search(r"\b(19|20)\d{2}\b", q)
        if y:
            year = y.group(0)

        # Basit: 'Hanım', 'Bey' gibi ekleri kullanarak isim yakala (örn. 'Ayça hanım')
        m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü]+)\s+(hanım|bey|hanim|beyefendi)", q)
        if m:
            name = m.group(1)

        # Poliçe tipi genelde format D4, D7 veya isim (DASK gibi)
        pt = re.search(r"\b([A-ZÇĞİÖŞÜ0-9]{1,6})\b", q)
        if pt and pt.group(1).upper().startswith('D'):
            policy_type = pt.group(1).upper()

        return SimpleFilters(name=name, year=year, policy_type=policy_type)

    # -------------------- Graph interactions --------------------
    def count_documents(self, filters: SimpleFilters) -> Optional[int]:
        """Document node'larını fileName alanındaki filtrelere göre sayar. CHUNK araması yapmaz."""
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

            # Sadeleştirilmiş count sorgusu
            cypher = f"""
            MATCH (d:Document)
            {where_clause}
            RETURN count(DISTINCT d) as count
            """

            # Parametreleri hazırla - sadece mevcut filtreleri ekle
            params = {}
            if filters.name:
                params['name'] = str(filters.name)
            if filters.year:
                params['year'] = str(filters.year)
            if filters.policy_type:
                params['policy_type'] = str(filters.policy_type)

            logger.info(f"Count query params: {params}")
            logger.info(f"Count query cypher:\n{cypher}")
            
            # Execute query
            res = self.graph.query(cypher, params)
            if res and isinstance(res, list) and len(res) > 0:
                row = res[0]
                count = row.get('count') if isinstance(row, dict) else None
                try:
                    return int(count)
                except Exception:
                    return None
            return None
        except Exception as e:
            logger.error(f"Count query failed: {e}")
            return None

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
            
            filter_res = self.graph.query(filter_query, params)
            
            if not filter_res:
                logger.info("No chunks with embeddings found matching filters")
                return []
            
            total_chunks = len(filter_res)
            logger.info(f"Step 1 - Found {total_chunks} chunks with embeddings matching filters")

            # 2. AŞAMA: Manual vector similarity hesaplama (sadece filtrelenmiş chunk'lar için)
            normalized = normalize_unicode_text(user_query)
            q_emb = self.embedding_model.embed_query(normalized)

            # Her chunk için similarity hesapla
            chunk_similarities = []
            for chunk in filter_res:
                try:
                    chunk_emb = chunk['embedding']
                    # Cosine similarity hesapla
                    similarity = self._cosine_similarity(q_emb, chunk_emb)
                    
                    chunk_similarities.append({
                        'chunk_id': chunk['chunk_id'],
                        'text': chunk['text'][:800] if chunk['text'] else '',
                        'document': chunk['document_name'],
                        'page': chunk['page'],
                        'score': float(similarity)
                    })
                except Exception as e:
                    logger.warning(f"Failed to calculate similarity for chunk {chunk.get('chunk_id')}: {e}")
                    continue

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
    def format_count_prompt(self, count: Optional[int], filters: SimpleFilters, raw_question: str) -> str:
        """Belirlenmiş şablonda cevap döndürür (count-case)."""
        f_name = filters.name or "-"
        f_year = filters.year or "-"
        f_policy = filters.policy_type or "-"

        answer = (
            f"Soru: {raw_question}\n\n"
            f"Bulunan filtreler:\n- İsim: {f_name}\n- Yıl: {f_year}\n- Poliçe tipi: {f_policy}\n\n"
        )

        if count is None:
            answer += "Sorgu çalıştırıldı fakat sonucu almak mümkün olmadı veya sonuç boş. Lütfen bağlantıları kontrol edin."
        else:
            answer += f"Graph sorgusu ile bulunan toplam Document/Policy sayısı: {count}\n"

        # belirli bir prompt template ile daha zenginleştir
        answer += "\nCevabı alttaki template formatında döndürdüm.\n\n"
        answer += (
            "PROMPT_TEMPLATE:\n---\nSistem: Uzman bir veri analisti olarak cevap ver.\n"
            "Soru: {user_question}\nGraph sonucu: {count}\nFiltreler: {filters}\n---\n"
        ).format(user_question=raw_question, count=count or 0, filters={'name': f_name, 'year': f_year, 'policy_type': f_policy})

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
        
        # Uzunluğu kontrol et, çok uzunsa kırp
        if len(result) > 2000:
            result = result[:2000] + "... [metin kesildi]"
            logger.info("Text truncated to 2000 characters")
            
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
                filters = SimpleFilters(name=f.get('name'), year=f.get('year'), policy_type=f.get('policy_type'))
            else:
                filters = self.extract_filters_heuristic(question)

            count = self.count_documents(filters)
            text = self.format_count_prompt(count, filters, question)
            return {'mode': 'count', 'response_text': text, 'meta': {'count': count, 'filters': filters}}

        # 2) Diğer durumlarda LLM'e sor: vector arama yapmalı mı?
        decision = self.ask_llm_for_decision(question)
        use_vector = bool(decision.get('use_vector')) if isinstance(decision, dict) else False
        filters = SimpleFilters()
        if isinstance(decision, dict):
            f = decision.get('filters', {}) or {}
            filters = SimpleFilters(name=f.get('name'), year=f.get('year'), policy_type=f.get('policy_type'))
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
            return {'mode': 'vector', 'response_text': text, 'meta': {'chunks': chunks, 'filters': filters, 'decision_reason': decision.get('reason')}}

        # 3) Eğer LLM vector demediyse, fallback olarak graph query ile belge listesi veya sayma yap
        logger.info("LLM chose not to use vector. Falling back to graph aggregate/list query.")
        filters = filters or self.extract_filters_heuristic(question)
        count = self.count_documents(filters)
        text = self.format_count_prompt(count, filters, question)
        return {'mode': 'cypher_fallback', 'response_text': text, 'meta': {'count': count, 'filters': filters, 'decision': decision}}


def test_alternative_agent():
    # Basit test fonksiyonu (lokal Neo4j config'ine bağlıdır)
    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        username="neo4j",
        password="qwerty5555"
    )

    agent = AlternativeAgent(graph)

    qs = [
        "Ayça hanımın 2020 yılında kaç adet poliçesi var?",
        "Ayça hanım 2020 D4 poliçesi taksitleri neler?",
        "2020 yılında kaç DASK poliçesi var?"
    ]

    for q in qs:
        print('\n' + '=' * 60)
        print('SORU:', q)
        r = agent.answer_question(q)
        print('MODE:', r['mode'])
        print(r['response_text'])  # Tüm metni göster


if __name__ == '__main__':
    test_alternative_agent()
