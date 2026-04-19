"""
Extraction MCP Server - Sampling + Elicitation document analysis tools.

Uses MCP Sampling to leverage the client's LLM for:
- Entity extraction from documents
- Document classification
- Document summarization
- Schema inference from samples

Uses MCP Elicitation to interact with users for:
- Low-confidence extraction review
- Schema approval
- Entity disambiguation

These tools read documents from MinIO, then use ctx.sample() to have
the client's LLM process them. The MCP server never needs its own LLM API key
for these operations (embedding in neo4j_server is separate).
"""

import json
import logging
from dataclasses import dataclass
from typing import Literal, Optional

from fastmcp import Context
from fastmcp.server import FastMCP
from pydantic import BaseModel, Field

from utils.minio_client import minio_client

logger = logging.getLogger("mcp_services.extraction")

extraction_mcp = FastMCP("Extraction")

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

CONFIDENCE_AUTO_ACCEPT = 0.70
CONFIDENCE_REVIEW = 0.40


# ---------------------------------------------------------------------------
# Pydantic models for structured sampling output
# ---------------------------------------------------------------------------

class ExtractedNode(BaseModel):
    label: str = Field(..., description="Entity type (e.g. Company, Person, Policy)")
    id: str = Field(..., description="Unique identifier for this entity")
    properties: dict = Field(default_factory=dict, description="Entity properties")

class ExtractedRelationship(BaseModel):
    from_id: str = Field(..., description="Source node ID")
    to_id: str = Field(..., description="Target node ID")
    type: str = Field(..., description="Relationship type (e.g. HAS_POLICY, WORKS_AT)")

class ExtractionResult(BaseModel):
    target_entity: str = Field("", description="Primary entity identified in the document")
    document_type: str = Field("", description="Detected document type")
    nodes: list[ExtractedNode] = Field(default_factory=list, description="Extracted entities")
    relationships: list[ExtractedRelationship] = Field(default_factory=list, description="Extracted relationships")
    confidence: float = Field(0.0, description="Extraction confidence score 0.0-1.0")
    summary: str = Field("", description="Brief summary of what was extracted")

class ClassificationResult(BaseModel):
    document_type: str = Field(..., description="Detected document type")
    confidence: float = Field(0.0, description="Classification confidence 0.0-1.0")
    reasoning: str = Field("", description="Why this classification was chosen")
    language: str = Field("", description="Detected language")
    key_indicators: list[str] = Field(default_factory=list, description="Keywords/phrases that led to this classification")

class SummaryResult(BaseModel):
    summary: str = Field(..., description="Document summary")
    key_points: list[str] = Field(default_factory=list, description="Key points extracted")
    entities_mentioned: list[str] = Field(default_factory=list, description="Entity names mentioned")
    word_count: int = Field(0, description="Original document word count")

class SchemaProposal(BaseModel):
    entity_types: list[dict] = Field(default_factory=list, description="Proposed entity types with properties")
    relationship_types: list[dict] = Field(default_factory=list, description="Proposed relationship types")
    reasoning: str = Field("", description="Why this schema was proposed")


# ---------------------------------------------------------------------------
# Elicitation models
# ---------------------------------------------------------------------------

@dataclass
class ExtractionReview:
    """User review of an extraction result."""
    decision: Literal["accept", "reject", "retry"]
    notes: str = ""

@dataclass
class SchemaDecision:
    """User decision on a schema proposal."""
    approved: bool
    modifications: str = ""


# ---------------------------------------------------------------------------
# Helper: heuristic confidence scoring
# ---------------------------------------------------------------------------

