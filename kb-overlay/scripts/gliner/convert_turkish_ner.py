"""TWNERTC (erayyildiz/turkish_ner) → GLiNER native training format converter.

Dataset:
- 532K Wikipedia cümlesi (TWNERTC, Coarse Grained, Domain Independent, Noise Reduced)
- Etiketler: PERSON, ORGANIZATION, LOCATION, MISC (IOB2)
- Format: TSV, kolonlar = domain \\t tags \\t tokens

GLiNER native format:
    [{"tokenized_text": [tok1, tok2, ...], "ner": [[start_idx, end_idx_inclusive, label], ...]}, ...]

Strateji:
- Her cümle için bir GLiNER örneği üret
- Etiket dilini örnek-bazında rotasyon yaparak hem Türkçe hem İngilizce etiket
  öğrenmesi için modeli teşvik et (PERSON → "kişi" ya da "person", vs.)
- 90/10 train/val split, seed=42
- Domain dağılımını koruyacak şekilde stratified değil (basitlik için)
- Default sample boyutu: 8000 (MPS'te 30 dk training için)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

# Coarse-grained etiket → Türkçe / İngilizce sinonim havuzu
LABEL_SYNONYMS = {
    "PERSON": {"tr": ["kişi"], "en": ["person"]},
    "ORGANIZATION": {"tr": ["kurum", "şirket"], "en": ["organization"]},
    "LOCATION": {"tr": ["yer"], "en": ["location"]},
    "MISC": {"tr": ["muhtelif"], "en": ["miscellaneous"]},
}

LANG_CHOICES = ("tr", "en")


def parse_dump_line(line: str) -> tuple[str, list[str], list[str]] | None:
    """DUMP satırını ayrıştır: domain \\t tags \\t tokens."""
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 3:
        return None
    domain, tags, tokens = parts
    tags_list = tags.split(" ")
    tokens_list = tokens.split(" ")
    if len(tags_list) != len(tokens_list):
        return None
    if not tokens_list:
        return None
    return domain, tags_list, tokens_list


def iob_to_spans(tags: list[str]) -> list[tuple[int, int, str]]:
    """IOB → (start_inclusive, end_inclusive, type) span'lerine çevir."""
    spans: list[tuple[int, int, str]] = []
    cur_type: str | None = None
    cur_start: int | None = None

    for i, tag in enumerate(tags):
        if tag == "O":
            if cur_type is not None:
                spans.append((cur_start, i - 1, cur_type))
                cur_type = None
                cur_start = None
            continue
        if tag.startswith("B-"):
            if cur_type is not None:
                spans.append((cur_start, i - 1, cur_type))
            cur_type = tag[2:]
            cur_start = i
        elif tag.startswith("I-"):
            ent_type = tag[2:]
            if cur_type is None:
                cur_type = ent_type
                cur_start = i
            elif cur_type != ent_type:
                spans.append((cur_start, i - 1, cur_type))
                cur_type = ent_type
                cur_start = i
    if cur_type is not None:
        spans.append((cur_start, len(tags) - 1, cur_type))
    return spans


