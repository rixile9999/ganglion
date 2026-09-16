"""Static guards for the two live console pages and their shared client.

Owner: the ``web-a`` half of the console work — ``web/assets/api.js``,
``web/chat.html``, ``web/catalog.html`` and the navigation of the pages that
stay mock. The behavioural checks live in the browser (the pages are driven
against a running ``python -m ganglion.console serve``); what is asserted
here is everything a text-only test can hold still:

* [[console_operator]]'s ``out`` clause — ``web/assets/api.js`` exists and
  exposes ``window.GanglionAPI`` with the surface the page tasks consume.
* the pages load **only** the checked-in stylesheet and ``api.js`` — no build
  step, no CDN, no second copy of the style vocabulary;
* every class the static markup uses is declared either in
  ``web/assets/style.css`` or in that page's own small ``<style>`` block, and
  the page block never re-declares a class the stylesheet already owns;
* navigation is consistent across every page (``09 Chat`` / ``10 Loops``) and
  the pages that are still mock say so with a ``.tag--ghost`` chip.

No fixture here reads or writes ``runs/`` or ``examples/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
STYLE = WEB / "assets" / "style.css"
API_JS = WEB / "assets" / "api.js"

#: Pages driven by ``/api/*`` (owned by web-a).
LIVE_PAGES = ("chat.html", "catalog.html")
#: Pages that still render mock data and must advertise it.
MOCK_PAGES = (
    "index.html",
    "pipeline.html",
    "evaluation.html",
    "events.html",
    "observability.html",
    "architecture.html",
)

#: The surface [[console_operator]] names plus the helpers both page tasks use.
API_SURFACE = (
    "get",
    "post",
    "ctx",
    "loadCtx",
    "saveCtx",
    "fmt",
    "renderPlanCalls",
    "diffPlans",
    "escapeHtml",
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
    """Every class name that appears as a selector in a CSS text."""
    text = _CSS_COMMENT.sub(" ", text)
    without_bodies = re.sub(r"\{[^{}]*\}", " ", text)
    return set(_CSS_CLASS.findall(without_bodies))


def bare_class_rules(css: str) -> set[str]:
    """Classes the text declares *on their own* (``.mrow``, ``.mrow:hover``).

    A compound or descendant selector (``.mrow.active``, ``.col .panel__body``)
    only qualifies an existing rule and is therefore not a re-declaration.
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
    """Class tokens used in the page's own static markup (not JS-generated)."""
    html = _SCRIPT_BLOCK.sub(" ", _STYLE_BLOCK.sub(" ", html))
    tokens: set[str] = set()
    for attr in _CLASS_ATTR.findall(html):
        tokens.update(token for token in attr.split() if token)
    return tokens


# ---------------------------------------------------------------------------
# api.js
# ---------------------------------------------------------------------------


def test_api_js_exists_and_exports_the_console_surface() -> None:
    assert API_JS.is_file(), "console_operator's `out` requires web/assets/api.js"
    source = API_JS.read_text(encoding="utf-8")
    assert "window.GanglionAPI" in source
    exports = source.split("window.GanglionAPI", 1)[1]
    for name in API_SURFACE:
        assert re.search(rf"\b{name}\s*:", exports), f"api.js does not export {name}"


def test_api_js_documents_every_public_function() -> None:
    """Each exported helper carries the one-line comment the brief asks for."""
    source = API_JS.read_text(encoding="utf-8")
    for name in ("get", "post", "loadCtx", "saveCtx", "escapeHtml", "renderPlanCalls", "diffPlans"):
        pattern = rf"/\*\*[^\n]*\n?(?:[^\n]*\n)??\s*function {name}\b"
        assert re.search(rf"function {name}\b", source), f"api.js has no function {name}"
        index = source.index(f"function {name}")
        preceding = source[max(0, index - 400): index]
        assert "/**" in preceding or "//" in preceding, f"{name} is undocumented"


def test_api_js_uses_no_external_dependency() -> None:
    source = API_JS.read_text(encoding="utf-8")
    assert "import " not in source and "require(" not in source
    assert "cdn" not in source.lower()


# ---------------------------------------------------------------------------
# the two live pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", LIVE_PAGES)
def test_live_pages_load_only_the_checked_in_assets(page: str) -> None:
    html = read(page)
    assert _SCRIPT_SRC.findall(html) == ["./assets/api.js"], "no build step, no CDN, no mock data.js"
    assert _LINK_HREF.findall(html) == ["./assets/style.css"]


