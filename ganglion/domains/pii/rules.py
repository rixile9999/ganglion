"""Deterministic baseline and contextual candidate recognizers, not a model."""
from __future__ import annotations

import re

from .types import Span

PATTERNS = (
    ("EMAIL", re.compile(r"(?<![a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+"), 0),
    ("PHONE", re.compile(r"(?<!\d)(?:\+82[- .]?)?(?:0?1[016789]|0[2-6][1-5]?)[- .]?\d{3,4}[- .]?\d{4}(?!\d)"), 0),
    ("IDENTIFIER", re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)"), 0),
    ("PERSON", re.compile(r"(?:성명|이름|고객명|담당자|수신자|고객)\s*[:：=]\s*([가-힣]{2,5}|[A-Z][a-z]+(?: [A-Z][a-z]+){1,2})(?=\s|[,.;]|$)"), 1),
    ("PERSON", re.compile(r"(?<![가-힣])([가-힣]{2,4})(?:님|씨)(?=[께은는이가을를의\s,.!?]|$)"), 1),
    ("PERSON", re.compile(r"(?:저는|제 이름은)\s+([가-힣]{2,4})(?=입니다|이라고|이에요|예요|\s)"), 1),
    ("ADDRESS", re.compile(r"(?:주소|배송지|거주지|address)\s*[:：=]\s*([^\r\n;]{5,160})", re.I), 1),
    ("ADDRESS", re.compile(r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|강원도|제주특별자치도)\s+[가-힣]+[시군구]\s+[가-힣\d]+(?:로|길|동)\s*\d+(?:-\d+)?(?:\s+\d+(?:동|호|층))*"), 0),
    ("PERSON", re.compile(r"\b(?:name|customer|recipient)\s*[:=]\s*([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})\b", re.I), 1),
)


class RulesDetector:
    backend = "rules"
    score_kind = "heuristic"

    def detect(self, text: str) -> list[Span]:
        spans = []
        for kind, pattern, group in PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span(group)
                while end > start and text[end - 1].isspace():
                    end -= 1
                if end > start:
                    spans.append(Span(start, end, kind, None, "rules"))
        return spans
