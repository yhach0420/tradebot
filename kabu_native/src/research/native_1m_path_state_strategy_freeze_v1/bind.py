"""Bind discrimination FOUND, R11 predicates, split/blocks, atlas episodes. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import (
    DIST_VWAP_THRESHOLD,
    EXPECTED_ATLAS_EPISODE_N,
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    MINS_FROM_OPEN_THRESHOLD,
    SOURCE_VERDICT,
    VWAP_RECLAIM_THRESHOLD,
)
from research.native_1m_path_state_strategy_freeze_v1.isolation import DISC_OUT
from research.native_1m_path_state_strategy_freeze_v1.r11 import PREDICATES
from research.native_path_state_discrimination_v1.bind import bind_prior as bind_disc


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _r11_from_disc(disc: dict[str, Any]) -> dict[str, Any] | None:
    for rule in list((disc.get("extracted_rules") or {}).get("rules") or []):
        if str(rule.get("rule_id") or "") == "R11":
            return rule
    return None


def _predicates_match(rule: dict[str, Any] | None) -> bool:
    if not rule:
        return False
    got = list(rule.get("predicates") or [])
    if len(got) != len(PREDICATES):
        return False
    for a, b in zip(got, PREDICATES):
        if str(a.get("feature")) != str(b["feature"]):
            return False
        if str(a.get("op")) != str(b["op"]):
            return False
        if float(a.get("threshold")) != float(b["threshold"]):
            return False
    return True


def bind_prior() -> dict[str, Any]:
    base = bind_disc()
    disc = _load_json(DISC_OUT / "report.json")
    disc_ans = dict(disc.get("answers") or {})
    disc_verdict = str(disc_ans.get("VERDICT") or (disc.get("decision") or {}).get("VERDICT") or "")
    r11 = _r11_from_disc(disc)
    pred_ok = _predicates_match(r11)
    disc_ok = disc_verdict == SOURCE_VERDICT
    ok = bool(base.get("ok")) and disc_ok and pred_ok
    reason = None
    if not base.get("ok"):
        reason = "disc_bind_failed"
    elif not disc_ok:
        reason = f"source_verdict_{disc_verdict}"
    elif not pred_ok:
        reason = "r11_predicates_mismatch"
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "disc_verdict": disc_verdict,
        "r11_predicates_match_source": pred_ok,
        "r11_source": r11,
        "dist_vwap_threshold": DIST_VWAP_THRESHOLD,
        "mins_from_open_threshold": MINS_FROM_OPEN_THRESHOLD,
        "vwap_reclaim_threshold": VWAP_RECLAIM_THRESHOLD,
        "split_sha256_ok": (base.get("split") or {}).get("split_sha256") == EXPECTED_SPLIT_SHA256,
        "block_sha256_ok": str((base.get("blocks") or {}).get("block_sha256") or "") == EXPECTED_BLOCK_SHA256,
        "atlas_episode_n_ok": int(base.get("atlas_episode_n") or 0) == EXPECTED_ATLAS_EPISODE_N,
        "reason": reason,
    }