def _compute_heuristic_confidence(extraction: ExtractionResult, schema_json: str) -> float:
    """Combine LLM self-assessment with heuristic checks for a more reliable score."""
    llm_score = extraction.confidence

    # Property fill ratio: how many nodes have at least one non-ID property?
    if extraction.nodes:
        filled = sum(1 for n in extraction.nodes if n.properties)
        property_ratio = filled / len(extraction.nodes)
    else:
        property_ratio = 0.0

    # ID format validity: IDs shouldn't be empty or just whitespace
    if extraction.nodes:
        valid_ids = sum(1 for n in extraction.nodes if n.id and n.id.strip())
        id_ratio = valid_ids / len(extraction.nodes)
    else:
        id_ratio = 0.0

    # Schema match: if schema given, check entity labels match expected types
    schema_match = 1.0
    if schema_json:
        try:
            schema = json.loads(schema_json)
            expected_types = set(schema.get("entity_types", []))
            if expected_types and extraction.nodes:
                matched = sum(1 for n in extraction.nodes if n.label in expected_types)
                schema_match = matched / len(extraction.nodes)
        except json.JSONDecodeError:
            pass

    # Relationship validity: check from_id/to_id reference existing nodes
    rel_valid = 1.0
    if extraction.relationships and extraction.nodes:
        node_ids = {n.id for n in extraction.nodes}
        valid_rels = sum(
            1 for r in extraction.relationships
            if r.from_id in node_ids or r.to_id in node_ids
        )
        rel_valid = valid_rels / len(extraction.relationships)

    # Weighted combination
    final = (
        0.25 * llm_score
        + 0.20 * property_ratio
        + 0.15 * id_ratio
        + 0.20 * schema_match
        + 0.20 * rel_valid
    )
    return round(min(max(final, 0.0), 1.0), 3)


# ---------------------------------------------------------------------------
# Helper: read document content from MinIO with size guard
# ---------------------------------------------------------------------------

MAX_CONTENT_CHARS = 30_000

def _read_document(bucket: str, key: str, max_chars: int = MAX_CONTENT_CHARS) -> tuple[str, int]:
    """Read document from MinIO, truncated to max_chars. Returns (content, total_chars)."""
    content = minio_client.get_object_text(bucket, key)
    total = len(content)
    if max_chars > 0 and total > max_chars:
        content = content[:max_chars]
    return content, total


# ---------------------------------------------------------------------------
# Helper: elicitation for extraction review
# ---------------------------------------------------------------------------

async def _elicit_extraction_review(
    ctx: Context, extraction: ExtractionResult, source: str, confidence: float
) -> Optional[ExtractionReview]:
    """Ask user to review a low-confidence extraction via MCP Elicitation."""
    try:
        nodes_summary = ", ".join(f"{n.label}:{n.id}" for n in extraction.nodes[:10])
        rels_summary = ", ".join(f"{r.from_id}-[{r.type}]->{r.to_id}" for r in extraction.relationships[:10])

        message = (
            f"Extraction review needed for: {source}\n\n"
            f"Confidence: {confidence:.0%}\n"
            f"Nodes ({len(extraction.nodes)}): {nodes_summary}\n"
            f"Relationships ({len(extraction.relationships)}): {rels_summary}\n"
            f"Summary: {extraction.summary}\n\n"
            f"What would you like to do?"
        )

        result = await ctx.elicit(
            message=message,
            response_type=ExtractionReview,
        )

        if result.action == "accept":
            return result.data
        elif result.action == "decline":
            return ExtractionReview(decision="reject", notes="User declined review")
        else:
            return ExtractionReview(decision="reject", notes="User cancelled")

    except Exception as e:
        await ctx.warning(f"Elicitation not available: {e}. Auto-accepting extraction.")
        return None


# ---------------------------------------------------------------------------
# Tool 1: Extract Entities (Sampling + Elicitation)
# ---------------------------------------------------------------------------

