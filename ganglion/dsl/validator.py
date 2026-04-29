from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.dsl.types import ActionPlan
from ganglion.schema.iot_light import CATALOG as IOT_LIGHT_CATALOG

VALID_ACTIONS = {tool.name for tool in IOT_LIGHT_CATALOG.tools}


def parse_json_dsl(raw: str | Mapping[str, Any]) -> ActionPlan:
    return IOT_LIGHT_CATALOG.parse_json_dsl(raw)


def validate_json_dsl(payload: Mapping[str, Any]) -> ActionPlan:
    return IOT_LIGHT_CATALOG.validate(payload)


__all__ = [
    "DSLValidationError",
    "VALID_ACTIONS",
    "parse_json_dsl",
    "validate_json_dsl",
]
