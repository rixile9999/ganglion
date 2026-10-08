"""PII contracts and bounded document execution, without a model download.

The literal detector makes execution assertions independent of recognition
quality: a detector's missed span must not be mistaken for an executor bug.
"""
from __future__ import annotations

from io import StringIO
import base64
import json
from pathlib import Path
import re

import pytest

from ganglion.adapters.documents import windows
from ganglion.domains.pii.pipeline import Cancelled, run_document
from ganglion.domains.pii.rules import RulesDetector
from ganglion.domains.pii.types import ENTITY_TYPES, Span, interpret, reconcile


class LiteralDetector:
    backend = "test-literals"
    score_kind = "unscored"

    def __init__(self, literals):
        self.literals = literals
        self.seen = []

    def detect(self, text):
        self.seen.append(text)
        found = []
        for literal, kind in self.literals.items():
            position = 0
            while (position := text.find(literal, position)) >= 0:
                found.append(Span(position, position + len(literal), kind))
                position += len(literal)
        return found


class BoundedReader(StringIO):
    """Fail immediately if a streaming reader asks for the whole document."""

    def __init__(self, text, maximum):
        super().__init__(text)
        self.maximum = maximum
        self.requested = []

    def read(self, size=-1):
        assert 0 < size <= self.maximum
        self.requested.append(size)
        return super().read(size)


def _execute(tmp_path, text, detector, **kwargs):
    from ganglion.adapters.recovery import generate_keypair

    source = tmp_path / "source.txt"
    source.write_bytes(text.encode("utf-8"))
    keys = generate_keypair()
    output = tmp_path / "output"
    result = run_document(source, output, detector, public_key=keys["public_key"],
                          document_id="test-document", **kwargs)
    return source, output, keys, result


@pytest.mark.parametrize("length", [0, 1, 31, 32, 63, 64, 65, 127, 4097])
@pytest.mark.parametrize("overlap", [0, 1, 15, 31])
def test_windows_reconstruct_every_character_with_bounded_reads(length, overlap):
    text = ("한😀e\u0301\r\nΩ文" * (length // 8 + 1))[:length]
    reader = BoundedReader(text, maximum=65)
    parts = list(windows(reader, window_chars=64, overlap_chars=overlap))
    reconstructed = ""
    position = 0
    for part in parts:
        assert part.start == position
        assert part.text == text[part.start:part.start + len(part.text)]
        assert 0 < len(part.text) <= 64
        assert part.start < part.commit_until <= part.start + len(part.text)
        reconstructed += part.text[:part.commit_until - part.start]
        position = part.commit_until
    assert reconstructed == text
    assert not parts or (parts[-1].final and sum(part.final for part in parts) == 1)


def test_window_token_budget_adapts_overlap_without_skipping_text():
    text = "한😀e\u0301\r\n文" * 100
    count = lambda value: len(value) * 2
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, token_count=count, max_tokens=40))
    assert all(count(part.text) <= 40 for part in parts)
    assert "".join(part.text[:part.commit_until - part.start] for part in parts) == text
    assert len(parts) > len(text) // 64


def _assert_exact_semantic_coordinates(text, parts):
    position = 0
    for part in parts:
        assert part.start == position
        assert part.text == text[part.start:part.start + len(part.text)]
        assert part.start < part.commit_until <= part.start + len(part.text)
        position = part.commit_until
    reconstructed = "".join(part.text[:part.commit_until - part.start] for part in parts)
    assert reconstructed == text
    assert not parts or (parts[-1].final and sum(part.final for part in parts) == 1)


@pytest.mark.parametrize("separator", ["\n\n", "\r\n\r\n"])
def test_semantic_windows_follow_first_paragraph_boundary_without_tail_overlap(separator):
    first = "첫 문단의 이름은 김민수입니다." + separator
    second = "두 번째 문단 😀 e\u0301 中文입니다." + separator
    text = first + second + "다" * 150
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, strategy="semantic"))
    assert parts[0].text == first
    assert parts[0].commit_until == len(first)
    assert parts[1].text == second
    assert parts[1].start == len(first)
    assert parts[1].commit_until == len(first + second)
    _assert_exact_semantic_coordinates(text, parts)


