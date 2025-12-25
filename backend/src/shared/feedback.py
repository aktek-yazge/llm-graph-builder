# -*- coding: utf-8 -*-
"""
Feedback Loop Module - User Feedback Integration with Learning

LAYER 10 Features:
- Thumbs up/down API endpoint integration
- Feedback storage (PostgreSQL + In-memory fallback)
- Langfuse feedback annotation
- Feedback analytics
- Agent steps (tool calls) recording
- Few-shot example extraction for learning

Kullanım:
    from src.shared.feedback import (
        record_feedback,
        get_feedback_stats,
        get_few_shot_examples,
        FeedbackType,
    )
    
    # Feedback kaydet (tool calls ile)
    record_feedback(
        session_id="xxx",
        question_id="yyy",
        feedback_type=FeedbackType.POSITIVE,
        comment="Çok yardımcı oldu",
        tool_calls=[
            {"tool": "execute_cypher_query", "input": "MATCH...", "output": "..."},
        ],
    )
    
    # Few-shot örnekleri al
    examples = get_few_shot_examples(question="Akenerji primleri", limit=3)

Environment Variables:
    FEEDBACK_ENABLED: Enable/disable feedback (default: true)
    FEEDBACK_LANGFUSE_SYNC: Sync feedback to Langfuse (default: true)
    FEEDBACK_POSTGRES_ENABLED: Use PostgreSQL storage (default: true)
    FEEDBACK_FEW_SHOT_ENABLED: Enable few-shot learning (default: true)
    FEEDBACK_MIN_SCORE_FOR_FEWSHOT: Min score for few-shot (default: 1)
"""

import os
import json
import logging
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

