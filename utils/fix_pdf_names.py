#!/usr/bin/env python3
"""
PDF dosyalarının isimlerinin sonunda bulundukları klasörün yıl bilgisini ekleyen script.
Bu script, dinkal klasörü altındaki her yıl klasöründeki PDF dosyalarını kontrol eder
ve dosya isminin sonunda yıl bilgisi yoksa ekler.
"""

import os
import re
from pathlib import Path
import logging

# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def has_year_suffix(filename: str, year: str) -> bool:
    """
    Dosya isminin sonunda yıl bilgisinin olup olmadığını kontrol eder.
    
    Args:
        filename (str): Kontrol edilecek dosya adı
        year (str): Beklenen yıl bilgisi
        
    Returns:
        bool: Yıl bilgisi varsa True, yoksa False
    """
    # .pdf uzantısını çıkar
    base_name = filename.replace('.pdf', '')
    
    # Dosya isminin sonunda yıl bilgisi var mı kontrol et
    # Örnekler: "filename 2020.pdf", "filename_2020.pdf", "filename2020.pdf"
    pattern = rf'.*\s*{year}\s*$'
    return bool(re.search(pattern, base_name))

def add_year_to_filename(filename: str, year: str) -> str:
    """
    Dosya isminin sonuna yıl bilgisini ekler.
    
    Args:
        filename (str): Orijinal dosya adı
        year (str): Eklenecek yıl bilgisi
        
    Returns:
        str: Yıl bilgisi eklenmiş dosya adı
    """
    base_name = filename.replace('.pdf', '')
    return f"{base_name} {year}.pdf"

def process_year_directory(year_dir_path: Path) -> None:
    """
    Belirtilen yıl klasöründeki PDF dosyalarını işler.
    
    Args:
        year_dir_path (Path): İşlenecek yıl klasörünün yolu
    """
    year = year_dir_path.name
    logger.info(f"{year} klasörü işleniyor...")
    
    # Klasördeki tüm PDF dosyalarını listele
    pdf_files = [f for f in os.listdir(year_dir_path) if f.endswith('.pdf')]
    
    renamed_count = 0
    
    for pdf_file in pdf_files:
        if not has_year_suffix(pdf_file, year):
            old_path = year_dir_path / pdf_file
            new_filename = add_year_to_filename(pdf_file, year)
            new_path = year_dir_path / new_filename
            
            try:
                # Dosyayı yeniden adlandır
                os.rename(old_path, new_path)
                logger.info(f"Yeniden adlandırıldı: {pdf_file} -> {new_filename}")
                renamed_count += 1
                
            except OSError as e:
                logger.error(f"Hata oluştu {pdf_file} dosyası işlenirken: {e}")
        else:
            logger.debug(f"Yıl bilgisi zaten mevcut: {pdf_file}")
    
    logger.info(f"{year} klasörü tamamlandı. {renamed_count} dosya yeniden adlandırıldı.")

def main():
    """Ana fonksiyon - dinkal klasörü altındaki tüm yıl klasörlerini işler."""
    dinkal_path = Path("/Users/mehmeterdogan/python-projects/llm-graph-builder/dinkal")
    
    if not dinkal_path.exists():
        logger.error(f"Dinkal klasörü bulunamadı: {dinkal_path}")
        return
    
    logger.info("PDF dosya isimleri düzenleme işlemi başlatılıyor...")
    
    # Sadece yıl klasörlerini işle (2020, 2021, 2022, 2023)
    year_dirs = [d for d in dinkal_path.iterdir() 
                 if d.is_dir() and d.name.isdigit() and len(d.name) == 4]
    
    if not year_dirs:
        logger.warning("Yıl klasörleri bulunamadı.")
        return
    
    total_processed = 0
    for year_dir in sorted(year_dirs):
        process_year_directory(year_dir)
        total_processed += 1
    
    logger.info(f"İşlem tamamlandı. {total_processed} yıl klasörü işlendi.")

if __name__ == "__main__":
    main()
