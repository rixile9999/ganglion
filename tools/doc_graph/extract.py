"""Extract a graph manifest from docs/tasks/*.md for the viewer.

Reads each non-legacy task doc and pulls:
  - title, kind (atomic|composite), module (contract|lm|analyzer|benchmark|factory)
  - one-line summary (first paragraph after the title)
  - Role (## Role section text)
  - in-scope / out-of-scope bullet lists (from ## Scope)
  - events consumed / emitted (from ## Contract's event clause)
  - wikilink references ([[name]]) appearing anywhere in the body
  - source path and line count

Cross-references emit↔consume to build event edges. Wikilinks become a second
edge kind. Module groupings come from the filename prefix and are cross-checked
against the README TOC.

Outputs:
  - tools/doc_graph/viewer/data/manifest.json
  - tools/doc_graph/viewer/docs/<doc_id>.md  (mirrored markdown for in-page fetch)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "docs" / "tasks"
OUT_DIR = Path(__file__).resolve().parent / "viewer" / "data"
DOCS_MIRROR_DIR = Path(__file__).resolve().parent / "viewer" / "docs"

MODULE_PREFIX = {
    "contract_": "contract",
    "lm_": "lm",
    "analyzer_": "analyzer",
    "benchmark_": "benchmark",
    "factory_": "factory",
}

MODULE_LABEL = {
    "contract": "Module 3 — contract/",
    "lm": "Module 1 — lm/",
    "analyzer": "Module 2 — analyzer/",
    "benchmark": "Consumers — benchmarks/",
    "factory": "Composites",
}

# event names look like  a.b.c  or  a.b.c.d  — lowercase, dot-separated, underscores ok
EVENT_RE = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z0-9_]+){2,})\b")
WIKILINK_RE = re.compile(r"\[\[([a-z][a-z0-9_]+)\]\]")
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$", re.MULTILINE)


@dataclass
class DocNode:
    id: str
    title: str
    kind: str  # "atomic" | "composite"
    module: str
    summary: str
    role: str
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    events_consumed: list[str] = field(default_factory=list)
    events_emitted: list[str] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    file: str = ""
    lines: int = 0


def split_sections(body: str) -> dict[str, str]:
    """Return {section_heading: section_body} from a markdown body."""
    matches = list(SECTION_RE.finditer(body))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[m.group(1).strip()] = body[start:end].strip()
    return out


def extract_title_and_summary(body: str) -> tuple[str, str, str]:
    """Return (title, kind, first-paragraph-summary). kind ∈ {atomic, composite}."""
    m = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    if not m:
        return "", "atomic", ""
    raw_title = m.group(1).strip()
    kind = "composite" if "(composite)" in raw_title.lower() else "atomic"
    title = re.sub(r"\s*\(composite\)\s*", "", raw_title, flags=re.IGNORECASE).strip()
    # first non-empty paragraph after the title
    after = body[m.end():].lstrip("\n")
    summary = ""
    for para in after.split("\n\n"):
        para = para.strip()
        if not para or para.startswith("##"):
            continue
        summary = " ".join(para.splitlines()).strip()
        break
    # strip wikilinks/markdown emphasis for cleanliness
    summary = re.sub(r"`([^`]+)`", r"\1", summary)
    summary = re.sub(r"\*\*([^*]+)\*\*", r"\1", summary)
    return title, kind, summary


def extract_scope(scope_body: str) -> tuple[list[str], list[str]]:
    """Pull bullets under **in-scope** and **out-of-scope** headers."""
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    current: list[str] | None = None
    for raw_line in scope_body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip().lower()
        if stripped.startswith("- **in-scope**") or stripped.startswith("**in-scope**"):
            current = in_scope
            continue
        if stripped.startswith("- **out-of-scope**") or stripped.startswith("**out-of-scope**"):
            current = out_of_scope
            continue
        if stripped.startswith("- **on violation**") or stripped.startswith("**on violation**"):
            current = None
            continue
        if current is None:
            continue
        # only collect indented sub-bullets (the top-level bullet introduced the section)
        m = re.match(r"^\s+[-*]\s+(.*)$", line)
        if m:
            text = m.group(1).strip()
            text = re.sub(r"`([^`]+)`", r"\1", text)
            text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
            current.append(text)
    return in_scope, out_of_scope


def _harvest(line_low: str, bucket: str, consumed: list[str], emitted: list[str]) -> None:
    target = emitted if bucket == "emit" else consumed
    for ev in EVENT_RE.findall(line_low):
        if ev not in target:
            target.append(ev)


# bullet headers like `- **event**:` or `**event**:` — the canonical contract sub-bullets
SUBSECTION_RE = re.compile(r"^\s*-?\s*\*\*([a-z_-]+)\*\*\s*:?\s*(.*)$")


def extract_events(contract_body: str) -> tuple[list[str], list[str]]:
    """Pull event names mentioned next to 'consume' / 'emit' inside the **event** bullet.

    The Contract section is laid out as nested bullets:
        - **in**: …
        - **out**: …
        - **event**:
            consume: X, Y
            emit: Z
        - **failure**: …
        - **success**: …

    We only collect events while we are *inside* the `**event**` bullet — that
    keeps tokens like `factory.pipeline.aborted` that appear in the failure
    clause out of the consume/emit lists.
    """
    consumed: list[str] = []
    emitted: list[str] = []
    in_event_section = False
    bucket: str | None = None
    for raw_line in contract_body.splitlines():
        line = raw_line.rstrip()
        low = line.lower()
        m = SUBSECTION_RE.match(low)
        if m:
            name = m.group(1)
            trailing = m.group(2)
            if name == "event":
                in_event_section = True
                bucket = None
                # honour inline content on the same line, e.g. "**event**: consumes X; emits Y."
                if trailing:
                    if "consum" in trailing and "emit" in trailing:
                        head, _, tail = trailing.partition("emit")
                        _harvest(head, "consume", consumed, emitted)
                        _harvest(tail, "emit", consumed, emitted)
                    elif "consum" in trailing:
                        _harvest(trailing, "consume", consumed, emitted)
                    elif "emit" in trailing:
                        _harvest(trailing, "emit", consumed, emitted)
                continue
            # any other bullet header exits the event section
            in_event_section = False
            bucket = None
            continue
        if not in_event_section:
            continue
        if not line.strip():
            # blank line inside the event subsection — stay inside, but reset bucket
            bucket = None
            continue
        if "consum" in low and "emit" in low:
            head, _, tail = low.partition("emit")
            _harvest(head, "consume", consumed, emitted)
            _harvest(tail, "emit", consumed, emitted)
            bucket = None
            continue
        if "consum" in low:
            bucket = "consume"
            _harvest(low, "consume", consumed, emitted)
            continue
        if re.search(r"\bemit", low):
            bucket = "emit"
            _harvest(low, "emit", consumed, emitted)
            continue
        if bucket is not None:
            _harvest(low, bucket, consumed, emitted)
    return consumed, emitted


def parse_doc(path: Path) -> DocNode:
    body = path.read_text(encoding="utf-8")
    doc_id = path.stem
    prefix_key = next((p for p in MODULE_PREFIX if doc_id.startswith(p)), None)
    module = MODULE_PREFIX.get(prefix_key, "other") if prefix_key else "other"
    title, kind, summary = extract_title_and_summary(body)
    sections = split_sections(body)
    role = sections.get("Role", "").strip()
    in_scope, out_of_scope = extract_scope(sections.get("Scope", ""))
    consumed, emitted = extract_events(sections.get("Contract", ""))
    wikilinks = sorted(set(WIKILINK_RE.findall(body)) - {doc_id})
    return DocNode(
        id=doc_id,
        title=title or doc_id,
        kind=kind,
        module=module,
        summary=summary,
        role=role,
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        events_consumed=consumed,
        events_emitted=emitted,
        wikilinks=wikilinks,
        file=str(path.relative_to(REPO_ROOT)),
        lines=body.count("\n") + 1,
    )


def build_manifest(nodes: list[DocNode]) -> dict:
    by_id = {n.id: n for n in nodes}

    emitters: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    for n in nodes:
        for ev in n.events_emitted:
            emitters.setdefault(ev, []).append(n.id)
        for ev in n.events_consumed:
            consumers.setdefault(ev, []).append(n.id)

    event_edges: list[dict] = []
    dangling: list[dict] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for ev in sorted(set(emitters) | set(consumers)):
        srcs = emitters.get(ev, [])
        tgts = consumers.get(ev, [])
        if srcs and tgts:
            for s in srcs:
                for t in tgts:
                    if s == t:
                        continue
                    key = (s, t, ev)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    event_edges.append({
                        "source": s,
                        "target": t,
                        "event": ev,
                        "kind": "event",
                    })
        elif srcs and not tgts:
            dangling.append({"event": ev, "emitters": srcs, "consumers": []})
        elif tgts and not srcs:
            dangling.append({"event": ev, "emitters": [], "consumers": tgts})

    wikilink_edges: list[dict] = []
    seen_w: set[tuple[str, str]] = set()
    for n in nodes:
        for target in n.wikilinks:
            if target not in by_id:
                continue
            key = (n.id, target)
            if key in seen_w:
                continue
            seen_w.add(key)
            wikilink_edges.append({"source": n.id, "target": target, "kind": "wikilink"})

    modules: list[dict] = []
    for mod_id, label in MODULE_LABEL.items():
        children = sorted([n.id for n in nodes if n.module == mod_id])
        if not children:
            continue
        modules.append({"id": mod_id, "label": label, "nodes": children})

    return {
        "generated_by": "tools/doc_graph/extract.py",
        "source_dir": str(TASKS_DIR.relative_to(REPO_ROOT)),
        "modules": modules,
        "nodes": [n.__dict__ for n in nodes],
        "event_edges": event_edges,
        "wikilink_edges": wikilink_edges,
        "dangling_events": dangling,
    }


def iter_task_docs() -> Iterable[Path]:
    for p in sorted(TASKS_DIR.glob("*.md")):
        if p.name == "README.md":
            continue
        yield p


def main(argv: list[str] | None = None) -> int:
    docs = list(iter_task_docs())
    if not docs:
        print(f"no task docs found under {TASKS_DIR}", file=sys.stderr)
        return 1
    nodes = [parse_doc(p) for p in docs]
    manifest = build_manifest(nodes)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_MIRROR_DIR.mkdir(parents=True, exist_ok=True)

    out_path = OUT_DIR / "manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    for p in docs:
        (DOCS_MIRROR_DIR / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

    # Stderr summary so the user can spot mis-classification immediately.
    by_mod: dict[str, int] = {}
    for n in nodes:
        by_mod[n.module] = by_mod.get(n.module, 0) + 1
    print(f"wrote {out_path}", file=sys.stderr)
    print(f"  nodes        : {len(nodes)}", file=sys.stderr)
    print(f"  event edges  : {len(manifest['event_edges'])}", file=sys.stderr)
    print(f"  wikilink edges: {len(manifest['wikilink_edges'])}", file=sys.stderr)
    print(f"  modules      : {by_mod}", file=sys.stderr)
    if manifest["dangling_events"]:
        print(f"  dangling events: {len(manifest['dangling_events'])} (see manifest.json)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
