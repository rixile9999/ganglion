"""Bounded streaming windows without Unicode normalization or truncation."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, TextIO, Iterator


@dataclass(frozen=True)
class TextWindow:
    start: int
    text: str
    commit_until: int
    final: bool


def single_window(source: TextIO, *, window_chars: int = 1024, token_count=None, max_tokens: int = 1024):
    """CoreBundle input without document preprocessing: reject oversized input."""
    text = source.read(window_chars + 1)
    if len(text) > window_chars or token_count and token_count(text) > max_tokens:
        raise ValueError("direct core input exceeds model budget; install the document preprocessor")
    if text:
        yield TextWindow(0, text, len(text), True)


def windows(source: TextIO, *, window_chars: int = 1024, overlap_chars: int = 128,
            token_count: Callable[[str], int] | None = None, max_tokens: int = 1024,
            strategy: str = "fixed") -> Iterator[TextWindow]:
    if window_chars < 32 or not 0 <= overlap_chars < window_chars // 2:
        raise ValueError("invalid window or overlap")
    if strategy not in {"fixed", "semantic"} or type(max_tokens) is not int or max_tokens < 1:
        raise ValueError("invalid document splitting policy")
    buffer, start, eof = "", 0, False
    while True:
        while len(buffer) <= window_chars and not eof:
            block = source.read(window_chars + 1 - len(buffer))
            if not block:
                eof = True
            buffer += block
        if not buffer:
            return
        count = min(window_chars, len(buffer))
        if token_count and token_count(buffer[:count]) > max_tokens:
            low, high = 0, count
            while low < high:
                mid = (low + high + 1) // 2
                if token_count(buffer[:mid]) <= max_tokens:
                    low = mid
                else:
                    high = mid - 1
            count = low
            if count == 0:
                raise ValueError("token budget cannot hold one character")
        natural = False
        if strategy == "semantic":
            # Keep one complete paragraph as a model work unit, rather than
            # packing unrelated records up to the maximum context length.
            prefix = buffer[:count]
            paragraph = re.search(r"(?:\r?\n)[ \t]*(?:\r?\n)", prefix)
            if paragraph:
                count, natural = paragraph.end(), True
            elif eof and count == len(buffer):
                natural = True
            else:
                # Oversized paragraphs use a complete sentence, then a line,
                # before falling back to a hard split with a pending overlap.
                # The lookahead includes the real next character: an email dot
                # at an artificial buffer end is not a sentence boundary.
                sentence = next((m for m in re.finditer(r"[.!?](?=\s|$)|[。！？]", buffer)
                                 if m.end() <= count), None)
                if sentence:
                    count, natural = sentence.end(), True
                    while count < len(prefix) and prefix[count].isspace():
                        count += 1
                else:
                    line = prefix.rfind("\n")
                    if line >= 0:
                        count, natural = line + 1, True
        final = eof and count == len(buffer)
        # Complete semantic units are disjoint; only hard splits need overlap.
        advance = count if final or natural else count - min(overlap_chars, count // 3)
        yield TextWindow(start, buffer[:count], start + advance, final)
        buffer, start = buffer[advance:], start + advance
