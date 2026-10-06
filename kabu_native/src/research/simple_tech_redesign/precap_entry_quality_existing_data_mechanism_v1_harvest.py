"""Attach 4 existing causal features to the frozen PRE_CAP executable pool. Days <= 20260902 only."""
from __future__ import annotations

import gc
import json
from collections import defaultdict
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
)
from research.joint_feature_architecture.features import block_l, block_p, vol_arrays
from research.simple_tech_entry_family.harvest import _board_row, load_day_cache
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_spec import (
    BLOCK_C_BURNED_STRESS,
    FEATURES,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    REBOUND_SOURCE_KEY,
    all_research_days,
    block_of,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_harvest import harvest_day as harvest_pool_day
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f
from small_paper.v1r_live_dual_lane import session_end_for_position

DAY_CACHE = RESEARCH_CACHE / "precap_entry_quality_existing_data_mechanism_v1"


def _finite_board(v: Any) -> Optional[float]:
    x = _f(v)
    if x is None or x != x:
        return None
    return x


def assert_research_day(day: str, *, today: str = TODAY) -> Optional[str]:
    d = str(day)
    if d in FORBIDDEN_INPUT_DAYS:
        return "FAIL_CLOSED_FORBIDDEN_INPUT"
    if d > str(MAX_RESEARCH_DATE):
        return "FAIL_CLOSED_FUTURE_DATA"
    if d == str(today) or d > str(today):
        return "FAIL_CLOSED_ACTIVE_OR_FUTURE"
    if block_of(d) is None:
        return "FAIL_CLOSED_DAY_NOT_IN_BLOCKS"
    return None


def cohort_for(day: str) -> str:
    return "FORWARD_BURNED" if str(day) in BLOCK_C_BURNED_STRESS else "DEVELOPMENT"


def in_pre_cap_pool(row: dict[str, Any]) -> bool:
    return bool(row.get("control_admitted") or row.get("cap_only_blocked"))


def stream_vol_events(day: str, capture, symbols: set[str]) -> tuple[dict[str, list], int]:
    am_start = float(hm_epoch(str(day), 9, 0))
    am_end = float(session_end_for_position(date=str(day), session="AM", fill_time=am_start + 60.0))
    events: dict[str, list] = defaultdict(list)
    n = 0
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in symbols:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None:
            continue
        if float(et) < am_start - 5.0 or float(et) > am_end + 2.0:
            continue
        row = _board_row(pay, float(et))
        bid = _finite_board(row.get("bid"))
        ask = _finite_board(row.get("ask"))
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask > bid else None
        tv = _finite_board(row.get("cum_vol"))
        events[str(sym)].append((float(et), tv, mid))
        n += 1
    return events, n


def features_at_t0(vol: dict[str, Any], *, t0: float, t_lo: float) -> tuple[dict[str, Any], int]:
    p, f1 = block_p(
        t_mid=vol["t"],
        mid=vol["mid"],
        t_vol=vol["t"],
        dvol=vol["dvol"],
        tv_inc=vol["tv_inc"],
        t0=float(t0),
        t_lo=float(t_lo),
    )
    l, f2 = block_l(
        t_vol=vol["t"],
        vol=vol["vol"],
        dvol=vol["dvol"],
        tv_inc=vol["tv_inc"],
        t0=float(t0),
        t_lo=float(t_lo),
        event_rate_60s=None,
        spread_bps=None,
    )
    out = {
        "volume_percentile_60s": l.get("volume_percentile_60s"),
        "distance_from_vwap_bps": p.get("distance_from_vwap_bps"),
        "rebound_from_recent_low_bps": p.get(REBOUND_SOURCE_KEY),
        "trading_value_percentile_180s": l.get("trading_value_percentile_180s"),
        "feature_source": "EXISTING_JOINT_BLOCK_P_L",
        "rebound_implementation": REBOUND_SOURCE_KEY,
    }
    return out, int(f1 + f2)


def attach_features(day: str, candidates: list[dict[str, Any]], *, today: str = TODAY) -> dict[str, Any]:
    bad = assert_research_day(day, today=today)
    if bad:
        return {"ok": False, "blocker": bad, "date": day}
    needed = {_bare(c.get("symbol")) for c in candidates if in_pre_cap_pool(c)}
    if not needed:
        return {"ok": True, "date": day, "events_n": 0, "future_feature_use_n": 0, "capture_path": "", "feature_join_n": 0}
    cap = find_capture_dir(str(day))
    if cap is None:
        return {"ok": False, "blocker": "CAPTURE_MISSING", "date": day}
    cap_s = str(cap).replace("\\", "/")
    if any(tok in cap_s for tok in FORBIDDEN_INPUT_DAYS):
        return {"ok": False, "blocker": "FAIL_CLOSED_FORBIDDEN_CAPTURE_PATH", "date": day}
    events, n_ev = stream_vol_events(str(day), cap, needed)
    t_lo = float(hm_epoch(str(day), 9, 0))
    vol_by = {sym: vol_arrays(list(evs)) for sym, evs in events.items()}
    future_n = 0
    joined = 0
    for c in candidates:
        if not in_pre_cap_pool(c):
            continue
        sym = _bare(c.get("symbol"))
        t0 = _f(c.get("t0"))
        if t0 is None:
            for k in FEATURES:
                c[k] = None
            c["feature_missing"] = True
            continue
        vol = vol_by.get(sym) or vol_arrays([])
        feat, fut = features_at_t0(vol, t0=float(t0), t_lo=t_lo)
        future_n += int(fut)
        if fut:
            for k in FEATURES:
                c[k] = None
            c["feature_missing"] = True
            c["feature_future_blocked"] = True
            continue
        for k in FEATURES:
            c[k] = feat.get(k)
        c["feature_source"] = feat.get("feature_source")
        c["rebound_implementation"] = feat.get("rebound_implementation")
        c["feature_missing"] = all(_f(c.get(k)) is None for k in FEATURES)
        joined += 1
    return {
        "ok": True,
        "date": day,
        "capture_path": str(cap),
        "events_n": int(n_ev),
        "future_feature_use_n": int(future_n),
        "feature_join_n": int(joined),
    }


def harvest_day(day: str, *, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    bad = assert_research_day(day, today=today)
    if bad:
        return {"ok": False, "blocker": bad, "date": day}
    path = DAY_CACHE / f"day_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    body = harvest_pool_day(str(day), cohort=cohort_for(day), today=today)
    if not body.get("ok"):
        return body
    feat = attach_features(str(day), list(body.get("candidates") or []), today=today)
    if not feat.get("ok"):
        return {"ok": False, "blocker": feat.get("blocker"), "date": day}
    if int(feat.get("future_feature_use_n") or 0):
        return {
            "ok": False,
            "blocker": "FUTURE_FEATURE_USE",
            "date": day,
            "future_feature_use_n": feat.get("future_feature_use_n"),
        }
    pool = [c for c in list(body.get("candidates") or []) if in_pre_cap_pool(c)]
    out = {
        "ok": True,
        "date": day,
        "block": block_of(day),
        "cohort": cohort_for(day),
        "spec_sha": spec_sha,
        "leftover_ok": body.get("leftover_ok"),
        "control_sot_ok": body.get("control_sot_ok"),
        "control": body.get("control"),
        "candidates": list(body.get("candidates") or []),
        "pool": pool,
        "executable_n": body.get("executable_n"),
        "admitted_n": body.get("admitted_n"),
        "hyp_n": body.get("hyp_n"),
        "pool_n": len(pool),
        "feature_attach": {
            k: feat.get(k) for k in ("capture_path", "events_n", "future_feature_use_n", "feature_join_n")
        },
    }
    DAY_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(out), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    gc.collect()
    return out


def harvest_blocks(*, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    bodies: dict[str, list[dict[str, Any]]] = {
        "BLOCK_A_DISCOVERY": [],
        "BLOCK_B_INTERNAL_STABILITY": [],
        "BLOCK_C_BURNED_STRESS": [],
    }
    for day in all_research_days():
        body = harvest_day(day, spec_sha=spec_sha, today=today)
        if not body.get("ok"):
            return {"ok": False, "blocker": body.get("blocker"), "date": day, "bodies": bodies}
        blk = str(body.get("block") or "")
        bodies[blk].append(body)
        att = dict(body.get("feature_attach") or {})
        print(
            f"{blk} {day} pool={body.get('pool_n')} admitted={body.get('admitted_n')} "
            f"hyp={body.get('hyp_n')} feat_join={att.get('feature_join_n')}",
            flush=True,
        )
    return {"ok": True, "bodies": bodies}
