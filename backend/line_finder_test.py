"""
Sigorta Poliçesi Satır Bulucu - Modüler Test Aracı

Kullanım:
    python line_finder_test.py                          # Config'deki varsayılan test
    python line_finder_test.py --test-set kobi_tepe     # Belirli test seti
    python line_finder_test.py --model gemini-2.5-flash # Farklı model
    python line_finder_test.py --provider google        # Farklı provider
    python line_finder_test.py --interactive            # Etkileşimli mod
    python line_finder_test.py --list-models            # Mevcut modelleri listele
    
    # Sliding Window Modu (daha iyi doğruluk için)
    python line_finder_test.py --sliding                # Varsayılan 150 satır window, 50 overlap
    python line_finder_test.py --sliding -w 200 -o 75   # Özel window boyutu
    python line_finder_test.py -s -p anthropic -m claude-3-5-haiku-20241022  # Haiku + sliding
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import argparse

load_dotenv()

# ============================================
# CONFIG
# ============================================
CONFIG_FILE = Path(__file__).parent / "line_finder_config.json"

SYSTEM_PROMPT = """Sen metin analizi ve bilgi çıkarma uzmanısın.
Sana satır numaralı bir metin ve bir soru verilecek.
Soruyla DOĞRUDAN ilgili kısmın başlangıç ve bitiş satır numaralarını bul.

Kurallar:
1. Sadece JSON formatında cevap ver: {"start_line": X, "end_line": Y}
2. Birden fazla bölge varsa: [{"start_line": X1, "end_line": Y1}, {"start_line": X2, "end_line": Y2}]
3. İlgili kısım yoksa: {"start_line": null, "end_line": null}

Önemli:
- Metni BAŞTAN SONA tara - bilgi farklı sayfalarda/bölümlerde olabilir.
- TÜM ilgili bölgeleri bul, sadece ilkiyle yetinme.
- Her bölge için EN DAR ARALIĞI seç.
- Somut veriyi tercih et (sayı, tarih, isim, tablo).
- Başka açıklama ekleme, sadece JSON döndür."""


def load_config() -> dict:
    """Config dosyasını yükle."""
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_config(config: dict):
    """Config dosyasını kaydet."""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_policy_text(file_path: str) -> str:
    """Poliçe dosyasını yükler ve CHUNK taglerini temizler."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'</?CHUNK>', '', content)
    content = re.sub(r'\[PAGE BREAK\]', '--- SAYFA SONU ---', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()


def add_line_numbers(text: str) -> str:
    """Metne satır numaraları ekler."""
    lines = text.strip().split('\n')
    return '\n'.join([f"{i+1:4}| {line}" for i, line in enumerate(lines)])


def read_lines(text: str, start_line: int, end_line: int) -> str:
    """Metinden belirli satırları okur."""
    lines = text.split('\n')
    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)
    return '\n'.join(lines[start_idx:end_idx])


