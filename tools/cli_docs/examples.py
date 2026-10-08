"""The example script the CLI reference site shows, as ordered steps.

Each :class:`Step` is executed for real against a throwaway seeded runs dir and
its scrubbed output is embedded in ``web/assets/cli.json`` — so every block on
the site is measured, not written. Order matters: a step may depend on state an
earlier one created (a label before ``label export``, a blind decision before
``patch list`` can compute a preview).

Add a step here; ``build.py`` needs no change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable, Mapping, Sequence

from ganglion.programs.specs import builtins

__all__ = ["Step", "STEPS"]

#: Placeholders resolved from the accumulated capture context at run time.
#: ``{trace_id}`` / ``{patch_id}`` are filled by the hidden ``--json`` steps.
Argv = Sequence[str]


@dataclass(frozen=True)
class Step:
    """One documented (or hidden) invocation.

    ``leaf`` is the space-joined command path the block is filed under
    (``"catalog show"``); an empty string files it nowhere, which is what
    ``show=False`` setup steps use. ``argv`` may contain ``{name}`` placeholders
    resolved from the capture context. ``capture`` receives
    ``(context, payload)`` for a ``--json`` step and mutates the context.
    """

    leaf: str
    argv: Argv
    note: str = ""
    show: bool = True
    json_mode: bool = False
    capture: Callable[[dict[str, Any], Any], None] | None = None
    #: Files the runner must materialise first, as ``{placeholder: contents}``.
    files: Mapping[str, str] = field(default_factory=dict)


_WEATHER_TOOLS = """\
[
  {"type": "function",
   "function": {
     "name": "get_weather",
     "description": "Current weather for a city.",
     "parameters": {
       "type": "object",
       "properties": {
         "city": {"type": "string", "description": "City name"},
         "unit": {"type": "string", "enum": ["c", "f"], "description": "Temperature unit"}
       },
       "required": ["city"]}}}
]
"""

_GOLD_PLAN = """\
{"calls": [
  {"action": "schedule_light", "args": {"room": "living", "at": "07:00", "state": "on"}},
  {"action": "schedule_light", "args": {"room": "living", "at": "22:00", "state": "off"}}
]}
"""

_SEED = "rules-degraded-seed"

_CUSTOM_PII = builtins()["pii-rules"]
_CUSTOM_PII.update(id="pii-demo", title="Document privacy demo")
_CUSTOM_SPEC = json.dumps(_CUSTOM_PII, ensure_ascii=False)
_DOCUMENT = "이름: 김민수\r\n전화: 010-1234-5678\r\n이메일: minsu@example.com\r\n"
# Independently annotate the known fixture, never copy the detector's output.
_DOCUMENT_GOLD = []
for _text, _kind in (("김민수", "PERSON"), ("010-1234-5678", "PHONE"), ("minsu@example.com", "EMAIL")):
    _start = _DOCUMENT.index(_text)
    _DOCUMENT_GOLD.append({"start": len(_DOCUMENT[:_start].encode("utf-8")),
                           "end": len(_DOCUMENT[:_start + len(_text)].encode("utf-8")), "type": _kind})

STEPS: tuple[Step, ...] = (
    # -- orientation ------------------------------------------------------
    Step("health", ["health"],
         note="Where the CLI is pointed, and what it resolved the context to."),
    Step("use", ["use", "--catalog", "iot_light_5", "--model", "rules"],
         note="Set once; every later command omits --catalog / --model."),

    # -- catalogs ---------------------------------------------------------
    Step("catalog list", ["catalog", "list"]),
    Step("catalog show", ["catalog", "show"],
         note="--part all (the default): identity, the tool table, and the DSL block "
              "appended to the system prompt."),
    Step("catalog show", ["catalog", "show", "--part", "dsl"],
         note="Just the Action IR grammar the model is shown — the whole point of the "
              "project, in one block."),
    Step("catalog show", ["catalog", "show", "bfcl/simple_python"],
         note="BFCL catalogs are per-case and not resolvable; the route's own 404 is "
              "surfaced unchanged, exit 1."),
    Step("catalog compile", ["catalog", "compile", "{weather_tools}", "--name", "weather"],
         note="Compile an external OpenAI/MCP tool list into an addressable catalog. "
              "The id is content-addressed, so re-compiling the same tools is a no-op.",
         files={"weather_tools": _WEATHER_TOOLS}),

    # -- models -----------------------------------------------------------
    Step("model list", ["model", "list"]),
    Step("model health", ["model", "health", "rules"]),
    Step("model health", ["model", "health", "qwen3-0.6b@local"],
         note="A local_hf entry reports whether this machine can run it at all."),
    Step("model load", ["model", "load", "rules"],
         note="load / unload exist only for kind: local_hf — anything else is a 404."),
    Step("model unload", ["model", "unload", "qwen3-0.6b@local"],
         note="Idempotent: unloading a model that was never loaded is a 200, not an error."),

    # -- runs -------------------------------------------------------------
    Step("run list", ["run", "list"]),
    Step("run show", ["run", "show", _SEED],
         note="The failure histogram, F⁰/Fᴷ correction summary and patch precision "
              "for one run bundle."),
    Step("run events", ["run", "events", _SEED, "--tail", "6"],
         note="--tail and --name filter the human view only; --json prints every row."),
    Step("run analyze", ["run", "analyze", _SEED],
         note="classify → attribute → synthesise, each writing its own sidecar."),

    # -- traces -----------------------------------------------------------
    Step("trace list", ["trace", "list", _SEED, "--filter", "wrong", "--limit", "3"],
         note="Filters are query params — the route does the filtering, not the CLI."),
    Step("", ["trace", "list", _SEED, "--filter", "wrong", "--limit", "1"],
         show=False, json_mode=True,
         capture=lambda ctx, payload: ctx.update(trace_id=payload["traces"][0]["trace_id"])),
    Step("trace show", ["trace", "show", "{trace_id}", "--run", _SEED],
         note="The validated plan next to the gold, plus the classification and the "
              "F⁰/Fᴷ attribution when one exists."),

    # -- one inference ----------------------------------------------------
    Step("session new", ["session", "new"],
         note="A chat session is an ordinary run bundle; the id is saved to the context."),
    Step("ask", ["ask", "거실", "불", "켜줘"],
         note="The F⁰ / Fᴷ pair: what the model produced alone, next to what the "
              "catalog accepted, and which hook closed the gap."),
    Step("ask", ["ask", "거실", "불", "켜줘"],
         note="The same prompt again is a second trace, not a collision — repeat_index "
              "advances (errata E7)."),

    # -- labels -----------------------------------------------------------
    Step("label add", ["label", "add", "{trace_id}", "--run", _SEED,
                       "--verdict", "incorrect", "--expected", "{gold_plan}",
                       "--note", "missed the second call"],
         note="A human verdict beats the dataset gold from here on (resolve_gold).",
         files={"gold_plan": _GOLD_PLAN}),
    Step("label add", ["label", "add", "{trace_id}", "--run", _SEED,
                       "--verdict", "incorrect", "--expected", "{broken_plan}"],
         note="A local input problem never reaches the route.",
         files={"broken_plan": "{oops\n"}),
    Step("label export", ["label", "export"],
         note="Corrected-SFT and hard-pool export, in the shape the synth pipeline writes."),

    # -- patches ----------------------------------------------------------
    Step("patch list", ["patch", "list", _SEED],
         note="Proposed ToolSpec patches with their decision state. Accepting one "
              "changes no catalog — it records a judgement."),
    Step("", ["patch", "list", _SEED], show=False, json_mode=True,
         capture=lambda ctx, payload: ctx.update(patch_id=payload["patches"][0]["patch_id"])),
    Step("patch decide", ["patch", "decide", "{patch_id}", "--run", _SEED,
                          "--stage", "blind", "--decision", "accept",
                          "--reason", "default state=on is safe"]),
    Step("patch list", ["patch", "list", _SEED],
         note="After a blind decision the preview is computed in memory — here it "
              "reports the patch is not mechanically applicable."),
    Step("patch ported", ["patch", "ported", "{patch_id}", "--run", _SEED,
                          "--commit", "deadbeef1234"],
         note="Porting is a reviewed code edit, recorded with its commit."),

    # -- comparison -------------------------------------------------------
    Step("compare", ["compare", "rules-seed", _SEED],
         note="Paired transition matrix and ΔEM with a bootstrap CI; refuses "
              "mismatched runs unless --allow-diff."),

    # -- Architecture v2: one program surface, different contracts ---------
    Step("program list", ["program", "list"],
         note="The reference fixture deliberately has no native checkpoint. "
              "Rules and the existing tool-planner remain usable without training."),
    Step("program show", ["program", "show", "pii-rules"],
         note="The same ApplicationSpec supplies UI fields, CLI inputs, result "
              "labels and optional preprocessor/executor adapters."),
    Step("program show", ["program", "show", "pii-qwen"],
         note="The versioned PreprocessorSpec pins semantic splitting and character, "
              "overlap and token budgets. Inspecting a spec does not load its model."),
    Step("program register", ["program", "register", "{custom_spec}"],
         files={"custom_spec": _CUSTOM_SPEC},
         note="Register a data-only spec using an installed adapter; arbitrary "
              "Python modules and server filesystem paths are not executable specs."),
    Step("program keygen", ["program", "keygen", "--private", "private-key.json",
                            "--public", "public-key.json"],
         note="Keep the private file. Only the public key is needed to pseudonymize; "
              "both files are created with private permissions and never overwritten."),
    Step("program run", ["program", "run", "pii-demo", "--file", "{document}",
                         "--public-key", "public-key.json", "--output-dir", "privacy-output"],
         files={"document": _DOCUMENT}, json_mode=True,
         capture=lambda ctx, payload: ctx.update(pii_job=payload["job_id"]),
         note="Stream UTF-8 chunks, wait for completion and download the "
              "pseudonymized document, public byte-coordinate edit plan, encrypted "
              "recovery sidecar and safe trace."),
    Step("program jobs", ["program", "jobs"]),
    Step("program job", ["program", "job", "{pii_job}"],
         note="A separate CLI invocation reads the persisted job rather than "
              "depending on in-process state from the command that started it."),
    Step("program artifact", ["program", "artifact", "{pii_job}", "document.txt",
                              "--output", "pseudonymized-copy.txt"],
         note="Artifact downloads are bounded chunks and refuse to overwrite "
              "an existing local destination."),
    Step("program artifact", ["program", "artifact", "{pii_job}", "plan.jsonl",
                              "--output", "public-plan.jsonl"],
         note="Execution also publishes its public TextEditPlan: UTF-8 source byte "
              "coordinates and entity types without original PII strings."),
    Step("program feedback", ["program", "feedback", "{pii_job}", "{feedback}"],
         files={"feedback": '{"verdict": "correct"}\n'},
         note="A verdict alone is not gold annotation: analysis remains unclassified "
              "and does not report precision, recall or F1."),
    Step("program feedback", ["program", "feedback", "{pii_job}", "{partial_feedback}"],
         files={"partial_feedback": json.dumps({"verdict": "correct", "expected_spans": _DOCUMENT_GOLD[:1]})},
         note="Partial annotation evaluates the supplied name span. Other predictions "
              "remain unclassified; false positives, precision and F1 are unknown."),
    Step("program feedback", ["program", "feedback", "{pii_job}", "{complete_feedback}"],
         files={"complete_feedback": json.dumps({"verdict": "correct", "expected_spans": _DOCUMENT_GOLD, "gold_complete": True})},
         note="Only explicit exhaustive gold enables document-wide exact-span metrics "
              "and rescue/regression attribution. These annotations describe this "
              "small demonstration fixture; feedback still awaits independent validation."),
    Step("program cancel", ["program", "cancel", "{pii_job}"],
         note="Cancellation is idempotent: this already completed job stays complete. "
              "Active jobs cancel cooperatively between document windows."),
    Step("program restore", ["program", "restore", "privacy-output/document.txt",
                             "privacy-output/recovery.bin", "--key", "private-key.json",
                             "--output", "restored.txt"],
         note="Authenticate the sidecar and output document before publishing "
              "the exact original bytes, including the source's CRLF line endings."),
    Step("program run", ["program", "run", "pii-demo", "--file", "{document}",
                         "--plan", "--output-dir", "core-plan-output"],
         note="Omit the optional executor to publish only the edit plan and safe trace. "
              "This request requires no recovery public key."),
    Step("program run", ["program", "run", "tool-planner", "--inputs", "{tool_inputs}"],
         files={"tool_inputs": '{"prompt": "거실 불 켜줘", "catalog_id": "iot_light_5", "model_id": "rules"}\n'},
         note="The same generic job API accepts an ActionPlan domain. Its executor "
              "is absent, so this creates a tool plan without operating hardware."),
)
