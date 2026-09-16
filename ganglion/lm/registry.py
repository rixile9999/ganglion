"""Named model registry for Module 1 ([[lm_model_registry]]).

`configs/models.yaml` → `ModelSpec` → `ModelClient`. Every run, session,
label and manifest carries a real `model_id` and `model_fingerprint`, and the
console's model picker lists the same entries the CLI accepts.

Public surface (see `docs/tasks/lm_model_registry.md`):

    ModelSpec               frozen description of one model entry
    Registry                id → spec lookup, filtered listing
    load_registry           yaml (+ discovered adapters) → Registry
    build_client_from_spec  spec + Catalog → ModelClient
    model_fingerprint       "mf-<sha12>" | "mf-rules"
    availability            (bool, detail) — can this machine run the spec?
    spec_to_row             to_jsonable() + availability columns
    discover_adapters       LoRA adapter dirs under runs/ → local_hf specs
    LLM_TO_MODEL_ID         legacy `--llm` name → registry id

This module never imports `torch` / `transformers`: the `local_hf` checks
are lazy inside `availability()` / `build_client_from_spec()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from ganglion.analyzer.repair import RepairConfig
from ganglion.contract.catalog import Catalog
from ganglion.lm.client import ModelClient

__all__ = [
    "CLIENTS",
    "DEFAULT_REGISTRY_PATH",
    "KINDS",
    "LLM_TO_MODEL_ID",
    "ModelSpec",
    "PROVIDERS",
    "RULES_SPEC",
    "Registry",
    "availability",
    "build_client_from_spec",
    "discover_adapters",
    "expand_env",
    "load_registry",
    "model_fingerprint",
    "resolve_registry_path",
    "spec_to_row",
]

KINDS: tuple[str, ...] = ("rules", "openai_compat", "local_hf")
CLIENTS: tuple[str, ...] = ("json-dsl", "freeform", "thinking", "native")
PROVIDERS: tuple[str, ...] = ("dashscope", "vllm", "openai", "none", "local")

#: Legacy `--llm` flavour → registry id ([[lm_model_registry]] CLI wiring).
LLM_TO_MODEL_ID: dict[str, str] = {
    "rules": "rules",
    "qwen": "qwen3.6-plus@dashscope",
    "qwen-text": "qwen3.6-plus-text@dashscope",
    "qwen-thinking": "qwen3.6-plus-thinking@dashscope",
    "qwen-native": "qwen3.6-plus-native@dashscope",
}

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = Path("configs") / "models.yaml"
REGISTRY_ENV = "GANGLION_MODELS"

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_ADAPTER_CONFIG = "adapter_config.json"
_MAX_DISCOVERY_DEPTH = 6


# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """One registry entry. Field semantics per [[lm_model_registry]].

    `kind ∈ KINDS`, `client ∈ CLIENTS`, `provider ∈ PROVIDERS`. The four
    trailing fields (`device`, `dtype`, `max_new_tokens`, `grammar_mask`)
    apply to `local_hf` only and are ignored elsewhere.
    """

    model_id: str
    kind: str  # "rules" | "openai_compat" | "local_hf"
    client: str = "json-dsl"  # "json-dsl" | "freeform" | "thinking" | "native"
    provider: str = "dashscope"  # "dashscope" | "vllm" | "openai" | "none" | "local"
    base_url: str | None = None
    served_model: str | None = None
    api_key_env: str | None = None  # None → "EMPTY"
    base_model: str | None = None
    adapter_dir: str | None = None
    catalog_ids: tuple[str, ...] = ("*",)
    trained_on: Mapping[str, Any] | None = None  # {"catalog_fingerprint", "dataset_sha256", "run_id"}
    notes: str = ""
    device: str = "auto"  # local_hf: "auto" (device_map) | "cuda" | "cuda:N" | "cpu"
    dtype: str = "bfloat16"  # local_hf: "bfloat16" | "float32" (checked at load time)
    max_new_tokens: int = 256  # local_hf: passed to generate_dsl
    grammar_mask: bool = False  # local_hf: honoured only when xgrammar is importable

    def to_jsonable(self) -> dict[str, Any]:
        """Every field, JSON-safe (`catalog_ids` → list, `trained_on` → dict)."""
        payload = asdict(self)
        payload["catalog_ids"] = list(self.catalog_ids)
        payload["trained_on"] = dict(self.trained_on) if self.trained_on is not None else None
        return payload

    def supports(self, catalog_id: str) -> bool:
        """`"*"` wildcard or exact `catalog_id` membership."""
        return "*" in self.catalog_ids or catalog_id in self.catalog_ids


#: Built-in fallback when no registry file exists.
RULES_SPEC = ModelSpec(
    model_id="rules",
    kind="rules",
    client="json-dsl",
    provider="none",
    catalog_ids=("iot_light_5",),
    notes="offline regex stand-in (single call)",
)

_SPEC_FIELDS: frozenset[str] = frozenset(f.name for f in fields(ModelSpec))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class Registry:
    """Ordered id → `ModelSpec` lookup."""

    def __init__(self, specs: Sequence[ModelSpec], *, path: Path | None = None) -> None:
        self._specs: dict[str, ModelSpec] = {}
        for spec in specs:
            if spec.model_id in self._specs:
                raise ValueError(f"duplicate model_id in registry: {spec.model_id!r}")
            self._specs[spec.model_id] = spec
        self.path: Path | None = Path(path) if path is not None else None

    def get(self, model_id: str) -> ModelSpec:
        """`KeyError` naming the known ids when `model_id` is unknown."""
        try:
            return self._specs[model_id]
        except KeyError:
            raise KeyError(
                f"unknown model_id {model_id!r}; known: {', '.join(self.ids()) or '(none)'}"
            ) from None

    def ids(self) -> list[str]:
        return list(self._specs)

    def list(self, catalog_id: str | None = None) -> list[ModelSpec]:
        """All specs, or only those whose `supports(catalog_id)` is true."""
        specs = list(self._specs.values())
        if catalog_id is None:
            return specs
        return [spec for spec in specs if spec.supports(catalog_id)]

    def to_jsonable(self) -> list[dict[str, Any]]:
        return [spec.to_jsonable() for spec in self._specs.values()]

    def __contains__(self, model_id: object) -> bool:
        return model_id in self._specs

    def __len__(self) -> int:
        return len(self._specs)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def expand_env(value: Any) -> Any:
    """Expand `${VAR}` / `${VAR:-default}` in strings, recursively in containers.

    Shell semantics: an unset variable with no default expands to `""`.
    Non-string scalars pass through untouched.
    """
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            env = os.environ.get(name)
            if env is not None and env != "":
                return env
            return default if default is not None else ""

        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, Mapping):
        return {str(k): expand_env(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [expand_env(v) for v in value]
    return value


def resolve_registry_path(path: str | Path | None = None) -> Path | None:
    """Explicit path → `$GANGLION_MODELS` → `./configs/models.yaml` → `<repo>/configs/models.yaml`.

    Returns `None` when none of the candidates exists (an explicit or env
    path that does not exist is returned as-is so the caller can report it).
    """
    if path is not None:
        return Path(path)
    env = os.environ.get(REGISTRY_ENV)
    if env:
        return Path(env)
    for candidate in (Path.cwd() / DEFAULT_REGISTRY_PATH, _REPO_ROOT / DEFAULT_REGISTRY_PATH):
        if candidate.is_file():
            return candidate
    return None


def _spec_from_entry(entry: Any, index: int) -> ModelSpec:
    label = f"models[{index}]"
    if not isinstance(entry, Mapping):
        raise ValueError(f"{label}: expected a mapping, got {type(entry).__name__}")
    data = dict(expand_env(entry))
    model_id = data.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError(f"{label}: 'model_id' must be a non-empty string")
    label = f"models[{index}] ({model_id})"
    unknown = sorted(set(data) - _SPEC_FIELDS)
    if unknown:
        raise ValueError(f"{label}: unknown field(s) {unknown}")
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{label}: kind must be one of {KINDS}, got {kind!r}")
    client = data.get("client", "json-dsl")
    if client not in CLIENTS:
        raise ValueError(f"{label}: client must be one of {CLIENTS}, got {client!r}")
    provider = data.get("provider", "dashscope")
    if provider not in PROVIDERS:
        raise ValueError(f"{label}: provider must be one of {PROVIDERS}, got {provider!r}")
    if kind == "local_hf" and not data.get("base_model"):
        raise ValueError(f"{label}: kind 'local_hf' requires 'base_model'")
    catalog_ids = data.get("catalog_ids", ["*"])
    if isinstance(catalog_ids, str):
        catalog_ids = [catalog_ids]
    if not isinstance(catalog_ids, (list, tuple)) or not all(isinstance(c, str) for c in catalog_ids):
        raise ValueError(f"{label}: catalog_ids must be a list of strings")
    data["catalog_ids"] = tuple(catalog_ids) or ("*",)
    trained_on = data.get("trained_on")
    if trained_on is not None and not isinstance(trained_on, Mapping):
        raise ValueError(f"{label}: trained_on must be a mapping")
    data["trained_on"] = dict(trained_on) if trained_on is not None else None
    for key in ("base_url", "served_model", "api_key_env", "base_model", "adapter_dir"):
        val = data.get(key)
        if val is not None and not isinstance(val, str):
            data[key] = str(val)
    data["notes"] = str(data.get("notes", "") or "")
    data["device"] = str(data.get("device", "auto") or "auto")
    data["dtype"] = str(data.get("dtype", "bfloat16") or "bfloat16")
    try:
        data["max_new_tokens"] = int(data.get("max_new_tokens", 256))
    except (TypeError, ValueError):
        raise ValueError(f"{label}: max_new_tokens must be an int") from None
    data["grammar_mask"] = bool(data.get("grammar_mask", False))
    return ModelSpec(**data)


def _read_yaml_specs(path: Path) -> list[ModelSpec]:
    import yaml

    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, Mapping):
        raise ValueError(f"{path}: top level must be a mapping with a 'models:' list")
    entries = doc.get("models")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError(f"{path}: 'models' must be a list")
    return [_spec_from_entry(entry, idx) for idx, entry in enumerate(entries)]


def load_registry(path: str | Path | None = None, *, discover: bool = True) -> Registry:
    """Load the registry file (or the rules-only fallback) plus discovered adapters.

    Resolution: `path` → `$GANGLION_MODELS` → `configs/models.yaml` relative
    to cwd, then to the repo root. A missing file is not an error: the
    registry then holds only the built-in `rules` spec and `path is None`.
    Malformed entries raise `ValueError` naming the entry. With
    `discover=True`, adapters found under `runs/` are merged after the
    YAML entries (YAML wins on an id clash).
    """
    file = resolve_registry_path(path)
    if file is not None and file.is_file():
        specs = _read_yaml_specs(file)
        registry_path: Path | None = file
    else:
        if path is not None or os.environ.get(REGISTRY_ENV):
            print(
                f"[registry] {file} not found; falling back to the built-in rules spec",
                file=sys.stderr,
            )
        specs = [RULES_SPEC]
        registry_path = None
    if discover:
        known = {spec.model_id for spec in specs}
        specs.extend(s for s in discover_adapters() if s.model_id not in known)
    return Registry(specs, path=registry_path)


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


def build_client_from_spec(
    spec: ModelSpec,
    catalog: Catalog,
    *,
    repair: RepairConfig | None = None,
) -> ModelClient:
    """`ModelSpec` + `Catalog` → concrete `ModelClient` (no network at build).

    - `rules` → `RuleBasedJSONDSLClient()`.
    - `openai_compat` → explicit `QwenConfig` then the client named by
      `spec.client`. `api_key` is `$<api_key_env>` or `"EMPTY"`; `base_url`
      is `$DASHSCOPE_BASE_URL` (dashscope provider only, when set) → `spec.base_url`
      → the `QwenConfig` default (never `None`). `disable_thinking =
      (spec.client != "thinking")`; the freeform client gets
      `enable_thinking=(spec.client == "thinking")` explicitly (errata E11).
    - `local_hf` → `LocalHFClient(catalog, spec)`; weights load lazily on the
      first `invoke`, so building never imports `torch`.
    """
    if spec.kind == "rules":
        from ganglion.lm.rules import RuleBasedJSONDSLClient

        return RuleBasedJSONDSLClient()

    if spec.kind == "openai_compat":
        from ganglion.lm.dashscope import (
            QwenConfig,
            QwenFreeformJSONDSLClient,
            QwenJSONDSLClient,
            QwenNativeToolClient,
        )

        api_key = (os.environ.get(spec.api_key_env) if spec.api_key_env else None) or "EMPTY"
        base_url: str | None = None
        if spec.provider == "dashscope":
            base_url = os.environ.get("DASHSCOPE_BASE_URL") or None
        base_url = base_url or spec.base_url or QwenConfig.base_url
        provider = spec.provider if spec.provider in ("dashscope", "vllm", "openai") else "openai"
        config = QwenConfig(
            api_key=api_key,
            model=spec.served_model or QwenConfig.model,
            base_url=base_url,
            disable_thinking=(spec.client != "thinking"),
            provider=provider,
        )
        if spec.client == "json-dsl":
            return QwenJSONDSLClient(catalog, config, repair=repair)
        if spec.client == "freeform":
            return QwenFreeformJSONDSLClient(catalog, config, enable_thinking=False)
        if spec.client == "thinking":
            return QwenFreeformJSONDSLClient(catalog, config, enable_thinking=True)
        if spec.client == "native":
            return QwenNativeToolClient(catalog, config)
        raise ValueError(f"unknown client {spec.client!r} for {spec.model_id}")

    if spec.kind == "local_hf":
        from ganglion.lm.local_hf import LocalHFClient

        return LocalHFClient(catalog, spec)

    raise ValueError(f"unknown kind {spec.kind!r} for {spec.model_id}")


# ---------------------------------------------------------------------------
# Fingerprint / availability / rows
# ---------------------------------------------------------------------------


def _adapter_digest(adapter_dir: str | None) -> str:
    """sha256 over `adapter_config.json` bytes + sorted `(name, size)` of `*.safetensors`."""
    if not adapter_dir:
        return ""
    root = Path(adapter_dir)
    if not root.is_dir():
        return f"missing:{adapter_dir}"
    h = hashlib.sha256()
    cfg = root / _ADAPTER_CONFIG
    if cfg.is_file():
        h.update(cfg.read_bytes())
    for weights in sorted(root.glob("*.safetensors")):
        try:
            size = weights.stat().st_size
        except OSError:
            size = -1
        h.update(f"{weights.name}:{size}".encode("utf-8"))
    return h.hexdigest()


def model_fingerprint(spec: ModelSpec) -> str:
    """`"mf-" + sha256(served_model | base_model | adapter digest)[:12]`; `"mf-rules"` for rules."""
    if spec.kind == "rules":
        return "mf-rules"
    material = "|".join(
        [spec.served_model or "", spec.base_model or "", _adapter_digest(spec.adapter_dir)]
    )
    return "mf-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _looks_like_path(value: str) -> bool:
    """Absolute / dotted / home-relative strings are paths; `a/b` that exists on disk too.

    A hub id such as `Qwen/Qwen3-0.6B` contains a separator but does not
    exist locally, so it is *not* treated as a path (no network check).
    """
    if value.startswith(("/", "./", "../", "~")):
        return True
    return os.sep in value and Path(value).expanduser().exists()


def availability(spec: ModelSpec) -> tuple[bool, str]:
    """Can this machine run `spec` right now? `(ok, detail)`.

    - `rules` → `(True, "")`.
    - `openai_compat` → `(True, "")` unless `api_key_env` names an unset
      variable → `(False, "<env> not set")`.
    - `local_hf` → `local_hf_available()` (lazy import; never at registry
      load) and, when `base_model` is a filesystem path, that it exists. A
      hub id is not resolved (no network here).
    """
    if spec.kind == "rules":
        return True, ""
    if spec.kind == "openai_compat":
        if spec.api_key_env and not os.environ.get(spec.api_key_env):
            return False, f"{spec.api_key_env} not set"
        return True, ""
    if spec.kind == "local_hf":
        from ganglion.lm.local_hf import local_hf_available

        ok, detail = local_hf_available()
        if not ok:
            return False, detail
        base = spec.base_model or ""
        if not base:
            return False, "base_model not set"
        if _looks_like_path(base) and not Path(base).expanduser().exists():
            return False, f"{base} not found"
        if spec.adapter_dir and not Path(spec.adapter_dir).expanduser().is_dir():
            return False, f"adapter_dir {spec.adapter_dir} not found"
        return True, detail
    return False, f"unknown kind {spec.kind!r}"


def spec_to_row(spec: ModelSpec) -> dict[str, Any]:
    """`to_jsonable()` + `available` / `available_detail` (what the picker shows)."""
    row = spec.to_jsonable()
    ok, detail = availability(spec)
    row["available"] = ok
    row["available_detail"] = detail
    return row


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------


def _walk_dirs(root: Path, max_depth: int):
    """Yield directories under `root` (root at depth 0) up to `max_depth`, no symlinks."""
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current
        if depth >= max_depth:
            continue
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue


def discover_adapters(
    roots: Sequence[str] = ("runs",),
    *,
    base_model_fallback: str | None = None,
) -> list[ModelSpec]:
    """Every dir under `roots` (max depth 6) holding `adapter_config.json` → a `local_hf` spec.

    `model_id = f"local:{dir.name}"`, `base_model` from the config's
    `base_model_name_or_path` (or `base_model_fallback`; neither → skipped),
    `notes="discovered"`. Unreadable / non-JSON configs are skipped with a
    stderr note; discovery never aborts a registry load.
    """
    specs: list[ModelSpec] = []
    seen_ids: set[str] = set()
    for root_name in roots:
        root = Path(root_name)
        if not root.is_dir():
            continue
        for directory in _walk_dirs(root, _MAX_DISCOVERY_DEPTH):
            cfg_path = directory / _ADAPTER_CONFIG
            if not cfg_path.is_file():
                continue
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"[registry] skipping {cfg_path}: {exc}", file=sys.stderr)
                continue
            base = cfg.get("base_model_name_or_path") if isinstance(cfg, Mapping) else None
            base = base or base_model_fallback
            if not base:
                continue
            model_id = f"local:{directory.name}"
            if model_id in seen_ids:
                # Same leaf name twice (e.g. two `adapter/` dirs) — disambiguate.
                model_id = f"local:{directory.parent.name}/{directory.name}"
                if model_id in seen_ids:
                    continue
            seen_ids.add(model_id)
            specs.append(
                ModelSpec(
                    model_id=model_id,
                    kind="local_hf",
                    provider="local",
                    base_model=str(base),
                    adapter_dir=str(directory),
                    catalog_ids=("*",),
                    notes="discovered",
                )
            )
    return specs