@extraction_mcp.tool(
    name="extract_entities",
    description=(
        "Extract entities and relationships from a document stored in MinIO. "
        "Uses the client's LLM via MCP Sampling to analyze the document. "
        "Optionally provide a JSON schema to guide extraction. "
        "Returns structured entities, relationships, and confidence score. "
        "For low-confidence results (below 70%), asks user for review via Elicitation. "
        "Set auto_accept=true to skip user review."
    ),
)
async def extract_entities(
    bucket: str = Field(..., description="MinIO bucket name (e.g. 'documents')"),
    key: str = Field(..., description="File path in bucket (e.g. 'invoices/2024/inv_001.txt')"),
    schema_json: str = Field(
        "",
        description=(
            "Optional JSON schema to guide extraction. Example: "
            '{"entity_types": ["Company", "Person"], "relationship_types": ["WORKS_AT", "OWNS"]}'
        ),
    ),
    max_chars: int = Field(MAX_CONTENT_CHARS, description="Max characters to process from document"),
    auto_accept: bool = Field(False, description="If true, skip user review for low-confidence results"),
    ctx: Context = None,  # type: ignore[assignment]
) -> dict:
    """Extract entities and relationships from a document using LLM Sampling."""
    content, total_chars = _read_document(bucket, key, max_chars)
    await ctx.info(f"Read {len(content)}/{total_chars} chars from {bucket}/{key}")

    schema_instruction = ""
    if schema_json:
        schema_instruction = f"\n\nUse this schema to guide your extraction:\n{schema_json}"

    system_prompt = (
        "You are an expert entity extraction system. "
        "Extract entities (nodes) and relationships from the given document. "
        "Be precise - only extract what is explicitly stated in the text. "
        "Do not infer or hallucinate information. "
        "Assign a confidence score (0.0-1.0) based on how clearly the entities "
        "are identifiable in the text. "
        "For properties, include all relevant attributes found in the document."
        f"{schema_instruction}"
    )

    user_message = (
        f"Document: {key}\n"
        f"Content ({len(content)} of {total_chars} total chars):\n\n"
        f"{content}"
    )

    await ctx.report_progress(progress=1, total=4)

    result = await ctx.sample(
        messages=user_message,
        system_prompt=system_prompt,
        result_type=ExtractionResult,
        temperature=0.1,
        max_tokens=4000,
    )

    await ctx.report_progress(progress=2, total=4)

    extraction: ExtractionResult = result.result
    confidence = _compute_heuristic_confidence(extraction, schema_json)
    await ctx.info(
        f"Extracted {len(extraction.nodes)} nodes, "
        f"{len(extraction.relationships)} relationships, "
        f"heuristic_confidence={confidence:.2f} (llm_score={extraction.confidence:.2f})"
    )

    # Determine review status based on confidence
    review_status = "auto_accepted"
    review_notes = ""

    if confidence < CONFIDENCE_AUTO_ACCEPT and not auto_accept:
        await ctx.report_progress(progress=3, total=4)

        if confidence < CONFIDENCE_REVIEW:
            review_status = "low_confidence_needs_review"
            await ctx.warning(
                f"Very low confidence ({confidence:.0%}). "
                f"Consider re-processing or manual review."
            )
        else:
            review_status = "pending_review"

        user_review = await _elicit_extraction_review(ctx, extraction, f"{bucket}/{key}", confidence)

        if user_review:
            review_status = f"user_{user_review.decision}"
            review_notes = user_review.notes

            if user_review.decision == "reject":
                await ctx.info("User rejected extraction")
                return {
                    "source": f"{bucket}/{key}",
                    "status": "rejected",
                    "confidence": confidence,
                    "review_notes": review_notes,
                    "node_count": 0,
                    "relationship_count": 0,
                }
            elif user_review.decision == "retry":
                await ctx.info("User requested retry - returning for re-processing")
                return {
                    "source": f"{bucket}/{key}",
                    "status": "retry_requested",
                    "confidence": confidence,
                    "review_notes": review_notes,
                    "node_count": len(extraction.nodes),
                    "relationship_count": len(extraction.relationships),
                }

    output = {
        "source": f"{bucket}/{key}",
        "status": review_status,
        "total_chars": total_chars,
        "processed_chars": len(content),
        "target_entity": extraction.target_entity,
        "document_type": extraction.document_type,
        "nodes": [n.model_dump() for n in extraction.nodes],
        "relationships": [r.model_dump() for r in extraction.relationships],
        "confidence": confidence,
        "llm_confidence": extraction.confidence,
        "summary": extraction.summary,
        "node_count": len(extraction.nodes),
        "relationship_count": len(extraction.relationships),
        "review_notes": review_notes,
    }

    await ctx.report_progress(progress=4, total=4)
    return output


# ---------------------------------------------------------------------------
# Tool 2: Classify Document (Sampling)
# ---------------------------------------------------------------------------

