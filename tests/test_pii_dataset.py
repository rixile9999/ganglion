"""Versioned synthetic benchmark partitions disclose and prevent leakage."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re

import pytest

from ganglion.domains.pii.dataset import (
    DIVERSE_FAMILIES, DIVERSE_NEGATIVES, DIVERSE_POOLS, dataset_manifest, generate, main,
)
from ganglion.domains.pii.types import ENTITY_TYPES, Span


@pytest.fixture(scope="module")
def diverse_partitions():
    return {split: generate(6000 if split == "train" else 1500, split=split,
                            seed=42, recipe="diverse-v1")
            for split in ("train", "validation", "test")}


def test_legacy_remains_default_with_unchanged_row_shape():
    default = generate(100)
    assert default == generate(100, recipe="legacy")
    assert all(row.keys() == {"id", "family", "text", "spans"} for row in default)
    assert default[0]["text"] == "내일 회의는 오후 세 시입니다. 개인정보가 없는 문장입니다."
    assert default[0]["spans"] == []
    assert default[1]["text"] == "배준호님께 연락해주세요. 연락처는 010-2424-7912입니다."
    assert default[1]["spans"] == [{"start": 0, "end": 3, "type": "PERSON"},
                                    {"start": 19, "end": 32, "type": "PHONE"}]


def test_generation_is_seeded_and_does_not_touch_global_random_state():
    import random

    state = random.getstate()
    one = generate(100, recipe="diverse-v1", seed=812)
    assert one == generate(100, recipe="diverse-v1", seed=812)
    assert one != generate(100, recipe="diverse-v1", seed=813)
    assert random.getstate() == state


def test_all_partitions_are_unique_and_have_no_cross_split_text_or_value_leakage(diverse_partitions):
    manifest = dataset_manifest(diverse_partitions, recipe="diverse-v1", seed=42)
    for rows in diverse_partitions.values():
        assert len({row["text"] for row in rows}) == len(rows)
        assert len({row["id"] for row in rows}) == len(rows)
    for audit in manifest["cross_split_overlap"].values():
        assert audit["exact_texts"] == audit["template_ids"] == 0
        assert audit["entity_values"] == dict.fromkeys(ENTITY_TYPES, 0)
    # Verify actual template contents, not only split-prefixed family IDs.
    template_sets = [{template for _language, template in DIVERSE_FAMILIES[split]}
                     | {template for _language, template in DIVERSE_NEGATIVES[split]}
                     for split in diverse_partitions]
    assert not (template_sets[0] & template_sets[1])
    assert not (template_sets[0] & template_sets[2])
    assert not (template_sets[1] & template_sets[2])


def test_every_partition_contains_all_five_classes_and_natural_language_variants(diverse_partitions):
    for split, rows in diverse_partitions.items():
        counts = Counter(span["type"] for row in rows for span in row["spans"])
        assert set(counts) == set(ENTITY_TYPES)
        assert min(counts.values()) >= 600
        assert {row["language"] for row in rows} == {"ko", "en", "mixed"}
        assert sum(not row["spans"] for row in rows) == len(rows) // 5
        assert all(row["recipe"] == "diverse-v1" for row in rows)
        assert all(row["family"] == row["template_id"] for row in rows)
        assert all(f":{split}:" in row["template_id"] for row in rows)


def test_exact_span_contract_matches_independent_entity_shapes(diverse_partitions):
    contact_patterns = {
        "EMAIL": re.compile(r"contact\d{6}\.s\d+@[a-z.]+\.example"),
        "PHONE": re.compile(r"010[- ]\d{4}[- ]\d{4}"),
        "IDENTIFIER": re.compile(r"\d{6}-1\d{6}"),
    }
    for split, rows in diverse_partitions.items():
        pools = DIVERSE_POOLS[split]
        for row in rows:
            text, cursor = row["text"], 0
            assert 0 < len(text) <= 256
            assert not re.search(r"\{[A-Z_]+\}", text)
            for span in row["spans"]:
                Span(span["start"], span["end"], span["type"]).validate(len(text))
                assert cursor <= span["start"]
                value = text[span["start"]:span["end"]]
                assert value.strip() == value
                kind = span["type"]
                if kind in contact_patterns:
                    assert contact_patterns[kind].fullmatch(value)
                elif kind == "PERSON":
                    if value[0].isascii():
                        first, last = value.split(" ")
                        assert first in pools["first_en"] and last in pools["last_en"]
                    else:
                        assert len(value) == 3 and value[0] in pools["surnames"]
                else:
                    assert kind == "ADDRESS"
                    assert (any(value.startswith(region + " ") for region in pools["regions"])
                            or any(value.endswith(", " + city) for city in pools["cities_en"]))
                cursor = span["end"]
            # A regex independently locates all printed strong-form contact
            # values: no forgotten or shifted EMAIL/PHONE/IDENTIFIER labels.
            for kind, pattern in contact_patterns.items():
                expected = {(match.start(), match.end()) for match in pattern.finditer(text)}
                actual = {(span["start"], span["end"]) for span in row["spans"] if span["type"] == kind}
                assert actual == expected


def test_negative_examples_include_sensitive_field_words_without_contact_values(diverse_partitions):
    words = ("name", "address", "phone", "email", "identifier", "personal", "성명", "이름", "주소", "번호", "개인", "사람", "전화", "전자우편", "본인", "식별")
    for rows in diverse_partitions.values():
        negatives = [row for row in rows if not row["spans"]]
        assert negatives
        assert all(":negative:" in row["template_id"] for row in negatives)
        assert all(any(word in row["text"].lower() for word in words) for row in negatives)
        assert all(not re.search(r"\d{6}-\d{7}|010[- ]\d{4}[- ]\d{4}|\S+@\S+", row["text"])
                   for row in negatives)


def test_small_default_partitions_still_cover_all_classes():
    for split in ("train", "validation", "test"):
        rows = generate(20, split=split, recipe="diverse-v1")
        assert {span["type"] for row in rows for span in row["spans"]} == set(ENTITY_TYPES)


def test_cli_writes_reproducible_hashes_and_manifest_without_changing_default_count(tmp_path, capsys):
    main(["--output", str(tmp_path), "--recipe", "diverse-v1", "--seed", "19"])
    first_output = json.loads(capsys.readouterr().out)
    assert first_output["train"] == 300
    assert first_output["recipe"] == "diverse-v1" and first_output["seed"] == 19
    manifest_raw = (tmp_path / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest["format"] == "ganglion-synthetic-dataset-v1"
    assert "synthetic" in manifest["provenance"]
    for split, count in (("train", 300), ("validation", 75), ("test", 75)):
        raw = (tmp_path / f"{split}.jsonl").read_bytes()
        assert manifest["splits"][split]["count"] == count
        assert manifest["splits"][split]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert len(raw.splitlines()) == count
    main(["--output", str(tmp_path), "--recipe", "diverse-v1", "--seed", "19"])
    assert (tmp_path / "manifest.json").read_bytes() == manifest_raw


@pytest.mark.parametrize("options", [{"count": -1}, {"count": True}, {"count": 1.5},
                                     {"count": 5, "split": "other"}, {"count": 5, "seed": True},
                                     {"count": 5, "recipe": "future"}])
def test_invalid_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        generate(**options)


def test_empty_partition_can_be_audited_without_fake_coverage():
    rows = generate(0, recipe="diverse-v1")
    manifest = dataset_manifest({"train": rows}, recipe="diverse-v1", seed=42)
    assert manifest["splits"]["train"]["entity_counts"] == {}
    assert manifest["splits"]["train"]["max_characters"] == 0
    assert manifest["splits"]["train"]["sha256"] == hashlib.sha256(b"").hexdigest()
