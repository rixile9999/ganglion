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


def _plan_shaped(payload: Any) -> bool:
    """True when ``payload`` is a mapping the Catalog would try to validate.

    Used to tell "the text held no Action IR at all" (a genuine extraction
    failure) apart from "the text held an Action IR that did not validate"
    (a *validation* failure whose message is the one the operator needs).
    """
    return isinstance(payload, Mapping) and ("calls" in payload or "action" in payload)


def _decode_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def parse_json_dsl_lenient(
    raw: str,
    *,
    catalog: Catalog | None = None,
    prompt: str | None = None,
) -> tuple[ActionPlan, str]:
    """Strict → fenced ```json``` → first decodable ``{...}`` salvage chain.

    On total failure the raised error is the **first plan-shaped candidate's**
    validation error (e.g. ``brightness must be <= 100``) — not the last
    candidate tried. The salvage loop walks every ``{`` in the text, so the
    inner ``args`` object is always tried last and its generic "expected
    'calls' array" complaint used to overwrite the real diagnosis, which then
    reached the failure taxonomy and the operator as ``syntax_invalid``.
    """
    if catalog is None:
        catalog = _default_catalog()

    # The first error from a candidate that actually looked like an Action IR.
    structural_error: DSLValidationError | None = None

    def _remember(exc: DSLValidationError, payload: Any) -> None:
        nonlocal structural_error
        if structural_error is None and _plan_shaped(payload):
            structural_error = exc

    try:
        return catalog.parse_json_dsl(raw, prompt=prompt), "strict"
    except DSLValidationError as strict_error:
        last_error: DSLValidationError = strict_error
        _remember(strict_error, raw if isinstance(raw, Mapping) else _decode_or_none(raw))

    for fenced in re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE):
        try:
            return catalog.parse_json_dsl(fenced.strip(), prompt=prompt), "fenced"
        except DSLValidationError as exc:
            last_error = exc
            _remember(exc, _decode_or_none(fenced.strip()))

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
            _remember(exc, payload)

    if structural_error is not None:
        raise structural_error
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
