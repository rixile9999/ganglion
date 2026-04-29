from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.json_extract import parse_json_dsl_lenient
from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.runtime.types import ModelResult


@dataclass(frozen=True)
class QwenConfig:
    api_key: str
    model: str = "qwen3.6-plus"
    base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    disable_thinking: bool = True

    @classmethod
    def from_env(cls) -> "QwenConfig":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=os.getenv("GANGLION_MODEL") or os.getenv("RLM_MODEL", "qwen3.6-plus"),
            base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            ),
            disable_thinking=(
                os.getenv("GANGLION_ENABLE_THINKING") or os.getenv("RLM_ENABLE_THINKING", "")
            ).lower()
            not in {"1", "true", "yes"},
        )


@dataclass(frozen=True)
class RepairConfig:
    enabled: bool = False
    max_attempts: int = 1


@dataclass(frozen=True)
class CompletionResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0


class Completer(Protocol):
    def complete(self, messages: list[dict[str, Any]]) -> CompletionResponse: ...


def _dsl_messages(catalog: Catalog, user_prompt: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "You convert user requests into the JSON DSL below. "
                "The response must be valid JSON.\n\n"
                f"{catalog.render_json_dsl()}"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def run_dsl_with_repair(
    catalog: Catalog,
    user_prompt: str,
    completer: Completer,
    repair: RepairConfig,
) -> ModelResult:
    messages = _dsl_messages(catalog, user_prompt)
    attempts: list[dict[str, Any]] = []
    total_input = 0
    total_output = 0
    started = time.perf_counter()
    last_error: DSLValidationError | None = None

    for attempt in range(repair.max_attempts + 1):
        response = completer.complete(messages)
        total_input += response.input_tokens
        total_output += response.output_tokens
        attempts.append(
            {
                "attempt": attempt,
                "content": response.content,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            }
        )

        try:
            plan = catalog.parse_json_dsl(response.content)
            latency_ms = (time.perf_counter() - started) * 1000
            return ModelResult(
                plan=plan,
                raw={"attempts": attempts, "final_content": response.content},
                latency_ms=latency_ms,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        except DSLValidationError as exc:
            last_error = exc
            attempts[-1]["error"] = str(exc)
            if not repair.enabled or attempt >= repair.max_attempts:
                raise
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": (
                        "Your previous JSON failed validation: "
                        f"{exc}. Return only valid JSON that matches the DSL."
                    ),
                },
            ]

    raise RuntimeError(f"repair loop exited without returning; last_error={last_error}")


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
        extra_body = (
            {"enable_thinking": False} if self.config.disable_thinking else None
        )
        self._completer = _OpenAIDSLCompleter(
            self._openai, self.config.model, extra_body
        )

    def invoke(self, user_prompt: str) -> ModelResult:
        return run_dsl_with_repair(
            self.catalog, user_prompt, self._completer, self.repair
        )


class QwenFreeformJSONDSLClient:
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
        extra_body = {"enable_thinking": self.enable_thinking}
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

        plan, parse_strategy = parse_json_dsl_lenient(content, catalog=self.catalog)
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
        extra_body: dict[str, bool],
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
        extra_body = (
            {"enable_thinking": False} if self.config.disable_thinking else None
        )
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
            raise RuntimeError(f"model did not return a tool call: {message.content}")

        dsl_calls = []
        for raw_call in tool_calls:
            function = raw_call.function
            args = json.loads(function.arguments or "{}")
            dsl_calls.append({"action": function.name, "args": args})
        plan = self.catalog.parse_json_dsl({"calls": dsl_calls})

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
