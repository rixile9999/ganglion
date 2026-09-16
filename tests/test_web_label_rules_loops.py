"""Static guards for the three operator pages driven by ``/api/*``.

Owner: the ``web-b`` half of the console work — ``web/traces.html`` (the label
queue), ``web/rules.html`` (the rule lifecycle) and ``web/loops.html`` (the
per-iteration loop log), specified by [[console_operator]]
(docs/tasks/console_operator.md §Scope, "web pages").

The behaviour of these pages lives in a browser and is exercised against a
running ``python -m ganglion.console serve``; what a text-only test can hold
still is asserted here:

* the pages load **only** the checked-in stylesheet and ``assets/api.js`` — no
  build step, no CDN, no second copy of the style vocabulary, no mock
  ``data.js``;
* every class their static markup uses is declared in
  ``web/assets/style.css``, in that page's own small ``<style>`` block or by
  ``api.js``, and the page block never re-declares a shared class;
* errors surface in a ``.callout`` (``GanglionAPI.showError``) and never in an
  ``alert()``;
* the keyboard handlers never swallow Cmd/Ctrl chords (undo) and are inert
  while a text field has focus;
* each page calls exactly the routes its half of the contract names, and
  renders the pieces the mockups (``web/mockups/{Labels,Rules,Loops}.dc.html``)
  put on the page;
* navigation is the same ten entries everywhere, with the page itself active.

No fixture here reads or writes ``runs/`` or ``examples/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
STYLE = WEB / "assets" / "style.css"
API_JS = WEB / "assets" / "api.js"

#: The pages this task owns.
PAGES = ("traces.html", "rules.html", "loops.html")

#: Every nav entry, in order, as it must appear in the opbar of each page.
NAV = (
    ("01", "./index.html"),
    ("02", "./catalog.html"),
    ("03", "./pipeline.html"),
    ("04", "./evaluation.html"),
    ("05", "./traces.html"),
    ("06", "./rules.html"),
    ("07", "./events.html"),
    ("08", "./observability.html"),
    ("09", "./chat.html"),
    ("10", "./loops.html"),
)

#: The 14 taxonomy buckets plus the E8 ``unclassified`` reporting bucket.
FAILURE_NAMES = (
    "syntax_invalid",
    "unknown_tool",
    "wrong_action",
    "missing_required_arg",
    "unknown_arg",
    "type_mismatch",
    "value_out_of_enum",
    "value_out_of_range",
    "alias_unrecognised",
    "abstention_miss_should_call",
    "abstention_miss_should_abstain",
    "parallel_order_mismatch",
    "partial_arg_value_mismatch",
    "no_failure",
    "unclassified",
)

_CLASS_ATTR = re.compile(r'class="([^"]*)"')
_STYLE_BLOCK = re.compile(r"<style>(.*?)</style>", re.S)
_SCRIPT_BLOCK = re.compile(r"<script.*?</script>", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_CLASS = re.compile(r"\.([A-Za-z][A-Za-z0-9_-]*)")
_BARE_CLASS = re.compile(r"^\.([A-Za-z][A-Za-z0-9_-]*)((?::{1,2}[A-Za-z-]+(?:\([^)]*\))?)*)$")
_SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc=\"([^\"]+)\"")
_LINK_HREF = re.compile(r"<link[^>]*\bhref=\"([^\"]+)\"")


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def css_classes(text: str) -> set[str]:
    """Every class name used as (part of) a selector in a CSS text."""
    text = _CSS_COMMENT.sub(" ", text)
    without_bodies = re.sub(r"\{[^{}]*\}", " ", text)
    return set(_CSS_CLASS.findall(without_bodies))


def bare_class_rules(css: str) -> set[str]:
    """Classes a text declares on their own (``.qrow``, ``.qrow:hover``).

    A compound or descendant selector (``.code.wrapv``, ``.tiles .kpi``) only
    qualifies an existing rule, so it is not a re-declaration.
    """
    css = _CSS_COMMENT.sub(" ", css)
    out: set[str] = set()
    for chunk in re.findall(r"([^{}]+)\{", css):
        for selector in chunk.split(","):
            match = _BARE_CLASS.match(selector.strip())
            if match:
                out.add(match.group(1))
    return out


def markup_classes(html: str) -> set[str]:
    """Class tokens in the page's own static markup (JS-built markup excluded)."""
    html = _SCRIPT_BLOCK.sub(" ", _STYLE_BLOCK.sub(" ", html))
    tokens: set[str] = set()
    for attr in _CLASS_ATTR.findall(html):
        tokens.update(token for token in attr.split() if token)
    return tokens


def page_script(html: str) -> str:
    """The page's own inline script (the one that is not ``src=``)."""
    blocks = [block for block in re.findall(r"<script>(.*?)</script>", html, re.S)]
    assert blocks, "the page has no inline script"
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# assets, errors, classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_pages_load_only_the_checked_in_assets(page: str) -> None:
    html = read(page)
    assert _SCRIPT_SRC.findall(html) == ["./assets/api.js"], "no build step, no CDN, no mock data.js"
    assert _LINK_HREF.findall(html) == ["./assets/style.css"]


