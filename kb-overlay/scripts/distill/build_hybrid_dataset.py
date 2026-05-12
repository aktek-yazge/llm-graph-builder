"""Sentetik annotation'ları GLiNER native format'a çevir, TWNERTC ile birleştir.

GLiNER native format:
    [{"tokenized_text": [tok1, tok2, ...], "ner": [[start_idx, end_idx_inclusive, label], ...]}]

Strateji:
- Annotation kayıtlarındaki düzleştirilmiş metni GLiNER'ın WordsSplitter'ı ile token'lara böl.
- Her entity text'i için karakter span'ı bul (`text.find` veya whitespace-tolerant).
- Karakter span'ından token span'ına dönüştür (token offset listesinden binary search).
- Bulamadığın entity'leri ATLA (Gemini halüsinasyonu varsa otomatik temizlik).
- TWNERTC ile birleştir: 70% TWNERTC + 30% sentetik (sentetik 3-4× oversample).
- Train/val split: 90/10 stratified by source.

Çıktı: data/hybrid_distill/{train,val,stats}.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from gliner.data_processing import WordsSplitter  # noqa: E402


def _tokenize_with_offsets(text: str, splitter: WordsSplitter) -> list[tuple[str, int, int]]:
    return list(splitter(text))


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _find_entity_char_span(text: str, entity_text: str) -> tuple[int, int] | None:
    """Entity text'ini metinde bul (önce exact, sonra whitespace-tolerant)."""
    if not entity_text:
        return None
    # Exact match
    idx = text.find(entity_text)
    if idx >= 0:
        return (idx, idx + len(entity_text))
    # Whitespace-tolerant: metni de düzleştirip bul, sonra orijinal offset'i tahmin et.
    ent_norm = _norm_ws(entity_text)
    if not ent_norm:
        return None
    # Build mapping from normalized index -> original index
    norm_chars: list[str] = []
    norm_to_orig: list[int] = []
    last_was_space = True  # leading-space-eat
    for i, ch in enumerate(text):
        if ch.isspace():
            if last_was_space:
                continue
            norm_chars.append(" ")
            norm_to_orig.append(i)
            last_was_space = True
        else:
            norm_chars.append(ch)
            norm_to_orig.append(i)
            last_was_space = False
    norm_text = "".join(norm_chars).strip()
    if not norm_text:
        return None
    j = norm_text.find(ent_norm)
    if j < 0:
        return None
    # Offset adjustment: we did .strip() at the end, so leading spaces removed.
    # Recover precise mapping.
    leading_skip = 0
    while leading_skip < len(norm_chars) and norm_chars[leading_skip] == " ":
        leading_skip += 1
    orig_start = norm_to_orig[leading_skip + j]
    end_idx = leading_skip + j + len(ent_norm) - 1
    if end_idx >= len(norm_to_orig):
        return None
    orig_end = norm_to_orig[end_idx] + 1
    return (orig_start, orig_end)


def _char_span_to_token_span(
    offsets: list[tuple[str, int, int]], char_start: int, char_end: int
) -> tuple[int, int] | None:
    """Karakter span'ını (start, end exclusive) token span'ına (start, end inclusive) çevir."""
    tok_start = -1
    tok_end = -1
    for ti, (_, ts, te) in enumerate(offsets):
        if tok_start == -1 and te > char_start and ts < char_end:
            tok_start = ti
        if ts < char_end:
            tok_end = ti
        elif tok_start != -1:
            break
    if tok_start == -1 or tok_end < tok_start:
        return None
    return (tok_start, tok_end)


