"""Deprecated compatibility shim.

Built-in catalogs moved to `ganglion.contract.builtins`. This module
re-exports `get_catalog` / `TIERS` and emits ``DeprecationWarning`` once
per process. It will be removed in Batch 6 of `docs/redesign_plan.md`.
Update imports to ``from ganglion.contract.builtins import …``.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "ganglion.schema is deprecated; use ganglion.contract.builtins instead",
    DeprecationWarning,
    stacklevel=2,
)

from ganglion.contract.builtins import (  # noqa: E402,F401
    TIERS,
    get_catalog,
    home_iot,
    iot_light,
    smart_home,
)

__all__ = ["TIERS", "get_catalog", "home_iot", "iot_light", "smart_home"]
