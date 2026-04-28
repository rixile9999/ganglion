"""Tool and DSL schema definitions."""

from __future__ import annotations

from rlm_poc.dsl.catalog import Catalog
from rlm_poc.schema import home_iot, iot_light, smart_home

TIERS: dict[str, Catalog] = {
    "iot_light_5": iot_light.CATALOG,
    "home_iot_20": home_iot.CATALOG,
    "smart_home_50": smart_home.CATALOG,
}


def get_catalog(tier: str) -> Catalog:
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}; available: {sorted(TIERS)}")
    return TIERS[tier]
