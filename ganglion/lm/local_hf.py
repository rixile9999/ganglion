"""In-process HF inference + held-out LoRA evaluation ([[lm_client]] §local HF).

Two halves share this module:

1. **Held-out evaluation** for a trained LoRA adapter (unchanged surface):
   ``split_train_eval()`` — stratified train/holdout split by strategy;
   ``evaluate_lora()`` — run inference on holdout, compute metrics;
   ``write_report()`` — Markdown + JSON report; ``write_split_jsonls()``.
   Reuses :mod:`ganglion.analyzer.metrics` (``CaseResult``, ``RunResult``,
   ``summarize``) so factory eval and the tier eval produce compatible JSON.

2. **Local model lifecycle + client** (2026-09-16 local-HF addendum):
   ``local_hf_available()``, the process-wide ``_MODEL_CACHE``,
   ``load_local_model()`` / ``unload_local_model()`` / ``local_model_status()``
   and ``LocalHFClient`` (a ``ModelClient`` over transformers + PEFT). The
   registry ([[lm_model_registry]]) builds the client; the console
   ([[console_operator]]) drives load / unload / status.

``torch`` / ``transformers`` are never imported at module import time —
every use is lazy inside a function — so importing this module (and the
registry that lazily reaches it) stays cheap on machines without the stack.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import random
import threading
import time
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.analyzer.metrics import CaseResult, RunResult, summarize
from ganglion.lm.finetune.sft import generate_dsl
from ganglion.lm.synth.pipeline import SynthExample

if TYPE_CHECKING:  # registry imports this module lazily; avoid the cycle at runtime
    from ganglion.lm.registry import ModelSpec

__all__ = [
    "EvalConfig",
    "LocalHFClient",
    "evaluate_lora",
    "load_local_model",
    "local_hf_available",
    "local_model_status",
    "split_train_eval",
    "unload_local_model",
    "write_report",
    "write_split_jsonls",
]


@dataclass(frozen=True)
class EvalConfig:
    max_new_tokens: int = 256
    temperature: float = 0.0
    # Optional XGrammar CompiledGrammar; when set, every generate() call is
    # masked to grammar-valid tokens. Build via
    # ``ganglion.factory.grammar.compile_catalog_grammar`` once per (catalog,
    # tokenizer) pair and reuse across the whole holdout.
    compiled_grammar: Any = None


def split_train_eval(
    examples: list[SynthExample],
    *,
    holdout_ratio: float = 0.2,
    seed: int = 42,
) -> tuple[list[SynthExample], list[SynthExample]]:
    """Stratified split: each strategy contributes ``holdout_ratio`` to holdout.

    For tool-anchored synth, ``strategy`` equals ``tool_anchored:<tool_name>``,
    so this guarantees every tool is represented in both splits (assuming the
    tool had at least 2 kept examples).
    """
    rng = random.Random(seed)
    by_strategy: dict[str, list[SynthExample]] = defaultdict(list)
    for ex in examples:
        by_strategy[ex.strategy].append(ex)

    train: list[SynthExample] = []
    holdout: list[SynthExample] = []
    for strategy, items in by_strategy.items():
        shuffled = list(items)
        rng.shuffle(shuffled)
        n_hold = max(1, int(round(len(shuffled) * holdout_ratio)))
        holdout.extend(shuffled[:n_hold])
        train.extend(shuffled[n_hold:])
    rng.shuffle(train)
    rng.shuffle(holdout)
    return train, holdout


def _release_device_memory() -> None:
    """Release per-iteration memory holds.

    Phase 1 evaluated 26 cases on CUDA whose caching allocator hides per-call
    KV-cache and intermediate-tensor leaks by recycling them in an internal
    pool. MPS (PyTorch 2.11) lacks an equivalent pool, so the same code on a
    long-context × many-case workload (smart_home_50: 92 cases × 1400-token
    context) accumulates 1-1.5 GB per call until the system swap-thrashes. An
    explicit gc + device cache flush closes the leak. No-op on CPU.
    """
    gc.collect()
    try:
        import torch  # local import: factory deps are optional
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, AttributeError):
        pass


def evaluate_lora(
    catalog: Catalog,
    holdout: Iterable[SynthExample],
    model,
    tokenizer,
    *,
    config: EvalConfig | None = None,
) -> tuple[dict[str, Any], list[CaseResult]]:
    """Run the model on each holdout example, return (summary, per-case results)."""
    cfg = config or EvalConfig()
    results: list[CaseResult] = []
    memory_log = os.environ.get("GANGLION_EVAL_MEMORY_LOG") == "1"
    psutil_proc = None
    if memory_log:
        try:
            import psutil
            psutil_proc = psutil.Process()
        except ImportError:
            memory_log = False
    holdout = list(holdout)
    n_total = len(holdout)
    for case_idx, ex in enumerate(holdout):
        case_id = hashlib.sha1(ex.intent.encode("utf-8")).hexdigest()[:8]
        try:
            expected_plan = catalog.parse_json_dsl(ex.expected_dsl)
        except DSLValidationError as exc:
            raise ValueError(
                f"holdout example has invalid expected DSL: {exc}"
            ) from exc

        started = time.perf_counter()
        try:
            raw_output = generate_dsl(
                model,
                tokenizer,
                catalog,
                ex.intent,
                max_new_tokens=cfg.max_new_tokens,
                temperature=cfg.temperature,
                compiled_grammar=cfg.compiled_grammar,
            )
        except Exception as exc:  # generation crashed
            run = RunResult(
                plan=None,
                raw={"intent": ex.intent},
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=None,
                output_tokens=None,
                error=f"generation failed: {exc}",
            )
            results.append(
                CaseResult(
                    id=case_id, prompt=ex.intent, expected=expected_plan, runs=(run,)
                )
            )
            continue
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            predicted_plan = catalog.parse_json_dsl(raw_output, prompt=ex.intent)
            error: str | None = None
        except DSLValidationError as exc:
            predicted_plan = None
            error = str(exc)
        except Exception as exc:
            predicted_plan = None
            error = f"unexpected: {exc}"

        run = RunResult(
            plan=predicted_plan,
            raw={"intent": ex.intent, "raw_output": raw_output},
            latency_ms=latency_ms,
            input_tokens=None,
            output_tokens=None,
            error=error,
        )
        results.append(
            CaseResult(id=case_id, prompt=ex.intent, expected=expected_plan, runs=(run,))
        )

        # Release per-call holds before next iteration. Without this, MPS
        # accumulates ~1-1.5 GB per call on long-context workloads.
        _release_device_memory()
        if memory_log and psutil_proc is not None and (case_idx + 1) % 10 == 0:
            rss_gb = psutil_proc.memory_info().rss / 1e9
            print(f"[eval] case {case_idx + 1}/{n_total}  RSS={rss_gb:.2f}GB",
                  flush=True)

    summary = summarize(results)
    summary["per_strategy"] = _per_strategy_breakdown(results, holdout)
    return summary, results


def _per_strategy_breakdown(
    results: list[CaseResult], holdout: Iterable[SynthExample]
) -> dict[str, dict[str, Any]]:
    """Compute exact / action match per strategy (≈ per-tool for tool-anchored)."""
    holdout_list = list(holdout)
    by_strategy: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n": 0, "valid": 0, "exact": 0, "action": 0}
    )
    for case, ex in zip(results, holdout_list):
        bucket = by_strategy[ex.strategy]
        bucket["n"] += 1
        if case.valid:
            bucket["valid"] += 1
        if case.exact_match:
            bucket["exact"] += 1
        if case.action_match:
            bucket["action"] += 1
    out: dict[str, dict[str, Any]] = {}
    for strategy, b in by_strategy.items():
        n = max(b["n"], 1)
        out[strategy] = {
            "n": b["n"],
            "syntax_valid": round(b["valid"] / n, 4),
            "exact_match": round(b["exact"] / n, 4),
            "action_match": round(b["action"] / n, 4),
        }
    return out


def write_report(
    summary: dict[str, Any],
    results: list[CaseResult],
    out_dir: Path,
    *,
    catalog_name: str,
    n_train: int,
    n_holdout: int,
) -> None:
    """Persist eval_report.json + eval_report.md."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "eval_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md: list[str] = [
        f"# Eval report — {catalog_name}",
        "",
        f"- train: {n_train}",
        f"- holdout: {n_holdout}",
        "",
        "## Headline metrics",
        "",
        f"- syntax_valid_rate: **{summary['syntax_valid_rate']:.1%}**",
        f"- exact_match_rate:  **{summary['exact_match_rate']:.1%}**",
        f"- action_match_rate: **{summary['action_match_rate']:.1%}**",
    ]
    if summary.get("latency_ms_p50") is not None:
        md.append(f"- latency P50: {summary['latency_ms_p50']:.0f} ms")
        md.append(f"- latency P95: {summary['latency_ms_p95']:.0f} ms")
    md.append("")

    md.append("## Per-strategy breakdown")
    md.append("")
    md.append("| strategy | n | syntax | action | exact |")
    md.append("|---|---|---|---|---|")
    for strategy, stats in summary.get("per_strategy", {}).items():
        md.append(
            f"| {strategy} | {stats['n']} | "
            f"{stats['syntax_valid']:.1%} | "
            f"{stats['action_match']:.1%} | "
            f"{stats['exact_match']:.1%} |"
        )
    md.append("")

    failures = summary.get("failures", []) or []
    if failures:
        md.append(f"## Failures ({len(failures)})")
        md.append("")
        for fail in failures[:20]:
            md.append(f"### `{fail['id']}`")
            md.append(f"**prompt:** {fail['prompt']}")
            md.append(f"**expected:** `{json.dumps(fail['expected'], ensure_ascii=False)}`")
            predicted = fail.get("predicted")
            if predicted is not None:
                md.append(
                    f"**predicted:** `{json.dumps(predicted, ensure_ascii=False)}`"
                )
            else:
                md.append("**predicted:** *(parse failed)*")
            if fail.get("error"):
                md.append(f"**error:** {fail['error']}")
            raw = fail.get("raw")
            if isinstance(raw, dict) and "raw_output" in raw:
                md.append(f"**raw:** `{raw['raw_output'][:200]}`")
            md.append("")

    (out_dir / "eval_report.md").write_text("\n".join(md), encoding="utf-8")


