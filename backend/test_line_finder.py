"""
Trendyol LLM ile satır numarası bulan test scripti.
Soru verildiğinde ilgili satır aralığını döndürür.
"""

import json
import re
import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

# ============================================
# CONFIG
# ============================================
MODEL_NAME = "Trendyol/Trendyol-LLM-7B-chat-v4.1.0"
# Daha küçük test için: "Trendyol/Trendyol-LLM-7B-chat-v2.0" veya Qwen 2.5 3B

# OpenAI API model seçimi
OPENAI_MODEL = "gpt-5-mini"  # veya "gpt-4o-mini"

# Reasoning modeller (temperature desteklemiyor)
REASONING_MODELS = ["gpt-5-mini", "o1", "o1-mini", "o1-preview", "o3-mini"]


def is_reasoning_model(model_name: str) -> bool:
    """Model reasoning model mi kontrol et."""
    return any(rm in model_name.lower() for rm in REASONING_MODELS)


def call_openai_api(client, numbered_text: str, question: str):
    """
    Model türüne göre uygun parametrelerle OpenAI API çağrısı yapar.
    Reasoning modeller (gpt-5-mini, o1, o3) için temperature kullanılmaz.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Metin:\n{numbered_text}\n\nSoru: {question}"}
    ]
    
    if is_reasoning_model(OPENAI_MODEL):
        # Reasoning model - temperature yok, reasoning_effort low
        print(f"  [Reasoning model: {OPENAI_MODEL}, effort: low]")
        return client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_completion_tokens=2000,
            reasoning_effort="low",  # low, medium, high
            response_format={"type": "json_object"}
        )
    else:
        # Normal model - temperature var
        print(f"  [Model: {OPENAI_MODEL}]")
        return client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"}
        )


def safe_parse_json(content: str) -> dict | list | None:
    """JSON parse hatalarını yakala."""
    if not content or content.strip() == "":
        print("  ⚠️ Model boş cevap döndürdü")
        return None
    
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"  ⚠️ JSON parse hatası: {e}")
        print(f"  Raw content: {content[:200]}...")
        return None

SYSTEM_PROMPT = """Sen bir sigorta poliçesi analiz asistanısın.
Sana satır numaralı bir poliçe metni ve bir soru verilecek.
Soruyla DOĞRUDAN ilgili kısmın başlangıç ve bitiş satır numaralarını bul.

Kurallar:
1. Sadece JSON formatında cevap ver: {"start_line": X, "end_line": Y}
2. Birden fazla bölge varsa: [{"start_line": X1, "end_line": Y1}, {"start_line": X2, "end_line": Y2}]
3. İlgili kısım yoksa: {"start_line": null, "end_line": null}

Önemli:
- EN DAR ARALIĞI seç. Sadece soruyu cevaplamak için gereken satırları dahil et.
- Yasal açıklamalar, klozlar veya genel şartlar yerine SOMUT VERİYİ (tarih, tutar, isim, tablo) tercih et.
- Tablo içinde aradığın veri varsa, sadece o satırı seç.
- "Taksit planı" sorulduğunda taksit TUTARLARI ve TARİHLERİ olan satırları bul, prim ödeme kurallarını değil.
- Başka açıklama veya metin ekleme, sadece JSON döndür."""


# ============================================
# TEST DATA - Gerçek poliçe dosyası
# ============================================
# Konut poliçesi (basit)
# POLICY_FILE_PATH = "/Users/mehmeterdogan/python-projects/llm-graph-builder-yedek/backend/output/Serdar Çiftçi ASTOR 10 D13 Konut Poliçsi/Serdar Çiftçi ASTOR 10 D13 Konut Poliçsi.pdf.md"

# KOBİ poliçesi (karmaşık - 13 sayfa)
POLICY_FILE_PATH = "/Users/mehmeterdogan/python-projects/llm-graph-builder-yedek/backend/output/TEPE GÜMRÜK İŞYERİ/TEPE GÜMRÜK İŞYERİ.pdf.md"


def load_policy_text(file_path: str) -> str:
    """Poliçe dosyasını yükler ve CHUNK taglerini temizler."""
    import re
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # <CHUNK> ve </CHUNK> taglerini kaldır
    content = re.sub(r'</?CHUNK>', '', content)
    # [PAGE BREAK] satırlarını kaldır
    content = re.sub(r'\[PAGE BREAK\]', '--- SAYFA SONU ---', content)
    # Birden fazla boş satırı tek satıra indir
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    return content.strip()


# Örnek metin (test için)
SAMPLE_PAGE_TEXT = """Doğa Konut Paket Sigortası ile Evinizde Güvendesiniz

