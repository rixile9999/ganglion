"""Built-in Catalogs shipped with Ganglion.

The three IoT tiers preserve the M2 scaling-experiment ratios (5 / 20 / 50
tools); see `docs/tasks/benchmark_iot.md` for the consumer side.
`home_assistant_4` is a projection of `iot_light_5` onto Home Assistant's
Assist API tool shape and is *not* a point on the scaling curve; see
`docs/tasks/contract_tier_home_assistant.md`.
"""
from __future__ import annotations

from ganglion.contract.catalog import Catalog
from ganglion.contract.builtins import home_assistant, home_iot, iot_light, smart_home

SCALING_TIERS: tuple[str, ...] = ("iot_light_5", "home_iot_20", "smart_home_50")

TIERS: dict[str, Catalog] = {
    "iot_light_5": iot_light.CATALOG,
    "home_iot_20": home_iot.CATALOG,
    "smart_home_50": smart_home.CATALOG,
    "home_assistant_4": home_assistant.CATALOG,
}


def get_catalog(tier: str) -> Catalog:
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}; available: {sorted(TIERS)}")
    return TIERS[tier]


__all__ = [
    "SCALING_TIERS",
    "TIERS",
    "get_catalog",
    "home_assistant",
    "home_iot",
    "iot_light",
    "smart_home",
]
