"""
Skill Agent - Async Description Generator

This module provides an asynchronous agent that generates skill descriptions
for blackboard steps. It runs in the background and doesn't block the main agent.

Key Features:
- Async execution (non-blocking)
- Uses gpt-4o-mini for cost efficiency
- Generates: description, tags, intent
- Updates blackboard via update_skill_description()

Usage:
    from src.shared.skill_agent import generate_skill_description_async
    
    # Fire and forget - doesn't block
    asyncio.create_task(generate_skill_description_async(
        step_id=123,
        question_text="Akis GYO'nun kira kaybi teminati?",
        cypher_query="MATCH (c:Customer)...",
        status="success",
        record_count=5,
        result_preview=[{"fileName": "policy.pdf"}]
    ))

Environment Variables:
    SKILL_AGENT_ENABLED: Enable/disable skill agent (default: true)
    SKILL_AGENT_MODEL: Model to use (default: gpt-4o-mini)
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Configuration
SKILL_AGENT_ENABLED = os.getenv("SKILL_AGENT_ENABLED", "true").lower() in ("true", "1", "yes")
SKILL_AGENT_MODEL = os.getenv("SKILL_AGENT_MODEL", "gpt-4o-mini")

# Skill generation prompt
SKILL_PROMPT = """Sen bir sorgu analiz asistanısın. Verilen Cypher sorgusu ve sonuçlarını analiz edip kısa bir açıklama oluştur.

## SORGU BİLGİLERİ

Kullanıcı Sorusu: {question_text}

Cypher Sorgusu:
```cypher
{cypher_query}
```

Durum: {status} ({record_count} kayıt)

Sonuç Önizleme:
{result_preview}

## GÖREV

Bu sorgu için şunları oluştur:

1. **description**: Sorgunun ne yaptığını ve sonucunu açıklayan 1 cümle (Türkçe, max 100 karakter)
   - Başarılı: Ne bulunduğunu belirt
   - Başarısız: Neden başarısız olabileceğini belirt

2. **tags**: İlgili anahtar kelimeler listesi (5-10 adet)
   - Şirket/kişi isimleri
   - Arama terimleri
   - Veri tipleri (customer, policy, document vb.)

3. **intent**: Sorgunun amacı (şunlardan biri seç):
   - entity_search: Entity/kayıt arama
   - variation_search: İsim/terim varyasyonları arama
   - content_search: Belge içeriği arama (embedding)
   - relationship_traverse: İlişki takibi
   - aggregation: Toplama/sayma
   - metadata_lookup: Metadata sorgulama

## ÇIKTI FORMATI

Sadece JSON döndür, başka bir şey yazma:

