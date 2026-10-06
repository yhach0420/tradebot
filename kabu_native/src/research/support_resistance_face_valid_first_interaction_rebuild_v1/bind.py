"""Bind MULTIPLE_DESIGN_DEFICIENCIES. Discovery only. Do not revive the old economic null."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.support_resistance_face_valid_first_interaction_rebuild_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.support_resistance_face_valid_first_interaction_rebuild_v1.isolation import AUDIT_OUT, FOUNDATION_OUT, ZONE_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def prior_sample_keys() -> set[tuple[str, str]]:
    prev = _load_json(AUDIT_OUT / "report.json")
    out: set[tuple[str, str]] = set()
    for row in list(prev.get("chart_index") or []):
        if row.get("symbol") and row.get("date") and row.get("chart_path"):
            out.add((str(row["symbol"]), str(row["date"])))
    return out


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    audit = _load_json(AUDIT_OUT / "report.json")
    ans = dict(audit.get("answers") or {})
    verd = str(ans.get("VERDICT?") or ans.get("VERDICT") or (audit.get("decision") or {}).get("VERDICT") or "")
    parent_ok = verd == PARENT_VERDICT
    split = dict(base.get("split") or {})
    disc = list(split.get("discovery_dates") or [])
    blocks = freeze_discovery_blocks(disc) if disc else {"ok": False}
    pool_n = len(list(base.get("symbols") or []))
    pool_ok = pool_n == 105
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    keys = prior_sample_keys()
    ok = (
        bool(base.get("ok"))
        and parent_ok
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and AUDIT_OUT.is_dir()
        and ZONE_OUT.is_dir()
        and (AUDIT_OUT / "report.json").is_file()
        and (ZONE_OUT / "report.json").is_file()
        and len(keys) >= 200
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_verdict": verd,
        "prior_sample_keys": keys,
        "rca_deferred": True,
        "old_no_info_revived": False,
        "reason": None if ok else "split_block_pool_or_parent_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "audit_artifacts_preserved": bool((AUDIT_OUT / "report.json").is_file()),
        "zone_artifacts_preserved": bool((ZONE_OUT / "report.json").is_file()),
        "no_pnl_parameter_tuning": True,
    }
