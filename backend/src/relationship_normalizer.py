# -*- coding: utf-8 -*-
"""
Relationship Normalizer - LLM destekli relationship type normalizasyonu

Akış:
1. Neo4j'den tüm relationship type'larını çek
2. LLM'e gönder - benzer olanları grupla, en uygun ismi seç
3. Kullanıcı onayı
4. Neo4j'de uygula
"""

import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NormalizationGroup:
    """Bir normalizasyon grubunu temsil eder"""
    original_types: List[str]  # Orijinal relationship type'ları
    suggested_name: str  # LLM'in önerdiği standart isim
    count: int  # Toplam relationship sayısı


def get_all_relationship_types(graph) -> List[Dict]:
    """
    Neo4j'den tüm relationship type'larını ve sayılarını çeker (APOC ile hızlı)
    
    Returns:
        [{"type": "IS_YACHT_POLICY", "count": 23}, ...]
    """
    import time
    start_time = time.time()
    
    # 1. APOC meta.stats() ile dene (en hızlı)
    try:
        result = graph.query("""
            CALL apoc.meta.stats() YIELD relTypesCount
            UNWIND keys(relTypesCount) as type
            RETURN type, relTypesCount[type] as cnt
            ORDER BY cnt DESC
        """)
        elapsed = time.time() - start_time
        logger.info(f"⚡ APOC meta.stats(): {len(result)} type, {elapsed:.2f}s")
        return [{"type": r["type"], "count": r["cnt"]} for r in result]
    except Exception as e:
        logger.warning(f"APOC meta.stats() başarısız: {e}")
    
    # 2. APOC meta.schema() ile dene
    try:
        result = graph.query("CALL apoc.meta.schema() YIELD value RETURN value")
        if result:
            schema_data = result[0]["value"]
            rel_types = {}
            for name, info in schema_data.items():
                if info.get("type") == "node":
                    for rel_name, rel_info in info.get("relationships", {}).items():
                        if rel_name not in rel_types:
                            rel_types[rel_name] = rel_info.get("count", 0)
            
            elapsed = time.time() - start_time
            logger.info(f"⚡ APOC meta.schema(): {len(rel_types)} type, {elapsed:.2f}s")
            return [{"type": t, "count": c} for t, c in sorted(rel_types.items(), key=lambda x: -x[1])]
    except Exception as e:
        logger.warning(f"APOC meta.schema() başarısız: {e}")
    
    # 3. Fallback - eski yavaş sorgu
    try:
        result = graph.query("""
    CALL db.relationshipTypes() YIELD relationshipType as type
    CALL {
        WITH type
        MATCH ()-[r]->() WHERE type(r) = type
        RETURN count(r) as cnt
    }
    RETURN type, cnt
    ORDER BY cnt DESC
        """)
        elapsed = time.time() - start_time
        logger.info(f"🐢 Fallback sorgu: {len(result)} type, {elapsed:.2f}s")
        return [{"type": r["type"], "count": r["cnt"]} for r in result]
    except Exception as e:
        logger.warning(f"Count sorgusu başarısız: {e}")
        result = graph.query("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType as type")
        return [{"type": r["type"], "count": 0} for r in result]


