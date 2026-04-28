from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rlm_poc.dsl.tool_spec import DSLValidationError
from rlm_poc.dsl.types import ActionPlan
from rlm_poc.schema.iot_light import CATALOG as IOT_LIGHT_CATALOG

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
