"""External baseline contracts preserve boundaries and hide evaluator gold."""
from __future__ import annotations

import json

import pytest

from tools.pii_baselines.gliner_models import CONFIGS, canonicalize, load_inputs


def test_inputs_project_away_gold_and_unknown_training_metadata(tmp_path):
    source = tmp_path / "test.jsonl"
    source.write_text(json.dumps({"id": "sample", "text": "😀김민수", "spans": [{"start": 1, "end": 4, "type": "PERSON"}],
                                  "family": "heldout", "secret_gold": "must not reach the detector"}, ensure_ascii=False) + "\n")
    assert load_inputs(source) == [{"id": "sample", "text": "😀김민수"}]


def test_native_span_mapping_preserves_character_boundaries_confidence_and_distinct_labels():
    # Emoji occupies one Python character and four UTF-8 bytes; mapping must
    # leave model character offsets intact, not convert them to byte offsets.
    text = "😀김민수 900101-1234567"
    raw = [{"start": 1, "end": 4, "label": "person", "score": .9, "text": "김민수"},
           {"start": 5, "end": 19, "label": "national id number", "score": .7},
           {"start": 5, "end": 19, "label": "social security number", "score": .8}]
    spans, native = canonicalize(raw, CONFIGS["gliner"]["labels"], len(text))
    assert spans == [{"start": 1, "end": 4, "type": "PERSON", "score": .9},
                     {"start": 5, "end": 19, "type": "IDENTIFIER", "score": .8}]
    assert len(native) == 3
    assert native[1]["label"] == "national id number" and native[2]["label"] == "social security number"
    assert all(set(span) == {"start", "end", "label", "score"} for span in native)


@pytest.mark.parametrize("changes", [{"start": True}, {"start": .5}, {"start": -1}, {"end": 11},
                                     {"end": 0}, {"label": "unexpected"}, {"score": float("nan")},
                                     {"score": 1.1}])
def test_invalid_native_prediction_does_not_silently_become_a_canonical_span(changes):
    entity = {"start": 0, "end": 3, "label": "person", "score": .8, **changes}
    with pytest.raises(ValueError):
        canonicalize([entity], CONFIGS["gliner"]["labels"], 10)


def test_frozen_public_schema_covers_all_classes_without_dataset_specific_prompts():
    for config in CONFIGS.values():
        assert set(config["labels"].values()) == {"PERSON", "ADDRESS", "PHONE", "EMAIL", "IDENTIFIER"}
        assert config["threshold_source"].startswith("https://huggingface.co/")
        assert len(config["revision"]) == 40
        assert "ko" not in config["pii_training_languages"]
    assert CONFIGS["gliner"]["threshold"] == .3
    assert CONFIGS["gliner2"]["threshold"] == .5


def test_duplicate_input_ids_are_rejected_before_model_loading(tmp_path):
    source = tmp_path / "test.jsonl"
    source.write_text('{"id":"repeated","text":"first"}\n{"id":"repeated","text":"second"}\n')
    with pytest.raises(ValueError, match="duplicate"):
        load_inputs(source)
