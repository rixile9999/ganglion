"""Customer schema ingestion → Catalog.

Accepts the diverse shapes a customer might bring:

* ``"iot_light_5"`` / ``"home_iot_20"`` / ``"smart_home_50"`` — built-in tiers
* path to a ``.json`` / ``.yaml`` file holding any of the schema shapes below
* a Python sequence of OpenAI-style tool dicts
* an MCP tool list (``{"tools": [...]}`` wrapper)
* a single OpenAPI/MCP function dict

Most of the work delegates to ``ganglion.dsl.compiler.compile_tool_calling_schema``
which already understands these shapes; this module only adds source detection
and convenience defaults (catalog name from filename, etc.).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.compiler import compile_tool_calling_schema
from ganglion.schema import TIERS, get_catalog

SchemaInput = str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]]


def ingest_schema(source: SchemaInput, *, name: str | None = None) -> Catalog:
    """Normalize ``source`` into a :class:`Catalog`.

    Resolution order:

    1. ``str`` matching a built-in tier name — return the bundled catalog.
    2. ``str`` or ``Path`` pointing to an existing file — load JSON/YAML and
       compile.
    3. ``Mapping`` or ``Sequence`` — pass straight to
       :func:`compile_tool_calling_schema`.
    """

    if isinstance(source, str) and source in TIERS:
        return get_catalog(source)

    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"schema file not found: {path}")
        return _ingest_path(path, name=name)

    if isinstance(source, (Mapping, Sequence)) and not isinstance(source, (str, bytes)):
        return compile_tool_calling_schema(
            source, name=name or "compiled_tools"
        ).catalog

    raise TypeError(f"unsupported schema input type: {type(source).__name__}")


def _ingest_path(path: Path, *, name: str | None) -> Catalog:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".json"}:
        data = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        import yaml  # local import to keep yaml optional at top level

        data = yaml.safe_load(text)
    else:
        # Try JSON first, then YAML
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            import yaml

            data = yaml.safe_load(text)
    catalog_name = name or path.stem
    return compile_tool_calling_schema(data, name=catalog_name).catalog
