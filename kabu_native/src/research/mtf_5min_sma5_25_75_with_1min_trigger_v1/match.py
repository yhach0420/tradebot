"""Same-symbol same-DIR matched controls. Own-price nuisance only. No 5m MA matching. No future outcome."""
from __future__ import annotations

from typing import Any

from research.cross_sectional_peer_propagation_discovery_v1.match import balance, find_control, key_of

_ = (balance, find_control, key_of)


def index_by_key(rows: list[dict[str, Any]]) -> dict:
    from collections import defaultdict

    out = defaultdict(list)
    for r in rows:
        out[key_of(r)].append(r)
    return dict(out)
