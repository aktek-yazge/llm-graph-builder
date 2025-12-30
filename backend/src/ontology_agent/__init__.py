"""
Ontology-Driven Neo4j Agent

Bu modül, LLM'in doğrudan Cypher yazmak yerine Graph DSL oluşturmasını
ve bu DSL'in deterministik olarak Cypher'a dönüştürülmesini sağlar.

Mimari:
1. LLM → Graph DSL üretir (daha basit, hata oranı düşük)
2. DSL Validator → Schema ile uyumu kontrol eder
3. DSL → Cypher Compiler → Doğru Cypher sorgusu üretir

Avantajlar:
- LLM daha basit bir dil kullanır → Hata oranı düşer
- DSL validate edilebilir → Hatalı sorgular önlenir  
- Ontoloji ile eşleştirme → Schema uyumu kontrol edilir
- Cypher üretimi deterministik → Tutarlı sonuçlar
"""

from .graph_dsl import GraphDSL, TraversalStep, Filter, ReturnSpec, AggregateSpec, QueryIntent
from .dsl_compiler import DSLCompiler, compile_dsl
from .dsl_validator import DSLValidator, SchemaInfo, validate_dsl

__all__ = [
    "GraphDSL",
    "QueryIntent",
    "TraversalStep", 
    "Filter",
    "ReturnSpec",
    "AggregateSpec",
    "DSLCompiler",
    "compile_dsl",
    "DSLValidator",
    "SchemaInfo",
    "validate_dsl",
]