@extraction_mcp.tool(
    name="classify_document",
    description=(
        "Classify a document's type and language by analyzing its content. "
        "Uses the client's LLM via MCP Sampling. "
        "Reads the first portion of the document for efficient classification."
    ),
)
async def classify_document(
    bucket: str = Field(..., description="MinIO bucket name"),
    key: str = Field(..., description="File path in bucket"),
    classification_categories: str = Field(
        "",
        description=(
            "Optional comma-separated list of valid categories. "
            "Example: 'MAIN_POLICY,ENDORSEMENT,RENEWAL,CANCELLATION' or "
            "'invoice,contract,report,correspondence'"
        ),
    ),
    ctx: Context = None,  # type: ignore[assignment]
) -> dict:
    """Classify a document type using LLM Sampling."""
    content, total_chars = _read_document(bucket, key, max_chars=10_000)
    await ctx.info(f"Read {len(content)} chars for classification of {key}")

    categories_instruction = ""
    if classification_categories:
        cats = [c.strip() for c in classification_categories.split(",")]
        categories_instruction = (
            f"\n\nValid document types (choose ONLY from these): {', '.join(cats)}"
        )

    system_prompt = (
        "You are a document classification expert. "
        "Analyze the document content and determine its type, language, and key characteristics. "
        "Assign a confidence score (0.0-1.0) for your classification."
        f"{categories_instruction}"
    )

    user_message = (
        f"Document: {key}\n"
        f"Content (first {len(content)} of {total_chars} chars):\n\n"
        f"{content}"
    )

    result = await ctx.sample(
        messages=user_message,
        system_prompt=system_prompt,
        result_type=ClassificationResult,
        temperature=0.1,
        max_tokens=500,
    )

    classification: ClassificationResult = result.result
    await ctx.info(f"Classified as '{classification.document_type}' (confidence={classification.confidence:.2f})")

    return {
        "source": f"{bucket}/{key}",
        "document_type": classification.document_type,
        "confidence": classification.confidence,
        "reasoning": classification.reasoning,
        "language": classification.language,
        "key_indicators": classification.key_indicators,
    }


# ---------------------------------------------------------------------------
# Tool 3: Summarize Document (Sampling)
# ---------------------------------------------------------------------------

@extraction_mcp.tool(
    name="summarize_document",
    description=(
        "Generate a summary of a document stored in MinIO. "
        "Uses the client's LLM via MCP Sampling. "
        "Returns summary, key points, and mentioned entities."
    ),
)
async def summarize_document(
    bucket: str = Field(..., description="MinIO bucket name"),
    key: str = Field(..., description="File path in bucket"),
    max_summary_length: int = Field(
        500, description="Target summary length in words (approximate)"
    ),
    focus: str = Field(
        "", description="Optional focus area (e.g. 'financial details', 'people mentioned', 'dates and deadlines')"
    ),
    ctx: Context = None,  # type: ignore[assignment]
) -> dict:
    """Summarize a document using LLM Sampling."""
    content, total_chars = _read_document(bucket, key)
    await ctx.info(f"Read {len(content)}/{total_chars} chars for summarization of {key}")

    focus_instruction = ""
    if focus:
        focus_instruction = f"\n\nFocus especially on: {focus}"

    system_prompt = (
        "You are a document summarization expert. "
        f"Create a concise summary (approximately {max_summary_length} words). "
        "Extract key points and list all entity names mentioned in the document."
        f"{focus_instruction}"
    )

    word_count = len(content.split())

    user_message = (
        f"Document: {key}\n"
        f"Content ({word_count} words, {total_chars} chars):\n\n"
        f"{content}"
    )

    result = await ctx.sample(
        messages=user_message,
        system_prompt=system_prompt,
        result_type=SummaryResult,
        temperature=0.3,
        max_tokens=2000,
    )

    summary: SummaryResult = result.result
    summary.word_count = word_count

    await ctx.info(f"Generated summary with {len(summary.key_points)} key points")

    return {
        "source": f"{bucket}/{key}",
        "summary": summary.summary,
        "key_points": summary.key_points,
        "entities_mentioned": summary.entities_mentioned,
        "original_word_count": word_count,
        "original_char_count": total_chars,
    }


# ---------------------------------------------------------------------------
# Tool 4: Infer Schema from Samples (Sampling + Elicitation)
# ---------------------------------------------------------------------------

