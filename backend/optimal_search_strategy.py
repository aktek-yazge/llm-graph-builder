"""
Business Node'larda Optimal Arama Stratejisi
==========================================

Hibrit yaklaşım: Hem contains hem semantic arama
"""

def business_node_search(query: str, search_type: str = "hybrid"):
    """
    Business node'larda arama yapar
    
    Args:
        query: Arama terimi
        search_type: "contains", "semantic", "hybrid"
    """
    
    if search_type == "contains":
        return contains_search(query)
    elif search_type == "semantic": 
        return semantic_search(query)
    else:
        return hybrid_search(query)

def contains_search(query: str):
    """
    Hızlı string contains araması
    ✅ Performanslı, doğrudan match
    ❌ Semantic anlama yok
    """
    cypher_query = """
    // Customer araması
    MATCH (c:Customer) 
    WHERE toLower(c.name) CONTAINS toLower($query) 
       OR toLower(c.fullName) CONTAINS toLower($query)
       OR toLower(c.searchTerms) CONTAINS toLower($query)
    RETURN c, 'Customer' as nodeType, 1.0 as score
    
    UNION ALL
    
    // PolicyType araması  
    MATCH (pt:PolicyType)
    WHERE toLower(pt.name) CONTAINS toLower($query)
       OR toLower(pt.typeName) CONTAINS toLower($query) 
       OR toLower(pt.searchTerms) CONTAINS toLower($query)
    RETURN pt as c, 'PolicyType' as nodeType, 1.0 as score
    
    UNION ALL
    
    // InsuredItem araması
    MATCH (ii:InsuredItem)
    WHERE toLower(ii.name) CONTAINS toLower($query)
       OR toLower(ii.description) CONTAINS toLower($query)
       OR toLower(ii.searchTerms) CONTAINS toLower($query)  
    RETURN ii as c, 'InsuredItem' as nodeType, 1.0 as score
    
    ORDER BY score DESC, nodeType
    """
    return cypher_query

def semantic_search(query: str):
    """
    Embedding tabanlı semantic arama
    ✅ Anlamsal benzerlik, typo tolerant
    ❌ Daha yavaş, embedding gerekli
    """
    cypher_query = """
    // Vector index ile semantic arama (eğer embedding varsa)
    CALL db.index.vector.queryNodes('business_vector', 10, $query_embedding) 
    YIELD node, score
    WHERE score > 0.7 
      AND (node:Customer OR node:PolicyType OR node:InsuredItem)
    RETURN node as c, 
           CASE 
             WHEN node:Customer THEN 'Customer'
             WHEN node:PolicyType THEN 'PolicyType' 
             WHEN node:InsuredItem THEN 'InsuredItem'
           END as nodeType,
           score
    ORDER BY score DESC
    """
    return cypher_query

def hybrid_search(query: str):
    """
    Hem contains hem semantic - en iyi sonuçlar için
    ✅ Hem hızlı exact match hem semantic
    ❌ Biraz karmaşık
    """
    cypher_query = """
    // İlk önce exact contains match (ağırlık: 2.0)
    MATCH (c:Customer) 
    WHERE toLower(c.name) CONTAINS toLower($query) 
       OR toLower(c.fullName) CONTAINS toLower($query)
    RETURN c, 'Customer' as nodeType, 2.0 as score, 'exact' as matchType
    
    UNION ALL
    
    MATCH (pt:PolicyType)
    WHERE toLower(pt.name) CONTAINS toLower($query)
       OR toLower(pt.typeName) CONTAINS toLower($query)
    RETURN pt as c, 'PolicyType' as nodeType, 2.0 as score, 'exact' as matchType
    
    UNION ALL
    
    // Sonra partial/search terms match (ağırlık: 1.5)
    MATCH (c:Customer)
    WHERE toLower(c.searchTerms) CONTAINS toLower($query)
      AND NOT (toLower(c.name) CONTAINS toLower($query))
    RETURN c, 'Customer' as nodeType, 1.5 as score, 'partial' as matchType
    
    UNION ALL
    
    // Vector similarity (varsa) (ağırlık: score)
    CALL db.index.vector.queryNodes('business_vector', 5, $query_embedding) 
    YIELD node, score
    WHERE score > 0.7 
      AND (node:Customer OR node:PolicyType OR node:InsuredItem)
      AND NOT EXISTS {
        MATCH (node)
        WHERE toLower(node.name) CONTAINS toLower($query)
      }
    RETURN node as c, 
           labels(node)[0] as nodeType,
           score, 
           'semantic' as matchType
    
    ORDER BY score DESC, matchType
    LIMIT 20
    """
    return cypher_query

def performance_comparison():
    """
    Arama stratejilerinin performans karşılaştırması
    """
    return {
        "contains_search": {
            "speed": "🚀 Çok Hızlı",
            "accuracy": "📊 Yüksek (exact match)",
            "flexibility": "🤏 Düşük",
            "cost": "💰 Düşük",
            "use_case": "Tam isim, kod aramaları"
        },
        "semantic_search": {
            "speed": "🐌 Yavaş", 
            "accuracy": "🎯 Orta (bazen irrelevant)",
            "flexibility": "🤸 Çok Yüksek",
            "cost": "💸 Yüksek",
            "use_case": "Anlamsal, çok dilli arama"
        },
        "hybrid_search": {
            "speed": "⚡ Orta",
            "accuracy": "🎯 Çok Yüksek", 
            "flexibility": "🚀 Yüksek",
            "cost": "💳 Orta",
            "use_case": "Genel amaçlı en iyi sonuç"
        }
    }

# Örnek kullanım senaryoları
search_examples = {
    "exact_name_search": {
        "query": "Ayça Dinçkök",
        "best_strategy": "contains",
        "reason": "Tam isim - exact match en hızlı"
    },
    "policy_type_search": {
        "query": "konut",
        "best_strategy": "contains", 
        "reason": "Policy type codes - direct match"
    },
    "fuzzy_search": {
        "query": "ev sigortası",  # Konut Sigortası'nı araması gerekiyor
        "best_strategy": "semantic",
        "reason": "Anlamsal benzerlik gerekli"
    },
    "typo_search": {
        "query": "Ayca Dinkok",  # Yanlış yazım
        "best_strategy": "semantic",
        "reason": "Typo tolerance için semantic"
    },
    "general_search": {
        "query": "galata",
        "best_strategy": "hybrid",
        "reason": "Hem exact hem partial match ihtimali"
    }
}
