[← New tasks](./README.md) · General principle: [task_principle](../agent-forge/task_principle.md) · Siblings: [[contract_catalog]] · [[benchmark_iot]] · [[benchmark_selection]]

# contract_tier_home_assistant

A fourth built-in tier, `home_assistant_4`, that renders the **same
IoT-light intents** as `iot_light_5` in the shape of Home Assistant's
LLM-facing **Assist API** (Open Home Foundation). Purpose: make the Action-IR
vs native-schema comparison runnable against a tool surface that a real
device can execute — Home Assistant exposes exactly these tools over its MCP
Server integration (`/api/mcp/assist`, since HA 2025.2), so a physical test
bench (HA Green + Zigbee bulbs) can later replace the synthetic grader
without changing the catalog.

The tier is a *projection* of `iot_light_5`, not a new intent set. A
deterministic translator maps `iot_light_5` expected plans onto Assist calls,
and a derived dataset is generated from `examples/iot_light/dataset.jsonl`.
Intents that Home Assistant's Assist API cannot express are excluded, never
approximated.

Schema source (verified 2026-09-09 against `home-assistant/core` `dev`):

| HA tool | HA slot schema | Notes |
|---|---|---|
| `HassTurnOn` / `HassTurnOff` | `name, area, floor: str`; `domain: list[str]`; `device_class: list[str]`; all optional | targeting slots; `preferred_area_id/floor_id` are stripped before the LLM sees the schema |
| `HassLightSet` | targeting slots + `color: str` (CSS colour name → RGB), `temperature: positive int` (Kelvin), `brightness: int 0..100` | all optional; maps to `light.turn_on` with `rgb_color / color_temp_kelvin / brightness_pct` |
| `GetLiveContext` | no args | returns the exposed-entity state snapshot; replaces the older `HassGetState` for LLMs |

Since HA 2026.9 the LLM API prefixes tool names with the providing domain
(`intent__HassTurnOn`, `homeassistant__GetLiveContext`). The tier keeps the
unprefixed intent names as canonical and records the prefix rule as a
constant so a future MCP adapter can apply it at the boundary.

## Role

Provide the `home_assistant_4` Catalog, the `iot_light_5 → Assist` plan
translator, and the derived dataset generator, so the IoT benchmark can run
unchanged against a Home-Assistant-shaped tool surface.

## Scope

