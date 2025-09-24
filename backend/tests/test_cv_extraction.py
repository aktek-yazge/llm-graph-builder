#!/usr/bin/env python3
"""
CV Bilgi Çıkarma Test Scripti

Bu script extract_cv_info_from_localfile fonksiyonunu test eder.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Backend dizinine path ekle
backend_dir = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(backend_dir / "src"))

from src.graphDB_dataAccess import graphDBdataAccess
from langchain_neo4j import Neo4jGraph

async def test_cv_extraction():
    """CV extraction fonksiyonunu test eder"""

    # Örnek CV dosyası yolu (gerçek bir CV PDF'i koyun)
    cv_file_path = "/workspace/test_cv.pdf"  # Bu dosyayı oluşturun veya mevcut bir CV kullanın

    # Neo4j bağlantısı (test için gerekli)
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    try:
        # Graph bağlantısı oluştur
        graph = Neo4jGraph(
            url=uri,
            username=username,
            password=password,
            database=database
        )

        # graphDBdataAccess instance oluştur
        graph_access = graphDBdataAccess(graph)

        # CV extraction fonksiyonunu çağır
        print(f"🔍 CV extraction başlıyor: {cv_file_path}")

        cv_info = await graph_access.extract_cv_info_from_localfile(
            file_path=cv_file_path,
            model_name="openai_gpt_4o_mini"
        )

        # Sonuçları göster
        print("✅ CV Bilgileri Çıkarıldı:")
        print(json.dumps(cv_info, indent=2, ensure_ascii=False))

        return cv_info

    except Exception as e:
        print(f"❌ Hata: {e}")
        return None

def create_sample_cv_text():
    """Örnek CV metni oluştur (test için)"""
    sample_cv = """
    MEHMET EMİN DOĞAN

    Yazılım Mühendisi
    İstanbul, Türkiye
    mehmet.dogan@email.com
    +90 555 123 45 67
    linkedin.com/in/mehmetemin
    github.com/mehmetemin

    PROFESYONEL ÖZET

    5+ yıllık deneyimli full-stack geliştirici. Python, JavaScript ve cloud teknolojilerinde uzman.
    Mikroservis mimarisi ve DevOps pratiklerinde deneyimli. Agile metodolojileriyle çalışmış.

    İŞ DENEYİMİ

    Senior Software Engineer
    TechCorp Inc., İstanbul
    Ocak 2022 - Günümüz

    - Mikroservis tabanlı e-ticaret platformu geliştirme
    - Python FastAPI ile REST API'ler geliştirme
    - Docker ve Kubernetes ile container orchestration
    - AWS cloud servisleri ile scalable çözümler

    Software Developer
    StartupXYZ, İstanbul
    Haziran 2019 - Aralık 2021

    - React.js ile frontend uygulamaları geliştirme
    - Node.js backend servisleri geliştirme
    - MongoDB veritabanı tasarımı
    - CI/CD pipeline kurulumu

    EĞİTİM

    Bilgisayar Mühendisliği
    İstanbul Teknik Üniversitesi
    2015 - 2019

    YETKİNLİKLER

    - Python, JavaScript, TypeScript
    - React, Vue.js, Node.js
    - Docker, Kubernetes, AWS
    - PostgreSQL, MongoDB, Redis
    - Git, Jenkins, GitLab CI

    DİLLER

    - Türkçe (Anadil)
    - İngilizce (İleri seviye)

    SERTİFİKALAR

    - AWS Certified Solutions Architect
    - Google Cloud Professional Developer
    """

    return sample_cv

if __name__ == "__main__":
    print("🚀 CV Extraction Test Scripti")
    print("=" * 50)

    # Test CV metnini göster
    sample_cv = create_sample_cv_text()
    print("📄 Örnek CV Metni:")
    print("-" * 30)
    print(sample_cv[:500] + "...")
    print()

    # Asenkron fonksiyonu çalıştır
    cv_info = asyncio.run(test_cv_extraction())

    if cv_info:
        print("\n📊 Extraction İstatistikleri:")
        print(f"İsim: {cv_info.get('full_name', 'N/A')}")
        print(f"Deneyim Yılı: {cv_info.get('experience_years', 'N/A')}")
        print(f"Beceri Sayısı: {len(cv_info.get('skills', []))}")
        print(f"İş Deneyimi: {len(cv_info.get('experience', []))}")
        print(f"Eğitim: {len(cv_info.get('education', []))}")
    else:
        print("❌ CV extraction başarısız oldu")