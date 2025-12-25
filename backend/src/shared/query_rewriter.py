# -*- coding: utf-8 -*-
"""
Query Rewriting Module - Agent Optimization

LAYER 9 Features:
- Query decomposition (complex → simple queries)
- Entity mention extraction pre-processing
- Query history context window optimization
- Intent classification

Kullanım:
    from src.shared.query_rewriter import QueryRewriter
    
    rewriter = QueryRewriter()
    result = rewriter.rewrite(question)
    
    # result.simplified_query - Ana sorgu
    # result.sub_queries - Alt sorgular (paralel çalıştırılabilir)
    # result.entities - Çıkarılan entity'ler
    # result.intent - Sorgu amacı

Environment Variables:
    QUERY_REWRITING_ENABLED: Enable/disable rewriting (default: true)
    QUERY_DECOMPOSITION_ENABLED: Enable query decomposition (default: true)
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Environment configuration
QUERY_REWRITING_ENABLED = os.getenv("QUERY_REWRITING_ENABLED", "true").lower() in ("true", "1", "yes")
QUERY_DECOMPOSITION_ENABLED = os.getenv("QUERY_DECOMPOSITION_ENABLED", "true").lower() in ("true", "1", "yes")


@dataclass
class RewriteResult:
    """Query rewrite result"""
    original_query: str
    simplified_query: str
    sub_queries: List[str] = field(default_factory=list)
    entities: List[Dict[str, str]] = field(default_factory=list)  # {name, type}
    intent: str = "general"  # general, lookup, comparison, aggregation, list
    keywords: List[str] = field(default_factory=list)
    language: str = "tr"  # tr, en
    confidence: float = 1.0


class QueryRewriter:
    """
    Query rewriting for agent optimization.
    
    Features:
    - Extract entity mentions
    - Decompose complex queries
    - Classify intent
    - Optimize for graph queries
    """
    
    def __init__(self, enabled: Optional[bool] = None):
        self.enabled = enabled if enabled is not None else QUERY_REWRITING_ENABLED
        
        # Common Turkish entity patterns
        self.entity_patterns = {
            "company": [
                r'\b([A-ZÇĞİÖŞÜ][a-zçğıöşü]*(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]*)*)\s+(?:A\.Ş\.|AŞ|Ltd\.|şirketi|holding)',
                r'\b([A-Z][A-Z0-9]+(?:\s+[A-Z][A-Z0-9]+)*)\b',  # All caps (acronyms)
            ],
            "person": [
                r'\b([A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+){1,3})\b',
            ],
            "policy": [
                r'(?:poliçe|policy)\s*(?:no|numarası|number)?:?\s*([A-Z0-9-]+)',
                r'\b(\d{4,}[/-]?\d+)\b',  # Policy numbers
            ],
            "date": [
                r'\b(\d{4})\s*(?:yılı?|year)\b',
                r'\b(\d{1,2}[./]\d{1,2}[./]\d{2,4})\b',
            ],
            "amount": [
                r'(\d+(?:\.\d+)?)\s*(?:TL|USD|EUR|₺|\$|€)',
                r'(\d{1,3}(?:\.\d{3})*(?:,\d+)?)\s*(?:TL|lira)',
            ],
        }
        
        # Intent classification keywords
        self.intent_keywords = {
            "list": ["listele", "list", "tüm", "all", "hepsi", "kaç tane", "say", "göster"],
            "comparison": ["karşılaştır", "compare", "fark", "difference", "vs", "mı yoksa"],
            "aggregation": ["toplam", "total", "ortalama", "average", "min", "max", "sum"],
            "lookup": ["nedir", "ne", "kim", "who", "what", "hangisi", "which"],
        }
        
        # Question words for decomposition
        self.question_markers = ["ve", "ayrıca", "hem", "aynı zamanda", "ek olarak"]
    
    def rewrite(self, query: str) -> RewriteResult:
        """
        Rewrite query for optimization.
        
        Args:
            query: Original query
        
        Returns:
            RewriteResult with processed query info
        """
        if not self.enabled:
            return RewriteResult(
                original_query=query,
                simplified_query=query,
            )
        
        result = RewriteResult(
            original_query=query,
            simplified_query=query,
        )
        
        # Detect language
        result.language = self._detect_language(query)
        
        # Extract entities
        result.entities = self._extract_entities(query)
        
        # Extract keywords
        result.keywords = self._extract_keywords(query)
        
        # Classify intent
        result.intent = self._classify_intent(query)
        
        # Decompose if complex
        if QUERY_DECOMPOSITION_ENABLED:
            result.sub_queries = self._decompose_query(query)
        
        # Simplify main query
        result.simplified_query = self._simplify_query(query, result.entities)
        
        logger.debug(f"🔄 Query rewritten: intent={result.intent}, entities={len(result.entities)}, sub_queries={len(result.sub_queries)}")
        
        return result
    
    def _detect_language(self, query: str) -> str:
        """Detect query language"""
        turkish_chars = set("çğıöşüÇĞİÖŞÜ")
        turkish_words = {"ne", "mi", "mı", "için", "ile", "ve", "veya", "bu", "şu", "nasıl", "neden"}
        
        # Check for Turkish characters
        if any(c in query for c in turkish_chars):
            return "tr"
        
        # Check for Turkish words
        words = set(query.lower().split())
        if words & turkish_words:
            return "tr"
        
        return "en"
    
    def _extract_entities(self, query: str) -> List[Dict[str, str]]:
        """Extract entity mentions from query"""
        entities = []
        
        for entity_type, patterns in self.entity_patterns.items():
            for pattern in patterns:
                matches = re.findall(pattern, query, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    if len(match) > 2:  # Skip very short matches
                        entities.append({
                            "name": match.strip(),
                            "type": entity_type,
                        })
        
        # Deduplicate
        seen = set()
        unique_entities = []
        for entity in entities:
            key = (entity["name"].lower(), entity["type"])
            if key not in seen:
                seen.add(key)
                unique_entities.append(entity)
        
        return unique_entities
    
    def _extract_keywords(self, query: str) -> List[str]:
        """Extract important keywords from query"""
        # Remove common stop words
        stop_words = {
            "bir", "bu", "şu", "o", "ve", "veya", "ile", "için", "de", "da",
            "mi", "mı", "mu", "mü", "ne", "nasıl", "neden", "nerede",
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
        }
        
        # Tokenize
        words = re.findall(r'\b\w+\b', query.lower())
        
        # Filter
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        return keywords[:10]  # Limit to top 10
    
    def _classify_intent(self, query: str) -> str:
        """Classify query intent"""
        query_lower = query.lower()
        
        for intent, keywords in self.intent_keywords.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return intent
        
        return "general"
    
    def _decompose_query(self, query: str) -> List[str]:
        """Decompose complex query into sub-queries"""
        sub_queries = []
        
        # Check for multiple questions
        for marker in self.question_markers:
            if marker in query.lower():
                parts = re.split(rf'\s*{marker}\s*', query, flags=re.IGNORECASE)
                if len(parts) > 1:
                    sub_queries.extend([p.strip() for p in parts if p.strip()])
                    break
        
        # Check for question marks (multiple questions)
        if not sub_queries and query.count("?") > 1:
            parts = query.split("?")
            sub_queries = [p.strip() + "?" for p in parts if p.strip()]
        
        return sub_queries
    
    def _simplify_query(self, query: str, entities: List[Dict[str, str]]) -> str:
        """Simplify query for better graph matching"""
        simplified = query
        
        # Replace entity mentions with normalized versions
        for entity in entities:
            if entity["type"] == "company":
                # Normalize company names
                name = entity["name"]
                # Remove common suffixes for matching
                normalized = re.sub(r'\s*(A\.Ş\.|AŞ|Ltd\.|şirketi|holding)\s*', '', name, flags=re.IGNORECASE)
                # Only replace if significantly different
                if normalized != name:
                    simplified = simplified.replace(name, normalized.strip())
        
        return simplified.strip()


# Global instance
_query_rewriter: Optional[QueryRewriter] = None


def get_query_rewriter() -> QueryRewriter:
    """Get or create global query rewriter instance"""
    global _query_rewriter
    
    if _query_rewriter is None:
        _query_rewriter = QueryRewriter()
    
    return _query_rewriter


def rewrite_query(query: str) -> RewriteResult:
    """Convenience function for query rewriting"""
    return get_query_rewriter().rewrite(query)

