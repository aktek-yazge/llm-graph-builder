"""Hibrit fine-tune: TWNERTC checkpoint'inden devam, sentetik distill veriyle tamamla.

Önemli:
- Sıfırdan değil, mevcut `models/gliner-multi-edu-tr-finetuned/final` checkpoint'inden başla.
- LR'i biraz düşür (3e-6) — zaten fine-tuned bir model.
- Step sayısı: 300-500 (önceki 250 step'in üzerine).
- Batch 4 + grad_accum 2.
- MPS, fallback CPU.

Kullanım:
    .venv/bin/python scripts/distill/finetune_hybrid.py \
        --base models/gliner-multi-edu-tr-finetuned/final \
        --train data/hybrid_distill/train.json \
        --val data/hybrid_distill/val.json \
        --output models/gliner-aksa-hybrid \
        --max-steps 400 --batch-size 4 --grad-accum 2 --lr 3e-6
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

import torch  # noqa: E402
from gliner import GLiNER  # noqa: E402
from gliner.data_processing.collator import SpanDataCollator  # noqa: E402
from gliner.training import Trainer, TrainingArguments  # noqa: E402
from transformers import TrainerCallback  # noqa: E402


class MPSEmptyCacheCallback(TrainerCallback):
    """Her step sonrası MPS cache'i boşalt — uzun batch'lerin RAM'i şişirmesini önle."""

    def on_step_end(self, args, state, control, **kwargs):
        if torch.backends.mps.is_available():
            try:
                torch.mps.empty_cache()
            except Exception:
                pass


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="models/gliner-multi-edu-tr-finetuned/final")
    ap.add_argument("--train", default="data/hybrid_distill/train.json")
    ap.add_argument("--val", default="data/hybrid_distill/val.json")
    ap.add_argument("--output", default="models/gliner-aksa-hybrid")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--batch-size", type=int, default=2,
                    help="MPS OOM riskli, batch=2 + grad_accum=4 = effective 8")
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--lr", type=float, default=3e-6)
    ap.add_argument("--others-lr", type=float, default=6e-6)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--save-steps", type=int, default=200)
    ap.add_argument("--eval-steps", type=int, default=100)
    ap.add_argument("--logging-steps", type=int, default=20)
    ap.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
    ap.add_argument("--max-train-time-min", type=float, default=30.0)
    ap.add_argument("--optim", default="adafactor",
                    help="Optimizer: 'adafactor' (low mem, MPS friendly) | 'adamw_torch'")
    ap.add_argument("--max-grad-norm", type=float, default=1.0)
    ap.add_argument("--freeze-encoder", action="store_true", default=True,
                    help="token_rep_layer'ı dondur (MPS OOM önlemek için, sadece span/prompt head eğit)")
    ap.add_argument("--no-freeze-encoder", dest="freeze_encoder", action="store_false")
    args = ap.parse_args()

    train_path = Path(args.train)
    val_path = Path(args.val)
    if not train_path.exists() or not val_path.exists():
        print(f"[!] Veri eksik: {train_path}, {val_path}", file=sys.stderr)
        return 2

    train_data = json.loads(train_path.read_text(encoding="utf-8"))
    val_data = json.loads(val_path.read_text(encoding="utf-8"))
    print(f"[data] train={len(train_data)} val={len(val_data)}")

    device = args.device if args.device != "auto" else _device()
    print(f"[setup] device: {device}")
    print(f"[setup] base: {args.base}")

    t0 = time.perf_counter()
    model = GLiNER.from_pretrained(args.base)

    # Bellek için encoder'ı dondur (MPS OOM önlemek için).
    # Span head + prompt head + RNN + classifier eğitilir (~44M param).
    if args.freeze_encoder:
        for p in model.model.token_rep_layer.parameters():
            p.requires_grad = False
        n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
        n_total = sum(p.numel() for p in model.parameters())
        print(f"[setup] encoder donduruldu | trainable: {n_train/1e6:.1f}M / {n_total/1e6:.1f}M")

    if device != "cpu":
        try:
            model = model.to(device)
            print(f"[setup] model -> {device}")
        except Exception as exc:
            print(f"[warn] {device} fallback to cpu: {exc}")
            device = "cpu"
    print(f"[setup] model loaded in {time.perf_counter() - t0:.1f}s")

    data_collator = SpanDataCollator(
        config=model.config,
        data_processor=model.data_processor,
        return_tokens=False,
        return_id_to_classes=False,
        return_entities=False,
        prepare_labels=True,
    )

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
        optim=args.optim,
        max_grad_norm=args.max_grad_norm,
    )

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=train_data,
        eval_dataset=val_data,
        data_collator=data_collator,
        callbacks=[MPSEmptyCacheCallback()],
    )

    print(
        f"[train] starting | max_steps={args.max_steps} batch={args.batch_size} "
        f"grad_accum={args.grad_accum} lr={args.lr} effective_bs={args.batch_size * args.grad_accum}"
    )

    t_start = time.perf_counter()
    train_result = trainer.train()
    train_elapsed = time.perf_counter() - t_start
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
        "base_model": args.base,
        "train_size": len(train_data),
        "val_size": len(val_data),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "grad_accum": args.grad_accum,
        "max_steps": args.max_steps,
        "lr": args.lr,
        "others_lr": args.others_lr,
        "device": device,
        "train_elapsed_s": round(train_elapsed, 1),
        "global_step": trainer.state.global_step,
        "best_metric": trainer.state.best_metric,
        "log_history_tail": metrics[-12:] if metrics else [],
    }
    summary_path = out_dir / "training_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[save] summary: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
