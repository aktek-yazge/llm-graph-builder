"""Gemini 2.5 Flash ile OCR chunk'larını schema-driven annotate et.

Çıktı: data/synthetic_distill/annotations/{chunk_id}.json
Format:
    {
      "chunk_id": "...",
      "text": "...",
      "entities": [{"text": "AKSA AKRİLİK ...", "label": "şirket"}, ...],
      "model": "gemini-2.5-flash",
      "elapsed_s": 1.23,
      "raw": "<gemini raw output>"
    }

Hata logging: failed_chunks.json (chunk_id + sebep).

Kullanım:
    .venv/bin/python scripts/distill/annotate_with_gemini.py [--limit N] [--rerun]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

# .env dosyalarını sırayla yükle (kb-overlay yoksa celery_worker/.env'e bak)
for env_path in [
    REPO_ROOT / ".env",
    REPO_ROOT.parent / "celery_worker" / ".env",
    REPO_ROOT.parent / "backend" / ".env",
]:
    if env_path.exists():
        load_dotenv(env_path, override=False)


SCHEMA_LABELS_TR = [
    "şirket",
    "kişi",
    "denetçi firma",
    "iştirak şirket",
    "ortak",
    "sermayedar",
    "kurum",
    "yer",
    "tarih",
    "para tutarı",
    "görev_unvanı",
]


PROMPT_TEMPLATE = """Sen bir bilgi çıkarım uzmanısın. Aşağıdaki Türkçe Ticaret Sicili belgesinden \
şu etiket setine uygun entity'leri çıkar:

ETİKETLER:
- şirket: Şirket unvanı (örn. "AKSA AKRİLİK KİMYA SANAYİİ A.Ş.")
- kişi: Gerçek kişi adı (örn. "Hakan Apak")
- denetçi firma: Bağımsız denetim/SMMM firması (örn. "Ata Uluslararası Bağımsız Denetim ve SMMM A.Ş.")
- iştirak şirket: Şirketin iştirak ettiği firma (örn. "İZBAŞ A.Ş.")
- ortak: Pay sahibi gerçek/tüzel kişi
- sermayedar: Sermaye katılımcısı
- kurum: Kamu/devlet kurumu (SPK, MKK, EPDK, Bakanlık, vs.)
- yer: Adres, şehir, mevki (örn. "Taksim, İstanbul", "Bornova - İzmir")
- tarih: Tam veya kısmi tarih (örn. "09.03.2016", "2016 yılı")
- para tutarı: Para miktarı + birimi (örn. "425.000.000 TL", "$50 milyon")
- görev_unvanı: Resmi görev/unvan (örn. "Yönetim Kurulu Başkanı", "Genel Müdür")

KURALLAR:
- Sadece metinde GERÇEKTEN GEÇEN entity'leri çıkar (halüsinasyon YASAK)
- "Sayın", "Av.", "Sn.", "Dr." gibi unvanları kişi adına dahil etme
- Etiket adlarını YUKARIDAKİ listeden BİREBİR kullan (örn. "şirket", "denetçi firma")
- Karakter offset'i (start/end) verme, sadece text + label
- Eksik veya kısmi alıntılardan kaçın; metinde geçen tam ifadeyi yaz

ÇIKTI FORMATI (sadece geçerli JSON, kod fence olmadan):
{{
  "entities": [
    {{"text": "AKSA AKRİLİK KİMYA SANAYİİ A.Ş.", "label": "şirket"}},
    {{"text": "Hakan Apak", "label": "kişi"}}
  ]
}}

