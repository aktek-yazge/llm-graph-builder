#!/usr/bin/env python3
"""
Test script for domain-agnostic job posting CV matching
Hibrit şema ile tamamen LLM-driven, şema-agnostic yaklaşım testi
"""

import sys
import os
import json
import logging
from typing import Dict, Any

# Path ayarla
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from src.intelligent_agent import IntelligentAgent
from langchain_neo4j import Neo4jGraph

# Logging ayarla
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_job_posting_cv_matching():
    """
    Domain-agnostic iş ilanı CV eşleştirme testi
    """
    
    # Neo4j bağlantısı
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
    )
    
    # Agent oluştur
    agent = IntelligentAgent(graph, model_name="openai_gpt_4.1")
    
    print("🎯 Domain-Agnostic İş İlanı CV Eşleştirme Testi Başlıyor...")
    print("="*70)
    
    # Test iş ilanı metni (domain-agnostic)
    job_posting = """
    YAZILIM GELİŞTİRİCİ ARAMASI
    
    Şirketimiz için yazılım geliştirme ekibimize katılacak deneyimli bir yazılım geliştiricisi arıyoruz.
    
    ARANAN NİTELİKLER:
    - En az 3 yıl yazılım geliştirme deneyimi
    - Python programlama dili konusunda uzmanlık
    - Web geliştirme deneyimi (Django, Flask)
    - Veri analizi ve makine öğrenmesi konularında bilgi
    - Ekip çalışmasına yatkınlık
    - İngilizce dil bilgisi (en az orta seviye)
    - Problem çözme yetenekleri
    
    ARTILARI:
    - React.js deneyimi
    - Docker ve Kubernetes bilgisi
    - Agile/Scrum metodolojilerinde deneyim
    - Lisans veya yüksek lisans derecesi
    
    ÇALIŞMA KOŞULLARI:
    - Tam zamanlı pozisyon
    - İstanbul lokasyonu
    - Rekabetçi maaş
    - Esnek çalışma saatleri
    
    İlgileniyorsanız CV'nizi gönderin.
    """
    
    print("📋 TEST İŞ İLANI:")
    print("-" * 50)
    print(job_posting)
    print("="*70)
    
    # 1. LLM-based iş ilanı tespiti testi
    print("\n🔍 1. İş İlanı Tespit Testi:")
    detection_result = agent._detect_job_posting_with_llm(job_posting)
    print(f"Tespit Sonucu: {json.dumps(detection_result, ensure_ascii=False, indent=2)}")
    
    if not detection_result["is_job_posting"]:
        print("❌ İş ilanı tespit edilemedi!")
        return
    
    # 2. Schema-driven CV eşleştirme testi
    print("\n🎯 2. Schema-Driven CV Eşleştirme Testi:")
    matching_result = agent.match_job_posting_to_cvs(job_posting, min_match_score=0.2)
    
    if matching_result["success"]:
        print("✅ Eşleştirme başarılı!")
        print(f"📊 Schema Analizi:")
        print(json.dumps(matching_result["schema_analysis"], ensure_ascii=False, indent=2))
        
        print(f"\n👥 Bulunan Adaylar: {matching_result['total_matches']}")
        for i, candidate in enumerate(matching_result["matched_candidates"][:5], 1):
            print(f"\n{i}. {candidate['p.name']} (%{int(candidate['match_score']*100)} uyumlu)")
            print(f"   Pozisyon: {candidate.get('p.career_current_position', 'N/A')}")
            print(f"   Deneyim: {candidate.get('p.career_experience_years', 'N/A')} yıl")
            print(f"   Beceriler: {candidate.get('skills', [])[:3]}")
            print(f"   İletişim: {candidate.get('p.contact_email', 'N/A')}")
    else:
        print("❌ Eşleştirme başarısız!")
        print(f"Hata: {matching_result.get('error', 'Bilinmeyen hata')}")
    
    # 3. Agent solve_question ile tam entegrasyon testi
    print("\n🤖 3. Agent Entegrasyon Testi:")
    agent_response = agent.solve_question(job_posting, session_id="test_session")
    
    print("Agent Yanıtı:")
    print("-" * 50)
    print(agent_response.get("answer", "Yanıt bulunamadı"))
    
    if agent_response.get("match_type") == "job_posting_cv_matching":
        print("\n✅ Agent otomatik olarak iş ilanı tespit etti ve CV eşleştirme yaptı!")
        print(f"Eşleşen aday sayısı: {len(agent_response.get('matched_candidates', []))}")
    else:
        print("\n⚠️ Agent normal sorgu işlemi yaptı (iş ilanı tespit edilmemiş olabilir)")

