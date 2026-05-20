"""Per-case `Catalog` builder for BFCL.

Each BFCL case ships its own tool list (`case.tools`), so the runner compiles
a fresh `Catalog` per case via `compile_tool_calling_schema`. This helper is
small but warrants its own module per `docs/tasks/benchmark_bfcl.md`.
"""
from __future__ import annotations

# TODO(cleanup): switch to canonical ganglion.benchmarks.bfcl.loader once M3-A lands
from ganglion.bfcl.loader import BFCLCase
from ganglion.contract.catalog import Catalog
from ganglion.contract.schema_compiler import compile_tool_calling_schema


def build_case_catalog(
    case: BFCLCase,
    *,
    allow_empty_calls: bool = False,
) -> Catalog:
    """Compile a per-case `Catalog` from the BFCL tool list.

    `compile_tool_calling_schema` accepts a sequence of tool schemas and
    returns a `CompiledToolMapper`; we keep the underlying `Catalog` so the
    runner can call `render_json_dsl()` and `render_openai_tools()` directly.
    """
    mapper = compile_tool_calling_schema(
        list(case.tools),
        name=f"bfcl_{case.id}",
        allow_empty_calls=allow_empty_calls,
    )
    return mapper.catalog


__all__ = ["build_case_catalog"]
