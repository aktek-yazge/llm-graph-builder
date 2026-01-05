"""
LLM-based DSL → Cypher Generator

GPT-5-mini kullanarak DSL'i Cypher'a çevirir.
Rule-based DSLCompiler'a alternatif olarak kullanılabilir.

LangChain ChatOpenAI kullanılır - react_agent.py ile aynı pattern.
Token takibi LangChain ve Langfuse tarafından otomatik yapılır.
"""

import os
import json
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from dotenv import load_dotenv
from pydantic import SecretStr

# Load environment variables
load_dotenv()

# LangChain imports - react_agent.py ile aynı
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    ChatOpenAI = None
    HumanMessage = None
    SystemMessage = None

# Langfuse LLM Observability - react_agent.py ile aynı
try:
    from src.shared.langfuse_client import get_langfuse, flush_langfuse, get_langfuse_callback_handler
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    get_langfuse = None
    flush_langfuse = None
    get_langfuse_callback_handler = None

logger = logging.getLogger(__name__)

# Cached model instance
_llm_instance: Optional[Any] = None


def get_cypher_llm(model: str = "gpt-5-mini", reasoning_effort: str = "low") -> Any:
    """
    LangChain ChatOpenAI instance al - react_agent.py ile aynı pattern.
    
    GPT-5-mini reasoning model olduğu için temperature kullanılmaz.
    Token takibi LangChain tarafından otomatik yapılır.
    
    Args:
        model: Model adı (default: gpt-5-mini)
        reasoning_effort: Reasoning effort seviyesi (low, medium, high)
    
    Returns:
        ChatOpenAI instance
    """
    global _llm_instance
    
    if not LANGCHAIN_AVAILABLE or ChatOpenAI is None:
        raise ImportError("LangChain not available. Install langchain-openai.")
    
    # Her model için farklı instance gerekebilir, şimdilik cache'leme
    if _llm_instance is not None:
        return _llm_instance
    
    api_key = os.environ.get("OPENAI_API_KEY")
    
    # GPT-5-mini reasoning model - react_agent.py ile aynı pattern
    # Reasoning modellerde temperature KULLANILMAZ
    if "gpt-5" in model.lower():
        logger.info(f"🔧 LLM Cypher Generator: {model}, reasoning={reasoning_effort}, summary=auto")
        _llm_instance = ChatOpenAI(
            model=model,
            api_key=SecretStr(api_key) if api_key else None,
            reasoning={"effort": reasoning_effort, "summary": "auto"},
        )
    else:
        # Non-reasoning modeller için temperature kullan
        logger.info(f"🔧 LLM Cypher Generator: {model}, temperature=0")
        _llm_instance = ChatOpenAI(
            model=model,
            api_key=SecretStr(api_key) if api_key else None,
            temperature=0.0,
        )
    
    return _llm_instance


@dataclass
class LLMCompilationResult:
    """LLM derleme sonucu - DSLCompiler.CompilationResult ile uyumlu"""
    cypher: str
    params: Dict[str, Any] = field(default_factory=dict)
    requires_embedding: bool = False
    query_text: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    
    # LLM-specific fields
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0


