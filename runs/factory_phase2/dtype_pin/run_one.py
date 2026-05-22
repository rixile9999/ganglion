"""dtype-pin matrix runner — single cell.

Trains one cell of the M1↔CUDA × bf16↔fp32 dtype-pin matrix on a single box.

Cells:
  A: mps + bf16    B: mps + fp32    C: cuda + bf16    D: cuda + fp32

Run on the box whose HW matches ``--hw`` (the script asserts). Output goes to
``<out-root>/<hw>_<dtype>_seed<N>/`` containing:

  env.json            — platform, package versions, data SHA, git HEAD
  diag.json           — collected by train_lora.collect_training_diagnostics
  train_metrics.json  — final loss + runtime from TRL
  adapter/            — LoRA adapter (gitignored, large)

Eval is intentionally NOT done here — all 8 adapters are evaluated under one
canonical environment (CUDA bf16) afterward, so training-side dtype variation
is the only thing that varies during this experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from typing import Any


def set_all_seeds(seed: int) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    import numpy as np
    np.random.seed(seed)
    import torch
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch, "mps") and hasattr(torch.mps, "manual_seed"):
        try:
            torch.mps.manual_seed(seed)
        except Exception:
            pass


def detect_hw() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def package_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for pkg in ("torch", "transformers", "peft", "trl", "accelerate", "datasets"):
        try:
            mod = __import__(pkg)
            out[pkg] = getattr(mod, "__version__", "?")
        except ImportError:
            out[pkg] = None
    return out


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(repo_root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root
        ).decode().strip()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw", required=True, choices=["mps", "cuda"],
                        help="Expected HW backend; script asserts actual matches")
    parser.add_argument("--dtype", required=True, choices=["bf16", "fp32"])
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--catalog", default="iot_light_5")
    parser.add_argument(
        "--synth",
        default="runs/factory_phase2/sft_0.6B_v2/iot_light_5/augmented_train.jsonl",
    )
    parser.add_argument("--base-model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--bs", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument(
        "--attn-impl", default="eager",
        help="Force attention impl. 'eager' removes a HW-asymmetric variable; "
             "use --attn-impl='' to keep transformers default (sdpa).",
    )
    parser.add_argument("--out-root", default="runs/factory_phase2/dtype_pin/results")
    args = parser.parse_args()

    set_all_seeds(args.seed)

    actual_hw = detect_hw()
    if args.hw != actual_hw:
        print(
            f"[run_one] HW MISMATCH: --hw={args.hw} but detected {actual_hw}. "
            f"Refusing to run on the wrong box.",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parents[3]
    synth_path = (
        Path(args.synth) if Path(args.synth).is_absolute()
        else repo_root / args.synth
    )
    if not synth_path.exists():
        print(f"[run_one] synth file not found: {synth_path}", file=sys.stderr)
        return 3

    out_root = (
        Path(args.out_root) if Path(args.out_root).is_absolute()
        else repo_root / args.out_root
    )
    out_dir = out_root / f"{args.hw}_{args.dtype}_seed{args.seed}"
    out_dir.mkdir(parents=True, exist_ok=True)

    attn_impl: str | None = args.attn_impl if args.attn_impl else None

    env_info: dict[str, Any] = {
        "hw_requested": args.hw,
        "hw_detected": actual_hw,
        "dtype": args.dtype,
        "seed": args.seed,
        "attn_impl": attn_impl,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "packages": package_versions(),
        "synth_path": str(synth_path.relative_to(repo_root)),
        "synth_sha256": file_sha256(synth_path),
        "synth_lines": sum(1 for _ in synth_path.open("r", encoding="utf-8")),
        "PYTORCH_ENABLE_MPS_FALLBACK": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
        "git_head": git_head(repo_root),
        "args": vars(args),
    }
    (out_dir / "env.json").write_text(
        json.dumps(env_info, indent=2), encoding="utf-8"
    )
    print(f"[run_one] env recorded to {out_dir / 'env.json'}")
    print(f"[run_one] hw={args.hw} dtype={args.dtype} seed={args.seed} "
          f"attn={attn_impl} synth_sha={env_info['synth_sha256'][:12]}")

    from ganglion.factory.customer.ingest import ingest_schema
    from ganglion.factory.customer.synth import read_jsonl
    from ganglion.factory.customer.train_lora import TrainConfig, train_lora

    catalog = ingest_schema(args.catalog)
    examples = read_jsonl(synth_path)
    print(f"[run_one] catalog={catalog.name} examples={len(examples)}")

    cfg = TrainConfig(
        base_model=args.base_model,
        epochs=args.epochs,
        lora_rank=args.rank,
        per_device_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_seq_length=args.max_seq_length,
        seed=args.seed,
        bf16=(args.dtype == "bf16"),
        attn_implementation=attn_impl,
        diagnostic_path=str(out_dir / "diag.json"),
    )

    adapter_dir = train_lora(catalog, examples, out_dir, config=cfg)
    print(f"[run_one] adapter saved to {adapter_dir}")
    print(f"[run_one] cell complete: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
