"""
Request Context Variables for Log Correlation

Bu modül, request scope'da session_id ve question_id'yi tutarak
tüm loglarda otomatik korelasyon sağlar.

Kullanım:
    from src.shared.context import current_session_id, current_question_id, set_request_context
    
    # API endpoint başında:
    set_request_context(session_id="abc", question_id="xyz")
    
    # Herhangi bir yerde context'i oku:
    session = current_session_id.get()
    question = current_question_id.get()
"""

from contextvars import ContextVar
from typing import Optional
import uuid


# Global context variables - request scope'da tutulur
current_session_id: ContextVar[str] = ContextVar('session_id', default='unknown')
current_question_id: ContextVar[str] = ContextVar('question_id', default='unknown')


def set_request_context(
    session_id: Optional[str] = None, 
    question_id: Optional[str] = None
) -> tuple[str, str]:
    """
    Request context'ini set et.
    
    Args:
        session_id: Chat session ID (frontend'den gelir)
        question_id: Soru ID (her mesaj için unique)
        
    Returns:
        (session_id, question_id) tuple - kullanılan değerler
    """
    # Session ID yoksa 'unknown' kullan
    sid = session_id if session_id else 'unknown'
    current_session_id.set(sid)
    
    # Question ID yoksa otomatik oluştur
    qid = question_id if question_id else str(uuid.uuid4())[:8]
    current_question_id.set(qid)
    
    return sid, qid


def get_request_context() -> dict:
    """
    Mevcut request context'ini döndür.
    
    Returns:
        dict with session_id and question_id
    """
    return {
        'session_id': current_session_id.get('unknown'),
        'question_id': current_question_id.get('unknown')
    }


def clear_request_context():
    """
    Request context'ini temizle (request sonunda çağrılabilir).
    """
    current_session_id.set('unknown')
    current_question_id.set('unknown')


