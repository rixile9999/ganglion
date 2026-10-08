"""Typed failure attribution must not turn overlap or incomplete gold into credit."""
from __future__ import annotations

import json

import pytest

from ganglion.analyzer.domain_analysis import analyze_predictions, register_analyzer
from ganglion.domains.pii.types import Span


def span(start, end, kind="PERSON", **extra):
    return {"start": start, "end": end, "type": kind, **extra}


def analyze(raw=(), final=(), gold=(), **kwargs):
    return analyze_predictions("pii-text", list(raw), list(final), None if gold is None else list(gold), **kwargs)


def final_failures(result):
    return [f for f in result["failures"] if f["stage"] == "final"]


@pytest.mark.parametrize("coordinate", ["character", "utf8-byte"])
def test_exact_typed_metrics_and_source_free_output(coordinate):
    person, email = span(0, 3, text="SECRET ORIGINAL"), span(10, 20, "EMAIL")
    result = analyze([person], [person, email], [person, email], coordinate=coordinate, document_length=20)
    assert result["domain"] == "pii-text" and result["coordinate"] == coordinate
    assert result["status"] == "pass"
    assert result["raw"]["tp"] == 1 and result["raw"]["fn"] == 1
    assert result["raw"]["f1"] == pytest.approx(2 / 3)
    assert result["final"]["f1"] == 1
    assert result["final"]["per_type"]["EMAIL"]["tp"] == 1
    assert result["attribution"]["rescued"] == [email]
    assert "SECRET ORIGINAL" not in json.dumps(result)


@pytest.mark.parametrize(("predicted", "gold", "kind"), [
    ([span(0, 4)], [span(0, 5)], "boundary_mismatch"),
    ([span(0, 4, "ADDRESS")], [span(0, 4)], "type_mismatch"),
    ([span(0, 3, "ADDRESS")], [span(0, 4)], "type_mismatch"),
    ([span(0, 3), span(3, 6)], [span(0, 6)], "split"),
    ([span(0, 6)], [span(0, 3), span(3, 6)], "merge"),
])
def test_overlapping_error_has_one_component_and_zero_exact_credit(predicted, gold, kind):
    result = analyze(predicted, predicted, gold)
    assert result["status"] == "fail"
    assert result["final"]["tp"] == 0 and result["final"]["f1"] == 0
    assert result["final"]["fp"] == len(predicted)
    assert result["final"]["fn"] == len(gold)
    assert result["histogram"] == {kind: 1}
    assert len(final_failures(result)) == 1
    assert result["stage_histograms"]["raw"] == {kind: 1}


def test_disjoint_prediction_and_gold_are_fp_and_fn_not_boundary_error():
    result = analyze([span(0, 3)], [span(0, 3)], [span(3, 6)])
    assert result["histogram"] == {"fn": 1, "fp": 1}
    assert {f["kind"] for f in final_failures(result)} == {"fn", "fp"}


def test_complex_overlap_component_is_not_counted_as_several_matching_errors():
    ps = [span(0, 4), span(4, 9)]
    gs = [span(0, 5), span(5, 9)]
    result = analyze(ps, ps, gs)
    assert result["final"]["tp"] == 0
    assert result["histogram"] == {"boundary_mismatch": 1}
    assert final_failures(result)[0]["evidence"]["complex_component"] is True


def test_exact_match_is_removed_before_component_matching():
    correct, wrong = span(0, 5), span(1, 4)
    result = analyze([correct, wrong], [correct, wrong], [correct])
    assert result["final"]["tp"] == 1 and result["final"]["fn"] == 0
    assert result["histogram"] == {"fp": 1}


def test_duplicate_predictions_do_not_inflate_true_positives():
    p = span(2, 4)
    result = analyze([p, p], [p], [p, p])
    assert result["raw"]["tp"] == 1 and result["raw"]["duplicate_count"] == 1
    assert result["final"]["tp"] == 1 and result["final"]["duplicate_count"] == 0
    assert result["status"] == "pass"
    assert result["stage_histograms"]["raw"] == {"duplicate": 1}
    assert result["attribution"]["rescued_count"] == 0
    still_duplicate = analyze([p], [p, p], [p])
    assert still_duplicate["final"]["f1"] == 1
    assert still_duplicate["status"] == "fail"


@pytest.mark.parametrize("malformed", [
    span(-1, 3), span(0, 0), span(5, 4), span(True, 4), span(0, 11),
    span("PII SECRET", 4), span(0, 4, "UNKNOWN SECRET"),
    {"start": 1}, "UNSAFE SOURCE STRING", None,
])
def test_invalid_predictions_are_diagnostics_but_invalid_gold_is_rejected(malformed):
    result = analyze([], [malformed], [], document_length=10)
    assert result["histogram"] == {"invalid_span": 1}
    assert result["status"] == "fail" and result["final"]["invalid_count"] == 1
    assert "SECRET" not in json.dumps(result) and "UNSAFE" not in json.dumps(result)
    with pytest.raises(ValueError, match="invalid gold span"):
        analyze([], [], [malformed], document_length=10)


def test_missing_prediction_container_is_invalid_not_empty_pass():
    result = analyze_predictions("pii-text", [], None, [])
    assert result["status"] == "fail"
    assert result["histogram"] == {"invalid_span": 1}


