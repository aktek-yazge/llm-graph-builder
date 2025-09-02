# 🧠 LLM PROMPT BÜYÜME FORMÜLü DETAYLI ANALİZ

## 📋 PROMPT YAPISI FORMÜLÜ

Her iterasyonda LLM'e gönderilen prompt bu formülle oluşuyor:

```
TOTAL_PROMPT = SYSTEM_PROMPT + USER_PROMPT + CONVERSATION_HISTORY

USER_PROMPT = CONTEXT_MEMORY + CURRENT_OBSERVATION + CONTEXT_INFO + INSTRUCTION

CONTEXT_MEMORY = "## DAHA ÖNCE BULUNAN BAŞARILI BİLGİLER:\n\n" +
                 SUM(successful_findings[-5:]) +  # Son 5 başarılı bulgu
                 "\n**BU BİLGİLERİ DİKKATE ALARAK SONRAKI ADIMI BELİRLE!**\n\n"

CONVERSATION_HISTORY = conversation_history[-4:]  # Son 4 adım
```

## 🔄 İTERASYON BAZINDA BÜYÜME

### İterasyon 1 (İlk çağrı):

```
SYSTEM_PROMPT = 21,464 karakter (schema + instructions) - SABİT
USER_PROMPT =
  - CONTEXT_MEMORY = "" (boş)
  - CURRENT_OBSERVATION = "Kullanıcı sorusu: 'SORU'"
  - CONTEXT_INFO = "" (boş)
  - INSTRUCTION = "Bu duruma göre next action'ını belirle:"

CONVERSATION_HISTORY = [] (boş)

TOPLAM ≈ 21,464 + 100 = ~21,564 karakter
```

### İterasyon 2:

```
SYSTEM_PROMPT = 21,464 karakter - SABİT

USER_PROMPT =
  - CONTEXT_MEMORY = "## DAHA ÖNCE BULUNAN BAŞARILI BİLGİLER:\n\n" +
                     "**Adım 1 - CYPHER_STRUCTURED_DATA:**\n" +
                     "Bulgu: [LLM özeti - ~200-300 karakter]\n" +
                     "Relevance Score: 0.8\n" +
                     "---\n" +
                     "**BU BİLGİLERİ DİKKATE ALARAK...**\n\n"

  - CURRENT_OBSERVATION = "Cypher sonucu: 1 kayıt bulundu..."
  - CONTEXT_INFO = "\n\nMevcut Durum:\n- 0 chunk keşfedildi..."
  - INSTRUCTION = "Bu duruma göre next action'ını belirle:"

CONVERSATION_HISTORY = [
  "Thought: İlk düşünce\nAction: cypher_query\nContent: MATCH..."
]

TOPLAM ≈ 21,464 + 600 + 150 = ~22,214 karakter
```

### İterasyon 3:

```
SYSTEM_PROMPT = 21,464 karakter - SABİT

USER_PROMPT =
  - CONTEXT_MEMORY = "## DAHA ÖNCE BULUNAN BAŞARILI BİLGİLER:\n\n" +
                     "**Adım 1 - CYPHER_STRUCTURED_DATA:**\n" +
                     "Bulgu: [Özet]\n" +
                     "---\n" +
                     "**Adım 2 - CYPHER_STRUCTURED_DATA:**\n" +
                     "Bulgu: [Özet]\n" +
                     "---\n" +
                     "**BU BİLGİLERİ DİKKATE ALARAK...**\n\n"

  - CURRENT_OBSERVATION = "Document bilgisi bulundu..."
  - CONTEXT_INFO = "\n\nMevcut Durum:\n- 0 chunk keşfedildi..."
  - INSTRUCTION = "Bu duruma göre next action'ını belirle:"

CONVERSATION_HISTORY = [
  "Thought: İlk\nAction: cypher_query\nContent: MATCH...",
  "Thought: İkinci\nAction: cypher_query\nContent: MATCH..."
]

TOPLAM ≈ 21,464 + 1000 + 300 = ~22,764 karakter
```

## 📊 BÜYÜME KATSAYILARI

### 🎯 Sabit Maliyetler (Her iterasyonda aynı):

- **SYSTEM_PROMPT:** 21,464 karakter (~6,500 token)
- **INSTRUCTION:** "Bu duruma göre next action'ını belirle:" (~20 token)

