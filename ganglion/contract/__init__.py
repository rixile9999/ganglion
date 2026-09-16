"""Module 3 — common schema/DSL contract.

The leaf of Ganglion's dependency DAG: `lm/` (Module 1) and `analyzer/`
(Module 2) speak through the surface re-exported here. See
`docs/factory_design.md` and `docs/tasks/contract_catalog.md`.
"""
from __future__ import annotations

from ganglion.contract.catalog import Catalog
from ganglion.contract.describe import catalog_fingerprint, describe
from ganglion.contract.parse import (
    VALID_ACTIONS,
    parse_json_dsl,
    parse_json_dsl_lenient,
    validate_json_dsl,
)
from ganglion.contract.patch import (
    ALL_HOOK_KINDS,
    PatchNotApplicableError,
    apply_patch,
    strip_hooks,
)
from ganglion.contract.schema_compiler import (
    CompiledToolMapper,
    compile_openai_tools,
    compile_tool_calling_schema,
)
from ganglion.contract.tool_spec import (
    BoolArg,
    DSLValidationError,
    EnumArg,
    IntArg,
    NumberArg,
    RawArg,
    StringArg,
    TimeArg,
    ToolSpec,
)
from ganglion.contract.types import ActionPlan, ToolCall

__all__ = [
    "ALL_HOOK_KINDS",
    "ActionPlan",
    "BoolArg",
    "Catalog",
    "CompiledToolMapper",
    "DSLValidationError",
    "EnumArg",
    "IntArg",
    "NumberArg",
    "PatchNotApplicableError",
    "RawArg",
    "StringArg",
    "TimeArg",
    "ToolCall",
    "ToolSpec",
    "VALID_ACTIONS",
    "apply_patch",
    "catalog_fingerprint",
    "compile_openai_tools",
    "compile_tool_calling_schema",
    "describe",
    "parse_json_dsl",
    "parse_json_dsl_lenient",
    "strip_hooks",
    "validate_json_dsl",
]
