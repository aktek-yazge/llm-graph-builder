"""GLiNER zero-shot benchmark — Aksa belgeleri.

Schema-driven NER ile entity extraction. LLM'lere alternatif olarak
hız + maliyet kazancı için test ediyoruz.

Kullanım:
    python scripts/gliner/benchmark_aksa.py \
        --model Ihor/gliner-multi-edu \
        --pages-dir "/path/to/ocr_pages"

Notlar:
- `Ihor/gliner-multi-edu` Jan 2026 multilingual GLiNER (24+ dil, sentetik veri)
- `urchade/gliner_multi-v2.1` resmi multilingual baseline
- Apple Silicon'da MPS backend kullanır (CPU fallback)
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

try:
    import torch
    from gliner import GLiNER
except ImportError as exc:
    raise SystemExit(f"gliner/torch eksik: {exc}\n→ uv add gliner")


SCHEMA_LABELS = [
    "company",
    "person",
    "location",
    "organization",
    "date",
    "monetary amount",
    "auditor company",
    "subsidiary company",
    "shareholder",
]

SCHEMA_LABELS_TR = [
    "şirket",
    "kişi",
    "yer",
    "kurum",
    "tarih",
    "para tutarı",
    "denetçi firma",
    "iştirak şirket",
    "ortak",
]


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _strip_page_marker(text: str) -> str:
    return re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()


def run_page(model: GLiNER, text: str, labels: list[str], threshold: float = 0.4) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    entities = model.predict_entities(text, labels, threshold=threshold)
    elapsed = time.perf_counter() - t0
    return entities, elapsed


def _summarize(entities: list[dict]) -> dict:
    by_type: dict[str, list[str]] = defaultdict(list)
    for e in entities:
        by_type[e["label"]].append(e["text"])
    return {
        "total": len(entities),
        "by_type": {k: len(v) for k, v in sorted(by_type.items())},
        "examples": {k: list(dict.fromkeys(v))[:5] for k, v in by_type.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Ihor/gliner-multi-edu")
    ap.add_argument(
        "--pages-dir",
        default="/Users/mehmeterdogan/python-projects/llm-graph-builder-yedek/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI/ocr_pages",
    )
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--labels", choices=["en", "tr", "both"], default="en")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pages_dir = Path(args.pages_dir)
    page_files = sorted(pages_dir.glob("*.md"))
    if not page_files:
        print(f"Sayfa bulunamadı: {pages_dir}")
        return 2

    if args.labels == "tr":
        labels = SCHEMA_LABELS_TR
    elif args.labels == "both":
        labels = SCHEMA_LABELS + SCHEMA_LABELS_TR
    else:
        labels = SCHEMA_LABELS

    device = _device()
    print(f"[setup] device: {device}")
    print(f"[setup] model:  {args.model}")
    print(f"[setup] labels ({args.labels}): {labels}")
    print()

    t0 = time.perf_counter()
    model = GLiNER.from_pretrained(args.model)
    if device != "cpu":
        try:
            model = model.to(device)
        except Exception as exc:  # MPS bazı op'lara uymaz
            print(f"[warn] {device} fallback to cpu: {exc}")
    load_time = time.perf_counter() - t0
    print(f"[setup] model loaded in {load_time:.1f}s\n")

    overall: list[dict] = []
    page_summaries: list[dict] = []
    grand_t0 = time.perf_counter()

    for page in page_files:
        text = _strip_page_marker(page.read_text(encoding="utf-8"))
        print(f"=== {page.name} ({len(text)} char) ===")

        entities, elapsed = run_page(model, text, labels, threshold=args.threshold)
        summary = _summarize(entities)

        print(f"  elapsed: {elapsed:6.2f} s")
        print(f"  total:   {summary['total']}")
        for t, n in summary["by_type"].items():
            print(f"    {t:25s} {n:3d}  e.g. {summary['examples'][t][:3]}")
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
    for t, _ in overall_summary["by_type"].items():
        unique = list(dict.fromkeys([e["text"] for e in overall if e["label"] == t]))
        print(f"  {t:25s} {len(unique):3d} unique  / {overall_summary['by_type'][t]:3d} total mention")

    print("\nKritik entity check:")
    target_keywords = {
        "Ata": "Ata Uluslararası Bağımsız Denetim ve SMMM A.Ş.",
        "İZBAŞ": "İZBAŞ A.Ş. (Aksa iştiraki)",
        "Menderes": "MENDERES TEKSTİL (sermayedar)",
        "Kreston": "Kreston International",
    }
    for kw, label in target_keywords.items():
        hits = [e["text"] for e in overall if kw.lower() in e["text"].lower()]
        status = "✅" if hits else "❌"
        print(f"  {status} {label}  → {hits[:2] if hits else 'KAÇIRILDI'}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "labels_mode": args.labels,
                    "labels": labels,
                    "threshold": args.threshold,
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