def _annotation_to_gliner_examples(rec: dict, splitter: WordsSplitter, max_tokens: int = 256) -> tuple[list[dict], dict]:
    """Tek annotation kaydı → 1+ GLiNER örneği. Uzun chunk'lar parçalanır.

    Cümle-bazında çoklu mini örnek üretmek yerine basit yaklaşım: tek örnek üret.
    Token sayısı çok büyükse tek örneği split etmek karmaşıklaştırır; yerine basit
    token-window yapacağız (gerekiyorsa).
    """
    text = rec["text"]
    ents = rec["entities"]
    offsets = _tokenize_with_offsets(text, splitter)
    n_tokens = len(offsets)

    stats = {"input_entities": len(ents), "kept": 0, "skipped_not_found": 0, "skipped_zero_span": 0}
    spans: list[list] = []
    for ent in ents:
        et = ent["text"]
        label = ent["label"]
        char_span = _find_entity_char_span(text, et)
        if char_span is None:
            stats["skipped_not_found"] += 1
            continue
        cs, ce = char_span
        if ce <= cs:
            stats["skipped_zero_span"] += 1
            continue
        tok_span = _char_span_to_token_span(offsets, cs, ce)
        if tok_span is None:
            stats["skipped_not_found"] += 1
            continue
        ts, te = tok_span
        if te < ts or te >= n_tokens or ts < 0:
            stats["skipped_zero_span"] += 1
            continue
        spans.append([ts, te, label])
        stats["kept"] += 1

    # Token-window split: çok uzun ise pencere
    examples: list[dict] = []
    if n_tokens <= max_tokens:
        # Tek örnek
        examples.append({
            "tokenized_text": [t for t, _, _ in offsets],
            "ner": spans,
            "_source": "synthetic",
            "_chunk_id": rec["chunk_id"],
        })
    else:
        # Window split (overlap=0). Her pencerede o pencereye düşen entity'leri al.
        step = max_tokens
        for w_start in range(0, n_tokens, step):
            w_end = min(w_start + max_tokens, n_tokens)
            tokens_w = [t for t, _, _ in offsets[w_start:w_end]]
            spans_w = []
            for s, e, lab in spans:
                if s >= w_start and e < w_end:
                    spans_w.append([s - w_start, e - w_start, lab])
            examples.append({
                "tokenized_text": tokens_w,
                "ner": spans_w,
                "_source": "synthetic",
                "_chunk_id": f"{rec['chunk_id']}_w{w_start//step}",
            })
    return examples, stats


