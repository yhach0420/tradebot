"""
Symbol normalization for kabu_native universe management.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_RE = re.compile(r"^(\d{4})$")


@dataclass(frozen=True)
class ParsedSymbol:
    """Normalized symbol with kabu market (exchange) code."""

    code: str
    exchange: int
    raw: str

    @property
    def symbol_key(self) -> str:
        return f"{self.code}@{self.exchange}"


def normalize_code(code: str) -> str:
    """
  Normalize to 4-digit TSE-style code (leading zeros preserved).

  Accepts: ``9984``, ``9984.T``, ``9984@1`` (code part only).
  """
    c = code.strip().upper()
    if c.endswith(".T"):
        c = c[:-2]
    if "@" in c:
        c = c.split("@", 1)[0]
    c = c.strip()
    if not _CODE_RE.match(c):
        raise ValueError(f"invalid symbol code (expected 4 digits): {code!r}")
    return c


def parse_symbol(text: str, *, default_exchange: int = 1) -> ParsedSymbol:
    """
    Parse user-facing symbol forms into ``ParsedSymbol``.

    Supported:
    - ``9984``
    - ``9984.T``
    - ``9984@1``
    """
    raw = text.strip()
    if not raw:
        raise ValueError("empty symbol")

    upper = raw.upper()
    if "@" in upper:
        code_part, ex_part = upper.split("@", 1)
        code = normalize_code(code_part)
        try:
            exchange = int(ex_part.strip())
        except ValueError as e:
            raise ValueError(f"invalid exchange in symbol: {text!r}") from e
        return ParsedSymbol(code=code, exchange=exchange, raw=raw)

    code = normalize_code(upper)
    return ParsedSymbol(code=code, exchange=int(default_exchange), raw=raw)


def parse_symbol_list(
    items: list[str],
    *,
    default_exchange: int = 1,
) -> list[ParsedSymbol]:
    seen: set[tuple[str, int]] = set()
    out: list[ParsedSymbol] = []
    for item in items:
        parsed = parse_symbol(item, default_exchange=default_exchange)
        key = (parsed.code, parsed.exchange)
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
    return out


def to_kabu_symbol_key(parsed: ParsedSymbol) -> str:
    return parsed.symbol_key


def to_kabu_register(parsed: ParsedSymbol) -> tuple[str, int]:
    """(Symbol, Exchange) for PUT /register."""
    return (parsed.code, parsed.exchange)