def write_split_jsonls(
    train: list[SynthExample],
    holdout: list[SynthExample],
    out_dir: Path,
) -> None:
    """Persist the train/holdout split as separate JSONL files."""
    from ganglion.lm.synth.pipeline import write_jsonl

    out_dir = Path(out_dir)
    write_jsonl(train, out_dir / "train.jsonl")
    write_jsonl(holdout, out_dir / "holdout.jsonl")


# ---------------------------------------------------------------------------
# Local model lifecycle ([[lm_client]] §local HF, consumed by
# [[lm_model_registry]].availability and [[console_operator]] load/unload)
# ---------------------------------------------------------------------------

#: ``(base_model, adapter_dir, dtype, device) → (model, tokenizer)``.
#: Process-wide and shared across catalogs — a catalog changes the prompt,
#: never the weights. ``unload_local_model`` is the only eviction.
_MODEL_CACHE: dict[tuple[str, str | None, str, str], tuple[Any, Any]] = {}
_LOAD_LOCK = threading.Lock()
_SUPPORTED_DTYPES: tuple[str, ...] = ("bfloat16", "float32")
_GRAMMAR_WARNED = False


def local_hf_available() -> tuple[bool, str]:
    """``(torch + transformers importable, detail)``; detail names the CUDA state.

    Presence is probed with ``importlib.util.find_spec`` (no import cost when
    a package is missing); ``torch`` is imported only to read the CUDA state
    once both are present. Never called at registry-load time.
    """
    import importlib.util

    missing = [name for name in ("torch", "transformers") if importlib.util.find_spec(name) is None]
    if missing:
        return False, f"{' and '.join(missing)} not importable"
    try:
        import torch
    except Exception as exc:  # noqa: BLE001 — a broken install is "unavailable", not a crash
        return False, f"torch import failed: {type(exc).__name__}: {exc}"
    version = getattr(torch, "__version__", "?")
    try:
        cuda = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        cuda = False
    if cuda:
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001
            name = "cuda"
        return True, f"torch {version}, cuda available ({name})"
    return True, f"torch {version}, cuda unavailable (cpu only)"