def test_semantic_windows_keep_a_paragraph_intact_ahead_of_inner_sentence_boundary():
    paragraph = "앞 문장입니다.\n뒷 문장입니다!\n\n"
    text = paragraph + "다" * 100
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, strategy="semantic"))
    assert parts[0].text == paragraph
    assert parts[0].commit_until == len(paragraph)
    _assert_exact_semantic_coordinates(text, parts)


@pytest.mark.parametrize("ending", [".\n", "!\r\n", "?\n", "。\n", "！\n", "？\n"])
def test_semantic_windows_use_sentence_boundaries_when_no_paragraph_is_available(ending):
    sentence = "첫 문장 내용입니다" + ending
    text = sentence + "긴다음문장" * 50
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, strategy="semantic"))
    assert parts[0].text == sentence
    assert parts[0].commit_until == len(sentence)
    _assert_exact_semantic_coordinates(text, parts)


def test_semantic_windows_fall_back_to_original_fixed_overlap_for_unbroken_text():
    text = "가😀e\u0301文" * 50
    fixed = list(windows(StringIO(text), window_chars=64, overlap_chars=16))
    semantic = list(windows(BoundedReader(text, 65), window_chars=64,
                            overlap_chars=16, strategy="semantic"))
    assert semantic == fixed
    assert semantic[0].commit_until == 48
    _assert_exact_semantic_coordinates(text, semantic)


def test_semantic_window_end_does_not_turn_a_domain_dot_into_a_sentence_end():
    # The maximum prefix ends after '.', but the actual document continues
    # with a domain suffix: a hard split must retain the overlap context.
    text = "a" * 63 + ".com" + "x" * 80
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, strategy="semantic"))
    assert parts[0].text == "a" * 63 + "."
    assert parts[0].commit_until == 48
    _assert_exact_semantic_coordinates(text, parts)


@pytest.mark.parametrize("linebreak", ["\n", "\r\n"])
def test_semantic_line_fallback_preserves_newlines_in_an_oversized_paragraph(linebreak):
    first_line = "가" * 30 + linebreak
    text = first_line + "나" * 100
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, strategy="semantic"))
    assert parts[0].text == first_line
    assert parts[0].commit_until == len(first_line)
    _assert_exact_semantic_coordinates(text, parts)


def test_semantic_windows_enforce_token_budget_before_choosing_a_text_boundary():
    text = "한" * 24 + "\r\n\r\n" + "😀e\u0301文" * 100
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, token_count=lambda value: len(value) * 2,
                         max_tokens=40, strategy="semantic"))
    assert all(len(part.text) * 2 <= 40 for part in parts)
    assert parts[0].text == "한" * 20
    assert parts[0].commit_until < len(parts[0].text)
    _assert_exact_semantic_coordinates(text, parts)


@pytest.mark.parametrize("text", ["", "단일 짧은 문장입니다!\r\n", "😀e\u0301 中文\r\n\r\n"])
def test_semantic_final_short_input_is_exact_without_padding_or_duplicate_tail(text):
    parts = list(windows(BoundedReader(text, 65), window_chars=64,
                         overlap_chars=16, strategy="semantic"))
    assert len(parts) == (1 if text else 0)
    if parts:
        assert parts[0].text == text
        assert parts[0].commit_until == len(text)
    _assert_exact_semantic_coordinates(text, parts)


def test_unknown_document_strategy_is_rejected():
    with pytest.raises(ValueError):
        list(windows(StringIO("문서"), strategy="unknown"))


@pytest.mark.parametrize("options", [{"window_chars": 31}, {"overlap_chars": -1},
                                      {"window_chars": 64, "overlap_chars": 32},
                                      {"token_count": len, "max_tokens": 0}])
def test_invalid_window_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        list(windows(StringIO("문서"), **options))


