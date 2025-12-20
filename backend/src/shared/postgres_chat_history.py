"""
PostgreSQL-based Chat Message History (Sync Version)

Neo4j'den PostgreSQL'e taşınan conversation history yönetimi.
LangChain BaseChatMessageHistory interface'ini implement eder.
psycopg2 kullanarak sync çalışır - event loop sorunları yok.
"""

import logging
import os
import json
import threading
from datetime import datetime
from typing import List, Optional, Sequence
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

logger = logging.getLogger(__name__)

# Global connection pool
_pg_pool: Optional[pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
_tables_initialized = False


def get_pg_pool() -> pool.ThreadedConnectionPool:
    """Get or create PostgreSQL connection pool (thread-safe)"""
    global _pg_pool
    
    if _pg_pool is None:
        with _pool_lock:
            if _pg_pool is None:
                db_url = os.getenv(
                    "QUEUE_DB_URL", 
                    "postgresql://postgres:postgres@localhost:5432/llm_graph_builder"
                )
                
                # Parse connection string
                # Format: postgresql://user:password@host:port/database
                try:
                    from urllib.parse import urlparse, unquote
                    parsed = urlparse(db_url)
                    
                    # URL decode password (handles special characters like ? ! @ etc.)
                    decoded_password = unquote(parsed.password) if parsed.password else 'postgres'
                    
                    _pg_pool = pool.ThreadedConnectionPool(
                        minconn=1,
                        maxconn=5,  # Reduced to share with other pools
                        host=parsed.hostname or 'localhost',
                        port=parsed.port or 5432,
                        user=parsed.username or 'postgres',
                        password=decoded_password,
                        database=parsed.path.lstrip('/') or 'llm_graph_builder'
                    )
                    logger.info(f"✅ PostgreSQL chat history pool oluşturuldu: {parsed.hostname}:{parsed.port}")
                except Exception as e:
                    logger.error(f"❌ PostgreSQL pool oluşturma hatası: {e}")
                    raise
    
    return _pg_pool


@contextmanager
def get_connection():
    """Get a connection from the pool (context manager)"""
    pool = get_pg_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_chat_history_tables():
    """Create chat_messages table if not exists"""
    global _tables_initialized
    
    if _tables_initialized:
        return
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Messages table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(100) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session 
                ON chat_messages(session_id);
                
                CREATE INDEX IF NOT EXISTS idx_chat_messages_created 
                ON chat_messages(session_id, created_at);
            """)
            
            # Sessions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id VARCHAR(100) PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    message_count INTEGER DEFAULT 0,
                    metadata JSONB DEFAULT '{}'
                );
            """)
    
    _tables_initialized = True
    logger.info("✅ PostgreSQL chat history tabloları hazır")


