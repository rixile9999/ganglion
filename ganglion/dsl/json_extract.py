from __future__ import annotations

import json
import re

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import DSLValidationError
from ganglion.dsl.types import ActionPlan


def parse_json_dsl_lenient(
    raw: str,
    *,
    catalog: Catalog | None = None,
) -> tuple[ActionPlan, str]:
    if catalog is None:
        from ganglion.schema.iot_light import CATALOG as DEFAULT_CATALOG

        catalog = DEFAULT_CATALOG

    try:
        return catalog.parse_json_dsl(raw), "strict"
    except DSLValidationError as strict_error:
        last_error = strict_error

    for fenced in re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE):
        try:
            return catalog.parse_json_dsl(fenced.strip()), "fenced"
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
            return catalog.parse_json_dsl(payload), "embedded"
        except DSLValidationError as exc:
            last_error = exc

    raise DSLValidationError(f"could not extract JSON DSL: {last_error}") from last_error
