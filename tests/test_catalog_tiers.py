from rlm_poc.dsl.tool_spec import DSLValidationError
from rlm_poc.schema import TIERS, get_catalog


def test_tier_tool_counts() -> None:
    assert len(get_catalog("iot_light_5").tools) == 5
    assert len(get_catalog("home_iot_20").tools) == 20
    assert len(get_catalog("smart_home_50").tools) == 50


def test_tier_dsl_renders_grow_with_size() -> None:
    sizes = [
        len(get_catalog(name).render_json_dsl()) for name in TIERS
    ]
    # Each subsequent tier strictly larger.
    assert sizes[0] < sizes[1] < sizes[2]


def test_tier_native_schema_grows_faster_than_dsl() -> None:
    # Native schema/DSL ratio should widen as tool count grows.
    ratios = []
    for name in ("iot_light_5", "home_iot_20", "smart_home_50"):
        catalog = get_catalog(name)
        dsl = len(catalog.render_json_dsl())
        native = sum(len(str(tool)) for tool in catalog.render_openai_tools())
        ratios.append(native / dsl)
    assert ratios[0] < ratios[1] < ratios[2]


def test_unknown_action_rejected_in_tier_5() -> None:
    catalog = get_catalog("iot_light_5")
    payload = {"calls": [{"action": "set_thermostat", "args": {"room": "living"}}]}
    try:
        catalog.validate(payload)
    except DSLValidationError:
        return
    raise AssertionError("expected DSLValidationError for unknown action")


def test_thermostat_supported_in_tier_20() -> None:
    catalog = get_catalog("home_iot_20")
    plan = catalog.validate(
        {
            "calls": [
                {
                    "action": "set_thermostat",
                    "args": {"room": "거실", "temperature": 22, "mode": "heat"},
                }
            ]
        }
    )
    assert plan.calls[0].action == "set_thermostat"
    assert plan.calls[0].args == {"room": "living", "temperature": 22, "mode": "heat"}


def test_oven_temperature_range_enforced_in_tier_50() -> None:
    catalog = get_catalog("smart_home_50")
    bad = {"calls": [{"action": "start_oven", "args": {"mode": "bake", "temperature": 1000}}]}
    try:
        catalog.validate(bad)
    except DSLValidationError:
        return
    raise AssertionError("expected DSLValidationError for out-of-range temperature")
