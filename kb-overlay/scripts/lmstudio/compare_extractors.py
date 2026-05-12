"""LM Studio benchmark — NuExtract 2.0 vs Qwen3.6-35B-A3B vs Turkish-Gemma-9b.

Aksa belgelerinden entity extraction yaparak 3 modeli kalite + hız açısından
karşılaştırır. LM Studio local server (:1234) üzerinden OpenAI-uyumlu API kullanır.

Kullanım:
    python scripts/lmstudio/compare_extractors.py \
        --page "/path/to/page_001_ocr.md" \
        --models nuextract,qwen3.6,gemma \
        --out runs/lmstudio_aksa_p1.json

Notlar:
- NuExtract 2.0 kendi template formatını ister (verbatim-string schema).
  Bu script template'i otomatik kurar.
- Qwen3.6 ve Gemma için geleneksel JSON-schema prompt kullanılır.
- Reproducibility için temperature=0.1, seed=42.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import openai
except ImportError:
    sys.stderr.write("openai paketi gerekli: pip install openai\n")
    sys.exit(2)


LMSTUDIO_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_KEY = "lm-studio"  # ignored by server but required by client


# ---------------------------------------------------------------------------
# Şemalar
# ---------------------------------------------------------------------------

NUEXTRACT_TEMPLATE = {
    "companies": [
        {
            "name": "verbatim-string",
            "legal_form": "verbatim-string",
            "registration_number": "verbatim-string",
            "address": "verbatim-string",
        }
    ],
    "persons": [
        {
            "full_name": "verbatim-string",
            "role": "verbatim-string",
        }
    ],
    "locations": ["verbatim-string"],
    "institutions": ["verbatim-string"],
    "dates": ["verbatim-string"],
    "monetary_amounts": ["verbatim-string"],
}

GENERIC_JSON_SCHEMA_PROMPT = """Aşağıdaki Türkçe Ticaret Sicil Gazetesi metninden entity'leri çıkar.
Sadece geçerli JSON dön, başka yazı ekleme. Şema:

{
  "companies": [
    {"name": "...", "legal_form": "A.Ş./Ltd./...", "registration_number": "...", "address": "..."}
  ],
  "persons": [
    {"full_name": "...", "role": "Yönetim Kurulu Başkanı/üye/..."}
  ],
  "locations": ["..."],
  "institutions": ["SPK", "MKK", ...],
  "dates": ["GG.AA.YYYY"],
  "monetary_amounts": ["1.000 TL", ...]
}

Kurallar:
- "Sayın", "Av." gibi unvanları person.full_name'e koyma
- Bir entity bulunmuyorsa o array boş kalır
- Halüsinasyon yapma, sadece metinde geçenleri al
- verbatim: kelimeyi metinden değiştirmeden al

