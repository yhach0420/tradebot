"""Freeze HM1 Top1 identities before any external join. Do not retune HM1."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from research.causal_path_to_complete_strategy_v1.candidates import _exit_from_fwd
from research.higher_magnitude_state_discovery_v1 import OCCUPANCY
from research.higher_magnitude_state_discovery_v1.candidates import ENTRY_CUTOFF, SESSION_FLAT, _specs, replay
from research.identify_minimum_missing_external_causal_information_v1 import HM1_ID, HM2_ID

ANCHOR_KEEP = (
    "episode_id",
    "date",
    "symbol",
    "sector",
    "start_time",
    "event_time",
    "decision_time",
    "feature_bar",
    "feature_available_at",
    "entry_bar",
    "sequence",
    "sequence_class",
    "archetype",
    "trigger_family",
    "cs_rank",
    "rank_score",
    "cs_n_at_clock",
    "sector_rs",
    "rs_5m",
    "ret_5m",
    "mfe_bps",
    "mae_bps",
    "mfe_before_mae",
    "initial_move_direction",
    "x0_h15_bps",
    "x0_entry_open",
)


def _spec(candidate_id: str) -> dict[str, Any]:
    for spec in _specs():
        if spec.get("candidate_id") == candidate_id:
            return spec
    raise KeyError(candidate_id)


def collect_ranked_top1(*, episodes: list[dict[str, Any]], spec: dict[str, Any], date_to_block: dict[str, str]) -> list[dict[str, Any]]:
    arch = spec["archetype"]
    rows = [e for e in episodes if e.get("archetype") == arch and e.get("x0_entry_open") and e.get("cs_rank") is not None and int(e["cs_rank"]) <= 1]
    rows.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), int(e.get("cs_rank") or 99), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    out: list[dict[str, Any]] = []
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            continue
        if len(occ[day]) >= OCCUPANCY:
            continue
        got = _exit_from_fwd(e, spec)
        if not got.get("ok"):
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        rec = {k: e.get(k) for k in ANCHOR_KEEP}
        rec.update(
            {
                "block": date_to_block.get(day),
                "x0_bps": got["x0_bps"],
                "x1_bps": got["x1_bps"],
                "exit_reason": got.get("exit_reason"),
                "hold_min": got.get("hold_min"),
                "exit_hh": exit_hh,
                "same_bar_close_entry": False,
                "future_used_as_decision_feature": False,
            }
        )
        out.append(rec)
        occ[day].append({"exit_hh": exit_hh})
        traded_today[day].add(str(e["symbol"]))
    return out


def _anchor_sha(spec: dict[str, Any], trades: list[dict[str, Any]]) -> str:
    payload = {
        "candidate_id": spec.get("candidate_id"),
        "spec_sha256": spec.get("spec_sha256"),
        "mode": "ranked_top1",
        "occupancy": OCCUPANCY,
        "entry_cutoff": ENTRY_CUTOFF,
        "session_flat": SESSION_FLAT,
        "ranking_rule": spec.get("ranking_rule"),
        "exit_state_machine": spec.get("exit_state_machine"),
        "rows": [
            {
                "episode_id": t.get("episode_id"),
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "decision_time": t.get("decision_time") or t.get("event_time"),
                "event_time": t.get("event_time"),
                "cs_rank": t.get("cs_rank"),
                "rank_score": t.get("rank_score"),
                "sector_rs": t.get("sector_rs"),
                "rs_5m": t.get("rs_5m"),
            }
            for t in sorted(trades, key=lambda r: str(r.get("episode_id")))
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def freeze_anchors(*, episodes: list[dict[str, Any]], date_to_block: dict[str, str]) -> dict[str, Any]:
    hm1_spec = _spec(HM1_ID)
    hm2_spec = _spec(HM2_ID)
    control = replay(episodes=episodes, spec=hm1_spec, date_to_block=date_to_block, mode="ranked_top1")
    hm1 = collect_ranked_top1(episodes=episodes, spec=hm1_spec, date_to_block=date_to_block)
    hm2 = collect_ranked_top1(episodes=episodes, spec=hm2_spec, date_to_block=date_to_block)
    sha = _anchor_sha(hm1_spec, hm1)
    return {
        "ok": bool(hm1),
        "label": "EXTERNAL_JOIN_ANCHOR_V1",
        "hm1": {
            "candidate_id": HM1_ID,
            "spec_sha256": hm1_spec.get("spec_sha256"),
            "spec_frozen": True,
            "tuned": False,
            "promoted": False,
            "discarded": False,
            "mode": "ranked_top1",
            "trade_n": len(hm1),
            "episode_ids": [t["episode_id"] for t in hm1],
            "anchor_sha256": sha,
            "control_replay": {
                "trade_n": control.get("trade_n"),
                "mean_x0_bps": control.get("mean_x0_bps"),
                "profit_factor": control.get("profit_factor"),
                "day_n": control.get("day_n"),
                "block_mean_x0": control.get("block_mean_x0"),
            },
            "near_threshold_stock_only_lead": True,
        },
        "hm2": {
            "candidate_id": HM2_ID,
            "spec_sha256": hm2_spec.get("spec_sha256"),
            "mode": "ranked_top1",
            "trade_n": len(hm2),
            "role": "secondary_comparison_only",
        },
        "hm1_trades": hm1,
        "hm2_trades": hm2,
        "hm3_used_as_primary": False,
        "identities_frozen_before_external_join": True,
        "outcomes_are_labels_only": True,
    }
