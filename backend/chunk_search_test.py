"""
Chunk Search Test - Fulltext, Fuzzy ve Semantic Search Karşılaştırması

Bu script:
1. Fulltext search ile chunk bul
2. Fuzzy search ile chunk bul  
3. Semantic (embedding) search ile chunk bul
4. Bulunan chunk'ların önceki/sonraki chunk'larını birleştir (smart window)
5. Sonuçları karşılaştır
"""

import os
import re
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

# ============================================
# CONFIG
# ============================================

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

WINDOW_SIZE = 2  # Her yönde kaç chunk ekle

TEST_QUERIES = [
    {
        "id": "prim_taksit",
        "question": "Toplam prim ve taksit tutarları nedir?",
        "keywords": ["prim", "taksit", "tutar", "ödeme", "brüt prim", "net prim", "toplam prim"],
        "keywords_fuzzy": ["prım", "taksıt", "tutar", "odeme"],  # typo variants
        "expected_content": ["taksit", "prim", "TL"]
    },
    {
        "id": "sigortalı_bilgi", 
        "question": "Sigortalının adı ve adresi nedir?",
        "keywords": ["sigortalı", "adres", "unvan", "ad soyad", "müşteri", "poliçe sahibi"],
        "keywords_fuzzy": ["sigortali", "adress", "unvanı"],
        "expected_content": ["sigortalı", "adres"]
    },
    {
        "id": "teminat",
        "question": "Teminat bedelleri ne kadar?",
        "keywords": ["teminat", "bedel", "sigorta bedeli", "teminat tutarı", "limit"],
        "keywords_fuzzy": ["temınat", "bedeli", "limitler"],
        "expected_content": ["teminat", "bedel", "TL"]
    },
    {
        "id": "muafiyet",
        "question": "Muafiyet oranları nelerdir?",
        "keywords": ["muafiyet", "muaf", "tenzili", "istisna", "kapsam dışı"],
        "keywords_fuzzy": ["muafıyet", "tenzılı", "ıstısna"],
        "expected_content": ["%", "muafiyet"]
    },
    {
        "id": "poliçe_tarih",
        "question": "Poliçe başlangıç ve bitiş tarihleri nedir?",
        "keywords": ["başlangıç tarihi", "bitiş tarihi", "tanzim tarihi", "süre", "vade"],
        "keywords_fuzzy": ["baslangıc", "bitis", "tanzım"],
        "expected_content": ["tarih", "2024", "2025"]
    },
    {
        "id": "acente",
        "question": "Acente bilgileri nedir?",
        "keywords": ["acente", "acentelik", "levha", "acente kodu"],
        "keywords_fuzzy": ["acenta", "acentalık"],
        "expected_content": ["acente", "kod"]
    },
    {
        "id": "kira_kaybi",
        "question": "Kira kaybı teminatı var mı?",
        "keywords": ["kira kaybı", "kira bedeli", "kira teminatı", "kira"],
        "keywords_fuzzy": ["kıra kaybı", "kira kaybı klozu"],
        "expected_content": ["kira", "kayıp", "teminat"]
    }
]

# ============================================
# NEO4J CONNECTION
# ============================================

def get_driver():
    return GraphDatabase.driver(
        NEO4J_URI, 
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        database=NEO4J_DATABASE
    )


# ============================================
# SEARCH METHODS
# ============================================

def fulltext_search(driver, keywords: list, limit: int = 10) -> list:
    """Fulltext index ile arama"""
    # Lucene query formatı
    query_str = " OR ".join(keywords)
    
    cypher = """
    CALL db.index.fulltext.queryNodes('chunk_text_fulltext', $search_query, {limit: $result_limit})
    YIELD node, score
    MATCH (node)-[:PART_OF]->(d:Document)
    RETURN 
        node.id as chunk_id,
        node.position as position,
        node.text as text,
        d.fileName as document,
        score
    ORDER BY score DESC
    """
    
    with driver.session() as session:
        result = session.run(cypher, search_query=query_str, result_limit=limit)
        return [dict(r) for r in result]