# Environment configuration
FEEDBACK_ENABLED = os.getenv("FEEDBACK_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_LANGFUSE_SYNC = os.getenv("FEEDBACK_LANGFUSE_SYNC", "true").lower() in ("true", "1", "yes")
FEEDBACK_POSTGRES_ENABLED = os.getenv("FEEDBACK_POSTGRES_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_FEW_SHOT_ENABLED = os.getenv("FEEDBACK_FEW_SHOT_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_MIN_SCORE_FOR_FEWSHOT = int(os.getenv("FEEDBACK_MIN_SCORE_FOR_FEWSHOT", "1"))


class FeedbackType(Enum):
    """Feedback types"""
    POSITIVE = "positive"  # Thumbs up
    NEGATIVE = "negative"  # Thumbs down
    NEUTRAL = "neutral"    # No preference
    
    # Detailed feedback
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    INCORRECT = "incorrect"
    INCOMPLETE = "incomplete"
    OFF_TOPIC = "off_topic"


@dataclass
class ToolCall:
    """Tool call record"""
    tool_name: str
    tool_input: str
    tool_output: str
    duration_ms: Optional[int] = None
    success: bool = True


@dataclass
class Feedback:
    """Feedback model with tool calls"""
    id: Optional[str] = None
    session_id: str = ""
    question_id: str = ""
    question: str = ""
    response: str = ""
    feedback_type: FeedbackType = FeedbackType.NEUTRAL
    score: int = 0  # -1, 0, 1
    comment: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    user_id: Optional[str] = None
    
    # NEW: Correction fields for learning from mistakes
    corrected_response: Optional[str] = None  # Admin'in düzelttiği cevap
    corrected_queries: Optional[List[str]] = None  # Doğru Cypher sorguları
    correction_note: Optional[str] = None  # Neden yanlıştı açıklaması
    
    # NEW: Tool calls for learning
    tool_calls: List[ToolCall] = field(default_factory=list)
    
    # NEW: Question embedding for similarity search
    question_embedding: Optional[List[float]] = None
    
    # NEW: Usage count for few-shot
    used_as_example_count: int = 0


# In-memory storage (fallback)
_feedback_store: List[Feedback] = []

# PostgreSQL connection
_pg_pool = None


# ============================================================================
# POSTGRESQL STORAGE
# ============================================================================

def _get_pg_pool():
    """Get or create PostgreSQL connection pool"""
    global _pg_pool
    
    if _pg_pool is not None:
        return _pg_pool
    
    if not FEEDBACK_POSTGRES_ENABLED:
        return None
    
    try:
        import psycopg2
        import psycopg2.pool
        
        db_url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
        if not db_url:
            logger.warning("⚠️ POSTGRES_URL not set, using in-memory storage")
            return None
        
        _pg_pool = psycopg2.pool.SimpleConnectionPool(1, 5, db_url)
        logger.info("✅ PostgreSQL connection pool created for feedback")
        
        # Create table if not exists
        _create_feedback_table()
        
        return _pg_pool
        
    except Exception as e:
        logger.warning(f"⚠️ PostgreSQL connection failed: {e}, using in-memory")
        return None


def _create_feedback_table():
    """Create feedback table with pgvector support and auto-migrate existing tables"""
    pool = _get_pg_pool()
    if not pool:
        return
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            # pgvector extension
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                logger.info("✅ pgvector extension ready")
            except Exception as e:
                logger.warning(f"⚠️ pgvector extension not available: {e}")
            
            # Feedback table with vector column
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id VARCHAR(255) PRIMARY KEY,
                    session_id VARCHAR(255) NOT NULL,
                    question_id VARCHAR(255) NOT NULL,
                    question TEXT,
                    response TEXT,
                    feedback_type VARCHAR(50),
                    score INTEGER DEFAULT 0,
                    comment TEXT,
                    user_id VARCHAR(255),
                    tool_calls JSONB,
                    question_embedding JSONB,
                    embedding vector(1536),
                    metadata JSONB,
                    used_as_example_count INTEGER DEFAULT 0,
                    corrected_response TEXT,
                    corrected_queries JSONB,
                    correction_note TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                
                CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_score ON feedback(score);
                CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);
            """)
            
            # AUTO-MIGRATION: Add missing columns to existing tables
            migrations = [
                # pgvector embedding column
                ("embedding", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS embedding vector(1536);"),
                # Correction fields
                ("corrected_response", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS corrected_response TEXT;"),
                ("corrected_queries", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS corrected_queries JSONB;"),
                ("correction_note", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS correction_note TEXT;"),
                # Other fields that might be missing
                ("used_as_example_count", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS used_as_example_count INTEGER DEFAULT 0;"),
            ]
            
            for column_name, migration_sql in migrations:
                try:
                    cur.execute(migration_sql)
                    logger.debug(f"✅ Migration applied: {column_name}")
                except Exception as e:
                    # Column already exists or other non-critical error
                    logger.debug(f"Migration skipped for {column_name}: {e}")
            
            # pgvector HNSW index for fast similarity search
            try:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_feedback_embedding 
                    ON feedback USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64);
                """)
                logger.info("✅ pgvector HNSW index ready")
            except Exception as e:
                logger.warning(f"⚠️ HNSW index creation failed (pgvector may not be installed): {e}")
            
            # Migrate existing embeddings from JSONB to vector format
            try:
                cur.execute("""
                    UPDATE feedback 
                    SET embedding = question_embedding::text::vector
                    WHERE embedding IS NULL 
                      AND question_embedding IS NOT NULL
                      AND jsonb_array_length(question_embedding) = 1536;
                """)
                migrated = cur.rowcount
                if migrated > 0:
                    logger.info(f"✅ Migrated {migrated} existing embeddings to pgvector format")
            except Exception as e:
                logger.debug(f"Embedding migration skipped: {e}")
            
            conn.commit()
            logger.info("✅ Feedback table ready with pgvector support")
    except Exception as e:
        logger.error(f"❌ Failed to create/migrate feedback table: {e}")
    finally:
        if conn:
            pool.putconn(conn)


def _save_to_postgres(feedback: Feedback) -> bool:
    """Save feedback to PostgreSQL with pgvector embedding"""
    pool = _get_pg_pool()
    if not pool:
        return False
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            # Serialize tool_calls
            tool_calls_json = json.dumps([
                {
                    "tool_name": tc.tool_name,
                    "tool_input": tc.tool_input,
                    "tool_output": tc.tool_output,
                    "duration_ms": tc.duration_ms,
                    "success": tc.success,
                }
                for tc in feedback.tool_calls
            ]) if feedback.tool_calls else "[]"
            
            # Format embedding for pgvector (string format: "[0.1, 0.2, ...]")
            embedding_str = None
            if feedback.question_embedding:
                embedding_str = "[" + ",".join(str(x) for x in feedback.question_embedding) + "]"
            
            cur.execute("""
                INSERT INTO feedback (
                    id, session_id, question_id, question, response,
                    feedback_type, score, comment, user_id, tool_calls,
                    question_embedding, embedding, metadata, 
                    corrected_response, corrected_queries, correction_note,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    score = EXCLUDED.score,
                    feedback_type = EXCLUDED.feedback_type,
                    comment = EXCLUDED.comment,
                    corrected_response = EXCLUDED.corrected_response,
                    corrected_queries = EXCLUDED.corrected_queries,
                    correction_note = EXCLUDED.correction_note,
                    embedding = EXCLUDED.embedding
            """, (
                feedback.id,
                feedback.session_id,
                feedback.question_id,
                feedback.question,
                feedback.response,
                feedback.feedback_type.value,
                feedback.score,
                feedback.comment,
                feedback.user_id,
                tool_calls_json,
                json.dumps(feedback.question_embedding) if feedback.question_embedding else None,
                embedding_str,  # pgvector format
                json.dumps(feedback.metadata) if feedback.metadata else None,
                feedback.corrected_response,
                json.dumps(feedback.corrected_queries) if feedback.corrected_queries else None,
                feedback.correction_note,
                feedback.created_at,
            ))
            conn.commit()
            return True
            
    except Exception as e:
        logger.error(f"❌ Failed to save feedback to PostgreSQL: {e}")
        return False
    finally:
        if conn:
            pool.putconn(conn)


