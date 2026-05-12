"""Ihor/gliner-multi-edu modelini Türkçe NER (TWNERTC) ile fine-tune et.

Yaklaşım:
- GLiNER native trainer (transformers Trainer subclass) kullanılır
- SpanDataCollator + UniEncoderSpanProcessor (model'de hazır)
- MPS varsa MPS, yoksa CPU. CUDA yok varsayımı (Apple Silicon).
- Dataset: data/turkish_ner_gliner/{train,val}.json (convert_turkish_ner.py çıktısı)

Kullanım:
    .venv/bin/python scripts/gliner/finetune_turkish_ner.py \
        --base-model Ihor/gliner-multi-edu \
        --train data/turkish_ner_gliner/train.json \
        --val   data/turkish_ner_gliner/val.json \
        --output models/gliner-multi-edu-tr-finetuned \
        --epochs 2 --batch-size 4 --grad-accum 2 --lr 5e-6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "true")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
from gliner import GLiNER
from gliner.data_processing.collator import SpanDataCollator
from gliner.training import Trainer, TrainingArguments


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", default="Ihor/gliner-multi-edu")
    ap.add_argument("--train", default="data/turkish_ner_gliner/train.json")
    ap.add_argument("--val", default="data/turkish_ner_gliner/val.json")
    ap.add_argument("--output", default="models/gliner-multi-edu-tr-finetuned")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--others-lr", type=float, default=1e-5)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    ap.add_argument("--max-steps", type=int, default=-1, help="Limit steps (negative = unlimited)")
    ap.add_argument("--save-steps", type=int, default=200)
    ap.add_argument("--eval-steps", type=int, default=200)
    ap.add_argument("--logging-steps", type=int, default=25)
    ap.add_argument("--save-total-limit", type=int, default=2)
    ap.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
    args = ap.parse_args()

    train_path = Path(args.train)
    val_path = Path(args.val)
    if not train_path.exists() or not val_path.exists():
        print(f"Eğitim verileri eksik: {train_path}, {val_path}", file=sys.stderr)
        return 2

    train_data = json.loads(train_path.read_text(encoding="utf-8"))
    val_data = json.loads(val_path.read_text(encoding="utf-8"))
    print(f"[data] train={len(train_data)}, val={len(val_data)}")

    device = args.device if args.device != "auto" else _device()
    print(f"[setup] device: {device}")
    print(f"[setup] base model: {args.base_model}")

    # Modeli yükle
    t0 = time.perf_counter()
    model = GLiNER.from_pretrained(args.base_model)
    if device != "cpu":
        try:
            model = model.to(device)
            print(f"[setup] model -> {device}")
        except Exception as exc:
            print(f"[warn] {device} fallback to cpu: {exc}")
            device = "cpu"
    print(f"[setup] model loaded in {time.perf_counter() - t0:.1f}s")

    # Collator (UniEncoderSpanProcessor zaten modelde hazır)
    data_collator = SpanDataCollator(
        config=model.config,
        data_processor=model.data_processor,
        return_tokens=False,
        return_id_to_classes=False,
        return_entities=False,
        prepare_labels=True,
    )

    # TrainingArguments
    use_cpu = device == "cpu"
    train_args = TrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        others_lr=args.others_lr,
        weight_decay=args.weight_decay,
        others_weight_decay=args.weight_decay,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type="cosine",
        max_steps=args.max_steps,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="no",
        logging_steps=args.logging_steps,
        load_best_model_at_end=False,
        dataloader_num_workers=0,
        dataloader_pin_memory=False,
        use_cpu=use_cpu,
        report_to="none",
        remove_unused_columns=False,
        focal_loss_alpha=0.75,
        focal_loss_gamma=2.0,
        loss_reduction="sum",
        seed=42,
    )

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=train_data,
        eval_dataset=val_data,
        data_collator=data_collator,
    )

    print(f"[train] starting | epochs={args.epochs} batch={args.batch_size} "
          f"grad_accum={args.grad_accum} lr={args.lr} steps_per_epoch≈{len(train_data) // (args.batch_size * args.grad_accum)}")
    t0 = time.perf_counter()
    train_result = trainer.train()
    train_elapsed = time.perf_counter() - t0
    print(f"\n[done] train elapsed: {train_elapsed:.1f}s ({train_elapsed/60:.1f} min)")

    # Final save
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_dir = out_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(final_dir))
    print(f"[save] final model: {final_dir}")

    metrics = trainer.state.log_history
    summary = {
        "base_model": args.base_model,
        "train_size": len(train_data),
        "val_size": len(val_data),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "grad_accum": args.grad_accum,
        "lr": args.lr,
        "others_lr": args.others_lr,
        "device": device,
        "train_elapsed_s": round(train_elapsed, 1),
        "global_step": trainer.state.global_step,
        "best_metric": trainer.state.best_metric,
        "best_model_checkpoint": trainer.state.best_model_checkpoint,
        "log_history_tail": metrics[-10:] if metrics else [],
    }
    summary_path = out_dir / "training_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] summary: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