def fuzzy_search(driver, keywords: list, limit: int = 10) -> list:
    """Fuzzy search (Lucene ~ operator)"""
    # Lucene fuzzy query
    query_str = " OR ".join([f"{kw}~" for kw in keywords])
    
    cypher = """
    CALL db.index.fulltext.queryNodes('chunk_text_fulltext', $search_query, {limit: $result_limit})
    YIELD node, score
    MATCH (node)-[:PART_OF]->(d:Document)
    RETURN 
        node.id as chunk_id,
        node.position as position,
        node.text as text,
        d.fileName as document,
        score
    ORDER BY score DESC
    """
    
    with driver.session() as session:
        result = session.run(cypher, search_query=query_str, result_limit=limit)
        return [dict(r) for r in result]


def semantic_search(driver, question: str, limit: int = 10) -> list:
    """Semantic search with embeddings"""
    # Embedding oluştur (Google veya OpenAI)
    embedding = get_embedding(question)
    
    cypher = """
    CALL db.index.vector.queryNodes('vector', $result_limit, $embedding_vector)
    YIELD node, score
    MATCH (node)-[:PART_OF]->(d:Document)
    RETURN 
        node.id as chunk_id,
        node.position as position,
        node.text as text,
        d.fileName as document,
        score
    ORDER BY score DESC
    """
    
    with driver.session() as session:
        result = session.run(cypher, embedding_vector=embedding, result_limit=limit)
        return [dict(r) for r in result]


def get_embedding(text: str) -> list:
    """Text'i embedding'e çevir"""
    # OpenAI veya Google embedding
    try:
        from openai import OpenAI
        client = OpenAI()
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding hatası: {e}")
        # Fallback: Google
        try:
            import google.generativeai as genai
            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text
            )
            return result['embedding']
        except Exception as e2:
            print(f"Google embedding hatası: {e2}")
            return [0.0] * 256  # Dummy


# ============================================
# SMART WINDOW EXPANSION
# ============================================

def merge_overlapping_positions(positions: list, window_size: int = 2) -> list:
    """Çakışan pozisyonları birleştir"""
    if not positions:
        return []
    
    # Her pozisyon için window range oluştur
    ranges = []
    for pos in sorted(set(positions)):
        start = max(1, pos - window_size)
        end = pos + window_size
        ranges.append([start, end])
    
    # Overlapping range'leri birleştir
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        if start <= merged[-1][1] + 1:  # Bitişik veya çakışan
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    
    return merged


def needs_continuation(chunk_text: str, next_chunk_text: str) -> bool:
    """Chunk'ın devam edip etmediğini tespit et"""
    if not chunk_text or not next_chunk_text:
        return False
    
    # 1. Yarım cümle: Noktalama ile bitmiyor
    last_line = chunk_text.strip().split('\n')[-1]
    if last_line and not last_line.rstrip().endswith(('.', '?', '!', ':', ';', '|')):
        return True
    
    # 2. Tablo devamı: Her iki chunk'ta | karakteri
    if '|' in chunk_text[-200:] and '|' in next_chunk_text[:200]:
        return True
    
    # 3. Liste devamı
    first_line = next_chunk_text.strip().split('\n')[0]
    if re.match(r'^[\s]*[-•*]|^\s*\d+\.', first_line):
        if re.search(r'[-•*]|\d+\.', chunk_text):
            return True
    
    return False