def analyze_with_llm(relationship_types: List[Dict], llm) -> List[NormalizationGroup]:
    """
    LLM ile relationship type'larını analiz et ve grupla
    
    Args:
        relationship_types: [{"type": "...", "count": N}, ...]
        llm: LangChain LLM instance
        
    Returns:
        List of NormalizationGroup
    """
    # Sistem ilişkilerini filtrele - SADECE IS_*_POLICY olanları analiz et
    SYSTEM_RELATIONSHIPS = {
        "PART_OF", "NEXT_CHUNK", "FIRST_CHUNK",
        "HAS_CLAUSE", "HAS_COVERAGE", "HAS_PREMIUM", "HAS_GUARANTEE",
        "HAS_POLICYHOLDER", "HAS_INSURED_PERSON", "HAS_INSURED_PROPERTY",
        "HAS_ADDRESS", "HAS_PAYMENT", "HAS_START_DATE", "HAS_END_DATE",
        "ISSUED_BY", "DOCUMENTED_IN", "APPLIED_TO",
        "FIRST_ENDORSEMENT", "NEXT_ENDORSEMENT", "HAS_POLICY"
    }
    
    # Sadece IS_*_POLICY formatındaki type'ları al
    policy_types = [
        rt for rt in relationship_types 
        if rt["type"].startswith("IS_") and rt["type"].endswith("_POLICY")
        and rt["type"] not in SYSTEM_RELATIONSHIPS
    ]
    
    logger.info(f"📋 {len(relationship_types)} type'tan {len(policy_types)} IS_*_POLICY type analiz edilecek")
    
    if not policy_types:
        logger.info("✅ Normalize edilecek IS_*_POLICY type bulunamadı")
        return []
    
    # Sadece type isimlerini al
    type_names = [rt["type"] for rt in policy_types]
    type_counts = {rt["type"]: rt["count"] for rt in policy_types}
    
    prompt = f"""Neo4j veritabanındaki relationship type'ları aşağıda. 
Bunların çoğu AYNI ŞEYİ ifade ediyor ama farklı yazılmış (OCR hataları, Türkçe karakter bozulmaları, farklı yazımlar).

ÖNEMLİ ÖRNEKLER - bunlar AYNI gruba girmeli:
- IS_YACHT_POLICY, IS_YAT_POLICY, IS_YAT_SIGORTA_POLICY, IS_YAT_SIGORTASI_POLICY → hepsi "YAT" sigortası
- IS_KONUT_POLICY, IS_KONUT_SIGORTASI_POLICY, IS_KONUT_PAKET_POLICY → hepsi "KONUT" sigortası
- IS_TEKNE_POLICY, IS_TEKNE_YAT_SIGORTA_POLICY, IS_TEKNE_YAT_S_GORTA_POL__ES__POLICY → hepsi "TEKNE" sigortası
- IS_INSAAT_POLICY, IS_INSAAT_ALL_RISKS_POLICY, IS__N_AAT_B_T_N_R_SKLER_S_GORTA_POL__ES__POLICY → hepsi "INSAAT" sigortası

OCR BOZUKLUKLARINI ÇÖZME REHBERİ:
- S_GORTA, S__GORTA, S_GORTA_ → SİGORTA
- POL__ES__, POL_CES__, POLI_ESI → POLİÇESİ (kaldır, sadece POLICY kullan)
- ___ (çift/üçlü alt çizgi) → tek alt çizgi veya kaldır
- ARA_LAR → ARAÇLAR (Ç karakteri kayıp)
- __YER_M_ → İŞYERİ (İŞ karakterleri kayıp)
- TIBB__K_T__ → TIBBİ KÖTÜ
- EMT_A → EMTİA
- NAKL_YAT → NAKLİYAT
- FAAL_YET → FAALİYET
- MAL__SORUMLULUK → MALİ SORUMLULUK

AĞIR OCR BOZUKLUKLARI İÇİN ÖRNEKLER:
- IS_MOTORLU_ARA_LAR_MESLEK__FAAL_YET_S_GORTA_POL__ES__POLICY → IS_MOTORLU_ARACLAR_MESLEKI_SORUMLULUK_POLICY
- IS___YER_M_S_GORTA_POL__ES__POLICY → IS_ISYERI_POLICY
- IS_TIBB__K_T__UYGULAMAYA__L__K_N_ZORUNLU_MAL__SORUMLULUK_S_GORTA_POL_CES__POLICY → IS_TIBBI_MALPRAKTIS_ZORUNLU_MALI_SORUMLULUK_POLICY
- IS_EMT_A_NAKL_YAT_S_GORTA_POL__ES__POLICY → IS_EMTIA_NAKLIYAT_POLICY veya IS_NAKLIYAT_EMTEA_POLICY grubuna ekle

Görevin:
1. SADECE "IS_*_POLICY" formatındaki relationship type'larını analiz et
2. Aynı sigorta türünü ifade eden IS_*_POLICY type'larını grupla
3. Her grup için EN KISA ve EN TEMİZ ismi seç (örn: IS_YAT_POLICY)
4. OCR bozukluklarını çöz ve benzer olanlarla grupla

⚠️ DOKUNMA - Bu relationship type'larını ASLA gruplama/değiştirme:
- PART_OF, NEXT_CHUNK, FIRST_CHUNK (Chunk ilişkileri)
- HAS_CLAUSE, HAS_COVERAGE, HAS_PREMIUM, HAS_GUARANTEE (Policy detayları)
- HAS_POLICYHOLDER, HAS_INSURED_PERSON, HAS_INSURED_PROPERTY (Sigortalı bilgileri)
- HAS_ADDRESS, HAS_PAYMENT, HAS_START_DATE, HAS_END_DATE (Tarih/Adres/Ödeme)
- ISSUED_BY, DOCUMENTED_IN, APPLIED_TO (Bağlantı ilişkileri)
- FIRST_ENDORSEMENT, NEXT_ENDORSEMENT (Zeyilname ilişkileri)
- HAS_POLICY (Genel policy bağlantısı)

Kurallar:
- SADECE "IS_" ile başlayan ve "_POLICY" ile biten type'ları grupla
- Standart isim: IS_{{KONU}}_POLICY formatında olmalı
- Türkçe karakterler ASCII: ş→s, ö→o, ü→u, ı→i, ğ→g, ç→c, İ→I
- OCR bozuklukları düzelt: S_GORTA→SIGORTA, POL__ES__→POLICESI, __→_, vb.
- YACHT = YAT, INSURANCE = SIGORTA gibi eşleştirmeler yap

Relationship type'ları:
{json.dumps(type_names, indent=2, ensure_ascii=False)}

JSON formatında yanıt ver:
{{
  "groups": [
    {{
      "suggested_name": "IS_YAT_POLICY",
      "original_types": ["IS_YACHT_POLICY", "IS_YAT_POLICY", "IS_YAT_SIGORTA_POLICY", ...]
    }},
    ...
  ]
}}

Sadece JSON döndür, açıklama yapma. Mümkün olduğunca çok birleştirme yap!"""

    try:
        response = llm.invoke(prompt)
        
        # Response'u parse et
        content = response.content if hasattr(response, 'content') else str(response)
        
        # Debug: LLM raw output
        logger.info(f"🤖 LLM Response length: {len(content)} chars")
        
        # JSON bloğunu çıkar
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content.strip())
        
        # NormalizationGroup'lara dönüştür
        groups = []
        for g in data.get("groups", []):
            original_types = g.get("original_types", [])
            total_count = sum(type_counts.get(t, 0) for t in original_types)
            
            groups.append(NormalizationGroup(
                original_types=original_types,
                suggested_name=g.get("suggested_name", original_types[0] if original_types else "UNKNOWN"),
                count=total_count
            ))
        
        return groups
        
    except Exception as e:
        logger.error(f"LLM analiz hatası: {e}")
        raise


