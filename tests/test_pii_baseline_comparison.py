"""Exact typed matching is not equivalent to privacy mask coverage."""
from __future__ import annotations

import copy
import hashlib
import json
import sys

import pytest

from tools.pii_baselines.compare import main, mask_counts, mask_metrics, score


def entity(start, end, kind="PERSON"):
    return {"start": start, "end": end, "type": kind}


def corpus(text="김민수 공개", gold=None):
    return [{"id": "doc-1", "text": text, "spans": [entity(0, 3)] if gold is None else gold, "language": "ko"}]


def captured(spans, **extras):
    return [{"id": "doc-1", "spans": spans, **extras}]


def test_wrong_type_receives_zero_exact_credit_but_complete_mask_coverage():
    result = score(corpus(), captured([entity(0, 3, "ADDRESS")]))
    assert result["exact"] == {"tp": 0, "fp": 1, "fn": 1, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert result["mask_coverage"]["character_recall"] == 1.0
    assert result["mask_coverage"]["fully_masked_entity_recall"] == 1.0
    assert result["per_type"]["PERSON"]["fn"] == 1
    assert result["per_type"]["ADDRESS"]["fp"] == 1
    assert result["failure_histogram"] == {"type_mismatch": 1}


def test_partial_boundary_is_both_false_positive_and_false_negative():
    result = score(corpus(), captured([entity(0, 2)]))
    assert result["exact"]["tp"] == 0
    assert result["exact"]["fp"] == result["exact"]["fn"] == 1
    assert result["exact"]["f1"] == 0.0
    assert result["failure_histogram"] == {"boundary_mismatch": 1}
    assert result["mask_coverage"]["character_recall"] == pytest.approx(2 / 3)
    assert result["mask_coverage"]["fully_masked_entity_recall"] == 0.0


def test_overlapping_masks_count_union_characters_once_and_never_inflate_recall():
    text = "abcdefghijklmno"
    gold = [entity(1, 7), entity(5, 10, "ADDRESS")]
    predictions = [entity(1, 5), entity(3, 8), entity(3, 8), entity(12, 14, "EMAIL")]
    counts = mask_counts(text, gold, predictions)
    assert counts["gold_characters"] == 9
    assert counts["masked_characters"] == 9
    assert counts["correctly_masked_characters"] == 7
    assert counts["overmasked_characters"] == 2
    assert counts["non_pii_characters"] == 6
    assert counts["fully_masked_entities"] == 1
    metrics = mask_metrics(counts)
    assert metrics["character_recall"] == metrics["character_precision"] == pytest.approx(7 / 9)
    assert metrics["fully_masked_entity_recall"] == .5
    assert metrics["non_pii_character_overmask_rate"] == pytest.approx(2 / 6)


def test_mask_counts_use_unicode_characters_including_combining_characters():
    # Python and model coordinates count the combining accent separately.
    text = "😀e\u0301김민수\r\n"
    counts = mask_counts(text, [entity(3, 6)], [entity(2, 6)])
    assert counts["gold_characters"] == counts["correctly_masked_characters"] == 3
    assert counts["masked_characters"] == 4
    assert counts["overmasked_characters"] == 1
    assert counts["non_pii_characters"] == 5


@pytest.mark.parametrize("changes", [{"start": True}, {"start": .5}, {"start": -1},
                                     {"end": 10}, {"end": 0}])
def test_invalid_captured_coordinates_fail_instead_of_being_clipped(changes):
    malformed = {**entity(0, 3), **changes}
    with pytest.raises(ValueError):
        score(corpus(), captured([malformed]))


def test_unsupported_canonical_type_is_rejected_but_native_extra_labels_are_allowed():
    with pytest.raises(ValueError, match="five-type"):
        score(corpus(), captured([entity(0, 3, "first_name")]))
    result = score(corpus(), captured([], raw_model_spans=[{"start": 0, "end": 3, "label": "first_name", "score": .9}]))
    assert result["exact"]["f1"] == 0.0
    assert result["mask_coverage"]["character_recall"] == 0.0
    assert result["native_all_label_mask_coverage"]["character_recall"] == 1.0


@pytest.mark.parametrize("predictions", [[], [{"id": "different", "spans": []}],
                                         [{"id": "doc-1", "spans": []}, {"id": "doc-1", "spans": []}],
                                         [{"id": "doc-1", "spans": []}, {"id": "extra", "spans": []}]])
def test_missing_duplicate_or_extra_document_ids_do_not_reduce_the_denominator(predictions):
    with pytest.raises(ValueError):
        score(corpus(), predictions)


def test_duplicate_document_ids_are_rejected_even_when_prediction_row_count_matches():
    rows = corpus() + [{"id": "doc-2", "text": "공개", "spans": []}]
    with pytest.raises(ValueError):
        score(rows, [{"id": "doc-1", "spans": []}, {"id": "doc-1", "spans": []}])


def test_duplicate_exact_predictions_do_not_inflate_true_positive_counts():
    result = score(corpus(), captured([entity(0, 3), entity(0, 3)]))
    assert result["exact"]["tp"] == 1 and result["exact"]["f1"] == 1.0
    assert result["duplicate_predictions"] == 1
    assert result["mask_coverage"]["correctly_masked_characters"] == 3


def test_exported_and_whitespace_normalized_metrics_are_both_reported():
    text = "\t김민수 \r\n"
    rows = corpus(text, [entity(1, 4)])
    original = captured([entity(0, len(text))])
    before = copy.deepcopy(original)
    result = score(rows, original)
    assert result["exact"]["f1"] == 1.0
    assert result["exported_exact"]["f1"] == 0.0
    assert result["exported_exact"]["fp"] == result["exported_exact"]["fn"] == 1
    assert result["mask_coverage"]["masked_characters"] == 3
    assert result["native_all_label_mask_coverage"]["masked_characters"] == len(text)
    assert original == before, "evaluation must leave the captured export immutable"


def test_common_trim_does_not_repair_korean_suffix_or_punctuation_errors():
    rows = corpus("김민수님, 공개", [entity(0, 3)])
    result = score(rows, captured([entity(0, 5)]))
    assert result["exact"]["f1"] == result["exported_exact"]["f1"] == 0.0
    assert result["mask_coverage"]["overmasked_characters"] == 2


def test_private_extras_and_source_values_are_not_echoed_in_default_report():
    secret = "PRIVATE_KEY_DO_NOT_PERSIST"
    rows = corpus()
    rows[0]["private_key"] = secret
    predictions = captured([dict(entity(0, 2), value="김민", original="source-private")],
                           private_key=secret, source=rows[0]["text"], debug="sensitive debug data")
    result = score(rows, predictions)
    serialized = json.dumps(result, ensure_ascii=False)
    assert result["examples"] is None
    for private in (secret, rows[0]["text"], "김민", "source-private", "sensitive debug data"):
        assert private not in serialized
    opted_in = score(rows, predictions, include_examples=True)
    example = opted_in["examples"]["boundary_mismatch"][0]
    assert example["source"] == rows[0]["text"]
    assert example["predicted"][0]["value"] == "김민"
    assert secret not in json.dumps(opted_in)


def test_negative_document_false_positive_rate_uses_documents_and_micro_counts():
    rows = [{"id": "clean", "text": "공개문서", "spans": []},
            {"id": "bad", "text": "잘못가린공개문서", "spans": []},
            {"id": "pii", "text": "김민수", "spans": [entity(0, 3)]}]
    predictions = [{"id": "clean", "spans": []},
                   {"id": "bad", "spans": [entity(0, 2), entity(3, 5)]},
                   {"id": "pii", "spans": [entity(0, 3)]}]
    result = score(rows, predictions)
    assert result["negative_documents"] == 2
    assert result["negative_documents_with_fp"] == 1
    assert result["negative_document_fp_rate"] == .5
    assert result["pure_non_pii_predictions"] == 2
    assert result["exact"]["tp"] == 1 and result["exact"]["fp"] == 2
    assert result["exact"]["f1"] == .5


@pytest.mark.parametrize("gap", [" ", "\t", "\r\n"])
def test_native_first_and_last_name_masking_distinguishes_whitespace_from_sensitive_characters(gap):
    text = "Kim" + gap + "Park"
    native = [{"start": 0, "end": 3, "label": "first_name"},
              {"start": 3 + len(gap), "end": len(text), "label": "last_name"}]
    result = score(corpus(text, [entity(0, len(text))]), captured([], raw_model_spans=native))
    coverage = result["native_all_label_mask_coverage"]
    assert result["exact"]["f1"] == 0.0
    assert coverage["fully_masked_entity_recall"] == 0.0
    assert coverage["fully_masked_non_whitespace_entity_recall"] == 1.0
    assert coverage["character_recall"] == pytest.approx(7 / len(text))


def test_unmasked_name_punctuation_is_not_ignored_as_whitespace():
    text = "Kim-Park"
    native = [{"start": 0, "end": 3, "label": "first_name"}, {"start": 4, "end": 8, "label": "last_name"}]
    coverage = score(corpus(text, [entity(0, 8)]), captured([], raw_model_spans=native))["native_all_label_mask_coverage"]
    assert coverage["fully_masked_entity_recall"] == 0.0
    assert coverage["fully_masked_non_whitespace_entity_recall"] == 0.0


def write_cli_fixture(tmp_path, *, synthetic):
    data = tmp_path / "test.jsonl"
    data.write_text(json.dumps(corpus()[0], ensure_ascii=False) + "\n")
    test_hash = hashlib.sha256(data.read_bytes()).hexdigest()
    manifest = {"recipe": "unit-test", "splits": {"test": {"sha256": test_hash}},
                "provenance": "fabricated synthetic paragraphs" if synthetic else "private human documents"}
    data.with_name("manifest.json").write_text(json.dumps(manifest))
    captures = tmp_path / "captures"
    captures.mkdir()
    predictions = captures / "example.predictions.jsonl"
    predictions.write_text(json.dumps(captured([entity(0, 2)])[0]) + "\n")
    metadata = {"data_sha256": test_hash, "prediction_sha256": hashlib.sha256(predictions.read_bytes()).hexdigest()}
    predictions.with_name("example.metadata.json").write_text(json.dumps(metadata))
    output = tmp_path / "comparison.json"
    return data, captures, output


def test_cli_requires_explicit_synthetic_provenance_to_echo_failure_examples(tmp_path, monkeypatch):
    data, captures, output = write_cli_fixture(tmp_path, synthetic=False)
    monkeypatch.setattr(sys, "argv", ["compare", "--data", str(data), "--captures", str(captures),
                                     "--output", str(output), "--include-examples"])
    with pytest.raises(ValueError, match="fabricated synthetic"):
        main()
    assert not output.exists()


def test_cli_explicit_synthetic_example_flag_includes_source_but_default_excludes_it(tmp_path, monkeypatch, capsys):
    data, captures, output = write_cli_fixture(tmp_path, synthetic=True)
    argv = ["compare", "--data", str(data), "--captures", str(captures), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    main()
    normal = json.loads(output.read_text())
    assert normal["models"]["example"]["examples"] is None
    assert corpus()[0]["text"] not in output.read_text()
    monkeypatch.setattr(sys, "argv", argv + ["--include-examples"])
    main()
    included = json.loads(output.read_text())
    assert included["models"]["example"]["examples"]["boundary_mismatch"][0]["source"] == corpus()[0]["text"]
    capsys.readouterr()


@pytest.mark.parametrize("changed", ["corpus", "predictions", "metadata"])
def test_cli_rejects_mutated_files_or_unproven_identical_test_hashes(tmp_path, monkeypatch, changed):
    data, captures, output = write_cli_fixture(tmp_path, synthetic=True)
    if changed == "corpus":
        data.write_text(data.read_text() + "\n")
    elif changed == "predictions":
        predictions = captures / "example.predictions.jsonl"
        predictions.write_text(predictions.read_text() + "\n")
    else:
        metadata = captures / "example.metadata.json"
        metadata.write_text(json.dumps({"data_sha256": "incorrect-hash"}))
    monkeypatch.setattr(sys, "argv", ["compare", "--data", str(data), "--captures", str(captures), "--output", str(output)])
    with pytest.raises(ValueError):
        main()
    assert not output.exists()
