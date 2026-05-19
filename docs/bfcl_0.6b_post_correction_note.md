# BFCL Post-Correction — Decision Gate #3 (Not Load-Bearing)

**Date:** 2026-05-19
**Substrate:** V2 SFT cases (`runs/bfcl/sft_v2_0.6b_cases.jsonl`) — 500 predictions
**Method:** Retroactive dict-level transforms on the predicted `ActionPlan`, re-grade with `bfcl.grader.ast_match`. No model re-inference.

## Result

| Transform | Passes | Δ vs V2 | Notes |
| --- | ---: | ---: | --- |
| baseline V2 | 367/500 (73.4%) | — | reference |
| C1 `strip_unknown_args` | 367/500 (73.4%) | **+0pp** | SFT already drops schema-foreign args |
| C2 `add_optional_blanks` | 323/500 (64.6%) | **−8.8pp** | Spurious `""` values rejected by grader |
| C3 `int_to_float` (promote where schema=float) | 367/500 (73.4%) | **+0pp** | Already correct in V2 outputs |
| C1 ∪ C2 | 323/500 | −8.8pp | C2 dominates |
| C1 ∪ C2 ∪ C3 | 323/500 | −8.8pp | C2 dominates |

**Best post-correction outcome on BFCL: 0pp lift.** The arc spec's decision gate #3 (`post_correction_lift_pp ≥ +2pp`) is **not load-bearing on BFCL**. Per the spec's stop-the-arc-and-write rule, this is a documented skip — not an arc failure. S2c' and S3' proceed as planned because they target different failure modes (call-count residuals, argument-value mismatches).

## Why IoT-style post-correction does not transfer

| Surface | IoT (Phase 2) | BFCL |
| --- | --- | --- |
| Catalog shape | one fixed `iot_light_5` per run | per-case, case has its own `function` list |
| Alias map | hard-coded in `ToolSpec.aliases` ("거실"→"living", "영화 모드"→"movie") | none — BFCL provides only the schema |
| Failure pattern targeted | numeric `#N` echo, KR time format, missing `state` | semantic mapping ("New York" vs "New York City, NY") |
| `defaults_when_missing` worked | yes, +6pp on iot_light_5 | no, grader rejects spurious `""` values |
| `strip_unknown_args` worked | yes (eliminates `#N` echo) | redundant — SFT learned the constraint |

The IoT lift came from rules that knew the catalog's domain (locale, scene names). BFCL hands a different catalog to every case, so the post-correction layer has no domain to specialise on. The only rules that can still fire are catalog-agnostic ones (schema-shape coercion), and those produce 0pp on this corpus.

## What about the 16 `value_error:string` failures?

Inspecting them confirms post-correction cannot fix them:

| Predicted | Ground-truth (any of) | Failure type |
| --- | --- | --- |
| `Chicago` | `Chicago, IL.`, `Chicago, IL` | locality detail missing |
| `humidity` | `c` (Celsius) | wrong enum branch |
| `erosion prevention` | `hill`, `steep`, `moderate` | wrong enum branch |
| `New York City, NY` | `New York`, `New York, NY`, `NYC` | locality detail extra |
| `open_hours` | `opening_hours` | synonym not aliased |
| `American Professional II` | `American Professional II Stratocaster` | trim |
| `tablespoons` | `tablespoon`, `tbsp` | plural |
| `New York City, NY` | `New York, NY` | locality detail extra |

These are **semantic argument-value choices**, not formatting bugs. They are the right shape, type, and key; the value is just not in the accepted list. The arc spec already routes this exact failure mode to S3' (DPO with verifier-graded reward), not S2a+.

## Decision

Per the arc spec:

> Decision gate #3 — *Is the IoT-style post-correction lift achievable on BFCL?* If lift is < 2pp, the post-correction track is not load-bearing on BFCL.

Gate #3 evaluates as **not-load-bearing**. The arc proceeds:

- **Skip** further S2a+ work (the wrapping `--allow-empty-mode` flag from V2 is the only post-correction piece that stays in tree).
- **Continue** to S2c' (self-bootstrap, targets the 38 residual `wrong_count` failures).
- **Continue** to S3' (DPO with graded reward, targets the 45 `cannot_find_match` and 16 `value_error:string` failures).

The 0pp result is a substantive negative finding: a recipe that delivered +6pp on the small IoT-light catalog produces nothing on a per-case-catalog benchmark with a strict accepted-value grader. The arc captures this rather than over-fit the IoT pattern.