def preview_normalization(graph, llm) -> Dict:
    """
    Normalizasyon önizlemesi oluştur
    
    Returns:
        {
            "groups": [
                {
                    "suggested_name": "IS_YACHT_POLICY",
                    "original_types": ["IS_YACHT_POLICY", "IS_YAT_POLICY", ...],
                    "count": 46,
                    "needs_change": True  # Birden fazla type varsa
                },
                ...
            ],
            "total_types": 112,
            "types_to_merge": 85,
            "groups_after": 27
        }
    """
    # 1. Tüm relationship type'larını al
    rel_types = get_all_relationship_types(graph)
    logger.info(f"📋 {len(rel_types)} relationship type bulundu")
    
    # 2. LLM ile analiz et
    groups = analyze_with_llm(rel_types, llm)
    logger.info(f"📋 LLM {len(groups)} grup oluşturdu")
    
    # 3. Preview formatına dönüştür
    preview_groups = []
    types_to_merge = 0
    
    for g in groups:
        needs_change = len(g.original_types) > 1 or g.original_types[0] != g.suggested_name
        if needs_change:
            types_to_merge += len(g.original_types)
        
        preview_groups.append({
            "suggested_name": g.suggested_name,
            "original_types": g.original_types,
            "count": g.count,
            "needs_change": needs_change
        })
    
    # Değişiklik gerekenleri öne al
    preview_groups.sort(key=lambda x: (not x["needs_change"], -len(x["original_types"])))
    
    return {
        "groups": preview_groups,
        "total_types": len(rel_types),
        "types_to_merge": types_to_merge,
        "groups_after": len(groups)
    }