def test_hybrid_schema_queries():
    """
    Hibrit şema sorgu pattern'larını test et
    """
    print("\n" + "="*70)
    print("🏗️ HİBRİT ŞEMA SORGU PATTERN TESTLERİ")
    print("="*70)
    
    # Neo4j bağlantısı
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
    )
    
    # Agent oluştur
    agent = IntelligentAgent(graph, model_name="openai_gpt_4.1")
    
    # Test sorguları
    test_queries = [
        {
            "name": "Core Person Properties",
            "query": """
            MATCH (p:Person)
            RETURN p.name, p.career_current_position, p.career_experience_years, p.profile_location
            LIMIT 3
            """
        },
        {
            "name": "Generic Entity Types Discovery",
            "query": """
            MATCH (e:Entity)
            RETURN DISTINCT e.type, count(*) as count
            ORDER BY count DESC
            LIMIT 10
            """
        },
        {
            "name": "Skill Entities",
            "query": """
            MATCH (p:Person)-[:HAS_ATTRIBUTE]-(e:Entity {type: "Skill"})
            RETURN p.name, collect(e.name) as skills
            LIMIT 3
            """
        },
        {
            "name": "Organization Connections",
            "query": """
            MATCH (p:Person)-[:CONNECTED_TO]-(e:Entity {type: "Organization"})
            RETURN p.name, collect(e.name) as organizations
            LIMIT 3
            """
        },
        {
            "name": "Hybrid Multi-Criteria",
            "query": """
            MATCH (p:Person)
            OPTIONAL MATCH (p)-[:HAS_ATTRIBUTE]-(skill:Entity {type: "Skill"})
            OPTIONAL MATCH (p)-[:CONNECTED_TO]-(org:Entity {type: "Organization"})
            OPTIONAL MATCH (p)-[:HAS_ATTRIBUTE]-(lang:Entity {type: "Language"})
            RETURN p.name, p.career_current_position, 
                   collect(DISTINCT skill.name)[0..3] as top_skills,
                   collect(DISTINCT org.name)[0..2] as companies,
                   collect(DISTINCT lang.name) as languages
            LIMIT 2
            """
        }
    ]
    
    for test_query in test_queries:
        print(f"\n📋 {test_query['name']}:")
        print("-" * 40)
        try:
            success, results = agent.execute_cypher_query(test_query['query'])
            if success and results:
                print(f"✅ Sonuç sayısı: {len(results)}")
                for i, result in enumerate(results[:2], 1):
                    print(f"  {i}. {result}")
            else:
                print("❌ Sonuç bulunamadı")
        except Exception as e:
            print(f"❌ Hata: {e}")

if __name__ == "__main__":
    print("🚀 DOMAIN-AGNOSTIC HİBRİT ŞEMA TEST SÜİTİ")
    print("LLM-based, Schema-driven Job Posting CV Matching Tests")
    print("=" * 70)
    
    try:
        # Ana test
        test_job_posting_cv_matching()
        
        # Hibrit şema pattern testleri
        test_hybrid_schema_queries()
        
        print("\n" + "="*70)
        print("✅ TÜM TESTLER TAMAMLANDI!")
        print("🎯 Hibrit şema domain-agnostic yaklaşımı başarıyla test edildi")
        
    except Exception as e:
        print(f"\n❌ TEST HATASI: {e}")
        import traceback
        traceback.print_exc()