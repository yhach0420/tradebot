"""Validate YYYYMMDD day stamps for universe / shadow pipeline scripts."""

from __future__ import annotations

import argparse
import re
from datetime import datetime

_DAY_RE = re.compile(r"^\d{8}$")
_RESERVED = frozenset({"YYYYMMDD", "YYYY-MM-DD", "YYYY_MM_DD"})


def normalize_day_stamp(raw: str | None, *, field: str = "day-stamp") -> str:
    if raw is None or not str(raw).strip():
        raise argparse.ArgumentTypeError(f"--{field} is required (8-digit trade date, e.g. 20260521)")
    s = str(raw).strip()
    if s.upper() in _RESERVED or not _DAY_RE.match(s):
        raise argparse.ArgumentTypeError(
            f"invalid --{field}={raw!r}: use 8-digit JST trade date (e.g. 20260521), not placeholder YYYYMMDD"
        )
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid --{field}={raw!r}: {e}") from e
    return s
