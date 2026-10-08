"""Typed neural decoding, exact character targets, and training padding safety."""
from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch")

from ganglion.domains.pii.native import decode_spans, make_heads
from ganglion.domains.pii.train import aligned_targets, batch_loss, score_predictions
from ganglion.domains.pii.types import ENTITY_TYPES, LABELS, Span, interpret


def outputs(labels, *, trims=None):
    bio = torch.full((len(labels), len(LABELS)), -8.0)
    types = torch.full((len(labels), len(ENTITY_TYPES) + 1), -4.0)
    for index, label in enumerate(labels):
        bio[index, LABELS.index(label)] = 8
        types[index, len(ENTITY_TYPES) if label == "O" else ENTITY_TYPES.index(label[2:])] = 4
    if trims is None:
        return bio, types
    starts, ends = torch.full((len(labels), 8), -8.0), torch.full((len(labels), 8), -8.0)
    for index, (left, right) in enumerate(trims):
        starts[index, left], ends[index, right] = 8, 8
    return bio, types, starts, ends


def test_v2_decoder_preserves_coordinates_and_real_probability_distribution():
    text = " 김민수 "
    result = decode_spans([(0, 2), (2, 3), (3, 5)], outputs(["B-PERSON", "I-PERSON", "I-PERSON"]))
    assert [(s.start, s.end, s.kind) for s in result] == [(0, 5, "PERSON")]
    assert math.isclose(sum(result[0].probabilities.values()), 1, abs_tol=1e-6)
    assert result[0].score == result[0].probabilities["PERSON"] < 1
    final, changes = interpret(text, result)
    assert (final[0].start, final[0].end) == (1, 4)
    assert changes[0]["rule"] == "trim-span-whitespace-v1"


def test_learned_character_heads_trim_inside_bpe_tokens_without_josa_rule():
    result = decode_spans([(0, 2), (2, 3), (3, 5)],
                          outputs(["B-PERSON", "I-PERSON", "I-PERSON"], trims=[(1, 0), (0, 0), (0, 1)]),
                          decode_mode="bio-viterbi", joint_type_weight=.5)
    assert [(s.start, s.end, s.kind) for s in result] == [(1, 4, "PERSON")]
    assert " 김민수의"[result[0].start:result[0].end] == "김민수"


def test_viterbi_retains_adjacent_same_type_entities_and_handles_duplicate_unicode_offsets():
    adjacent = decode_spans([(0, 2), (2, 4)], outputs(["B-PERSON", "B-PERSON"]), decode_mode="bio-viterbi")
    assert [(s.start, s.end) for s in adjacent] == [(0, 2), (2, 4)]
    duplicate = decode_spans([(0, 1), (0, 1), (1, 2)], outputs(["B-PERSON", "I-PERSON", "I-PERSON"]), decode_mode="bio-viterbi")
    assert [(s.start, s.end) for s in duplicate] == [(0, 2)]


def test_joint_type_evidence_can_reject_bio_false_positive_without_invented_score():
    bio = torch.full((1, len(LABELS)), -20.)
    bio[0, 0], bio[0, LABELS.index("B-PERSON")] = 0, 1
    types = torch.zeros(1, len(ENTITY_TYPES) + 1)
    types[0, -1] = 8
    assert len(decode_spans([(0, 3)], (bio, types))) == 1
    assert decode_spans([(0, 3)], (bio, types), decode_mode="bio-viterbi", joint_type_weight=.5) == []


def test_illegal_inside_transition_is_resolved_using_a_legal_sequence_path():
    bio, kinds = outputs(["O", "I-PERSON"])
    bio[1, LABELS.index("B-PERSON")] = 7
    result = decode_spans([(0, 1), (1, 4)], (bio, kinds), decode_mode="bio-viterbi")
    assert [(s.start, s.end, s.kind) for s in result] == [(1, 4, "PERSON")]


def test_single_token_boundary_predictions_cannot_consume_the_entire_entity():
    result = decode_spans([(0, 4)], outputs(["B-PERSON"], trims=[(3, 3)]))
    assert len(result) == 1
    result[0].validate(4)


