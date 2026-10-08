"""Versioned data-only application contracts; backend code is allowlisted."""
from __future__ import annotations

import hashlib
import json
import re


def fingerprint(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def preprocessing(spec):
    """Resolve a pinned optional PreprocessorSpec, including legacy aliases."""
    value = spec.get("preprocessor")
    if value is None:
        return None
    if value == "utf8-windows":
        return {"strategy": "fixed", "max_chars": 1024, "overlap_chars": 128, "max_tokens": 1024}
    if not isinstance(value, dict) or set(value) - {"adapter", "version", "config", "candidate_graph"} or not {"adapter", "version", "config"}.issubset(value) or value.get("adapter") != "utf8-windows" or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("unsupported PreprocessorSpec")
    config = value["config"]
    if not isinstance(config, dict) or set(config) != {"strategy", "max_chars", "overlap_chars", "max_tokens"}:
        raise ValueError("PreprocessorSpec must declare splitting and input budgets")
    if config["strategy"] not in ("fixed", "semantic"):
        raise ValueError("unknown preprocessor splitting strategy")
    if any(type(config[name]) is not int for name in ("max_chars", "overlap_chars", "max_tokens")):
        raise ValueError("preprocessor budgets must be integers")
    if not 32 <= config["max_chars"] <= 8192 or not 0 <= config["overlap_chars"] < config["max_chars"] // 2 or not 1 <= config["max_tokens"] <= 1024:
        raise ValueError("preprocessor budget is out of range")
    return dict(config)


def candidate_preprocessing(spec):
    """Pinned structured candidate IR; runtime requests cannot change budgets."""
    preprocessor = spec.get("preprocessor")
    descriptor = preprocessor.get("candidate_graph") if isinstance(preprocessor, dict) else None
    backend = spec.get("model", {}).get("backend")
    if descriptor is None:
        if backend == "qwen_candidates":
            raise ValueError("candidate backend requires a versioned candidate graph preprocessor")
        if isinstance(preprocessor, dict) and "candidate_graph" in preprocessor:
            raise ValueError("candidate graph requires the candidate model backend")
        return None
    if backend != "qwen_candidates":
        raise ValueError("candidate graph requires the candidate model backend")
    if not isinstance(descriptor, dict) or set(descriptor) != {"adapter", "version", "config"} or descriptor.get("adapter") != "pii-candidate-graph" or type(descriptor.get("version")) is not int or descriptor["version"] != 1:
        raise ValueError("unsupported candidate graph preprocessor")
    config = descriptor["config"]
    bounds = {"context_chars": (0, 4096), "max_candidates": (1, 10000), "max_candidate_chars": (1, 4096)}
    if not isinstance(config, dict) or set(config) != set(bounds):
        raise ValueError("candidate graph must declare context and candidate budgets")
    if any(type(config[key]) is not int or not low <= config[key] <= high for key, (low, high) in bounds.items()):
        raise ValueError("candidate graph budget is out of range")
    return dict(config)


def validate(spec):
    if not isinstance(spec, dict) or spec.get("version") != 2:
        raise ValueError("ApplicationSpec version must be 2")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", str(spec.get("id", ""))):
        raise ValueError("invalid spec id")
    if spec.get("domain") not in {"pii-text", "tool-calling"}:
        raise ValueError("domain adapter is not installed")
    if not isinstance(spec.get("title"), str) or not spec["title"]:
        raise ValueError("spec title is required")
    expected = {"pii-text": {"rules", "qwen_native", "qwen_candidates"}, "tool-calling": {"registry"}}
    if not isinstance(spec.get("model"), dict) or spec["model"].get("backend") not in expected[spec["domain"]]:
        raise ValueError("model backend is not installed for this domain")
    if not isinstance(spec.get("contract"), dict) or spec["contract"].get("output") != {"pii-text": "TextEditPlan", "tool-calling": "ActionPlan"}[spec["domain"]]:
        raise ValueError("output contract does not match domain")
    schema = spec.get("input_schema", {})
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        raise ValueError("input_schema must be an object schema")
    for name, field in schema["properties"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(name)):
            raise ValueError("invalid input field name")
        if not isinstance(field, dict) or field.get("type") not in {"string", "integer", "boolean"}:
            raise ValueError("input fields must be string, integer or boolean")
        check = {"string": lambda v: isinstance(v, str), "integer": lambda v: type(v) is int, "boolean": lambda v: type(v) is bool}[field["type"]]
        for key in ("minimum", "maximum", "maxLength"):
            if key in field and (type(field[key]) is not int or key == "maxLength" and not 1 <= field[key] <= 1048576):
                raise ValueError("invalid schema bound")
        if field.get("minimum", 0) > field.get("maximum", field.get("minimum", 0)):
            raise ValueError("schema minimum exceeds maximum")
        if "enum" in field and (not isinstance(field["enum"], list) or not field["enum"] or any(not check(v) for v in field["enum"])):
            raise ValueError("invalid schema enum")
        if "default" in field and (not check(field["default"]) or "enum" in field and field["default"] not in field["enum"]):
            raise ValueError("invalid schema default")
        if "default" in field:
            default = field["default"]
            if field["type"] == "integer" and not field.get("minimum", default) <= default <= field.get("maximum", default):
                raise ValueError("schema default is out of range")
            if field["type"] == "string" and len(default) > field.get("maxLength", 32768):
                raise ValueError("schema default is too long")
    if not isinstance(schema.get("required", []), list) or any(not isinstance(s, str) for s in schema.get("required", [])) or not set(schema.get("required", [])).issubset(schema["properties"]):
        raise ValueError("required fields must exist in schema")
    if not isinstance(spec.get("result_schema"), dict) or not isinstance(spec["result_schema"].get("properties"), dict):
        raise ValueError("result_schema must declare properties")
    if any(not isinstance(field, dict) for field in spec["result_schema"]["properties"].values()):
        raise ValueError("result schema fields must be objects")
    required = {"pii-text": {"document", "execute", "public_key"}, "tool-calling": {"prompt", "catalog_id", "model_id"}}
    if not required[spec["domain"]].issubset(schema["properties"]):
        raise ValueError("domain input fields are missing")
    for name in required[spec["domain"]]:
        if schema["properties"][name]["type"] != ("boolean" if name == "execute" else "string"):
            raise ValueError("domain input field type mismatch")
    if spec["domain"] == "pii-text":
        if spec["contract"].get("coordinate") != "utf8-byte":
            raise ValueError("TextEditPlan coordinates must be utf8-byte")
        for name in ("window_chars", "overlap_chars"):
            if name in schema["properties"] and schema["properties"][name]["type"] != "integer":
                raise ValueError("window parameters must be integers")
        if spec["model"]["backend"] in {"qwen_native", "qwen_candidates"} and spec["model"].get("base_model", "Qwen/Qwen3.5-0.8B") != "Qwen/Qwen3.5-0.8B":
            raise ValueError("native adapter is installed for Qwen/Qwen3.5-0.8B")
    preprocessing(spec)
    candidate_preprocessing(spec)
    executor = spec.get("executor")
    if executor is not None and executor != "reversible-text":
        raise ValueError("optional executor adapter is not installed")
    if isinstance(spec.get("preprocessor"), dict) and {"window_chars", "overlap_chars"} & set(schema["properties"]):
        raise ValueError("preprocessor budgets belong in its spec, not runtime input fields")
    return spec


def validate_inputs(schema, inputs):
    if not isinstance(inputs, dict) or set(inputs) - set(schema["properties"]):
        raise ValueError("unknown input fields")
    result = {}
    for name, field in schema["properties"].items():
        value = inputs.get(name, field.get("default"))
        if value is None:
            if name in schema.get("required", []):
                raise ValueError(f"missing input: {name}")
            continue
        valid = {"string": isinstance(value, str), "integer": type(value) is int, "boolean": type(value) is bool}
        if not valid[field["type"]] or ("enum" in field and value not in field["enum"]):
            raise ValueError(f"invalid input: {name}")
        if isinstance(value, str) and len(value) > field.get("maxLength", 32768):
            raise ValueError(f"input too long: {name}")
        if type(value) is int and not field.get("minimum", value) <= value <= field.get("maximum", value):
            raise ValueError(f"input out of range: {name}")
        result[name] = value
    return result


def builtins():
    pii = {"version": 2, "id": "pii-rules", "title": "개인정보 가명화 · 규칙 기준선", "domain": "pii-text",
           "description": "한국어·영어 UTF-8 문서를 분할 처리합니다. 탐지 품질은 실험 단계입니다.",
           "model": {"backend": "rules"}, "contract": {"output": "TextEditPlan", "coordinate": "utf8-byte"},
           "preprocessor": {"adapter": "utf8-windows", "version": 1, "config": {"strategy": "fixed", "max_chars": 1024, "overlap_chars": 128, "max_tokens": 1024}}, "executor": "reversible-text",
           "input_schema": {"type": "object", "required": ["document"], "properties": {
               "document": {"type": "string", "title": "입력 문서", "format": "document", "maxLength": 64},
               "execute": {"type": "boolean", "title": "가명화 실행 및 복원 파일 생성", "default": True},
               "public_key": {"type": "string", "title": "복원 공개키", "format": "public-key", "default": "", "maxLength": 100}}},
           "result_schema": {"type": "object", "properties": {"edits": {"title": "가명화 구간"}, "units": {"title": "처리 구간"},
               "processed_bytes": {"title": "문서 바이트"}, "input_tokens": {"title": "입력 토큰"},
               "output_tokens": {"title": "생성 토큰"}, "latency_ms": {"title": "실행 시간 (ms)"}}}}
    native = json.loads(json.dumps(pii))
    native.update(id="pii-qwen", title="개인정보 가명화 · Qwen 0.8B", description="Qwen3.5-0.8B + BIO/유형 head. 합성 데이터로 학습한 실험 모델입니다.")
    native["model"] = {"backend": "qwen_native", "base_model": "Qwen/Qwen3.5-0.8B"}
    native["preprocessor"]["config"] = {"strategy": "semantic", "max_chars": 256, "overlap_chars": 64, "max_tokens": 128}
    candidate = json.loads(json.dumps(native))
    candidate.update(id="pii-qwen-candidates", title="개인정보 가명화 · 구조화 후보 Qwen", description="후보 ID와 유형을 선택하는 실험 모델입니다. 후보 한도 초과 시 원본 Qwen 탐지기로 명시적으로 대체합니다.")
    candidate["model"]["backend"] = "qwen_candidates"
    candidate["preprocessor"]["candidate_graph"] = {"adapter": "pii-candidate-graph", "version": 1,
        "config": {"context_chars": 48, "max_candidates": 256, "max_candidate_chars": 160}}
    tool = {"version": 2, "id": "tool-planner", "title": "도구 호출 계획", "domain": "tool-calling",
            "description": "기존 모델 레지스트리와 계약으로 ActionPlan을 만듭니다.",
            "model": {"backend": "registry"}, "contract": {"output": "ActionPlan"},
            "preprocessor": None, "executor": None,
            "input_schema": {"type": "object", "required": ["prompt"], "properties": {
                "prompt": {"type": "string", "title": "요청", "format": "textarea"},
                "catalog_id": {"type": "string", "title": "계약 ID", "default": "iot_light_5"},
                "model_id": {"type": "string", "title": "모델 ID", "default": "rules"}}},
            "result_schema": {"type": "object", "properties": {"latency_ms": {"title": "실행 시간 (ms)"},
                "input_tokens": {"title": "입력 토큰"}, "output_tokens": {"title": "출력 토큰"}}}}
    return {s["id"]: s for s in (pii, native, candidate, tool)}
