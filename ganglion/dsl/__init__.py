"""Deprecated compatibility shim.

Module 3 moved to `ganglion.contract`. This module re-exports the same
public surface and emits ``DeprecationWarning`` once per process. It will
be removed in Batch 6 of `docs/redesign_plan.md`. Update imports to
``from ganglion.contract import …``.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.dsl is deprecated; use ganglion.contract instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.contract import (  # noqa: E402,F401
    ActionPlan,
    BoolArg,
    Catalog,
    CompiledToolMapper,
    DSLValidationError,
    EnumArg,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolCall,
    ToolSpec,
    VALID_ACTIONS,
    compile_openai_tools,
    compile_tool_calling_schema,
    parse_json_dsl,
    parse_json_dsl_lenient,
    validate_json_dsl,
)

__all__ = [
    "ActionPlan",
    "BoolArg",
    "Catalog",
    "CompiledToolMapper",
    "DSLValidationError",
    "EnumArg",
    "IntArg",
    "NumberArg",
    "RawArg",
    "StringArg",
    "TimeArg",
    "ToolCall",
    "ToolSpec",
    "VALID_ACTIONS",
    "compile_openai_tools",
    "compile_tool_calling_schema",
    "parse_json_dsl",
    "parse_json_dsl_lenient",
    "validate_json_dsl",
]
