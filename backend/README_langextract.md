# LangExtract Integration

Bu klasörde Google'ın LangExtract kütüphanesi submodule olarak entegre edilmiştir.

## Kurulum

LangExtract zaten editable mode'da kurulmuş durumda:

```bash
cd backend/langextract
pip install -e .
```

## Kullanım

Normal bir pip paketi gibi kullanabilirsiniz:

```python
import langextract as lx
from langextract.extraction import extract
from langextract import data, schema, providers

# Basit metin çıkarma örneği
result = extract(
    text_or_documents="John Smith is 30 years old and works at Google",
    prompt_description="Extract person information",
    model_id="gpt-4o-mini"  # OPENAI_API_KEY gerekli
)
```

## Örnekler

- `langextract_example.py`: Temel kullanım örneği
- `backend/langextract/examples/`: Resmi örnekler

## Provider Konfigürasyonu

### OpenAI

```bash
export OPENAI_API_KEY="your-key"
```

```python
result = extract(text, prompt, model_id="gpt-4o-mini")
```

### Google Gemini

```bash
export GOOGLE_API_KEY="your-key"
```

```python
result = extract(text, prompt, model_id="gemini-1.5-flash")
```

### Ollama (Local)

```bash
# Ollama sunucusunu başlat
ollama serve
ollama pull llama3
```

```python
result = extract(text, prompt, model_id="ollama/llama3")
```

## Geliştirme

Submodule'u güncellemek için:

```bash
cd backend/langextract
git pull origin main
cd ../..
git add backend/langextract
git commit -m "Update langextract submodule"
```

## API Referansı

- **extract()**: Ana metin çıkarma fonksiyonu
- **data.ExampleData**: Örnek veri yapısı
- **data.Extraction**: Çıkarım sonucu yapısı
- **providers**: LLM provider'ları

