"""Terminal rendering for ``ganglion`` ([[cli_operator]] §Contract.out).

:class:`Output` is the one place a payload becomes bytes on stdout: ``--json``
prints the route payload **verbatim** so scripts bind to the console's shapes
rather than to this package, ``--jsonl`` prints one row per line, and the
default mode calls the command's own renderer. Everything else here is pure
formatting over JSON-able values — no file reads, no route calls.

Widths are counted with ``unicodedata.east_asian_width`` because every IoT
tier prompt is Korean; ``len()`` would misalign every table in the CLI.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "Column",
    "MODES",
    "NONE",
    "Output",
    "bar",
    "fmt",
    "kv",
    "pad",
    "plan_lines",
    "table",
    "truncate",
    "width",
]

MODES: tuple[str, ...] = ("human", "json", "jsonl")

#: What a ``None`` cell renders as. One glyph, so it never widens a column.
NONE = "-"

_WIDE = frozenset({"W", "F"})


def width(text: str) -> int:
    """Display columns of ``text``; CJK / fullwidth characters count as 2."""
    total = 0
    for char in str(text):
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in _WIDE else 1
    return total


def pad(text: str, columns: int, align: str = "<") -> str:
    """Pad ``text`` to ``columns`` display columns (``<`` left, ``>`` right)."""
    text = str(text)
    fill = max(0, columns - width(text))
    if align == ">":
        return " " * fill + text
    return text + " " * fill


def fmt(value: Any) -> str:
    """One cell: ``None`` → ``-``, bools → yes/no, floats trimmed, rest JSON."""
    if value is None:
        return NONE
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def truncate(text: str, columns: int | None) -> str:
    """Collapse whitespace and clip to ``columns`` display columns with ``…``."""
    text = " ".join(str(text).split())
    if not columns or columns <= 1 or width(text) <= columns:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        step = width(char)
        if used + step > columns - 1:
            break
        out.append(char)
        used += step
    return "".join(out) + "…"


@dataclass(frozen=True)
class Column:
    """One table column. ``get`` is a mapping key, a callable, or ``None`` (the row)."""

    header: str
    get: str | Callable[[Any], Any] | None = None
    align: str = "<"
    max_width: int | None = None

    def cell(self, row: Any) -> str:
        if callable(self.get):
            value = self.get(row)
        elif self.get is None:
            value = row
        elif isinstance(row, Mapping):
            value = row.get(self.get)
        else:
            value = getattr(row, self.get, None)
        return truncate(fmt(value), self.max_width)


def table(rows: Sequence[Any], columns: Sequence[Column], *, empty: str = "(none)") -> str:
    """Header + rule + rows, two-space gutters, widths by display columns."""
    if not rows:
        return empty
    cells = [[col.cell(row) for col in columns] for row in rows]
    widths = [
        max([width(col.header)] + [width(line[i]) for line in cells])
        for i, col in enumerate(columns)
    ]
    out = ["  ".join(pad(col.header, widths[i], col.align) for i, col in enumerate(columns)).rstrip()]
    out.append("  ".join("-" * widths[i] for i in range(len(columns))))
    for line in cells:
        out.append("  ".join(pad(line[i], widths[i], columns[i].align) for i in range(len(columns))).rstrip())
    return "\n".join(out)


def kv(pairs: Iterable[tuple[str, Any]], *, indent: int = 0) -> str:
    """Aligned ``key  value`` block; a ``(None, None)`` pair renders a blank line."""
    items = [(str(k), fmt(v)) for k, v in pairs if k is not None]
    if not items:
        return ""
    keyw = max(width(k) for k, _ in items)
    lead = " " * indent
    return "\n".join(f"{lead}{pad(k, keyw)}  {v}".rstrip() for k, v in items)


def plan_lines(plan: Mapping[str, Any] | None, *, indent: int = 2) -> list[str]:
    """``1. set_light(room=living, state=on)`` per call of an Action IR plan.

    ``calls`` entries are ``{"action", "args"}`` — the DSL's own key names
    ([[contract_catalog]]), not OpenAI's ``{"name", "arguments"}``.
    """
    lead = " " * indent
    if plan is None:
        return [f"{lead}(no plan)"]
    calls = plan.get("calls") if isinstance(plan, Mapping) else None
    if not isinstance(calls, (list, tuple)):
        return [f"{lead}(no calls)"]
    if not calls:
        return [f'{lead}(no calls — {{"calls": []}} abstention)']
    out: list[str] = []
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, Mapping):
            out.append(f"{lead}{index}. {fmt(call)}")
            continue
        action = str(call.get("action", "?"))
        args = call.get("args")
        rendered = ""
        if isinstance(args, Mapping) and args:
            rendered = ", ".join(f"{name}={fmt(args[name])}" for name in sorted(args))
        out.append(f"{lead}{index}. {action}({rendered})")
    return out


def bar(count: Any, total: Any, *, columns: int = 18) -> str:
    """``█████░░░░░`` for ``count/total`` — the histogram / transition renderer."""
    try:
        n, d = float(count), float(total)
    except (TypeError, ValueError):
        return ""
    if d <= 0 or n < 0:
        return ""
    filled = min(columns, int(round(columns * n / d)))
    return "█" * filled + "░" * (columns - filled)


def _rows(payload: Any, rows_key: str | None) -> list[Any]:
    if rows_key and isinstance(payload, Mapping):
        value = payload.get(rows_key)
        return list(value) if isinstance(value, (list, tuple)) else []
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return [payload]


class Output:
    """stdout for one invocation: ``human`` | ``json`` | ``jsonl``."""

    def __init__(self, mode: str = "human", stream: Any = None) -> None:
        self.mode = mode if mode in MODES else "human"
        self.stream = sys.stdout if stream is None else stream

    @property
    def human(self) -> bool:
        return self.mode == "human"

    def write(self, text: Any = "") -> None:
        """One line (or a multi-line block) of human output."""
        print("" if text is None else str(text), file=self.stream)

    def blank(self) -> None:
        print("", file=self.stream)

    def json(self, payload: Any) -> None:
        """The payload verbatim — the machine contract, never reshaped."""
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), file=self.stream)

    def emit(
        self,
        payload: Any,
        render: Callable[[Any], Any] | None = None,
        *,
        rows_key: str | None = None,
    ) -> int:
        """Print ``payload`` in this mode; returns the command's exit status (0)."""
        if self.mode == "json":
            self.json(payload)
            return 0
        if self.mode == "jsonl":
            for row in _rows(payload, rows_key):
                print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str), file=self.stream)
            return 0
        if render is None:
            self.json(payload)
            return 0
        result = render(payload)
        return 0 if result is None else int(result)