DOĞA KONUT PAKET SİGORTA POLİÇESİ
(Deprem Bina Teminatlı)
Tanzim Tarihi: 25.01.2024
Başlama Tarihi: 09.01.2024
Bitiş Tarihi: 09.01.2025

Poliçe No: 129384711
Acente: DİNKAL SİGORTA ACENTELİĞİ ANONİM ŞİRKETİ

Sigortalı: SERDAR ÇİFTÇİ
Adres: ORKİDE AKAT ASTOR 10 (PARK MAYA SİTESİ) AP.2 J / 13 MERKEZ BEŞİKTAŞ İSTANBUL
T.C. Kimlik No: 308*****910

Riziko Adresi:
AKAT MAH. ORKİDE SK.
ASTOR 10 (PARK MAYA SİTESİ) Apt No: 2 J Daire No:13
MERKEZ / BEŞİKTAŞ / İSTANBUL

TEMİNATLAR:
BİNA: 3,500,000.00 TL
YANGIN MALİ SORUMLULUK: 3,500,000.00 TL
DEPREM (Bina): 2,972,200.00 TL
SEL / SU BASKINI: 3,500,000.00 TL
CAM KIRILMASI: 400,000.00 TL

Prim Bilgileri:
Net Prim: 6,959.50 TL
Brüt Prim: 7,312.87 TL

Taksit Planı:
P 25.01.2024: 1,828.22 TL
1 25.02.2024: 1,096.93 TL
2 25.03.2024: 1,096.93 TL
3 25.04.2024: 1,096.93 TL
4 25.05.2024: 1,096.93 TL
5 25.06.2024: 1,096.93 TL

