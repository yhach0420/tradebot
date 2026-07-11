"""Backward-compat shim for ``market.yahoo.paper_trade_extended``."""
from __future__ import annotations

import sys

import market.yahoo.paper_trade_extended as _mod

sys.modules[__name__] = _mod