```json
{{
  "description": "...",
  "tags": ["tag1", "tag2", ...],
  "intent": "..."
}}
```
"""


async def generate_skill_description_async(
    step_id: int,
    question_text: str,
    cypher_query: str,
    status: str,
    record_count: int,
    result_preview: Optional[List[Dict]] = None,
) -> bool:
    """
    Asynchronously generate and store skill description.
    
    This function should be called with asyncio.create_task() to not block.
    
    Args:
        step_id: Step ID in blackboard
        question_text: User's question
        cypher_query: The Cypher query executed
        status: 'success', 'empty', or 'failed'
        record_count: Number of records returned
        result_preview: First few results for context
    
    Returns:
        True if successful
    """
    if not SKILL_AGENT_ENABLED:
        logger.debug("Skill agent disabled, skipping")
        return False
    
    try:
        # Import here to avoid circular imports
        from src.shared.session_blackboard import update_skill_description
        
        # Format result preview
        preview_str = "Sonuç yok"
        if result_preview:
            preview_lines = []
            for i, r in enumerate(result_preview[:3], 1):
                preview_lines.append(f"  {i}. {json.dumps(r, ensure_ascii=False)[:200]}")
            preview_str = "\n".join(preview_lines)
        
        # Build prompt
        prompt = SKILL_PROMPT.format(
            question_text=question_text,
            cypher_query=cypher_query[:1000],  # Truncate long queries
            status=status,
            record_count=record_count,
            result_preview=preview_str,
        )
        
        # Call LLM
        result = await _call_llm_async(prompt)
        
        if not result:
            logger.warning(f"⚠️ Skill agent: No result for step {step_id}")
            return False
        
        # Parse JSON response
        try:
            # Extract JSON from response (might have markdown code block)
            json_str = result
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0].strip()
            
            data = json.loads(json_str)
            
            description = data.get("description", "")[:500]
            tags = data.get("tags", [])[:15]
            intent = data.get("intent", "entity_search")
            
            # Validate intent
            valid_intents = [
                "entity_search", "variation_search", "content_search",
                "relationship_traverse", "aggregation", "metadata_lookup"
            ]
            if intent not in valid_intents:
                intent = "entity_search"
            
            # Update blackboard
            success = update_skill_description(
                step_id=step_id,
                skill_description=description,
                skill_tags=tags,
                intent=intent,
            )
            
            if success:
                logger.info(f"✅ Skill description generated for step {step_id}: {description[:50]}...")
            
            return success
            
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Skill agent: JSON parse error for step {step_id}: {e}")
            
            # Fallback: create basic description
            basic_desc = _create_basic_description(status, record_count, cypher_query)
            basic_tags = _extract_basic_tags(question_text, cypher_query)
            
            return update_skill_description(
                step_id=step_id,
                skill_description=basic_desc,
                skill_tags=basic_tags,
                intent="entity_search",
            )
            
    except Exception as e:
        logger.error(f"❌ Skill agent error for step {step_id}: {e}")
        return False


async def _call_llm_async(prompt: str) -> Optional[str]:
    """Call LLM asynchronously using available provider."""
    try:
        # Try OpenAI first
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return await _call_openai_async(prompt, openai_key)
        
        # Try Anthropic
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            return await _call_anthropic_async(prompt, anthropic_key)
        
        logger.warning("⚠️ No LLM API key found for skill agent")
        return None
        
    except Exception as e:
        logger.error(f"❌ LLM call failed: {e}")
        return None


async def _call_openai_async(prompt: str, api_key: str) -> Optional[str]:
    """Call OpenAI API asynchronously."""
    try:
        import httpx
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": SKILL_AGENT_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.warning(f"⚠️ OpenAI API error: {response.status_code}")
                return None
                
    except Exception as e:
        logger.error(f"❌ OpenAI async call failed: {e}")
        return None


async def _call_anthropic_async(prompt: str, api_key: str) -> Optional[str]:
    """Call Anthropic API asynchronously."""
    try:
        import httpx
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-3-haiku-20240307",  # Fast and cheap
                    "max_tokens": 500,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["content"][0]["text"]
            else:
                logger.warning(f"⚠️ Anthropic API error: {response.status_code}")
                return None
                
    except Exception as e:
        logger.error(f"❌ Anthropic async call failed: {e}")
        return None


def _create_basic_description(status: str, record_count: int, cypher_query: str) -> str:
    """Create a basic description without LLM."""
    cypher_lower = cypher_query.lower()
    
    if status == "success":
        if "embedding" in cypher_lower or "vector" in cypher_lower:
            return f"Semantic arama ile {record_count} içerik bulundu"
        elif "contains" in cypher_lower or "ilike" in cypher_lower:
            return f"Metin arama ile {record_count} kayıt bulundu"
        elif "customer" in cypher_lower:
            return f"Müşteri araması: {record_count} kayıt"
        elif "document" in cypher_lower or "chunk" in cypher_lower:
            return f"Belge araması: {record_count} kayıt"
        else:
            return f"Sorgu başarılı: {record_count} kayıt"
    elif status == "empty":
        return "Sorgu sonuç döndürmedi - filtreler çok dar olabilir"
    else:
        return "Sorgu başarısız - syntax veya bağlantı hatası olabilir"


def _extract_basic_tags(question_text: str, cypher_query: str) -> List[str]:
    """Extract basic tags without LLM."""
    import re
    
    tags = set()
    
    # Common node types
    node_types = ["Customer", "Document", "Chunk", "Policy", "Company", "Person"]
    for nt in node_types:
        if nt.lower() in cypher_query.lower():
            tags.add(nt.lower())
    
    # Extract quoted strings from query (potential search terms)
    quoted = re.findall(r"['\"]([^'\"]{3,30})['\"]", cypher_query)
    for q in quoted[:5]:
        if not q.startswith("$"):
            tags.add(q.lower())
    
    # Extract capitalized words from question (potential entities)
    capitals = re.findall(r'\b[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)*\b', question_text)
    for c in capitals[:5]:
        tags.add(c)
    
    # Query type tags
    if "embedding" in cypher_query.lower() or "vector" in cypher_query.lower():
        tags.add("semantic")
        tags.add("embedding")
    if "contains" in cypher_query.lower():
        tags.add("fulltext")
    if "match" in cypher_query.lower() and "->" in cypher_query:
        tags.add("relationship")
    
    return list(tags)[:15]


# Synchronous wrapper for fire-and-forget usage
def schedule_skill_generation(
    step_id: int,
    question_text: str,
    cypher_query: str,
    status: str,
    record_count: int,
    result_preview: Optional[List[Dict]] = None,
) -> None:
    """
    Schedule skill generation in background (fire-and-forget).
    
    This is the main entry point for the main agent to use.
    Creates an async task that runs independently.
    """
    if not SKILL_AGENT_ENABLED:
        return
    
    try:
        # Get or create event loop
        try:
            loop = asyncio.get_running_loop()
            # If we're in an async context, create task
            loop.create_task(generate_skill_description_async(
                step_id=step_id,
                question_text=question_text,
                cypher_query=cypher_query,
                status=status,
                record_count=record_count,
                result_preview=result_preview,
            ))
        except RuntimeError:
            # No running loop - run in thread
            import threading
            
            def run_async():
                asyncio.run(generate_skill_description_async(
                    step_id=step_id,
                    question_text=question_text,
                    cypher_query=cypher_query,
                    status=status,
                    record_count=record_count,
                    result_preview=result_preview,
                ))
            
            thread = threading.Thread(target=run_async, daemon=True)
            thread.start()
            
    except Exception as e:
        logger.error(f"❌ Failed to schedule skill generation: {e}")