@extraction_mcp.tool(
    name="infer_schema",
    description=(
        "Analyze multiple sample documents and propose a knowledge graph schema. "
        "Reads several documents from a MinIO prefix and uses the client's LLM "
        "to suggest entity types, properties, and relationship types. "
        "Then asks user to approve or modify the proposed schema via Elicitation. "
        "This is the first step in creating a KB extraction pipeline."
    ),
)
async def infer_schema(
    bucket: str = Field(..., description="MinIO bucket name"),
    prefix: str = Field("", description="Folder prefix to find sample documents"),
    max_samples: int = Field(5, description="Max number of sample documents to analyze (1-10)"),
    domain_hint: str = Field(
        "",
        description="Optional hint about the domain (e.g. 'insurance policies', 'trade registry', 'invoices')"
    ),
    auto_approve: bool = Field(False, description="If true, skip user approval of schema"),
    ctx: Context = None,  # type: ignore[assignment]
) -> dict:
    """Analyze sample documents and propose a graph schema using LLM Sampling."""
    max_samples = min(max(max_samples, 1), 10)

    files = minio_client.search_objects(bucket, query="", prefix=prefix, limit=max_samples)
    if not files:
        return {"error": f"No files found in {bucket}/{prefix}"}

    await ctx.info(f"Found {len(files)} sample documents in {bucket}/{prefix}")
    await ctx.report_progress(progress=0, total=len(files) + 2)

    samples_text = []
    for i, f in enumerate(files):
        try:
            content, total = _read_document(bucket, f["key"], max_chars=8_000)
            samples_text.append(
                f"--- Document {i+1}: {f['key']} ({total} chars) ---\n{content}\n"
            )
            await ctx.report_progress(progress=i + 1, total=len(files) + 2)
        except Exception as e:
            await ctx.warning(f"Could not read {f['key']}: {e}")

    if not samples_text:
        return {"error": "Could not read any sample documents"}

    domain_instruction = ""
    if domain_hint:
        domain_instruction = f"\n\nDomain context: {domain_hint}"

    system_prompt = (
        "You are a knowledge graph schema designer. "
        "Analyze the provided sample documents and propose a graph schema. "
        "For each entity type, list its properties with data types. "
        "For each relationship type, specify source and target entity types. "
        "Be specific to the actual content - don't propose generic schemas."
        f"{domain_instruction}"
    )

    user_message = (
        f"Analyze these {len(samples_text)} sample documents and propose a knowledge graph schema:\n\n"
        + "\n".join(samples_text)
    )

    result = await ctx.sample(
        messages=user_message,
        system_prompt=system_prompt,
        result_type=SchemaProposal,
        temperature=0.2,
        max_tokens=3000,
    )

    proposal: SchemaProposal = result.result
    await ctx.report_progress(progress=len(files) + 1, total=len(files) + 2)
    await ctx.info(
        f"Proposed schema: {len(proposal.entity_types)} entity types, "
        f"{len(proposal.relationship_types)} relationship types"
    )

    # Elicitation: ask user to approve schema
    approval_status = "auto_approved" if auto_approve else "pending"

    if not auto_approve:
        try:
            entity_summary = ", ".join(
                e.get("name", e.get("label", str(e))) for e in proposal.entity_types
            )
            rel_summary = ", ".join(
                r.get("type", r.get("name", str(r))) for r in proposal.relationship_types
            )

            approval = await ctx.elicit(
                message=(
                    f"Schema proposal for {bucket}/{prefix}:\n\n"
                    f"Entity types ({len(proposal.entity_types)}): {entity_summary}\n"
                    f"Relationship types ({len(proposal.relationship_types)}): {rel_summary}\n\n"
                    f"Reasoning: {proposal.reasoning}\n\n"
                    f"Approve this schema?"
                ),
                response_type=SchemaDecision,
            )

            if approval.action == "accept":
                decision: SchemaDecision = approval.data
                if decision.approved:
                    approval_status = "approved"
                    if decision.modifications:
                        approval_status = "approved_with_modifications"
                else:
                    approval_status = "rejected"
            elif approval.action == "decline":
                approval_status = "declined"
            else:
                approval_status = "cancelled"

        except Exception as e:
            await ctx.warning(f"Elicitation not available: {e}. Auto-approving schema.")
            approval_status = "auto_approved"

    await ctx.report_progress(progress=len(files) + 2, total=len(files) + 2)

    return {
        "samples_analyzed": len(samples_text),
        "sample_files": [f["key"] for f in files],
        "entity_types": proposal.entity_types,
        "relationship_types": proposal.relationship_types,
        "reasoning": proposal.reasoning,
        "approval_status": approval_status,
    }


# ---------------------------------------------------------------------------
# Tool 5: Review Extraction Results (Elicitation)
# ---------------------------------------------------------------------------