class PostgresChatMessageHistory(BaseChatMessageHistory):
    """
    PostgreSQL-based chat message history (sync version).
    LangChain'in BaseChatMessageHistory interface'ini implement eder.
    """
    
    def __init__(self, session_id: str, window: int = 50):
        """
        Initialize PostgreSQL chat history.
        
        Args:
            session_id: Unique session identifier
            window: Number of message pairs to keep (default 50 = 100 messages)
        """
        self.session_id = session_id
        self.window = window
        self._ensure_tables()
        self._ensure_session()
    
    def _ensure_tables(self):
        """Ensure tables exist"""
        init_chat_history_tables()
    
    def _ensure_session(self):
        """Ensure session exists in database"""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_sessions (session_id, created_at, updated_at)
                    VALUES (%s, NOW(), NOW())
                    ON CONFLICT (session_id) DO UPDATE SET updated_at = NOW()
                """, (self.session_id,))
    
    @property
    def messages(self) -> List[BaseMessage]:  # type: ignore[override]
        """
        Retrieve messages from PostgreSQL.
        Returns the last `window * 2` messages.
        """
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                limit = self.window * 2
                cur.execute("""
                    SELECT role, content, metadata, created_at
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (self.session_id, limit))
                
                rows = cur.fetchall()
        
        # Reverse to get chronological order
        rows = list(reversed(rows))
        
        messages = []
        for row in rows:
            role = row['role']
            content = row['content']
            
            if role == 'human':
                messages.append(HumanMessage(content=content))
            elif role == 'ai':
                messages.append(AIMessage(content=content))
            elif role == 'system':
                messages.append(SystemMessage(content=content))
        
        return messages
    
    def add_message(self, message: BaseMessage) -> None:
        """Add a message to the history."""
        # Determine role
        if isinstance(message, HumanMessage):
            role = 'human'
        elif isinstance(message, AIMessage):
            role = 'ai'
        elif isinstance(message, SystemMessage):
            role = 'system'
        else:
            role = 'unknown'
        
        content = message.content
        metadata = json.dumps(getattr(message, 'additional_kwargs', {}))
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Insert message
                cur.execute("""
                    INSERT INTO chat_messages (session_id, role, content, metadata)
                    VALUES (%s, %s, %s, %s::jsonb)
                """, (self.session_id, role, content, metadata))
                
                # Update session message count
                cur.execute("""
                    UPDATE chat_sessions 
                    SET message_count = message_count + 1, updated_at = NOW()
                    WHERE session_id = %s
                """, (self.session_id,))
        
        logger.debug(f"✅ Mesaj kaydedildi: session={self.session_id}, role={role}")
    
    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        """Add multiple messages"""
        for message in messages:
            self.add_message(message)
    
    def clear(self) -> None:
        """Clear all messages for this session"""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM chat_messages WHERE session_id = %s
                """, (self.session_id,))
                
                cur.execute("""
                    UPDATE chat_sessions 
                    SET message_count = 0, updated_at = NOW()
                    WHERE session_id = %s
                """, (self.session_id,))
        
        logger.info(f"✅ Session temizlendi: {self.session_id}")


# ==========================================
# Session Cache Manager
# ==========================================

class PostgresSessionChatHistory:
    """
    PostgreSQL chat history için session cache manager.
    """
    
    _sessions: dict = {}
    _lock = threading.Lock()
    
    @classmethod
    def get_or_create_session(cls, session_id: str, window: int = 50) -> PostgresChatMessageHistory:
        """
        Get or create a PostgreSQL chat history instance.
        
        Args:
            session_id: Session identifier
            window: Number of message pairs to retrieve
            
        Returns:
            PostgresChatMessageHistory instance
        """
        with cls._lock:
            if session_id not in cls._sessions:
                logger.info(f"🆕 PostgreSQL session oluşturuluyor: {session_id}")
                cls._sessions[session_id] = PostgresChatMessageHistory(
                    session_id=session_id,
                    window=window
                )
            else:
                logger.debug(f"📦 Cached PostgreSQL session kullanılıyor: {session_id}")
            
            return cls._sessions[session_id]
    
    @classmethod
    def clear_session(cls, session_id: str) -> None:
        """Clear a specific session from cache and database"""
        with cls._lock:
            if session_id in cls._sessions:
                cls._sessions[session_id].clear()
                del cls._sessions[session_id]
                logger.info(f"🗑️ Session silindi: {session_id}")
            else:
                # Session cache'de yok ama DB'de olabilir
                try:
                    history = PostgresChatMessageHistory(session_id=session_id)
                    history.clear()
                    logger.info(f"🗑️ Session DB'den silindi: {session_id}")
                except Exception as e:
                    logger.warning(f"⚠️ Session silme hatası: {e}")
    
    @classmethod
    def clear_all_sessions(cls) -> None:
        """Clear all sessions from cache"""
        with cls._lock:
            logger.info(f"🗑️ Tüm session cache temizleniyor: {len(cls._sessions)} session")
            cls._sessions.clear()
    
    @classmethod
    def get_active_sessions(cls) -> List[str]:
        """Get list of active session IDs"""
        with cls._lock:
            return list(cls._sessions.keys())


# ==========================================
# Convenience Functions
# ==========================================

def create_postgres_chat_message_history(
    session_id: str, 
    write_access: bool = True,
    window: int = 50
) -> PostgresChatMessageHistory:
    """
    Create or get a PostgreSQL chat message history.
    Neo4j versiyonunun drop-in replacement'ı.
    
    Args:
        session_id: Session identifier
        write_access: Ignored (always True for PostgreSQL)
        window: Number of message pairs to retrieve
        
    Returns:
        PostgresChatMessageHistory instance
    """
    return PostgresSessionChatHistory.get_or_create_session(session_id, window)


def clear_postgres_session(session_id: str) -> None:
    """Clear a session from PostgreSQL"""
    PostgresSessionChatHistory.clear_session(session_id)


def get_session_message_count(session_id: str) -> int:
    """Get message count for a session"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT message_count FROM chat_sessions WHERE session_id = %s
            """, (session_id,))
            row = cur.fetchone()
            return row[0] if row else 0