def test_partial_gold_does_not_label_unannotated_predictions_false_positive():
    known, unknown = span(0, 3), span(10, 13, "PHONE")
    result = analyze([known], [known, unknown], [known], gold_complete=False)
    assert result["status"] == "partial" and result["gold_complete"] is False
    assert result["final"]["tp"] == 1 and result["final"]["fn"] == 0
    assert result["final"]["fp"] is None
    assert result["final"]["precision"] is None and result["final"]["f1"] is None
    assert result["final"]["recall"] == 1
    assert result["final"]["unclassified_predictions"] == 1
    assert result["histogram"] == {"unclassified": 1}
    assert result["attribution"]["false_positives_introduced"] is None
    assert result["final"]["per_type"]["PHONE"]["f1"] is None


def test_partial_gold_still_classifies_errors_against_annotated_entities():
    known = span(0, 5)
    result = analyze([known], [span(0, 4)], [known], gold_complete=False)
    assert result["status"] == "partial"
    assert result["histogram"] == {"boundary_mismatch": 1}
    assert result["final"]["fn"] == 1 and result["final"]["recall"] == 0
    assert result["attribution"]["regressed"] == [known]


def test_empty_partial_gold_never_claims_perfect_recall_or_f1():
    result = analyze([], [], [], gold_complete=False)
    assert result["status"] == "partial"
    assert all(result["final"][name] is None for name in ("precision", "recall", "f1", "fp"))


def test_no_gold_is_unclassified_instead_of_self_scored_pass():
    result = analyze([span(0, 3)], [span(0, 3)], None)
    assert result["status"] == "unclassified"
    assert result["final"]["unclassified_predictions"] == 1
    assert all(result["final"][name] is None for name in ("tp", "fp", "fn", "precision", "recall", "f1"))
    assert result["attribution"] is None and result["attribution_status"] == "unclassified"


def test_missing_raw_stage_does_not_create_invalid_predictions_or_fake_attribution():
    p = span(0, 3)
    result = analyze_predictions("pii-text", None, [p], [p], coordinate="utf8-byte")
    assert result["raw"] is None and result["stage_histograms"]["raw"] is None
    assert result["final"]["tp"] == 1 and result["final"]["f1"] == 1
    assert result["status"] == "pass" and result["histogram"] == {}
    assert result["attribution"] is None and result["attribution_status"] == "unavailable"
    assert not result["failures"]


def test_missing_raw_stage_preserves_partial_gold_limits():
    p, extra = span(0, 3), span(10, 15)
    result = analyze_predictions("pii-text", None, [p, extra], [p], gold_complete=False)
    assert result["status"] == "partial"
    assert result["raw"] is None and result["final"]["fp"] is None
    assert result["histogram"] == {"unclassified": 1}
    assert result["attribution"] is None


def test_verified_empty_gold_and_empty_predictions_are_perfect():
    result = analyze([], [], [], document_length=0)
    assert result["status"] == "pass"
    assert all(result["final"][name] == 1 for name in ("precision", "recall", "f1"))
    assert result["final"]["tp"] == result["final"]["fp"] == result["final"]["fn"] == 0


def test_correction_attribution_separates_rescue_regression_and_false_positives():
    rescued, regressed, fp_old, fp_new = span(0, 3), span(4, 7), span(8, 11), span(12, 15)
    result = analyze([regressed, fp_old], [rescued, fp_new], [rescued, regressed])
    attribution = result["attribution"]
    assert attribution["rescued"] == [rescued] and attribution["rescued_count"] == 1
    assert attribution["regressed"] == [regressed] and attribution["regressed_count"] == 1
    assert attribution["false_positives_removed"] == [fp_old]
    assert attribution["false_positives_introduced"] == [fp_new]


def test_permuting_input_does_not_change_component_attribution():
    ps, gs = [span(0, 3), span(3, 6), span(20, 25)], [span(0, 6), span(30, 35)]
    a, b = analyze(ps, ps, gs), analyze(list(reversed(ps)), list(reversed(ps)), list(reversed(gs)))
    assert a == b


def test_span_instances_are_supported():
    result = analyze([Span(0, 3, "PERSON")], [Span(0, 3, "PERSON")], [Span(0, 3, "PERSON")])
    assert result["status"] == "pass"


@pytest.mark.parametrize("kwargs", [
    {"coordinate": "token"}, {"document_length": -1}, {"document_length": True}, {"gold_complete": 1},
])
def test_analysis_contract_rejects_invalid_context(kwargs):
    with pytest.raises(ValueError):
        analyze(**kwargs)


def test_internal_domain_adapters_are_extensible_without_dynamic_imports():
    observed = {}

    def adapter(raw, final, gold, **context):
        observed.update(context)
        return {"status": "unclassified"}

    register_analyzer("test-installed-domain", adapter)
    result = analyze_predictions("test-installed-domain", [], [], None, coordinate="utf8-byte")
    assert result == {"domain": "test-installed-domain", "status": "unclassified"}
    assert observed["coordinate"] == "utf8-byte"
    with pytest.raises(ValueError, match="already installed"):
        register_analyzer("test-installed-domain", adapter)
    for domain in ("tool-calling", "os.system", None, {}):
        with pytest.raises(ValueError, match="not installed"):
            analyze_predictions(domain, [], [], [])