@pytest.mark.parametrize("page", PAGES)
def test_pages_hit_the_real_api_through_ganglion_api(page: str) -> None:
    html = read(page)
    assert "window.GanglionAPI" in html
    assert "/api/runs" in html, "every page starts from the run list"
    assert "window.GANGLION" not in html, "assets/data.js mock is not used by a live page"


@pytest.mark.parametrize("page", PAGES)
def test_pages_surface_errors_in_a_callout_not_an_alert(page: str) -> None:
    html = read(page)
    assert "alert(" not in html
    assert "document.write" not in html
    assert 'id="err"' in html and "showError" in html
    assert "callout" in html, "the api.js-less fallback must still paint a .callout"


@pytest.mark.parametrize("page", PAGES)
def test_page_classes_are_declared_somewhere(page: str) -> None:
    """No typo'd class: every static class is in style.css or the page block."""
    html = read(page)
    declared = css_classes(STYLE.read_text(encoding="utf-8"))
    for block in _STYLE_BLOCK.findall(html):
        declared |= css_classes(block)
    declared |= css_classes(API_JS.read_text(encoding="utf-8"))
    unknown = {name for name in markup_classes(html) if name not in declared}
    assert not unknown, f"{page} uses undeclared classes: {sorted(unknown)}"


@pytest.mark.parametrize("page", PAGES)
def test_page_style_block_never_redeclares_the_vocabulary(page: str) -> None:
    """``assets/style.css`` stays the single owner of its class names."""
    shared = bare_class_rules(STYLE.read_text(encoding="utf-8"))
    for block in _STYLE_BLOCK.findall(read(page)):
        clash = bare_class_rules(block) & shared
        assert not clash, f"{page} re-declares style.css classes: {sorted(clash)}"


@pytest.mark.parametrize("page", PAGES)
def test_pages_are_not_marked_mock_and_show_an_empty_state(page: str) -> None:
    html = read(page)
    assert 'tag--ghost">mock' not in html
    assert "ganglion.console seed" in html, "an empty runs dir must point at the seed command"


@pytest.mark.parametrize("page", ("traces.html", "rules.html"))
def test_keyboard_handlers_never_swallow_undo(page: str) -> None:
    """The two pages that bind keys guard the chord modifiers; loops.html binds none."""
    script = page_script(read(page))
    assert "addEventListener('keydown'" in script
    handler = script.split("addEventListener('keydown'", 1)[1]
    assert "ev.ctrlKey || ev.metaKey || ev.altKey" in handler, "Cmd/Ctrl+Z must reach the browser"
    assert "TEXTAREA" in handler and "INPUT" in handler, "keys stay inert while typing"


def test_loop_log_binds_no_keyboard_shortcut() -> None:
    """The loop log is read-only: no key binding, so nothing to swallow."""
    assert "keydown" not in page_script(read("loops.html"))


# ---------------------------------------------------------------------------
# navigation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_nav_carries_all_ten_entries_with_the_page_active(page: str) -> None:
    html = read(page)
    for number, href in NAV:
        assert f'href="{href}"' in html, f"{page} is missing the {number} nav entry"
        assert f'>{number}</span>' in html
    assert f'href="./{page}" class="active"' in html, f"{page} must mark itself active"


# ---------------------------------------------------------------------------
# traces.html — the label queue
# ---------------------------------------------------------------------------


def test_label_queue_offers_every_filter_and_posts_labels() -> None:
    html = read("traces.html")
    for name in ("unlabelled", "invalid", "wrong", "changed", "by_failure", "all"):
        assert f'data-filter="{name}"' in html, f"the seg is missing the {name} filter"
    assert "/traces?filter=" not in html, "filters go through GanglionAPI's query builder"
    assert "'/traces'" in html and "failure_type" in html
    assert "'/api/labels'" in html
    for verdict in ("'correct'", "'incorrect'", "'unsure'", "'should_abstain'"):
        assert verdict in html
    assert "order_sensitive" in html and "time_to_label_ms" in html and "supersedes" in html


def test_label_queue_gates_the_changed_filter_on_a_compare_file() -> None:
    html = read("traces.html")
    assert "parent_run_id" in html and "has_compare" in html
    assert "changedEnabled" in html
    assert "is-off" in html, "the disabled option must be visibly disabled"
    assert "console compare" in html, "the title must say how to produce the compare file"


def test_label_queue_shows_the_diff_attribution_evidence_and_attempts() -> None:
    html = read("traces.html")
    assert "diffPlans" in html and "diff__col--expected" in html and "diff__col--actual" in html
    for key in ("f0_plan", "fk_plan", "rescued_by", "regressed_by", "necessary_hooks", "unattributable"):
        assert key in html, f"the F⁰/Fᴷ strip must read {key}"
    assert "CLASSIFICATION EVIDENCE" in html and "classification.evidence" in html.replace(" ", "")
    assert "ATTEMPTS (" in html and "raw_plan" in html and "raw_output" in html
    assert "renderLabel" in html, "the current label must be shown"


