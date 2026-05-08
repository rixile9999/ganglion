from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable


class DSLValidationError(ValueError):
    """Raised when a JSON DSL payload cannot be turned into safe tool calls."""


@dataclass(frozen=True)
class EnumArg:
    values: tuple[str, ...]
    aliases: Mapping[str, str] = field(default_factory=dict)
    required: bool = True
    description: str = ""
    bool_true: str | None = None
    bool_false: str | None = None
    kind: str = field(init=False, default="enum")


@dataclass(frozen=True)
class IntArg:
    min_value: int | None = None
    max_value: int | None = None
    required: bool = True
    description: str = ""
    allow_percent: bool = False
    kind: str = field(init=False, default="integer")


@dataclass(frozen=True)
class NumberArg:
    min_value: float | None = None
    max_value: float | None = None
    required: bool = True
    description: str = ""
    kind: str = field(init=False, default="number")


@dataclass(frozen=True)
class StringArg:
    aliases: Mapping[str, str] = field(default_factory=dict)
    pattern: str | None = None
    required: bool = True
    description: str = ""
    kind: str = field(init=False, default="string")


@dataclass(frozen=True)
class BoolArg:
    required: bool = True
    description: str = ""
    kind: str = field(init=False, default="boolean")


@dataclass(frozen=True)
class TimeArg:
    required: bool = True
    description: str = ""
    kind: str = field(init=False, default="time")


@dataclass(frozen=True)
class RawArg:
    """Embed an explicit JSON Schema fragment and DSL description.

    Used for shapes that the generic renderer cannot express (e.g. nested arrays).
    """

    json_schema: dict[str, Any]
    dsl_description: str
    required: bool = True
    kind: str = field(init=False, default="raw")


ArgSpec = EnumArg | IntArg | NumberArg | StringArg | BoolArg | TimeArg | RawArg


DefaultRule = tuple[str, Any, Callable[[Mapping[str, Any]], bool]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: tuple[tuple[str, ArgSpec], ...] = ()
    dsl_args_override: str | None = None
    custom_validator: Callable[..., dict[str, Any]] | None = None
    # Post-correction rules: when a required arg is missing but the rest of
    # ``args`` makes the right value unambiguous, fill it in before the
    # validator runs. Each entry is ``(arg_name, default_value, predicate)``;
    # the rule fires iff ``arg_name`` is absent AND ``predicate(args)`` is
    # True. Used to absorb the ~6% failure mass on iot_light_5 from small
    # models (e.g. 0.6B) that omit ``state`` when ``brightness`` makes the
    # implied state self-evident. The grammar/OpenAI rendering does NOT
    # advertise these defaults — the contract still requires ``state``,
    # post-correction is purely a parse-time inference layer.
    defaults_when_missing: tuple[DefaultRule, ...] = ()

    def get_arg(self, name: str) -> ArgSpec | None:
        for arg_name, spec in self.args:
            if arg_name == name:
                return spec
        return None

    def required_arg_names(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.args if spec.required)
