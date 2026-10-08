"""LoRA fine-tune of a small code model for rule enrichment (implementation.md 5.4).

Runs in the training environment (.venv-train: torch + transformers + peft):

    .venv-train/Scripts/python scripts/finetune.py --epochs 2

Base: Qwen2.5-Coder-1.5B-Instruct (Apache-2.0), bf16 + LoRA on all attention and
MLP projections, gradient checkpointing, loss only on the assistant answer.
Fits a 6 GB laptop GPU. Writes the adapter, a merged model (for Ollama import)
and a training log.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parent.parent


def ids(tok, msgs, generation_prompt: bool) -> list[int]:
    out = tok.apply_chat_template(msgs, tokenize=True, add_generation_prompt=generation_prompt)
    if isinstance(out, dict) or hasattr(out, "keys"):
        out = out["input_ids"]
    return list(out)


def encode(tok, example: dict, max_len: int) -> dict | None:
    msgs = example["messages"]
    prompt = ids(tok, msgs[:-1], True)
    full = ids(tok, msgs, False)
    if full[: len(prompt)] != prompt:
        raise ValueError("chat template: prompt is not a prefix of the full conversation")
    if len(full) > max_len:
        return None
    labels = [-100] * len(prompt) + full[len(prompt):]
    return {"input_ids": full, "labels": labels}


def load(path: Path, tok, max_len: int) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        enc = encode(tok, json.loads(line), max_len)
        if enc is None:
            skipped += 1
        else:
            rows.append(enc)
    return rows, skipped


@torch.no_grad()
def eval_loss(model, rows: list[dict], device: str) -> float:
    model.eval()
    total, n = 0.0, 0
    for r in rows:
        x = torch.tensor([r["input_ids"]], device=device)
        y = torch.tensor([r["labels"]], device=device)
        total += model(input_ids=x, labels=y).loss.item()
        n += 1
    model.train()
    return total / max(n, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=str(ROOT / "models" / "Qwen2.5-Coder-1.5B-Instruct"))
    ap.add_argument("--train", default=str(ROOT / "data" / "finetune" / "train.jsonl"))
    ap.add_argument("--val", default=str(ROOT / "data" / "finetune" / "val.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "models" / "cobol-enrich-1.5b"))
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--val-samples", type=int, default=48)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(args.base)
    train, skipped = load(Path(args.train), tok, args.max_len)
    val, _ = load(Path(args.val), tok, args.max_len)
    val = random.sample(val, min(args.val_samples, len(val)))
    lengths = sorted(len(r["input_ids"]) for r in train)
    print(f"train {len(train)} (skipped {skipped} > {args.max_len} tokens), val sample {len(val)}, "
          f"tokens median {lengths[len(lengths) // 2]} max {lengths[-1]}")

    model = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.bfloat16).to(device)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model.config.use_cache = False
    lora = LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    total_steps = math.ceil(len(train) * args.epochs / args.accum)
    warmup = max(1, int(0.06 * total_steps))

    def lr_at(step: int) -> float:
        if step < warmup:
            return args.lr * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return args.lr * 0.5 * (1 + math.cos(math.pi * progress))

    log = {"args": vars(args), "steps": [], "val": []}
    log["val"].append({"step": 0, "loss": eval_loss(model, val, device)})
    print(f"step 0 val loss {log['val'][-1]['loss']:.4f}")
    model.train()
    started = time.perf_counter()
    step, micro, running = 0, 0, 0.0
    n_micro = int(len(train) * args.epochs)
    order: list[int] = []
    while micro < n_micro:
        if not order:
            order = list(range(len(train)))
            random.shuffle(order)
        r = train[order.pop()]
        x = torch.tensor([r["input_ids"]], device=device)
        y = torch.tensor([r["labels"]], device=device)
        loss = model(input_ids=x, labels=y).loss / args.accum
        loss.backward()
        running += loss.item()
        micro += 1
        if micro % args.accum == 0 or micro == n_micro:
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            for g in opt.param_groups:
                g["lr"] = lr_at(step)
            opt.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            log["steps"].append({"step": step, "loss": round(running, 4), "lr": lr_at(step - 1)})
            if step % 10 == 0 or step == total_steps:
                rate = micro / (time.perf_counter() - started)
                print(f"step {step}/{total_steps} loss {running:.4f} lr {lr_at(step - 1):.2e} "
                      f"({rate:.2f} ex/s, mem {torch.cuda.max_memory_allocated() / 2**30:.2f} GB)", flush=True)
            running = 0.0
            if step % max(1, total_steps // 4) == 0 and step != total_steps:
                log["val"].append({"step": step, "loss": eval_loss(model, val, device)})
                print(f"step {step} val loss {log['val'][-1]['loss']:.4f}", flush=True)
    log["val"].append({"step": step, "loss": eval_loss(model, val, device)})
    log["train_seconds"] = round(time.perf_counter() - started, 1)
    log["peak_gpu_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    print(f"final val loss {log['val'][-1]['loss']:.4f}  ({log['train_seconds']}s, peak {log['peak_gpu_gb']} GB)")

    model.save_pretrained(out / "adapter")
    merged = model.merge_and_unload()
    merged.config.use_cache = True
    merged.save_pretrained(out / "merged", safe_serialization=True)
    tok.save_pretrained(out / "merged")
    (out / "train_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"saved adapter and merged model under {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
