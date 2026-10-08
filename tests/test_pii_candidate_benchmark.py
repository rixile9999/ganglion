"""Audit before first test access; distinguish proposal and classifier errors."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from ganglion.domains.pii.candidates import Candidate, CandidateBatch, CandidateConfig
from ganglion.domains.pii.types import Span
from tools.benchmark_pii_candidates import (
    FREEZE_NAME, _fixture_rows, capture_predictions, evaluate_captures,
    freeze_checkpoint, load_frozen_test, proposal_analysis,
)


def entity(start, end, kind="PERSON"):
    return {"start": start, "end": end, "type": kind}


def proposal(ranges, *, overflow=False, proposed_count=None):
    return {"complete": not overflow, "overflow": overflow,
            "proposed_count": len(ranges) if proposed_count is None else proposed_count,
            "candidates": [{"start": start, "end": end} for start, end in ranges]}


@pytest.fixture
def checkpoint(tmp_path):
    directory = tmp_path / "model"
    directory.mkdir()
    metadata = {"ready": True, "training_complete": True, "selection_split": "validation",
                "candidate_config": asdict(CandidateConfig()), "max_tokens": 128}
    (directory / "model.json").write_text(json.dumps(metadata))
    (directory / "candidate_heads.safetensors").write_bytes(b"frozen-test-weights")
    (directory / "vocab.json").write_text('{"가": 2}')
    data = tmp_path / "data"
    data.mkdir()
    text = json.dumps({"id": "tiny", "text": "가", "spans": [entity(0, 1)]}, ensure_ascii=False) + "\n"
    (data / "test.jsonl").write_text(text)
    manifest = {"splits": {"train": {"count": 2, "sha256": "train-hash"},
                            "validation": {"count": 1, "sha256": "validation-hash"},
                            "test": {"count": 1, "sha256": hashlib.sha256(text.encode()).hexdigest()}}}
    (data / "manifest.json").write_text(json.dumps(manifest))
    return directory, data


def test_explicit_ready_and_trained_metadata_required_before_freeze(checkpoint):
    directory, data = checkpoint
    with pytest.raises(ValueError, match="--ready"):
        freeze_checkpoint(directory, data / "manifest.json")
    assert not (directory / FREEZE_NAME).exists()
    metadata = json.loads((directory / "model.json").read_text())
    metadata["training_complete"] = False
    (directory / "model.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="completed training"):
        freeze_checkpoint(directory, data / "manifest.json", ready=True)
    assert not (directory / FREEZE_NAME).exists()


def test_freeze_happens_without_opening_test_and_is_reused_unchanged(checkpoint, monkeypatch):
    directory, data = checkpoint
    original = Path.open

    def forbidden_test_read(path, *args, **kwargs):
        if path == data / "test.jsonl":
            pytest.fail("test must not be read during configuration freeze")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_test_read)
    audit = freeze_checkpoint(directory, data / "manifest.json", ready=True)
    assert (directory / FREEZE_NAME).exists()
    assert audit["test_read_before_freeze"] is audit["test_forward_before_freeze"] is False
    assert audit["artifact_sha256"]["candidate"]["vocab.json"]
    assert audit == freeze_checkpoint(directory, data / "manifest.json", ready=True)


@pytest.mark.parametrize("file", ["candidate_heads.safetensors", "model.json", "vocab.json"])
def test_weight_config_and_character_vocabulary_changes_invalidate_pretest_freeze(checkpoint, file):
    directory, data = checkpoint
    freeze_checkpoint(directory, data / "manifest.json", ready=True)
    path = directory / file
    if file == "model.json":
        value = json.loads(path.read_text())
        value["threshold"] = .7
        path.write_text(json.dumps(value))
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed after"):
        freeze_checkpoint(directory, data / "manifest.json", ready=True)


def test_shared_source_checkpoint_changes_are_detected(checkpoint, tmp_path):
    directory, data = checkpoint
    source = tmp_path / "source-model"
    source.mkdir()
    (source / "model.json").write_text("{}")
    (source / "heads.safetensors").write_bytes(b"original-backbone-readout")
    value = json.loads((directory / "model.json").read_text())
    value["source_checkpoint"] = str(source)
    (directory / "model.json").write_text(json.dumps(value))
    audit = freeze_checkpoint(directory, data / "manifest.json", ready=True)
    assert "source" in audit["artifact_sha256"]
    (source / "heads.safetensors").write_bytes(b"different-source")
    with pytest.raises(ValueError, match="changed after"):
        freeze_checkpoint(directory, data / "manifest.json", ready=True)


@pytest.mark.parametrize("changes", [{"selection_split": "test"}, {"test_used_for_tuning": True},
                                     {"training_splits_sha256": {"test": "test-hash"}},
                                     {"training_splits_sha256": {"train": "wrong-hash"}}])
def test_test_selected_or_mismatched_training_provenance_is_rejected(checkpoint, changes):
    directory, data = checkpoint
    value = json.loads((directory / "model.json").read_text())
    value.update(changes)
    (directory / "model.json").write_text(json.dumps(value))
    with pytest.raises(ValueError):
        freeze_checkpoint(directory, data / "manifest.json", ready=True)
    assert not (directory / FREEZE_NAME).exists()


def test_frozen_reader_rejects_changed_test_data(checkpoint):
    directory, data = checkpoint
    audit = freeze_checkpoint(directory, data / "manifest.json", ready=True)
    rows = load_frozen_test(data, audit)
    assert rows[0]["id"] == "tiny"
    path = data / "test.jsonl"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="differs"):
        load_frozen_test(data, audit)


class FakeClassifier:
    device = "cpu"
    metadata = {"candidate_config": asdict(CandidateConfig())}

    def __init__(self):
        self.seen = []
        self.interpreted = 0

    def detect_batch(self, texts):
        assert all(type(text) is str for text in texts)
        self.seen.extend(texts)
        self.batch = texts
        return [[Span(0, 3, "PERSON", .9), Span(0, 4, "ADDRESS", .6)] for text in texts]

    def get_batch_diagnostics(self):
        return [{"candidates": 2, "encoded_tokens": 3, "context_characters": len(text),
                 "fallback_count": 0, "source": text, "private_key": "NEVER_PERSIST"} for text in self.batch]

    def interpret(self, text, spans):
        self.interpreted += 1
        # A domain-specific choice preserves the exact high-scoring name;
        # generic overlap union would incorrectly produce ADDRESS(0,4).
        return [spans[0]], [{"rule": "fixture-selection", "source": text}]

    def propose(self, text):
        return CandidateBatch("candidate-v1", "character", len(text),
                              (Candidate("c0:3", 0, 3, text[:3], "PRIVATE_LEFT", "PRIVATE_RIGHT", None,
                                         ("PERSON",), ("fixture-source",)),), False, 1)


def test_capture_calls_custom_interpreter_text_only_and_keeps_ir_source_free():
    rows = [{"id": "a", "text": "김민수 공개", "spans": [entity(0, 3)], "private_gold": "hidden label"},
            {"id": "b", "text": "박지민 공개", "spans": [entity(0, 3)]}]
    detector = FakeClassifier()
    raw, final, ir, timing = capture_predictions(rows, detector, batch_size=1)
    assert detector.seen == [row["text"] for row in rows]
    assert detector.interpreted == 2
    assert all(len(row["spans"]) == 2 for row in raw)
    assert all(len(row["spans"]) == 1 and row["spans"][0]["type"] == "PERSON" for row in final)
    serialized = json.dumps({"raw": raw, "final": final, "ir": ir}, ensure_ascii=False)
    for private in ("김민수", "박지민", "PRIVATE_LEFT", "PRIVATE_RIGHT", "NEVER_PERSIST", "hidden label"):
        assert private not in serialized
    assert timing["pipeline_ms"] >= timing["proposal_encoder_classification_ms"]
    assert timing["documents"] == 2
    measured = evaluate_captures(rows, raw, final, ir)
    assert measured["raw_classifier"]["exact"]["f1"] == pytest.approx(2 / 3)
    assert measured["classifier_with_interpreter"]["exact"]["f1"] == 1.0
    assert measured["input_statistics"]["encoded_tokens"]["total"] == 6
    assert measured["input_statistics"]["encoded_tokens"]["max"] == 3
    assert measured["input_statistics"]["generated_output_tokens"] == 0


def test_proposal_recall_distinguishes_exact_containment_misses_and_classifier_errors():
    rows = [{"id": "a", "text": "abcdefghijklmnop", "spans": [entity(1, 3), entity(5, 7), entity(10, 12)]}]
    ir = [{"id": "a", "proposal": proposal([(0, 4), (5, 7)])}]
    raw = [{"id": "a", "spans": []}]
    result = proposal_analysis(rows, ir, raw, raw)
    assert result["exact_span_proposal_recall"] == pytest.approx(1 / 3)
    assert result["containment_proposal_recall"] == pytest.approx(2 / 3)
    assert result["final_miss_root_causes"] == {"proposal_boundary_mismatch": 1,
                                               "classifier_rejection_or_type_error": 1, "proposal_missed_entity": 1}


def test_interpreter_regression_is_not_attributed_to_proposal_generation():
    rows = [{"id": "a", "text": "abcdef", "spans": [entity(1, 3)]}]
    raw, final = [{"id": "a", "spans": [entity(1, 3)]}], [{"id": "a", "spans": [entity(0, 4)]}]
    result = proposal_analysis(rows, [{"id": "a", "proposal": proposal([(1, 3)])}], raw, final)
    assert result["exact_span_proposal_recall"] == 1.0
    assert result["final_miss_root_causes"] == {"interpreter_regression": 1}


def test_overflow_coverage_is_explicitly_lower_bound_and_missing_gold_not_called_exhaustively_missed():
    rows = [{"id": "a", "text": "abcdef", "spans": [entity(3, 5)]}]
    empty = [{"id": "a", "spans": []}]
    result = proposal_analysis(rows, [{"id": "a", "proposal": proposal([(0, 1)], overflow=True, proposed_count=1000)}], empty, empty)
    assert result["coverage_is_lower_bound"] is True
    assert result["overflow_documents"] == 1 and result["proposal_mean"] == 1000
    assert result["exact_span_proposal_recall"] == 0.0
    assert result["final_miss_root_causes"] == {"proposal_coverage_unavailable_overflow": 1}


def test_strict_false_positives_are_separated_from_non_pii_false_positives():
    rows = [{"id": "a", "text": "abcdefghijklmnop", "spans": [entity(1, 4)]}]
    predicted = [{"id": "a", "spans": [entity(1, 4, "ADDRESS"), entity(8, 10)]}]
    result = proposal_analysis(rows, [{"id": "a", "proposal": proposal([(1, 4), (8, 10)])}], predicted, predicted)
    document = result["documents"][0]
    assert document["raw"]["strict_false_positives"] == 2
    assert document["raw"]["ordinary_non_pii_false_positives"] == 1
    assert document["raw"]["overlapping_boundary_or_type_false_positives"] == 1
    assert document["raw"]["exact"]["f1"] == 0.0


@pytest.mark.parametrize("capture", [[], [{"id": "wrong", "proposal": proposal([])}]])
def test_candidate_diagnostic_id_mismatches_cannot_change_denominators(capture):
    rows = [{"id": "a", "text": "abcdef", "spans": [entity(1, 3)]}]
    empty = [{"id": "a", "spans": []}]
    with pytest.raises(ValueError, match="align"):
        proposal_analysis(rows, capture, empty, empty)


def test_long_document_fixture_selects_complete_paragraphs_for_exact_requested_gold_count():
    rows = [{"id": "five", "text": "abcdef", "spans": [entity(i, i + 1) for i in range(5)]},
            {"id": "two", "text": "ab", "spans": [entity(0, 1), entity(1, 2)]},
            {"id": "three", "text": "abc", "spans": [entity(i, i + 1) for i in range(3)]}]
    chosen = _fixture_rows(rows, 7)
    assert [row["id"] for row in chosen] == ["five", "two"]
    assert sum(len(row["spans"]) for row in chosen) == 7
    with pytest.raises(ValueError):
        _fixture_rows(rows, 1)