def test_label_queue_escapes_prompts_and_model_output() -> None:
    html = read("traces.html")
    assert "esc(row.prompt)" in html and "esc(trace.prompt)" in html
    assert "escapeHtml" in html
    assert "+ trace.prompt +" not in html, "a prompt must never reach innerHTML unescaped"


def test_label_queue_documents_its_keyboard_flow() -> None:
    html = read("traces.html")
    script = page_script(html)
    for key in ("'j'", "'k'", "'y'", "'n'", "'u'", "'o'", "'Enter'", "'z'", "'g'", "'?'"):
        assert f"case {key}:" in script, f"the keyboard flow is missing {key}"


# ---------------------------------------------------------------------------
# rules.html — the rule lifecycle
# ---------------------------------------------------------------------------


def test_rule_lifecycle_calls_analyze_patches_and_decisions() -> None:
    html = read("rules.html")
    assert "'/analyze'" in html and "'/patches'" in html
    assert "/decision'" in html and "/ported'" in html
    assert "commit_sha" in html, "porting takes a reviewed commit sha"
    for stage in ('data-stage="blind"', 'data-stage="after_preview"'):
        assert stage in html
    for action in ('data-act="accept"', 'data-act="hold"', 'data-act="reject"', 'data-act="ported"'):
        assert action in html
    assert "'a'" in html and "'h'" in html and "'r'" in html


def test_rule_lifecycle_hides_the_preview_until_a_blind_decision() -> None:
    html = read("rules.html")
    assert "preview hidden until a blind decision" in html
    assert "blind decision required first" in html
    assert "patch.preview" in html, "the preview comes from the API, never from the page"


def test_rule_lifecycle_renders_the_kpi_strip_and_the_contract_column() -> None:
    html = read("rules.html")
    for key in ("n_proposed", "accepted_blind", "accepted_after_preview", "ported", "precision_at_conf"):
        assert key in html, f"the KPI strip must read precision.{key}"
    for key in ("by_hook", "by_gold_origin", "retire_candidates"):
        assert key in html, f"the CONTRACT column must read {key}"
    for token in ("retire_candidate", "insufficient_evidence", "n_active", "cp95_upper", "conservative"):
        assert token in html
    assert "em_f0" in html and "em_fk" in html


def test_rule_lifecycle_handles_every_patch_operation() -> None:
    html = read("rules.html")
    for operation in (
        "add_alias",
        "set_default",
        "enable_strip_unknown_args",
        "extend_argspec",
        "add_prompt_correction",
        "retire_rule",
        "ESCALATE",
    ):
        assert f"'{operation}'" in html or f'"{operation}"' in html, f"no edit hint for {operation}"
    assert "example_trace_ids" in html and "./traces.html" in html, "ESCALATE must link into the queue"


# ---------------------------------------------------------------------------
# loops.html — the per-iteration log
# ---------------------------------------------------------------------------


def test_loop_log_reads_the_manifest_compare_events_and_catalog() -> None:
    html = read("loops.html")
    assert "'/api/runs'" in html and "'/api/compare'" in html
    assert "/events'" in html and "'/api/catalogs/'" in html
    for key in ("parent_run_id", "catalog_fingerprint", "dataset_sha256", "decoding", "metric_kind", "git_head"):
        assert key in html, f"the manifest header must read {key}"
    assert "drift: " in html, "the header compares the run fingerprint with the live catalog"


def test_loop_log_draws_every_failure_bucket() -> None:
    html = read("loops.html")
    for name in FAILURE_NAMES:
        assert f"'{name}'" in html, f"the histogram is missing {name}"
    assert "hbar__fill" in html


def test_loop_log_draws_the_transition_matrix_with_delta_and_ci() -> None:
    html = read("loops.html")
    for cell in ("same_pass", "regressed", "fixed", "same_fail", "only_a", "only_b", "ungraded"):
        assert cell in html, f"the matrix must read counts.{cell}"
    assert "em_delta" in html and "ci95" in html and "bootstrap" in html
    assert "filter: 'changed'" in html, "the matrix links into the changed-only label queue"


def test_loop_log_shows_corrections_decisions_training_and_events() -> None:
    html = read("loops.html")
    assert "by_gold_origin" in html and "by_hook" in html
    for chip in ("blind accept", "after_preview accept", "held", "rejected", "ported"):
        assert chip in html, f"the decision chips must include {chip}"
    for key in ("base_model", "adapter_dir", "adapter_sha256", "train_provenance", "labels_used", "manifest_diff"):
        assert key in html, f"the training block must read {key}"
    assert "events__row" in html and "events__pay" in html


def test_loop_log_badges_are_derived_from_the_manifest() -> None:
    html = read("loops.html")
    for badge in (">iter ", "chat · ", ">reference<", ">no seed<", ">not analyzed<"):
        assert badge in html, f"the rail is missing the {badge!r} badge"
    assert "title=" in html, "each derived badge explains itself in a title"