- **in-scope**:
  - `ganglion/contract/builtins/home_assistant.py` exporting `CATALOG`
    (`name="home_assistant_4"`), the four `ToolSpec`s above, and:
    - `AREA_ARG = EnumArg(values=ROOMS, aliases=ROOM_ALIASES, required=False)`
      — reuses the `iot_light` room vocabulary as HA *area* names so the
      Korean/English alias canonicalisation carries over.
    - `domain` as `EnumArg(values=("light",), required=False)` with a
      `defaults_when_missing` rule that fills `"light"` when any targeting
      slot is present — the catalog has one domain, so the value is
      unambiguous and both `{"area":"living"}` and
      `{"area":"living","domain":"light"}` normalise to the same `ToolCall`.
    - A `custom_validator` on the three targeting tools requiring at least
      one of `name / area / floor`. HA rejects an untargeted intent at
      execution time ("no entity matched"); the tier rejects it at parse
      time so exact-match does not reward empty calls.
    - `COLOR_TEMP_KELVIN = {"warm": 2700, "neutral": 4000, "cool": 6500}` —
      the enum→Kelvin projection. 6500 is the upper bound of HA's
      `color_temp` selector.
    - `TOOL_NAME_PREFIX = {"HassTurnOn": "intent", ...}` — the 2026.9 prefix
      rule, data only.
  - `translate_plan(plan: ActionPlan) -> ActionPlan | None` in the same
    module:
    - `set_light(room, state="off", …)` → `HassTurnOff{area, domain}`
    - `set_light(room, state="on")` with no brightness/color_temp →
      `HassTurnOn{area, domain}`
    - `set_light(room, state="on", brightness?, color_temp?)` →
      `HassLightSet{area, domain, brightness?, temperature?}` (single call;
      HA's `HassLightSet` itself issues `light.turn_on`)
    - `get_light_state(room)` and `list_devices()` → `GetLiveContext{}`
      (HA has no per-area state query for LLMs; the room is dropped and
      the loss is recorded in `translation_notes`)
    - `schedule_light(...)`, `create_scene(...)` → `None` (unsupported:
      Assist has no clock-time scheduling intent — `HassStartTimer` is
      duration-based — and no scene-creation intent)
  - `examples/home_assistant/generate_dataset.py` → deterministic
    `examples/home_assistant/dataset.jsonl`: for each `iot_light` row,
    `expected ← translate_plan(...)`; rows with `None` are skipped; ids are
    `ha-<n>` with `source_id` preserved. Row count is a function of the
    source dataset (currently 320 of 500).
  - Registration in `ganglion/contract/builtins/__init__.py:TIERS` (and
    `SCALING_TIERS` naming the three scaling tiers explicitly), the CLI
    picking `examples/home_assistant/dataset.jsonl` as the default
    `--dataset` when `--tier home_assistant_4` is selected
    (`benchmarks/iot/dataset.py:default_dataset_for`), and
    `load_dataset(..., catalog=)` so the derived rows are parsed against this
    tier rather than the `iot_light_5` default.
  - Tests: `tests/test_home_assistant_tier.py` (catalog validates every
    translated plan; translator coverage counts; derived dataset integrity;
    DSL render shorter than native render).
- **out-of-scope**:
  - Talking to a real Home Assistant instance (REST, WebSocket, or MCP
    `/api/mcp/assist`). That is the future `benchmark_home_assistant`
    adapter named in [[benchmark_selection]].
  - Any change to `iot_light_5` / `home_iot_20` / `smart_home_50` or to
    `examples/iot_light/dataset.jsonl` (SSOT, regenerate-only).
  - The M2 scaling series. `home_assistant_4` is not a fourth point on the
    5/20/50 curve; `tests/test_catalog_tiers.py` keeps its three-tier
    ordering assertions explicit.
  - Rule-based client support. `RuleBasedJSONDSLClient` is bound to
    `iot_light_5`; `--llm rules --tier home_assistant_4` is not a supported
    combination and is not made to work here.
  - Colour-name canonicalisation (`빨강 → red`). `color` is a plain optional
    string; no dataset row uses it.
  - Timer / scene emulation via `HassStartTimer.conversation_command` or
    `scene.create`. Explicitly rejected as non-deterministic (depends on
    wall-clock) or non-LLM-facing.
- **on violation**: if a translation needs a slot HA does not have, or a
  dataset row needs a tool outside the four above, **exclude the row** and
  count it — never add a synthetic tool to the tier. Adding a tool means the
  tier is no longer HA's surface and the comparison claim is void.

## Procedure

```
construct (module import):
    tools ← (HassTurnOn, HassTurnOff, HassLightSet, GetLiveContext)
    examples ← translate_plan over IOT_LIGHT_EXAMPLES, dropping None
    CATALOG ← Catalog(name="home_assistant_4", tools, examples, extra_rules)
    register in TIERS                              # → contract.catalog.published

derive dataset (python examples/home_assistant/generate_dataset.py):
    for row in examples/iot_light/dataset.jsonl:
        plan ← parse_json_dsl(row.expected)
        ha   ← translate_plan(plan)
        if ha is None: skipped[plan.calls[0].action] += 1 ; continue
        write {id: "ha-NNN", source_id: row.id, prompt: row.prompt, expected: ha}
    print skipped histogram to stderr

run (python -m ganglion.cli --llm qwen --tier home_assistant_4):
    catalog ← get_catalog("home_assistant_4")
    dataset ← examples/home_assistant/dataset.jsonl   # tier default
    → benchmark_iot runner unchanged

on unsupported source action in translate_plan: return None (caller decides).
on targeting-slot-less HassTurnOn/Off/LightSet: DSLValidationError.
```

## Contract

- **in**: `iot_light_5` `ActionPlan`s (from dataset rows or examples);
  model output `str | Mapping` at parse time.
- **out**:
  - `ganglion/contract/builtins/home_assistant.py` — `CATALOG`,
    `translate_plan`, `COLOR_TEMP_KELVIN`, `TOOL_NAME_PREFIX`.
  - `examples/home_assistant/dataset.jsonl` — derived, deterministic,
    checked in; regenerate via the script, never hand-edit.
  - `CATALOG.render_json_dsl()` / `render_openai_tools()` — the two
    renderings the benchmark compares.
- **event**:
  - emit `contract.catalog.published(catalog_id="home_assistant_4")` on
    registration.
  - consume nothing; `benchmark.iot.*` events are emitted by the runner,
    not this task.
- **failure**:
  - Translator sees an unknown source action → `None` (not an exception);
    the generator counts and skips.
  - Model emits `HassTurnOn {}` → `DSLValidationError("… requires name, area or floor")`.
  - `temperature` outside 2000..6500 → `DSLValidationError` from `IntArg`.
  - Source dataset missing → generator `FileNotFoundError`; no partial
    output file.
- **success**:
  - `pytest tests/test_home_assistant_tier.py` green.
  - `python examples/home_assistant/generate_dataset.py` is idempotent
    (`git diff --quiet examples/home_assistant/dataset.jsonl` after a rerun).
  - Every row of the derived dataset round-trips through
    `CATALOG.parse_json_dsl(row["expected"])`.

## Observation

- `translation_coverage` = derived rows ÷ source rows (currently 320/500 =
  0.64). Drops only if the source dataset adds unsupported actions.
- `skipped_by_action` = histogram printed by the generator
  (`schedule_light: 140, create_scene: 40` today).
- `dsl_render_chars` / `openai_render_chars` for `home_assistant_4`, for the
  cross-tier table in [[benchmark_iot]] — reported alongside, not inside,
  the 5/20/50 series.

Wikilinks: [[contract_catalog]], [[benchmark_iot]], [[benchmark_selection]],
[[contract_null_action]].
