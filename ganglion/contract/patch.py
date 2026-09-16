"""Pure, in-memory ``RulePatch`` application and hook stripping.

Spec: [[contract_patch_apply]] (``docs/tasks/contract_patch_apply.md``);
payload shapes are the ones ``ganglion/analyzer/rules.py`` really emits
(SSOT: [[analyzer_rule_synthesis]]).

Two entry points:

* :func:`apply_patch` — return a new ``Catalog`` with exactly one patch
  applied to ``target_tool``. Every payload that cannot be applied
  *mechanically* (a new arg, a ``RawArg`` swap, a prompt nudge, an
  escalation) raises :class:`PatchNotApplicableError` instead of being
  approximated with a looser spec.
* :func:`strip_hooks` — return a copy with the named post-correction
  hook kinds removed from **every** tool, so a stored raw output can be
  re-parsed as F⁰ (model alone) next to Fᴷ (model + hooks).
  ``custom_validator`` is never stripped: it is the validator for
  ``RawArg`` shapes, not a correction.

Both are pure: ``dataclasses.replace`` all the way down (``Catalog`` →
``ToolSpec`` → ``ArgSpec`` are frozen), the input object is never mutated,
no files are written and no events are emitted. ``apply_patch`` never
publishes a catalog — the "analyzer proposes, human applies" boundary
stays where it is.

Import discipline: ``tool_spec`` at top level, ``Catalog`` only under
``TYPE_CHECKING`` (see the cycle note in ``describe.py``).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ganglion.contract.tool_spec import (
    DefaultRule,
    EnumArg,
    IntArg,
    StringArg,
    ToolSpec,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, see [[contract_patch_apply]]
    from ganglion.contract.catalog import Catalog

__all__ = [
    "ALL_HOOK_KINDS",
    "PatchNotApplicableError",
    "apply_patch",
    "strip_hooks",
]

#: The three post-correction hook kinds a ``ToolSpec`` can carry. Order is
#: the order ``Catalog.validate_call`` applies them in.
ALL_HOOK_KINDS: tuple[str, ...] = (
    "defaults_when_missing",
    "strip_unknown_args",
    "prompt_correction",
)

_HOOK_KIND_SET = frozenset(ALL_HOOK_KINDS)

#: Operations ``apply_patch`` refuses by design (not a bug): a prompt nudge
#: is not a ``ToolSpec`` field, and an escalation is a human decision.
_NEVER_APPLICABLE: frozenset[str] = frozenset({"add_prompt_correction", "ESCALATE"})


class PatchNotApplicableError(ValueError):
    """Raised when a ``RulePatch`` cannot be applied mechanically.

    Covers: unknown ``target_tool``, ``catalog_id`` mismatch, unknown or
    never-applicable ``operation``, and every per-operation precondition
    listed in [[contract_patch_apply]]. Callers record it (the console
    preview shows "not applicable"); there is never a partial apply.
    """


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


def apply_patch(catalog: Catalog, patch: Mapping[str, Any]) -> Catalog:
    """Return a new ``Catalog`` with ``patch`` applied to its ``target_tool``.

    ``patch`` is a ``RulePatch.to_dict()`` mapping; only ``catalog_id``,
    ``target_tool``, ``operation`` and ``payload`` are read.

    Preconditions (each failure → :class:`PatchNotApplicableError`):

    * ``catalog.get_tool(patch["target_tool"])`` exists;
    * when ``patch["catalog_id"]`` is present (non-empty) it equals
      ``catalog.name`` — ``synthesize_rules`` stamps ``catalog.name`` and
      compiled catalogs are compiled with ``name=catalog_id``, so the two
      agree; patching the wrong catalog fails loud.

    Operation table (payload keys as emitted by ``analyzer/rules.py``):

    ``add_alias``
        ``{"arg", "aliases": {observed: gold}, "kind": "enum"|"string"}`` —
        extend ``EnumArg.aliases`` / ``StringArg.aliases``. ``kind`` must
        match the spec class; for ``enum`` every gold must be in ``values``;
        a key already mapped to a *different* gold raises; an identical
        mapping is a no-op.
    ``set_default``
        ``{"arg", "default", "predicate_hint": {"requires_args": [...]}}`` —
        append ``(arg, default, lambda args: all(k in args for k in req))``
        to ``defaults_when_missing``; an empty ``requires_args`` degenerates
        to always-True. ``arg`` must be declared; an existing rule for the
        same ``arg`` raises (retire it first).
    ``enable_strip_unknown_args``
        ``{"strip_unknown_args": true}`` — ``replace(tool,
        strip_unknown_args=True)``; already ``True`` → no-op.
    ``extend_argspec``
        applicable **iff** ``payload["transform"] == "percent"`` and the
        arg is an ``IntArg`` → ``replace(spec, allow_percent=True)``.
        Shape A (``spec_hint: "RawArg"`` + ``observed_types``) is an
        add-a-new-arg shape change; ``int_from_string`` is already accepted
        by the validator; ``strip_unit`` has no ``ArgSpec`` equivalent —
        all raise.
    ``retire_rule``
        ``{"hook_kind", "arg": str|null}`` — remove that hook from the
        tool (``defaults_when_missing`` entries for ``arg``, or all when
        ``null``; ``strip_unknown_args`` → ``False``; ``prompt_correction``
        → ``None``). Nothing to retire raises.
    ``add_prompt_correction`` / ``ESCALATE`` / anything else
        always raise.

    The input catalog is unchanged; ``result.fingerprint()`` moves iff a
    field changed.
    """
    target_tool = patch.get("target_tool")
    if not isinstance(target_tool, str) or not target_tool:
        raise PatchNotApplicableError("patch has no 'target_tool'")
    tool = catalog.get_tool(target_tool)
    if tool is None:
        raise PatchNotApplicableError(
            f"unknown target_tool {target_tool!r} in catalog {catalog.name!r}"
        )
    patch_catalog_id = patch.get("catalog_id")
    if patch_catalog_id not in (None, "") and patch_catalog_id != catalog.name:
        raise PatchNotApplicableError(
            f"patch is for catalog {patch_catalog_id!r}, not {catalog.name!r}"
        )

    operation = patch.get("operation")
    payload = patch.get("payload") or {}
    if not isinstance(payload, Mapping):
        raise PatchNotApplicableError("patch 'payload' must be a mapping")

    if operation == "add_alias":
        new_tool = _apply_add_alias(tool, payload)
    elif operation == "set_default":
        new_tool = _apply_set_default(tool, payload)
    elif operation == "enable_strip_unknown_args":
        new_tool = _apply_enable_strip_unknown_args(tool, payload)
    elif operation == "extend_argspec":
        new_tool = _apply_extend_argspec(tool, payload)
    elif operation == "retire_rule":
        new_tool = _apply_retire_rule(tool, payload)
    elif operation in _NEVER_APPLICABLE:
        raise PatchNotApplicableError(
            f"operation {operation!r} is not mechanically applicable "
            "(requires a reviewed source edit)"
        )
    else:
        raise PatchNotApplicableError(f"unknown operation {operation!r}")

    return _replace_tool(catalog, new_tool)


def _apply_add_alias(tool: ToolSpec, payload: Mapping[str, Any]) -> ToolSpec:
    arg_name = _require_str(payload, "arg", "add_alias")
    spec = tool.get_arg(arg_name)
    if spec is None:
        raise PatchNotApplicableError(
            f"add_alias: {tool.name} has no arg {arg_name!r}"
        )
    if isinstance(spec, EnumArg):
        expected_kind = "enum"
    elif isinstance(spec, StringArg):
        expected_kind = "string"
    else:
        raise PatchNotApplicableError(
            f"add_alias: {tool.name}.{arg_name} is {spec.kind!r}, "
            "aliases exist only on enum/string args"
        )
    kind = payload.get("kind")
    if kind is not None and kind != expected_kind:
        raise PatchNotApplicableError(
            f"add_alias: payload kind {kind!r} does not match "
            f"{tool.name}.{arg_name} ({expected_kind!r})"
        )
    aliases = payload.get("aliases")
    if not isinstance(aliases, Mapping) or not aliases:
        raise PatchNotApplicableError("add_alias: payload 'aliases' must be a non-empty mapping")

    existing = dict(spec.aliases)
    merged = dict(existing)
    for raw_key, gold in aliases.items():
        if not isinstance(raw_key, str) or not isinstance(gold, str):
            raise PatchNotApplicableError(
                f"add_alias: alias entries must be str → str, got {raw_key!r}: {gold!r}"
            )
        # Match the lookup normalisation of ``_normalize_enum`` /
        # ``_normalize_string`` (``raw.strip().lower()``) so the alias can
        # actually fire; rules.py already emits keys in this form.
        key = raw_key.strip().lower()
        if not key:
            raise PatchNotApplicableError("add_alias: empty alias key")
        if isinstance(spec, EnumArg) and gold not in spec.values:
            raise PatchNotApplicableError(
                f"add_alias: gold {gold!r} is not one of {tool.name}.{arg_name} "
                f"values {list(spec.values)!r}"
            )
        previous = existing.get(key)
        if previous is not None and previous != gold:
            raise PatchNotApplicableError(
                f"add_alias: {tool.name}.{arg_name} already maps {key!r} → "
                f"{previous!r}, refusing to remap to {gold!r}"
            )
        merged[key] = gold

    if merged == existing:
        return tool  # idempotent no-op
    new_spec = replace(spec, aliases=merged)
    return _replace_arg(tool, arg_name, new_spec)


def _apply_set_default(tool: ToolSpec, payload: Mapping[str, Any]) -> ToolSpec:
    arg_name = _require_str(payload, "arg", "set_default")
    if tool.get_arg(arg_name) is None:
        raise PatchNotApplicableError(
            f"set_default: {tool.name} has no arg {arg_name!r}"
        )
    if "default" not in payload:
        raise PatchNotApplicableError("set_default: payload has no 'default'")
    if any(rule[0] == arg_name for rule in tool.defaults_when_missing):
        raise PatchNotApplicableError(
            f"set_default: {tool.name} already has a defaults_when_missing rule "
            f"for {arg_name!r} (retire it first)"
        )
    hint = payload.get("predicate_hint") or {}
    if not isinstance(hint, Mapping):
        raise PatchNotApplicableError("set_default: 'predicate_hint' must be a mapping")
    raw_req = hint.get("requires_args") or ()
    if isinstance(raw_req, (str, bytes)) or not isinstance(raw_req, Iterable):
        raise PatchNotApplicableError(
            "set_default: 'predicate_hint.requires_args' must be a list of arg names"
        )
    req: tuple[str, ...] = tuple(str(name) for name in raw_req)
    rule: DefaultRule = (arg_name, payload["default"], _requires_all(req))
    return replace(
        tool, defaults_when_missing=tuple(tool.defaults_when_missing) + (rule,),
    )


def _requires_all(req: tuple[str, ...]):
    """Build the ``set_default`` predicate: fire iff every name in ``req``
    is present in ``args``. Closes over the immutable ``req`` tuple only, so
    equal patches yield behaviourally equal catalogs; an empty ``req`` is
    always-True (the plain ``Catalog.validate_call`` predicate gate).
    """

    def predicate(args: Mapping[str, Any], req: tuple[str, ...] = req) -> bool:
        return all(key in args for key in req)

    return predicate


def _apply_enable_strip_unknown_args(
    tool: ToolSpec, payload: Mapping[str, Any],
) -> ToolSpec:
    if payload.get("strip_unknown_args", True) is False:
        raise PatchNotApplicableError(
            "enable_strip_unknown_args: payload says strip_unknown_args=false"
        )
    if tool.strip_unknown_args:
        return tool  # idempotent no-op
    return replace(tool, strip_unknown_args=True)


def _apply_extend_argspec(tool: ToolSpec, payload: Mapping[str, Any]) -> ToolSpec:
    transform = payload.get("transform")
    if transform != "percent":
        raise PatchNotApplicableError(
            "extend_argspec: only transform='percent' on an IntArg is mechanically "
            f"applicable (got transform={transform!r}, "
            f"spec_hint={payload.get('spec_hint')!r})"
        )
    arg_name = _require_str(payload, "arg", "extend_argspec")
    spec = tool.get_arg(arg_name)
    if not isinstance(spec, IntArg):
        raise PatchNotApplicableError(
            f"extend_argspec: {tool.name}.{arg_name} is not an IntArg "
            f"({None if spec is None else spec.kind!r})"
        )
    if spec.allow_percent:
        return tool  # idempotent no-op
    return _replace_arg(tool, arg_name, replace(spec, allow_percent=True))


def _apply_retire_rule(tool: ToolSpec, payload: Mapping[str, Any]) -> ToolSpec:
    hook_kind = payload.get("hook_kind")
    if hook_kind not in _HOOK_KIND_SET:
        raise PatchNotApplicableError(
            f"retire_rule: unknown hook_kind {hook_kind!r}; expected one of {ALL_HOOK_KINDS}"
        )
    if hook_kind == "defaults_when_missing":
        arg_name = payload.get("arg")
        if arg_name is None:
            kept: tuple[DefaultRule, ...] = ()
        else:
            kept = tuple(
                rule for rule in tool.defaults_when_missing if rule[0] != arg_name
            )
        if len(kept) == len(tool.defaults_when_missing):
            raise PatchNotApplicableError(
                f"retire_rule: {tool.name} has no defaults_when_missing rule"
                + ("" if arg_name is None else f" for {arg_name!r}")
            )
        return replace(tool, defaults_when_missing=kept)
    if hook_kind == "strip_unknown_args":
        if not tool.strip_unknown_args:
            raise PatchNotApplicableError(
                f"retire_rule: {tool.name}.strip_unknown_args is already False"
            )
        return replace(tool, strip_unknown_args=False)
    # prompt_correction
    if tool.prompt_correction is None:
        raise PatchNotApplicableError(
            f"retire_rule: {tool.name} has no prompt_correction"
        )
    return replace(tool, prompt_correction=None)


# ---------------------------------------------------------------------------
# strip_hooks
# ---------------------------------------------------------------------------


def strip_hooks(catalog: Catalog, kinds: Iterable[str]) -> Catalog:
    """Return a copy of ``catalog`` with the named hook kinds removed from
    **every** tool.

    * ``"defaults_when_missing"`` → ``()`` on each tool;
    * ``"strip_unknown_args"`` → ``False`` on each tool **and**
      ``Catalog.default_strip_unknown_args`` → ``False``;
    * ``"prompt_correction"`` → ``None`` on each tool.

    ``custom_validator`` is never stripped. A kind outside
    :data:`ALL_HOOK_KINDS` raises ``ValueError``. Pass ``ALL_HOOK_KINDS`` to
    obtain the F⁰ (model-alone) validator.
    """
    kind_set = {kinds} if isinstance(kinds, str) else set(kinds)
    unknown = kind_set - _HOOK_KIND_SET
    if unknown:
        raise ValueError(
            f"strip_hooks: unknown hook kind(s) {sorted(unknown)!r}; "
            f"expected a subset of {ALL_HOOK_KINDS}"
        )
    strip_defaults = "defaults_when_missing" in kind_set
    strip_unknown = "strip_unknown_args" in kind_set
    strip_prompt = "prompt_correction" in kind_set

    tools = tuple(
        replace(
            tool,
            defaults_when_missing=() if strip_defaults else tool.defaults_when_missing,
            strip_unknown_args=False if strip_unknown else tool.strip_unknown_args,
            prompt_correction=None if strip_prompt else tool.prompt_correction,
        )
        for tool in catalog.tools
    )
    return replace(
        catalog,
        tools=tools,
        default_strip_unknown_args=(
            False if strip_unknown else catalog.default_strip_unknown_args
        ),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _require_str(payload: Mapping[str, Any], key: str, operation: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PatchNotApplicableError(f"{operation}: payload {key!r} must be a non-empty string")
    return value


def _replace_arg(tool: ToolSpec, arg_name: str, new_spec: Any) -> ToolSpec:
    args = tuple(
        (name, new_spec if name == arg_name else spec) for name, spec in tool.args
    )
    return replace(tool, args=args)


def _replace_tool(catalog: Catalog, new_tool: ToolSpec) -> Catalog:
    tools = tuple(new_tool if tool.name == new_tool.name else tool for tool in catalog.tools)
    return replace(catalog, tools=tools)
