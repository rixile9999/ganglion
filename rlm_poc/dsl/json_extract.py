from __future__ import annotations

import json
import re
from typing import Any

from rlm_poc.dsl.types import ActionPlan
from rlm_poc.dsl.validator import DSLValidationError, parse_json_dsl


def parse_json_dsl_lenient(raw: str) -> tuple[ActionPlan, str]:
    try:
        return parse_json_dsl(raw), "strict"
    except DSLValidationError as strict_error:
        last_error = strict_error

    for fenced in re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE):
        try:
            return parse_json_dsl(fenced.strip()), "fenced"
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
            return parse_json_dsl(payload), "embedded"
        except DSLValidationError as exc:
            last_error = exc

    raise DSLValidationError(f"could not extract JSON DSL: {last_error}") from last_error