# System prompt for Cypher generation
CYPHER_GENERATOR_PROMPT = """Sen bir Neo4j Cypher sorgu uzmanısın. Sana verilen DSL (Domain Specific Language) JSON'ını Neo4j Cypher sorgusuna çevirmelisin.

## KURALLAR

1. **MATCH Clause**: Traversal'ı takip et, node label'ları ve relationship'leri doğru yaz
2. **WHERE Clause**: Filters'ı doğru operatörlerle çevir
3. **RETURN Clause**: return_spec'teki node ve property'leri döndür
4. **LIMIT**: DSL'deki limit değerini kullan

## FILTER OPERATÖRLERİ
- equals → apoc.text.clean(property) = apoc.text.clean(value)
- not_equals → apoc.text.clean(property) <> apoc.text.clean(value)
- contains → apoc.text.clean(property) CONTAINS apoc.text.clean(value)
- starts_with → apoc.text.clean(property) STARTS WITH apoc.text.clean(value)
- ends_with → apoc.text.clean(property) ENDS WITH apoc.text.clean(value)
- gt, lt, gte, lte → >, <, >=, <=
- in → property IN [values]
- is_null → property IS NULL
- is_not_null → property IS NOT NULL

NOT: String karşılaştırmalarında HER ZAMAN apoc.text.clean() kullan. Bu fonksiyon boşlukları, özel karakterleri ve case'i normalize eder.

## SEMANTIC SEARCH (search_content intent)
Eğer DSL'de "semantic_search" varsa, Vector Index kullan:

```cypher
CALL db.index.vector.queryNodes('vector', <limit * 5>, $embedding_vector)
YIELD node AS chunk, score
WHERE score > <similarity_threshold>
MATCH <traversal from chunk>
<additional filters>
RETURN chunk.text AS text, score, <other properties>
ORDER BY score DESC
LIMIT <limit>
```

## ÖNEMLİ
- String karşılaştırmalarında HER ZAMAN apoc.text.clean() kullan
- Parameterized query KULLANMA - değerleri inline yaz
- DISTINCT kullanımına dikkat et (return_spec.distinct = true ise)
- String değerleri tek tırnak içinde yaz: 'value'

## ÖRNEK CYPHER
```cypher
MATCH (p:Policy)-[:HAS_COVERAGE]->(c:Coverage)
WHERE apoc.text.clean(c.name) CONTAINS apoc.text.clean('fire')
RETURN DISTINCT p.policyNumber, c.name
LIMIT 10
```

## ÇIKTI FORMATI
Sadece Cypher sorgusunu döndür, başka bir şey yazma. Markdown code block KULLANMA."""