def _load_from_postgres(
    session_id: Optional[str] = None,
    min_score: Optional[int] = None,
    limit: int = 100,
) -> List[Feedback]:
    """Load feedbacks from PostgreSQL"""
    pool = _get_pg_pool()
    if not pool:
        return []
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            query = "SELECT * FROM feedback WHERE 1=1"
            params: List[Any] = []
            
            if session_id:
                query += " AND session_id = %s"
                params.append(session_id)
            
            if min_score is not None:
                query += " AND score >= %s"
                params.append(min_score)
            
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            
            cur.execute(query, params)
            rows = cur.fetchall()
            
            feedbacks = []
            for row in rows:
                # Parse tool_calls JSON
                tool_calls_data = row[9] if row[9] else []
                if isinstance(tool_calls_data, str):
                    tool_calls_data = json.loads(tool_calls_data)
                
                tool_calls = [
                    ToolCall(
                        tool_name=tc.get("tool_name", ""),
                        tool_input=tc.get("tool_input", ""),
                        tool_output=tc.get("tool_output", ""),
                        duration_ms=tc.get("duration_ms"),
                        success=tc.get("success", True),
                    )
                    for tc in tool_calls_data
                ]
                
                feedbacks.append(Feedback(
                    id=row[0],
                    session_id=row[1],
                    question_id=row[2],
                    question=row[3] or "",
                    response=row[4] or "",
                    feedback_type=FeedbackType(row[5]) if row[5] else FeedbackType.NEUTRAL,
                    score=row[6] or 0,
                    comment=row[7],
                    user_id=row[8],
                    tool_calls=tool_calls,
                    question_embedding=json.loads(row[10]) if row[10] else None,
                    metadata=json.loads(row[11]) if row[11] else None,
                    created_at=row[13],
                    used_as_example_count=row[12] or 0,
                ))
            
            return feedbacks
            
    except Exception as e:
        logger.error(f"❌ Failed to load feedbacks from PostgreSQL: {e}")
        return []
    finally:
        if conn:
            pool.putconn(conn)


# ============================================================================
# EMBEDDING FOR SIMILARITY
# ============================================================================

