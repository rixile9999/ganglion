"""Aggregate the dtype-pin matrix into a single decision-grade report.

Reads ``results/<hw>_<dtype>_seed<N>/`` for every cell that exists and emits a
Markdown report with:

  - Loss + runtime table across all cells
  - dtype-axis Δ (bf16 → fp32, holding HW + seed)
  - HW-axis Δ (M1 → CUDA, holding dtype + seed)
  - seed-axis variance (same HW + dtype, different seed) — the noise floor
  - LoRA init SHA divergence map (which cells share init, which don't)
  - eval exact_match comparison if eval_report.json files are present
  - Failure-set Jaccard between cell pairs (if eval failures.json present)
  - Decision verdict per docs §15.5 #3

Usage:

  python runs/factory_phase2/dtype_pin/analyze.py
  python runs/factory_phase2/dtype_pin/analyze.py --results-dir <path>
  python runs/factory_phase2/dtype_pin/analyze.py --no-eval  # train-side only

Eval inputs are optional. If only training cells are present, the script writes
a partial report flagged ``(loss-only)``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def load_cell(cell_dir: Path) -> dict[str, Any] | None:
    """Load all JSON artifacts for one cell, returning None if minimum missing."""
    train_path = cell_dir / "train_metrics.json"
    diag_path = cell_dir / "diag.json"
    env_path = cell_dir / "env.json"
    if not train_path.exists():
        return None
    out: dict[str, Any] = {"name": cell_dir.name, "dir": cell_dir}
    out["train"] = json.loads(train_path.read_text(encoding="utf-8"))
    if diag_path.exists():
        out["diag"] = json.loads(diag_path.read_text(encoding="utf-8"))
    if env_path.exists():
        out["env"] = json.loads(env_path.read_text(encoding="utf-8"))
    eval_path = cell_dir / "eval_report.json"
    if eval_path.exists():
        out["eval"] = json.loads(eval_path.read_text(encoding="utf-8"))
    failures_path = cell_dir / "failures.json"
    if failures_path.exists():
        out["failures"] = json.loads(failures_path.read_text(encoding="utf-8"))
    parts = cell_dir.name.split("_")
    if len(parts) == 3 and parts[2].startswith("seed"):
        out["hw"] = parts[0]
        out["dtype"] = parts[1]
        out["seed"] = int(parts[2][len("seed") :])
    return out


def collect(results_dir: Path) -> list[dict[str, Any]]:
    cells = []
    for sub in sorted(results_dir.iterdir()):
        if not sub.is_dir():
            continue
        cell = load_cell(sub)
        if cell is None:
            continue
        cells.append(cell)
    return cells


def fmt_loss(x: float | None) -> str:
    return f"{x:.4f}" if isinstance(x, (int, float)) else "—"


def fmt_pct(x: float | None) -> str:
    if not isinstance(x, (int, float)):
        return "—"
    return f"{x * 100:.1f}%"


def fmt_secs(x: float | None) -> str:
    if not isinstance(x, (int, float)):
        return "—"
    return f"{x:.0f}s"


def cell_table(cells: list[dict[str, Any]]) -> list[str]:
    md = ["| cell | hw | dtype | seed | loss | runtime | exact_match |"]
    md.append("|---|---|---|---:|---:|---:|---:|")
    for c in cells:
        loss = c["train"].get("train_loss")
        runtime = c["train"].get("train_runtime")
        exact = (c.get("eval") or {}).get("exact_match_rate")
        md.append(
            f"| {c['name']} | {c.get('hw', '?')} | {c.get('dtype', '?')} | "
            f"{c.get('seed', '?')} | {fmt_loss(loss)} | {fmt_secs(runtime)} | "
            f"{fmt_pct(exact)} |"
        )
    return md


def axis_deltas(cells: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Compute (dtype-axis, hw-axis, seed-axis) deltas where pairs exist."""
    by_key: dict[tuple[str, str, int], dict[str, Any]] = {
        (c["hw"], c["dtype"], c["seed"]): c for c in cells if "hw" in c
    }

    out: dict[str, list[str]] = {"dtype": [], "hw": [], "seed": []}

    seen_hw_seed: set = set()
    for (hw, dtype, seed), c in by_key.items():
        if (hw, seed) in seen_hw_seed:
            continue
        bf = by_key.get((hw, "bf16", seed))
        fp = by_key.get((hw, "fp32", seed))
        if bf and fp:
            d = fp["train"]["train_loss"] - bf["train"]["train_loss"]
            out["dtype"].append(
                f"- {hw} seed={seed}: bf16={fmt_loss(bf['train']['train_loss'])} "
                f"→ fp32={fmt_loss(fp['train']['train_loss'])} (Δ={d:+.4f})"
            )
            seen_hw_seed.add((hw, seed))

    seen_dtype_seed: set = set()
    for (hw, dtype, seed), c in by_key.items():
        if (dtype, seed) in seen_dtype_seed:
            continue
        m1 = by_key.get(("mps", dtype, seed))
        cu = by_key.get(("cuda", dtype, seed))
        if m1 and cu:
            d = cu["train"]["train_loss"] - m1["train"]["train_loss"]
            out["hw"].append(
                f"- {dtype} seed={seed}: mps={fmt_loss(m1['train']['train_loss'])} "
                f"→ cuda={fmt_loss(cu['train']['train_loss'])} (Δ={d:+.4f})"
            )
            seen_dtype_seed.add((dtype, seed))

    seen_hw_dtype: set = set()
    for (hw, dtype, _), c in by_key.items():
        if (hw, dtype) in seen_hw_dtype:
            continue
        seeds = [
            (s, by_key[(hw, dtype, s)]["train"]["train_loss"])
            for s in {sd for (h, d, sd) in by_key if h == hw and d == dtype}
        ]
        if len(seeds) >= 2:
            losses = [l for _, l in seeds]
            out["seed"].append(
                f"- {hw} {dtype}: seeds {sorted([s for s, _ in seeds])} → "
                f"losses {[round(l, 4) for l in losses]}, "
                f"stddev={statistics.stdev(losses):.4f}"
            )
            seen_hw_dtype.add((hw, dtype))

    return out