async def generate_cypher(
    dsl_json: str,
    schema_info: Optional[str] = None,
    model: str = "gpt-5-mini",
    parent_span: Optional[Any] = None
) -> LLMCompilationResult:
    """
    DSL JSON'ı GPT-5-mini ile Cypher'a çevir.
    
    LangChain ChatOpenAI kullanır - react_agent.py ile aynı pattern.
    Token takibi LangChain ve Langfuse tarafından otomatik yapılır.
    
    Args:
        dsl_json: DSL JSON string
        schema_info: Opsiyonel schema bilgisi (node labels, relationships)
        model: Kullanılacak model (default: gpt-5-mini)
        parent_span: Langfuse parent span - child generation olarak bağlanır
    
    Returns:
        LLMCompilationResult: Cypher sorgusu ve metadata
    """
    start_time = time.time()
    
    if not LANGCHAIN_AVAILABLE:
        return LLMCompilationResult(
            cypher="",
            warnings=["❌ LangChain not available"],
            latency_ms=(time.time() - start_time) * 1000
        )
    
    try:
        # Parse DSL to extract semantic search info
        dsl_dict = json.loads(dsl_json)
        
        # Build user message
        user_message = f"DSL:\n```json\n{dsl_json}\n```"
        
        if schema_info:
            user_message = f"SCHEMA:\n{schema_info}\n\n{user_message}"
        
        # Add semantic search hint if present
        requires_embedding = False
        query_text = None
        
        if dsl_dict.get("semantic_search"):
            requires_embedding = True
            query_text = dsl_dict["semantic_search"].get("query_text", "")
            user_message += f"\n\n⚠️ Bu sorgu semantic search içeriyor. Vector index kullan."
        
        # Get LangChain model - reasoning effort from env
        reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "low")
        llm = get_cypher_llm(model, reasoning_effort)
        
        # Build messages - LangChain format
        messages = [
            SystemMessage(content=CYPHER_GENERATOR_PROMPT),
            HumanMessage(content=user_message)
        ]
        
        # Call LLM - callback handler KULLANMA, parent span varsa generation oluşturacağız
        response = await llm.ainvoke(messages)
        
        latency_ms = (time.time() - start_time) * 1000
        
        # Extract Cypher from response - reasoning model format handling
        cypher = ""
        if hasattr(response, "content"):
            content = response.content
            # GPT-5 reasoning format: content liste olabilir
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        cypher = item.get("text", "").strip()
                        break
            elif isinstance(content, str):
                cypher = content.strip()
        
        # Clean up any markdown formatting
        if cypher.startswith("```"):
            lines = cypher.split("\n")
            cypher = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        
        # Token usage from LangChain response metadata
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata:
            prompt_tokens = usage_metadata.get("input_tokens", 0)
            completion_tokens = usage_metadata.get("output_tokens", 0)
            
            # OpenAI cached tokens - input_token_details içinde
            input_details = usage_metadata.get("input_token_details", {})
            if input_details and isinstance(input_details, dict):
                cached_tokens = input_details.get("cache_read", 0) or input_details.get("cached_tokens", 0)
        
        # Log token usage
        cache_pct = round(cached_tokens / max(prompt_tokens, 1) * 100, 1)
        cache_status = f"🟢 CACHE HIT {cache_pct}%" if cached_tokens > 0 else "🔴 CACHE MISS"
        logger.info(f"📊 [LLM Cypher] in={prompt_tokens}, out={completion_tokens}, cached={cached_tokens} ({cache_status})")
        logger.info(f"🤖 LLM Cypher generated in {latency_ms:.0f}ms ({model})")
        logger.debug(f"   Cypher: {cypher[:200]}...")
        
        # Langfuse: parent span varsa child generation olarak ekle (ayrı trace değil!)
        if parent_span and LANGFUSE_AVAILABLE:
            try:
                generation = parent_span.start_generation(
                    name="llm_cypher_generator",
                    model=model,
                    input={"dsl": dsl_json[:500], "schema": schema_info[:200] if schema_info else None},
                    output={"cypher": cypher[:500]},
                    metadata={
                        "latency_ms": latency_ms,
                        "cache_hit": cached_tokens > 0,
                        "requires_embedding": requires_embedding
                    }
                )
                # Token bilgilerini update ile ekle (react_agent.py ile aynı pattern)
                uncached_input = max(0, prompt_tokens - cached_tokens)
                generation.update(
                    usage_details={
                        "input": uncached_input,
                        "input_cached_tokens": cached_tokens,
                        "output": completion_tokens,
                        "total": prompt_tokens + completion_tokens,
                    }
                )
                generation.end()
                logger.info(f"📊 [LLM Cypher] Langfuse generation added to parent span")
            except Exception as lf_err:
                logger.warning(f"⚠️ [LLM Cypher] Langfuse generation failed: {lf_err}")
        
        # Langfuse flush (parent yoksa standalone için)
        if LANGFUSE_AVAILABLE and flush_langfuse and not parent_span:
            try:
                flush_langfuse()
            except Exception:
                pass
        
        return LLMCompilationResult(
            cypher=cypher,
            params={},
            requires_embedding=requires_embedding,
            query_text=query_text,
            warnings=[f"🤖 Generated by {model}"],
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            latency_ms=latency_ms
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ DSL JSON parse error: {e}")
        return LLMCompilationResult(
            cypher="",
            warnings=[f"❌ JSON parse error: {e}"],
            latency_ms=(time.time() - start_time) * 1000
        )
    except Exception as e:
        logger.error(f"❌ LLM Cypher generation failed: {e}")
        return LLMCompilationResult(
            cypher="",
            warnings=[f"❌ LLM error: {e}"],
            latency_ms=(time.time() - start_time) * 1000
        )


async def generate_cypher_with_retry(
    dsl_json: str,
    schema_info: Optional[str] = None,
    model: str = "gpt-5-mini",
    max_retries: int = 2
) -> LLMCompilationResult:
    """
    Retry logic ile Cypher generation.
    İlk denemede hata olursa tekrar dener.
    """
    last_error = None
    
    for attempt in range(max_retries):
        result = await generate_cypher(dsl_json, schema_info, model)
        
        if result.cypher and not any("❌" in w for w in result.warnings):
            return result
        
        last_error = result.warnings[-1] if result.warnings else "Unknown error"
        logger.warning(f"⚠️ LLM attempt {attempt + 1} failed: {last_error}")
    
    # All retries failed
    return LLMCompilationResult(
        cypher="",
        warnings=[f"❌ All {max_retries} attempts failed. Last error: {last_error}"]
    )