def _get_question_embedding(question: str) -> Optional[List[float]]:
    """Get embedding for question (for similarity search)"""
    try:
        # Try OpenAI
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=question,
            )
            return response.data[0].embedding
        
        return None
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to get embedding: {e}")
        return None


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors"""
    if not a or not b or len(a) != len(b):
        return 0.0
    
    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


# ============================================================================
# CORE FUNCTIONS
# ============================================================================

def record_feedback(
    session_id: str,
    question_id: str,
    feedback_type: FeedbackType,
    question: str = "",
    response: str = "",
    comment: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    score: Optional[int] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    # Correction fields for learning from mistakes
    corrected_response: Optional[str] = None,
    corrected_queries: Optional[List[str]] = None,
    correction_note: Optional[str] = None,
) -> Optional[Feedback]:
    """
    Record user feedback with optional tool calls and corrections.
    
    Args:
        session_id: Session ID
        question_id: Question/trace ID
        feedback_type: Type of feedback
        question: Original question
        response: Agent response
        comment: User comment
        user_id: User ID
        metadata: Additional metadata
        score: Optional explicit score (-1, 0, 1)
        tool_calls: List of tool call dicts with tool_name, tool_input, tool_output
        corrected_response: Admin's corrected response
        corrected_queries: List of correct Cypher queries
        correction_note: Why the original was wrong
    
    Returns:
        Feedback object if successful, None otherwise
    """
    if not FEEDBACK_ENABLED:
        return None
    
    try:
        # Calculate score if not provided
        if score is None:
            if feedback_type in [FeedbackType.POSITIVE, FeedbackType.HELPFUL]:
                score = 1
            elif feedback_type in [FeedbackType.NEGATIVE, FeedbackType.NOT_HELPFUL, 
                                   FeedbackType.INCORRECT, FeedbackType.INCOMPLETE, 
                                   FeedbackType.OFF_TOPIC]:
                score = -1
            else:
                score = 0
        
        # Parse tool_calls
        parsed_tool_calls: List[ToolCall] = []
        if tool_calls:
            for tc in tool_calls:
                parsed_tool_calls.append(ToolCall(
                    tool_name=tc.get("tool_name", tc.get("tool", "")),
                    tool_input=str(tc.get("tool_input", tc.get("input", ""))),
                    tool_output=str(tc.get("tool_output", tc.get("output", ""))),
                    duration_ms=tc.get("duration_ms"),
                    success=tc.get("success", True),
                ))
        
        # Get embedding for few-shot (only for positive feedback)
        question_embedding = None
        if FEEDBACK_FEW_SHOT_ENABLED and score >= FEEDBACK_MIN_SCORE_FOR_FEWSHOT and question:
            question_embedding = _get_question_embedding(question)
        
        # Create feedback record
        feedback = Feedback(
            id=f"{session_id}_{question_id}_{datetime.now().timestamp()}",
            session_id=session_id,
            question_id=question_id,
            question=question if question else "",
            response=response if response else "",
            feedback_type=feedback_type,
            score=score,
            comment=comment,
            user_id=user_id,
            metadata=metadata,
            created_at=datetime.now(timezone.utc),
            tool_calls=parsed_tool_calls,
            question_embedding=question_embedding,
            # Correction fields
            corrected_response=corrected_response,
            corrected_queries=corrected_queries,
            correction_note=correction_note,
        )
        
        # Store in PostgreSQL (primary) or memory (fallback)
        if FEEDBACK_POSTGRES_ENABLED:
            saved = _save_to_postgres(feedback)
            if not saved:
                _feedback_store.append(feedback)
        else:
            _feedback_store.append(feedback)
        
        # Sync to Langfuse
        if FEEDBACK_LANGFUSE_SYNC:
            _sync_to_langfuse(feedback)
        
        logger.info(f"✅ Feedback recorded: {feedback_type.value} (score={score}) for session {session_id[:8]}")
        
        return feedback
        
    except Exception as e:
        logger.error(f"❌ Failed to record feedback: {e}")
        return None


def _sync_to_langfuse(feedback: Feedback) -> bool:
    """Sync feedback to Langfuse"""
    try:
        from src.shared.langfuse_client import get_langfuse
        
        langfuse = get_langfuse()
        if not langfuse:
            return False
        
        # Create score in Langfuse
        langfuse.score(  # type: ignore[union-attr]
            name="user_feedback",
            value=feedback.score,
            trace_id=feedback.question_id,
            comment=feedback.comment,
            data_type="NUMERIC",
        )
        
        # Also create categorical score
        langfuse.score(  # type: ignore[union-attr]
            name="feedback_type",
            value=feedback.feedback_type.value,
            trace_id=feedback.question_id,
            data_type="CATEGORICAL",
        )
        
        logger.debug(f"📊 Feedback synced to Langfuse: {feedback.question_id}")
        return True
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to sync feedback to Langfuse: {e}")
        return False


# ============================================================================
# FEW-SHOT LEARNING
# ============================================================================

def _search_similar_with_pgvector(
    embedding: List[float],
    min_score: int = 1,
    limit: int = 5,
    similarity_threshold: float = 0.7,
) -> List[Tuple[float, Feedback]]:
    """
    Search similar feedbacks using pgvector (native PostgreSQL vector search).
    Much faster than Python-based similarity calculation.
    
    Args:
        embedding: Query embedding vector
        min_score: Minimum feedback score
        limit: Max results
        similarity_threshold: Min cosine similarity (0-1)
    
    Returns:
        List of (similarity, Feedback) tuples sorted by similarity DESC
    """
    pool = _get_pg_pool()
    if not pool:
        return []
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            # Format embedding for pgvector
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            
            # pgvector cosine distance: 1 - cosine_similarity
            # So we need: 1 - distance >= threshold => distance <= 1 - threshold
            max_distance = 1 - similarity_threshold
            
            cur.execute("""
                SELECT 
                    id, session_id, question_id, question, response,
                    feedback_type, score, comment, user_id, tool_calls,
                    question_embedding, metadata, created_at, used_as_example_count,
                    corrected_response, corrected_queries, correction_note,
                    1 - (embedding <=> %s::vector) as similarity
                FROM feedback
                WHERE score >= %s
                  AND embedding IS NOT NULL
                  AND (embedding <=> %s::vector) <= %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (embedding_str, min_score, embedding_str, max_distance, embedding_str, limit))
            
            rows = cur.fetchall()
            
            results = []
            for row in rows:
                # Parse tool_calls
                tool_calls_data = row[9] or []
                if isinstance(tool_calls_data, str):
                    tool_calls_data = json.loads(tool_calls_data)
                
                tool_calls = [
                    ToolCall(
                        tool_name=tc.get("tool_name", ""),
                        tool_input=tc.get("tool_input", ""),
                        tool_output=tc.get("tool_output", ""),
                        duration_ms=tc.get("duration_ms"),
                        success=tc.get("success", True),
                    )
                    for tc in tool_calls_data
                ]
                
                fb = Feedback(
                    id=row[0],
                    session_id=row[1],
                    question_id=row[2],
                    question=row[3] or "",
                    response=row[4] or "",
                    feedback_type=FeedbackType(row[5]) if row[5] else FeedbackType.NEUTRAL,
                    score=row[6] or 0,
                    comment=row[7],
                    user_id=row[8],
                    tool_calls=tool_calls,
                    question_embedding=json.loads(row[10]) if row[10] else None,
                    metadata=json.loads(row[11]) if row[11] else None,
                    created_at=row[12],
                    used_as_example_count=row[13] or 0,
                    corrected_response=row[14],
                    corrected_queries=json.loads(row[15]) if row[15] else None,
                    correction_note=row[16],
                )
                
                similarity = row[17]  # Last column is similarity
                results.append((similarity, fb))
            
            logger.info(f"🔍 pgvector search found {len(results)} similar feedbacks")
            return results
            
    except Exception as e:
        logger.warning(f"⚠️ pgvector search failed: {e}, falling back to Python")
        return []
    finally:
        if conn:
            pool.putconn(conn)