METİN:
"""


# ---------------------------------------------------------------------------
# Model adapter'ları
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    model: str
    elapsed_s: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_response: str = ""
    parsed: dict | None = None
    parse_error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


def _client() -> openai.OpenAI:
    return openai.OpenAI(base_url=LMSTUDIO_BASE, api_key=LMSTUDIO_KEY)


def _strip_code_fence(s: str) -> str:
    """LLM bazen ```json ... ``` içine sarar; çıkar."""
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


def _count(parsed: dict | None) -> dict[str, int]:
    if not isinstance(parsed, dict):
        return {}
    counts: dict[str, int] = {}
    for k, v in parsed.items():
        if isinstance(v, list):
            counts[k] = len(v)
    return counts


def run_nuextract(model_id: str, text: str, max_tokens: int = 4096) -> ExtractionResult:
    """NuExtract 2.0 — template-driven extraction."""
    template_str = json.dumps(NUEXTRACT_TEMPLATE, ensure_ascii=False, indent=2)
    user_msg = f"# Template:\n{template_str}\n\n# Context:\n{text}"

    client = _client()
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.0,
        max_tokens=max_tokens,
        seed=42,
    )
    elapsed = time.perf_counter() - t0

    raw = resp.choices[0].message.content or ""
    parsed, err = _parse_json(raw)
    return ExtractionResult(
        model=model_id,
        elapsed_s=elapsed,
        prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0,
        completion_tokens=getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0,
        raw_response=raw,
        parsed=parsed,
        parse_error=err,
        counts=_count(parsed),
    )


def run_generic(model_id: str, text: str, max_tokens: int = 4096) -> ExtractionResult:
    """Qwen3.6 / Gemma için geleneksel JSON-schema prompt."""
    user_msg = GENERIC_JSON_SCHEMA_PROMPT + text

    client = _client()
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model_id,
        messages=[
            {
                "role": "system",
                "content": "Sen bir entity extraction uzmanısın. Sadece geçerli JSON dön, başka açıklama ekleme.",
            },
            {"role": "user", "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        seed=42,
    )
    elapsed = time.perf_counter() - t0

    raw = resp.choices[0].message.content or ""
    parsed, err = _parse_json(raw)
    return ExtractionResult(
        model=model_id,
        elapsed_s=elapsed,
        prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0,
        completion_tokens=getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0,
        raw_response=raw,
        parsed=parsed,
        parse_error=err,
        counts=_count(parsed),
    )


MODEL_REGISTRY = {
    "nuextract": ("nuextract-2.0-8b", run_nuextract),
    "qwen3.6": ("qwen3.6-35b-a3b", run_generic),
    "gemma": ("turkish-gemma-9b", run_generic),
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True, help="OCR sayfası (.md veya .txt)")
    ap.add_argument(
        "--models",
        default="nuextract,qwen3.6,gemma",
        help="Virgülle ayrılmış model alias'ları (nuextract,qwen3.6,gemma)",
    )
    ap.add_argument("--out", default=None, help="JSON çıktı dosyası (opsiyonel)")
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()

    page_path = Path(args.page)
    if not page_path.is_file():
        sys.stderr.write(f"Sayfa bulunamadı: {page_path}\n")
        return 2
    text = page_path.read_text(encoding="utf-8")
    text = re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()
    print(f"[input] {page_path.name} — {len(text)} char\n")

    aliases = [a.strip() for a in args.models.split(",") if a.strip()]
    results: list[dict] = []

    for alias in aliases:
        if alias not in MODEL_REGISTRY:
            print(f"[skip] bilinmeyen model alias: {alias}")
            continue
        model_id, runner = MODEL_REGISTRY[alias]
        print(f"\n=== {alias} ({model_id}) ===")
        try:
            r = runner(model_id, text, max_tokens=args.max_tokens)
        except Exception as exc:
            print(f"  HATA: {exc}")
            results.append({"alias": alias, "model": model_id, "error": str(exc)})
            continue

        tps = (r.completion_tokens / r.elapsed_s) if r.elapsed_s > 0 else 0
        print(f"  elapsed:    {r.elapsed_s:7.2f} s")
        print(f"  prompt:     {r.prompt_tokens:5d} tok")
        print(f"  completion: {r.completion_tokens:5d} tok ({tps:.1f} tok/s)")
        if r.parse_error:
            print(f"  parse_err:  {r.parse_error}")
            print(f"  raw[:300]:  {r.raw_response[:300]}")
        else:
            print(f"  counts:     {r.counts}")
            if r.parsed:
                companies = r.parsed.get("companies") or []
                persons = r.parsed.get("persons") or []
                if companies:
                    print(f"  ─ companies örnek: {companies[:2]}")
                if persons:
                    print(f"  ─ persons örnek:   {persons[:3]}")

        results.append(
            {
                "alias": alias,
                "model": model_id,
                "elapsed_s": round(r.elapsed_s, 3),
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "tokens_per_s": round(tps, 2),
                "parse_error": r.parse_error,
                "counts": r.counts,
                "parsed": r.parsed,
                "raw_response": r.raw_response if r.parse_error else None,
            }
        )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {"page": page_path.name, "results": results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[saved] {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