def safe_parse_json(content: str) -> dict | list | None:
    """JSON parse hatalarını yakala."""
    if not content or content.strip() == "":
        return None
    try:
        # Bazen model extra text ekliyor, JSON'ı çıkar
        json_match = re.search(r'[\[\{].*[\]\}]', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(content)
    except json.JSONDecodeError:
        return None


# ============================================
# SLIDING WINDOW
# ============================================

def create_windows(text: str, window_size: int = 150, overlap: int = 50) -> list[dict]:
    """
    Metni overlapping window'lara böl.
    Her window orijinal satır numaralarını korur.
    """
    lines = text.split('\n')
    total_lines = len(lines)
    windows = []
    
    start = 0
    while start < total_lines:
        end = min(start + window_size, total_lines)
        
        # Window içindeki satırları al
        window_lines = lines[start:end]
        
        # Orijinal satır numaralarıyla formatla
        numbered_window = '\n'.join([
            f"{start + i + 1:4}| {line}" 
            for i, line in enumerate(window_lines)
        ])
        
        windows.append({
            "start_line": start + 1,
            "end_line": end,
            "text": numbered_window,
            "line_count": end - start
        })
        
        # Sonraki window'a geç
        if end >= total_lines:
            break
        start += window_size - overlap
    
    return windows


def merge_results(all_results: list[dict]) -> list[dict]:
    """
    Birden fazla window'dan gelen sonuçları birleştir.
    Overlapping range'leri merge et.
    """
    if not all_results:
        return []
    
    # Tüm range'leri topla
    ranges = []
    for result in all_results:
        if isinstance(result, list):
            ranges.extend(result)
        elif result and result.get("start_line") and result.get("end_line"):
            ranges.append(result)
    
    if not ranges:
        return []
    
    # Başlangıç satırına göre sırala
    ranges.sort(key=lambda x: (x.get("start_line") or 0))
    
    # Overlapping range'leri birleştir
    merged = []
    current = ranges[0].copy()
    
    for r in ranges[1:]:
        if r.get("start_line") is None:
            continue
        # Overlap veya bitişik mi?
        if r["start_line"] <= current["end_line"] + 5:  # 5 satır tolerans
            current["end_line"] = max(current["end_line"], r["end_line"])
        else:
            merged.append(current)
            current = r.copy()
    
    merged.append(current)
    return merged


def sliding_window_find(
    text: str, 
    question: str, 
    provider: str, 
    model: str, 
    config: dict,
    window_size: int = 150,
    overlap: int = 50,
    verbose: bool = True
) -> list[dict]:
    """
    Sliding window ile satır bul.
    Metni parçalara böl, her parçada ara, sonuçları birleştir.
    """
    windows = create_windows(text, window_size, overlap)
    
    if verbose:
        print(f"  📊 {len(windows)} window oluşturuldu (her biri ~{window_size} satır, {overlap} overlap)")
    
    all_results = []
    
    for i, window in enumerate(windows):
        if verbose:
            print(f"  🔍 Window {i+1}/{len(windows)} (satır {window['start_line']}-{window['end_line']})...", end=" ")
        
        try:
            response = call_model(window["text"], question, provider, model, config)
            parsed = safe_parse_json(response["content"])
            
            if parsed:
                if verbose:
                    if isinstance(parsed, list):
                        ranges = [f"{r.get('start_line')}-{r.get('end_line')}" for r in parsed if r.get('start_line')]
                        print(f"✓ {ranges}")
                    elif parsed.get("start_line"):
                        print(f"✓ {parsed['start_line']}-{parsed['end_line']}")
                    else:
                        print("- (bulunamadı)")
                all_results.append(parsed)
            else:
                if verbose:
                    print("- (bulunamadı)")
        
        except Exception as e:
            if verbose:
                print(f"✗ Hata: {e}")
    
    # Sonuçları birleştir
    merged = merge_results(all_results)
    
    if verbose and merged:
        ranges_str = [f"{r['start_line']}-{r['end_line']}" for r in merged]
        print(f"  📍 Birleştirilmiş sonuç: {ranges_str}")
    
    return merged


# ============================================
# MODEL PROVIDERS
# ============================================

def call_openai(numbered_text: str, question: str, model: str, config: dict) -> dict:
    """OpenAI API çağrısı."""
    from openai import OpenAI
    client = OpenAI()
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Metin:\n{numbered_text}\n\nSoru: {question}"}
    ]
    
    # Reasoning modeller
    reasoning_models = ["gpt-5-mini", "o1", "o1-mini", "o1-preview", "o3-mini"]
    is_reasoning = any(rm in model.lower() for rm in reasoning_models)
    
    if is_reasoning:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=2000,
            reasoning_effort=config.get("model", {}).get("reasoning_effort", "low"),
            response_format={"type": "json_object"}
        )
    else:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"}
        )
    
    return {
        "content": response.choices[0].message.content,
        "model": model,
        "provider": "openai"
    }


def call_google(numbered_text: str, question: str, model: str, config: dict) -> dict:
    """Google Gemini API çağrısı."""
    import google.generativeai as genai
    
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    
    gemini_model = genai.GenerativeModel(
        model_name=model,
        generation_config={
            "temperature": 0,
            "response_mime_type": "application/json"
        },
        system_instruction=SYSTEM_PROMPT
    )
    
    response = gemini_model.generate_content(
        f"Metin:\n{numbered_text}\n\nSoru: {question}"
    )
    
    return {
        "content": response.text,
        "model": model,
        "provider": "google"
    }


def call_anthropic(numbered_text: str, question: str, model: str, config: dict) -> dict:
    """Anthropic Claude API çağrısı."""
    import anthropic
    
    client = anthropic.Anthropic()
    
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Metin:\n{numbered_text}\n\nSoru: {question}"}
        ]
    )
    
    return {
        "content": response.content[0].text,
        "model": model,
        "provider": "anthropic"
    }


# Global cache for local model (to avoid reloading)
_local_model_cache = {}


