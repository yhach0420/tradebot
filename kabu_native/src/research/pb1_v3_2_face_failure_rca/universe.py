"""Combine V3 67 + unseen 21. All SEMANTIC_DEVELOPMENT_CONTAMINATED. Not holdout."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca import EXEMPLAR_EARLY_REVERSAL, PARENT_UNSEEN_N, PARENT_V3_FACE_N, SEMANTIC_RCA_N
from research.pb1_v3_2_face_failure_rca.first_pass import normalize_first_pass
from research.pb1_v3_2_face_failure_rca.isolation import UNSEEN_OUT, V3_CACHE, V3_OUT, V32_CACHE

KEEP_WALKED = (
    "DIR",
    "direction",
    "trigger_pos",
    "trigger_t",
    "entry_t",
    "break_t",
    "break_pos",
    "retest_t",
    "retest_pos",
    "retest_high",
    "retest_low",
    "or_high",
    "or_low",
    "or_close_loc",
    "auction",
    "open_state",
    "planned_R",
    "NORMAL_1M_RANGE",
    "median_1m_range_tod",
    "pdh",
    "pdl",
    "pdc",
    "sma5",
    "sma25",
    "sma75",
    "daily_bias",
    "in_play",
    "in_play_reason",
    "abs_gap_atr",
    "gap_signed",
    "tv_0915",
    "tv_0915_pctl",
    "xs_rank_pct",
    "zone_class",
    "defended_level_type",
    "structural_route",
    "nearest_opposing",
    "expand_vs_retest",
    "trigger_dir_close_loc",
    "trigger_body",
    "trigger_range",
    "impulse_tv_pctl",
    "retest_tv_pctl",
    "trigger_tv_pctl",
    "tv_sequence",
    "block",
)


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _index_walked(path) -> dict[tuple[str, str, str], dict[str, Any]]:
    data = _load_json(path)
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in list(data.get("events") or []):
        k = (str(e.get("symbol")), str(e.get("date")), str(e.get("direction")))
        slim = {name: e.get(name) for name in KEEP_WALKED if name in e}
        slim["symbol"] = k[0]
        slim["date"] = k[1]
        out[k] = slim
    return out


def _pick(row: dict[str, Any], walked: dict[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
        if walked.get(name) not in (None, ""):
            return walked.get(name)
    return None


def assemble_universe(bind: dict[str, Any]) -> dict[str, Any]:
    v3_rows = list((bind.get("v3_human") or {}).get("rows") or [])
    unseen_rows = list((bind.get("unseen_human") or {}).get("rows") or [])
    v3_walk = _index_walked(V3_CACHE / "walked.json")
    v32_walk = _index_walked(V32_CACHE / "walked.json")
    events: list[dict[str, Any]] = []
    for i, row in enumerate(v3_rows, start=1):
        k = (str(row.get("symbol")), str(row.get("date")), str(row.get("direction")))
        w3 = dict(v3_walk.get(k) or {})
        w32 = dict(v32_walk.get(k) or {})
        walked = {**w3, **{kk: vv for kk, vv in w32.items() if vv is not None}}
        fp = normalize_first_pass(row, cohort="v3_semantic_dev")
        events.append(
            {
                "rca_id": i,
                "cohort": "v3_semantic_dev",
                "origin_sample_id": int(row.get("sample_id") or i),
                "symbol": k[0],
                "date": k[1],
                "direction": k[2],
                "DIR": int(walked.get("DIR") or (1 if k[2] == "bull" else -1)),
                "block": row.get("block") or walked.get("block"),
                "break_t": _pick(row, walked, "break_t"),
                "retest_t": _pick(row, walked, "retest_t"),
                "trigger_t": _pick(row, walked, "trigger_t", "trigger_time"),
                "entry_t": walked.get("entry_t"),
                "trigger_pos": walked.get("trigger_pos"),
                "semantic_status": "SEMANTIC_DEVELOPMENT_CONTAMINATED",
                "holdout": False,
                "v32_emitted": k in v32_walk,
                "v32_auction": w32.get("auction"),
                "machine_auction": w32.get("auction") or w3.get("auction"),
                "prior_chart": f"pb1_playbook_redesign_v3/charts/{row.get('chart')}",
                **{name: walked.get(name) for name in KEEP_WALKED if name not in {"DIR", "direction", "block"}},
                **fp,
            }
        )
    for j, row in enumerate(unseen_rows, start=1):
        k = (str(row.get("symbol")), str(row.get("date")), str(row.get("direction")))
        w32 = dict(v32_walk.get(k) or {})
        fp = normalize_first_pass(row, cohort="v32_independent_failed")
        events.append(
            {
                "rca_id": PARENT_V3_FACE_N + j,
                "cohort": "v32_independent_failed",
                "origin_sample_id": int(row.get("sample_id") or j),
                "symbol": k[0],
                "date": k[1],
                "direction": k[2],
                "DIR": int(row.get("DIR") or w32.get("DIR") or (1 if k[2] == "bull" else -1)),
                "block": row.get("block") or w32.get("block"),
                "break_t": _pick(row, w32, "break_t"),
                "retest_t": _pick(row, w32, "retest_t"),
                "trigger_t": _pick(row, w32, "trigger_t", "trigger_time"),
                "entry_t": _pick(row, w32, "entry_t", "entry_time"),
                "trigger_pos": w32.get("trigger_pos"),
                "semantic_status": "SEMANTIC_DEVELOPMENT_CONTAMINATED",
                "holdout": False,
                "v32_emitted": True,
                "v32_auction": row.get("auction") or w32.get("auction"),
                "machine_auction": row.get("auction") or w32.get("auction"),
                "prior_chart": f"pb1_v3_2_discovery_unseen_face_verify/charts/{row.get('chart')}",
                **{name: w32.get(name) for name in KEEP_WALKED if name not in {"DIR", "direction", "block"}},
                **fp,
            }
        )
    ex = [e for e in events if (e["symbol"], e["date"], e["direction"]) == EXEMPLAR_EARLY_REVERSAL]
    return {
        "n": len(events),
        "expected_n": int(SEMANTIC_RCA_N),
        "v3_n": int(PARENT_V3_FACE_N),
        "unseen_n": int(PARENT_UNSEEN_N),
        "ok": len(events) == int(SEMANTIC_RCA_N),
        "holdout_reused": False,
        "exemplar_3382_present": bool(ex),
        "v32_emitted_n": sum(1 for e in events if e.get("v32_emitted")),
        "events": events,
    }