def _strip_internal_keys(ex: dict) -> dict:
    return {"tokenized_text": ex["tokenized_text"], "ner": ex["ner"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annotations", default="data/synthetic_distill/annotations")
    ap.add_argument("--twnertc-train", default="data/turkish_ner_gliner/train.json")
    ap.add_argument("--twnertc-val", default="data/turkish_ner_gliner/val.json")
    ap.add_argument("--out-dir", default="data/hybrid_distill")
    ap.add_argument("--synthetic-oversample", type=int, default=4,
                    help="Sentetik örnekleri kaç kez tekrarla (oversample for balance)")
    ap.add_argument("--twnertc-keep", type=float, default=1.0,
                    help="TWNERTC fraction to keep (1.0 = all 3600 train)")
    ap.add_argument("--val-ratio", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=96,
                    help="GLiNER örneği max token (TWNERTC ile uyumlu, MPS OOM önlemek için)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    splitter = WordsSplitter()

    # 1) Sentetik annotation'ları yükle
    ann_dir = REPO_ROOT / args.annotations
    ann_files = sorted(ann_dir.glob("*.json"))
    if not ann_files:
        print(f"[!] Annotation bulunamadı: {ann_dir}", file=sys.stderr)
        return 2

    synthetic_examples: list[dict] = []
    agg_stats = {"input_entities": 0, "kept": 0, "skipped_not_found": 0, "skipped_zero_span": 0}
    chunk_kept_by_label: Counter[str] = Counter()
    chunks_processed = 0
    skipped_empty_examples = 0
    for f in ann_files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        examples, stats = _annotation_to_gliner_examples(rec, splitter, max_tokens=args.max_tokens)
        # GLiNER trainer 0-entity örneklerle çökeren batch'lere yol açabiliyor —
        # empty pencere split'lerini ele.
        non_empty = [ex for ex in examples if ex["ner"]]
        skipped_empty_examples += len(examples) - len(non_empty)
        synthetic_examples.extend(non_empty)
        for k in agg_stats:
            agg_stats[k] += stats[k]
        for ex in non_empty:
            for _, _, label in ex["ner"]:
                chunk_kept_by_label[label] += 1
        chunks_processed += 1

    print(f"[synthetic] {chunks_processed} annotation -> {len(synthetic_examples)} GLiNER ex (boş örnek atıldı: {skipped_empty_examples})")
    print(f"  entity stats: {agg_stats}")
    if agg_stats["input_entities"] > 0:
        recovery = agg_stats["kept"] / agg_stats["input_entities"]
        print(f"  span recovery rate: {recovery:.1%}")
    print(f"  per-label kept: {dict(sorted(chunk_kept_by_label.items(), key=lambda x: -x[1]))}")

    # 2) TWNERTC train + val yükle
    twn_train_path = REPO_ROOT / args.twnertc_train
    twn_val_path = REPO_ROOT / args.twnertc_val
    twn_train = json.loads(twn_train_path.read_text(encoding="utf-8"))
    twn_val = json.loads(twn_val_path.read_text(encoding="utf-8"))
    print(f"[twnertc] train={len(twn_train)}, val={len(twn_val)}")

    if args.twnertc_keep < 1.0:
        n_keep = int(len(twn_train) * args.twnertc_keep)
        rng.shuffle(twn_train)
        twn_train = twn_train[:n_keep]
        print(f"  twnertc fraction: {args.twnertc_keep} -> {len(twn_train)} train")

    # Wrap with source tag for stats
    twn_train_tagged = [{"tokenized_text": ex["tokenized_text"], "ner": ex["ner"], "_source": "twnertc"} for ex in twn_train]
    twn_val_tagged = [{"tokenized_text": ex["tokenized_text"], "ner": ex["ner"], "_source": "twnertc"} for ex in twn_val]

    # 3) Sentetik örneklerden train/val split
    rng.shuffle(synthetic_examples)
    n_synth_val = max(1, int(len(synthetic_examples) * args.val_ratio))
    synthetic_val = synthetic_examples[:n_synth_val]
    synthetic_train = synthetic_examples[n_synth_val:]
    print(f"[synthetic-split] train={len(synthetic_train)} val={len(synthetic_val)}")

    # 4) Oversample sentetik train
    synthetic_train_over: list[dict] = []
    for _ in range(args.synthetic_oversample):
        synthetic_train_over.extend(synthetic_train)
    print(f"[synthetic-oversample] x{args.synthetic_oversample} -> {len(synthetic_train_over)} train ex")

    # 5) Birleştir + shuffle
    train_all = twn_train_tagged + synthetic_train_over
    val_all = twn_val_tagged + synthetic_val
    rng.shuffle(train_all)
    rng.shuffle(val_all)

    print(f"[hybrid] train_total={len(train_all)} val_total={len(val_all)}")

    # 6) Kaydet (internal _source key'ini at)
    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    train_clean = [_strip_internal_keys(ex) for ex in train_all]
    val_clean = [_strip_internal_keys(ex) for ex in val_all]
    train_path = out_dir / "train.json"
    val_path = out_dir / "val.json"
    train_path.write_text(json.dumps(train_clean, ensure_ascii=False), encoding="utf-8")
    val_path.write_text(json.dumps(val_clean, ensure_ascii=False), encoding="utf-8")

    # 7) Stats
    train_label_counter: Counter[str] = Counter()
    train_source_counter: Counter[str] = Counter()
    for ex in train_all:
        train_source_counter[ex["_source"]] += 1
        for _, _, lab in ex["ner"]:
            train_label_counter[lab] += 1
    val_label_counter: Counter[str] = Counter()
    val_source_counter: Counter[str] = Counter()
    for ex in val_all:
        val_source_counter[ex["_source"]] += 1
        for _, _, lab in ex["ner"]:
            val_label_counter[lab] += 1

    stats = {
        "synthetic_annotations": chunks_processed,
        "synthetic_examples_total": len(synthetic_examples),
        "synthetic_train": len(synthetic_train),
        "synthetic_val": len(synthetic_val),
        "synthetic_oversample": args.synthetic_oversample,
        "synthetic_train_oversampled": len(synthetic_train_over),
        "twnertc_train_used": len(twn_train_tagged),
        "twnertc_val_used": len(twn_val_tagged),
        "hybrid_train_total": len(train_all),
        "hybrid_val_total": len(val_all),
        "entity_recovery_stats": agg_stats,
        "train_label_distribution": dict(train_label_counter.most_common()),
        "val_label_distribution": dict(val_label_counter.most_common()),
        "train_source_distribution": dict(train_source_counter),
        "val_source_distribution": dict(val_source_counter),
    }
    stats_path = out_dir / "stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"[ok] train: {train_path} ({len(train_clean)} ex)")
    print(f"[ok] val:   {val_path}   ({len(val_clean)} ex)")
    print(f"[ok] stats: {stats_path}")
    print()
    print("Train kaynağı:", dict(train_source_counter))
    print("Train etiket top-12:")
    for lab, cnt in train_label_counter.most_common(12):
        print(f"  {lab:25s} {cnt:6d}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
