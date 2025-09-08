#!/usr/bin/env python3
"""
PDF dosyalarını bulan ve dosya adının sonuna bulunduğu klasörün adını ekleyen script.
"""

import os
import shutil
from pathlib import Path
import argparse


def find_and_rename_pdfs(root_directory, dry_run=False, file_extension=None):
    """
    Belirtilen dizinin altındaki tüm PDF dosyalarını bulur ve 
    dosya adının sonuna bulunduğu klasörün adını ekler.
    
    Args:
        root_directory (str): Aranacak ana dizin
        dry_run (bool): Sadece ne yapılacağını göster, gerçek işlem yapma
        file_extension (str): Spesifik dosya uzantısı (örn: '.PDF', '.pdf', '.Pdf')
    
    Returns:
        list: İşlem yapılan dosyaların listesi
    """
    root_path = Path(root_directory)
    
    if not root_path.exists() or not root_path.is_dir():
        print(f"❌ Hata: '{root_directory}' dizini bulunamadı!")
        return []
    
    # PDF dosyalarını bul
    pdf_files = []
    for file_path in root_path.rglob("*"):
        if file_path.is_file():
            if file_extension:
                # Kullanıcı spesifik uzantı belirtmişse, tam eşleşme ara
                if file_path.suffix == file_extension:
                    pdf_files.append(file_path)
            else:
                # Uzantı belirtilmemişse, büyük-küçük harf duyarsız PDF ara
                if file_path.suffix.lower() == '.pdf':
                    pdf_files.append(file_path)
    
    if not pdf_files:
        print("📁 Hiç PDF dosyası bulunamadı.")
        return []
    
    print(f"📄 {len(pdf_files)} PDF dosyası bulundu:")
    print("-" * 60)
    
    processed_files = []
    
    for pdf_file in pdf_files:
        try:
            # Dosyanın bulunduğu klasörün adı
            folder_name = pdf_file.parent.name
            
            # Dosya adı ve uzantısı
            file_stem = pdf_file.stem  # uzantısız dosya adı
            file_suffix = pdf_file.suffix  # .pdf
            
            # Yeni dosya adı oluştur (eğer klasör adı zaten yoksa)
            if folder_name not in file_stem:
                new_filename = f"{file_stem}_{folder_name}{file_suffix}"
            else:
                print(f"⏭️  Atlanıyor (zaten var): {pdf_file.name}")
                continue
            
            new_file_path = pdf_file.parent / new_filename
            
            # Eğer aynı isimde dosya zaten varsa, sayı ekle
            counter = 1
            while new_file_path.exists():
                new_filename = f"{file_stem}_{folder_name}_{counter}{file_suffix}"
                new_file_path = pdf_file.parent / new_filename
                counter += 1
            
            print(f"📁 Klasör: {folder_name}")
            print(f"📄 Eski: {pdf_file.name}")
            print(f"📝 Yeni: {new_filename}")
            
            if not dry_run:
                # Dosyayı yeniden adlandır
                pdf_file.rename(new_file_path)
                print("✅ Başarılı!")
            else:
                print("🔍 DRY RUN - İşlem yapılmadı")
            
            processed_files.append({
                'old_path': str(pdf_file),
                'new_path': str(new_file_path),
                'folder_name': folder_name
            })
            
            print("-" * 60)
            
        except Exception as e:
            print(f"❌ Hata: {pdf_file} işlenirken hata oluştu: {e}")
            print("-" * 60)
            continue
    
    return processed_files


def main():
    """Ana fonksiyon"""
    parser = argparse.ArgumentParser(
        description="PDF dosyalarını bulan ve sonuna klasör adını ekleyen araç"
    )
    parser.add_argument(
        "directory", 
        help="Aranacak ana dizin yolu"
    )
    parser.add_argument(
        "--extension", "-e",
        help="Spesifik dosya uzantısı (örn: .PDF, .pdf, .Pdf). Belirtilmezse tüm PDF uzantıları işlenir"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="Sadece ne yapılacağını göster, gerçek işlem yapma"
    )
    parser.add_argument(
        "--recursive", 
        action="store_true", 
        default=True,
        help="Alt klasörlerde de ara (varsayılan: True)"
    )
    
    args = parser.parse_args()
    
    print("🔍 PDF Dosya Yeniden Adlandırma Aracı")
    print("=" * 60)
    print(f"📁 Ana Dizin: {args.directory}")
    print(f"� Dosya Uzantısı: {args.extension if args.extension else 'Tümü (.pdf, .PDF, .Pdf, vb.)'}")
    print(f"�🔄 Recursive: {args.recursive}")
    print(f"🔍 Dry Run: {args.dry_run}")
    print("=" * 60)
    
    # İşlemi başlat
    processed = find_and_rename_pdfs(args.directory, args.dry_run, args.extension)
    
    print(f"\n📊 İşlem Özeti:")
    print(f"✅ İşlenen dosya sayısı: {len(processed)}")
    
    if args.dry_run:
        print("\n💡 Gerçek işlem yapmak için --dry-run parametresini kaldırın.")


if __name__ == "__main__":
    # Eğer komut satırından çalıştırılmıyorsa, örnek kullanım
    if len(os.sys.argv) == 1:
        print("🔍 PDF Dosya Yeniden Adlandırma Aracı")
        print("=" * 60)
        print("Kullanım örnekleri:")
        print("python pdf_renamer.py /path/to/directory")
        print("python pdf_renamer.py /path/to/directory --dry-run")
        print("python pdf_renamer.py /path/to/directory --extension .PDF")
        print("python pdf_renamer.py /path/to/directory --extension .pdf")
        print()
        
        # Mevcut dizinde örnek çalıştır
        current_dir = os.getcwd()
        print(f"Mevcut dizinde ({current_dir}) örnek çalıştırılıyor...")
        processed = find_and_rename_pdfs(current_dir, dry_run=True)
    else:
        main()
