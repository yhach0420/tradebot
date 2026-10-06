"""Discovery-only chronological research blocks. Frozen before candidate evaluation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.causal_path_to_complete_strategy_v1 import DISCOVERY_BLOCK_N


def freeze_discovery_blocks(discovery_dates: list[str], *, n_blocks: int = DISCOVERY_BLOCK_N) -> dict[str, Any]:
    days = [str(d) for d in discovery_dates]
    n = len(days)
    k = max(int(n_blocks), 2)
    if n < k * 10:
        return {"ok": False, "reason": "discovery_too_short_for_blocks", "n": n}
    sizes = [n // k] * k
    for i in range(n - sum(sizes)):
        sizes[i] += 1
    blocks = []
    i = 0
    for b, sz in enumerate(sizes):
        chunk = days[i : i + sz]
        i += sz
        blocks.append({"block_id": f"D{b+1}", "first": chunk[0], "last": chunk[-1], "n": len(chunk), "dates": chunk})
    payload = [{"block_id": b["block_id"], "dates": b["dates"]} for b in blocks]
    sha = hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    date_to_block = {}
    for b in blocks:
        for d in b["dates"]:
            date_to_block[d] = b["block_id"]
    return {
        "ok": True,
        "random_split": False,
        "n_blocks": len(blocks),
        "blocks": blocks,
        "date_to_block": date_to_block,
        "block_sha256": sha,
        "frozen_before_candidate_evaluation": True,
    }


assert freeze_discovery_blocks([f"202501{i:02d}" for i in range(1, 29)], n_blocks=4)["ok"] is False
_ok = freeze_discovery_blocks([f"20250{m}{d:02d}" for m in (1, 2, 3, 4) for d in range(10, 30)], n_blocks=4)
assert _ok["ok"] is True
assert _ok["random_split"] is False
assert _ok["blocks"][0]["last"] < _ok["blocks"][1]["first"]
