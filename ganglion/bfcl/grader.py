"""BFCL v4 AST grader — Python-only re-implementation.

Mirrors the official `bfcl_eval.eval_checker.ast_eval.ast_checker` semantics
(see `examples/bfcl/v4/SOURCE.md` for the upstream commit), restricted to
Python categories: simple_python, multiple, parallel, parallel_multiple.
The Java/JavaScript type-conversion branches and the `convert_func_name`
OpenAI-name munging are deliberately omitted — Ganglion only evaluates
Python tool calls and preserves function names verbatim through the DSL.

Inputs from Ganglion side:
    predicted_calls — sequence of ToolCall (ganglion.dsl.tool_spec)
    case            — BFCLCase whose `ground_truth` and `tools` drive the check

Output:
    GraderResult(valid: bool, error_type: str | None, errors: list[str])

The grader is the single decision metric for `ast_match_rate` in M1'-M4'.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ganglion.bfcl.loader import BFCLCase
from ganglion.dsl.types import ToolCall


PYTHON_TYPE_MAPPING: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "array": list,
    "tuple": list,
    "dict": dict,
    "any": str,
}

NESTED_TYPES = ("array", "tuple")


@dataclass
class GraderResult:
    valid: bool
    error_type: str | None = None
    errors: list[str] = field(default_factory=list)


def ast_match(predicted_calls: Sequence[ToolCall], case: BFCLCase) -> GraderResult:
    """Grade predicted calls for a BFCLCase using BFCL AST semantics.

    Irrelevance cases (no ground_truth) are valid iff the model emitted no
    calls — `predicted_calls` is empty.
    """
    if case.ground_truth is None:
        if not predicted_calls:
            return GraderResult(valid=True)
        return GraderResult(
            valid=False,
            error_type="irrelevance:unexpected_call",
            errors=[f"Expected no call, got {len(predicted_calls)}."],
        )

    model_output = [_call_to_dict(c) for c in predicted_calls]
    possible_answers = list(case.ground_truth)
    func_descriptions = list(case.tools)
    category = case.category

    if "parallel" in category:
        return _parallel_no_order(func_descriptions, model_output, possible_answers)
    if "multiple" in category:
        return _multiple(func_descriptions, model_output, possible_answers)

    if len(model_output) != 1:
        return GraderResult(
            valid=False,
            error_type="simple_function_checker:wrong_count",
            errors=["Wrong number of functions."],
        )
    return _simple(func_descriptions[0], model_output[0], possible_answers[0])


def _call_to_dict(call: ToolCall) -> dict[str, dict[str, Any]]:
    return {call.action: dict(call.args)}


def _find_description(
    func_descriptions: Sequence[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    for desc in func_descriptions:
        if desc.get("name") == name:
            return desc
    return None


def _multiple(
    func_descriptions: Sequence[dict[str, Any]],
    model_output: list[dict[str, Any]],
    possible_answers: list[dict[str, Any]],
) -> GraderResult:
    if len(model_output) != len(possible_answers):
        return GraderResult(
            valid=False,
            error_type="multiple_function_checker:wrong_count",
            errors=["Wrong number of functions."],
        )
    func_name_expected = next(iter(possible_answers[0].keys()))
    func_description = _find_description(func_descriptions, func_name_expected)
    if func_description is None:
        return GraderResult(
            valid=False,
            error_type="multiple_function_checker:missing_description",
            errors=[f"No description for {func_name_expected!r}."],
        )
    return _simple(func_description, model_output[0], possible_answers[0])


def _parallel_no_order(
    func_descriptions: Sequence[dict[str, Any]],
    model_output: list[dict[str, Any]],
    possible_answers: list[dict[str, Any]],
) -> GraderResult:
    if len(model_output) != len(possible_answers):
        return GraderResult(
            valid=False,
            error_type="parallel_function_checker_no_order:wrong_count",
            errors=["Wrong number of functions."],
        )

    matched: set[int] = set()
    for answer in possible_answers:
        func_name_expected = next(iter(answer.keys()))
        func_description = _find_description(func_descriptions, func_name_expected)
        if func_description is None:
            return GraderResult(
                valid=False,
                error_type="parallel_function_checker_no_order:missing_description",
                errors=[f"No description for {func_name_expected!r}."],
            )

        found = False
        for idx, candidate in enumerate(model_output):
            if idx in matched:
                continue
            sub = _simple(func_description, candidate, answer)
            if sub.valid:
                matched.add(idx)
                found = True
                break
        if not found:
            return GraderResult(
                valid=False,
                error_type="parallel_function_checker_no_order:cannot_find_match",
                errors=[
                    f"No model output matches expected call for {func_name_expected!r}."
                ],
            )
    return GraderResult(valid=True)


def _simple(
    func_description: dict[str, Any],
    model_output: dict[str, Any],
    possible_answer: dict[str, Any],
) -> GraderResult:
    accepted = next(iter(possible_answer.values()))
    func_name = func_description["name"]
    param_details = func_description["parameters"]["properties"]
    required_params = func_description["parameters"].get("required", [])

    if func_name not in model_output:
        return GraderResult(
            valid=False,
            error_type="simple_function_checker:wrong_func_name",
            errors=[f"Function name {func_name!r} not found in model output."],
        )

    model_params = model_output[func_name]

    for param in required_params:
        if param not in model_params:
            return GraderResult(
                valid=False,
                error_type="simple_function_checker:missing_required",
                errors=[f"Missing required parameter: {param!r}."],
            )

    for param, value in model_params.items():
        if param not in param_details or param not in accepted:
            return GraderResult(
                valid=False,
                error_type="simple_function_checker:unexpected_param",
                errors=[f"Unexpected parameter: {param!r}."],
            )

        full_details = param_details[param]
        expected_type_desc = full_details["type"]
        expected_type = PYTHON_TYPE_MAPPING.get(expected_type_desc)
        if expected_type is None:
            return GraderResult(
                valid=False,
                error_type="type_error:unknown",
                errors=[f"Unknown expected type {expected_type_desc!r} for {param!r}."],
            )

        nested_type = None
        if expected_type_desc in NESTED_TYPES:
            items_meta = full_details.get("items") or {}
            nested_desc = items_meta.get("type")
            if nested_desc is not None:
                nested_type = PYTHON_TYPE_MAPPING.get(nested_desc)

        # Tuple value normalisation (BFCL: tuples in possible answer become lists
        # after JSON round-trip; we coerce model tuples to list before checking).
        if expected_type_desc == "tuple" and isinstance(value, tuple):
            value = list(value)

        # Python int → float auto promotion when expected type is float.
        if expected_type_desc == "float" and type(value) is int:
            value = float(value)

        type_check = _type_checker(
            param, value, accepted[param], expected_type_desc, expected_type, nested_type
        )
        if not type_check.valid:
            return type_check
        is_variable = type_check.error_type == "is_variable"

        if not is_variable:
            if expected_type is dict:
                check = _dict_checker(param, value, accepted[param])
                if not check.valid:
                    return check
                continue

            if expected_type is list and nested_type is dict:
                check = _list_dict_checker(param, value, accepted[param])
                if not check.valid:
                    return check
                continue

            if expected_type is str:
                check = _string_checker(param, value, accepted[param])
                if not check.valid:
                    return check
                continue

            if expected_type is list:
                check = _list_checker(param, value, accepted[param])
                if not check.valid:
                    return check
                continue

        if value not in accepted[param]:
            return GraderResult(
                valid=False,
                error_type="value_error:others",
                errors=[
                    f"Invalid value for parameter {param!r}: {value!r}. "
                    f"Expected one of {accepted[param]}."
                ],
            )

    for param, accepted_values in accepted.items():
        if param not in model_params and "" not in accepted_values:
            return GraderResult(
                valid=False,
                error_type="simple_function_checker:missing_optional",
                errors=[f"Optional parameter {param!r} not provided and not marked as optional."],
            )

    return GraderResult(valid=True)


def _type_checker(
    param: str,
    value: Any,
    accepted: list[Any],
    expected_type_desc: str,
    expected_type: type,
    nested_type: type | None,
) -> GraderResult:
    """Replicates BFCL's type_checker, returning GraderResult.

    The "is_variable" signal (model returned a string placeholder where the
    schema expects another type, but the possible_answer also lists strings)
    is encoded by setting `error_type == "is_variable"` on a valid result.
    """
    answer_type = _possible_answer_type(accepted)
    is_variable = answer_type is not None and answer_type is not expected_type

    # bool is a subclass of int in Python; BFCL relies on `type() ==` which
    # already separates them, so we mirror that with `type(value) is`.
    if type(value) is expected_type:
        if nested_type is None:
            return GraderResult(
                valid=True,
                error_type="is_variable" if is_variable else None,
            )
        # nested check: each element of `value` must be `nested_type` and
        # match at least one accepted nested option.
        for accepted_item in accepted:
            if not isinstance(accepted_item, list):
                continue
            ok = True
            for inner in value:
                inner_check = _type_checker(
                    param, inner, accepted_item, str(nested_type), nested_type, None
                )
                if not inner_check.valid:
                    ok = False
                    break
            if ok:
                return GraderResult(
                    valid=True,
                    error_type="is_variable" if is_variable else None,
                )
        return GraderResult(
            valid=False,
            error_type="type_error:nested",
            errors=[
                f"Nested type checking failed for {param!r}. "
                f"Expected outer {expected_type_desc} with inner {nested_type}. "
                f"Value: {value!r}."
            ],
        )

    # Variable substitution: model emitted a string placeholder.
    if answer_type is not None and type(value) is answer_type:
        return GraderResult(valid=True, error_type="is_variable")

    return GraderResult(
        valid=False,
        error_type="type_error:simple",
        errors=[
            f"Incorrect type for parameter {param!r}. "
            f"Expected {expected_type_desc}, got {type(value).__name__}. "
            f"Value: {value!r}."
        ],
    )


def _possible_answer_type(accepted: list[Any]) -> type | None:
    for answer in accepted:
        if answer != "":
            return type(answer)
    return None


_STANDARDIZE_RE = re.compile(r"[ ,./\-_*^]")


def _standardize_string(text: str) -> str:
    return _STANDARDIZE_RE.sub("", text).lower().replace("'", '"')


def _string_checker(param: str, value: str, accepted: list[Any]) -> GraderResult:
    standardized_value = _standardize_string(value)
    standardized_accepted = [
        _standardize_string(a) for a in accepted if isinstance(a, str)
    ]
    if standardized_value not in standardized_accepted:
        return GraderResult(
            valid=False,
            error_type="value_error:string",
            errors=[
                f"Invalid value for parameter {param!r}: {value!r}. "
                f"Expected one of {accepted}. Case insensitive."
            ],
        )
    return GraderResult(valid=True)


def _list_checker(param: str, value: list[Any], accepted: list[Any]) -> GraderResult:
    standardized_value = [
        _standardize_string(item) if isinstance(item, str) else item for item in value
    ]
    standardized_accepted: list[list[Any]] = []
    for option in accepted:
        if not isinstance(option, list):
            continue
        standardized_accepted.append(
            [_standardize_string(item) if isinstance(item, str) else item for item in option]
        )
    if standardized_value not in standardized_accepted:
        return GraderResult(
            valid=False,
            error_type="value_error:list/tuple",
            errors=[
                f"Invalid value for parameter {param!r}: {value!r}. "
                f"Expected one of {accepted}."
            ],
        )
    return GraderResult(valid=True)


def _dict_checker(
    param: str, model_dict: dict[str, Any], accepted_dicts: list[Any]
) -> GraderResult:
    last_failure = GraderResult(
        valid=False, error_type="dict_checker:unclear", errors=[]
    )
    for option in accepted_dicts:
        if option == "":
            continue
        failure: GraderResult | None = None

        for key, value in model_dict.items():
            if key not in option:
                failure = GraderResult(
                    valid=False,
                    error_type="value_error:dict_key",
                    errors=[f"Unexpected dict key: {key!r}."],
                )
                break

            standardized_value = _standardize_string(value) if isinstance(value, str) else value
            standardized_accepted = [
                _standardize_string(v) if isinstance(v, str) else v
                for v in option[key]
            ]
            if standardized_value not in standardized_accepted:
                failure = GraderResult(
                    valid=False,
                    error_type="value_error:dict_value",
                    errors=[
                        f"Invalid value for {key!r}: {value!r}. "
                        f"Expected one of {standardized_accepted}."
                    ],
                )
                break

        if failure is None:
            for key, accepted_values in option.items():
                if key not in model_dict and "" not in accepted_values:
                    failure = GraderResult(
                        valid=False,
                        error_type="value_error:dict_key",
                        errors=[f"Missing dict key: {key!r}."],
                    )
                    break

        if failure is None:
            return GraderResult(valid=True)
        last_failure = failure

    return last_failure


def _list_dict_checker(
    param: str, model_list: list[Any], accepted_lists: list[Any]
) -> GraderResult:
    last_failure = GraderResult(
        valid=False, error_type="list_dict_checker:unclear", errors=[]
    )
    for option in accepted_lists:
        if not isinstance(option, list):
            continue
        if len(model_list) != len(option):
            last_failure = GraderResult(
                valid=False,
                error_type="value_error:list_dict_count",
                errors=["Wrong number of dictionaries in the list."],
            )
            continue
        ok = True
        for idx, model_dict in enumerate(model_list):
            sub = _dict_checker(param, model_dict, [option[idx]])
            if not sub.valid:
                last_failure = sub
                ok = False
                break
        if ok:
            return GraderResult(valid=True)
    return last_failure


__all__ = ["GraderResult", "ast_match"]
