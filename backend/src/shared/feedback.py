# -*- coding: utf-8 -*-
"""
Feedback Loop Module - User Feedback Integration

LAYER 10 Features:
- Thumbs up/down API endpoint integration
- Feedback storage (PostgreSQL)
- Langfuse feedback annotation
- Feedback analytics

Kullanım:
    from src.shared.feedback import (
        record_feedback,
        get_feedback_stats,
        FeedbackType,
    )
    
    # Feedback kaydet
    record_feedback(
        session_id="xxx",
        question_id="yyy",
        feedback_type=FeedbackType.POSITIVE,
        comment="Çok yardımcı oldu",
    )

Environment Variables:
    FEEDBACK_ENABLED: Enable/disable feedback (default: true)
    FEEDBACK_LANGFUSE_SYNC: Sync feedback to Langfuse (default: true)
"""

import os
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

# Environment configuration
FEEDBACK_ENABLED = os.getenv("FEEDBACK_ENABLED", "true").lower() in ("true", "1", "yes")
FEEDBACK_LANGFUSE_SYNC = os.getenv("FEEDBACK_LANGFUSE_SYNC", "true").lower() in ("true", "1", "yes")


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
class Feedback:
    """Feedback model"""
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


# In-memory storage (for development)
_feedback_store: List[Feedback] = []


def record_feedback(
    session_id: str,
    question_id: str,
    feedback_type: FeedbackType,
    question: str = "",
    response: str = "",
    comment: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record user feedback.
    
    Args:
        session_id: Session ID
        question_id: Question/trace ID
        feedback_type: Type of feedback
        question: Original question
        response: Agent response
        comment: User comment
        user_id: User ID
        metadata: Additional metadata
    
    Returns:
        Success status
    """
    if not FEEDBACK_ENABLED:
        return False
    
    try:
        # Calculate score
        if feedback_type in [FeedbackType.POSITIVE, FeedbackType.HELPFUL]:
            score = 1
        elif feedback_type in [FeedbackType.NEGATIVE, FeedbackType.NOT_HELPFUL, 
                               FeedbackType.INCORRECT, FeedbackType.INCOMPLETE, 
                               FeedbackType.OFF_TOPIC]:
            score = -1
        else:
            score = 0
        
        # Create feedback record
        feedback = Feedback(
            id=f"{session_id}_{question_id}_{datetime.now().timestamp()}",
            session_id=session_id,
            question_id=question_id,
            question=question[:500] if question else "",
            response=response[:1000] if response else "",
            feedback_type=feedback_type,
            score=score,
            comment=comment,
            user_id=user_id,
            metadata=metadata,
            created_at=datetime.now(timezone.utc),
        )
        
        # Store in memory (TODO: PostgreSQL storage)
        _feedback_store.append(feedback)
        
        # Sync to Langfuse
        if FEEDBACK_LANGFUSE_SYNC:
            _sync_to_langfuse(feedback)
        
        logger.info(f"✅ Feedback recorded: {feedback_type.value} for session {session_id[:8]}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to record feedback: {e}")
        return False


def _sync_to_langfuse(feedback: Feedback) -> bool:
    """Sync feedback to Langfuse"""
    try:
        from src.shared.langfuse_client import get_langfuse
        
        langfuse = get_langfuse()
        if not langfuse:
            return False
        
        # Create score in Langfuse
        langfuse.score(
            name="user_feedback",
            value=feedback.score,
            trace_id=feedback.question_id,
            comment=feedback.comment,
            data_type="NUMERIC",
        )
        
        # Also create categorical score
        langfuse.score(
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


def get_feedback_for_session(session_id: str) -> List[Feedback]:
    """Get all feedback for a session"""
    return [f for f in _feedback_store if f.session_id == session_id]


def get_feedback_stats() -> Dict[str, Any]:
    """Get feedback statistics"""
    if not _feedback_store:
        return {
            "total": 0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "average_score": 0,
        }
    
    positive = sum(1 for f in _feedback_store if f.score > 0)
    negative = sum(1 for f in _feedback_store if f.score < 0)
    neutral = sum(1 for f in _feedback_store if f.score == 0)
    
    total = len(_feedback_store)
    avg_score = sum(f.score for f in _feedback_store) / total if total > 0 else 0
    
    # Group by feedback type
    by_type = {}
    for f in _feedback_store:
        key = f.feedback_type.value
        by_type[key] = by_type.get(key, 0) + 1
    
    return {
        "enabled": FEEDBACK_ENABLED,
        "total": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "positive_rate": round(positive / total * 100, 1) if total > 0 else 0,
        "average_score": round(avg_score, 2),
        "by_type": by_type,
    }


def get_negative_feedback(limit: int = 10) -> List[Feedback]:
    """Get recent negative feedback for review"""
    negative = [f for f in _feedback_store if f.score < 0]
    negative.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
    return negative[:limit]


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
        
except ImportError:
    FeedbackRequest = None