def _cache_key(spec: "ModelSpec") -> tuple[str, str | None, str, str]:
    return (spec.base_model or "", spec.adapter_dir or None, spec.dtype, spec.device)


def load_local_model(spec: "ModelSpec") -> tuple[Any, Any]:
    """Load (or fetch from ``_MODEL_CACHE``) the ``(model, tokenizer)`` for ``spec``.

    ``load_lora_for_inference(spec.adapter_dir, base_model=spec.base_model,
    bf16=…)`` when ``adapter_dir`` is set, else ``load_base_for_inference``
    (both ``device_map="auto"``); ``spec.device != "auto"`` → ``model.to(device)``
    afterwards. ``dtype ∉ {"bfloat16", "float32"}`` → ``ValueError``; missing
    torch/transformers → ``RuntimeError`` carrying ``local_hf_available()``'s
    detail. Loads are serialised by a module lock.
    """
    if not spec.base_model:
        raise ValueError(f"{spec.model_id}: local_hf spec has no base_model")
    if spec.dtype not in _SUPPORTED_DTYPES:
        raise ValueError(
            f"{spec.model_id}: dtype must be one of {_SUPPORTED_DTYPES}, got {spec.dtype!r}"
        )
    key = _cache_key(spec)
    with _LOAD_LOCK:
        hit = _MODEL_CACHE.get(key)
        if hit is not None:
            return hit
        ok, detail = local_hf_available()
        if not ok:
            raise RuntimeError(f"{spec.model_id}: local HF inference unavailable: {detail}")
        from ganglion.lm.finetune.sft import load_base_for_inference, load_lora_for_inference

        bf16 = spec.dtype == "bfloat16"
        if spec.adapter_dir:
            model, tokenizer = load_lora_for_inference(
                spec.adapter_dir, base_model=spec.base_model, bf16=bf16
            )
        else:
            model, tokenizer = load_base_for_inference(spec.base_model, bf16=bf16)
        if spec.device != "auto":
            model = model.to(spec.device)
        model.eval()
        _MODEL_CACHE[key] = (model, tokenizer)
        return _MODEL_CACHE[key]


