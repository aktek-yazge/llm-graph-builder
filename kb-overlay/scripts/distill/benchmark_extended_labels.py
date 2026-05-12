"""Genişletilmiş etiket setiyle Aksa benchmark — sentetik distill ile öğrenilen
yeni etiketler (görev_unvanı, sermayedar) test edilir.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _strip_page_marker(text: str) -> str:
    return re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()


EXTENDED_LABELS = [
    "şirket",
    "kişi",
    "yer",
    "kurum",
    "tarih",
    "para tutarı",
    "denetçi firma",
    "iştirak şirket",
    "ortak",
    "sermayedar",
    "görev_unvanı",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True,
                    help="Karşılaştırılacak model checkpoint'leri")
    ap.add_argument(
        "--pages-dir",
        default=str(REPO_ROOT.parent / "celery_worker" / "output_celery"
                    / "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI" / "ocr_pages"),
    )
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--out", default="runs/gliner_aksa_extended_labels_compare.json")
    args = ap.parse_args()

    import torch
    from gliner import GLiNER

    pages_dir = Path(args.pages_dir)
    page_files = sorted(pages_dir.glob("*.md"))
    if not page_files:
        print(f"[!] Sayfa yok: {pages_dir}", file=sys.stderr)
        return 2

    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    results: dict[str, dict] = {}

    for model_path in args.models:
        print(f"\n=== model: {model_path} ===")
        model = GLiNER.from_pretrained(model_path)
        if device != "cpu":
            try:
                model = model.to(device)
            except Exception:
                pass

        per_label_unique: dict[str, set[str]] = defaultdict(set)
        per_label_total = defaultdict(int)
        all_entities: list[dict] = []
        page_summaries = []
        grand_t0 = time.perf_counter()

        for page in page_files:
            text = _strip_page_marker(page.read_text(encoding="utf-8"))
            t0 = time.perf_counter()
            ents = model.predict_entities(text, EXTENDED_LABELS, threshold=args.threshold)
            elapsed = time.perf_counter() - t0
            for e in ents:
                per_label_unique[e["label"]].add(e["text"].strip())
                per_label_total[e["label"]] += 1
                all_entities.append({"page": page.name, **e})
            page_summaries.append({"page": page.name, "elapsed_s": round(elapsed, 2), "entities": len(ents)})

        grand_elapsed = time.perf_counter() - grand_t0

        print(f"  süre: {grand_elapsed:.2f} s | toplam entity: {sum(per_label_total.values())}")
        print(f"  per-label (unique / total):")
        for lab in EXTENDED_LABELS:
            n_uniq = len(per_label_unique[lab])
            n_tot = per_label_total[lab]
            print(f"    {lab:18s} {n_uniq:3d} unique / {n_tot:3d} mention")

        # Kritik entity check
        critical = {
            "Ata Uluslararası SMMM": ["Ata", "SMMM"],
            "İZBAŞ": ["İZBAŞ", "İzbaş"],
            "MENDERES TEKSTİL": ["MENDERES"],
            "Kreston International": ["Kreston"],
        }
        crit_results = {}
        for name, kws in critical.items():
            hits = [e for e in all_entities if any(kw.lower() in e["text"].lower() for kw in kws)]
            crit_results[name] = {"hit": bool(hits), "examples": [(h["text"][:60], h["label"]) for h in hits[:2]]}

        results[model_path] = {
            "elapsed_s": round(grand_elapsed, 2),
            "total_entities": sum(per_label_total.values()),
            "per_label_unique": {k: len(v) for k, v in per_label_unique.items()},
            "per_label_total": dict(per_label_total),
            "critical": crit_results,
            "page_summaries": page_summaries,
        }

        del model

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"threshold": args.threshold, "labels": EXTENDED_LABELS, "device": device, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[saved] {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
