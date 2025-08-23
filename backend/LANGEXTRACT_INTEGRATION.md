# LangExtract Graph Builder Integration Guide

## Overview

Bu entegrasyon, mevcut LLM-based graph extraction sisteminize LangExtract'ı alternatif veya tamamlayıcı olarak eklemenizi sağlar.

## Files Added

1. **`langextract_graph_integration.py`** - Ana entegrasyon modülü
2. **`test_langextract_integration.py`** - Test ve karşılaştırma scripti
3. **`langextract_example.py`** - Basit kullanım örneği

## Integration Options

### Option 1: Drop-in Replacement

Mevcut `get_graph_from_llm` fonksiyonunu tamamen değiştirmek için:

```python
# src/main.py içinde
from langextract_graph_integration import get_graph_from_langextract

# processing_chunks fonksiyonunda değiştirin:
# graph_documents = await get_graph_from_llm(...)
graph_documents = await get_graph_from_langextract(
    model, chunkId_chunkDoc_list, allowedNodes, allowedRelationship,
    chunks_to_combine, file_name, additional_instructions, graph
)
```

### Option 2: Conditional Usage

Model parametresine göre seçim yapmak için:

```python
# src/main.py içinde
from langextract_graph_integration import get_graph_from_langextract

async def processing_chunks(...):
    # Model kontrolü
    if model.startswith("langextract-"):
        # LangExtract kullan
        graph_documents = await get_graph_from_langextract(
            model.replace("langextract-", ""),
            chunkId_chunkDoc_list, allowedNodes, allowedRelationship,
            chunks_to_combine, file_name, additional_instructions, graph
        )
    else:
        # Mevcut LLM sistemini kullan
        graph_documents = await get_graph_from_llm(
            model, chunkId_chunkDoc_list, allowedNodes, allowedRelationship,
            chunks_to_combine, file_name, additional_instructions, graph
        )
```

### Option 3: Hybrid Approach

Her iki metodu da kullanıp sonuçları birleştirmek için:

```python
async def hybrid_extraction(...):
    # Önce LangExtract ile structured extraction
    langextract_docs = await get_graph_from_langextract(...)

    # Sonra LLM ile context-aware extraction
    llm_docs = await get_graph_from_llm(...)

    # Sonuçları merge et
    combined_docs = merge_graph_documents(langextract_docs, llm_docs)
    return combined_docs
```

## Configuration

### Environment Variables

```bash
# OpenAI için
export OPENAI_API_KEY="your-openai-key"

# Google Gemini için
export GOOGLE_API_KEY="your-google-key"

# Ollama için (local)
export OLLAMA_HOST="http://localhost:11434"
```

### Model Mapping

```python
# langextract_graph_integration.py içinde güncelleme yapabilirsiniz:
model_mapping = {
    "openai-gpt-4": "gpt-4o-mini",
    "openai-gpt-3.5": "gpt-3.5-turbo",
    "gemini-pro": "gemini-1.5-flash",
    "gemini-1.5-pro": "gemini-1.5-pro",
    "ollama-llama3": "ollama/llama3"
}
```

## Testing

### Basic Test

```bash
cd /Users/mehmeterdogan/python-projects/llm-graph-builder/backend
export OPENAI_API_KEY="your-key"
python langextract_example.py
```

### Comparison Test

```bash
export OPENAI_API_KEY="your-key"
python test_langextract_integration.py
```

### Integration Test

```bash
# Mevcut sisteminizde test etmek için:
python -c "
import asyncio
from langextract_graph_integration import get_graph_from_langextract

async def test():
    # Mock data
    chunks = [type('Chunk', (), {'page_content': 'Test Turkish text...'})()]
    result = await get_graph_from_langextract(
        'openai-gpt-4', chunks, 'Person,Organization',
        'Person,WORKS_AT,Organization', 1
    )
    print(f'Extracted {len(result)} documents')

asyncio.run(test())
"
```

## Benefits of LangExtract

### ✅ Advantages

1. **Structured Output**: Garantili JSON schema compliance
2. **Better Error Handling**: Built-in validation ve retry logic
3. **Provider Flexibility**: Kolay provider switching (OpenAI, Gemini, Ollama)
4. **Example-based Learning**: Few-shot learning ile better accuracy
5. **Type Safety**: Pydantic models ile type validation

### ⚠️ Considerations

1. **Learning Curve**: Yeni API öğrenme gereksinimi
2. **Dependencies**: Ekstra package dependencies
3. **Customization**: Mevcut custom prompting logic'in adaptasyon gereksinimi

## Performance Comparison

Test sonuçlarında genellikle:

- **LangExtract**: Daha hızlı, daha tutarlı output format
- **Existing LLM**: Daha esnek, domain-specific customization

## Monitoring & Debugging

### Logging

```python
import logging
logging.basicConfig(level=logging.INFO)

# LangExtract operations loglanır:
# "LangExtract extracted 5 entities"
# "LangExtract extraction completed: 5 entities, 3 relationships"
```

### Error Handling

```python
try:
    result = await get_graph_from_langextract(...)
except Exception as e:
    logging.error(f"LangExtract failed, falling back to LLM: {e}")
    result = await get_graph_from_llm(...)
```

## Next Steps

1. **Test**: `python test_langextract_integration.py` çalıştırın
2. **Evaluate**: Sonuçları karşılaştırın
3. **Integrate**: Tercih ettiğiniz option'ı implement edin
4. **Monitor**: Production'da performance'ı takip edin
5. **Optimize**: Model ve parameter tuning yapın

## Support

Sorularınız için:

- LangExtract docs: https://github.com/google/langextract
- Integration issues: Bu modüllerdeki logging'leri inceleyin

