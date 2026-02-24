"""
Ontology Module
===============

Neo4j Ontology DB ile etkileşim sağlayan modül.
Goal-driven reasoning ve semantic skill matching işlevleri içerir.

Bileşenler:
-----------
- neo4j_client.py: Ontology DB bağlantı yönetimi
- reasoner.py: OntologyReasoner - Goal/Skill matching ve reasoning
- schema_suggester.py: EntitySchema önerisi (örnek belgelerden)

Ontology Node Tipleri:
----------------------
- Goal: Kullanıcı hedefleri
- Skill: Hedefleri gerçekleştiren yetenekler  
- EntitySchema: Domain entity tanımları
- RelationshipSchema: Entity ilişki tanımları
- AgentDefinition: Oluşturulan agent tanımları
- Context: Skill/Goal'un geçerli olduğu bağlamlar
- Learning: Agent'ın öğrendiği pattern'ler (tenant-isolated)
"""

from .neo4j_client import OntologyDBClient, get_ontology_client, initialize_ontology_db
from .reasoner import OntologyReasoner

__all__ = [
    "OntologyDBClient",
    "OntologyReasoner",
    "get_ontology_client",
    "initialize_ontology_db",
]