def get_expanded_chunks(driver, document: str, positions: list, window_size: int = 2, use_continuation: bool = False) -> list:
    """
    Pozisyonları genişlet ve chunk'ları getir
    1. Overlapping pozisyonları birleştir
    2. Her range için chunk'ları getir
    3. (Opsiyonel) Kesilen bilgi varsa genişlet
    """
    # 1. Overlapping pozisyonları birleştir
    merged_ranges = merge_overlapping_positions(positions, window_size)
    
    results = []
    
    for start_pos, end_pos in merged_ranges:
        # Chunk'ları getir
        cypher = """
        MATCH (c:Chunk)-[:PART_OF]->(d:Document)
        WHERE d.fileName = $document
        AND c.position >= $start_pos AND c.position <= $end_pos
        RETURN c.position as position, c.text as text
        ORDER BY c.position
        """
        
        with driver.session() as session:
            result = session.run(cypher, document=document, start_pos=start_pos, end_pos=end_pos)
            chunks = [dict(r) for r in result]
        
        if not chunks:
            continue
        
        actual_start = start_pos
        actual_end = end_pos
        
        # needs_continuation sadece use_continuation=True ise çalışsın
        if use_continuation:
            # 2. Kesilen bilgi kontrolü - başlangıçta
            while actual_start > 1:
                prev_chunk = get_chunk_by_position(driver, document, actual_start - 1)
                curr_first = chunks[0]['text'] if chunks else ""
                if prev_chunk and needs_continuation(prev_chunk['text'], curr_first):
                    actual_start -= 1
                    chunks.insert(0, prev_chunk)
                else:
                    break
            
            # 3. Kesilen bilgi kontrolü - sonda
            max_pos = get_max_position(driver, document)
            while actual_end < max_pos:
                next_chunk = get_chunk_by_position(driver, document, actual_end + 1)
                curr_last = chunks[-1]['text'] if chunks else ""
                if next_chunk and needs_continuation(curr_last, next_chunk['text']):
                    actual_end += 1
                    chunks.append(next_chunk)
                else:
                    break
        
        # Birleşik text oluştur
        combined_text = '\n\n'.join([c['text'] for c in chunks])
        
        results.append({
            'range': [actual_start, actual_end],
            'original_range': [start_pos, end_pos],
            'chunk_count': len(chunks),
            'text': combined_text
        })
    
    return results


def get_chunk_by_position(driver, document: str, position: int) -> dict | None:
    """Belirli pozisyondaki chunk'ı getir"""
    cypher = """
    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
    WHERE d.fileName = $document AND c.position = $position
    RETURN c.position as position, c.text as text
    """
    
    with driver.session() as session:
        result = session.run(cypher, document=document, position=position)
        record = result.single()
        return dict(record) if record else None


def get_max_position(driver, document: str) -> int:
    """Belgedeki maksimum chunk pozisyonunu getir"""
    cypher = """
    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
    WHERE d.fileName = $document
    RETURN max(c.position) as max_pos
    """
    
    with driver.session() as session:
        result = session.run(cypher, document=document)
        record = result.single()
        return record['max_pos'] if record else 0


# ============================================
# TEST RUNNER
# ============================================