def call_local(numbered_text: str, question: str, model: str, config: dict) -> dict:
    """Yerel Hugging Face model çağrısı (Trendyol LLM vb.)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    
    global _local_model_cache
    
    # Model'i cache'den al veya yükle
    if model not in _local_model_cache:
        print(f"  ⏳ Model yükleniyor: {model}...")
        
        # Device seçimi
        if torch.backends.mps.is_available():
            device = torch.device("mps")
            print("  🍎 Apple Metal GPU (MPS) kullanılıyor")
        elif torch.cuda.is_available():
            device = torch.device("cuda")
            print("  🎮 NVIDIA CUDA GPU kullanılıyor")
        else:
            device = torch.device("cpu")
            print("  💻 CPU kullanılıyor (yavaş)")
        
        tokenizer = AutoTokenizer.from_pretrained(model)
        
        if device.type == "mps":
            loaded_model = AutoModelForCausalLM.from_pretrained(
                model,
                torch_dtype=torch.float16,
            ).to(device)
        else:
            loaded_model = AutoModelForCausalLM.from_pretrained(
                model,
                device_map="auto",
                torch_dtype=torch.float16,
            )
        
        _local_model_cache[model] = {
            "model": loaded_model,
            "tokenizer": tokenizer,
            "device": device
        }
        print(f"  ✅ Model yüklendi")
    
    cached = _local_model_cache[model]
    loaded_model = cached["model"]
    tokenizer = cached["tokenizer"]
    device = cached["device"]
    
    # Prompt oluştur
    prompt = f"""### Sistem:
{SYSTEM_PROMPT}

### Kullanıcı:
Metin:
{numbered_text}

Soru: {question}