def unload_local_model(spec: "ModelSpec") -> bool:
    """Drop the cache entry for ``spec``; ``gc.collect()`` + CUDA cache flush.

    Returns ``False`` when nothing was loaded (idempotent).
    """
    key = _cache_key(spec)
    with _LOAD_LOCK:
        loaded = key in _MODEL_CACHE
        _MODEL_CACHE.pop(key, None)
        gc.collect()
        if loaded:
            _release_device_memory()
    return loaded


def local_model_status(spec: "ModelSpec") -> dict[str, Any]:
    """``{"status": "loaded"|"not_loaded"|"unavailable", "detail", "device", "vram_mb"}``.

    ``unavailable`` iff ``local_hf_available()[0]`` is false. ``vram_mb`` is
    ``torch.cuda.memory_allocated() / 2**20`` when CUDA is up, else ``None``.
    ``device`` is the loaded model's device when loaded, else ``spec.device``.
    """
    ok, detail = local_hf_available()
    if not ok:
        return {"status": "unavailable", "detail": detail, "device": spec.device, "vram_mb": None}
    key = _cache_key(spec)
    with _LOAD_LOCK:
        hit = _MODEL_CACHE.get(key)
    device = spec.device
    if hit is not None:
        try:
            device = str(hit[0].device)
        except Exception:  # noqa: BLE001
            device = spec.device
    vram_mb: float | None = None
    try:
        import torch

        if torch.cuda.is_available():
            vram_mb = round(torch.cuda.memory_allocated() / 2**20, 1)
    except Exception:  # noqa: BLE001
        vram_mb = None
    return {
        "status": "loaded" if hit is not None else "not_loaded",
        "detail": detail,
        "device": device,
        "vram_mb": vram_mb,
    }