def get_few_shot_examples(
    question: str,
    limit: int = 3,
    min_score: int = 1,
    similarity_threshold: float = 0.7,
) -> List[Dict[str, Any]]:
    """
    Get similar high-rated examples for few-shot learning.
    Uses pgvector for fast similarity search when available.
    
    Args:
        question: Current question to find similar examples for
        limit: Max number of examples to return
        min_score: Minimum feedback score (default: 1 = positive)
        similarity_threshold: Min similarity score (0-1)
    
    Returns:
        List of example dicts with question, response, tool_calls
    """
    if not FEEDBACK_FEW_SHOT_ENABLED:
        return []
    
    try:
        # Get embedding for current question
        question_embedding = _get_question_embedding(question)
        if not question_embedding:
            return []
        
        # Try pgvector first (fast, ~5-10ms)
        scored_feedbacks: List[Tuple[float, Feedback]] = []
        
        if FEEDBACK_POSTGRES_ENABLED:
            scored_feedbacks = _search_similar_with_pgvector(
                embedding=question_embedding,
                min_score=min_score,
                limit=limit,
                similarity_threshold=similarity_threshold,
            )
        
        # Fallback to Python-based search if pgvector failed or not available
        if not scored_feedbacks:
            if FEEDBACK_POSTGRES_ENABLED:
                feedbacks = _load_from_postgres(min_score=min_score, limit=100)
            else:
                feedbacks = [f for f in _feedback_store if f.score >= min_score]
            
            for fb in feedbacks:
                if fb.question_embedding:
                    similarity = _cosine_similarity(question_embedding, fb.question_embedding)
                    if similarity >= similarity_threshold:
                        scored_feedbacks.append((similarity, fb))
            
            # Sort by similarity (descending)
            scored_feedbacks.sort(key=lambda x: x[0], reverse=True)
            scored_feedbacks = scored_feedbacks[:limit]
        
        # Sort by similarity (descending)
        scored_feedbacks.sort(key=lambda x: x[0], reverse=True)
        
        # Build examples
        examples = []
        for similarity, fb in scored_feedbacks[:limit]:
            example = {
                "question": fb.question,
                "response": fb.response,
                "similarity": round(similarity, 3),
                "tool_calls": [
                    {
                        "tool": tc.tool_name,
                        "input": tc.tool_input,
                        "output": tc.tool_output[:200],  # Truncate for prompt
                    }
                    for tc in fb.tool_calls
                ],
            }
            examples.append(example)
            
            # Increment usage count (async, don't block)
            _increment_example_usage(fb.id)
        
        logger.info(f"📚 Found {len(examples)} few-shot examples for question")
        return examples
        
    except Exception as e:
        logger.error(f"❌ Failed to get few-shot examples: {e}")
        return []