### Asistan:
"""
    
    # Generate
    inputs = tokenizer(prompt, return_tensors="pt")
    input_ids = inputs.input_ids.to(device)
    
    with torch.no_grad():
        outputs = loaded_model.generate(
            input_ids,
            max_new_tokens=200,
            temperature=0.1,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    
    # Sadece yeni üretilen tokenları al
    new_tokens = outputs[0][input_ids.shape[1]:]
    response_text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    
    return {
        "content": response_text.strip(),
        "model": model,
        "provider": "local"
    }


def call_model(numbered_text: str, question: str, provider: str, model: str, config: dict) -> dict:
    """Provider'a göre uygun API'yi çağır."""
    providers = {
        "openai": call_openai,
        "google": call_google,
        "anthropic": call_anthropic,
        "local": call_local
    }
    
    if provider not in providers:
        raise ValueError(f"Bilinmeyen provider: {provider}")
    
    return providers[provider](numbered_text, question, model, config)


# ============================================
# TEST & RESULTS
# ============================================

def evaluate_result(extracted_text: str, expected_keywords: list) -> dict:
    """Sonucu değerlendir - beklenen anahtar kelimeler var mı?"""
    found = []
    missing = []
    
    for keyword in expected_keywords:
        if keyword.lower() in extracted_text.lower():
            found.append(keyword)
        else:
            missing.append(keyword)
    
    score = len(found) / len(expected_keywords) if expected_keywords else 1.0
    
    return {
        "score": score,
        "found_keywords": found,
        "missing_keywords": missing,
        "status": "pass" if score >= 0.5 else "fail"
    }


def run_test(test_set_name: str, provider: str, model: str, config: dict, use_sliding: bool = False, window_size: int = 150, overlap: int = 50) -> dict:
    """Belirli bir test setini çalıştır."""
    test_set = config["test_sets"][test_set_name]
    policy_text = load_policy_text(test_set["file"])
    numbered_text = add_line_numbers(policy_text)
    total_lines = len(policy_text.split('\n'))
    
    mode_str = f"SLIDING WINDOW ({window_size}/{overlap})" if use_sliding else "STANDARD"
    
    print(f"\n{'='*60}")
    print(f"TEST: {test_set_name}")
    print(f"Model: {provider}/{model}")
    print(f"Mod: {mode_str}")
    print(f"Dosya: {Path(test_set['file']).name}")
    print(f"Satır sayısı: {total_lines}")
    print(f"{'='*60}")
    
    results = {
        "test_set": test_set_name,
        "model": model,
        "provider": provider,
        "mode": "sliding" if use_sliding else "standard",
        "window_size": window_size if use_sliding else None,
        "overlap": overlap if use_sliding else None,
        "timestamp": datetime.now().isoformat(),
        "total_lines": total_lines,
        "questions": [],
        "summary": {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "by_category": {}
        }
    }
    
    for q in test_set["questions"]:
        print(f"\n{'─'*40}")
        print(f"[{q['category'].upper()}] {q['question']}")
        print(f"{'─'*40}")
        
        try:
            if use_sliding:
                # Sliding window modu
                parsed = sliding_window_find(
                    policy_text, q["question"], provider, model, config,
                    window_size=window_size, overlap=overlap, verbose=True
                )
                # sliding_window_find zaten liste döndürüyor
                if not parsed:
                    parsed = {"start_line": None, "end_line": None}
            else:
                # Standard mod
                response = call_model(numbered_text, q["question"], provider, model, config)
                parsed = safe_parse_json(response["content"])
            
            if parsed is None:
                print(f"  ⚠️ JSON parse hatası")
                q_result = {
                    "id": q["id"],
                    "category": q["category"],
                    "question": q["question"],
                    "status": "error",
                    "error": "JSON parse failed"
                }
            else:
                # Tek sonuç veya liste
                result_list = parsed if isinstance(parsed, list) else [parsed]
                all_text = ""
                line_ranges = []
                
                for r in result_list:
                    if r.get("start_line") and r.get("end_line"):
                        start = r["start_line"]
                        end = r["end_line"]
                        extracted = read_lines(policy_text, start, end)
                        all_text += extracted + "\n"
                        line_ranges.append(f"{start}-{end}")
                        
                        print(f"  📍 Satırlar: {start}-{end}")
                        # Kısa önizleme
                        preview = extracted[:150] + "..." if len(extracted) > 150 else extracted
                        print(f"  {preview}")
                
                if line_ranges:
                    evaluation = evaluate_result(all_text, q.get("expected_keywords", []))
                    status_icon = "✅" if evaluation["status"] == "pass" else "❌"
                    print(f"  {status_icon} Skor: {evaluation['score']:.0%}")
                    if evaluation["missing_keywords"]:
                        print(f"  ⚠️ Eksik: {evaluation['missing_keywords']}")
                    
                    q_result = {
                        "id": q["id"],
                        "category": q["category"],
                        "question": q["question"],
                        "line_ranges": line_ranges,
                        "extracted_text": all_text[:500],
                        "evaluation": evaluation,
                        "status": evaluation["status"]
                    }
                else:
                    print(f"  ❌ İlgili bölüm bulunamadı")
                    q_result = {
                        "id": q["id"],
                        "category": q["category"],
                        "question": q["question"],
                        "status": "not_found"
                    }
        
        except Exception as e:
            print(f"  ❌ Hata: {e}")
            q_result = {
                "id": q["id"],
                "category": q["category"],
                "question": q["question"],
                "status": "error",
                "error": str(e)
            }
        
        results["questions"].append(q_result)
        
        # Özet güncelle
        results["summary"]["total"] += 1
        if q_result["status"] == "pass":
            results["summary"]["passed"] += 1
        else:
            results["summary"]["failed"] += 1
        
        cat = q["category"]
        if cat not in results["summary"]["by_category"]:
            results["summary"]["by_category"][cat] = {"total": 0, "passed": 0}
        results["summary"]["by_category"][cat]["total"] += 1
        if q_result["status"] == "pass":
            results["summary"]["by_category"][cat]["passed"] += 1
    
    return results


def save_results(results: dict, config: dict):
    """Sonuçları dosyaya kaydet."""
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{results['test_set']}_{results['provider']}_{results['model']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = output_dir / filename
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 Sonuçlar kaydedildi: {filepath}")
    return filepath


def print_summary(results: dict):
    """Özet raporu yazdır."""
    print(f"\n{'='*60}")
    print("ÖZET RAPOR")
    print(f"{'='*60}")
    
    s = results["summary"]
    pass_rate = s["passed"] / s["total"] * 100 if s["total"] > 0 else 0
    
    print(f"Model: {results['provider']}/{results['model']}")
    print(f"Test Seti: {results['test_set']}")
    print(f"Toplam: {s['total']} soru")
    print(f"Başarılı: {s['passed']} ({pass_rate:.0f}%)")
    print(f"Başarısız: {s['failed']}")
    
    print(f"\nKategorilere göre:")
    for cat, stats in s["by_category"].items():
        cat_rate = stats["passed"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  {cat}: {stats['passed']}/{stats['total']} ({cat_rate:.0f}%)")


def interactive_mode(provider: str, model: str, config: dict):
    """Etkileşimli test modu."""
    print(f"\n{'='*60}")
    print("ETKİLEŞİMLİ MOD")
    print(f"Model: {provider}/{model}")
    print(f"{'='*60}")
    
    # Dosya seç
    print("\nMevcut test setleri:")
    for name, ts in config["test_sets"].items():
        print(f"  {name}: {Path(ts['file']).name}")
    
    test_set_name = input("\nTest seti seç (veya dosya yolu gir): ").strip()
    
    if test_set_name in config["test_sets"]:
        file_path = config["test_sets"][test_set_name]["file"]
    else:
        file_path = test_set_name
    
    if not Path(file_path).exists():
        print(f"❌ Dosya bulunamadı: {file_path}")
        return
    
    policy_text = load_policy_text(file_path)
    numbered_text = add_line_numbers(policy_text)
    print(f"\nDosya yüklendi: {Path(file_path).name}")
    print(f"Satır sayısı: {len(policy_text.split(chr(10)))}")
    
    print("\nSorularınızı yazın (çıkmak için 'q'):")
    
    while True:
        question = input("\n❓ Soru: ").strip()
        if question.lower() == 'q':
            break
        if not question:
            continue
        
        try:
            response = call_model(numbered_text, question, provider, model, config)
            parsed = safe_parse_json(response["content"])
            
            print(f"\n📊 Model çıktısı: {parsed}")
            
            if parsed:
                result_list = parsed if isinstance(parsed, list) else [parsed]
                for r in result_list:
                    if r.get("start_line") and r.get("end_line"):
                        start, end = r["start_line"], r["end_line"]
                        print(f"\n📍 Satırlar: {start}-{end}")
                        print("-" * 40)
                        print(read_lines(policy_text, start, end))
                        print("-" * 40)
            else:
                print("❌ İlgili bölüm bulunamadı")
        
        except Exception as e:
            print(f"❌ Hata: {e}")


def list_models(config: dict):
    """Mevcut modelleri listele."""
    print("\n📋 Mevcut Modeller:")
    for provider, models in config["available_models"].items():
        print(f"\n  {provider.upper()}:")
        for m in models:
            marker = " ← aktif" if (config["model"]["provider"] == provider and config["model"]["name"] == m) else ""
            print(f"    - {m}{marker}")


# ============================================
# MAIN
# ============================================

def main():
    parser = argparse.ArgumentParser(description="Sigorta Poliçesi Satır Bulucu Test Aracı")
    parser.add_argument("--test-set", "-t", help="Test seti adı (config'den)")
    parser.add_argument("--provider", "-p", help="Model provider (openai, google, anthropic, local)")
    parser.add_argument("--model", "-m", help="Model adı")
    parser.add_argument("--interactive", "-i", action="store_true", help="Etkileşimli mod")
    parser.add_argument("--list-models", "-l", action="store_true", help="Mevcut modelleri listele")
    parser.add_argument("--list-tests", action="store_true", help="Test setlerini listele")
    
    # Sliding window parametreleri
    parser.add_argument("--sliding", "-s", action="store_true", help="Sliding window modu")
    parser.add_argument("--window-size", "-w", type=int, default=150, help="Window boyutu (satır, varsayılan: 150)")
    parser.add_argument("--overlap", "-o", type=int, default=50, help="Overlap boyutu (satır, varsayılan: 50)")
    
    args = parser.parse_args()
    
    config = load_config()
    
    # Model ve provider seçimi
    provider = args.provider or config["model"]["provider"]
    model = args.model or config["model"]["name"]
    
    print("""
╔══════════════════════════════════════════════════════════╗
║     SİGORTA POLİÇESİ SATIR BULUCU - TEST ARACI          ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    if args.list_models:
        list_models(config)
        return
    
    if args.list_tests:
        print("\n📋 Mevcut Test Setleri:")
        for name, ts in config["test_sets"].items():
            print(f"\n  {name}:")
            print(f"    Dosya: {Path(ts['file']).name}")
            print(f"    Soru sayısı: {len(ts['questions'])}")
        return
    
    if args.interactive:
        interactive_mode(provider, model, config)
        return
    
    # Test çalıştır
    test_set_name = args.test_set or list(config["test_sets"].keys())[0]
    
    if test_set_name not in config["test_sets"]:
        print(f"❌ Test seti bulunamadı: {test_set_name}")
        print(f"Mevcut: {list(config['test_sets'].keys())}")
        return
    
    results = run_test(
        test_set_name, provider, model, config,
        use_sliding=args.sliding,
        window_size=args.window_size,
        overlap=args.overlap
    )
    print_summary(results)
    save_results(results, config)


if __name__ == "__main__":
    main()