class LocalHFClient:
    """``ModelClient`` over in-process transformers (+ PEFT LoRA) inference.

    ``messages = _dsl_messages(catalog, prompt)`` — the byte-identical
    ``SYSTEM_PROMPT_TEMPLATE`` render that SFT trained on. Text comes from
    ``generate_dsl`` (``ganglion.lm.finetune.sft``) with
    ``max_new_tokens=spec.max_new_tokens`` and an XGrammar mask only when
    ``spec.grammar_mask`` and ``xgrammar`` is importable (else a one-time
    warning, unmasked decode). Parsing: strict ``catalog.parse_json_dsl`` →
    ``parse_json_dsl_lenient`` (``fenced`` | ``embedded``); still failing →
    ``ModelOutputError(raw=text, attempts=attempts_from_raw(text))``.

    ``generate_fn(messages, spec) -> str`` is the test seam: when injected
    no model is loaded, ``torch`` is never imported and both token counts
    are ``None``. Otherwise the model loads lazily on the first ``invoke``
    (or via ``load_local_model``).
    """

    def __init__(
        self,
        catalog: Catalog,
        spec: "ModelSpec",
        *,
        generate_fn: Callable[[list[dict[str, Any]], "ModelSpec"], str] | None = None,
    ) -> None:
        self.catalog = catalog
        self.spec = spec
        self._generate_fn = generate_fn
        self._compiled_grammar: Any = None
        self._grammar_key: tuple[Any, ...] | None = None

    # -- helpers -----------------------------------------------------------

    def _messages(self, user_prompt: str) -> list[dict[str, Any]]:
        from ganglion.lm.prompts import _dsl_messages

        return _dsl_messages(self.catalog, user_prompt)

    def _grammar_for(self, model: Any, tokenizer: Any) -> Any:
        """Compile (once per catalog fingerprint × model) or return the cached grammar."""
        global _GRAMMAR_WARNED
        if not self.spec.grammar_mask:
            return None
        key = (self.catalog.fingerprint(), _cache_key(self.spec))
        if self._grammar_key == key:
            return self._compiled_grammar
        try:
            import xgrammar  # noqa: F401
        except ImportError:
            if not _GRAMMAR_WARNED:
                warnings.warn(
                    f"{self.spec.model_id}: grammar_mask requested but xgrammar is not "
                    "importable; decoding unmasked",
                    RuntimeWarning,
                    stacklevel=2,
                )
                _GRAMMAR_WARNED = True
            self._grammar_key, self._compiled_grammar = key, None
            return None
        from ganglion.lm.grammar import compile_catalog_grammar

        vocab_size = getattr(getattr(model, "config", None), "vocab_size", None)
        self._compiled_grammar = compile_catalog_grammar(
            self.catalog, tokenizer, vocab_size=vocab_size
        )
        self._grammar_key = key
        return self._compiled_grammar

    @staticmethod
    def _count_tokens(tokenizer: Any, messages: list[dict[str, Any]], text: str) -> tuple[int | None, int | None]:
        """``(len(prompt ids), len(generated ids))`` re-tokenised after generation."""
        input_tokens: int | None = None
        output_tokens: int | None = None
        try:
            ids = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=True, enable_thinking=False
            )
            if hasattr(ids, "input_ids"):
                ids = ids["input_ids"]
            input_tokens = len(ids)
        except Exception:  # noqa: BLE001 — a tokenizer quirk must not fail the call
            input_tokens = None
        try:
            output_tokens = len(tokenizer(text).input_ids)
        except Exception:  # noqa: BLE001
            output_tokens = None
        return input_tokens, output_tokens

    # -- ModelClient -------------------------------------------------------

    def invoke(self, user_prompt: str) -> "ModelResult":
        from ganglion.contract.parse import parse_json_dsl_lenient
        from ganglion.lm.client import ModelOutputError, ModelResult

        messages = self._messages(user_prompt)
        started = time.perf_counter()
        if self._generate_fn is not None:
            text = self._generate_fn(messages, self.spec)
            input_tokens: int | None = None
            output_tokens: int | None = None
        else:
            model, tokenizer = load_local_model(self.spec)
            compiled_grammar = self._grammar_for(model, tokenizer)
            text = generate_dsl(
                model,
                tokenizer,
                self.catalog,
                user_prompt,
                max_new_tokens=self.spec.max_new_tokens,
                compiled_grammar=compiled_grammar,
            )
            input_tokens, output_tokens = self._count_tokens(tokenizer, messages, text)
        latency_ms = (time.perf_counter() - started) * 1000
        text = "" if text is None else str(text)

        try:
            plan = self.catalog.parse_json_dsl(text, prompt=user_prompt)
            strategy = "strict"
        except DSLValidationError:
            try:
                plan, strategy = parse_json_dsl_lenient(
                    text, catalog=self.catalog, prompt=user_prompt
                )
            except DSLValidationError as exc:
                from ganglion.analyzer.trace import attempts_from_raw

                raise ModelOutputError(
                    str(exc),
                    raw=text,
                    attempts=attempts_from_raw(text),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ) from exc
        return ModelResult(
            plan=plan,
            raw={"content": text, "parse_strategy": strategy},
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
