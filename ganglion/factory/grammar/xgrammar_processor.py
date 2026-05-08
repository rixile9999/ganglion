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

    Returns a subclass that overrides ``__call__`` to coerce the sampled
    token to a Python ``int`` via ``.item()`` before passing it to
    ``GrammarMatcher.accept_token``. Upstream xgrammar 0.2.0
    (``contrib.hf.py``) hands the matcher a 0-dim tensor; the
    ``GrammarMatcher.accept_token`` TVM-FFI signature strictly expects
    ``int`` and rejects tensors with ``Expected int but got ffi.Tensor``
    on Apple Silicon. The override mirrors upstream's body verbatim
    except for the one ``.item()`` insertion; can be deleted once
    upstream lands the fix.
    """
    import xgrammar as xgr

    base_cls = xgr.contrib.hf.LogitsProcessor

    class _IntCoercedLogitsProcessor(base_cls):
        def __call__(self, input_ids, scores):
            if len(self.matchers) == 0:
                self.batch_size = input_ids.shape[0]
                self.compiled_grammars = (
                    self.compiled_grammars
                    if len(self.compiled_grammars) > 1
                    else self.compiled_grammars * self.batch_size
                )
                assert len(self.compiled_grammars) == self.batch_size, (
                    "The number of compiled grammars must equal the batch size."
                )
                self.matchers = [
                    xgr.GrammarMatcher(self.compiled_grammars[i])
                    for i in range(self.batch_size)
                ]
                self.token_bitmask = xgr.allocate_token_bitmask(
                    self.batch_size, self.full_vocab_size
                )

            if input_ids.shape[0] != self.batch_size:
                raise RuntimeError(
                    f"Expect input_ids.shape[0]={self.batch_size}, "
                    f"got {input_ids.shape[0]}"
                )

            if not self.prefilled:
                self.prefilled = True
            else:
                for i in range(self.batch_size):
                    if not self.matchers[i].is_terminated():
                        # The single-line fix: coerce 0-dim tensor → Python int.
                        sampled_token = int(input_ids[i][-1].item())
                        assert self.matchers[i].accept_token(sampled_token)

            for i in range(self.batch_size):
                if not self.matchers[i].is_terminated():
                    self.matchers[i].fill_next_token_bitmask(self.token_bitmask, i)

            device_type = scores.device.type
            if device_type != "cuda":
                scores = scores.to("cpu")
            xgr.apply_token_bitmask_inplace(
                scores, self.token_bitmask.to(scores.device)
            )
            if device_type != "cuda":
                scores = scores.to(device_type)
            return scores

    return _IntCoercedLogitsProcessor(compiled_grammar)
