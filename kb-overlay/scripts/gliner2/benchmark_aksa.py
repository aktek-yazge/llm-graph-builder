"""GLiNER2 zero-shot + hierarchical benchmark — Aksa belgeleri.

GLiNER2 (fastino/gliner2-multi-v1, 205M, mDeBERTa multilingual) test scripti.
gliner1 benchmark_aksa.py'nin yapısını birebir izler ama iki ayrı pass yapar:

    Pass A — Düz NER (extract_entities, gliner1 ile aynı etiketler)
    Pass B — Hierarchical (extract_json, parent→children schema)

Kullanım:
    python scripts/gliner2/benchmark_aksa.py \\
        --model fastino/gliner2-multi-v1 \\
        --pass a            # veya b veya both
        --out runs/gliner2_aksa_pass_a.json
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
    from gliner2 import GLiNER2
except ImportError as exc:
    raise SystemExit(f"gliner2/torch eksik: {exc}\n→ uv add gliner2")


SCHEMA_LABELS_EN = [
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


HIERARCHICAL_SCHEMA: dict = {
    "şirket": [
        "unvan::str::Şirketin tam ticari unvanı (A.Ş./Ltd. dahil)",
        "sicil_no::str::Ticaret sicil numarası",
        "sermaye::str::Esas sermaye tutarı (TL)",
        "denetçi::str::Bağımsız denetim firması veya SMMM unvanı",
        "iştirak::str::Bağlı ortaklık veya iştirak şirket adı",
        "adres::str::Şirket adresi (varsa)",
    ],
    "kişi": [
        "ad::str::Kişinin tam adı",
        "görev::str::Yönetim Kurulu Başkanı, üye, denetçi vb. unvan",
    ],
    "karar": [
        "tarih::str::Karar veya toplantı tarihi (GG.AA.YYYY)",
        "gündem_maddesi::str::Karar konusu / gündem maddesi",
        "oy_oranı::str::Lehte/aleyhte oy bilgisi",
    ],
    "sermayedar": [
        "ad::str::Ortağın veya sermayedarın adı",
        "pay_oranı::str::Sahip olunan pay oranı veya tutarı",
    ],
}


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _strip_page_marker(text: str) -> str:
    return re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()


def _flatten_pass_a(result: dict) -> list[dict]:
    """`extract_entities` çıktısını [{label,text}] listesine dönüştür.

    GLiNER2 dönüşü: {'entities': {'company': ['Apple','...'], 'person': [...]}}
    veya format_results=False ile span listesi.
    """
    out: list[dict] = []
    ents = result.get("entities", result) if isinstance(result, dict) else {}
    if isinstance(ents, dict):
        for label, items in ents.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict):
                    out.append({"label": label, "text": it.get("text", str(it))})
                else:
                    out.append({"label": label, "text": str(it)})
    elif isinstance(ents, list):
        for it in ents:
            if isinstance(it, dict):
                out.append({"label": it.get("label", "?"), "text": it.get("text", "")})
    return out


def _flatten_pass_b(result: dict) -> list[dict]:
    """Hierarchical extract_json çıktısını flat entity listesine indirger.

    Çıktı formatı: {'şirket': [{'unvan':'Aksa','denetçi':'Ata SMMM',...}, ...], ...}
    Her parent altındaki her objedeki her dolu field bir entity sayılır
    (label = '<parent>.<field>').
    """
    out: list[dict] = []
    if not isinstance(result, dict):
        return out
    for parent, items in result.items():
        if not isinstance(items, list):
            continue
        for obj in items:
            if not isinstance(obj, dict):
                continue
            for field, value in obj.items():
                if value is None or value == "" or value == []:
                    continue
                if isinstance(value, list):
                    for v in value:
                        if v:
                            out.append({"label": f"{parent}.{field}", "text": str(v)})
                else:
                    out.append({"label": f"{parent}.{field}", "text": str(value)})
    return out


def _summarize(entities: list[dict]) -> dict:
    by_type: dict[str, list[str]] = defaultdict(list)
    for e in entities:
        by_type[e["label"]].append(e["text"])
    return {
        "total": len(entities),
        "by_type": {k: len(v) for k, v in sorted(by_type.items())},
        "examples": {k: list(dict.fromkeys(v))[:5] for k, v in by_type.items()},
    }


def _critical_check(overall: list[dict]) -> dict:
    targets = {
        "Ata": "Ata Uluslararası Bağımsız Denetim ve SMMM A.Ş.",
        "İZBAŞ": "İZBAŞ A.Ş. (Aksa iştiraki)",
        "Menderes": "MENDERES TEKSTİL (sermayedar)",
        "Kreston": "Kreston International",
    }
    report = {}
    for kw, label in targets.items():
        hits = [e["text"] for e in overall if kw.lower() in e["text"].lower()]
        report[kw] = {"label": label, "found": bool(hits), "examples": hits[:3]}
        status = "✅" if hits else "❌"
        print(f"  {status} {label}  → {hits[:2] if hits else 'KAÇIRILDI'}")
    return report


def run_pass_a(
    model: GLiNER2,
    pages: list[Path],
    labels: list[str],
    threshold: float,
) -> dict:
    overall: list[dict] = []
    page_summaries: list[dict] = []
    grand_t0 = time.perf_counter()
    for page in pages:
        text = _strip_page_marker(page.read_text(encoding="utf-8"))
        print(f"=== {page.name} ({len(text)} char) ===")
        t0 = time.perf_counter()
        result = model.extract_entities(text, labels, threshold=threshold)
        elapsed = time.perf_counter() - t0
        ents = _flatten_pass_a(result)
        summary = _summarize(ents)
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
                "raw_result": result,
            }
        )
        for e in ents:
            overall.append({"page": page.name, **e})
    grand_elapsed = time.perf_counter() - grand_t0
    overall_summary = _summarize(overall)
    print("=" * 60)
    print(f"PASS A TOPLAM: {grand_elapsed:.2f} s | entity: {overall_summary['total']}")
    for t in overall_summary["by_type"]:
        unique = list(dict.fromkeys([e["text"] for e in overall if e["label"] == t]))
        print(f"  {t:25s} {len(unique):3d} unique  / {overall_summary['by_type'][t]:3d} total")
    print("\nKritik entity check (Pass A):")
    crit = _critical_check(overall)
    return {
        "pass": "A",
        "labels": labels,
        "threshold": threshold,
        "grand_elapsed_s": round(grand_elapsed, 2),
        "page_summaries": page_summaries,
        "overall_total": overall_summary["total"],
        "overall_by_type": overall_summary["by_type"],
        "all_entities": overall,
        "critical_check": crit,
    }


def run_pass_b(
    model: GLiNER2,
    pages: list[Path],
    schema: dict,
    threshold: float,
) -> dict:
    overall: list[dict] = []
    page_summaries: list[dict] = []
    grand_t0 = time.perf_counter()
    for page in pages:
        text = _strip_page_marker(page.read_text(encoding="utf-8"))
        print(f"=== {page.name} ({len(text)} char) ===")
        t0 = time.perf_counter()
        result = model.extract_json(text, schema, threshold=threshold)
        elapsed = time.perf_counter() - t0
        ents = _flatten_pass_b(result)
        summary = _summarize(ents)
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
                "raw_result": result,
            }
        )
        for e in ents:
            overall.append({"page": page.name, **e})
    grand_elapsed = time.perf_counter() - grand_t0
    overall_summary = _summarize(overall)
    print("=" * 60)
    print(f"PASS B TOPLAM: {grand_elapsed:.2f} s | entity: {overall_summary['total']}")
    for t in overall_summary["by_type"]:
        unique = list(dict.fromkeys([e["text"] for e in overall if e["label"] == t]))
        print(f"  {t:25s} {len(unique):3d} unique  / {overall_summary['by_type'][t]:3d} total")
    print("\nKritik entity check (Pass B):")
    crit = _critical_check(overall)
    return {
        "pass": "B",
        "schema": schema,
        "threshold": threshold,
        "grand_elapsed_s": round(grand_elapsed, 2),
        "page_summaries": page_summaries,
        "overall_total": overall_summary["total"],
        "overall_by_type": overall_summary["by_type"],
        "all_entities": overall,
        "critical_check": crit,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="fastino/gliner2-multi-v1")
    ap.add_argument(
        "--pages-dir",
        default="/Users/mehmeterdogan/python-projects/llm-graph-builder-yedek/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI/ocr_pages",
    )
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--labels", choices=["en", "tr", "both"], default="both")
    ap.add_argument("--pass-mode", dest="pass_mode", choices=["a", "b", "both"], default="both")
    ap.add_argument("--out-a", default="runs/gliner2_aksa_pass_a.json")
    ap.add_argument("--out-b", default="runs/gliner2_aksa_pass_b_hierarchical.json")
    args = ap.parse_args()

    pages_dir = Path(args.pages_dir)
    page_files = sorted(pages_dir.glob("*.md"))
    if not page_files:
        print(f"Sayfa bulunamadı: {pages_dir}")
        return 2

    if args.labels == "tr":
        labels = SCHEMA_LABELS_TR
    elif args.labels == "both":
        labels = SCHEMA_LABELS_EN + SCHEMA_LABELS_TR
    else:
        labels = SCHEMA_LABELS_EN

    device = _device()
    print(f"[setup] device: {device}")
    print(f"[setup] model:  {args.model}")
    print(f"[setup] pass:   {args.pass_mode}")
    print()

    t0 = time.perf_counter()
    model = GLiNER2.from_pretrained(args.model)
    if device != "cpu":
        try:
            model = model.to(device)
        except Exception as exc:
            print(f"[warn] {device} fallback to cpu: {exc}")
            device = "cpu"
    load_time = time.perf_counter() - t0
    print(f"[setup] model loaded in {load_time:.1f}s\n")

    bundle = {
        "model": args.model,
        "device": device,
        "load_time_s": round(load_time, 2),
        "pages_dir": str(pages_dir),
    }

    if args.pass_mode in ("a", "both"):
        print("\n" + "#" * 60)
        print("# PASS A — Düz NER (extract_entities)")
        print("#" * 60)
        pass_a = run_pass_a(model, page_files, labels, args.threshold)
        out_path = Path(args.out_a)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({**bundle, **pass_a}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[saved] {out_path}")

    if args.pass_mode in ("b", "both"):
        print("\n" + "#" * 60)
        print("# PASS B — Hierarchical (extract_json)")
        print("#" * 60)
        pass_b = run_pass_b(model, page_files, HIERARCHICAL_SCHEMA, args.threshold)
        out_path = Path(args.out_b)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({**bundle, **pass_b}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[saved] {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
