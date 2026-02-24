"""
Agent Builder Module
====================

Goal-driven ve Ontology-driven yaklaşımla kullanıcıların özel agent'lar 
oluşturmasını sağlayan modül.

Temel Bileşenler:
-----------------
- ontology/: Neo4j Ontology DB ile etkileşim (Goal, Skill, Schema reasoning)
- goals/: Goal parsing, decomposition ve tracking
- skills/: Skill generation, validation ve registry
- conversation/: Builder agent conversation state machine
- gateway/: MCP Context-Forge Gateway entegrasyonu

Mimari Notlar:
--------------
- Belgeler ve Agent metadata ayrı Neo4j instance'larında tutulur
- Documents DB: Tenant-specific belge verileri
- Ontology DB: Shared goals, skills, schemas + tenant-specific agents/learnings
- Learning'ler tenant-isolated tutulur

Kullanım:
---------
    from backend.src.agent_builder import AgentBuilderService
    
    service = AgentBuilderService(ontology_db, tenant_id)
    session = await service.start_session(user_id)
    response = await service.process_message(session.id, "Sigorta belgeleri işlemek istiyorum")
"""

__version__ = "0.1.0"

# Lazy imports to avoid circular dependencies
def get_agent_builder_router():
    """Agent Builder API router'ını döndürür"""
    from .router import router
    return router

def get_agent_builder_service():
    """AgentBuilderService sınıfını döndürür"""
    from .service import AgentBuilderService
    return AgentBuilderService
