"""Auto-import all built-in node types so their @register decorators fire."""
from . import (  # noqa: F401
    resources_input,
    ocr_step,
    wiki_builder,
    ontology_designer,
    entity_extractor,
    kg_writer,
    quality_gate,
    human_review,
    publish_graphrag_endpoint,
)
