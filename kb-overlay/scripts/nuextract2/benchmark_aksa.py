"""NuExtract 2.0 8B benchmark — Aksa belgeleri (page-by-page).

LM Studio (http://127.0.0.1:1234/v1) üzerinden `nuextract-2.0-8b` modelini
3 sayfa için tek tek çağırır. Çıktı format gliner2 benchmark'ları ile aynı
JSON yapısında olur ki final karşılaştırma matrisi tek formatta tutulsun.

Eğer LM Studio JIT-load açıksa model talep edildiğinde otomatik yüklenir.
Eğer transformers fallback gerekiyorsa --backend transformers kullanılır
(ama Aksa 32k bağlamı için MPS'de yavaş olur).

Kullanım:
    python scripts/nuextract2/benchmark_aksa.py \\
        --pages-dir "/path/to/ocr_pages" \\
        --out runs/nuextract2_aksa.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import openai
except ImportError:
    sys.stderr.write("openai paketi gerekli: uv add openai\n")
    sys.exit(2)


LMSTUDIO_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_KEY = "lm-studio"


# Hierarchical-ish template — gliner2 Pass B ile karşılaştırılabilir alanlar
NUEXTRACT_TEMPLATE: dict = {
    "şirket": [
        {
            "unvan": "verbatim-string",
            "sicil_no": "verbatim-string",
            "sermaye": "verbatim-string",
            "denetçi": "verbatim-string",
            "iştirak": "verbatim-string",
            "adres": "verbatim-string",
        }
    ],
    "kişi": [
        {
            "ad": "verbatim-string",
            "görev": "verbatim-string",
        }
    ],
    "iştirak": ["verbatim-string"],
    "ortak": ["verbatim-string"],
    "kurum": ["verbatim-string"],
    "yer": ["verbatim-string"],
    "tarih": ["verbatim-string"],
    "para_tutarı": ["verbatim-string"],
}


def _strip_page_marker(text: str) -> str:
    return re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    return m.group(1).strip() if m else s


def _parse_json(raw: str) -> tuple[dict | None, str | None]:
    cleaned = _strip_code_fence(raw)
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError as e:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0)), None
            except json.JSONDecodeError as e2:
                return None, f"json decode failed twice: {e2}"
        return None, f"json decode failed: {e}"


def _flatten_template_result(parsed: dict | None) -> list[dict]:
    """NuExtract template çıktısını flat [{label,text}] listesine indir."""
    out: list[dict] = []
    if not isinstance(parsed, dict):
        return out
    for key, value in parsed.items():
        if value is None:
            continue
        if isinstance(value, list):
            for it in value:
                if isinstance(it, dict):
                    for field, fval in it.items():
                        if fval in (None, "", []):
                            continue
                        if isinstance(fval, list):
                            for v in fval:
                                if v:
                                    out.append({"label": f"{key}.{field}", "text": str(v)})
                        else:
                            out.append({"label": f"{key}.{field}", "text": str(fval)})
                elif it not in (None, ""):
                    out.append({"label": key, "text": str(it)})
        elif isinstance(value, dict):
            for field, fval in value.items():
                if fval in (None, "", []):
                    continue
                out.append({"label": f"{key}.{field}", "text": str(fval)})
        else:
            out.append({"label": key, "text": str(value)})
    return out


def _summarize(entities: list[dict]) -> dict:
    by_type: dict[str, list[str]] = {}
    for e in entities:
        by_type.setdefault(e["label"], []).append(e["text"])
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


def run_nuextract_page(
    client: openai.OpenAI,
    model_id: str,
    text: str,
    template: dict,
    max_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    template_str = json.dumps(template, ensure_ascii=False, indent=2)
    user_msg = f"# Template:\n{template_str}\n\n# Context:\n{text}"
    t0 = time.perf_counter()
    resp = client.with_options(timeout=timeout).chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.0,
        max_tokens=max_tokens,
        seed=42,
    )
    elapsed = time.perf_counter() - t0
    raw = resp.choices[0].message.content or ""
    parsed, err = _parse_json(raw)
    return {
        "elapsed_s": elapsed,
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0,
        "completion_tokens": getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0,
        "raw_response": raw,
        "parsed": parsed,
        "parse_error": err,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", default="nuextract-2.0-8b")
    ap.add_argument(
        "--pages-dir",
        default="/Users/mehmeterdogan/python-projects/llm-graph-builder-yedek/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI/ocr_pages",
    )
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=float, default=300.0, help="Seconds per page request")
    ap.add_argument("--out", default="runs/nuextract2_aksa.json")
    args = ap.parse_args()

    pages_dir = Path(args.pages_dir)
    page_files = sorted(pages_dir.glob("*.md"))
    if not page_files:
        print(f"Sayfa bulunamadı: {pages_dir}")
        return 2

    print(f"[setup] LM Studio:  {LMSTUDIO_BASE}")
    print(f"[setup] model_id:   {args.model_id}")
    print(f"[setup] pages:      {len(page_files)}")
    print(f"[setup] timeout:    {args.timeout}s/page")
    print(f"[setup] template keys: {list(NUEXTRACT_TEMPLATE.keys())}")
    print()

    client = openai.OpenAI(base_url=LMSTUDIO_BASE, api_key=LMSTUDIO_KEY)

    overall: list[dict] = []
    page_summaries: list[dict] = []
    grand_t0 = time.perf_counter()

    for page in page_files:
        text = _strip_page_marker(page.read_text(encoding="utf-8"))
        print(f"=== {page.name} ({len(text)} char) ===")
        try:
            r = run_nuextract_page(
                client, args.model_id, text, NUEXTRACT_TEMPLATE, args.max_tokens, args.timeout
            )
        except Exception as exc:
            print(f"  HATA: {exc}")
            page_summaries.append(
                {
                    "page": page.name,
                    "char_count": len(text),
                    "error": str(exc),
                }
            )
            continue

        ents = _flatten_template_result(r["parsed"])
        summary = _summarize(ents)
        tps = (r["completion_tokens"] / r["elapsed_s"]) if r["elapsed_s"] > 0 else 0
        print(f"  elapsed:    {r['elapsed_s']:7.2f} s")
        print(f"  prompt:     {r['prompt_tokens']:5d} tok")
        print(f"  completion: {r['completion_tokens']:5d} tok ({tps:.1f} tok/s)")
        if r["parse_error"]:
            print(f"  parse_err:  {r['parse_error']}")
            print(f"  raw[:300]:  {r['raw_response'][:300]}")
        else:
            print(f"  total entity: {summary['total']}")
            for t, n in summary["by_type"].items():
                print(f"    {t:25s} {n:3d}  e.g. {summary['examples'][t][:3]}")
        print()

        page_summaries.append(
            {
                "page": page.name,
                "char_count": len(text),
                "elapsed_s": round(r["elapsed_s"], 3),
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "tokens_per_s": round(tps, 2),
                "parse_error": r["parse_error"],
                "parsed": r["parsed"],
                "total_entities": summary["total"],
                "by_type": summary["by_type"],
                "examples": summary["examples"],
                "raw_response": r["raw_response"] if r["parse_error"] else None,
            }
        )
        for e in ents:
            overall.append({"page": page.name, **e})

    grand_elapsed = time.perf_counter() - grand_t0
    overall_summary = _summarize(overall)
    print("=" * 60)
    print(f"NuExtract 2.0 8B TOPLAM (3 sayfa): {grand_elapsed:.2f} s")
    print(f"Toplam entity:  {overall_summary['total']}")
    for t in overall_summary["by_type"]:
        unique = list(dict.fromkeys([e["text"] for e in overall if e["label"] == t]))
        print(f"  {t:25s} {len(unique):3d} unique  / {overall_summary['by_type'][t]:3d} total")

    print("\nKritik entity check:")
    crit = _critical_check(overall)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "model": args.model_id,
                "backend": "lmstudio",
                "base_url": LMSTUDIO_BASE,
                "max_tokens": args.max_tokens,
                "template": NUEXTRACT_TEMPLATE,
                "pages_dir": str(pages_dir),
                "grand_elapsed_s": round(grand_elapsed, 2),
                "page_summaries": page_summaries,
                "overall_total": overall_summary["total"],
                "overall_by_type": overall_summary["by_type"],
                "all_entities": overall,
                "critical_check": crit,
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
