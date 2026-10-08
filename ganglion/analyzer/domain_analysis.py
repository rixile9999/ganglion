"""Dispatch typed prediction analysis through internally installed adapters.

Application data names a domain, never a Python import path. This interface is
independent of the existing tool-calling Trace/Catalog failure taxonomy.
"""
from __future__ import annotations

from collections.abc import Callable
import re

from ganglion.domains.pii.analysis import analyze as _analyze_pii

_ADAPTERS: dict[str, Callable] = {"pii-text": _analyze_pii}


def register_analyzer(domain: str, adapter: Callable, *, replace: bool = False) -> None:
    """Install a trusted Python adapter; registration is not an HTTP capability."""
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", domain) or not callable(adapter):
        raise ValueError("a domain name and callable analyzer are required")
    if domain in _ADAPTERS and not replace:
        raise ValueError("domain analyzer is already installed")
    _ADAPTERS[domain] = adapter


def analyze_predictions(domain, raw_spans, final_spans, gold_spans, *,
                        coordinate="character", gold_complete=True, document_length=None):
    """Analyze typed predictions without source text or an implicit gold label.

    An adapter receives the same arguments and returns domain-specific diagnostics.
    The legacy tool-calling taxonomy continues to use its existing API until a
    compatible adapter is explicitly installed here.
    """
    if not isinstance(domain, str) or domain not in _ADAPTERS:
        raise ValueError("domain analyzer is not installed")
    result = _ADAPTERS[domain](raw_spans, final_spans, gold_spans,
                               coordinate=coordinate, gold_complete=gold_complete,
                               document_length=document_length)
    return {"domain": domain, **result}
