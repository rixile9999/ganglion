"""OpenAI-SDK Qwen clients (DashScope / vLLM / OpenAI-compatible).

Three OpenAI-SDK clients plus a transport-specific completer adapter
([[lm_client]], `docs/tasks/lm_client.md`):

- `QwenJSONDSLClient` — `response_format={"type": "json_object"}`; supports M4 repair.
- `QwenFreeformJSONDSLClient` — no `response_format`; salvaged via `parse_json_dsl_lenient`.
- `QwenNativeToolClient` — native `tools=[...]` baseline.

Every client is parameterised by `QwenConfig`, whose `provider` field selects
the provider-specific thinking switch (`_thinking_extra_body`): DashScope
takes `extra_body={"enable_thinking": …}`, vLLM's Qwen3 chat template takes
`extra_body={"chat_template_kwargs": {"enable_thinking": …}}`, and a plain
OpenAI endpoint takes nothing.

Failure shape: the freeform and native clients raise `ModelOutputError`
(`ganglion.lm.client`) carrying the model's raw output; the json-dsl client
goes through `run_dsl_with_repair`, whose terminal failure is
`RepairExhaustedError` (same `.raw` / `.attempts` attributes).

The repair-loop core (`run_dsl_with_repair`, `RepairConfig`,
`CompletionResponse`, `RepairExhaustedError`) lives in
`ganglion.analyzer.repair`; this module re-exports them for backwards
compatibility (see `docs/tasks/analyzer_repair_policy.md`).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from ganglion.analyzer.repair import (
    CompletionResponse,
    RepairConfig,
    RepairExhaustedError,
    run_dsl_with_repair,
)
from ganglion.contract import Catalog, DSLValidationError, parse_json_dsl_lenient
from ganglion.lm.client import ModelOutputError, ModelResult

__all__ = [
    "CompletionResponse",
    "PROVIDERS",
    "QwenConfig",
    "QwenFreeformJSONDSLClient",
    "QwenJSONDSLClient",
    "QwenNativeToolClient",
    "RepairConfig",
    "RepairExhaustedError",
    "run_dsl_with_repair",
]

#: Providers `QwenConfig.provider` accepts; the value only changes how the
#: thinking switch is encoded (see `_thinking_extra_body`).
PROVIDERS: tuple[str, ...] = ("dashscope", "vllm", "openai")

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.6-plus"


@dataclass(frozen=True)
class QwenConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    disable_thinking: bool = True
    # "dashscope" | "vllm" | "openai" — selects the thinking-switch encoding.
    provider: str = "dashscope"

    @classmethod
    def from_env(cls) -> "QwenConfig":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=os.getenv("GANGLION_MODEL") or os.getenv("RLM_MODEL", DEFAULT_MODEL),
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            disable_thinking=(
                os.getenv("GANGLION_ENABLE_THINKING") or os.getenv("RLM_ENABLE_THINKING", "")
            ).lower()
            not in {"1", "true", "yes"},
        )


def _thinking_extra_body(config: QwenConfig, enable: bool) -> dict[str, Any] | None:
    """Provider-specific `extra_body` that turns Qwen3 thinking on or off.

    - `dashscope` → `{"enable_thinking": enable}` (DashScope compatible-mode).
    - `vllm` → `{"chat_template_kwargs": {"enable_thinking": enable}}` (the
      Qwen3 chat template flag, forwarded by vLLM's OpenAI server).
    - `openai` → `None` (a plain OpenAI endpoint rejects unknown fields).

    Unknown providers raise `ValueError` — the registry validates the field
    before a client is built, so this is a programming error, not config.
    """
    if config.provider == "dashscope":
        return {"enable_thinking": bool(enable)}
    if config.provider == "vllm":
        return {"chat_template_kwargs": {"enable_thinking": bool(enable)}}
    if config.provider == "openai":
        return None
    raise ValueError(
        f"unknown provider {config.provider!r}; expected one of {PROVIDERS}"
    )


def _attempts(raw: Any) -> tuple[dict[str, Any], ...]:
    """`attempts_from_raw` behind a lazy import (keeps lm → analyzer one-way)."""
    from ganglion.analyzer.trace import attempts_from_raw

    return attempts_from_raw(raw)


class _OpenAIDSLCompleter:
    def __init__(self, client: Any, model: str, extra_body: dict[str, Any] | None) -> None:
        self.client = client
        self.model = model
        self.extra_body = extra_body

    def complete(self, messages: list[dict[str, Any]]) -> CompletionResponse:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            extra_body=self.extra_body,
        )
        content = completion.choices[0].message.content or "{}"
        usage = getattr(completion, "usage", None)
        return CompletionResponse(
            content=content,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


class QwenJSONDSLClient:
    """`response_format=json_object` client wired to the repair slot.

    Terminal validation failure surfaces as `RepairExhaustedError`
    (`ganglion.analyzer.repair`) carrying `.raw` / `.attempts`.
    """

    def __init__(
        self,
        catalog: Catalog,
        config: QwenConfig | None = None,
        *,
        repair: RepairConfig | None = None,
    ) -> None:
        from openai import OpenAI

        self.catalog = catalog
        self.config = config or QwenConfig.from_env()
        self.repair = repair or RepairConfig()
        self._openai = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)
        extra_body = _thinking_extra_body(self.config, not self.config.disable_thinking)
        self._completer = _OpenAIDSLCompleter(
            self._openai, self.config.model, extra_body
        )

    def invoke(self, user_prompt: str) -> ModelResult:
        return run_dsl_with_repair(
            self.catalog, user_prompt, self._completer, self.repair
        )


class QwenFreeformJSONDSLClient:
    """No `response_format`; output salvaged by `parse_json_dsl_lenient`.

    `enable_thinking` (constructor kwarg) is the thinking switch for this
    client — it deliberately ignores `config.disable_thinking` so the same
    config can back both the `freeform` and `thinking` registry clients.
    Validation failure raises `ModelOutputError(raw=<content str>)`.
    """

    def __init__(
        self,
        catalog: Catalog,
        config: QwenConfig | None = None,
        *,
        enable_thinking: bool = False,
    ) -> None:
        from openai import OpenAI

        self.catalog = catalog
        self.config = config or QwenConfig.from_env()
        self.enable_thinking = enable_thinking
        self.client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def invoke(self, user_prompt: str) -> ModelResult:
        messages = [
            {
                "role": "system",
                "content": (
                    "You convert user requests into the JSON DSL below. "
                    "Return JSON only, with no Markdown and no explanation.\n\n"
                    f"{self.catalog.render_json_dsl()}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        extra_body = _thinking_extra_body(self.config, self.enable_thinking)
        started = time.perf_counter()
        if self.enable_thinking:
            content, reasoning, usage = self._stream_completion(messages, extra_body)
        else:
            completion = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                extra_body=extra_body,
            )
            content = completion.choices[0].message.content or ""
            reasoning = None
            usage = getattr(completion, "usage", None)
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            plan, parse_strategy = parse_json_dsl_lenient(
                content, catalog=self.catalog, prompt=user_prompt,
            )
        except DSLValidationError as exc:
            raise ModelOutputError(
                str(exc),
                raw=content,
                attempts=_attempts(content),
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
            ) from exc
        return ModelResult(
            plan=plan,
            raw={
                "content": content,
                "parse_strategy": parse_strategy,
                "thinking_enabled": self.enable_thinking,
                "reasoning_chars": len(reasoning or ""),
            },
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )

    def _stream_completion(
        self,
        messages: list[dict[str, str]],
        extra_body: dict[str, Any] | None,
    ) -> tuple[str, str, Any]:
        stream = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            extra_body=extra_body,
            stream=True,
            stream_options={"include_usage": True},
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage = None
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                content_parts.append(content)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
        return "".join(content_parts), "".join(reasoning_parts), usage


class QwenNativeToolClient:
    """Native `tools=[...]` baseline sharing the DSL validator.

    Returned `tool_calls` are converted to Action-IR calls and validated by
    the same `Catalog`; failures raise `ModelOutputError` with
    `raw=<dsl_calls list>` (validation) or `raw=<message.content or "">`
    (no tool call at all) — errata E12.
    """

    def __init__(self, catalog: Catalog, config: QwenConfig | None = None) -> None:
        from openai import OpenAI

        self.catalog = catalog
        self.config = config or QwenConfig.from_env()
        self.client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def invoke(self, user_prompt: str) -> ModelResult:
        messages = [
            {
                "role": "system",
                "content": "Choose the correct tool call for the user request.",
            },
            {"role": "user", "content": user_prompt},
        ]
        extra_body = _thinking_extra_body(self.config, not self.config.disable_thinking)
        started = time.perf_counter()
        completion = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            tools=self.catalog.render_openai_tools(),
            tool_choice="auto",
            extra_body=extra_body,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        message = completion.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            content = getattr(message, "content", None) or ""
            raise ModelOutputError(
                f"model did not return a tool call: {content}",
                raw=content,
                attempts=_attempts(content),
            )

        dsl_calls: list[dict[str, Any]] = []
        for raw_call in tool_calls:
            function = raw_call.function
            try:
                args = json.loads(function.arguments or "{}")
            except json.JSONDecodeError:
                # Keep the undecodable text so the trace shows what came back.
                args = {"__raw_arguments__": function.arguments}
            dsl_calls.append({"action": function.name, "args": args})
        try:
            plan = self.catalog.parse_json_dsl({"calls": dsl_calls}, prompt=user_prompt)
        except DSLValidationError as exc:
            raise ModelOutputError(
                str(exc), raw=dsl_calls, attempts=_attempts(dsl_calls)
            ) from exc

        emitted_calls = [
            {"name": call.action, "arguments": call.args}
            for call in plan.calls
        ]

        usage = getattr(completion, "usage", None)
        return ModelResult(
            plan=plan,
            raw=emitted_calls,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )
