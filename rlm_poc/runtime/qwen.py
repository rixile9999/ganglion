from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from rlm_poc.dsl.json_extract import parse_json_dsl_lenient
from rlm_poc.dsl.validator import parse_json_dsl
from rlm_poc.runtime.types import ModelResult
from rlm_poc.schema.iot_light import JSON_DSL_CATALOG, OPENAI_TOOLS


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
            model=os.getenv("RLM_MODEL", "qwen3.6-plus"),
            base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            ),
            disable_thinking=os.getenv("RLM_ENABLE_THINKING", "").lower()
            not in {"1", "true", "yes"},
        )


class QwenJSONDSLClient:
    def __init__(self, config: QwenConfig | None = None) -> None:
        from openai import OpenAI

        self.config = config or QwenConfig.from_env()
        self.client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def invoke(self, user_prompt: str) -> ModelResult:
        messages = [
            {
                "role": "system",
                "content": (
                    "You convert user requests into the JSON DSL below. "
                    "The response must be valid JSON.\n\n"
                    f"{JSON_DSL_CATALOG}"
                ),
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
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        content = completion.choices[0].message.content or "{}"
        plan = parse_json_dsl(content)
        usage = getattr(completion, "usage", None)
        return ModelResult(
            plan=plan,
            raw=content,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )


class QwenFreeformJSONDSLClient:
    def __init__(
        self,
        config: QwenConfig | None = None,
        *,
        enable_thinking: bool = False,
    ) -> None:
        from openai import OpenAI

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
                    f"{JSON_DSL_CATALOG}"
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

        plan, parse_strategy = parse_json_dsl_lenient(content)
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
    def __init__(self, config: QwenConfig | None = None) -> None:
        from openai import OpenAI

        self.config = config or QwenConfig.from_env()
        self.client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def invoke(self, user_prompt: str) -> ModelResult:
        messages = [
            {
                "role": "system",
                "content": "Choose the correct light-control tool call for the user request.",
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
            tools=OPENAI_TOOLS,
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
        plan = parse_json_dsl({"calls": dsl_calls})

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
