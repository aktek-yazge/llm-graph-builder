"""
Workflow engine for Knowledge Base Graph builder.

DSL-driven pipeline: Resources → OCR → Wiki → Ontology → Entity Extraction
→ KG Write → Quality Gate → Publish.

The builder agent mutates the DSL via tool calls; the compiler turns it into
a LangGraph StateGraph for execution.
"""