def run_comparison_test(driver, query: dict, document: str = None):
    """Tek bir sorgu için 3 arama yöntemini karşılaştır"""
    
    print(f"\n{'='*60}")
    print(f"SORGU: {query['question']}")
    print(f"Keywords: {query['keywords']}")
    if query.get('keywords_fuzzy'):
        print(f"Fuzzy variants: {query['keywords_fuzzy']}")
    print(f"{'='*60}")
    
    results = {}
    
    # 1. Fulltext Search - tüm keyword varyantları ile
    all_keywords = query['keywords'] + query.get('keywords_fuzzy', [])
    
    print("\n📚 FULLTEXT SEARCH (tüm varyantlar)")
    print("-" * 40)
    ft_results = fulltext_search(driver, all_keywords, limit=10)
    
    if document:
        ft_results = [r for r in ft_results if r['document'] == document]
    
    ft_positions = {}
    for r in ft_results[:5]:
        doc = r['document']
        if doc not in ft_positions:
            ft_positions[doc] = []
        ft_positions[doc].append(r['position'])
        print(f"  [{r['score']:.2f}] {doc[:40]}... pos:{r['position']}")
        print(f"      {r['text'][:100]}...")
    
    results['fulltext'] = {
        'count': len(ft_results),
        'positions': ft_positions
    }
    
    # 2. Fuzzy Search - typo varyantları ile
    print("\n🔍 FUZZY SEARCH (typo tolerant)")
    print("-" * 40)
    fz_results = fuzzy_search(driver, all_keywords, limit=10)
    
    if document:
        fz_results = [r for r in fz_results if r['document'] == document]
    
    fz_positions = {}
    for r in fz_results[:5]:
        doc = r['document']
        if doc not in fz_positions:
            fz_positions[doc] = []
        fz_positions[doc].append(r['position'])
        print(f"  [{r['score']:.2f}] {doc[:40]}... pos:{r['position']}")
        print(f"      {r['text'][:100]}...")
    
    results['fuzzy'] = {
        'count': len(fz_results),
        'positions': fz_positions
    }
    
    # 3. Semantic Search
    print("\n🧠 SEMANTIC SEARCH")
    print("-" * 40)
    try:
        sem_results = semantic_search(driver, query['question'], limit=10)
        
        if document:
            sem_results = [r for r in sem_results if r['document'] == document]
        
        sem_positions = {}
        for r in sem_results[:5]:
            doc = r['document']
            if doc not in sem_positions:
                sem_positions[doc] = []
            sem_positions[doc].append(r['position'])
            print(f"  [{r['score']:.4f}] {doc[:40]}... pos:{r['position']}")
            print(f"      {r['text'][:100]}...")
        
        results['semantic'] = {
            'count': len(sem_results),
            'positions': sem_positions
        }
    except Exception as e:
        print(f"  ❌ Hata: {e}")
        results['semantic'] = {'count': 0, 'positions': {}}
    
    # 4. Smart Window Expansion (ilk belge için, needs_continuation KAPALI)
    if ft_positions:
        first_doc = list(ft_positions.keys())[0]
        all_positions = ft_positions.get(first_doc, [])
        
        print(f"\n📦 SMART WINDOW EXPANSION ({first_doc[:30]}...)")
        print("-" * 40)
        print(f"  Orijinal pozisyonlar: {all_positions}")
        
        # use_continuation=False ile çalıştır
        expanded = get_expanded_chunks(driver, first_doc, all_positions, window_size=WINDOW_SIZE, use_continuation=False)
        
        for exp in expanded:
            print(f"  📍 Range: {exp['original_range']} → {exp['range']} ({exp['chunk_count']} chunk)")
            preview = exp['text'][:300].replace('\n', ' ')
            print(f"      {preview}...")
        
        results['expanded'] = expanded
    
    return results


def run_all_tests(document: str = None):
    """Tüm test sorgularını çalıştır"""
    driver = get_driver()
    
    print("""
╔══════════════════════════════════════════════════════════╗
║     CHUNK SEARCH KARŞILAŞTIRMA TESTİ                    ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    if document:
        print(f"📄 Belge filtresi: {document}")
    
    all_results = {}
    
    for query in TEST_QUERIES:
        try:
            result = run_comparison_test(driver, query, document)
            all_results[query['id']] = result
        except Exception as e:
            print(f"❌ Hata ({query['id']}): {e}")
    
    # Özet
    print(f"\n{'='*60}")
    print("ÖZET")
    print(f"{'='*60}")
    
    for qid, res in all_results.items():
        print(f"\n{qid}:")
        print(f"  Fulltext: {res.get('fulltext', {}).get('count', 0)} sonuç")
        print(f"  Fuzzy:    {res.get('fuzzy', {}).get('count', 0)} sonuç")
        print(f"  Semantic: {res.get('semantic', {}).get('count', 0)} sonuç")
    
    driver.close()
    return all_results


# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Chunk Search Karşılaştırma Testi")
    parser.add_argument("--document", "-d", help="Belirli bir belge ile filtrele")
    parser.add_argument("--query", "-q", help="Tek bir sorgu çalıştır (id)")
    parser.add_argument("--list-docs", "-l", action="store_true", help="Belgeleri listele")
    
    args = parser.parse_args()
    
    if args.list_docs:
        driver = get_driver()
        cypher = "MATCH (d:Document) RETURN d.fileName ORDER BY d.fileName LIMIT 20"
        with driver.session() as session:
            result = session.run(cypher)
            print("\n📄 Belgeler:")
            for r in result:
                print(f"  - {r['d.fileName']}")
        driver.close()
    else:
        run_all_tests(document=args.document)