def test_unicode_crlf_round_trip_consistent_pseudonyms_and_literal_collision(tmp_path):
    from ganglion.adapters.recovery import restore_document

    text = ("원래 표기 <PERSON_1>\r\n이름: 김민수\r\n"
            "김민수님께 e\u0301 / 😀 / Ω / 中文\r\n"
            "전화: 010-1234-5678\r\n메일: minsu@example.com\r\n"
            "주소: 서울특별시 강남구 테헤란로 123\r\n"
            "식별: 900101-1234567\r\n마지막 김민수\r\n")
    detector = LiteralDetector({"김민수": "PERSON", "010-1234-5678": "PHONE",
                                "minsu@example.com": "EMAIL",
                                "서울특별시 강남구 테헤란로 123": "ADDRESS",
                                "900101-1234567": "IDENTIFIER"})
    source, output, keys, result = _execute(tmp_path, text, detector,
                                          window_chars=96, overlap_chars=32)
    rendered = (output / "document.txt").read_bytes().decode()
    assert rendered.count("<PERSON_1>") == 4  # Three edits plus one literal.
    assert "<PERSON_2>" not in rendered
    for original in detector.literals:
        assert original not in rendered
    assert "e\u0301 / 😀 / Ω / 中文\r\n" in rendered
    assert result["edits"] == 7
    assert result["processed_bytes"] == len(source.read_bytes())
    assert result["input_tokens"] is None and result["output_tokens"] == 0
    assert result["entity_counts"] == {"PERSON": 3, "ADDRESS": 1, "PHONE": 1,
                                       "EMAIL": 1, "IDENTIFIER": 1}
    restored = tmp_path / "restored.txt"
    info = restore_document(output / "document.txt", output / "recovery.bin",
                            keys["private_key"], restored)
    assert restored.read_bytes() == source.read_bytes()
    assert info["edits"] == 7
    assert restored.stat().st_mode & 0o777 == 0o600
    assert (output / "recovery.bin").stat().st_mode & 0o777 == 0o600
    assert not (output / ".lookup.sqlite").exists()


@pytest.mark.parametrize("shift", range(96))
def test_detector_spans_at_every_window_boundary_are_edited_once(tmp_path, shift):
    from ganglion.adapters.recovery import restore_document

    text = "가" * shift + "김민수" + "나" * 5 + "010-1234-5678" + "다" * 96
    detector = LiteralDetector({"김민수": "PERSON", "010-1234-5678": "PHONE"})
    source, output, keys, result = _execute(tmp_path, text, detector,
                                          window_chars=64, overlap_chars=16)
    rendered = (output / "document.txt").read_bytes().decode()
    assert rendered == "가" * shift + "<PERSON_1>" + "나" * 5 + "<PHONE_1>" + "다" * 96
    assert result["edits"] == 2
    assert max(map(len, detector.seen)) <= 64
    restored = tmp_path / "restored.txt"
    restore_document(output / "document.txt", output / "recovery.bin", keys["private_key"], restored)
    assert restored.read_bytes() == source.read_bytes()


def test_long_document_inspects_all_windows_and_reports_only_real_input_tokens(tmp_path):
    class CountingDetector(LiteralDetector):
        def token_count(self, text):
            return len(text) * 2

    text = "공개문장 " * 6000 + "이름: 김민수\r\n"
    detector = CountingDetector({"김민수": "PERSON"})
    source, output, _keys, result = _execute(tmp_path, text, detector,
                                           window_chars=64, overlap_chars=16, max_tokens=96)
    assert all(len(value) * 2 <= 96 for value in detector.seen)
    assert result["input_tokens"] == sum(len(value) * 2 for value in detector.seen)
    assert result["units"] == len(detector.seen) > 500
    assert result["processed_bytes"] == len(source.read_bytes())
    assert result["edits"] == 1
    assert (output / "document.txt").read_bytes().endswith(b"<PERSON_1>\r\n")


def test_semantic_pipeline_preserves_document_bytes_and_edits_every_paragraph_once(tmp_path):
    from ganglion.adapters.recovery import restore_document

    paragraph = "공개 😀e\u0301\r\n이름: 김민수\r\n\r\n"
    text = paragraph * 20
    detector = LiteralDetector({"김민수": "PERSON"})
    source, output, keys, result = _execute(tmp_path, text, detector,
                                          window_chars=64, overlap_chars=16,
                                          strategy="semantic")
    assert result["edits"] == 20
    assert (output / "document.txt").read_bytes() == paragraph.replace("김민수", "<PERSON_1>").encode() * 20
    assert all(len(part) <= 64 for part in detector.seen)
    # Complete paragraph cuts must not send the previous paragraph's tail as
    # context again: the repeated name appears once per actual paragraph.
    assert sum(part.count("김민수") for part in detector.seen) == 20
    restored = tmp_path / "restored.txt"
    restore_document(output / "document.txt", output / "recovery.bin", keys["private_key"], restored)
    assert restored.read_bytes() == source.read_bytes()


