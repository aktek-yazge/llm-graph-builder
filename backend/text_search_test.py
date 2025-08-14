#!/usr/bin/env python3
"""
Mevcut embedding'ler ile direkt sorgu testi
"""

import os
from neo4j import GraphDatabase

# Neo4j bağlantı bilgileri
NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')

def search_by_text_keywords():
    """Text içinde anahtar kelimeler ile arama"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        print("=== ANAHTAR KELİME ARAMASI ===")
        print("Sorgu: ayça hanımın 2020 yılı D4 konut projesinin taksitleri ne kadar")
        print()
        
        # 1. "AYÇA" ismi geçen dokümanlara bak
        print("1. AYÇA ismi geçen dokümanlara bakılıyor...")
        result = session.run("""
            MATCH (d:Document)
            WHERE toLower(d.fileName) CONTAINS 'ayça' 
               OR toLower(toString(d.fileName)) CONTAINS 'ayca'
            RETURN d.fileName AS document_name, d.fileSize, d.pageCount
            ORDER BY d.fileName
        """)
        
        ayca_docs = list(result)
        if ayca_docs:
            print(f"✅ AYÇA ile ilgili {len(ayca_docs)} dokuman bulundu:")
            for doc in ayca_docs:
                print(f"   📄 {doc['document_name']}")
        else:
            print("❌ AYÇA isimli dokuman bulunamadı")
        
        print()
        
        # 2. "D4" geçen chunk'lara bak
        print("2. D4 geçen chunk'lara bakılıyor...")
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE toLower(toString(c.text)) CONTAINS 'd4'
               OR toLower(toString(c.text)) CONTAINS 'daire 4'
               OR toLower(toString(c.text)) CONTAINS 'daire no: 4'
               OR toLower(toString(c.text)) CONTAINS 'daire no:4'
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                c.position AS chunk_position
            ORDER BY d.fileName, c.position
            LIMIT 10
        """)
        
        d4_chunks = list(result)
        if d4_chunks:
            print(f"✅ D4 ile ilgili {len(d4_chunks)} chunk bulundu:")
            for i, chunk in enumerate(d4_chunks, 1):
                print(f"\n   {i}. Chunk:")
                print(f"      📄 Dokuman: {chunk['document_name']}")
                print(f"      📍 Position: {chunk['chunk_position']}")
                chunk_text = str(chunk['chunk_text'])
                if isinstance(chunk_text, list) and len(chunk_text) > 0:
                    chunk_text = chunk_text[0]
                print(f"      📝 Text (ilk 300 karakter):")
                print(f"         {chunk_text[:300]}...")
        else:
            print("❌ D4 ile ilgili chunk bulunamadı")
        
        print()
        
        # 3. "taksit" geçen chunk'lara bak
        print("3. TAKSİT geçen chunk'lara bakılıyor...")
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE toLower(toString(c.text)) CONTAINS 'taksit'
               OR toLower(toString(c.text)) CONTAINS 'taksit'
               OR toLower(toString(c.text)) CONTAINS 'ödeme'
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                c.position AS chunk_position
            ORDER BY d.fileName, c.position
            LIMIT 15
        """)
        
        taksit_chunks = list(result)
        if taksit_chunks:
            print(f"✅ TAKSİT ile ilgili {len(taksit_chunks)} chunk bulundu:")
            for i, chunk in enumerate(taksit_chunks[:5], 1):  # İlk 5'ini göster
                print(f"\n   {i}. Chunk:")
                print(f"      📄 Dokuman: {chunk['document_name']}")
                print(f"      📍 Position: {chunk['chunk_position']}")
                chunk_text = str(chunk['chunk_text'])
                if isinstance(chunk_text, list) and len(chunk_text) > 0:
                    chunk_text = chunk_text[0]
                print(f"      📝 Text (ilk 300 karakter):")
                print(f"         {chunk_text[:300]}...")
        else:
            print("❌ TAKSİT ile ilgili chunk bulunamadı")
        
        print()
        
        # 4. AYÇA + 2020 + D4 + TAKSİT kombinasyonu
        print("4. AYÇA + 2020 + D4 + TAKSİT kombinasyonu aranıyor...")
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE (toLower(d.fileName) CONTAINS 'ayça' OR toLower(d.fileName) CONTAINS 'ayca')
            AND (toLower(toString(c.text)) CONTAINS '2020' OR toLower(d.fileName) CONTAINS '2020')
            AND (toLower(toString(c.text)) CONTAINS 'd4' 
                 OR toLower(toString(c.text)) CONTAINS 'daire 4'
                 OR toLower(toString(c.text)) CONTAINS 'daire no: 4'
                 OR toLower(toString(c.text)) CONTAINS 'daire no:4')
            AND (toLower(toString(c.text)) CONTAINS 'taksit' 
                 OR toLower(toString(c.text)) CONTAINS 'ödeme')
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                c.position AS chunk_position
            ORDER BY d.fileName, c.position
        """)
        
        combined_chunks = list(result)
        if combined_chunks:
            print(f"🎯 MÜKEMMEL! Tüm kriterleri karşılayan {len(combined_chunks)} chunk bulundu:")
            for i, chunk in enumerate(combined_chunks, 1):
                print(f"\n   {i}. HEDEF CHUNK:")
                print(f"      📄 Dokuman: {chunk['document_name']}")
                print(f"      📍 Position: {chunk['chunk_position']}")
                chunk_text = str(chunk['chunk_text'])
                if isinstance(chunk_text, list) and len(chunk_text) > 0:
                    chunk_text = chunk_text[0]
                print(f"      📝 TAM METİN:")
                print(f"         {chunk_text}")
                print(f"      ---")
        else:
            print("❌ Tüm kriterleri karşılayan chunk bulunamadı")
            
            # Alternatif arama: Sadece AYÇA + TAKSİT
            print("\n   📍 Alternatif: AYÇA + TAKSİT aranıyor...")
            result = session.run("""
                MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
                WHERE (toLower(d.fileName) CONTAINS 'ayça' OR toLower(d.fileName) CONTAINS 'ayca')
                AND (toLower(toString(c.text)) CONTAINS 'taksit' 
                     OR toLower(toString(c.text)) CONTAINS 'ödeme'
                     OR toLower(toString(c.text)) CONTAINS 'tutar'
                     OR toLower(toString(c.text)) CONTAINS 'prim')
                RETURN 
                    d.fileName AS document_name,
                    c.text AS chunk_text,
                    c.position AS chunk_position
                ORDER BY d.fileName, c.position
            """)
            
            alt_chunks = list(result)
            if alt_chunks:
                print(f"   ✅ AYÇA + ödeme ile {len(alt_chunks)} chunk bulundu:")
                for i, chunk in enumerate(alt_chunks[:3], 1):
                    print(f"\n      {i}. Chunk:")
                    print(f"         📄 Dokuman: {chunk['document_name']}")
                    print(f"         📍 Position: {chunk['chunk_position']}")
                    chunk_text = str(chunk['chunk_text'])
                    if isinstance(chunk_text, list) and len(chunk_text) > 0:
                        chunk_text = chunk_text[0]
                    print(f"         📝 Text:")
                    print(f"            {chunk_text}")
                    print(f"         ---")
    
    driver.close()

if __name__ == "__main__":
    search_by_text_keywords()