### 📈 Değişken Maliyetler (Her iterasyonda büyüyen):

1. **CONTEXT_MEMORY:**

   - İterasyon 1: 0 karakter
   - İterasyon 2: ~400 karakter
   - İterasyon 3: ~800 karakter
   - İterasyon 4: ~1200 karakter
   - İterasyon 5: ~1600 karakter
   - **Büyüme oranı:** ~400 karakter/iterasyon

2. **CONVERSATION_HISTORY:**

   - İterasyon 1: 0 karakter
   - İterasyon 2: ~150 karakter
   - İterasyon 3: ~300 karakter
   - İterasyon 4: ~450 karakter (maksimum 4 geçmiş)
   - İterasyon 5: ~450 karakter (sabit kalır)
   - **Büyüme oranı:** ~150 karakter/iterasyon (ilk 4 iterasyon)

3. **CURRENT_OBSERVATION:**

   - Her iterasyonda farklı (~50-200 karakter)

4. **CONTEXT_INFO:**
   - Chunk/entity sayısına göre (~50-300 karakter)

## 📐 TOKEN HESAPLAMA FORMÜLü

```
ITERATION_TOKENS = BASE_TOKENS + CONTEXT_GROWTH * iteration_number

BASE_TOKENS ≈ 6,500 (system prompt)
CONTEXT_GROWTH ≈ 150-200 token/iterasyon

İterasyon 1: ~6,500 + 200*1 = ~6,700 token
İterasyon 2: ~6,500 + 200*2 = ~6,900 token
İterasyon 3: ~6,500 + 200*3 = ~7,100 token
İterasyon 4: ~6,500 + 200*4 = ~7,300 token
İterasyon 5: ~6,500 + 200*5 = ~7,500 token
```

## 🎯 OPTİMİZASYON STRATEJİLERİ

### 1. **Context Memory Sınırlaması:**

```python
# Mevcut: Son 5 başarılı bulgu
context_prompt += finding for finding in self.successful_findings[-5:]

# Optimize: Son 3 bulgu + token limiti
context_prompt += finding for finding in self.successful_findings[-3:]
if len(context_prompt) > 2000:  # Token limiti
    context_prompt = summarize_with_llm(context_prompt)
```

### 2. **Conversation History Sınırlaması:**

```python
# Mevcut: Son 4 konuşma
conversation_history[-4:]

# Optimize: Son 2 konuşma
conversation_history[-2:]
```

### 3. **System Prompt Kompresyonu:**

```python
# Schema bilgisini sadeleştir
# 21,464 karakter -> 15,000 karakter
# Sadece kritik node/relationship'leri dahil et
```

### 4. **Dinamik Context Özetleme:**

```python
# Her 3 iterasyonda context memory'i LLM ile özetle
if iteration_count % 3 == 0:
    self.context_memory = summarize_context_with_llm(self.context_memory)
```

## 🧮 GERÇEK TOKEN ANALİZİ (Test Verileri)

### Count Sorusu (2 iterasyon):

- İterasyon 1: 7,008 input token
- İterasyon 2: 7,236 input token (+228 token artış)
- **Artış oranı:** 228/1 = 228 token/iterasyon

### Detay Sorusu (5 iterasyon):

- İterasyon 1: 7,017 input token
- İterasyon 2: 7,603 input token (+586 token)
- İterasyon 3: 8,283 input token (+680 token)
- İterasyon 4: 7,988 input token (-295 token, context özetleme?)
- İterasyon 5: 8,088 input token (+100 token)
- **Ortalama artış:** ~400 token/iterasyon

## 🎯 SONUÇ

**PROMPT BÜYÜME FORMÜLÜ:**

```
Total_Token(n) = Base_System_Prompt + Context_Memory(n) + Conversation_History(n) + Current_Observation

Context_Memory(n) = min(400 * n, 2000)  # Maksimum 2000 token
Conversation_History(n) = min(150 * n, 600)  # Maksimum 600 token (4 geçmiş)
Current_Observation = 50-200 token

Total_Token(n) ≈ 6500 + min(400*n, 2000) + min(150*n, 600) + 125
```

Bu formül ile her iterasyondaki token kullanımını tahmin edebiliriz! 🚀

