"""Local Hugging Face client adapters for BFCL / IoT eval paths.

These clients wrap a locally-served Qwen3 (base or LoRA-adapter-on-base) so
the eval runner can compare them on the same surface as the DashScope-backed
clients in :mod:`ganglion.runtime.qwen`. The chat-template, DSL system prompt,
and lenient JSON extraction match the DashScope path so train/inference drift
is bounded by the catalog rendering alone — the same invariant
``train_lora.build_messages`` enforces.

Two flavours are exposed:

* :class:`LocalQwenDSLClient` — base model only. Untuned baseline.
* :class:`LocalQwenLoRAClient` — base + adapter. The factory output.

Both share the heavy lift through helpers in
:mod:`ganglion.factory.customer.train_lora` (``load_base_for_inference``,
``load_lora_for_inference``, ``generate_dsl``), so any drift in inference is
fixed in one place.

The model is loaded once at construction; ``invoke()`` is per-case. Each call
records wall-clock latency and an approximate output_tokens count (HF
returns generated ids; we count post-decode token length). No input-token
count is reported because HF tokenizers and DashScope tokenizers differ —
mixing them would mis-report the compression ratio downstream.
"""
from __future__ import annotations

import time
from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.json_extract import parse_json_dsl_lenient
from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.runtime.types import ModelResult


class LocalQwenDSLClient:
    """Local HF base model emitting the JSON DSL (no LoRA, no API).

    Per the BFCL runner ``ModelClient`` Protocol: ``invoke(prompt) ->
    ModelResult``. Errors surface as :class:`DSLValidationError` from the
    lenient parser — the runner catches them and records the case as
    syntax-invalid, same as for the DashScope clients.
    """

    def __init__(
        self,
        catalog: Catalog,
        *,
        base_model: str = "Qwen/Qwen3-0.6B",
        max_new_tokens: int = 256,
        temperature: float = 0.0,
        bf16: bool = True,
    ) -> None:
        from ganglion.factory.customer.train_lora import load_base_for_inference

        self.catalog = catalog
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.base_model = base_model
        self.model, self.tokenizer = load_base_for_inference(base_model, bf16=bf16)

    def invoke(self, user_prompt: str) -> ModelResult:
        from ganglion.factory.customer.train_lora import generate_dsl

        started = time.perf_counter()
        raw_output = generate_dsl(
            self.model,
            self.tokenizer,
            self.catalog,
            user_prompt,
            max_new_tokens=self.max_new_tokens,
            temperature=self.temperature,
        )
        latency_ms = (time.perf_counter() - started) * 1000

        plan, parse_strategy = parse_json_dsl_lenient(
            raw_output, catalog=self.catalog, prompt=user_prompt,
        )
        output_token_count = len(self.tokenizer(raw_output)["input_ids"])
        return ModelResult(
            plan=plan,
            raw={"content": raw_output, "parse_strategy": parse_strategy},
            latency_ms=latency_ms,
            input_tokens=None,
            output_tokens=output_token_count,
        )


class LocalQwenLoRAClient(LocalQwenDSLClient):
    """Local HF base + LoRA adapter emitting the JSON DSL.

    Constructor differs from the base client: it loads via
    ``load_lora_for_inference`` with the adapter directory. Everything else
    inherits unchanged.
    """

    def __init__(
        self,
        catalog: Catalog,
        adapter_path: str,
        *,
        base_model: str = "Qwen/Qwen3-0.6B",
        max_new_tokens: int = 256,
        temperature: float = 0.0,
        bf16: bool = True,
    ) -> None:
        from ganglion.factory.customer.train_lora import load_lora_for_inference

        self.catalog = catalog
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.base_model = base_model
        self.adapter_path = adapter_path
        self.model, self.tokenizer = load_lora_for_inference(
            adapter_path, base_model=base_model, bf16=bf16
        )


__all__ = ["LocalQwenDSLClient", "LocalQwenLoRAClient"]
