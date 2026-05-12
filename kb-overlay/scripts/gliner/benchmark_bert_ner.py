"""BERT-NER baseline benchmark — Aksa belgeleri.

`akdeniz27/mmbert-base-tr-uncased-ner` modelini test eder.
Sadece PER/ORG/LOC tipleri var (custom entity tipleri yok, GLiNER'dan farklı).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

try:
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
except ImportError as exc:
    raise SystemExit(f"transformers/torch eksik: {exc}")


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _strip_page_marker(text: str) -> str:
    return re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()


def _summarize(entities: list[dict]) -> dict:
    by_type: dict[str, list[str]] = defaultdict(list)
    for e in entities:
        by_type[e["entity_group"]].append(e["word"])
    return {
        "total": len(entities),
        "by_type": {k: len(v) for k, v in sorted(by_type.items())},
        "examples": {k: list(dict.fromkeys(v))[:5] for k, v in by_type.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="akdeniz27/mmbert-base-tr-uncased-ner")
    ap.add_argument(
        "--pages-dir",
        default="/Users/mehmeterdogan/python-projects/llm-graph-builder-yedek/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI/ocr_pages",
    )
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-length", type=int, default=512, help="Tokenizer max length per chunk")
    args = ap.parse_args()

    pages_dir = Path(args.pages_dir)
    page_files = sorted(pages_dir.glob("*.md"))
    if not page_files:
        print(f"Sayfa bulunamadı: {pages_dir}")
        return 2

    device = _device()
    device_idx = 0 if device != "cpu" else -1
    print(f"[setup] device: {device}")
    print(f"[setup] model:  {args.model}")
    print()

    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForTokenClassification.from_pretrained(args.model)
    ner = pipeline(
        "ner",
        model=model,
        tokenizer=tokenizer,
        aggregation_strategy="simple",
        device=device_idx if device != "cpu" else -1,
    )
    load_time = time.perf_counter() - t0
    print(f"[setup] model loaded in {load_time:.1f}s\n")

    overall: list[dict] = []
    page_summaries: list[dict] = []
    grand_t0 = time.perf_counter()

    for page in page_files:
        text = _strip_page_marker(page.read_text(encoding="utf-8"))
        print(f"=== {page.name} ({len(text)} char) ===")

        # NER pipeline tek seferde uzun metni handle eder (auto-chunk)
        t1 = time.perf_counter()
        try:
            entities = ner(text)
        except Exception as exc:
            print(f"  HATA: {exc}")
            page_summaries.append({"page": page.name, "error": str(exc)})
            continue
        elapsed = time.perf_counter() - t1
        # Pipeline numpy floats döndürür; jsonable hale getir
        entities = [
            {
                "entity_group": e["entity_group"],
                "word": e["word"],
                "score": float(e["score"]),
                "start": int(e["start"]),
                "end": int(e["end"]),
            }
            for e in entities
        ]
        summary = _summarize(entities)

        print(f"  elapsed: {elapsed:6.2f} s")
        print(f"  total:   {summary['total']}")
        for t, n in summary["by_type"].items():
            print(f"    {t:8s} {n:3d}  e.g. {summary['examples'][t][:3]}")
        print()

        page_summaries.append(
            {
                "page": page.name,
                "char_count": len(text),
                "elapsed_s": round(elapsed, 3),
                "total_entities": summary["total"],
                "by_type": summary["by_type"],
                "examples": summary["examples"],
            }
        )
        for e in entities:
            overall.append({"page": page.name, **e})

    grand_elapsed = time.perf_counter() - grand_t0
    overall_summary = _summarize(overall)

    print("=" * 60)
    print(f"TOPLAM (3 sayfa): {grand_elapsed:.2f} s")
    print(f"Toplam entity:    {overall_summary['total']}")
    print(f"Unique by type:")
    for t in overall_summary["by_type"]:
        unique = list(dict.fromkeys([e["word"] for e in overall if e["entity_group"] == t]))
        print(f"  {t:8s} {len(unique):3d} unique  / {overall_summary['by_type'][t]:3d} total mention")

    print("\nKritik entity check (substring search):")
    target_keywords = {
        "Ata": "Ata Uluslararası Bağımsız Denetim ve SMMM A.Ş.",
        "İzbaş": "İZBAŞ A.Ş. (Aksa iştiraki)",
        "İZBAŞ": "İZBAŞ A.Ş. (uppercase variant)",
        "Menderes": "MENDERES TEKSTİL (sermayedar)",
        "Kreston": "Kreston International",
    }
    for kw, label in target_keywords.items():
        hits = [e["word"] for e in overall if kw.lower() in e["word"].lower()]
        status = "✅" if hits else "❌"
        print(f"  {status} {label}  → {hits[:2] if hits else 'KAÇIRILDI'}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "device": device,
                    "load_time_s": round(load_time, 2),
                    "grand_elapsed_s": round(grand_elapsed, 2),
                    "page_summaries": page_summaries,
                    "overall_total": overall_summary["total"],
                    "overall_by_type": overall_summary["by_type"],
                    "all_entities": overall,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[saved] {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
