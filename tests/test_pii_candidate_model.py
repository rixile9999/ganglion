"""Candidate anchors and policy must preserve source coordinates and recall."""
from dataclasses import replace
import math

import pytest

from ganglion.domains.pii.candidate_model import (CLASSES, character_ids, context_view,
                                                 make_candidate_heads, scalar_features,
                                                 select_nonoverlapping)
from ganglion.domains.pii.candidate_train import targets_for_candidates
from ganglion.domains.pii.candidates import Candidate, CandidateConfig, prepare_candidates
from ganglion.domains.pii.types import Span


def candidate(start, end, value, hints=('PERSON',)):
    return Candidate(f'c{start}:{end}', start, end, value, '', '', None, hints, ('unit',))


def test_context_compilation_keeps_disjoint_source_anchors_and_unicode():
    text = '😀 김민수' + ' 공개 설명 ' * 20 + 'user@example.com 끝'
    start = text.index('user@')
    proposals = [candidate(2, 5, '김민수'), candidate(start, start + 16, 'user@example.com', ('EMAIL',))]
    view = context_view(text, proposals, 2)
    assert len(view.segments) == 2 and len(view.text) < len(text)
    for proposal in proposals:
        a, b = view.position(proposal.start, proposal.end)
        assert view.text[a:b] == text[proposal.start:proposal.end] == proposal.value
    with pytest.raises(ValueError):
        view.position(10, 15)


def test_overlapping_context_blocks_merge_without_duplicate_source_characters():
    text = '성명: 김민수 / 이메일: a@b.com'
    proposals = prepare_candidates(text).candidates
    view = context_view(text, proposals, 48)
    assert view.text == text and view.segments == ((0, len(text), 0),)


def test_contextless_proposal_still_preserves_its_value():
    text = '앞 김민수 뒤'
    view = context_view(text, [candidate(2, 5, '김민수')], 0)
    assert view.text == '김민수' and view.position(2, 5) == (0, 3)


def test_candidate_training_target_uses_exact_boundary_and_gold_type_not_hint():
    proposals = [candidate(0, 3, '김민수', ('ADDRESS',)), candidate(0, 2, '김민'), candidate(0, 4, '김민수님')]
    labels = targets_for_candidates(proposals, [{'start': 0, 'end': 3, 'type': 'PERSON'}])
    assert labels == [CLASSES.index('PERSON'), CLASSES.index('NOT_PII'), CLASSES.index('NOT_PII')]


def test_character_vocabulary_handles_unseen_characters_and_padding_explicitly():
    assert character_ids('김😀', {'김': 2}, 4) == [2, 1, 0, 0]
    assert character_ids('', {}, 2) == [0, 0]


def test_typed_scalar_hints_are_features_and_do_not_replace_actual_value():
    proposal = candidate(0, 3, '김민수', ('ADDRESS', 'PERSON'))
    values = scalar_features(proposal, 30)
    assert len(values) == 16 and all(math.isfinite(value) for value in values)
    assert values[:5] == [1, 1, 0, 0, 0]
    assert values[12] == 1.0


def test_candidate_policy_selects_complete_entity_over_weaker_overlaps():
    text = '김민수'
    spans = [Span(0, 3, 'PERSON', .95, 'candidate:c0:3'),
             Span(0, 2, 'PERSON', .7, 'candidate:c0:2'), Span(2, 3, 'PERSON', .7, 'candidate:c2:3')]
    final, changes = select_nonoverlapping(text, spans)
    assert [(span.start, span.end) for span in final] == [(0, 3)]
    assert changes[0]['rule'] == 'candidate-compatible-selection-v1'


def test_candidate_policy_preserves_separate_adjacent_entities_and_their_types():
    text = '김민수서울'
    spans = [Span(0, 3, 'PERSON', .95), Span(3, 5, 'ADDRESS', .95), Span(0, 5, 'ADDRESS', .8)]
    final, _ = select_nonoverlapping(text, spans)
    assert [(span.start, span.end, span.kind) for span in final] == [(0, 3, 'PERSON'), (3, 5, 'ADDRESS')]


def test_candidate_policy_can_abstain_without_fabricating_a_span():
    assert select_nonoverlapping('공개 설명', []) == ([], [])


def test_candidate_policy_rejects_invalid_source_anchor_instead_of_clipping():
    with pytest.raises(ValueError):
        select_nonoverlapping('김민수', [Span(0, 4, 'PERSON', .8)])


def test_candidate_character_encoder_handles_empty_context_and_backpropagates():
    torch = pytest.importorskip('torch')
    heads = make_candidate_heads(10, 8, width=32, dropout=0)
    pooled = torch.randn(3, 10)
    scalar = torch.zeros(3, 16)
    value = torch.tensor([[2, 3, 0], [1, 0, 0], [4, 5, 6]])
    context = torch.zeros(3, 4, dtype=torch.long)
    outputs = heads(pooled, scalar, value, context, context)
    assert outputs.shape == (3, 6) and torch.isfinite(outputs).all()
    loss = torch.nn.functional.cross_entropy(outputs, torch.tensor([0, 5, 1]))
    loss.backward()
    assert heads.embedding.weight.grad is not None
    assert torch.isfinite(heads.embedding.weight.grad).all()
    assert heads.embedding.weight.grad[0].eq(0).all()