def init_sha_map(cells: list[dict[str, Any]]) -> list[str]:
    """For each cell, list the first lora_A SHA. Highlight ties and divergences."""
    md = ["| cell | first lora_A SHA[:16] |", "|---|---|"]
    for c in cells:
        diag = c.get("diag") or {}
        sha_list = diag.get("lora_a_sha256_first") or []
        first = sha_list[0][1] if sha_list else "—"
        md.append(f"| {c['name']} | `{first}` |")
    return md


def jaccard_failures(cells: list[dict[str, Any]]) -> list[str]:
    """Failure-set Jaccard between cell pairs (only if failures.json present)."""
    cells_with_fails = [c for c in cells if c.get("failures")]
    if len(cells_with_fails) < 2:
        return ["_(no eval failure files yet — re-run after eval_all.py lands)_"]
    md = ["| cell A | cell B | |A∩B| | |A∪B| | Jaccard |"]
    md.append("|---|---|---:|---:|---:|")
    sets = {c["name"]: {f["id"] for f in c["failures"]} for c in cells_with_fails}
    names = list(sets.keys())
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            inter = sets[a] & sets[b]
            union = sets[a] | sets[b]
            j = len(inter) / len(union) if union else 0.0
            md.append(f"| {a} | {b} | {len(inter)} | {len(union)} | {j:.3f} |")
    return md


