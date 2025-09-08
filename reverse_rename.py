#!/usr/bin/env python3
"""
PDF dosyalarından '_not_uploaded' eklentisini çıkarma scripti
"""

import os
import argparse
from pathlib import Path

def reverse_rename_pdfs(directory_path):
    """Belirtilen dizindeki PDF dosyalarından '_not_uploaded' eklentisini çıkarır"""
    
    directory = Path(directory_path)
    
    if not directory.exists():
        print(f"❌ Hata: Dizin bulunamadı: {directory_path}")
        return
    
    if not directory.is_dir():
        print(f"❌ Hata: Belirtilen yol bir dizin değil: {directory_path}")
        return
    
    print(f"🔍 PDF Dosya Geri Alma Aracı")
    print("=" * 60)
    print(f"📁 Ana Dizin: {directory_path}")
    print("=" * 60)
    
    # PDF dosyalarını bul (case-insensitive)
    pdf_files = []
    for file_path in directory.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() == '.pdf':
            pdf_files.append(file_path)
    
    if not pdf_files:
        print("📄 Hiç PDF dosyası bulunamadı.")
        return
    
    print(f"📄 {len(pdf_files)} PDF dosyası bulundu:")
    print("-" * 60)
    
    renamed_count = 0
    
    for pdf_file in pdf_files:
        # Dosya adından '_not_uploaded' kısmını çıkar
        current_name = pdf_file.stem  # uzantı olmadan dosya adı
        current_extension = pdf_file.suffix  # uzantı (.pdf veya .PDF)
        
        # '_not_uploaded' varsa çıkar
        if '_not_uploaded' in current_name:
            new_name = current_name.replace('_not_uploaded', '')
            new_file_path = pdf_file.parent / f"{new_name}{current_extension}"
            
            try:
                pdf_file.rename(new_file_path)
                print(f"📁 Klasör: {pdf_file.parent.name}")
                print(f"📄 Eski: {pdf_file.name}")
                print(f"📝 Yeni: {new_file_path.name}")
                print(f"✅ Başarılı!")
                print("-" * 60)
                renamed_count += 1
            except Exception as e:
                print(f"❌ Hata: {pdf_file.name} -> {e}")
                print("-" * 60)
    
    print(f"\n📊 İşlem Özeti:")
    print(f"✅ Geri alınan dosya sayısı: {renamed_count}")

def main():
    parser = argparse.ArgumentParser(description="PDF dosyalarından '_not_uploaded' eklentisini çıkarır")
    parser.add_argument("directory", help="İşlenecek dizin yolu")
    
    args = parser.parse_args()
    
    reverse_rename_pdfs(args.directory)

if __name__ == "__main__":
    main()
