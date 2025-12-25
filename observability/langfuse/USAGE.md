# Langfuse Entegrasyonu - Kullanım Kılavuzu

Bu dokümantasyon, LLM Graph Builder projesindeki Langfuse entegrasyonunu açıklar.

## 📋 İçindekiler

1. [Langfuse Nedir?](#langfuse-nedir)
2. [Aktif Özellikler](#aktif-özellikler)
3. [Langfuse UI Erişimi](#langfuse-ui-erişimi)
4. [Tracing (İzleme)](#tracing-izleme)
5. [Sessions (Oturumlar)](#sessions-oturumlar)
6. [User Tracking (Kullanıcı Takibi)](#user-tracking-kullanıcı-takibi)
7. [LLM-as-a-Judge (Otomatik Değerlendirme)](#llm-as-a-judge-otomatik-değerlendirme)
8. [Prompt Management](#prompt-management)
9. [Datasets & Experiments](#datasets--experiments)
10. [API Endpoint'leri](#api-endpointleri)

---

## Langfuse Nedir?

Langfuse, LLM uygulamaları için açık kaynaklı bir observability (gözlemlenebilirlik) platformudur.

**Temel Özellikleri:**
- 📊 **Tracing**: Her LLM çağrısının detaylı kaydı
- 💰 **Cost Tracking**: Token kullanımı ve maliyet takibi
- 📈 **Analytics**: Kullanım metrikleri ve dashboard'lar
- 🧪 **Evaluation**: Otomatik kalite değerlendirmesi
- 📝 **Prompt Management**: Prompt versiyonlama ve A/B testing

---

## Aktif Özellikler

| Özellik | Durum | Açıklama |
|---------|-------|----------|
| Tracing | ✅ Aktif | Tüm LLM çağrıları izleniyor |
| Sessions | ✅ Aktif | Konuşmalar session bazlı gruplandı |
| User Tracking | ✅ Aktif | Kullanıcı bazlı metrikler |
| LLM-as-a-Judge | ✅ Aktif | Hallucination & Helpfulness evaluator'ları |
| Prompt Management | ✅ Aktif | `react-agent-system` prompt'u yönetiliyor |
| Datasets | ✅ Aktif | Test case yönetimi |
| Cost Tracking | ✅ Aktif | Token ve maliyet hesaplama |

---

## Langfuse UI Erişimi

### URL
```
http://localhost:3101
```

### Varsayılan Giriş Bilgileri
- **Email**: `admin@llmgraphbuilder.local`
- **Password**: `admin_password_2024`

### Proje
- **Organization**: LLM Graph Builder
- **Project**: LLM Graph Builder Main

---

## Tracing (İzleme)

Her chat sorusu için otomatik olarak trace oluşturulur.

### Trace İçeriği
```
📦 Trace: "chat-session-xxx"
├── 📍 Span: "react-agent"
│   ├── 📍 Span: "prompt:react-agent-system"
│   ├── 📍 Generation: "llm-call"
│   │   └── Token Usage: input=500, output=200
│   ├── 📍 Span: "tool:graph-query"
│   └── 📍 Span: "tool:vector-search"
└── 📊 Scores: Hallucination=0.1, Helpfulness=0.9
```

### Langfuse UI'da Görüntüleme
1. Sol menüden **Tracing** seçin
2. Trace listesinden bir trace'e tıklayın
3. Detaylı timeline ve token kullanımını görün

---

## Sessions (Oturumlar)

Aynı kullanıcının ardışık soruları bir session altında gruplandırılır.

### Session Yapısı
```
📂 Session: "session-abc-123"
├── 📝 Trace 1: "Neo4j nedir?"
├── 📝 Trace 2: "Örnek bir sorgu göster"
└── 📝 Trace 3: "Bu sorguyu optimize et"
```

### Langfuse UI'da Görüntüleme
1. Sol menüden **Sessions** seçin
2. Session'ları kullanıcı veya tarih bazlı filtreleyin
3. Bir session'a tıklayarak tüm konuşmayı görün

### Session Metrikleri
- Toplam trace sayısı
- Session süresi
- Toplam token kullanımı
- Toplam maliyet

---

## User Tracking (Kullanıcı Takibi)

Her kullanıcının aktiviteleri ayrı takip edilir.

### Langfuse UI'da Görüntüleme
1. Sol menüden **Users** seçin
2. Kullanıcı listesini görün
3. Bir kullanıcıya tıklayarak:
   - Toplam trace sayısı
   - Session sayısı
   - Token kullanımı
   - Maliyet
   - Ortalama kalite skorları

### Metrikler
- Kullanıcı başına maliyet analizi
- En aktif kullanıcılar
- Kullanıcı bazlı kalite skorları

---

## LLM-as-a-Judge (Otomatik Değerlendirme)

Her trace otomatik olarak GPT ile değerlendirilir.

### Aktif Evaluator'lar

| Evaluator | Ölçtüğü | Skor Aralığı |
|-----------|---------|--------------|
| **Hallucination** | Cevabın uydurma bilgi içerip içermediği | 0 (iyi) → 1 (kötü) |
| **Helpfulness** | Cevabın kullanıcıya ne kadar yardımcı olduğu | 0 (kötü) → 1 (iyi) |

### Nasıl Çalışır?
1. Kullanıcı soru sorar
2. Agent cevap verir
3. 30 saniye sonra GPT evaluator'lar çalışır
4. Trace'e skor eklenir

### Langfuse UI'da Görüntüleme
1. Sol menüden **Evaluation > Scores** seçin
2. Tüm skorları listeleyin
3. Düşük skorlu trace'leri filtreleyin

### Yeni Evaluator Ekleme
1. **Evaluation > LLM-as-a-Judge** menüsüne gidin
2. **Set up evaluator** butonuna tıklayın
3. Hazır template'lerden birini seçin:
   - Conciseness (Kısalık)
   - Relevance (İlgililik)
   - Toxicity (Toksiklik)
   - Correctness (Doğruluk - ground truth gerektirir)

---

## Prompt Management

System prompt'ları Langfuse üzerinden yönetilir.

### Aktif Prompt'lar
| İsim | Label | Açıklama |
|------|-------|----------|
| `react-agent-system` | production | Ana agent system prompt'u |

### Langfuse UI'da Düzenleme
1. Sol menüden **Prompt Management > Prompts** seçin
2. `react-agent-system` prompt'una tıklayın
3. Düzenleyin ve yeni versiyon oluşturun
4. "production" label'ı ile yayınlayın

### Versiyon Yönetimi
- Her değişiklik yeni bir versiyon oluşturur
- Eski versiyonlara geri dönebilirsiniz
- A/B testing için farklı label'lar kullanabilirsiniz

### Kod Entegrasyonu
```python
# Backend otomatik olarak Langfuse'dan prompt çeker
# Fallback: Langfuse erişilemezse yerel prompt kullanılır
```

---

## Datasets & Experiments

Test case'leri oluşturup uygulamayı sistematik test edin.

### Dataset Nedir?
```
📂 Dataset: "qa-regression-tests"
├── 📝 Item 1: {input: "Neo4j nedir?", expected: "Graph veritabanı..."}
├── 📝 Item 2: {input: "MATCH sorgusu", expected: "..."}
└── 📝 Item 3: {input: "Index nasıl oluşturulur?", expected: "..."}
```

### Langfuse UI'dan Dataset Oluşturma
1. Sol menüden **Evaluation > Datasets** seçin
2. **+ New dataset** butonuna tıklayın
3. İsim ve açıklama girin
4. **Items** tab'ından test case'ler ekleyin

### Production Trace'i Dataset'e Ekleme
1. **Tracing** menüsünden hatalı bir trace bulun
2. Trace detayına girin
3. **+ Add to dataset** butonuna tıklayın
4. Hedef dataset'i seçin
5. Expected output'u düzeltin

### Experiment Çalıştırma
1. **Datasets** menüsünden dataset seçin
2. **Run experiment** butonuna tıklayın
3. Sonuçları karşılaştırın

---

## API Endpoint'leri

Backend üzerinden dataset yönetimi için API endpoint'leri:

### Dataset Oluşturma
```bash
curl -X POST http://localhost:8000/api/v2/datasets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "qa-regression-tests",
    "description": "Regresyon testleri için QA soruları",
    "metadata": {"type": "regression"}
  }'
```

### Dataset Görüntüleme
```bash
curl http://localhost:8000/api/v2/datasets/qa-regression-tests
```

### Test Case Ekleme
```bash
curl -X POST http://localhost:8000/api/v2/datasets/qa-regression-tests/items \
  -H "Content-Type: application/json" \
  -d '{
    "input": {"question": "Neo4j nedir?"},
    "expected_output": {"answer": "Neo4j açık kaynaklı bir graph veritabanıdır."},
    "metadata": {"difficulty": "easy"}
  }'
```

### Trace'i Dataset'e Ekleme
```bash
curl -X POST http://localhost:8000/api/v2/datasets/qa-regression-tests/from-trace \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "trace-abc-123",
    "expected_output": {"answer": "Düzeltilmiş cevap"},
    "metadata": {"error_type": "hallucination"}
  }'
```

### Langfuse Durum Kontrolü
```bash
curl http://localhost:8000/api/v2/langfuse/status
```

---

## Tipik Kullanım Senaryoları

### 1. Hatalı Cevabı Düzeltme
```
1. Kullanıcı şikayet eder: "Yanlış cevap aldım"
2. Langfuse UI'dan trace'i bulun
3. Trace'i dataset'e ekleyin
4. Expected output'u düzeltin
5. Artık bu soru her prompt değişikliğinde test edilir
```

### 2. Prompt Optimizasyonu
```
1. Langfuse UI'dan düşük skorlu trace'leri filtreleyin
2. Ortak sorunları tespit edin
3. Prompt'u düzenleyin (yeni versiyon)
4. Dataset üzerinde experiment çalıştırın
5. Skorların iyileştiğini doğrulayın
6. "production" label'ı ile yayınlayın
```

### 3. Maliyet Analizi
```
1. Langfuse Dashboard'a gidin
2. Kullanıcı veya zaman bazlı maliyet görün
3. Yüksek maliyetli trace'leri inceleyin
4. Token kullanımını optimize edin
```

### 4. Performans İzleme
```
1. Sessions menüsünden oturum sürelerini izleyin
2. Yavaş trace'leri tespit edin
3. Latency metriklerini analiz edin
```

---

## Sorun Giderme

### Langfuse Bağlantı Hatası
```bash
# Durumu kontrol et
curl http://localhost:8000/api/v2/langfuse/status

# Docker container'ları kontrol et
docker ps | grep langfuse
```

### Trace'ler Görünmüyor
1. `.preview.env` dosyasında Langfuse değişkenlerini kontrol edin
2. Backend loglarını kontrol edin
3. Langfuse container'larının çalıştığından emin olun

### Evaluator Çalışmıyor
1. OpenAI API key'in Langfuse'da tanımlı olduğunu kontrol edin
2. Evaluator durumunu LLM-as-a-Judge menüsünden kontrol edin
3. Evaluator log'larını inceleyin

---

## Ortam Değişkenleri

`.preview.env` dosyasında:

```bash
# Langfuse Entegrasyonu
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3101
LANGFUSE_PUBLIC_KEY=pk-llmgb-main-2024
LANGFUSE_SECRET_KEY=sk-llmgb-secret-2024
```

---

## Daha Fazla Bilgi

- [Langfuse Resmi Dokümantasyon](https://langfuse.com/docs)
- [LLM-as-a-Judge Kılavuzu](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
- [Prompt Management](https://langfuse.com/docs/prompt-management/get-started)
- [Datasets & Experiments](https://langfuse.com/docs/evaluation/experiments/datasets)

