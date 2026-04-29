"""LLM tabanlı ilişki çıkarımı + Neo4j writer.

Akış:
    document_text + resolved_entities  →  RelationExtractor (LLM)  →  validated JSON
                                       →  RelationPipeline         →  Neo4j MERGE

Provider'lar pluggable (OpenAI, Ollama, Stub). Test için StubProvider deterministik.
"""
from .extractor import (
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
    RelationExtractor,
    ResolvedEntityInput,
    StubProvider,
)
from .schemas import (
    ConfidenceLabel,
    ExtractedRelation,
    RelationExtractionResponse,
    response_json_schema,
)
from .writer import IngestionResult, RelationPipeline

__all__ = [
    "ConfidenceLabel",
    "ExtractedRelation",
    "IngestionResult",
    "LLMProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "RelationExtractionResponse",
    "RelationExtractor",
    "RelationPipeline",
    "ResolvedEntityInput",
    "StubProvider",
    "response_json_schema",
]
