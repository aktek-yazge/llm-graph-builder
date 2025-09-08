#!/usr/bin/env python3
"""
LLM kullanarak zeyilname belgelerini tespit eden ve ZEYILNAME klasörüne taşıyan script
"""
import os
import sys
import shutil
import json
from pathlib import Path

# Backend path'ini ekle
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend')

from src.graphDB_dataAccess import graphDBdataAccess
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/.env')

class ZeyilnameOrganizer:
    def __init__(self):
        """
        ZeyilnameOrganizer sınıfını başlat
        """
        try:
            # Neo4j bağlantısı
            graph = Neo4jGraph(
                url=os.getenv('NEO4J_URI'),
                username=os.getenv('NEO4J_USERNAME'), 
                password=os.getenv('NEO4J_PASSWORD')
            )
            
            self.db = graphDBdataAccess(graph)
            print("✅ LLM bağlantısı başarılı")
            
        except Exception as e:
            print(f"❌ LLM bağlantısı başarısız: {e}")
            sys.exit(1)
    
    def is_endorsement_document(self, file_path: str) -> dict:
        """
        LLM kullanarak dosyanın zeyilname olup olmadığını kontrol eder
        
        Args:
            file_path: Kontrol edilecek dosya yolu
            
        Returns:
            dict: {
                'is_endorsement': bool,
                'document_type': str,
                'confidence': str,
                'details': dict
            }
        """
        try:
            file_name = os.path.basename(file_path)
            base_name = os.path.splitext(file_name)[0]
            
            print(f"🔍 LLM analizi: {file_name}")
            
            # LLM ile dosya ismini analiz et
            policy_info = self.db._extract_policy_info_with_llm(base_name)
            
            if not policy_info:
                return {
                    'is_endorsement': False,
                    'document_type': 'UNKNOWN',
                    'confidence': 'LOW',
                    'details': {'error': 'LLM analizi başarısız'}
                }
            
            doc_type = policy_info.get('document_type', 'UNKNOWN')
            is_endorsement = (doc_type == 'ENDORSEMENT')
            
            # Güven seviyesi belirleme
            if 'zeyl' in file_name.lower() or 'ek' in file_name.lower():
                confidence = 'HIGH' if is_endorsement else 'MEDIUM'
            else:
                confidence = 'MEDIUM' if is_endorsement else 'LOW'
            
            return {
                'is_endorsement': is_endorsement,
                'document_type': doc_type,
                'confidence': confidence,
                'details': policy_info
            }
            
        except Exception as e:
            print(f"   ❌ LLM analiz hatası: {e}")
            return {
                'is_endorsement': False,
                'document_type': 'ERROR',
                'confidence': 'LOW',
                'details': {'error': str(e)}
            }
    
    def create_zeyilname_folder(self, source_directory: str) -> str:
        """
        ZEYILNAME klasörünü oluşturur
        
        Args:
            source_directory: Ana dizin
            
        Returns:
            str: ZEYILNAME klasörünün tam yolu
        """
        zeyilname_folder = os.path.join(source_directory, 'ZEYILNAME')
        
        if not os.path.exists(zeyilname_folder):
            os.makedirs(zeyilname_folder)
            print(f"📁 ZEYILNAME klasörü oluşturuldu: {zeyilname_folder}")
        else:
            print(f"📁 ZEYILNAME klasörü mevcut: {zeyilname_folder}")
            
        return zeyilname_folder
    
    def move_file_safely(self, source_path: str, destination_folder: str) -> bool:
        """
        Dosyayı güvenli bir şekilde taşır
        
        Args:
            source_path: Kaynak dosya yolu
            destination_folder: Hedef klasör
            
        Returns:
            bool: Taşıma işlemi başarılı mı
        """
        try:
            file_name = os.path.basename(source_path)
            destination_path = os.path.join(destination_folder, file_name)
            
            # Eğer aynı isimde dosya varsa, numaralandır
            counter = 1
            original_destination = destination_path
            while os.path.exists(destination_path):
                name, ext = os.path.splitext(file_name)
                new_name = f"{name}_{counter}{ext}"
                destination_path = os.path.join(destination_folder, new_name)
                counter += 1
                
                if counter > 100:  # Sonsuz döngü koruması
                    print(f"   ⚠️ Çok fazla aynı isimli dosya: {file_name}")
                    return False
            
            # Dosyayı taşı
            shutil.move(source_path, destination_path)
            
            if destination_path != original_destination:
                print(f"   📦 Taşındı (yeniden adlandırıldı): {file_name} -> {os.path.basename(destination_path)}")
            else:
                print(f"   📦 Taşındı: {file_name}")
                
            return True
            
        except Exception as e:
            print(f"   ❌ Taşıma hatası: {e}")
            return False
    
    def scan_and_organize(self, source_directory: str, dry_run: bool = False, file_extensions: list = None):
        """
        Klasörü tarar ve zeyilname belgelerini organize eder
        
        Args:
            source_directory: Taranacak klasör
            dry_run: Sadece analiz yap, taşıma yapma
            file_extensions: İzin verilen dosya uzantıları (None = hepsi)
        """
        if not os.path.exists(source_directory):
            print(f"❌ Klasör bulunamadı: {source_directory}")
            return
        
        print(f"📂 Klasör taranıyor: {source_directory}")
        print(f"🧪 Test modu: {'AÇIK' if dry_run else 'KAPALI'}")
        print("-" * 80)
        
        # Varsayılan dosya uzantıları
        if file_extensions is None:
            file_extensions = ['.pdf', '.docx', '.doc', '.txt', '.jpg', '.jpeg', '.png']
        
        # ZEYILNAME klasörünü oluştur (test modunda değilse)
        zeyilname_folder = None
        if not dry_run:
            zeyilname_folder = self.create_zeyilname_folder(source_directory)
        
        # Dosyaları tara
        found_files = []
        for root, dirs, files in os.walk(source_directory):
            # ZEYILNAME klasörünü atla
            if 'ZEYILNAME' in root:
                continue
                
            for file in files:
                file_path = os.path.join(root, file)
                file_ext = os.path.splitext(file)[1].lower()
                
                # Dosya uzantısını kontrol et
                if file_ext in file_extensions:
                    found_files.append(file_path)
        
        if not found_files:
            print("📄 Analiz edilecek dosya bulunamadı")
            return
        
        print(f"📄 {len(found_files)} dosya bulundu, analiz başlıyor...\n")
        
        # Analiz sonuçları
        results = {
            'total_files': len(found_files),
            'endorsements_found': 0,
            'successfully_moved': 0,
            'errors': 0,
            'details': []
        }
        
        # Her dosyayı analiz et
        for i, file_path in enumerate(found_files, 1):
            file_name = os.path.basename(file_path)
            print(f"[{i}/{len(found_files)}] 📄 {file_name}")
            
            try:
                # LLM ile analiz et
                analysis = self.is_endorsement_document(file_path)
                
                result_detail = {
                    'file_name': file_name,
                    'file_path': file_path,
                    'is_endorsement': analysis['is_endorsement'],
                    'document_type': analysis['document_type'],
                    'confidence': analysis['confidence'],
                    'moved': False,
                    'error': None
                }
                
                # Sonuçları göster
                if analysis['is_endorsement']:
                    results['endorsements_found'] += 1
                    print(f"   ✅ ZEYILNAME tespit edildi!")
                    print(f"   📋 Belge türü: {analysis['document_type']}")
                    print(f"   🎯 Güven: {analysis['confidence']}")
                    
                    if 'customer_name' in analysis['details']:
                        print(f"   👤 Müşteri: {analysis['details']['customer_name']}")
                    
                    # Dosyayı taşı (test modunda değilse)
                    if not dry_run:
                        if self.move_file_safely(file_path, zeyilname_folder):
                            results['successfully_moved'] += 1
                            result_detail['moved'] = True
                        else:
                            results['errors'] += 1
                            result_detail['error'] = 'Taşıma başarısız'
                    else:
                        print(f"   🧪 TEST MODU: Taşınacaktı -> ZEYILNAME/")
                        
                else:
                    print(f"   ⚪ Normal belge: {analysis['document_type']}")
                    
                results['details'].append(result_detail)
                    
            except Exception as e:
                print(f"   ❌ Analiz hatası: {e}")
                results['errors'] += 1
                results['details'].append({
                    'file_name': file_name,
                    'file_path': file_path,
                    'is_endorsement': False,
                    'document_type': 'ERROR',
                    'confidence': 'LOW',
                    'moved': False,
                    'error': str(e)
                })
            
            print()  # Boş satır
        
        # Özet raporu
        self.print_summary_report(results, dry_run)
    
    def print_summary_report(self, results: dict, dry_run: bool):
        """
        Özet raporu yazdırır
        """
        print("=" * 80)
        print("📊 ÖZET RAPORU")
        print("=" * 80)
        print(f"📁 Toplam dosya: {results['total_files']}")
        print(f"📋 Zeyilname tespit edilen: {results['endorsements_found']}")
        
        if not dry_run:
            print(f"📦 Başarıyla taşınan: {results['successfully_moved']}")
        else:
            print(f"🧪 Taşınacak dosya (test): {results['endorsements_found']}")
            
        print(f"❌ Hata olan: {results['errors']}")
        
        # Detaylı rapor
        if results['endorsements_found'] > 0:
            print(f"\n📋 Tespit edilen zeyilname belgeleri:")
            for detail in results['details']:
                if detail['is_endorsement']:
                    status = "✅ Taşındı" if detail['moved'] else ("🧪 Test" if dry_run else "❌ Hata")
                    print(f"   {status} - {detail['file_name']} ({detail['confidence']} güven)")

def main():
    """
    Ana fonksiyon - komut satırı argümanlarını işler
    """
    import argparse
    
    parser = argparse.ArgumentParser(description='LLM ile zeyilname belgelerini organize et')
    parser.add_argument('source_directory', help='Taranacak klasör yolu')
    parser.add_argument('--dry-run', action='store_true', help='Sadece analiz yap, taşıma yapma')
    parser.add_argument('--extensions', nargs='+', default=['.pdf', '.docx', '.doc'], 
                       help='İzin verilen dosya uzantıları (örn: --extensions .pdf .docx)')
    
    args = parser.parse_args()
    
    # Klasör varlığını kontrol et
    if not os.path.exists(args.source_directory):
        print(f"❌ Klasör bulunamadı: {args.source_directory}")
        sys.exit(1)
    
    # Organizer'ı başlat ve çalıştır
    try:
        organizer = ZeyilnameOrganizer()
        organizer.scan_and_organize(
            source_directory=args.source_directory,
            dry_run=args.dry_run,
            file_extensions=args.extensions
        )
        
    except KeyboardInterrupt:
        print("\n⏹️ İşlem kullanıcı tarafından durduruldu")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Beklenmeyen hata: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