def test_partially_detected_long_entity_is_stitched_across_three_windows(tmp_path):
    from ganglion.adapters.recovery import restore_document

    class ClippedRunDetector:
        backend = "test-clipped-spans"
        score_kind = "unscored"

        def detect(self, text):
            return [Span(match.start(), match.end(), "PERSON")
                    for match in re.finditer("민+", text)]

    text = "가" * 20 + "민" * 100 + "다" * 100
    source, output, keys, result = _execute(tmp_path, text, ClippedRunDetector(),
                                          window_chars=64, overlap_chars=16)
    assert result["edits"] == 1
    assert (output / "document.txt").read_text() == "가" * 20 + "<PERSON_1>" + "다" * 100
    restored = tmp_path / "restored.txt"
    restore_document(output / "document.txt", output / "recovery.bin", keys["private_key"], restored)
    assert restored.read_bytes() == source.read_bytes()


def test_entity_exceeding_bounded_stitching_budget_fails_without_success_manifest(tmp_path):
    from ganglion.adapters.recovery import generate_keypair

    class ClippedRunDetector:
        backend = "test-clipped-spans"
        score_kind = "unscored"

        def detect(self, text):
            return [Span(match.start(), match.end(), "PERSON")
                    for match in re.finditer("민+", text)]

    source = tmp_path / "source.txt"
    source.write_text("가" * 20 + "민" * 300 + "다" * 100)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="unresolved document boundary"):
        run_document(source, output, ClippedRunDetector(),
                     public_key=generate_keypair()["public_key"], document_id="oversized-span",
                     window_chars=64, overlap_chars=16)
    assert not (output / "manifest.json").exists()
    assert not (output / ".lookup.sqlite").exists()


def test_plan_mode_uses_utf8_byte_offsets_without_keys_or_originals(tmp_path):
    text = "😀e\u0301 이름: 김민수\r\n메일 minsu@example.com\r\n"
    source = tmp_path / "source.txt"
    source.write_bytes(text.encode())
    output = tmp_path / "plan"
    detector = LiteralDetector({"김민수": "PERSON", "minsu@example.com": "EMAIL"})
    result = run_document(source, output, detector, public_key=None,
                          document_id="plan-document", execute=False,
                          window_chars=64, overlap_chars=16)
    records = [json.loads(line) for line in (output / "plan.jsonl").read_text().splitlines()]
    assert len(records) == 2
    originals = ["김민수", "minsu@example.com"]
    for record, original in zip(records, originals):
        start = len(text[:text.index(original)].encode())
        assert record["source_span"] == [start, start + len(original.encode())]
        assert source.read_bytes()[slice(*record["source_span"])] == original.encode()
        assert record.keys() == {"operation", "source_span", "type"}
    assert result["mode"] == "plan"
    assert not (output / "recovery.bin").exists()
    assert not (output / "document.txt").exists()
    assert not (output / ".lookup.sqlite").exists()
    for artifact in output.iterdir():
        content = artifact.read_bytes()
        for original in originals:
            assert original.encode() not in content
            assert base64.b64encode(original.encode()) not in content