@pytest.mark.parametrize("temperature", [0, -1, float("inf"), float("nan")])
def test_decoder_rejects_invalid_temperature(temperature):
    with pytest.raises(ValueError):
        decode_spans([(0, 1)], outputs(["O"]), temperature=temperature)


def test_decoder_refuses_nonfinite_model_logits_instead_of_emitting_nan_confidence():
    bio, kinds = outputs(["B-PERSON"])
    kinds[0, 0] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        decode_spans([(0, 1)], (bio, kinds))


def test_gold_alignment_keeps_whitespace_and_suffix_trims_as_learned_targets():
    bio, kinds, starts, ends = aligned_targets([(0, 2), (2, 3), (3, 5)], [{"start": 1, "end": 4, "type": "PERSON"}])
    assert bio == [LABELS.index("B-PERSON"), LABELS.index("I-PERSON"), LABELS.index("I-PERSON")]
    assert kinds == [ENTITY_TYPES.index("PERSON")] * 3
    assert starts == [1, -100, -100]
    assert ends == [-100, -100, 1]


def test_gold_alignment_refuses_two_entities_inside_a_single_token():
    with pytest.raises(ValueError, match="multiple gold entities"):
        aligned_targets([(0, 4)], [{"start": 0, "end": 1, "type": "PERSON"}, {"start": 2, "end": 4, "type": "PERSON"}])


def test_gold_alignment_refuses_silently_clipping_character_boundary_targets():
    with pytest.raises(ValueError, match="capacity"):
        aligned_targets([(0, 8)], [{"start": 5, "end": 8, "type": "PERSON"}], boundary_classes=4)


def test_negative_only_batch_has_finite_loss_and_gradients():
    head = make_heads(8, 16, boundary_classes=8)
    result = head(torch.randn(1, 3, 8), torch.ones(1, 3, dtype=torch.bool))
    expected = [torch.zeros(1, 3, dtype=torch.long), torch.full((1, 3), len(ENTITY_TYPES)),
                torch.full((1, 3), -100), torch.full((1, 3), -100)]
    loss = batch_loss(result, expected)
    assert torch.isfinite(loss)
    loss.backward()
    assert all(torch.isfinite(p.grad).all() for p in head.parameters() if p.grad is not None)


def test_readout_ignores_padding_and_supports_old_and_boundary_checkpoints():
    head = make_heads(8, 16, dropout=.1, layers=2, boundary_classes=8).eval()
    values = torch.randn(1, 3, 8)
    padded = torch.cat((values, torch.randn(1, 4, 8) * 1000), 1)
    with torch.no_grad():
        short = head(values, torch.ones(1, 3, dtype=torch.bool))
        long = head(padded, torch.tensor([[True, True, True, False, False, False, False]]))
    assert len(short) == 4
    for left, right in zip(short, long):
        assert torch.allclose(left, right[:, :3], atol=1e-5, rtol=1e-5)
    old = make_heads(8, 16)
    old.load_state_dict(make_heads(8, 16).state_dict(), strict=True)
    assert len(old(values, torch.ones(1, 3, dtype=torch.bool))) == 2


def test_evaluation_preserves_exact_f1_and_routes_failures_through_domain_analyzer():
    rows = [{"id": "independent", "text": " 김민수 ", "spans": [{"start": 1, "end": 4, "type": "PERSON"}]}]
    report = score_predictions(rows, [[Span(0, 5, "PERSON"), Span(4, 5, "EMAIL")]])
    assert report["raw"]["tp"] == 0 and report["raw"]["fn"] == 1
    assert report["interpreted"]["tp"] == 1 and report["interpreted"]["fp"] == 0
    assert report["correction_attribution"]["rescued_count"] == 1
    assert report["interpreted"]["exact_span_f1"] == 1


def test_evaluation_refuses_missing_document_predictions():
    with pytest.raises(ValueError, match="every evaluation document"):
        score_predictions([{"id": "unscored", "text": "text", "spans": []}], [])
