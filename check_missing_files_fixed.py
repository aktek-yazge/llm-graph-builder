#!/usr/bin/env python3
"""
Veritabanındaki belge isimlerini kontrol edip, dizindeki dosyalarla karşılaştırır.
Yüklenmeyen dosyaları 'not_uploaded' alt klasörüne taşır.
Unicode normalizasyon ile Türkçe karakter sorunlarını çözümlenir.
"""

import os
import shutil
import argparse
import unicodedata
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Environment dosyasını yükle
load_dotenv("backend/.env")

# Neo4j bağlantı bilgileri (.env dosyasından)
NEO4J_URI = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URI_SERVER")
NEO4J_USER = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Kontrol edilecek dizin (.env dosyasından veya varsayılan)
SOURCE_DIR = os.getenv("PDF_SOURCE_DIR", "/Users/mehmeterdogan/2020 POLİÇELER/2021")

def normalize_filename(filename):
    """Unicode karakterleri normalize eder - hem escape'li hem normal formları karşılaştırabilir"""
    if not filename:
        return ""
    
    # Unicode escape karakterlerini decode et (\u00e7 -> ç)
    try:
        # Eğer string'de \u escape karakterleri varsa decode et
        if '\\u' in filename:
            filename = filename.encode().decode('unicode_escape')
    except:
        pass
    
    # Unicode normalizasyon yap (NFC - Canonical Decomposition + Canonical Composition)
    normalized = unicodedata.normalize('NFC', filename)
    return normalized

def get_uploaded_files():
    """Veritabanındaki tüm belge isimlerini getirir ve normalize eder"""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        result = session.run("MATCH (d:Document) RETURN d.fileName as fileName")
        uploaded_files = set([normalize_filename(record["fileName"]) for record in result if record["fileName"]])
    
    driver.close()
    return uploaded_files

def get_directory_files(directory):
    """Dizindeki tüm PDF dosyalarını getirir ve normalize eder"""
    files = []
    for file in os.listdir(directory):
        if file.lower().endswith('.pdf'):
            files.append(normalize_filename(file))
    return set(files)

def move_not_uploaded_files(source_dir, uploaded_files, dry_run=False):
    """Yüklenmeyen dosyaları not_uploaded klasörüne taşır"""
    
    # not_uploaded klasörünü oluştur
    not_uploaded_dir = os.path.join(source_dir, "not_uploaded")
    
    if not dry_run:
        os.makedirs(not_uploaded_dir, exist_ok=True)
    
    # Dizindeki dosyaları kontrol et
    directory_files = get_directory_files(source_dir)
    
    # Yüklenmeyen dosyaları bul
    not_uploaded = directory_files - uploaded_files
    
    print(f"Toplam dizindeki PDF dosyası: {len(directory_files)}")
    print(f"Veritabanındaki dosya sayısı: {len(uploaded_files)}")
    print(f"Yüklenmeyen dosya sayısı: {len(not_uploaded)}")
    
    if dry_run:
        print("🔍 DRY RUN - Gerçek taşıma yapılmayacak, sadece önizleme:")
        print("📋 Unicode normalizasyon aktif - Türkçe karakter sorunları çözümlendi")
    else:
        print("📁 Gerçek taşıma işlemi yapılacak:")
    
    print("-" * 50)
    
    # Debug: Birkaç örnek dosya karşılaştırması göster
    if not_uploaded:
        print("📝 Örnek dosya karşılaştırması (ilk 3):")
        sample_files = list(not_uploaded)[:3]
        for sample in sample_files:
            print(f"   Eksik: '{sample}'")
            # Benzer isimli veritabanı dosyalarını ara
            similar = [f for f in uploaded_files if sample[:20] in f or f[:20] in sample][:2]
            if similar:
                for sim in similar:
                    print(f"   Benzer DB: '{sim}'")
        print("-" * 50)
    
    # Dosyaları taşı (veya dry-run'da sadece listele)
    moved_count = 0
    for file in not_uploaded:
        # Orijinal dosya adını dizinde bul (normalize edilmemiş hali)
        original_filename = None
        for original_file in os.listdir(source_dir):
            if original_file.lower().endswith('.pdf') and normalize_filename(original_file) == file:
                original_filename = original_file
                break
        
        if not original_filename:
            print(f"⚠️ Orijinal dosya bulunamadı: {file}")
            continue
            
        source_path = os.path.join(source_dir, original_filename)
        target_path = os.path.join(not_uploaded_dir, original_filename)
        
        if dry_run:
            print(f"[DRY RUN] Taşınacak: {original_filename}")
            moved_count += 1
        else:
            try:
                shutil.move(source_path, target_path)
                print(f"Taşındı: {original_filename}")
                moved_count += 1
            except Exception as e:
                print(f"Hata - {original_filename}: {e}")
    
    print("-" * 50)
    if dry_run:
        print(f"[DRY RUN] Toplam {moved_count} dosya taşınacak.")
    else:
        print(f"Toplam {moved_count} dosya taşındı.")

def main():
    """Ana fonksiyon"""
    
    # Command line arguments
    parser = argparse.ArgumentParser(description="Neo4j veritabanındaki belgelerle dizindeki dosyaları karşılaştırır")
    parser.add_argument("directory", nargs="?", help="Kontrol edilecek dizin (opsiyonel)")
    parser.add_argument("--dry-run", action="store_true", help="Gerçek taşıma yapmadan sadece önizleme")
    
    args = parser.parse_args()
    
    # Dizin belirleme: argüman -> environment -> varsayılan
    target_dir = args.directory or SOURCE_DIR
    
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
    if not os.path.exists(target_dir):
        print(f"Hata: Dizin bulunamadı - {target_dir}")
        return
    
    print(f"🎯 Kontrol edilecek dizin: {target_dir}")
    if args.dry_run:
        print("🔍 DRY RUN modu aktif - gerçek değişiklik yapılmayacak")
    print("🔤 Unicode normalizasyon aktif - Türkçe karakter uyumluluğu sağlandı")
    
    try:
        print("\nVeritabanından yüklenen dosyalar getiriliyor...")
        uploaded_files = get_uploaded_files()
        
        print("Dosya karşılaştırması yapılıyor...")
        move_not_uploaded_files(target_dir, uploaded_files, dry_run=args.dry_run)
        
        print("\nİşlem tamamlandı!")
        
    except Exception as e:
        print(f"Hata oluştu: {e}")
        print("Lütfen Neo4j bağlantı bilgilerini kontrol edin.")

if __name__ == "__main__":
    main()