def apply_normalization(graph, groups: List[Dict]) -> Dict:
    """
    Normalizasyonu Neo4j'de uygula
    
    Args:
        graph: Neo4j graph connection
        groups: [{"suggested_name": "...", "original_types": [...], "needs_change": True}, ...]
        
    Returns:
        {"success": True, "changes": [...], "errors": [...]}
    """
    changes = []
    errors = []
    
    for group in groups:
        if not group.get("needs_change", False):
            continue
            
        suggested_name = group["suggested_name"]
        original_types = group["original_types"]
        
        for original_type in original_types:
            if original_type == suggested_name:
                continue  # Zaten doğru isimde
            
            try:
                # Relationship'leri yeni tipe dönüştür
                # Not: Neo4j'de relationship type değiştirmek için:
                # 1. Yeni relationship oluştur
                # 2. Eski relationship'i sil
                
                query = f"""
                MATCH (a)-[r:`{original_type}`]->(b)
                CALL {{
                    WITH a, r, b
                    CREATE (a)-[new:`{suggested_name}`]->(b)
                    SET new = properties(r)
                    DELETE r
                    RETURN count(*) as cnt
                }}
                RETURN sum(cnt) as total_changed
                """
                
                result = graph.query(query)
                count = result[0]["total_changed"] if result else 0
                
                changes.append({
                    "from": original_type,
                    "to": suggested_name,
                    "count": count
                })
                
                logger.info(f"✅ {original_type} → {suggested_name}: {count} relationship")
                
            except Exception as e:
                error_msg = f"{original_type} → {suggested_name}: {str(e)}"
                errors.append(error_msg)
                logger.error(f"❌ {error_msg}")
    
    return {
        "success": len(errors) == 0,
        "changes": changes,
        "errors": errors,
        "total_changed": sum(c["count"] for c in changes)
    }


# Test fonksiyonu
if __name__ == "__main__":
    import sys
    sys.path.insert(0, 'src')
    
    from dotenv import load_dotenv
    load_dotenv('.preview.env')
    
    from langchain_community.graphs import Neo4jGraph
    import os
    
    # Neo4j bağlantısı
    graph = Neo4jGraph(
        url='bolt://3.76.55.209:7688',
        username='neo4j',
        password='qwerty5555',
        database='neo4j'
    )
    
    # LLM - get_llm kullan (Gemini veya OpenAI)
    from src.llm import get_llm
    
    llm = None
    try:
        llm, _ = get_llm("gemini-2.5-flash")
        print("🤖 Gemini 2.5 Flash kullanılıyor")
    except Exception as e:
        print(f"⚠️ Gemini başarısız: {e}")
    
    if llm is None:
        try:
            llm, _ = get_llm("openai-gpt-4o-mini")
            print("🤖 GPT-4o-mini kullanılıyor (fallback)")
        except Exception as e2:
            print(f"⚠️ GPT-4o-mini de başarısız: {e2}")
            # Son çare: direkt ChatOpenAI
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                api_key=os.getenv("OPENAI_API_KEY"),
                temperature=0
            )
            print("🤖 GPT-4o-mini (direkt) kullanılıyor")
    
    print("🔍 Relationship type'ları analiz ediliyor...")
    preview = preview_normalization(graph, llm)
    
    print(f"\n📊 Özet:")
    print(f"   Toplam type: {preview['total_types']}")
    print(f"   Birleştirilecek: {preview['types_to_merge']}")
    print(f"   Sonuç grup sayısı: {preview['groups_after']}")
    
    print(f"\n📋 Değişiklik gereken gruplar:")
    change_count = 0
    for g in preview["groups"]:
        if g["needs_change"] and len(g["original_types"]) > 1:
            change_count += 1
            print(f"\n   {change_count}. {g['suggested_name']} ({g['count']} relationship, {len(g['original_types'])} type birleşecek)")
            for orig in g["original_types"]:
                marker = "✓" if orig == g["suggested_name"] else "  →"
                print(f"      {marker} {orig}")
    
    print(f"\n📋 Tek kalan gruplar (değişmeyecek): {len([g for g in preview['groups'] if not g['needs_change'] or len(g['original_types']) == 1])}")