def _increment_example_usage(feedback_id: Optional[str]) -> None:
    """Increment usage count for a feedback example"""
    if not feedback_id:
        return
    
    pool = _get_pg_pool()
    if not pool:
        return
    
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE feedback 
                SET used_as_example_count = used_as_example_count + 1 
                WHERE id = %s
            """, (feedback_id,))
            conn.commit()
        pool.putconn(conn)
    except Exception:
        pass  # Non-critical


def format_few_shot_prompt(examples: List[Dict[str, Any]], corrections: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Format few-shot examples for prompt injection.
    Includes both successful examples and corrected mistakes.
    
    Args:
        examples: List of example dicts from get_few_shot_examples()
        corrections: List of corrected examples (wrong query → right query)
    
    Returns:
        Formatted string to inject into agent prompt
    """
    if not examples and not corrections:
        return ""
    
    lines = []
    
    # Başarılı örnekler
    if examples:
        lines.append("📚 ÖNCEKİ BAŞARILI SORGU ÖRNEKLERİ:")
        
        for i, ex in enumerate(examples, 1):
            lines.append(f"\n--- Örnek {i} (benzerlik: {ex['similarity']:.2f}) ---")
            lines.append(f"Soru: {ex['question']}")
            
            if ex.get("tool_calls"):
                lines.append("Kullanılan Cypher sorguları:")
                for tc in ex["tool_calls"]:
                    if tc.get("tool") == "execute_cypher_query":
                        lines.append(f"```cypher\n{tc.get('input', '')}\n```")
            
            lines.append(f"Cevap: {ex['response'][:500]}...")
    
    # Düzeltilmiş örnekler (hatalardan öğrenme)
    if corrections:
        lines.append("\n\n⚠️ ÖNCEKİ HATALAR VE DÜZELTMELERİ:")
        lines.append("(Bu hataları YAPMA, düzeltilmiş versiyonları kullan)")
        
        for i, corr in enumerate(corrections, 1):
            lines.append(f"\n--- Düzeltme {i} ---")
            lines.append(f"Soru: {corr['question']}")
            
            if corr.get("wrong_queries"):
                lines.append("❌ YANLIŞ:")
                for wq in corr["wrong_queries"][:2]:
                    lines.append(f"```cypher\n{wq}\n```")
            
            if corr.get("correct_queries"):
                lines.append("✅ DOĞRU:")
                for cq in corr["correct_queries"]:
                    lines.append(f"```cypher\n{cq}\n```")
            
            if corr.get("correction_note"):
                lines.append(f"💡 Not: {corr['correction_note']}")
    
    lines.append("\n\n--- MEVCUT SORU ---")
    
    return "\n".join(lines)


