"""Wire a Catalog's JSON Schema into an XGrammar HF LogitsProcessor.

Two-step usage to amortize the expensive compile cost across many
``model.generate()`` calls:

1. Call :func:`compile_catalog_grammar` once per (catalog, tokenizer) pair to
   produce a reusable ``CompiledGrammar``.
2. Call :func:`make_logits_processor` per ``model.generate()`` invocation to
   get a fresh, single-use ``LogitsProcessor`` (XGrammar matchers carry
   per-generation state and cannot be reused across generates).

The grammar itself comes from
:func:`ganglion.factory.grammar.catalog_to_xgrammar.catalog_to_json_schema`,
which is unchanged from Phase 1; this module only adds the runtime wiring
that Phase 1 documented as deferred.
"""

from __future__ import annotations

from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.factory.grammar.catalog_to_xgrammar import catalog_to_json_schema


def compile_catalog_grammar(
    catalog: Catalog,
    tokenizer: Any,
    *,
    vocab_size: int | None = None,
    stop_token_ids: list[int] | int | None = None,
) -> Any:
    """Compile ``catalog``'s JSON Schema into a reusable XGrammar grammar.

    ``vocab_size`` should be the model's ``config.vocab_size``, NOT
    ``tokenizer.vocab_size`` — Qwen3 (and many other modern LMs) pad the
    embedding table beyond the tokenizer vocab, and a mismatch here causes
    silent mask-shape bugs. Pass it explicitly when the model config is
    available; XGrammar will fall back to the tokenizer vocab otherwise.

    ``stop_token_ids`` is forwarded as-is. Qwen3 has multiple EOS tokens
    (``<|im_end|>`` and ``<|endoftext|>``); pass both to avoid premature
    termination.
    """
    import xgrammar as xgr

    schema = catalog_to_json_schema(catalog)
    tokenizer_info = xgr.TokenizerInfo.from_huggingface(
        tokenizer,
        vocab_size=vocab_size,
        stop_token_ids=stop_token_ids,
    )
    compiler = xgr.GrammarCompiler(tokenizer_info)
    return compiler.compile_json_schema(schema)


def make_logits_processor(compiled_grammar: Any) -> Any:
    """Build a fresh single-use HF ``LogitsProcessor`` for one ``generate()``.

    XGrammar's matcher state is consumed during generation, so a new
    processor must be instantiated per call. Cheap — only ``compile_*`` is
    expensive.
    """
    import xgrammar as xgr

    return xgr.contrib.hf.LogitsProcessor(compiled_grammar)