def to_gliner_example(
    tokens: list[str],
    spans: list[tuple[int, int, str]],
    rng: random.Random,
) -> dict:
    """GLiNER örneği üret. Etiket dilini örnek-bazında rotasyonla seç."""
    lang = rng.choice(LANG_CHOICES)
    ner = []
    for start, end, etype in spans:
        synonyms = LABEL_SYNONYMS.get(etype, {}).get(lang)
        if not synonyms:
            continue
        label = rng.choice(synonyms)
        ner.append([start, end, label])
    return {"tokenized_text": tokens, "ner": ner}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dump",
        default="data/turkish_ner/TWNERTC_TC_Coarse Grained NER_DomainIndependent_NoiseReduction.DUMP",
    )
    ap.add_argument("--out-dir", default="data/turkish_ner_gliner")
    ap.add_argument("--sample-size", type=int, default=8000)
    ap.add_argument("--val-ratio", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=128, help="Üst sınır token sayısı (uzunları at)")
    ap.add_argument("--min-tokens", type=int, default=4, help="Alt sınır token sayısı")
    ap.add_argument("--require-entity", action="store_true", default=True,
                    help="Sadece en az 1 entity'si olan cümleleri al")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    dump_path = Path(args.dump)
    if not dump_path.exists():
        print(f"DUMP dosyası bulunamadı: {dump_path}", file=sys.stderr)
        return 2

    # 1) Tüm satırları oku, parse et, filtrele
    print(f"[read] {dump_path}")
    examples: list[tuple[list[str], list[tuple[int, int, str]], str]] = []
    domain_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()
    skipped_no_ent = 0
    skipped_too_long = 0
    skipped_too_short = 0
    skipped_parse = 0

    with dump_path.open(encoding="utf-8") as f:
        for line in f:
            parsed = parse_dump_line(line)
            if parsed is None:
                skipped_parse += 1
                continue
            domain, tags, tokens = parsed
            if len(tokens) > args.max_tokens:
                skipped_too_long += 1
                continue
            if len(tokens) < args.min_tokens:
                skipped_too_short += 1
                continue
            spans = iob_to_spans(tags)
            if args.require_entity and not spans:
                skipped_no_ent += 1
                continue
            examples.append((tokens, spans, domain))
            domain_counter[domain] += 1
            for _, _, et in spans:
                label_counter[et] += 1

    print(f"[parsed] eligible={len(examples)} | "
          f"skipped: parse={skipped_parse}, no_ent={skipped_no_ent}, "
          f"too_long={skipped_too_long}, too_short={skipped_too_short}")

    # 2) Sample
    n_total = len(examples)
    if args.sample_size > 0 and n_total > args.sample_size:
        rng.shuffle(examples)
        examples = examples[: args.sample_size]
        print(f"[sample] sampled {len(examples)} of {n_total}")
    else:
        rng.shuffle(examples)
        print(f"[sample] using all {len(examples)} examples")

    # 3) Convert to GLiNER format
    converted = [to_gliner_example(toks, spans, rng) for toks, spans, _ in examples]

    # 4) Train/val split
    n_val = max(1, int(len(converted) * args.val_ratio))
    val = converted[:n_val]
    train = converted[n_val:]

    # 5) Save
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train.json"
    val_path = out_dir / "val.json"
    train_path.write_text(json.dumps(train, ensure_ascii=False, indent=None), encoding="utf-8")
    val_path.write_text(json.dumps(val, ensure_ascii=False, indent=None), encoding="utf-8")

    # 6) Stats
    sampled_label_counter: Counter[str] = Counter()
    for ex in train + val:
        for _, _, label in ex["ner"]:
            sampled_label_counter[label] += 1

    stats = {
        "source": str(dump_path),
        "total_in_dump_eligible": n_total,
        "sampled": len(converted),
        "train_size": len(train),
        "val_size": len(val),
        "domain_distribution_top10": domain_counter.most_common(10),
        "raw_label_counts": dict(label_counter),
        "sampled_label_counts_after_synonym_mapping": dict(sampled_label_counter),
        "label_synonyms": LABEL_SYNONYMS,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
    }
    stats_path = out_dir / "stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"[ok] train: {train_path} ({len(train)} ex)")
    print(f"[ok] val:   {val_path} ({len(val)} ex)")
    print(f"[ok] stats: {stats_path}")
    print()
    print("Etiket dağılımı (örneklem sonrası, sinonim mapping uygulanmış):")
    for lab, cnt in sorted(sampled_label_counter.items(), key=lambda x: -x[1]):
        print(f"  {lab:20s} {cnt:6d}")
    print()
    print("Domain dağılımı (top-10):")
    for dom, cnt in domain_counter.most_common(10):
        print(f"  {dom:20s} {cnt:6d}")
    print()
    print("İlk eğitim örneği:")
    print(json.dumps(train[0], ensure_ascii=False, indent=2)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