async def generate_correction_with_llm(
    question: str,
    wrong_queries: List[str],
    user_feedback: str,
    schema_info: str = "",
) -> Tuple[List[str], str]:
    """
    Use Evaluator LLM to generate corrected queries from user feedback.
    
    Args:
        question: Original user question
        wrong_queries: List of wrong Cypher queries
        user_feedback: Simple user feedback like "Yıl filtresini önce uygula"
        schema_info: Optional graph schema
    
    Returns:
        Tuple of (corrected_queries, detailed_correction_note)
    """
    try:
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        prompt = f"""Sen bir Cypher sorgu düzeltme uzmanısın.

Kullanıcı bir soru sordu, agent yanlış sorgular üretti ve kullanıcı geri bildirim verdi.
Senin görevin doğru Cypher sorgularını oluşturmak.

## Orijinal Soru
{question}

## Yanlış Sorgular
{chr(10).join([f"```cypher{chr(10)}{q}{chr(10)}```" for q in wrong_queries])}

## Kullanıcı Geri Bildirimi
{user_feedback}

{f"## Graph Schema{chr(10)}{schema_info}" if schema_info else ""}

## Görev
1. Kullanıcının geri bildirimine göre sorguları düzelt
2. Doğru Cypher sorgularını yaz
3. Neden yanlış olduğunu ve nasıl düzelttiğini açıkla

Yanıtını şu formatta ver:
CORRECTED_QUERIES:
```cypher
<doğru sorgu 1>
```
```cypher
<doğru sorgu 2 (varsa)>
```

CORRECTION_NOTE:
<Neden yanlıştı ve nasıl düzeltildi açıklaması>
"""
        
        response = await client.chat.completions.create(
            model=os.getenv("EVALUATOR_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        
        content = response.choices[0].message.content or ""
        
        # Parse response
        corrected_queries = []
        correction_note = ""
        
        # Extract queries
        import re
        query_matches = re.findall(r'```cypher\s*(.*?)\s*```', content, re.DOTALL)
        corrected_queries = [q.strip() for q in query_matches if q.strip()]
        
        # Extract note
        if "CORRECTION_NOTE:" in content:
            correction_note = content.split("CORRECTION_NOTE:")[-1].strip()
        
        logger.info(f"🤖 Evaluator LLM generated {len(corrected_queries)} corrected queries")
        return corrected_queries, correction_note
        
    except Exception as e:
        logger.error(f"❌ Evaluator LLM error: {e}")
        return [], f"Auto-correction failed: {str(e)}"


def _search_corrections_with_pgvector(
    embedding: List[float],
    limit: int = 5,
    similarity_threshold: float = 0.7,
) -> List[Tuple[float, Feedback]]:
    """
    Search similar corrections using pgvector.
    Only returns feedbacks that have corrected_queries.
    """
    pool = _get_pg_pool()
    if not pool:
        return []
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            max_distance = 1 - similarity_threshold
            
            cur.execute("""
                SELECT 
                    id, session_id, question_id, question, response,
                    feedback_type, score, comment, user_id, tool_calls,
                    question_embedding, metadata, created_at, used_as_example_count,
                    corrected_response, corrected_queries, correction_note,
                    1 - (embedding <=> %s::vector) as similarity
                FROM feedback
                WHERE corrected_queries IS NOT NULL
                  AND embedding IS NOT NULL
                  AND (embedding <=> %s::vector) <= %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (embedding_str, embedding_str, max_distance, embedding_str, limit))
            
            rows = cur.fetchall()
            
            results = []
            for row in rows:
                tool_calls_data = row[9] or []
                if isinstance(tool_calls_data, str):
                    tool_calls_data = json.loads(tool_calls_data)
                
                tool_calls = [
                    ToolCall(
                        tool_name=tc.get("tool_name", ""),
                        tool_input=tc.get("tool_input", ""),
                        tool_output=tc.get("tool_output", ""),
                        duration_ms=tc.get("duration_ms"),
                        success=tc.get("success", True),
                    )
                    for tc in tool_calls_data
                ]
                
                fb = Feedback(
                    id=row[0],
                    session_id=row[1],
                    question_id=row[2],
                    question=row[3] or "",
                    response=row[4] or "",
                    feedback_type=FeedbackType(row[5]) if row[5] else FeedbackType.NEUTRAL,
                    score=row[6] or 0,
                    comment=row[7],
                    user_id=row[8],
                    tool_calls=tool_calls,
                    question_embedding=json.loads(row[10]) if row[10] else None,
                    metadata=json.loads(row[11]) if row[11] else None,
                    created_at=row[12],
                    used_as_example_count=row[13] or 0,
                    corrected_response=row[14],
                    corrected_queries=json.loads(row[15]) if row[15] else None,
                    correction_note=row[16],
                )
                
                similarity = row[17]
                results.append((similarity, fb))
            
            logger.info(f"🔍 pgvector corrections search found {len(results)} results")
            return results
            
    except Exception as e:
        logger.warning(f"⚠️ pgvector corrections search failed: {e}")
        return []
    finally:
        if conn:
            pool.putconn(conn)


def get_corrections_for_fewshot(
    question: str,
    limit: int = 2,
    similarity_threshold: float = 0.7,
) -> List[Dict[str, Any]]:
    """
    Get corrected examples (mistakes with fixes) for few-shot learning.
    Uses pgvector for fast similarity search.
    
    Args:
        question: Current question
        limit: Max corrections to return
        similarity_threshold: Min similarity
    
    Returns:
        List of correction dicts
    """
    if not FEEDBACK_FEW_SHOT_ENABLED:
        return []
    
    try:
        question_embedding = _get_question_embedding(question)
        if not question_embedding:
            return []
        
        # Try pgvector first
        scored: List[Tuple[float, Feedback]] = []
        
        if FEEDBACK_POSTGRES_ENABLED:
            scored = _search_corrections_with_pgvector(
                embedding=question_embedding,
                limit=limit,
                similarity_threshold=similarity_threshold,
            )
        
        # Fallback to Python-based search
        if not scored:
            if FEEDBACK_POSTGRES_ENABLED:
                feedbacks = _load_from_postgres(limit=100)
            else:
                feedbacks = _feedback_store
            
            corrected = [
                f for f in feedbacks 
                if f.corrected_queries and f.question_embedding
            ]
            
            for fb in corrected:
                if fb.question_embedding:
                    sim = _cosine_similarity(question_embedding, fb.question_embedding)
                    if sim >= similarity_threshold:
                        scored.append((sim, fb))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            scored = scored[:limit]
        
        # Build corrections
        corrections = []
        for sim, fb in scored[:limit]:
            # Extract wrong queries from tool_calls
            wrong_queries = []
            for tc in fb.tool_calls:
                if tc.tool_name == "execute_cypher_query":
                    try:
                        import json
                        input_data = json.loads(tc.tool_input) if isinstance(tc.tool_input, str) else tc.tool_input
                        if isinstance(input_data, dict) and "cypher" in input_data:
                            wrong_queries.append(input_data["cypher"])
                    except:
                        pass
            
            corrections.append({
                "question": fb.question,
                "similarity": sim,
                "wrong_queries": wrong_queries,
                "correct_queries": fb.corrected_queries,
                "correction_note": fb.correction_note,
            })
        
        if corrections:
            logger.info(f"📝 Found {len(corrections)} corrections for few-shot")
        
        return corrections
        
    except Exception as e:
        logger.error(f"❌ Failed to get corrections: {e}")
        return []


# ============================================================================
# QUERY FUNCTIONS
# ============================================================================

def get_feedback_for_session(session_id: str) -> List[Feedback]:
    """Get all feedback for a session"""
    if FEEDBACK_POSTGRES_ENABLED:
        return _load_from_postgres(session_id=session_id)
    return [f for f in _feedback_store if f.session_id == session_id]


# Alias for API compatibility
get_session_feedback = get_feedback_for_session


def get_feedback_stats(session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get feedback statistics.
    
    Args:
        session_id: Optional filter by session
        
    Returns:
        Statistics dictionary
    """
    # Get feedbacks
    if FEEDBACK_POSTGRES_ENABLED:
        feedbacks = _load_from_postgres(session_id=session_id, limit=1000)
    elif session_id:
        feedbacks = [f for f in _feedback_store if f.session_id == session_id]
    else:
        feedbacks = _feedback_store
    
    if not feedbacks:
        return {
            "enabled": FEEDBACK_ENABLED,
            "postgres_enabled": FEEDBACK_POSTGRES_ENABLED,
            "few_shot_enabled": FEEDBACK_FEW_SHOT_ENABLED,
            "total": 0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "positive_rate": 0,
            "average_score": 0,
            "by_type": {},
            "with_tool_calls": 0,
        }
    
    positive = sum(1 for f in feedbacks if f.score > 0)
    negative = sum(1 for f in feedbacks if f.score < 0)
    neutral = sum(1 for f in feedbacks if f.score == 0)
    with_tool_calls = sum(1 for f in feedbacks if f.tool_calls)
    
    total = len(feedbacks)
    avg_score = sum(f.score for f in feedbacks) / total if total > 0 else 0
    
    # Group by feedback type
    by_type: Dict[str, int] = {}
    for f in feedbacks:
        key = f.feedback_type.value
        by_type[key] = by_type.get(key, 0) + 1
    
    return {
        "enabled": FEEDBACK_ENABLED,
        "postgres_enabled": FEEDBACK_POSTGRES_ENABLED,
        "few_shot_enabled": FEEDBACK_FEW_SHOT_ENABLED,
        "total": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "positive_rate": round(positive / total * 100, 1) if total > 0 else 0,
        "average_score": round(avg_score, 2),
        "by_type": by_type,
        "with_tool_calls": with_tool_calls,
    }


def get_negative_feedback(limit: int = 10) -> List[Feedback]:
    """Get recent negative feedback for review"""
    if FEEDBACK_POSTGRES_ENABLED:
        feedbacks = _load_from_postgres(min_score=-1, limit=limit)
        return [f for f in feedbacks if f.score < 0]
    
    negative = [f for f in _feedback_store if f.score < 0]
    negative.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
    return negative[:limit]


def get_positive_examples_for_training(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get positive examples for training/export.
    Useful for fine-tuning or dataset creation.
    """
    if FEEDBACK_POSTGRES_ENABLED:
        feedbacks = _load_from_postgres(min_score=1, limit=limit)
    else:
        feedbacks = [f for f in _feedback_store if f.score > 0]
        feedbacks.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
        feedbacks = feedbacks[:limit]
    
    return [
        {
            "question": f.question,
            "response": f.response,
            "tool_calls": [
                {"tool": tc.tool_name, "input": tc.tool_input, "output": tc.tool_output}
                for tc in f.tool_calls
            ],
            "score": f.score,
            "feedback_type": f.feedback_type.value,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in feedbacks
    ]


# ============================================================================
# FASTAPI ENDPOINT HELPERS
# ============================================================================

def create_feedback_response(success: bool, message: str = "") -> Dict[str, Any]:
    """Create standard feedback API response"""
    return {
        "success": success,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# FastAPI model (for API endpoint)
try:
    from pydantic import BaseModel
    
    class FeedbackRequest(BaseModel):
        """Feedback API request model"""
        session_id: str
        question_id: str
        feedback_type: str  # positive, negative, helpful, etc.
        comment: Optional[str] = None
        question: Optional[str] = None
        response: Optional[str] = None
        tool_calls: Optional[List[Dict[str, Any]]] = None
        
except ImportError:
    FeedbackRequest = None  # type: ignore[misc, assignment]
