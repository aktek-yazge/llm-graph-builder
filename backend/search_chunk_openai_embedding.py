#!/usr/bin/env python3
"""
Neo4j Chunk node'larında OpenAI embedding ile vektör araması yapan script.

Kullanım:
  python search_chunk_openai_embedding.py --question "soru metni" --top_k 5 --threshold 0.3

Bu script aşağıyı yapar:
- OpenAI ile sorgu metninin embedding'ini oluşturur.
- Neo4j'de `Chunk` düğümlerinin `embedding` özelliği ile cosine similarity hesaplayıp sıralar.
- Eşleşen chunk'ları ve ilgili Document dosya adını listeler.
"""

import os
import argparse
import openai
from neo4j import GraphDatabase
from dotenv import load_dotenv


# --- Konfigürasyon ve environment yükleme ---
load_dotenv()

NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY


def get_embedding(text: str):
    """OpenAI ile text embedding oluşturur. Dönen embedding list[float]."""
    try:
        resp = openai.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return resp.data[0].embedding
    except Exception as e:
        print(f"Embedding oluşturma hatası: {e}")
        return None


def search_chunks(query_vector, top_k=10, min_score=None):
    """Neo4j'de Chunk düğümlerini embedding ile arar.

    Returns: list of records with document_name, chunk_text, chunk_position, score
    """
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    cypher = """
    MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
    WHERE c.embedding IS NOT NULL
    WITH d, c, vector.similarity.cosine($query_vector, c.embedding) AS score
    WHERE ($min_score IS NULL OR score > $min_score)
    RETURN d.fileName AS document_name, c.text AS chunk_text, c.position AS chunk_position, score
    ORDER BY score DESC
    LIMIT $top_k
    """

    with driver.session() as session:
        result = session.run(cypher, query_vector=query_vector, top_k=top_k, min_score=min_score)
        records = list(result)

    driver.close()
    return records


def print_results(records):
    if not records:
        print("Hiç sonuç bulunamadı.")
        return

    print(f"{len(records)} sonuç bulundu. En iyi sonuçlar:\n")
    for i, r in enumerate(records, start=1):
        doc = r.get('document_name')
        chunk = r.get('chunk_text')
        pos = r.get('chunk_position')
        score = r.get('score')

        # chunk text bazen liste ya da başka formatta olabilir
        if isinstance(chunk, list) and len(chunk) > 0:
            chunk = chunk[0]
        chunk_snippet = (str(chunk)[:300] + '...') if chunk else ''

        print(f"{i}. Document: {doc}")
        print(f"   Position: {pos} | Score: {score:.4f}")
        print(f"   Text: {chunk_snippet}\n")


def main():
    parser = argparse.ArgumentParser(description='OpenAI embedding ile Neo4j Chunk araması')
    parser.add_argument('--question', '-q', required=True, help='Aranacak soru/metin')
    parser.add_argument('--top_k', '-k', type=int, default=5, help='Geri döndürülecek maksimum sonuç sayısı')
    parser.add_argument('--threshold', '-t', type=float, default=None, help='Minimum similarity skor eşiği (0-1 arası)')

    args = parser.parse_args()

    print(f"Sorgu: {args.question}")

    # 1) Embedding oluştur
    print("1) Embedding oluşturuluyor...")
    embedding = get_embedding(args.question)
    if not embedding:
        print("Embedding alınamadı, çıkılıyor.")
        return
    print(f"✅ Embedding oluşturuldu (boyut: {len(embedding)})")

    # 2) Neo4j araması
    print("2) Neo4j Chunk düğümlerinde aranıyor...")
    records = search_chunks(embedding, top_k=args.top_k, min_score=args.threshold)

    # 3) Sonuçları yazdır
    print_results(records)


if __name__ == '__main__':
    main()
