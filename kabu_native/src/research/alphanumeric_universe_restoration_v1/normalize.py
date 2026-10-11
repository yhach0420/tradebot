"""Research-only symbol codes. Runtime normalize_code is not replaced.

Active JPX master forms observed on 2026-10-08:
3575 four-digit codes and 162 three-digit-plus-letter codes.
No other active form was present, so the pattern below covers that master.
"""
from __future__ import annotations

import re

_RESEARCH_CODE_RE = re.compile(r"^(?:\d{4}|\d{3}[A-Z])$")


class ResearchCodeRejected(ValueError):
    """A code was seen and refused. Callers must record it, not skip it quietly."""


def research_normalize_code(code: str) -> str:
    """Accept a four-digit code or a master alphanumeric code. Do not zfill letters."""
    text = str(code or "").strip().upper()
    if text.endswith(".T"):
        text = text[:-2]
    if "@" in text:
        text = text.split("@", 1)[0]
    text = text.strip()
    if not _RESEARCH_CODE_RE.fullmatch(text):
        raise ResearchCodeRejected(text)
    return text


def research_yahoo_ticker(code: str) -> str:
    return f"{research_normalize_code(code)}.T"