@extraction_mcp.tool(
    name="review_extraction",
    description=(
        "Present extraction results to user for review via Elicitation. "
        "Use this for batch-mode review of previously extracted entities. "
        "Reads a stored extraction result from MinIO and asks user to "
        "accept, reject, or request re-processing."
    ),
)
async def review_extraction(
    key: str = Field(..., description="Path to extraction result JSON file"),
    bucket: str = Field("ocr-output", description="Bucket with extraction results"),
    ctx: Context = None,  # type: ignore[assignment]
) -> dict:
    """Present an extraction result for user review via Elicitation."""
    try:
        raw = minio_client.get_object_text(bucket, key)
        extraction_data = json.loads(raw)
    except Exception as e:
        return {"error": f"Could not read extraction result: {e}"}

    nodes = extraction_data.get("nodes", [])
    relationships = extraction_data.get("relationships", [])
    confidence = extraction_data.get("confidence", 0.0)
    source = extraction_data.get("source", key)

    nodes_summary = ", ".join(
        f"{n.get('label', '?')}:{n.get('id', '?')}" for n in nodes[:15]
    )
    rels_summary = ", ".join(
        f"{r.get('from_id', '?')}-[{r.get('type', '?')}]->{r.get('to_id', '?')}"
        for r in relationships[:10]
    )

    extra_nodes_msg = f" (+{len(nodes) - 15} more)" if len(nodes) > 15 else ""
    extra_rels_msg = f" (+{len(relationships) - 10} more)" if len(relationships) > 10 else ""

    try:
        review = await ctx.elicit(
            message=(
                f"Review extraction from: {source}\n\n"
                f"Confidence: {confidence:.0%}\n"
                f"Nodes ({len(nodes)}): {nodes_summary}{extra_nodes_msg}\n"
                f"Relationships ({len(relationships)}): {rels_summary}{extra_rels_msg}\n"
                f"Summary: {extraction_data.get('summary', 'N/A')}\n\n"
                f"Decision:"
            ),
            response_type=ExtractionReview,
        )

        if review.action == "accept":
            decision = review.data
            return {
                "source": source,
                "result_key": key,
                "decision": decision.decision,
                "notes": decision.notes,
                "node_count": len(nodes),
                "relationship_count": len(relationships),
            }
        elif review.action == "decline":
            return {"source": source, "result_key": key, "decision": "skipped"}
        else:
            return {"source": source, "result_key": key, "decision": "cancelled"}

    except Exception as e:
        return {"error": f"Elicitation not available: {e}", "source": source}


# ---------------------------------------------------------------------------
# Tool 6: Batch Review Summary (Elicitation)
# ---------------------------------------------------------------------------

@extraction_mcp.tool(
    name="batch_review_status",
    description=(
        "Get a summary of extraction results that need review. "
        "Scans the ocr-output bucket for results with low confidence "
        "and returns a summary with counts by status."
    ),
)
async def batch_review_status(
    bucket: str = Field("ocr-output", description="Bucket with extraction results"),
    prefix: str = Field("", description="Filter by prefix"),
    confidence_threshold: float = Field(
        CONFIDENCE_AUTO_ACCEPT,
        description="Show results below this confidence (default 0.70)"
    ),
    ctx: Context = None,  # type: ignore[assignment]
) -> dict:
    """Scan extraction results and report review status."""
    files = minio_client.search_objects(bucket, query=".json", prefix=prefix, limit=200)
    await ctx.info(f"Scanning {len(files)} result files in {bucket}/{prefix}")

    stats = {
        "total": 0,
        "auto_accepted": 0,
        "needs_review": 0,
        "rejected": 0,
        "retry": 0,
        "low_confidence_files": [],
    }

    for f in files:
        try:
            raw = minio_client.get_object_text(bucket, f["key"])
            data = json.loads(raw)
            conf = data.get("confidence", 0.0)
            status = data.get("status", "unknown")
            stats["total"] += 1

            if conf >= confidence_threshold:
                stats["auto_accepted"] += 1
            else:
                stats["needs_review"] += 1
                stats["low_confidence_files"].append({
                    "key": f["key"],
                    "confidence": conf,
                    "status": status,
                    "node_count": data.get("node_count", 0),
                })

            if status == "rejected":
                stats["rejected"] += 1
            elif status == "retry_requested":
                stats["retry"] += 1

        except Exception:
            continue

    stats["low_confidence_files"].sort(key=lambda x: x["confidence"])

    await ctx.info(
        f"Review status: {stats['total']} total, "
        f"{stats['auto_accepted']} auto-accepted, "
        f"{stats['needs_review']} needs review"
    )

    return stats
