"""Phase687W9 — Paper PushSource abstraction (default remains KABU_DIRECT).

Does not switch Paper to gateway automatically. Scaffold only.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterator, Mapping, Optional, Protocol


class PushSourceMode(str, Enum):
    KABU_DIRECT = "KABU_DIRECT"
    LOCAL_CAPTURE_GATEWAY = "LOCAL_CAPTURE_GATEWAY"


DEFAULT_PUSH_SOURCE = PushSourceMode.KABU_DIRECT


class PushSource(Protocol):
    def iter_board_messages(self) -> Iterator[Mapping[str, Any]]:
        ...


def resolve_push_source_mode(raw: Optional[str] = None) -> PushSourceMode:
    """Default KABU_DIRECT. Gateway requires explicit adoption after dual-WS incompatibility + parity."""
    if not raw:
        return DEFAULT_PUSH_SOURCE
    try:
        mode = PushSourceMode(str(raw).strip().upper())
    except ValueError:
        return DEFAULT_PUSH_SOURCE
    return mode
