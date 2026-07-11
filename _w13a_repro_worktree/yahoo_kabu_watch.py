"""Backward-compat entry; implementation lives in ``market.yahoo.watch``."""
from __future__ import annotations

import sys

import market.yahoo.watch as _mod

sys.modules[__name__] = _mod

if __name__ == "__main__":
    raise SystemExit(_mod.main(sys.argv[1:]))
