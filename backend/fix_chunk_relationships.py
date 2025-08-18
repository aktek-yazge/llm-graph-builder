#!/usr/bin/env python3
"""
Chunk ve Document İlişki Temizleme Script'i
Bu script, yanlış PART_OF ilişkilerini temizler ve chunk ID'lerini dosya adı ile birlikte yeniden oluşturur.
"""

import os
import hashlib
import logging
from datetime import datetime
from dotenv import load_dotenv
from src.shared.common_fn import create_graph_database_connection, execute_graph_query
from src.utf8_utils import normalize_unicode_text, normalize_file_name

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_graph_connection():
    """Neo4j bağlantısını oluştur"""
    uri = os.getenv('NEO4J_URI')
    username = os.getenv('NEO4J_USERNAME') 
    password = os.getenv('NEO4J_PASSWORD')
    database = os.getenv('NEO4J_DATABASE')
    
    return create_graph_database_connection(uri, username, password, database)

def analyze_duplicate_chunks(graph):
    """Duplicate chunk ID'leri analiz et"""
    query = """
    MATCH (c:Chunk)
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
    WITH c.chunkId AS chunkId, c.fileName AS chunkFileName, 
         collect(DISTINCT d.fileName) AS connectedDocs,
         count(DISTINCT d) AS docCount
    WHERE docCount > 1
    RETURN chunkId, chunkFileName, connectedDocs, docCount
    ORDER BY docCount DESC
    LIMIT 50
    """
    
    results = execute_graph_query(graph, query)
    
    if results:
        logging.info(f"Problematik chunk sayısı: {len(results)}")
        for result in results[:10]:  # İlk 10'unu göster
            logging.info(f"Chunk ID: {result['chunkId']}")
            logging.info(f"  Chunk dosyası: {result['chunkFileName']}")
            logging.info(f"  Bağlı dökümanlar ({result['docCount']}): {result['connectedDocs']}")
            logging.info("---")
    else:
        logging.info("Problematik chunk bulunamadı!")
    
    return results

def regenerate_chunk_ids(graph, dry_run=True):
    """Chunk ID'lerini dosya adı ile birlikte yeniden oluştur"""
    
    # Mevcut chunk'ları al
    query = """
    MATCH (c:Chunk)
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
    RETURN c.chunkId AS oldChunkId, c.fileName AS chunkFileName, 
           c.text AS content, d.fileName AS documentFileName,
           c.position AS position, c.length AS length, c.content_offset AS content_offset,
           c.page_number AS page_number, c.start_time AS start_time, c.end_time AS end_time
    ORDER BY c.fileName, c.position
    """
    
    chunks = execute_graph_query(graph, query)
    logging.info(f"Toplam {len(chunks)} chunk bulundu")
    
    updates = []
    
    for chunk in chunks:
        old_id = chunk['oldChunkId']
        file_name = chunk['chunkFileName']
        content = chunk['content']
        
        if not file_name or not content:
            logging.warning(f"Eksik veri: chunk {old_id}")
            continue
            
        # Normalize dosya adı ve içerik
        normalized_file_name = normalize_file_name(file_name)
        normalized_content = normalize_unicode_text(content)
        
        # Yeni ID oluştur (dosya adı + içerik)
        content_with_filename = f"{normalized_file_name}:::{normalized_content}"
        new_id = hashlib.sha1(content_with_filename.encode('utf-8')).hexdigest()
        
        if old_id != new_id:
            updates.append({
                'old_id': old_id,
                'new_id': new_id,
                'file_name': file_name,
                'document_file': chunk['documentFileName'],
                'position': chunk['position'],
                'length': chunk['length'],
                'content_offset': chunk['content_offset'],
                'page_number': chunk['page_number'],
                'start_time': chunk['start_time'],
                'end_time': chunk['end_time'],
                'content': content
            })
    
    logging.info(f"Güncellenecek chunk sayısı: {len(updates)}")
    
    if dry_run:
        logging.info("DRY RUN modu - değişiklikler uygulanmayacak")
        for update in updates[:5]:  # İlk 5'ini göster
            logging.info(f"  {update['old_id']} -> {update['new_id']} ({update['file_name']})")
        return updates
    
    # Gerçek güncelleme
    batch_size = 100
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i+batch_size]
        
        # Önce yeni chunk'ları oluştur
        create_query = """
        UNWIND $updates AS update
        CREATE (c:Chunk {
            id: update.new_id,
            chunkId: update.new_id,
            text: update.content,
            fileName: update.file_name,
            position: update.position,
            length: update.length,
            content_offset: update.content_offset
        })
        SET c.page_number = CASE WHEN update.page_number IS NOT NULL THEN update.page_number END,
            c.start_time = CASE WHEN update.start_time IS NOT NULL THEN update.start_time END,
            c.end_time = CASE WHEN update.end_time IS NOT NULL THEN update.end_time END
        """
        
        execute_graph_query(graph, create_query, params={'updates': batch})
        
        # PART_OF ilişkilerini oluştur (doğru Document'lara)
        relation_query = """
        UNWIND $updates AS update
        MATCH (c:Chunk {id: update.new_id})
        MATCH (d:Document {fileName: update.file_name})
        MERGE (c)-[:PART_OF]->(d)
        """
        
        execute_graph_query(graph, relation_query, params={'updates': batch})
        
        logging.info(f"Batch {i//batch_size + 1}/{(len(updates)-1)//batch_size + 1} tamamlandı")
    
    # Eski chunk'ları sil
    delete_query = """
    UNWIND $old_ids AS old_id
    MATCH (c:Chunk {id: old_id})
    DETACH DELETE c
    """
    
    old_ids = [update['old_id'] for update in updates]
    for i in range(0, len(old_ids), batch_size):
        batch = old_ids[i:i+batch_size]
        execute_graph_query(graph, delete_query, params={'old_ids': batch})
    
    logging.info("Chunk ID güncellemesi tamamlandı")
    return updates

def main():
    """Ana fonksiyon"""
    graph = get_graph_connection()
    
    print("1. Problematik chunk'ları analiz et")
    print("2. Chunk ID'lerini düzelt (DRY RUN)")  
    print("3. Chunk ID'lerini düzelt (GERÇEK)")
    print("0. Çıkış")
    
    choice = input("Seçiminiz: ")
    
    if choice == "1":
        analyze_duplicate_chunks(graph)
    elif choice == "2":
        regenerate_chunk_ids(graph, dry_run=True)
    elif choice == "3":
        confirm = input("Bu işlem geri alınamaz! Devam etmek istiyor musunuz? (evet/hayır): ")
        if confirm.lower() in ['evet', 'yes', 'y']:
            regenerate_chunk_ids(graph, dry_run=False)
        else:
            print("İşlem iptal edildi")
    elif choice == "0":
        print("Çıkış yapılıyor...")
    else:
        print("Geçersiz seçim!")

if __name__ == "__main__":
    main()