METİN:
{text}
"""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    # Triple backtick code fence
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def _parse_response(raw: str) -> tuple[list[dict] | None, str | None]:
    """Gemini cevabını parse et. (entities, error) döndürür."""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # JSON içinden ilk { ... } eşleşmesini bulmaya çalış
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                return None, f"json_decode: {e}"
        else:
            return None, f"json_decode: {e}"
    entities = data.get("entities")
    if not isinstance(entities, list):
        return None, "missing_entities_key"
    cleaned_ents: list[dict] = []
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        text = ent.get("text")
        label = ent.get("label")
        if not isinstance(text, str) or not isinstance(label, str):
            continue
        text = text.strip()
        label = label.strip()
        if not text or not label:
            continue
        cleaned_ents.append({"text": text, "label": label})
    return cleaned_ents, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="data/synthetic_distill/corpus_inventory.json")
    ap.add_argument("--out-dir", default="data/synthetic_distill/annotations")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--limit", type=int, default=0, help="Sadece N chunk işle (test için 0=hepsi)")
    ap.add_argument("--training-only", action="store_true", default=True,
                    help="Sadece for_training=True chunk'ları işle (default)")
    ap.add_argument("--rerun", action="store_true", help="Var olan annotation dosyalarını sil ve yeniden çalıştır")
    ap.add_argument("--max-requests", type=int, default=200, help="Maliyet kontrolü: üst sınır request sayısı")
    ap.add_argument("--sleep-s", type=float, default=1.0, help="Request'ler arası bekleme (rate limit)")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("[!] GEMINI_API_KEY veya GOOGLE_API_KEY bulunamadı.", file=sys.stderr)
        print("    .env dosyasında set edip tekrar dene (kb-overlay/.env veya celery_worker/.env).", file=sys.stderr)
        return 2

    inventory_path = REPO_ROOT / args.inventory
    if not inventory_path.exists():
        print(f"[!] Inventory yok: {inventory_path}", file=sys.stderr)
        return 2
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Chunk listesi
    chunks: list[dict] = []
    for doc in inventory["documents"]:
        for c in doc["chunks"]:
            if args.training_only and not c["for_training"]:
                continue
            chunks.append({
                "chunk_id": c["chunk_id"],
                "doc_id": doc["doc_id"],
                "company": doc["company"],
                "char_count": c["char_count"],
                "path": c["path"],
            })

    if args.limit > 0:
        chunks = chunks[: args.limit]

    print(f"[setup] model={args.model} chunks={len(chunks)} out={out_dir}")
    print(f"[setup] training_only={args.training_only} limit={args.limit} rerun={args.rerun}")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=8192,
        response_mime_type="application/json",
    )

    failed: list[dict] = []
    success = 0
    skipped = 0
    request_count = 0
    grand_t0 = time.perf_counter()

    for i, ch in enumerate(chunks, start=1):
        out_path = out_dir / f"{ch['chunk_id']}.json"
        if out_path.exists() and not args.rerun:
            skipped += 1
            continue

        if request_count >= args.max_requests:
            print(f"[!] max-requests limit ({args.max_requests}) aşıldı, durduruluyor")
            break

        chunk_path = REPO_ROOT / ch["path"]
        text = chunk_path.read_text(encoding="utf-8")
        prompt = PROMPT_TEMPLATE.format(text=text)

        t0 = time.perf_counter()
        try:
            resp = client.models.generate_content(
                model=args.model,
                contents=prompt,
                config=config,
            )
            request_count += 1
            elapsed = time.perf_counter() - t0
            raw = resp.text or ""
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            err = f"api_error: {type(exc).__name__}: {str(exc)[:200]}"
            print(f"  [{i}/{len(chunks)}] {ch['chunk_id']}: ❌ {err}")
            failed.append({"chunk_id": ch["chunk_id"], "error": err, "elapsed_s": round(elapsed, 2)})
            time.sleep(min(5.0, args.sleep_s * 2))
            continue

        entities, parse_err = _parse_response(raw)
        if entities is None:
            err = f"parse_error: {parse_err} | raw_preview={raw[:200]}"
            print(f"  [{i}/{len(chunks)}] {ch['chunk_id']}: ❌ {err}")
            failed.append({"chunk_id": ch["chunk_id"], "error": err, "elapsed_s": round(elapsed, 2)})
            time.sleep(args.sleep_s)
            continue

        record = {
            "chunk_id": ch["chunk_id"],
            "doc_id": ch["doc_id"],
            "company": ch["company"],
            "model": args.model,
            "char_count": ch["char_count"],
            "text": text,
            "entities": entities,
            "elapsed_s": round(elapsed, 2),
        }
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        success += 1
        labels_used = sorted(set(e["label"] for e in entities))
        print(f"  [{i}/{len(chunks)}] {ch['chunk_id']}: ✅ {elapsed:.1f}s {len(entities):2d} ent  labels={labels_used[:6]}")
        time.sleep(args.sleep_s)

    grand_elapsed = time.perf_counter() - grand_t0

    summary_path = REPO_ROOT / "data" / "synthetic_distill" / "annotation_summary.json"
    failed_path = REPO_ROOT / "data" / "synthetic_distill" / "failed_chunks.json"
    summary = {
        "model": args.model,
        "total_chunks_planned": len(chunks),
        "requests_made": request_count,
        "succeeded": success,
        "skipped_existing": skipped,
        "failed": len(failed),
        "elapsed_s": round(grand_elapsed, 1),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    failed_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"[ok] tamamlandı: {success} başarılı | {skipped} atlandı (mevcut) | {len(failed)} başarısız")
    print(f"     toplam süre: {grand_elapsed:.1f}s ({grand_elapsed/60:.1f} dk)")
    print(f"     request: {request_count} (~${request_count * 0.001:.3f} tahmini maliyet)")
    print(f"     summary: {summary_path}")
    if failed:
        print(f"     failed: {failed_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
