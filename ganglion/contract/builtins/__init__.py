"""Built-in Catalogs shipped with Ganglion.

The three tiers preserve the M2 scaling-experiment ratios (5 / 20 / 50 tools);
see `docs/tasks/benchmark_iot.md` for the consumer side.
"""
from __future__ import annotations

from ganglion.contract.catalog import Catalog
from ganglion.contract.builtins import home_iot, iot_light, smart_home

TIERS: dict[str, Catalog] = {
    "iot_light_5": iot_light.CATALOG,
    "home_iot_20": home_iot.CATALOG,
    "smart_home_50": smart_home.CATALOG,
}


def get_catalog(tier: str) -> Catalog:
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}; available: {sorted(TIERS)}")
    return TIERS[tier]


__all__ = ["TIERS", "get_catalog", "home_iot", "iot_light", "smart_home"]