Sigortacı: Doğa Sigorta A.Ş.
Adres: Maslak Mah. Büyükdere Cd. Spine Tower No:243 K: 20-21 Maslak Sarıyer İstanbul
Tel: (212) 212 36 42
Hasar İhbar: 0850 811 51 00
"""


def add_line_numbers(text: str) -> str:
    """Metne satır numaraları ekler."""
    lines = text.strip().split('\n')
    numbered_lines = [f"{i+1:3}| {line}" for i, line in enumerate(lines)]
    return '\n'.join(numbered_lines)


def parse_json_response(response: str) -> dict | list | None:
    """Model çıktısından JSON parse eder."""
    # JSON bloğunu bul
    json_match = re.search(r'\{.*\}|\[.*\]', response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return None


class LineFinder:
    def __init__(self, model_name: str = MODEL_NAME):
        print(f"Model yükleniyor: {model_name}")
        
        # Apple Silicon MPS kontrolü
        if torch.backends.mps.is_available():
            self.device = torch.device("mps")
            print("Apple Metal GPU (MPS) kullanılıyor")
        elif torch.cuda.is_available():
            self.device = torch.device("cuda")
            print("NVIDIA CUDA GPU kullanılıyor")
        else:
            self.device = torch.device("cpu")
            print("CPU kullanılıyor (yavaş olacak)")
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        
        # Apple Silicon için özel ayarlar
        if self.device.type == "mps":
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.float16,
            ).to(self.device)
        else:
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                device_map="auto",
                torch_dtype=torch.float16,
            )
        
        print(f"Model yüklendi. Device: {self.device}")
    
    def find_lines(self, page_text: str, question: str) -> dict | list | None:
        """
        Verilen metin ve soru için ilgili satır aralığını bulur.
        
        Args:
            page_text: Düz metin (satır numarasız)
            question: Kullanıcı sorusu
            
        Returns:
            {"start_line": int, "end_line": int} veya liste
        """
        # Satır numarası ekle
        numbered_text = add_line_numbers(page_text)
        
        # Prompt oluştur
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Metin:\n{numbered_text}\n\nSoru: {question}"}
        ]
        
        # Tokenize
        input_ids = self.tokenizer.apply_chat_template(
            messages, 
            return_tensors="pt",
            add_generation_prompt=True
        ).to(self.device)
        
        # Generate
        with torch.no_grad():
            outputs = self.model.generate(
                input_ids,
                max_new_tokens=100,
                temperature=0.1,  # Düşük temperature = deterministik
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id
            )
        
        # Decode
        response = self.tokenizer.decode(
            outputs[0][input_ids.shape[1]:], 
            skip_special_tokens=True
        )
        
        print(f"Model çıktısı: {response}")
        
        return parse_json_response(response)


def read_lines(text: str, start_line: int, end_line: int) -> str:
    """Metinden belirli satırları okur (tool simülasyonu)."""
    lines = text.split('\n')
    # 1-indexed to 0-indexed
    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)
    return '\n'.join(lines[start_idx:end_idx])


def test_with_mock():
    """GPU olmadan mock test - gerçek dosya ile."""
    print("=" * 60)
    print("MOCK TEST (Model yüklemeden)")
    print("=" * 60)
    
    # Gerçek poliçe dosyasını yükle
    print(f"\nDosya yükleniyor: {POLICY_FILE_PATH}")
    policy_text = load_policy_text(POLICY_FILE_PATH)
    
    total_lines = len(policy_text.split('\n'))
    print(f"Toplam satır sayısı: {total_lines}")
    
    numbered_text = add_line_numbers(policy_text)
    print("\nSatır numaralı metin (ilk 30 satır):")
    print('\n'.join(numbered_text.split('\n')[:30]))
    print("...\n")
    
    test_questions = [
        "Sigortalının adı ve adresi nedir?",
        "Poliçe numarası ve tarihleri nedir?",
        "Teminat bedelleri nelerdir?",
        "Taksit planı nasıl?",
        "Deprem teminatı var mı, bedeli ne kadar?",
        "Sigorta şirketinin iletişim bilgileri nedir?",
        "Dahili su teminatı kapsamında neler var?",
        "Sel ve su baskını muafiyeti nedir?",
    ]
    
    print("Test soruları (model çalıştırılınca bunlar sorulacak):")
    for i, q in enumerate(test_questions, 1):
        print(f"  {i}. {q}")


def test_with_model():
    """Gerçek model ile test - Trendyol LLM."""
    print("=" * 60)
    print("MODEL TEST - Trendyol LLM")
    print("=" * 60)
    
    # Gerçek poliçe dosyasını yükle
    print(f"\nDosya yükleniyor: {POLICY_FILE_PATH}")
    policy_text = load_policy_text(POLICY_FILE_PATH)
    total_lines = len(policy_text.split('\n'))
    print(f"Toplam satır sayısı: {total_lines}")
    
    finder = LineFinder()
    
    test_questions = [
        # Temel sorular
        "Sigortalının adı ve adresi nedir?",
        "Poliçe numarası ve tarihleri nedir?",
        "Toplam brüt prim ve taksit tutarları nedir?",
        
        # Zorlu sorular
        "Sel ve su baskını muafiyeti oranı nedir?",
        "Elektronik cihaz teminatı muafiyet grupları ve oranları nelerdir?",
        "Hırsızlık güvenlik önlemleri şartları nelerdir?",
        
        # Çok detaylı bölümler
        "Taşınabilir cihazlar ek teminatı şartları nelerdir?",
        "İzolasyon kusurları klozunun istisnaları nelerdir?",
        
        # Liste ve tablo soruları
        "Ray Kulüp yardım hizmetleri ve limitleri nelerdir?",
        "Sigortalı elektronik cihazların listesi nerede?",
        
        # Spesifik bilgi
        "Deprem muhteviyat muafiyeti koasürans oranı nedir?",
    ]
    
    results = []
    
    for question in test_questions:
        print(f"\n{'='*60}")
        print(f"SORU: {question}")
        print("="*60)
        
        result = finder.find_lines(policy_text, question)
        print(f"\nModel çıktısı: {result}")
        
        if result:
            # Tek sonuç veya liste olabilir
            result_list = result if isinstance(result, list) else [result]
            
            for r in result_list:
                if r.get('start_line') and r.get('end_line'):
                    start = r['start_line']
                    end = r['end_line']
                    
                    print(f"\n📍 Bulunan satırlar: {start} - {end}")
                    print("-" * 40)
                    
                    # Tool ile satırları oku
                    extracted_text = read_lines(policy_text, start, end)
                    print(extracted_text)
                    print("-" * 40)
                    
                    results.append({
                        'question': question,
                        'start_line': start,
                        'end_line': end,
                        'text': extracted_text
                    })
        else:
            print("❌ İlgili bölüm bulunamadı")
    
    # Özet
    print("\n\n" + "=" * 60)
    print("ÖZET")
    print("=" * 60)
    for r in results:
        print(f"\n• {r['question']}")
        print(f"  Satırlar: {r['start_line']} - {r['end_line']}")


def test_with_api():
    """
    OpenAI API ile test (daha hızlı, GPU gerektirmez).
    GPT-4o-mini veya Claude kullanılabilir.
    """
    from openai import OpenAI
    
    client = OpenAI()  # OPENAI_API_KEY environment variable gerekli
    
    # Gerçek poliçe dosyasını yükle
    print(f"\nDosya yükleniyor: {POLICY_FILE_PATH}")
    policy_text = load_policy_text(POLICY_FILE_PATH)
    total_lines = len(policy_text.split('\n'))
    print(f"Toplam satır sayısı: {total_lines}")
    
    numbered_text = add_line_numbers(policy_text)
    
    test_questions = [
        # Temel sorular
        "Sigortalının adı ve adresi nedir?",
        "Poliçe numarası ve tarihleri nedir?",
        "Toplam brüt prim ve taksit tutarları nedir?",
        
        # Zorlu sorular - birden fazla yerde geçen konular
        "Sel ve su baskını muafiyeti oranı nedir?",
        "Elektronik cihaz teminatı muafiyet grupları ve oranları nelerdir?",
        "Hırsızlık güvenlik önlemleri şartları nelerdir?",
        
        # Çok detaylı bölümler
        "Taşınabilir cihazlar ek teminatı şartları nelerdir?",
        "İzolasyon kusurları klozunun istisnaları nelerdir?",
        
        # Liste ve tablo soruları  
        "Ray Kulüp yardım hizmetleri ve limitleri nelerdir?",
        "Sigortalı elektronik cihazların listesi nerede?",
        
        # Spesifik bilgi
        "Deprem muhteviyat muafiyeti koasürans oranı nedir?",
    ]
    
    results = []
    
    for question in test_questions:
        print(f"\n{'='*60}")
        print(f"SORU: {question}")
        print("="*60)
        
        try:
            response = call_openai_api(client, numbered_text, question)
            
            # Reasoning bilgisini göster (varsa)
            if hasattr(response.choices[0].message, 'reasoning_content') and response.choices[0].message.reasoning_content:
                print(f"\n🧠 Düşünce: {response.choices[0].message.reasoning_content[:200]}...")
            
            result = safe_parse_json(response.choices[0].message.content)
            
            if result is None:
                print("❌ Geçersiz cevap")
                continue
            
            print(f"\nModel çıktısı: {result}")
            
            result_list = result if isinstance(result, list) else [result]
            
            for r in result_list:
                if r.get('start_line') and r.get('end_line'):
                    start = r['start_line']
                    end = r['end_line']
                    
                    print(f"\n📍 Bulunan satırlar: {start} - {end}")
                    print("-" * 40)
                    
                    extracted_text = read_lines(policy_text, start, end)
                    print(extracted_text)
                    print("-" * 40)
                    
                    results.append({
                        'question': question,
                        'start_line': start,
                        'end_line': end,
                        'text': extracted_text
                    })
                else:
                    print("❌ İlgili bölüm bulunamadı")
        except Exception as e:
            print(f"❌ Hata: {e}")
            continue
    
    # Özet
    print("\n\n" + "=" * 60)
    print("ÖZET")
    print("=" * 60)
    for r in results:
        print(f"\n• {r['question']}")
        print(f"  Satırlar: {r['start_line']} - {r['end_line']}")


def interactive_test():
    """Etkileşimli test modu - kendi sorularını sor."""
    from openai import OpenAI
    
    client = OpenAI()
    
    print("=" * 60)
    print("ETKİLEŞİMLİ TEST MODU")
    print("=" * 60)
    
    # Gerçek poliçe dosyasını yükle
    print(f"\nDosya yükleniyor: {POLICY_FILE_PATH}")
    policy_text = load_policy_text(POLICY_FILE_PATH)
    total_lines = len(policy_text.split('\n'))
    print(f"Toplam satır sayısı: {total_lines}")
    
    numbered_text = add_line_numbers(policy_text)
    
    print("\nSorunuzu yazın (çıkmak için 'q'):")
    
    while True:
        question = input("\n❓ Soru: ").strip()
        if question.lower() == 'q':
            break
        
        if not question:
            continue
        
        try:
            response = call_openai_api(client, numbered_text, question)
            
            # Reasoning bilgisini göster (varsa)
            if hasattr(response.choices[0].message, 'reasoning_content') and response.choices[0].message.reasoning_content:
                print(f"\n🧠 Düşünce: {response.choices[0].message.reasoning_content[:300]}...")
            
            result = safe_parse_json(response.choices[0].message.content)
            
            if result is None:
                print("❌ Geçersiz cevap")
                continue
            
            print(f"\n📊 Model çıktısı: {result}")
        except Exception as e:
            print(f"❌ Hata: {e}")
            continue
        
        if result.get('start_line') and result.get('end_line'):
            start = result['start_line']
            end = result['end_line']
            
            print(f"\n📍 Bulunan satırlar: {start} - {end}")
            print("-" * 40)
            
            extracted_text = read_lines(policy_text, start, end)
            print(extracted_text)
            print("-" * 40)
        else:
            print("❌ İlgili bölüm bulunamadı")


if __name__ == "__main__":
    import sys
    
    print("""
╔══════════════════════════════════════════════════════════╗
║     SİGORTA POLİÇESİ SATIR BULUCU - TEST ARACI          ║
╠══════════════════════════════════════════════════════════╣
║  Kullanım:                                               ║
║    python test_line_finder.py mock   - Yapı testi       ║
║    python test_line_finder.py model  - Trendyol LLM     ║
║    python test_line_finder.py api    - OpenAI API       ║
║    python test_line_finder.py interactive - Etkileşimli ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "mock":
            test_with_mock()
        elif mode == "model":
            test_with_model()
        elif mode == "api":
            test_with_api()
        elif mode == "interactive":
            interactive_test()
        else:
            print("Geçersiz mod. Kullanım: mock | model | api | interactive")
    else:
        # Default: mock test
        test_with_mock()
        print("\n" + "=" * 60)
        print("Gerçek test için:")
        print("  uv run python test_line_finder.py model       # Trendyol LLM")
        print("  uv run python test_line_finder.py api         # OpenAI API")
        print("  uv run python test_line_finder.py interactive # Etkileşimli")
        print("=" * 60)