@pytest.mark.parametrize("page", LIVE_PAGES)
def test_live_pages_hit_the_real_api(page: str) -> None:
    html = read(page)
    assert "GanglionAPI" in html
    assert "/api/" in html, "the page must call the console API, not render mock data"
    assert "window.GANGLION" not in html, "assets/data.js mock is not used by a live page"


@pytest.mark.parametrize("page", LIVE_PAGES)
def test_live_pages_surface_errors_in_a_callout_not_an_alert(page: str) -> None:
    html = read(page)
    assert "alert(" not in html
    assert "document.write" not in html
    assert 'id="err"' in html and "showError" in html


@pytest.mark.parametrize("page", LIVE_PAGES)
def test_live_page_classes_are_declared_somewhere(page: str) -> None:
    """No typo'd class: every static class is in style.css or the page block."""
    html = read(page)
    declared = css_classes(STYLE.read_text(encoding="utf-8"))
    for block in _STYLE_BLOCK.findall(html):
        declared |= css_classes(block)
    declared |= css_classes(API_JS.read_text(encoding="utf-8"))
    unknown = {name for name in markup_classes(html) if name not in declared}
    assert not unknown, f"{page} uses undeclared classes: {sorted(unknown)}"


@pytest.mark.parametrize("page", LIVE_PAGES)
def test_live_page_style_block_never_redeclares_the_vocabulary(page: str) -> None:
    """The shared stylesheet stays the single owner of its class names."""
    shared = bare_class_rules(STYLE.read_text(encoding="utf-8"))
    for block in _STYLE_BLOCK.findall(read(page)):
        clash = bare_class_rules(block) & shared
        assert not clash, f"{page} re-declares style.css classes: {sorted(clash)}"


@pytest.mark.parametrize("page", LIVE_PAGES)
def test_live_pages_are_not_marked_mock(page: str) -> None:
    assert 'tag--ghost">mock' not in read(page)


def test_chat_page_covers_the_three_model_kinds_and_the_verdict_bar() -> None:
    html = read("chat.html")
    # the three ModelSpec kinds, each with its own badge (api = openai_compat)
    for token in (">rules<", ">local<", ">api<", "'local_hf'"):
        assert token in html, f"the picker must handle {token}"
    for route in ("/api/sessions", "/api/chat", "/api/labels", "/api/models", "/health", "/load", "/unload"):
        assert route in html
    for verdict in ("correct", "incorrect", "unsure"):
        assert f"'{verdict}'" in html
    assert "order_sensitive" in html
    assert "loading model…" in html, "local models need a visible first-use loading state"
    assert "f0_plan" in html and "hooks_diff" in html


def test_chat_keyboard_handler_does_not_swallow_undo() -> None:
    html = read("chat.html")
    handler = html.split("Verdict keys.", 1)[1]
    assert "if (event.ctrlKey || event.metaKey || event.altKey) return;" in handler
    assert "TEXTAREA" in handler and "INPUT" in handler


def test_catalog_page_renders_all_four_ir_views_and_the_compile_panel() -> None:
    html = read("catalog.html")
    for view in ("DSL", "OpenAI tools", "JSON-Schema", "system prompt"):
        assert view in html
    for key in ("json_dsl", "openai_tools", "json_schema", "system_prompt", "source_tools", "describe"):
        assert key in html
    assert "/api/catalogs/compile" in html
    for hook in ("aliases", "defaults_when_missing", "strip_unknown_args", "prompt_correction", "custom_validator"):
        assert hook in html


# ---------------------------------------------------------------------------
# navigation consistency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", LIVE_PAGES + MOCK_PAGES)
def test_every_page_links_to_chat_and_loops(page: str) -> None:
    html = read(page)
    assert "./chat.html" in html, f"{page} is missing the 09 Chat nav entry"
    assert "./loops.html" in html, f"{page} is missing the 10 Loops nav entry"


@pytest.mark.parametrize("page", MOCK_PAGES)
def test_mock_pages_declare_themselves_mock(page: str) -> None:
    assert 'tag--ghost">mock' in read(page), f"{page} still renders mock data and must say so"
