#!/usr/bin/env python3
"""
Veritabanındaki belge isimlerini kontrol edip, dizindeki dosyalarla karşılaştırır.
Yüklenmeyen dosyaları 'not_uploaded' alt klasörüne taşır.
"""

import os
import shutil
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Environment dosyasını yükle
load_dotenv("backend/.env")

# Neo4j bağlantı bilgileri (.env dosyasından)
NEO4J_URI = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URI_SERVER")
NEO4J_USER = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Kontrol edilecek dizin (.env dosyasından veya varsayılan)
SOURCE_DIR = os.getenv("PDF_SOURCE_DIR", "/Users/mehmeterdogan/2020 POLİÇELER/2021")

def get_uploaded_files():
    """Veritabanındaki tüm belge isimlerini getirir"""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        result = session.run("MATCH (d:Document) RETURN d.fileName as fileName")
        uploaded_files = set([record["fileName"] for record in result])
    
    driver.close()
    return uploaded_files

def get_directory_files(directory):
    """Dizindeki tüm PDF dosyalarını getirir"""
    files = []
    for file in os.listdir(directory):
        if file.lower().endswith('.pdf'):
            files.append(file)
    return set(files)

def move_not_uploaded_files(source_dir, uploaded_files):
    """Yüklenmeyen dosyaları not_uploaded klasörüne taşır"""
    
    # not_uploaded klasörünü oluştur
    not_uploaded_dir = os.path.join(source_dir, "not_uploaded")
    os.makedirs(not_uploaded_dir, exist_ok=True)
    
    # Dizindeki dosyaları kontrol et
    directory_files = get_directory_files(source_dir)
    
    # Yüklenmeyen dosyaları bul
    not_uploaded = directory_files - uploaded_files
    
    print(f"Toplam dizindeki PDF dosyası: {len(directory_files)}")
    print(f"Veritabanındaki dosya sayısı: {len(uploaded_files)}")
    print(f"Yüklenmeyen dosya sayısı: {len(not_uploaded)}")
    print("-" * 50)
    
    # Dosyaları taşı
    moved_count = 0
    for file in not_uploaded:
        source_path = os.path.join(source_dir, file)
        target_path = os.path.join(not_uploaded_dir, file)
        
        try:
            shutil.move(source_path, target_path)
            print(f"Taşındı: {file}")
            moved_count += 1
        except Exception as e:
            print(f"Hata - {file}: {e}")
    
    print("-" * 50)
    print(f"Toplam {moved_count} dosya taşındı.")

def main():
    """Ana fonksiyon"""
    
    # Environment değişkenlerini kontrol et
    if not NEO4J_URI:
        print("Hata: NEO4J_URI environment değişkeni bulunamadı")
        print("Lütfen .env dosyasını kontrol edin.")
        return
        
    if not NEO4J_PASSWORD:
        print("Hata: NEO4J_PASSWORD environment değişkeni bulunamadı")
        print("Lütfen .env dosyasını kontrol edin.")
        return
    
    # Dizin kontrolü
    if not os.path.exists(SOURCE_DIR):
        print(f"Hata: Dizin bulunamadı - {SOURCE_DIR}")
        print("Lütfen PDF_SOURCE_DIR environment değişkenini kontrol edin veya .env dosyasına ekleyin.")
        return
    
    try:
        print("Veritabanından yüklenen dosyalar getiriliyor...")
        uploaded_files = get_uploaded_files()
        
        print("Dosya karşılaştırması yapılıyor...")
        move_not_uploaded_files(SOURCE_DIR, uploaded_files)
        
        print("İşlem tamamlandı!")
        
    except Exception as e:
        print(f"Hata oluştu: {e}")
        print("Lütfen Neo4j bağlantı bilgilerini kontrol edin.")

if __name__ == "__main__":
    main()