def verdict(cells: list[dict[str, Any]]) -> list[str]:
    """Heuristic decision call per docs §15.5 #3 / plan §5 criteria."""
    by_key: dict[tuple[str, str], list[float]] = {}
    for c in cells:
        if "hw" not in c:
            continue
        by_key.setdefault((c["hw"], c["dtype"]), []).append(c["train"]["train_loss"])
    means = {k: statistics.fmean(v) for k, v in by_key.items()}

    md = ["## Verdict", ""]
    if not means:
        md.append("_insufficient data_")
        return md

    a = means.get(("mps", "bf16"))
    b = means.get(("mps", "fp32"))
    c_ = means.get(("cuda", "bf16"))
    d = means.get(("cuda", "fp32"))

    md.append(f"- mean loss A (mps,bf16) = {fmt_loss(a)}")
    md.append(f"- mean loss B (mps,fp32) = {fmt_loss(b)}")
    md.append(f"- mean loss C (cuda,bf16) = {fmt_loss(c_)}")
    md.append(f"- mean loss D (cuda,fp32) = {fmt_loss(d)}")
    md.append("")

    if all(x is not None for x in (a, b, c_, d)):
        gap_init = abs(a - c_)
        gap_after_dtype_pin = abs(b - d)
        md.append(f"- gap before dtype pin (A−C) = {gap_init:.4f}")
        md.append(f"- gap after  dtype pin (B−D) = {gap_after_dtype_pin:.4f}")
        if gap_after_dtype_pin <= max(0.005, 0.2 * gap_init):
            md.append("")
            md.append(
                "**H1 strongly supported**: pinning fp32 collapses the HW gap. "
                "Operational rule: M1 training acceptable with `--dtype fp32 "
                "--attn-impl eager`; M1 bf16 produces systematically under-trained "
                "adapters and should be forbidden for any final-quality run."
            )
        elif b - d > 0.005:
            md.append("")
            md.append(
                "**H2 likely**: dtype pin doesn't fully close the gap. Probable "
                "additional MPS precision losses outside bf16. Layer-by-layer "
                "audit recommended before declaring M1 fp32 acceptable."
            )
        else:
            md.append("")
            md.append("(no clean call — see numbers above)")
    elif a is not None and b is not None and c_ is None and d is None:
        md.append(
            "_M1 cells only — partial verdict_: bf16→fp32 on M1 closed loss by "
            f"{(a - b):+.4f}. Pending CUDA cells to confirm CUDA bf16 is "
            "near-saturated (i.e., CUDA bf16 ≈ CUDA fp32) and that the M1 fp32 "
            "loss matches the CUDA reference."
        )
    return md


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        default="runs/factory_phase2/dtype_pin/results",
        help="Where the per-cell directories live",
    )
    parser.add_argument(
        "--out",
        default="runs/factory_phase2/dtype_pin/dtype_pin_report.md",
        help="Output Markdown path",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    results_dir = (
        Path(args.results_dir)
        if Path(args.results_dir).is_absolute()
        else repo_root / args.results_dir
    )
    out_path = (
        Path(args.out) if Path(args.out).is_absolute() else repo_root / args.out
    )

    if not results_dir.exists():
        print(f"results dir not found: {results_dir}", file=sys.stderr)
        return 1

    cells = collect(results_dir)
    if not cells:
        print("no cells found", file=sys.stderr)
        return 1

    md: list[str] = ["# dtype-pin matrix report", ""]
    md.append(f"_{len(cells)} cells loaded from `{results_dir.relative_to(repo_root)}`_")
    md.append("")
    has_eval = any("eval" in c for c in cells)
    if not has_eval:
        md.append("> **(loss-only)** — no `eval_report.json` present yet. "
                  "Run eval phase to populate exact_match.")
        md.append("")

    md.append("## Cells")
    md.append("")
    md.extend(cell_table(cells))
    md.append("")

    deltas = axis_deltas(cells)
    if any(deltas.values()):
        md.append("## Axis deltas")
        md.append("")
        if deltas["dtype"]:
            md.append("**dtype axis** (bf16 → fp32, same hw + seed):")
            md.extend(deltas["dtype"])
            md.append("")
        if deltas["hw"]:
            md.append("**hw axis** (mps → cuda, same dtype + seed):")
            md.extend(deltas["hw"])
            md.append("")
        if deltas["seed"]:
            md.append("**seed axis** (same hw + dtype, across seeds — noise floor):")
            md.extend(deltas["seed"])
            md.append("")

    md.append("## LoRA init SHA map")
    md.append("")
    md.extend(init_sha_map(cells))
    md.append("")
    md.append(
        "Same SHA → identical init weights → loss differences purely from "
        "training-time numerics. Different SHA → init also differs (PEFT's "
        "Kaiming init went through a HW/dtype-dependent RNG path)."
    )
    md.append("")

    md.append("## Failure-set Jaccard (eval-side)")
    md.append("")
    md.extend(jaccard_failures(cells))
    md.append("")

    md.extend(verdict(cells))
    md.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {out_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
