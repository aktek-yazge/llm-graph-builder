#!/usr/bin/env python3
"""
CV Node Oluşturma Test Scripti

Bu script create_cv_node_from_document fonksiyonunu test eder.
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

async def test_cv_node_creation():
    """CV node oluşturma fonksiyonunu test eder"""

    # Örnek CV dosyası adı (merged_files klasöründe olmalı)
    cv_file_name = "sample_cv.pdf"  # Bu dosyanın merged_files klasöründe olması gerekiyor

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

        print(f"🔍 CV node oluşturma başlıyor: {cv_file_name}")

        # CV node oluşturma fonksiyonunu çağır
        graph_access.create_cv_node_from_document(cv_file_name)

        print("✅ CV node oluşturma tamamlandı")

        # Oluşturulan node'ları kontrol et
        check_query = """
        MATCH (p:Person)-[:HAS_CV]->(d:Document {fileName: $file_name})
        RETURN p.name as person_name, p.fullName as full_name,
               p.email as email, p.currentPosition as position,
               count{(p)-[:HAS_EXPERIENCE]->()} as experience_count,
               count{(p)-[:HAS_EDUCATION]->()} as education_count,
               count{(p)-[:HAS_SKILL]->()} as skill_count
        """

        result = graph_access.execute_query(check_query, {"file_name": cv_file_name})

        if result:
            person = result[0]
            print("\n📊 Oluşturulan CV Node Bilgileri:")
            print(f"İsim: {person['person_name']}")
            print(f"Full Name: {person['full_name']}")
            print(f"Email: {person['email']}")
            print(f"Pozisyon: {person['position']}")
            print(f"Deneyim Sayısı: {person['experience_count']}")
            print(f"Eğitim Sayısı: {person['education_count']}")
            print(f"Beceri Sayısı: {person['skill_count']}")
        else:
            print("❌ CV node bulunamadı")

        return True

    except Exception as e:
        print(f"❌ Hata: {e}")
        return False

def create_sample_cv_data():
    """Test için örnek CV verisi oluştur"""
    sample_data = {
        "full_name": "Ahmet Yılmaz",
        "email": "ahmet.yilmaz@email.com",
        "phone": "+90 555 123 45 67",
        "location": "İstanbul, Türkiye",
        "summary": "5+ yıllık deneyimli yazılım geliştirici",
        "current_position": "Senior Software Developer",
        "current_company": "TechCorp Inc.",
        "experience_years": 5,
        "experience": [
            {
                "company": "TechCorp Inc.",
                "position": "Senior Software Developer",
                "duration": "2022-Günümüz",
                "description": "Full-stack development"
            }
        ],
        "education": [
            {
                "institution": "İstanbul Teknik Üniversitesi",
                "degree": "Bilgisayar Mühendisliği",
                "field": "Bilgisayar Mühendisliği",
                "graduation_year": "2019"
            }
        ],
        "skills": ["Python", "JavaScript", "React", "Node.js"],
        "languages": ["Türkçe (Anadil)", "İngilizce (İleri)"],
        "certifications": ["AWS Certified Developer"],
        "projects": [
            {
                "name": "E-commerce Platform",
                "description": "Modern e-commerce uygulaması",
                "technologies": ["React", "Node.js", "MongoDB"]
            }
        ]
    }

    return sample_data

if __name__ == "__main__":
    print("🚀 CV Node Oluşturma Test Scripti")
    print("=" * 50)

    # Test CV verisini göster
    sample_data = create_sample_cv_data()
    print("📄 Örnek CV Verisi:")
    print("-" * 30)
    print(json.dumps(sample_data, indent=2, ensure_ascii=False)[:500] + "...")
    print()

    # Asenkron fonksiyonu çalıştır
    success = asyncio.run(test_cv_node_creation())

    if success:
        print("\n✅ Test başarılı!")
    else:
        print("\n❌ Test başarısız oldu")