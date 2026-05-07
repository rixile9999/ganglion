"""Tool-anchored intent synthesis with validator gating.

Pipeline per call to :func:`synthesize`:

    for each tool in catalog:
        while kept_for_tool < target_per_tool and budget remains:
            messages = render_tool_anchored_prompt(catalog, tool, n=K)
            raw     = teacher.call(messages)
            for pair in parse(raw):
                if synth_gate(catalog, pair, expected_tool=tool.name):
                    kept_for_tool += 1
                    examples.append(pair)
    examples = embedding_dedupe(examples, threshold)
    persist_jsonl(examples, output_path)

Two cost controls run in parallel: a per-tool ``max_attempts`` cap (in case
the teacher cannot satisfy the spec for some tool) and a global
``max_cost_usd`` cap (in case the API meter runs hot). Both fire as
short-circuit ``break`` paths and the partial result is still returned.

Real ``DashScopeTeacher`` lives at the bottom; it is constructed from
``DASHSCOPE_API_KEY`` and reuses the same OpenAI-SDK-against-DashScope
pattern as ``ganglion.runtime.qwen``. Tests inject a fake teacher via the
``teacher`` parameter and never hit the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import DSLValidationError, ToolSpec
from ganglion.factory.prompts.synth_templates import render_tool_anchored_prompt


@dataclass(frozen=True)
class SynthConfig:
    n_target: int = 5000
    samples_per_request: int = 5
    teacher_model: str = "qwen3.6-plus"
    teacher_temperature: float = 0.92
    seed: int = 42
    max_cost_usd: float = 5.0
    max_attempts_per_tool: int = 60
    dedupe_threshold: float = 0.95


@dataclass(frozen=True)
class SynthExample:
    intent: str
    expected_dsl: str          # JSON-serialized DSL string
    strategy: str              # e.g., "tool_anchored:set_light"
    teacher_score: float = 1.0  # gate score; placeholder for richer future scoring


@dataclass
class SynthStats:
    n_kept: int = 0
    n_attempted: int = 0
    n_dropped_parse: int = 0
    n_dropped_wrong_tool: int = 0
    n_dropped_other: int = 0
    n_deduped: int = 0
    pass_rate_by_tool: dict[str, float] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    duration_sec: float = 0.0
    cost_capped: bool = False
    n_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def pass_rate(self) -> float:
        return self.n_kept / self.n_attempted if self.n_attempted else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pass_rate"] = self.pass_rate
        return d


class TeacherClient(Protocol):
    """Anything that can take chat messages and return (content, in_toks, out_toks)."""

    def call(self, messages: list[dict[str, Any]]) -> tuple[str, int, int]: ...


# Approximate DashScope pricing in USD per 1k tokens. These are rough
# estimates used only for budget enforcement; actual billing is what counts.
# Update from the DashScope console when the user surfaces real numbers.
_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    "qwen3.6-plus": (0.0008, 0.0024),
}
_DEFAULT_PRICING = (0.001, 0.003)


def estimate_cost(model: str, in_toks: int, out_toks: int) -> float:
    in_price, out_price = _PRICING_PER_1K.get(model, _DEFAULT_PRICING)
    return in_toks / 1000 * in_price + out_toks / 1000 * out_price


def synthesize(
    catalog: Catalog,
    config: SynthConfig | None = None,
    *,
    teacher: TeacherClient | None = None,
    output_path: Path | None = None,
) -> tuple[list[SynthExample], SynthStats]:
    """Generate up to ``config.n_target`` examples and persist as JSONL."""

    cfg = config or SynthConfig()
    teacher = teacher or DashScopeTeacher(model=cfg.teacher_model, temperature=cfg.teacher_temperature)
    rng = random.Random(cfg.seed)

    if not catalog.tools:
        raise ValueError(f"catalog '{catalog.name}' has no tools")

    target_per_tool = max(1, cfg.n_target // len(catalog.tools))
    examples: list[SynthExample] = []
    stats = SynthStats()
    started = time.perf_counter()

    for tool in catalog.tools:
        kept_for_tool = 0
        attempted_for_tool = 0
        attempt = 0

        while kept_for_tool < target_per_tool and attempt < cfg.max_attempts_per_tool:
            if stats.estimated_cost_usd >= cfg.max_cost_usd:
                stats.cost_capped = True
                break
            attempt += 1
            messages = render_tool_anchored_prompt(
                catalog, tool, n=cfg.samples_per_request
            )
            try:
                raw, in_toks, out_toks = teacher.call(messages)
            except Exception as exc:
                # Network blips, rate limits, etc. — log and retry up to cap
                stats.n_dropped_other += 1
                continue
            stats.n_calls += 1
            stats.input_tokens += in_toks
            stats.output_tokens += out_toks
            stats.estimated_cost_usd += estimate_cost(cfg.teacher_model, in_toks, out_toks)

            for pair in _parse_pairs(raw):
                attempted_for_tool += 1
                stats.n_attempted += 1
                kept, drop_reason = synth_gate(catalog, pair, expected_tool=tool.name)
                if not kept:
                    if drop_reason == "parse":
                        stats.n_dropped_parse += 1
                    elif drop_reason == "wrong_tool":
                        stats.n_dropped_wrong_tool += 1
                    else:
                        stats.n_dropped_other += 1
                    continue

                intent = pair["intent"].strip()
                # Re-serialize DSL through the catalog parse to canonicalize
                plan = catalog.parse_json_dsl(pair["dsl"])
                expected_dsl = json.dumps(plan.to_jsonable(), ensure_ascii=False, sort_keys=True)

                examples.append(
                    SynthExample(
                        intent=intent,
                        expected_dsl=expected_dsl,
                        strategy=f"tool_anchored:{tool.name}",
                    )
                )
                kept_for_tool += 1
                stats.n_kept += 1
                if kept_for_tool >= target_per_tool:
                    break

        if attempted_for_tool > 0:
            stats.pass_rate_by_tool[tool.name] = kept_for_tool / attempted_for_tool

        if stats.cost_capped:
            break

    # Embedding-based dedup across the whole corpus
    if examples:
        deduped = _dedupe_by_embedding(examples, threshold=cfg.dedupe_threshold)
        stats.n_deduped = len(examples) - len(deduped)
        examples = deduped

    rng.shuffle(examples)
    stats.duration_sec = time.perf_counter() - started

    if output_path is not None:
        write_jsonl(examples, output_path)

    return examples, stats


def synth_gate(
    catalog: Catalog,
    pair: Mapping[str, Any],
    *,
    expected_tool: str,
) -> tuple[bool, str | None]:
    """Decide whether a teacher-emitted pair is keepable.

    Returns ``(kept, drop_reason)`` where ``drop_reason`` is None on accept.

    For tool-anchored strategy we require *exactly one* call, and that
    call's action must equal ``expected_tool``. Multi-call outputs from a
    tool-anchored prompt are off-spec and dropped; they belong in the
    multi-tool strategy (Phase 2).
    """
    intent = pair.get("intent")
    dsl = pair.get("dsl")
    if not isinstance(intent, str) or not intent.strip():
        return False, "other"
    if not isinstance(dsl, Mapping):
        return False, "parse"

    try:
        plan = catalog.parse_json_dsl(dsl)
    except (DSLValidationError, Exception):
        return False, "parse"

    if not plan.calls:
        return False, "other"
    if len(plan.calls) > 1:
        return False, "wrong_tool"
    if plan.calls[0].action != expected_tool:
        return False, "wrong_tool"

    return True, None


def _parse_pairs(raw: str) -> list[dict[str, Any]]:
    """Parse the teacher's raw JSON response into a list of pair dicts.

    Tolerant: tries strict JSON first, then strips triple-backtick fences,
    then returns empty list. Validation of pair shape is left to ``synth_gate``.
    """
    if not raw:
        return []
    candidates = [raw, _strip_code_fences(raw)]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        pairs = data.get("pairs") if isinstance(data, dict) else None
        if isinstance(pairs, list):
            return [p for p in pairs if isinstance(p, dict)]
    return []


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop opening fence (with optional language tag) and closing fence
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def _dedupe_by_embedding(
    examples: list[SynthExample], *, threshold: float
) -> list[SynthExample]:
    """Drop near-duplicate intents using sentence-transformer embeddings.

    O(N^2) cosine over a single GPU/CPU pass; fine up to N≈10k. For larger N
    we'd swap to a FAISS/HNSW index but Phase 1 caps at 5k.
    """
    if len(examples) <= 1:
        return list(examples)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        # Dedup is best-effort; if the optional dep is missing, fall back to
        # exact-string dedup so the pipeline still progresses.
        seen: set[str] = set()
        kept: list[SynthExample] = []
        for ex in examples:
            key = ex.intent.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append(ex)
        return kept

    import numpy as np

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    embeddings = model.encode(
        [ex.intent for ex in examples],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    embeddings = np.asarray(embeddings)

    keep_mask = [True] * len(examples)
    for i in range(len(examples)):
        if not keep_mask[i]:
            continue
        # Compare to all subsequent items
        sims = embeddings[i + 1 :] @ embeddings[i]
        for offset, sim in enumerate(sims):
            j = i + 1 + offset
            if keep_mask[j] and sim >= threshold:
                keep_mask[j] = False

    return [ex for ex, keep in zip(examples, keep_mask) if keep]


def write_jsonl(examples: Iterable[SynthExample], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ex in examples:
            row = {
                "intent": ex.intent,
                "expected": ex.expected_dsl,
                "strategy": ex.strategy,
                "teacher_score": ex.teacher_score,
                "id": _stable_id(ex),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[SynthExample]:
    rows: list[SynthExample] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rows.append(
                SynthExample(
                    intent=obj["intent"],
                    expected_dsl=obj["expected"],
                    strategy=obj.get("strategy", ""),
                    teacher_score=obj.get("teacher_score", 1.0),
                )
            )
    return rows


def _stable_id(ex: SynthExample) -> str:
    payload = ex.intent + "|" + ex.expected_dsl
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Real teacher: DashScope qwen3.6-plus via OpenAI-compatible endpoint.
# --------------------------------------------------------------------------


class DashScopeTeacher:
    """Teacher backed by DashScope's OpenAI-compatible chat completions API.

    Mirrors the configuration of ``ganglion.runtime.qwen.QwenJSONDSLClient``:
    ``DASHSCOPE_API_KEY`` env var, ``response_format=json_object``,
    ``enable_thinking=False`` for the non-thinking model.
    """

    def __init__(
        self,
        *,
        model: str = "qwen3.6-plus",
        temperature: float = 0.85,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not set")
        self.model = model
        self.temperature = temperature
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or os.environ.get(
                "DASHSCOPE_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            ),
        )

    def call(self, messages: list[dict[str, Any]]) -> tuple[str, int, int]:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        content = completion.choices[0].message.content or "{}"
        usage = getattr(completion, "usage", None)
        in_toks = getattr(usage, "prompt_tokens", 0) or 0
        out_toks = getattr(usage, "completion_tokens", 0) or 0
        return content, in_toks, out_toks
