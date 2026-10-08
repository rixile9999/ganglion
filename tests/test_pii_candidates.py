from dataclasses import asdict

import pytest

from ganglion.domains.pii.candidates import (
    CandidateConfig,
    CandidateOverflowError,
    extract_candidates,
    prepare_candidates,
)


def values(text, **options):
    return {candidate.value: candidate for candidate in extract_candidates(text, CandidateConfig(**options))}


def test_shapes_keep_reserved_domains_and_exact_original_coordinates():
    text = '🙂\r\n메일 zoë42@privacy.example로, 전화 +82 10 2468 1357, 식별번호 850207-1234567.'
    candidates = extract_candidates(text)
    by_value = {candidate.value: candidate for candidate in candidates}
    for value, kind in [('zoë42@privacy.example', 'EMAIL'), ('+82 10 2468 1357', 'PHONE'), ('850207-1234567', 'IDENTIFIER')]:
        candidate = by_value[value]
        assert kind in candidate.type_hints
        assert text[candidate.start:candidate.end] == candidate.value
        assert candidate.start == text.index(value)
    assert 'zoë42@privacy.example로' in by_value  # an uncertain alternative, not a fixed correction


def test_names_without_roles_unicode_and_suffix_alternatives():
    text = 'Élodie Moreau와 홍성은에게 연락해 주세요.'
    proposed = values(text)
    assert 'Élodie Moreau' in proposed
    assert '홍성은' in proposed
    assert '홍성' in proposed
    assert '홍성은에게' in proposed
    assert 'PERSON' in proposed['홍성은'].type_hints


def test_address_proposes_complete_span_and_boundary_alternatives():
    text = '거주 주소 서울특별시 용산구 한강대로 73 802호입니다; residence 52 Oak Boulevard, Riverton.'
    proposed = values(text)
    assert '서울특별시 용산구 한강대로 73 802호' in proposed
    assert '서울특별시 용산구 한강대로 73' in proposed
    assert '52 Oak Boulevard, Riverton' in proposed
    assert 'ADDRESS' in proposed['52 Oak Boulevard, Riverton'].type_hints


def test_proposal_is_not_privacy_classification():
    public = values('공개 도움말 번호 4201234, 예제 이름 민지수와 private name Ada Lovelace')
    assert '4201234' in public
    assert 'Ada Lovelace' in public
    assert '민지수' in public
    # The classifier sees the nearby public/example context; extraction does not
    # drop numbers or names merely because such words appear in the document.
    assert '공개' in public['4201234'].left
    assert '예제' in public['민지수'].left


def test_context_is_bounded_source_slice_and_batch_metadata_has_no_text():
    text = '앞말' * 40 + '\n이름: 오하준\r\n뒤말' * 10
    batch = prepare_candidates(text, CandidateConfig(context_chars=7, max_candidates=10000))
    candidate = next(c for c in batch.candidates if c.value == '오하준')
    assert candidate.left == text[candidate.start - 7:candidate.start]
    assert candidate.right == text[candidate.end:candidate.end + 7]
    assert candidate.field_hint == 'PERSON'
    assert batch.coordinate == 'character'
    assert batch.document_length == len(text)
    assert batch.metadata['line_count'] == text.count('\n') + 1
    assert '오하준' not in str(batch.metadata)


def test_overlapping_proposals_are_deduplicated_and_sources_preserved():
    text = 'name Ada Lovelace'
    candidates = extract_candidates(text)
    assert len({(c.start, c.end) for c in candidates}) == len(candidates)
    candidate = next(c for c in candidates if c.value == 'Ada Lovelace')
    assert 'titlecase-sequence' in candidate.extraction_sources
    assert 'field-value' in candidate.extraction_sources
    assert extract_candidates(text) == candidates
    assert [c.id for c in candidates] == [f'c{c.start}:{c.end}' for c in candidates]


def test_explicit_overflow_prevents_silent_partial_extraction():
    text = '이름: 박하준; 전화 010-2222-4444; 이메일 one@example.org'
    config = CandidateConfig(max_candidates=2)
    batch = prepare_candidates(text, config)
    assert batch.overflow and not batch.metadata['complete']
    assert batch.proposed_count > config.max_candidates
    assert len(batch.candidates) == config.max_candidates + 1
    with pytest.raises(CandidateOverflowError) as error:
        extract_candidates(text, config)
    assert error.value.batch == batch


def test_empty_input_and_no_unicode_normalization():
    assert extract_candidates('') == []
    text = 'name Cafe\u0301 Alice'
    batch = prepare_candidates(text)
    for candidate in batch.candidates:
        assert candidate.value == text[candidate.start:candidate.end]
    assert batch.document_length == len(text)


@pytest.mark.parametrize('kwargs', [
    {'version': 'future'}, {'context_chars': -1}, {'context_chars': True},
    {'context_chars': 4097}, {'max_candidates': 0}, {'max_candidates': 10001},
    {'max_candidate_chars': 0}, {'max_candidate_chars': 4097}, {'max_candidates': 1.2},
])
def test_config_version_limits_are_explicit(kwargs):
    with pytest.raises(ValueError):
        CandidateConfig(**kwargs)


def test_configuration_serializes_and_rejects_bad_input():
    assert CandidateConfig(**asdict(CandidateConfig())) == CandidateConfig()
    with pytest.raises(TypeError):
        prepare_candidates(b'text')
    with pytest.raises(TypeError):
        prepare_candidates('text', {})
