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


def _safe_json_parse(value: Any, default: Any = None) -> Any:
    """
    Safely parse JSON - handles both string and already-parsed values.
    PostgreSQL JSONB columns may return already-parsed Python objects.
    """
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        # Already parsed by psycopg2
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


# Environment configuration
FEEDBACK_ENABLED = os.getenv("FEEDBACK_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_LANGFUSE_SYNC = os.getenv("FEEDBACK_LANGFUSE_SYNC", "true").lower() in ("true", "1", "yes")
FEEDBACK_POSTGRES_ENABLED = os.getenv("FEEDBACK_POSTGRES_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_FEW_SHOT_ENABLED = os.getenv("FEEDBACK_FEW_SHOT_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_MIN_SCORE_FOR_FEWSHOT = int(os.getenv("FEEDBACK_MIN_SCORE_FOR_FEWSHOT", "1"))

# LLM-as-Judge: Cypher sorgularını değerlendirme
LLM_JUDGE_ENABLED = os.getenv("LLM_JUDGE_ENABLED", "true").lower() in ("true", "1", "yes")
LLM_JUDGE_MODEL = os.getenv("LLM_JUDGE_MODEL", "gpt-4o-mini")
LLM_JUDGE_PROMPT_NAME = os.getenv("LLM_JUDGE_PROMPT_NAME", "llm-judge-evaluation")  # Langfuse prompt adı


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
    pgvector_available = False
    
    try:
        conn = pool.getconn()
        
        # pgvector extension (ayrı transaction'da dene)
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                conn.commit()
                pgvector_available = True
                logger.info("✅ pgvector extension ready")
        except Exception as e:
            conn.rollback()  # Transaction'ı temizle
            logger.warning(f"⚠️ pgvector extension not available: {e}")
            logger.info("ℹ️ Continuing without pgvector (Python fallback will be used)")
        
        with conn.cursor() as cur:
            
            # Feedback table - pgvector olmadan temel tablo
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
            conn.commit()
            
            # AUTO-MIGRATION: Add missing columns to existing tables
            migrations = [
                # Correction fields (always available)
                ("corrected_response", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS corrected_response TEXT;"),
                ("corrected_queries", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS corrected_queries JSONB;"),
                ("correction_note", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS correction_note TEXT;"),
                ("used_as_example_count", "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS used_as_example_count INTEGER DEFAULT 0;"),
            ]
            
            for column_name, migration_sql in migrations:
                try:
                    cur.execute(migration_sql)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.debug(f"Migration skipped for {column_name}: {e}")
            
            # pgvector column ve index (sadece pgvector varsa)
            if pgvector_available:
                try:
                    cur.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS embedding vector(1536);")
                    conn.commit()
                    logger.info("✅ pgvector embedding column ready")
                except Exception as e:
                    conn.rollback()
                    logger.warning(f"⚠️ pgvector column creation failed: {e}")
                
                try:
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_feedback_embedding 
                        ON feedback USING hnsw (embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 64);
                    """)
                    conn.commit()
                    logger.info("✅ pgvector HNSW index ready")
                except Exception as e:
                    conn.rollback()
                    logger.warning(f"⚠️ HNSW index creation failed: {e}")
            
                # Migrate existing embeddings from JSONB to vector format
                try:
                    cur.execute("""
                        UPDATE feedback 
                        SET embedding = question_embedding::text::vector
                        WHERE embedding IS NULL 
                          AND question_embedding IS NOT NULL
                          AND jsonb_array_length(question_embedding) = 1536;
                    """)
                    conn.commit()
                    migrated = cur.rowcount
                    if migrated > 0:
                        logger.info(f"✅ Migrated {migrated} existing embeddings to pgvector format")
                except Exception as e:
                    conn.rollback()
                    logger.debug(f"Embedding migration skipped: {e}")
            
            logger.info(f"✅ Feedback table ready (pgvector: {'enabled' if pgvector_available else 'disabled'})")
    except Exception as e:
        logger.error(f"❌ Failed to create/migrate feedback table: {e}")
    finally:
        if conn:
            pool.putconn(conn)


def _has_pgvector_column() -> bool:
    """Check if embedding column (pgvector) exists in feedback table"""
    pool = _get_pg_pool()
    if not pool:
        return False
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'feedback' AND column_name = 'embedding';
            """)
            return cur.fetchone() is not None
    except:
        return False
    finally:
        if conn:
            pool.putconn(conn)


def _save_to_postgres(feedback: Feedback) -> bool:
    """Save feedback to PostgreSQL with optional pgvector embedding"""
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
            
            # Check if pgvector column exists
            has_embedding = _has_pgvector_column()
            
            if has_embedding and feedback.question_embedding:
                # With pgvector
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
                    embedding_str,
                    json.dumps(feedback.metadata) if feedback.metadata else None,
                    feedback.corrected_response,
                    json.dumps(feedback.corrected_queries) if feedback.corrected_queries else None,
                    feedback.correction_note,
                    feedback.created_at or datetime.now(timezone.utc),
                ))
            else:
                # Without pgvector
                cur.execute("""
                    INSERT INTO feedback (
                        id, session_id, question_id, question, response,
                        feedback_type, score, comment, user_id, tool_calls,
                        question_embedding, metadata, 
                        corrected_response, corrected_queries, correction_note,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        score = EXCLUDED.score,
                        feedback_type = EXCLUDED.feedback_type,
                        comment = EXCLUDED.comment,
                        corrected_response = EXCLUDED.corrected_response,
                        corrected_queries = EXCLUDED.corrected_queries,
                        correction_note = EXCLUDED.correction_note
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
                    json.dumps(feedback.metadata) if feedback.metadata else None,
                    feedback.corrected_response,
                    json.dumps(feedback.corrected_queries) if feedback.corrected_queries else None,
                    feedback.correction_note,
                    feedback.created_at or datetime.now(timezone.utc),
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
                # Parse tool_calls (psycopg2 may auto-parse JSONB)
                tool_calls_data = _safe_json_parse(row[9], [])
                
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
                    question_embedding=_safe_json_parse(row[10]),
                    metadata=_safe_json_parse(row[11]),
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

def _check_recent_feedback(question: str, hours: int = 1) -> Optional["Feedback"]:
    """Check if a similar feedback was recorded recently (within N hours)."""
    pool = _get_pg_pool()
    if not pool:
        return None
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, session_id, question_id, question, response, 
                       feedback_type, score, comment, created_at
                FROM feedback
                WHERE question = %s
                  AND created_at > NOW() - INTERVAL '%s hours'
                ORDER BY created_at DESC
                LIMIT 1
            """, (question, hours))
            
            row = cur.fetchone()
            if row:
                return Feedback(
                    id=row[0],
                    session_id=row[1],
                    question_id=row[2],
                    question=row[3],
                    response=row[4],
                    feedback_type=FeedbackType(row[5]) if row[5] else FeedbackType.NEUTRAL,
                    score=row[6] or 0,
                    comment=row[7],
                    created_at=row[8],
                )
        return None
    except Exception as e:
        logger.debug(f"Duplicate check failed: {e}")
        return None
    finally:
        if conn:
            pool.putconn(conn)


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
        # Duplicate kontrolü: Aynı soru için son 1 saat içinde kayıt var mı?
        skip_duplicate = metadata.get("skip_duplicate_check", False) if metadata else False
        if not skip_duplicate and question and FEEDBACK_POSTGRES_ENABLED:
            existing = _check_recent_feedback(question, hours=1)
            if existing:
                logger.debug(f"⏭️ Duplicate feedback skipped for: {question[:50]}...")
                return existing  # Mevcut feedback'i döndür
        
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
        
        # Create score in Langfuse (v3 API uses create_score)
        try:
            # Try v3 API first
            langfuse.create_score(  # type: ignore[union-attr]
                name="user_feedback",
                value=feedback.score,
                trace_id=feedback.question_id,
                comment=feedback.comment,
                data_type="NUMERIC",
            )
            langfuse.create_score(  # type: ignore[union-attr]
                name="feedback_type",
                value=feedback.feedback_type.value,
                trace_id=feedback.question_id,
                data_type="CATEGORICAL",
            )
        except AttributeError:
            # Fallback to v2 API
            langfuse.score(  # type: ignore[union-attr]
                name="user_feedback",
                value=feedback.score,
                trace_id=feedback.question_id,
                comment=feedback.comment,
                data_type="NUMERIC",
            )
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
                # Parse tool_calls (psycopg2 may auto-parse JSONB)
                tool_calls_data = _safe_json_parse(row[9], [])
                
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
                    question_embedding=_safe_json_parse(row[10]),
                    metadata=_safe_json_parse(row[11]),
                    created_at=row[12],
                    used_as_example_count=row[13] or 0,
                    corrected_response=row[14],
                    corrected_queries=_safe_json_parse(row[15]),
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
                ] if fb.tool_calls else [],
                # LLM-Judge strateji ve değerlendirmesi
                "strategy": fb.correction_note if fb.correction_note else None,
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
    
    # Teknik strateji önerileri
    if examples:
        for ex in examples:
            strategy = ex.get("strategy")
            if strategy:
                lines.append("📋 BENZERİ SORULAR İÇİN TEKNİK STRATEJİ:")
                
                # Strateji JSON formatında olabilir
                strategy_data = strategy
                if isinstance(strategy, str):
                    try:
                        strategy_data = json.loads(strategy)
                    except (json.JSONDecodeError, TypeError):
                        # Eski format (düz string)
                        lines.append(strategy.strip())
                        break
                
                if isinstance(strategy_data, dict):
                    # Yeni teknik format
                    if strategy_data.get("summary"):
                        lines.append(f"\n🎯 ÖZET: {strategy_data['summary']}")
                    
                    if strategy_data.get("recommended_path"):
                        lines.append("\n📍 ÖNERİLEN YOL:")
                        for step in strategy_data["recommended_path"]:
                            lines.append(f"  {step}")
                    
                    if strategy_data.get("key_nodes"):
                        lines.append(f"\n🔵 KULLANILACAK NODE'LAR: {', '.join(strategy_data['key_nodes'])}")
                    
                    if strategy_data.get("key_relationships"):
                        lines.append(f"🔗 KULLANILACAK İLİŞKİLER: {', '.join(strategy_data['key_relationships'])}")
                    
                    if strategy_data.get("key_properties"):
                        lines.append(f"📌 ÖNEMLİ PROPERTY'LER: {', '.join(strategy_data['key_properties'])}")
                    
                    if strategy_data.get("pitfalls"):
                        lines.append("\n⚠️ DİKKAT (YAPMA):")
                        for pitfall in strategy_data["pitfalls"]:
                            lines.append(f"  ❌ {pitfall}")
                    
                    if strategy_data.get("alternative_approaches"):
                        lines.append("\n💡 ALTERNATİF YAKLAŞIMLAR:")
                        for alt in strategy_data["alternative_approaches"]:
                            lines.append(f"  → {alt}")
                else:
                    lines.append(str(strategy_data).strip())
                
                break  # Sadece en benzer örneğin stratejisini al
    
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


async def evaluate_cypher_queries(
    question: str,
    tool_calls: List[Dict[str, Any]],
    response: str = "",
    schema_info: str = "",
) -> Dict[str, Any]:
    """
    LLM-as-Judge: Evaluate if Cypher queries correctly answer the question.
    
    Env: LLM_JUDGE_ENABLED=true/false (default: true)
    
    Args:
        question: User's original question
        tool_calls: List of tool calls with cypher queries and results
        response: Agent's final response
        schema_info: Optional graph schema
    
    Returns:
        Dict with:
        - score: 0.0-1.0 (1.0 = perfect, 0.0 = completely wrong)
        - is_correct: bool
        - issues: List of identified problems
        - suggested_query: Recommended correct query (if issues found)
        - explanation: Detailed explanation
    """
    # Check if LLM-Judge is enabled
    if not LLM_JUDGE_ENABLED:
        return {
            "score": 1.0,
            "is_correct": True,
            "issues": [],
            "suggested_query": None,
            "explanation": "LLM-Judge disabled",
            "skipped": True,
        }
    
    try:
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # Extract Cypher queries from tool_calls
        queries_info = []
        for tc in tool_calls:
            tool_name = tc.get("tool_name", tc.get("tool", ""))
            if tool_name == "execute_cypher_query":
                tool_input = tc.get("tool_input", tc.get("input", ""))
                tool_output = tc.get("tool_output", tc.get("output", ""))
                
                # Parse input if JSON string
                if isinstance(tool_input, str):
                    try:
                        import json
                        parsed = json.loads(tool_input)
                        cypher = parsed.get("cypher", tool_input)
                    except:
                        cypher = tool_input
                else:
                    cypher = tool_input.get("cypher", str(tool_input)) if isinstance(tool_input, dict) else str(tool_input)
                
                queries_info.append({
                    "cypher": cypher,
                    "result": str(tool_output)[:1000],  # Truncate long results
                })
        
        if not queries_info:
            return {
                "score": 0.5,
                "is_correct": True,
                "issues": ["No Cypher queries found in tool calls"],
                "suggested_query": None,
                "explanation": "No queries to evaluate",
            }
        
        # Format queries for prompt
        queries_text = ""
        for i, q in enumerate(queries_info, 1):
            queries_text += f"\n### Sorgu {i}\n```cypher\n{q['cypher']}\n```\n**Sonuç:** {q['result'][:500]}...\n"
        
        prompt = f"""Sen bir Cypher sorgu değerlendirme uzmanısın. 
Görevin: Yapılan Cypher sorgularının kullanıcının sorusuna doğru cevap verip vermediğini değerlendir.

## Kullanıcı Sorusu
{question}

## Yapılan Cypher Sorguları ve Sonuçları
{queries_text}

## Agent'ın Final Cevabı
{response[:1000] if response else "(Cevap yok)"}

{f"## Graph Schema{chr(10)}{schema_info}" if schema_info else ""}

## Değerlendirme Kriterleri
1. **Filtreleme Sırası**: WHERE koşulları LIMIT'ten önce mi uygulanmış?
2. **Yıl/Tarih Filtresi**: Soruda yıl belirtilmişse, sorguda doğru filtrelenmiş mi?
3. **Sıralama**: ORDER BY gerekiyorsa doğru mu?
4. **İlişkiler**: Doğru node'lar ve relationship'ler kullanılmış mı?
5. **Sonuç Tutarlılığı**: Sorgu sonucu soruya uygun mu?

## Yanıt Formatı (JSON)
{{
    "score": 0.0-1.0,
    "is_correct": true/false,
    "issues": ["sorun1", "sorun2"],
    "suggested_query": "MATCH ... (düzeltilmiş sorgu, sorun varsa)",
    "explanation": "Kısa açıklama"
}}

SADECE JSON döndür, başka bir şey yazma."""

        response_obj = await client.chat.completions.create(
            model=os.getenv("EVALUATOR_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        
        content = response_obj.choices[0].message.content or "{}"
        
        import json
        result = json.loads(content)
        
        # Ensure required fields
        result.setdefault("score", 0.5)
        result.setdefault("is_correct", result["score"] >= 0.7)
        result.setdefault("issues", [])
        result.setdefault("suggested_query", None)
        result.setdefault("explanation", "")
        
        logger.info(f"🧑‍⚖️ LLM-as-Judge: score={result['score']}, correct={result['is_correct']}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ LLM-as-Judge error: {e}")
        return {
            "score": 0.5,
            "is_correct": True,  # Default to positive on error
            "issues": [f"Evaluation failed: {str(e)}"],
            "suggested_query": None,
            "explanation": f"Error: {str(e)}",
        }


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
                tool_calls_data = _safe_json_parse(row[9], [])
                
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
                    question_embedding=_safe_json_parse(row[10]),
                    metadata=_safe_json_parse(row[11]),
                    created_at=row[12],
                    used_as_example_count=row[13] or 0,
                    corrected_response=row[14],
                    corrected_queries=_safe_json_parse(row[15]),
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


# =============================================================================
# BLACKBOARD-BASED LLM JUDGE EVALUATION
# =============================================================================

async def evaluate_from_blackboard(
    blackboard_dir: str,
    question: str,
    response: str = "",
    session_id: str = "",
    question_id: str = "",
    similarity_threshold: float = 0.95,
) -> Dict[str, Any]:
    """
    LLM-Judge: Blackboard dosyalarını okuyarak tüm tool çağrılarını değerlendirir.
    
    Her sorgu için kısa öneri verir ve genel strateji önerir.
    %95 benzerlik ile daha önce değerlendirilmiş soruları atlar.
    
    Args:
        blackboard_dir: Blackboard dosyalarının bulunduğu dizin
        question: Kullanıcının sorusu
        response: Agent'ın final cevabı
        session_id: Session ID
        question_id: Question ID
        similarity_threshold: Duplicate check threshold (default: 0.95)
    
    Returns:
        Dict with:
        - evaluated: bool - Değerlendirme yapıldı mı
        - skipped_duplicate: bool - Duplicate olduğu için atlandı mı
        - query_evaluations: List[Dict] - Her sorgu için değerlendirme
        - strategy: str - Genel strateji önerisi
        - overall_score: float - Genel skor (0-1)
        - feedback_id: str - Kaydedilen feedback ID
    """
    import glob
    
    result = {
        "evaluated": False,
        "skipped_duplicate": False,
        "query_evaluations": [],
        "strategy": "",
        "overall_score": 0.5,
        "feedback_id": None,
    }
    
    if not LLM_JUDGE_ENABLED:
        result["skipped"] = True
        return result
    
    # 1. Benzer feedback var mı kontrol et (update için + önceki stratejiyi al)
    existing_feedback_id: Optional[str] = None
    previous_strategy: Optional[str] = None  # Önceki stratejiyi LLM-Judge'a göster
    try:
        question_embedding = _get_question_embedding(question)
        if question_embedding and FEEDBACK_POSTGRES_ENABLED:
            similar = _search_similar_with_pgvector(
                embedding=question_embedding,
                min_score=-999,  # Tüm score'ları kontrol et
                limit=1,
                similarity_threshold=similarity_threshold,
            )
            if similar:
                similarity, existing_fb = similar[0]
                if similarity >= similarity_threshold and existing_fb and existing_fb.id:
                    existing_feedback_id = existing_fb.id
                    # Önceki stratejiyi al (correction_note alanında saklanıyor)
                    if existing_fb.correction_note:
                        previous_strategy = existing_fb.correction_note
                        logger.info(f"📝 Previous strategy found: {previous_strategy[:100]}...")
                    logger.info(f"📝 Similar feedback found (similarity={similarity:.3f}): {existing_fb.id[:8]} - will update")
    except Exception as e:
        logger.warning(f"⚠️ Similar feedback check failed: {e}")
    
    # 2. Blackboard dosyalarını oku
    try:
        import os as os_module
        
        # _blackboard.txt ana özet
        blackboard_file = os_module.path.join(blackboard_dir, "_blackboard.txt")
        blackboard_content = ""
        if os_module.path.exists(blackboard_file):
            with open(blackboard_file, "r", encoding="utf-8") as f:
                blackboard_content = f.read()
        
        # Tüm .txt dosyaları (_blackboard.txt hariç)
        all_txt_files = sorted(glob.glob(os_module.path.join(blackboard_dir, "*.txt")))
        step_files = [f for f in all_txt_files if not os_module.path.basename(f).startswith("_")]
        steps_content = []
        
        for step_file in step_files:
            filename = os_module.path.basename(step_file)
            with open(step_file, "r", encoding="utf-8") as f:
                content = f.read()
            steps_content.append({
                "filename": filename,
                "content": content[:3000],  # Truncate very long files
            })
        
        if not steps_content:
            logger.warning(f"⚠️ No step files found in {blackboard_dir}")
            return result
            
    except Exception as e:
        logger.error(f"❌ Failed to read blackboard files: {e}")
        return result
    
    # 3. LLM-Judge prompt oluştur
    steps_text = ""
    for i, step in enumerate(steps_content, 1):
        steps_text += f"\n### {step['filename']}\n```\n{step['content']}\n```\n"
    
    # Önceki strateji varsa prompt'a ekle
    previous_strategy_section = ""
    if previous_strategy:
        previous_strategy_section = f"""
## ÖNCEKİ STRATEJİ (Bu soru için daha önce belirlenen strateji)
{previous_strategy}

⚠️ GÖREV: Agent bu stratejiyi takip etti mi? Stratejiyi güncelle veya onayla.
"""
    
    # 3a. Langfuse'dan agent system prompt'unu al (kuralları görmek için)
    agent_system_prompt_section = ""
    try:
        from .langfuse_client import get_prompt
        
        # Agent'ın system prompt'unu çek (react-agent-system gibi)
        agent_prompt = get_prompt(
            name="react-agent-system",  # Agent'ın kullandığı prompt
            prompt_type="text",
            label="production",
            cache_enabled=True,
        )
        if agent_prompt:
            raw_prompt = getattr(agent_prompt, 'prompt', str(agent_prompt))
            agent_system_prompt_section = f"""
## AGENT KURALLARI (System Prompt)
Aşağıdaki kurallara uyulup uyulmadığını değerlendir:
```
{raw_prompt}
```
"""
            logger.info(f"📋 Agent system prompt loaded from Langfuse for evaluation")
    except Exception as e:
        logger.debug(f"Agent system prompt not available from Langfuse: {e}")
    
    # 3b. LLM-Judge prompt'unu Langfuse'dan çek veya fallback kullan
    prompt = None
    try:
        from .langfuse_client import get_prompt
        
        llm_judge_prompt = get_prompt(
            name=LLM_JUDGE_PROMPT_NAME,  # env: LLM_JUDGE_PROMPT_NAME
            prompt_type="text",
            label="production",
            cache_enabled=True,
        )
        if llm_judge_prompt:
            # Langfuse prompt'unu compile et (variables ile)
            prompt = llm_judge_prompt.compile(
                question=question,
                blackboard_content=blackboard_content,
                steps_text=steps_text,
                response=response[:1500] if response else "(Cevap yok)",
                previous_strategy_section=previous_strategy_section,
                agent_system_prompt_section=agent_system_prompt_section,
                has_previous_strategy="true" if previous_strategy else "false",
            )
            logger.info(f"📋 LLM-Judge prompt loaded from Langfuse: {LLM_JUDGE_PROMPT_NAME}")
    except Exception as e:
        logger.debug(f"LLM-Judge prompt not available from Langfuse: {e}")
    
    # Fallback: Langfuse'dan prompt alınamazsa hardcoded prompt kullan
    if not prompt:
        prompt = f"""Sen bir Neo4j Cypher sorgu değerlendirme ve optimizasyon uzmanısın.

## GÖREV
1. Aşağıdaki soru için yapılan tüm Cypher sorgularını DEĞERLENDİR
2. Her sorgu için KISA ama TEKNİK değerlendirme yap
3. ÖNEMLİ: Sonraki sorgular için TEKNİK STRATEJİ öner:
   - Hangi Node label'ları kullanılmalı
   - Hangi ilişkiler (relationship types) takip edilmeli  
   - Hangi property'ler filtrelenmeli
   - Sorgu sırası nasıl olmalı
   - Alternatif yaklaşımlar neler
{f"4. Önceki strateji ile karşılaştır ve uyumluluk değerlendir." if previous_strategy else ""}

{agent_system_prompt_section}

## KULLANICI SORUSU
{question}
{previous_strategy_section}
## BLACKBOARD ÖZETİ
{blackboard_content}

## TOOL ÇAĞRILARI (STEP DOSYALARI - SIRALI)
{steps_text}

## AGENT'IN FİNAL CEVABI
{response[:1500] if response else "(Cevap yok)"}

## YANIT FORMATI (JSON)
{{
    "overall_score": 0.0-1.0,
    "followed_previous_strategy": true|false|null,
    "followed_system_rules": true|false,
    "query_evaluations": [
        {{
            "step": "01_step_name",
            "status": "success|empty|error",
            "verdict": "✅ Doğru|⚠️ Kısmen|❌ Yanlış",
            "short_note": "Kısa teknik açıklama (max 80 karakter)"
        }}
    ],
    "strategy": {{
        "summary": "Genel yaklaşım özeti (1-2 cümle)",
        "recommended_path": [
            "1. İlk adım: Customer/Policyholder node'larını bul (name CONTAINS veya =~ regex)",
            "2. İkinci adım: Policy ilişkilerini takip et (-[:HAS_POLICY]->)",
            "3. Üçüncü adım: Coverage/Teminat bilgilerini al (-[:HAS_COVERAGE]->)"
        ],
        "key_nodes": ["Customer", "Policy", "Coverage", "InsuranceCompany"],
        "key_relationships": ["HAS_POLICY", "HAS_COVERAGE", "INSURED_BY"],
        "key_properties": ["name", "coverage_type", "premium"],
        "pitfalls": ["Doğrudan Coverage araması yerine Policy üzerinden git", "LIMIT'i WHERE'den önce kullanma"],
        "alternative_approaches": ["Embedding search ile metin içeriğinde ara", "Farklı entity varyasyonlarını dene"]
    }}
}}

ÖNEMLİ:
- Strateji bölümü TEKNİK ve SPESİFİK olmalı
- Şemadaki gerçek node ve relationship isimlerini kullan
- Gelecek sorgular için somut yol haritası ver
- SADECE JSON döndür"""

    # 4. LLM çağrısı
    try:
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        llm_response = await client.chat.completions.create(
            model=os.getenv("EVALUATOR_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        
        content = llm_response.choices[0].message.content or "{}"
        evaluation = json.loads(content)
        
        result["evaluated"] = True
        result["query_evaluations"] = evaluation.get("query_evaluations", [])
        result["strategy"] = evaluation.get("strategy", "")
        result["overall_score"] = evaluation.get("overall_score", 0.5)
        
        logger.info(f"🧑‍⚖️ Blackboard evaluation: score={result['overall_score']}, steps={len(result['query_evaluations'])}")
        
    except Exception as e:
        logger.error(f"❌ LLM evaluation failed: {e}")
        return result
    
    # 5. Feedback olarak PostgreSQL'e kaydet
    try:
        is_correct = result["overall_score"] >= 0.7
        feedback_score = 1 if is_correct else -1
        
        # Strateji artık bir dictionary - JSON olarak kaydet
        # correction_note = strateji (tam teknik detaylar)
        # query_evaluations = metadata'da (ayrı)
        strategy_data = result.get("strategy", {})
        if isinstance(strategy_data, dict):
            # Teknik stratejiyi JSON olarak sakla
            strategy_only = json.dumps(strategy_data, ensure_ascii=False)
        else:
            # Eski format (string) uyumu
            strategy_only = str(strategy_data)
        
        # Eğer benzer feedback varsa update et, yoksa yeni oluştur
        if existing_feedback_id:
            # UPDATE mevcut feedback
            updated = _update_feedback_strategy(
                feedback_id=existing_feedback_id,
                new_score=feedback_score,
                new_strategy=strategy_only,
                new_evaluations=result["query_evaluations"],
                new_overall_score=result["overall_score"],
            )
            if updated:
                result["feedback_id"] = existing_feedback_id
                result["updated_existing"] = True
                logger.info(f"🔄 Blackboard feedback UPDATED: {existing_feedback_id[:8]}... (score={feedback_score})")
            else:
                logger.warning(f"⚠️ Failed to update feedback {existing_feedback_id[:8]}")
        else:
            # CREATE yeni feedback
            feedback = record_feedback(
                session_id=session_id,
                question_id=question_id,
                feedback_type=FeedbackType.POSITIVE if is_correct else FeedbackType.INCORRECT,
                score=feedback_score,
                comment=f"LLM-Judge blackboard evaluation (score={result['overall_score']:.2f})",
                question=question,
                response=response,
                user_id="llm-judge-blackboard",
                tool_calls=None,  # Blackboard zaten tüm detayları içeriyor
                metadata={
                    "source": "llm_judge_blackboard",
                    "blackboard_dir": blackboard_dir,
                    "overall_score": result["overall_score"],
                    "query_evaluations": result["query_evaluations"],
                },
                correction_note=strategy_only,
            )
            
            if feedback and feedback.id:
                result["feedback_id"] = feedback.id
                logger.info(f"📝 Blackboard feedback CREATED: {feedback.id[:8]}... (score={feedback_score})")
            
    except Exception as e:
        logger.warning(f"⚠️ Failed to save blackboard feedback: {e}")
    
    return result


def get_blackboard_dir(session_id: str, question_id: str) -> str:
    """
    Blackboard dizin yolunu döndürür.
    
    Args:
        session_id: Session ID (first 8 chars used)
        question_id: Question ID (first 8 chars used)
    
    Returns:
        Blackboard directory path
    """
    import os as os_module
    
    # Backend root'u bul
    current_dir = os_module.path.dirname(os_module.path.abspath(__file__))
    backend_root = os_module.path.dirname(os_module.path.dirname(current_dir))
    
    # Blackboard path
    session_short = session_id[:8] if session_id else "unknown"
    question_short = question_id[:8] if question_id else "unknown"
    
    return os_module.path.join(
        backend_root, 
        "agent_findings", 
        "react",
        session_short,
        question_short
    )


def _update_feedback_strategy(
    feedback_id: str,
    new_score: int,
    new_strategy: str,
    new_evaluations: List[Dict[str, Any]],
    new_overall_score: float,
) -> bool:
    """
    Mevcut feedback'in strateji ve değerlendirmelerini günceller.
    
    LLM-Judge her çalıştığında, benzer sorular için mevcut feedback'i
    günceller böylece strateji sürekli iyileşir.
    
    Args:
        feedback_id: Güncellenecek feedback ID
        new_score: Yeni score (-1, 0, 1)
        new_strategy: Yeni strateji metni
        new_evaluations: Yeni query değerlendirmeleri
        new_overall_score: Yeni genel skor (0-1)
    
    Returns:
        True if successful, False otherwise
    """
    pool = _get_pg_pool()
    if not pool:
        return False
    
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            # Mevcut metadata'yı al
            cur.execute("SELECT metadata FROM feedback WHERE id = %s", (feedback_id,))
            row = cur.fetchone()
            
            if not row:
                logger.warning(f"⚠️ Feedback not found: {feedback_id}")
                return False
            
            # Metadata güncelle
            current_metadata = _safe_json_parse(row[0], {})
            current_metadata["overall_score"] = new_overall_score
            current_metadata["query_evaluations"] = new_evaluations
            current_metadata["last_updated"] = datetime.now(timezone.utc).isoformat()
            current_metadata["update_count"] = current_metadata.get("update_count", 0) + 1
            
            # Update query
            cur.execute("""
                UPDATE feedback
                SET score = %s,
                    correction_note = %s,
                    metadata = %s,
                    feedback_type = %s
                WHERE id = %s
            """, (
                new_score,
                new_strategy,
                json.dumps(current_metadata),
                FeedbackType.POSITIVE.value if new_score >= 0 else FeedbackType.INCORRECT.value,
                feedback_id,
            ))
            conn.commit()
            
            logger.info(f"🔄 Feedback updated: {feedback_id[:8]}... (update_count={current_metadata['update_count']})")
            return True
            
    except Exception as e:
        logger.error(f"❌ Failed to update feedback: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            pool.putconn(conn)
