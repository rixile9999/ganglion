"""Structured candidate judgments over a shared, bounded context encoder.

The LM encodes only the union of candidate context blocks. Numeric source
positions and candidate metadata are supplied as typed tensors, never generated
or serialized into a prompt. Candidate values remain transient model inputs.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import threading

from .candidates import CandidateConfig, DEFAULT_CONFIG, prepare_candidates
from .types import ENTITY_TYPES, Span, interpret

CLASSES = (*ENTITY_TYPES, 'NOT_PII')
SCALAR_SIZE = 16


@dataclass(frozen=True)
class ContextView:
    text: str
    segments: tuple[tuple[int, int, int], ...]  # source start/end, view start

    def position(self, source_start, source_end):
        for start, end, view_start in self.segments:
            if start <= source_start < source_end <= end:
                return view_start + source_start - start, view_start + source_end - start
        raise ValueError('candidate is outside its source-mapped context blocks')


def context_view(text, candidates, context_chars):
    ranges = sorted((max(0, c.start - context_chars), min(len(text), c.end + context_chars)) for c in candidates)
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces, segments, position = [], [], 0
    for start, end in merged:
        if pieces:
            pieces.append('\n')
            position += 1
        segments.append((start, end, position))
        pieces.append(text[start:end])
        position += end - start
    return ContextView(''.join(pieces), tuple(segments))


def checkpoint_fingerprint(path):
    path = Path(path)
    files = [path / 'model.json', path / 'heads.safetensors']
    files += sorted((path / 'adapter').glob('*.json')) + sorted((path / 'adapter').glob('*.safetensors'))
    digest = hashlib.sha256()
    for file in files:
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def scalar_features(candidate, document_length):
    value, n = candidate.value, max(1, len(candidate.value))
    result = [float(kind in candidate.type_hints) for kind in ENTITY_TYPES]
    result += [float(candidate.field_hint == kind) for kind in ENTITY_TYPES]
    result += [min(n, 4096) / 160, sum(c.isdigit() for c in value) / n,
               sum('가' <= c <= '힣' for c in value) / n,
               sum(c.isupper() for c in value) / n,
               sum(c.isspace() for c in value) / n,
               sum(not c.isalnum() and not c.isspace() for c in value) / n]
    return result


def character_ids(text, vocab, maximum):
    # Value capacity equals the declared proposal length. Context is explicitly
    # bounded by its own encoder setting, not silently truncated source data.
    return [vocab.get(char, 1) for char in text[:maximum]] + [0] * max(0, maximum - len(text))


def make_candidate_heads(pooled_size, vocab_size, *, width=256, char_width=32, dropout=.15):
    import torch
    from torch import nn

    class CandidateHeads(nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = nn.Embedding(vocab_size, char_width, padding_idx=0)
            self.convolutions = nn.ModuleList([nn.Conv1d(char_width, 64, kernel, padding=kernel // 2) for kernel in (3, 5)])
            self.classifier = nn.Sequential(nn.LayerNorm(pooled_size + 384 + SCALAR_SIZE),
                                            nn.Linear(pooled_size + 384 + SCALAR_SIZE, width), nn.GELU(),
                                            nn.Dropout(dropout), nn.Linear(width, width // 2), nn.GELU(),
                                            nn.Dropout(dropout), nn.Linear(width // 2, len(CLASSES)))

        def characters(self, ids):
            embedded = self.embedding(ids).transpose(1, 2)
            valid = ids.ne(0).unsqueeze(1)
            outputs = []
            for convolution in self.convolutions:
                values = torch.nn.functional.gelu(convolution(embedded))
                maximum = values.masked_fill(~valid, -1e4).amax(-1)
                maximum = torch.where(valid.any(-1), maximum, torch.zeros_like(maximum))
                outputs.append(maximum)
            return torch.cat(outputs, -1)

        def forward(self, pooled, scalars, value, left, right):
            chars = torch.cat([self.characters(part) for part in (value, left, right)], -1)
            return self.classifier(torch.cat([pooled.float(), scalars.float(), chars], -1))

    return CandidateHeads()


def select_nonoverlapping(text, spans):
    """Maximize accepted-candidate log odds without merging different entities."""
    for span in spans:
        span.validate(len(text))
    ordered = sorted(spans, key=lambda s: (s.end, s.start, s.kind))
    ends = [span.end for span in ordered]
    totals, choices = [0.0], []
    for index, span in enumerate(ordered):
        previous = bisect_right(ends, span.start, hi=index)
        probability = min(1 - 1e-7, max(1e-7, span.score if span.score is not None else .5))
        # Positive epsilon keeps an accepted p=.5 candidate representable.
        weight = max(1e-6, math.log(probability / (1 - probability)))
        take = weight + totals[previous]
        chosen = take > totals[-1]
        totals.append(take if chosen else totals[-1])
        choices.append((chosen, previous))
    selected, index = [], len(ordered)
    while index:
        chosen, previous = choices[index - 1]
        if chosen:
            selected.append(ordered[index - 1])
            index = previous
        else:
            index -= 1
    final, changes = interpret(text, sorted(selected, key=lambda s: s.start))
    retained = {(span.start, span.end, span.kind) for span in selected}
    removed = [span.to_dict() for span in spans if (span.start, span.end, span.kind) not in retained]
    if removed:
        changes.insert(0, {'rule': 'candidate-compatible-selection-v1', 'before': removed, 'after': []})
    return final, changes


class CandidateDetector:
    backend = 'qwen_candidates'
    score_kind = 'uncalibrated_probability'

    def __init__(self, checkpoint, device=None, *, candidate_config=None, fallback_checkpoint=None):
        import torch
        from safetensors.torch import load_file
        from .native import NativeDetector
        self.path = Path(checkpoint)
        self.metadata = json.loads((self.path / 'model.json').read_text())
        if self.metadata.get('contract') != 'pii-candidate-judgments-v1' or self.metadata.get('classes') != list(CLASSES):
            raise ValueError('candidate checkpoint contract mismatch')
        if self.metadata.get('ready') is not True or self.metadata.get('training_complete') is not True:
            raise ValueError('candidate checkpoint training is not complete')
        declared = self.metadata['candidate_config']
        if isinstance(candidate_config, CandidateConfig):
            supplied = asdict(candidate_config)
        elif candidate_config is None:
            supplied = declared
        else:
            supplied = {'version': 'candidate-v1', **candidate_config}
        if supplied != declared:
            raise ValueError('candidate preprocessor differs from the trained checkpoint')
        self.config = CandidateConfig(**declared)
        source = Path(self.metadata['source_checkpoint'])
        if fallback_checkpoint is not None and Path(fallback_checkpoint).resolve() != source.resolve():
            raise ValueError('fallback must use the fingerprinted shared source checkpoint')
        if checkpoint_fingerprint(source) != self.metadata['source_checkpoint_sha256']:
            raise ValueError('source encoder checkpoint changed after candidate training')
        self.encoder = NativeDetector(source, device)
        self.device, self.tokenizer = self.encoder.device, self.encoder.tokenizer
        self.tokenizer.padding_side = 'right'
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.vocab = json.loads((self.path / 'vocab.json').read_text())
        self.context_character_budget = self.metadata['context_character_budget']
        self.value_character_budget = self.config.max_candidate_chars
        self.candidate_heads = make_candidate_heads(self.metadata['pooled_size'], len(self.vocab) + 2,
                                                   width=self.metadata['head_width'], dropout=self.metadata['head_dropout']).to(self.device)
        self.candidate_heads.load_state_dict(load_file(str(self.path / 'candidate_heads.safetensors'), device=self.device))
        self.candidate_heads.eval()
        self.threshold = float(self.metadata['threshold'])
        if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 1:
            raise ValueError('invalid candidate acceptance threshold')
        self.lock, self._local = threading.RLock(), threading.local()
        self._torch = torch

    @classmethod
    def for_training(cls, source_checkpoint, vocab, config=DEFAULT_CONFIG, device=None):
        """Build the frozen encoder before learned candidate weights exist."""
        from .native import NativeDetector
        obj = cls.__new__(cls)
        obj.encoder = NativeDetector(source_checkpoint, device)
        obj.device, obj.tokenizer = obj.encoder.device, obj.encoder.tokenizer
        obj.tokenizer.padding_side = 'right'
        if obj.tokenizer.pad_token_id is None:
            obj.tokenizer.pad_token = obj.tokenizer.eos_token
        obj.config, obj.vocab = config, vocab
        obj.context_character_budget = max(1, config.context_chars)
        obj.value_character_budget = config.max_candidate_chars
        obj.lock, obj._local = threading.RLock(), threading.local()
        return obj

    def token_count(self, text):
        return self.encoder.token_count(text)

    def propose(self, text):
        return prepare_candidates(text, self.config)

    def get_last_diagnostics(self):
        return dict(getattr(self._local, 'last', {}))

    def get_batch_diagnostics(self):
        return list(getattr(self._local, 'batch', []))

    def build_features(self, texts, batch_size=64):
        import torch
        records = []
        for text in texts:
            batch = self.propose(text)
            view = context_view(text, batch.candidates, self.config.context_chars)
            count = self.token_count(view.text) if view.text else 0
            fallback = batch.overflow or count > self.encoder.metadata['max_tokens']
            diagnostics = {'candidates': len(batch.candidates), 'proposed_count': batch.proposed_count,
                           'overflow': batch.overflow, 'fallback_count': int(fallback),
                           'context_characters': len(view.text), 'source_characters': len(text),
                           'encoded_tokens': count, 'output_candidates': 0,
                           'candidate_sources': dict(Counter(source for c in batch.candidates for source in c.extraction_sources))}
            records.append({'candidates': batch.candidates, 'view': view, 'fallback': fallback, 'diagnostics': diagnostics})
        active = [i for i, record in enumerate(records) if record['candidates'] and not record['fallback']]
        for start in range(0, len(active), batch_size):
            indices = active[start:start + batch_size]
            encoded = self.tokenizer([records[i]['view'].text for i in indices], return_tensors='pt',
                                     add_special_tokens=False, padding=True, return_offsets_mapping=True)
            offsets = encoded.pop('offset_mapping').tolist()
            lengths = encoded['attention_mask'].sum(-1).tolist()
            tensors = {key: value.to(self.device) for key, value in encoded.items()}
            with self.encoder.lock, torch.inference_mode():
                hidden = self.encoder.backbone(**tensors, use_cache=False).last_hidden_state
                readout = self.encoder.heads.context(self.encoder.heads.projection(hidden.float()),
                                                    src_key_padding_mask=~tensors['attention_mask'].bool()).cpu()
            for local, index in enumerate(indices):
                record, length = records[index], lengths[local]
                features = readout[local, :length]
                values, scalar, characters = [], [], [[], [], []]
                global_mean = features.mean(0)
                for candidate in record['candidates']:
                    left, right = record['view'].position(candidate.start, candidate.end)
                    covered = [j for j, (a, b) in enumerate(offsets[local][:length]) if a < right and b > left]
                    if not covered:
                        raise ValueError('candidate has no shared-context tokenizer coverage')
                    selected = features[covered]
                    values.append(torch.cat([selected[0], selected[-1], selected.mean(0), selected.amax(0), global_mean]))
                    scalar.append(scalar_features(candidate, len(texts[index])))
                    for target, value, capacity in zip(characters, (candidate.value, candidate.left, candidate.right),
                                                       (self.value_character_budget, self.context_character_budget, self.context_character_budget)):
                        if target is characters[1]:
                            value = value[-capacity:]
                        target.append(character_ids(value, self.vocab, capacity))
                record['tensors'] = (torch.stack(values).half(), torch.tensor(scalar, dtype=torch.float32),
                                     *(torch.tensor(part, dtype=torch.long) for part in characters))
        return records

    def decisions_from_features(self, record, *, threshold=None):
        import torch
        if not record.get('tensors'):
            return []
        threshold = self.threshold if threshold is None else threshold
        with torch.inference_mode():
            probabilities = self.candidate_heads(*(value.to(self.device) for value in record['tensors'])).softmax(-1).cpu()
        spans = []
        for candidate, distribution in zip(record['candidates'], probabilities.tolist()):
            kind = max(range(len(CLASSES)), key=lambda i: distribution[i])
            if kind < len(ENTITY_TYPES) and distribution[kind] >= threshold:
                spans.append(Span(candidate.start, candidate.end, CLASSES[kind], distribution[kind],
                                  'candidate:' + candidate.id, dict(zip(CLASSES, distribution))))
        return spans

    def detect_batch(self, texts, batch_size=64):
        with self.lock:
            records = self.build_features(texts, batch_size)
            output = []
            for text, record in zip(texts, records):
                spans = self.encoder.detect(text) if record['fallback'] else self.decisions_from_features(record)
                record['diagnostics']['output_candidates'] = len(spans)
                if record['fallback']:
                    record['diagnostics']['encoded_tokens'] = self.token_count(text)
                output.append(spans)
            self._local.batch = [record['diagnostics'] for record in records]
            self._local.last = self._local.batch[-1] if records else {}
            return output

    def detect(self, text):
        return self.detect_batch([text])[0]

    def interpret(self, text, spans):
        # Fallback BIO outputs use the existing native policy.
        if spans and all(not span.origin.startswith('candidate:') for span in spans):
            return interpret(text, spans)
        return select_nonoverlapping(text, spans)
