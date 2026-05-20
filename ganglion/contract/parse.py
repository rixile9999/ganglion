"""Parse + extract entrypoints for JSON DSL payloads.

Merged from the historical ``ganglion/dsl/validator.py`` and
``ganglion/dsl/json_extract.py``. Public surface preserved verbatim:

- ``parse_json_dsl`` — strict parser bound to the default iot_light Catalog.
- ``validate_json_dsl`` — same, but takes a pre-decoded mapping.
- ``parse_json_dsl_lenient`` — strict → fenced ```json``` → first decodable
  ``{...}`` salvage chain.
- ``DSLValidationError`` / ``VALID_ACTIONS`` re-exported for back-compat.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from ganglion.contract.catalog import Catalog
from ganglion.contract.tool_spec import DSLValidationError
from ganglion.contract.types import ActionPlan


def _default_catalog() -> Catalog:
    """Resolve the default iot_light Catalog lazily.

    Imported inside a function so that ``ganglion.contract`` can be imported
    without forcing the built-in catalogs to be constructed at import time.
    """
    from ganglion.contract.builtins.iot_light import CATALOG

    return CATALOG


def parse_json_dsl(raw: str | Mapping[str, Any]) -> ActionPlan:
    return _default_catalog().parse_json_dsl(raw)


def validate_json_dsl(payload: Mapping[str, Any]) -> ActionPlan:
    return _default_catalog().validate(payload)


def parse_json_dsl_lenient(
    raw: str,
    *,
    catalog: Catalog | None = None,
    prompt: str | None = None,
) -> tuple[ActionPlan, str]:
    if catalog is None:
        catalog = _default_catalog()

    try:
        return catalog.parse_json_dsl(raw, prompt=prompt), "strict"
    except DSLValidationError as strict_error:
        last_error = strict_error

    for fenced in re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE):
        try:
            return catalog.parse_json_dsl(fenced.strip(), prompt=prompt), "fenced"
        except DSLValidationError as exc:
            last_error = exc

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        try:
            return catalog.parse_json_dsl(payload, prompt=prompt), "embedded"
        except DSLValidationError as exc:
            last_error = exc

    raise DSLValidationError(f"could not extract JSON DSL: {last_error}") from last_error


def _valid_actions() -> set[str]:
    return {tool.name for tool in _default_catalog().tools}


# Public legacy constant — preserved for ``from ganglion.dsl.validator import VALID_ACTIONS``.
# Computed eagerly because the original module did the same (module-level set
# comprehension over ``IOT_LIGHT_CATALOG.tools``).
VALID_ACTIONS = _valid_actions()


__all__ = [
    "DSLValidationError",
    "VALID_ACTIONS",
    "parse_json_dsl",
    "parse_json_dsl_lenient",
    "validate_json_dsl",
]
