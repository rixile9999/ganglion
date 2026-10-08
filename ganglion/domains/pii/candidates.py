"""Gold-blind, bounded candidate IR; proposals are not privacy decisions.

Offsets are Python Unicode character offsets into the exact input. Consumers
must classify candidates and handle overflow explicitly, or fall back to another
adapter. No names, addresses, or dataset-specific entity values are whitelisted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

TYPES = ('PERSON', 'ADDRESS', 'PHONE', 'EMAIL', 'IDENTIFIER')


@dataclass(frozen=True)
class CandidateConfig:
    version: str = 'candidate-v1'
    context_chars: int = 48
    max_candidates: int = 256
    max_candidate_chars: int = 160

    def __post_init__(self):
        if self.version != 'candidate-v1':
            raise ValueError('unsupported candidate version')
        for name, minimum, maximum in [('context_chars', 0, 4096), ('max_candidates', 1, 10000), ('max_candidate_chars', 1, 4096)]:
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'invalid {name}')


DEFAULT_CONFIG = CandidateConfig()


@dataclass(frozen=True)
class Candidate:
    id: str
    start: int
    end: int
    value: str
    left: str
    right: str
    field_hint: str | None
    type_hints: tuple[str, ...]
    extraction_sources: tuple[str, ...]


@dataclass(frozen=True)
class CandidateBatch:
    version: str
    coordinate: str
    document_length: int
    candidates: tuple[Candidate, ...]
    overflow: bool
    proposed_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


class CandidateOverflowError(ValueError):
    def __init__(self, batch: CandidateBatch):
        self.batch = batch
        super().__init__(f'candidate limit exceeded: {batch.proposed_count} proposals')


_EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_PHONE = re.compile(r'(?<!\d)(?:\+\d{1,3}[- .]?)?(?:\(\d{2,4}\)|\d{2,4})[- .]\d{3,4}[- .]\d{4}(?!\d)')
_ID = re.compile(r'(?<!\w)(?:\d{6}[- ]\d{7}|[A-Z]{1,4}-?\d{5,16}|\d{7,16})(?!\d)')
_HANGUL = re.compile(r'[가-힣]+')
# Unicode letter words, including accented names and apostrophe/hyphen compounds.
_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)
_KO_ADDRESS = re.compile(r'[가-힣]+(?:특별자치시|특별자치도|특별시|광역시|도|시)\s+(?:[가-힣]+\s+){0,7}\d+(?:[- ]\d+)?(?:\s+\d+(?:호|층))?(?:[가-힣]*)')
_EN_ADDRESS = re.compile(r'(?<!\w)\d{1,6}\s+(?:[^\W\d_]+[\s.-]+){1,5}(?:Road|Street|Avenue|Lane|Drive|Court|Crescent|Way|Boulevard|Place|Rd|St|Ave|Ln|Dr|Ct)\b(?:,?\s+(?:[A-ZÀ-ÖØ-Þ][^\W\d_]*\s*){1,3})?', re.UNICODE | re.IGNORECASE)
_ROLE = re.compile(r'(?P<PERSON>성명|이름|실명|명의자|수령인|담당자|주인|회원|사람|name|owner|recipient)|(?P<ADDRESS>주소지?|거주지|거주 주소|수령 장소|배송지|address|residence|home)|(?P<EMAIL>이메일|메일|e-?mail|mailbox)|(?P<PHONE>전화(?:번호)?|휴대폰|연락처|phone|mobile|telephone)|(?P<IDENTIFIER>식별(?:번호)?|본인번호|개인번호|identifier|identity|\bID\b)', re.IGNORECASE)
_SUFFIXES = ('입니다', '이었다', '이며', '이고', '에게', '께서', '님께', '님이', '씨의', '으로', '에서', '까지', '부터', '이라고', '이라', '라는', '께', '님', '씨', '은', '는', '이', '가', '을', '를', '의', '과', '와', '로')


def prepare_candidates(text: str, config: CandidateConfig = DEFAULT_CONFIG) -> CandidateBatch:
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    if not isinstance(config, CandidateConfig):
        raise TypeError('config must be CandidateConfig')
    proposals: dict[tuple[int, int], tuple[set[str], set[str]]] = {}

    def add(start: int, end: int, hints: tuple[str, ...], source: str):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end or end - start > config.max_candidate_chars:
            return
        types, sources = proposals.setdefault((start, end), (set(), set()))
        types.update(hints)
        sources.add(source)

    def variants(start: int, end: int, hints: tuple[str, ...], source: str):
        add(start, end, hints, source)
        value = text[start:end]
        for suffix in _SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                add(start, end - len(suffix), hints, source + ':suffix-variant')

    for pattern, hints, source in [(_EMAIL, ('EMAIL',), 'email-shape'), (_PHONE, ('PHONE',), 'phone-shape'), (_ID, ('IDENTIFIER', 'PHONE'), 'identifier-shape')]:
        for match in pattern.finditer(text):
            variants(match.start(), match.end(), hints, source)
    for pattern, source in [(_KO_ADDRESS, 'ko-address-shape'), (_EN_ADDRESS, 'en-address-shape')]:
        for match in pattern.finditer(text):
            variants(match.start(), match.end(), ('ADDRESS',), source)
            # End alternatives at token boundaries preserve uncertain city/unit tails.
            for boundary in re.finditer(r'\S+', match.group()):
                if any(c.isdigit() for c in match.group()[:boundary.end()]):
                    variants(match.start(), match.start() + boundary.end(), ('ADDRESS',), source + ':end-variant')
    for match in _HANGUL.finditer(text):
        if 2 <= len(match.group()) <= 32:
            variants(match.start(), match.end(), ('PERSON',), 'hangul-word')
            # Names attached to particles keep both full and short proposals.
            for length in range(2, min(6, len(match.group())) + 1):
                add(match.start(), match.start() + length, ('PERSON',), 'hangul-prefix-variant')
    words = list(_WORD.finditer(text))
    for index, match in enumerate(words):
        if not any('가' <= c <= '힣' for c in match.group()) and len(match.group()) >= 2:
            add(match.start(), match.end(), ('PERSON',), 'unicode-word')
            if match.group()[0].isupper():
                sequence_start = match.start()
                for other in words[index + 1:index + 4]:
                    between = text[match.end():other.start()]
                    if not between or not between.isspace() or not other.group()[0].isupper():
                        break
                    variants(sequence_start, other.end(), ('PERSON',), 'titlecase-sequence')
                    # Subsequent words must themselves be separated by whitespace.
                    match = other
    roles = list(_ROLE.finditer(text))
    for role in roles:
        kind = role.lastgroup
        start = role.end()
        tail = text[start:start + config.max_candidate_chars]
        prefix = re.match(r'(?:은|는|이|가)?\s*[:=]?\s*', tail)
        start += prefix.end()
        segment = re.match(r'[^\n\r;,/]+', text[start:start + config.max_candidate_chars])
        if segment:
            end = start + segment.end()
            variants(start, end, (kind,), 'field-value')
            for word in _WORD.finditer(text[start:end]):
                variants(start, start + word.end(), (kind,), 'field-prefix-variant')
    ordered = sorted(proposals)
    candidates = []
    for start, end in ordered[:config.max_candidates + 1]:
        hints, sources = proposals[(start, end)]
        previous = [role for role in roles if role.end() <= start and start - role.end() <= config.context_chars]
        hint = previous[-1].lastgroup if previous else None
        candidates.append(Candidate(f'c{start}:{end}', start, end, text[start:end], text[max(0, start - config.context_chars):start], text[end:end + config.context_chars], hint, tuple(t for t in TYPES if t in hints), tuple(sorted(sources))))
    return CandidateBatch(config.version, 'character', len(text), tuple(candidates), len(ordered) > config.max_candidates, len(ordered), {'line_count': text.count('\n') + 1, 'config': {'context_chars': config.context_chars, 'max_candidates': config.max_candidates, 'max_candidate_chars': config.max_candidate_chars}, 'proposal_semantics': 'overlapping hypotheses; not privacy decisions', 'complete': len(ordered) <= config.max_candidates})


def extract_candidates(text: str, config: CandidateConfig = DEFAULT_CONFIG) -> list[Candidate]:
    batch = prepare_candidates(text, config)
    if batch.overflow:
        raise CandidateOverflowError(batch)
    return list(batch.candidates)
