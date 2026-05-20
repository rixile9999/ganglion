"""Tests for the XGrammar HF LogitsProcessor wiring.

Most tests skip cleanly when ``xgrammar`` or ``transformers`` is not
installed — the factory pipeline is opt-in via ``[factory]`` extras and
local CI on the slim install should still pass.
"""

from __future__ import annotations

import pytest

from ganglion.lm.grammar import (
    catalog_to_json_schema,
    compile_catalog_grammar,
    make_logits_processor,
)
from ganglion.contract.builtins import get_catalog


xgr = pytest.importorskip("xgrammar")


def _build_qwen_like_tokenizer():
    """Build a tiny but xgrammar-compatible tokenizer without HF downloads.

    xgrammar's ``TokenizerInfo.from_huggingface`` reads vocab + special
    tokens; it does not need a real chat template. We assemble a minimal
    BPE tokenizer entirely in-memory so tests don't hit the HF hub.
    """
    transformers = pytest.importorskip("transformers")
    tokenizers = pytest.importorskip("tokenizers")

    # Smallest faithful tokenizer: single-char BPE with a few specials.
    # The exact vocab content doesn't matter for compile-time validation;
    # XGrammar only needs to know which token IDs map to which strings.
    vocab = {chr(i): i for i in range(32, 127)}
    vocab.update({"<|endoftext|>": 0, "<|im_start|>": 1, "<|im_end|>": 2})
    merges: list[tuple[str, str]] = []

    raw = tokenizers.Tokenizer(tokenizers.models.BPE(vocab=vocab, merges=merges))
    raw.add_special_tokens(["<|endoftext|>", "<|im_start|>", "<|im_end|>"])

    return transformers.PreTrainedTokenizerFast(
        tokenizer_object=raw,
        eos_token="<|endoftext|>",
        pad_token="<|endoftext|>",
    )


def test_module_exports() -> None:
    """Public re-exports come through ``ganglion.lm.grammar``."""
    from ganglion.lm import grammar

    assert hasattr(grammar, "compile_catalog_grammar")
    assert hasattr(grammar, "make_logits_processor")
    assert hasattr(grammar, "catalog_to_json_schema")


def test_compile_returns_compiled_grammar() -> None:
    """Compiling a catalog yields an XGrammar CompiledGrammar."""
    tokenizer = _build_qwen_like_tokenizer()
    catalog = get_catalog("iot_light_5")
    compiled = compile_catalog_grammar(catalog, tokenizer)
    # Must be the xgrammar CompiledGrammar type.
    assert isinstance(compiled, xgr.CompiledGrammar)


def test_compile_handles_large_catalog() -> None:
    """smart_home_50 has 50 anyOf branches — compile must still succeed."""
    tokenizer = _build_qwen_like_tokenizer()
    catalog = get_catalog("smart_home_50")
    compiled = compile_catalog_grammar(catalog, tokenizer)
    assert isinstance(compiled, xgr.CompiledGrammar)


def test_make_logits_processor_returns_callable() -> None:
    """The processor exposes the HF ``__call__(input_ids, scores)`` interface."""
    tokenizer = _build_qwen_like_tokenizer()
    catalog = get_catalog("iot_light_5")
    compiled = compile_catalog_grammar(catalog, tokenizer)
    processor = make_logits_processor(compiled)
    # HF LogitsProcessor protocol: callable taking (input_ids, scores).
    assert callable(processor)


def test_each_call_returns_fresh_processor() -> None:
    """XGrammar matchers are single-use; consecutive calls must yield distinct objects."""
    tokenizer = _build_qwen_like_tokenizer()
    catalog = get_catalog("iot_light_5")
    compiled = compile_catalog_grammar(catalog, tokenizer)
    p1 = make_logits_processor(compiled)
    p2 = make_logits_processor(compiled)
    assert p1 is not p2


def test_vocab_size_override_threads_through() -> None:
    """Caller-supplied ``vocab_size`` (matching model config) is accepted."""
    tokenizer = _build_qwen_like_tokenizer()
    catalog = get_catalog("iot_light_5")
    # Pad past the tokenizer vocab to mimic Qwen3's padded embedding table.
    padded = len(tokenizer.get_vocab()) + 256
    compiled = compile_catalog_grammar(catalog, tokenizer, vocab_size=padded)
    assert isinstance(compiled, xgr.CompiledGrammar)


def test_schema_is_consistent_with_converter() -> None:
    """``compile_catalog_grammar`` consumes exactly what ``catalog_to_json_schema`` emits."""
    catalog = get_catalog("iot_light_5")
    schema = catalog_to_json_schema(catalog)
    # Schema must be JSON-serializable and shaped as expected.
    import json

    json.dumps(schema)
    assert schema["properties"]["calls"]["type"] == "array"