def test_trace_and_manifest_exclude_pii_keys_and_original_hash(tmp_path):
    from hashlib import sha256

    text = "이름: 김민수\r\n민감한 메일: minsu@example.com\r\n"
    detector = LiteralDetector({"김민수": "PERSON", "minsu@example.com": "EMAIL"})
    source, output, keys, _result = _execute(tmp_path, text, detector)
    visible = (output / "trace.jsonl").read_bytes() + (output / "manifest.json").read_bytes()
    for value in [*detector.literals, keys["public_key"], keys["private_key"],
                  sha256(source.read_bytes()).hexdigest()]:
        assert value.encode() not in visible
    assert all("source" not in row for row in [json.loads((output / "manifest.json").read_text())])
    assert len((output / "trace.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("text", ["", "공개 정보만 담은 문장\r\n😀e\u0301"])
def test_empty_and_pii_free_documents_remain_exactly_recoverable(tmp_path, text):
    from ganglion.adapters.recovery import restore_document

    source, output, keys, result = _execute(tmp_path, text, LiteralDetector({}))
    assert (output / "document.txt").read_bytes() == source.read_bytes()
    assert result["edits"] == 0
    restored = tmp_path / "restored.txt"
    restore_document(output / "document.txt", output / "recovery.bin", keys["private_key"], restored)
    assert restored.read_bytes() == source.read_bytes()


def test_cancellation_never_publishes_success_and_removes_lookup(tmp_path):
    from ganglion.adapters.recovery import generate_keypair

    source = tmp_path / "source.txt"
    source.write_bytes(("이름: 김민수\r\n" * 100).encode())
    output = tmp_path / "cancelled"
    events = []
    with pytest.raises(Cancelled):
        run_document(source, output, LiteralDetector({"김민수": "PERSON"}),
                     public_key=generate_keypair()["public_key"], document_id="cancelled",
                     window_chars=64, overlap_chars=16, progress=events.append,
                     cancelled=lambda: bool(events))
    assert len(events) == 1
    assert not (output / "manifest.json").exists()
    assert not (output / ".lookup.sqlite").exists()


def test_reversible_execution_requires_public_key_before_processing(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("이름: 김민수")
    detector = LiteralDetector({"김민수": "PERSON"})
    with pytest.raises(ValueError, match="public key"):
        run_document(source, tmp_path / "output", detector, public_key=None, document_id="test")
    assert detector.seen == []


@pytest.mark.parametrize("span", [Span(-1, 2, "PERSON"), Span(0, 11, "PERSON"),
                                  Span(2, 2, "PERSON"), Span(0, 2, "UNKNOWN"),
                                  Span(0.5, 2, "PERSON"), Span(False, 2, "PERSON"),
                                  Span(0, True, "PERSON"), Span(0, 2, "PERSON", float("nan")),
                                  Span(0, 2, "PERSON", float("inf")), Span(0, 2, "PERSON", 1.1),
                                  Span(0, 2, "PERSON", probabilities={"PERSON": 1.0})])
def test_interpreter_rejects_invalid_contract_values(span):
    with pytest.raises(ValueError):
        interpret("0123456789", [span])


def test_interpreter_trims_whitespace_and_unions_overlapping_entities():
    distribution = {kind: 0.0 for kind in (*ENTITY_TYPES, "NOT_PII")}
    distribution["PERSON"] = 1.0
    text = "  김민수  1234567 "
    final, changes = interpret(text, [Span(0, 7, "PERSON", .8, probabilities=distribution),
                                     Span(4, 14, "IDENTIFIER", .9)])
    assert [(span.start, span.end, span.kind) for span in final] == [(2, 14, "IDENTIFIER")]
    assert {change["rule"] for change in changes} == {"trim-span-whitespace-v1", "merge-overlapping-spans-v1"}
    assert final[0].score == .9
    untouched, _ = interpret(" 김민수 ", [Span(0, 5, "PERSON", .8, probabilities=distribution)])
    assert untouched[0].probabilities == distribution


def test_reconciliation_does_not_merge_adjacent_entities_or_need_source_text():
    final, changes = reconcile(10**9, [Span(1, 3, "PERSON"), Span(3, 5, "PHONE")])
    assert [(span.start, span.end) for span in final] == [(1, 3), (3, 5)]
    assert changes == []


def test_rules_find_korean_english_contacts_without_inventing_probability():
    text = ("성명: 김민수\r\n김민수님께 전달\r\nName: Alice Johnson\r\n"
            "전화: 010-1234-5678\r\nEMAIL: alice@example.org\r\n"
            "번호: 900101-1234567\r\n주소: 서울특별시 강남구 테헤란로 123\r\n")
    raw = RulesDetector().detect(text)
    final, _ = interpret(text, raw)
    found = {(span.kind, text[span.start:span.end]) for span in final}
    assert {( "PERSON", "김민수"), ("PERSON", "Alice Johnson"),
            ("PHONE", "010-1234-5678"), ("EMAIL", "alice@example.org"),
            ("IDENTIFIER", "900101-1234567"),
            ("ADDRESS", "서울특별시 강남구 테헤란로 123")} <= found
    assert all(span.score is None and not span.probabilities for span in raw)


def test_rules_do_not_classify_ordinary_korean_and_english_prose_as_pii():
    text = "오늘 날씨는 맑습니다. 회의 내용을 요약해 주세요.\nA simple public note about the weather."
    assert RulesDetector().detect(text) == []
