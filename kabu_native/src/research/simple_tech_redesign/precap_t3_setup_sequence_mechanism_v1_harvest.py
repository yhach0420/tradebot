"""Recover exact T3 P2 3-bar pullback touch age on the frozen PRE_CAP pool. Days <= 20260902 only."""
from __future__ import annotations

import copy
import gc
import json
from collections import Counter
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family import PULLBACK_LOOKBACK
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family.stages import _ok
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_harvest import (
    PX_EPS,
    _arr_at,
    recover_p2_reference,
)
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import (
    DAY_CACHE as V1_DAY_CACHE,
    assert_research_day,
    in_pre_cap_pool,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import (
    EXPECTED_ATTRITION_N,
    EXPECTED_OUTCOME_EVALUABLE_N,
    EXPECTED_POOL_N,
)
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_spec import (
    ALLOWED_AGES,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    PRIMARY_FIELD,
    all_research_days,
    block_of,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import _f, _tid, _tid_s

DAY_CACHE = RESEARCH_CACHE / "precap_t3_setup_sequence_mechanism_v1"
BLOCK_KEYS = (
    "BLOCK_A_DISCOVERY",
    "BLOCK_B_INTERNAL_STABILITY",
    "BLOCK_C_BURNED_STRESS",
)
LB = int(PULLBACK_LOOKBACK)


def touch_age_from_window(lows: list[float], ema9s: list[float]) -> dict[str, Any]:
    """Signal bar is the last of the exact 3-bar P2 window. Age 0 = that bar."""
    if len(lows) != int(LB) or len(ema9s) != int(LB):
        return {"ok": False, "blocker": "P2_WINDOW_LEN", "P2_TOUCH_AGE_BARS": None, "P2_TOUCH_COUNT": None}
    ages: list[int] = []
    flags: list[bool] = []
    for offset, (lo, e9) in enumerate(zip(lows, ema9s)):
        if not (_ok(lo) and _ok(e9)):
            return {"ok": False, "blocker": "P2_TOUCH_INPUT_MISSING", "P2_TOUCH_AGE_BARS": None, "P2_TOUCH_COUNT": None}
        age = int(LB) - 1 - int(offset)
        touched = float(lo) <= float(e9)
        flags.append(bool(touched))
        if touched:
            ages.append(age)
    if not ages:
        return {
            "ok": False,
            "blocker": "NO_TOUCH_BAR",
            "P2_TOUCH_AGE_BARS": None,
            "P2_TOUCH_COUNT": 0,
            "touch_flags": flags,
        }
    most_recent = int(min(ages))
    if most_recent not in ALLOWED_AGES:
        return {
            "ok": False,
            "blocker": "AGE_OUT_OF_RANGE",
            "P2_TOUCH_AGE_BARS": most_recent,
            "P2_TOUCH_COUNT": len(ages),
            "touch_flags": flags,
        }
    return {
        "ok": True,
        "blocker": None,
        "P2_TOUCH_AGE_BARS": most_recent,
        "P2_TOUCH_COUNT": int(len(ages)),
        "touch_flags": flags,
        "touch_ages": ages,
        "SIGNAL_BAR_TOUCH": bool(most_recent == 0),
    }


def compute_p2_sequence(tf1: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    ref = recover_p2_reference(tf1, float(t0))
    out: dict[str, Any] = {
        "ok": False,
        "blocker": ref.get("blocker"),
        "signal_t0_found": ref.get("signal_bar_i") is not None,
        "exact_p2_3_bars_found": bool(ref.get("bar_indices") and len(ref.get("bar_indices") or []) == LB),
        "all_3_completed_le_signal": bool(ref.get("reference_le_signal")),
        "future_bar_use": 0 if ref.get("reference_le_signal") else 1,
        "p1_trend_up": ref.get("p1_trend_up"),
        "p2_pullback_setup": ref.get("p2_pullback_setup"),
        "p3_reversal_rci": ref.get("p3_reversal_rci_at_signal_only"),
        "reference": {k: ref.get(k) for k in ("signal_bar_i", "signal_bar_finalize_t", "bar_indices", "setup_low", "setup_high", "signal_bar_close")},
        PRIMARY_FIELD: None,
        "P2_TOUCH_COUNT": None,
        "SIGNAL_BAR_TOUCH": None,
        "SETUP_LOW": ref.get("setup_low"),
        "SETUP_HIGH": ref.get("setup_high"),
        "SETUP_RANGE_BPS": None,
        "SIGNAL_CLOSE_LOCATION": None,
        "bars": [],
    }
    if not ref.get("ok"):
        return out
    idxs = [int(k) for k in list(ref.get("bar_indices") or [])]
    if idxs != list(range(int(ref["signal_bar_i"]) - LB + 1, int(ref["signal_bar_i"]) + 1)):
        out["blocker"] = "P2_WINDOW_MISMATCH"
        return out
    lows: list[float] = []
    ema9s: list[float] = []
    bars: list[dict[str, Any]] = []
    i_sig = int(ref["signal_bar_i"])
    for k in idxs:
        lo = _arr_at(tf1, "low", k)
        hi = _arr_at(tf1, "high", k)
        cl = _arr_at(tf1, "close", k)
        e9 = _arr_at(tf1, "ema9", k)
        bb = _arr_at(tf1, "bb_lower", k)
        ft = _arr_at(tf1, "finalize_t", k)
        if lo is None or hi is None or cl is None or e9 is None or bb is None or ft is None:
            out["blocker"] = f"P2_BAR_FIELD_MISSING:{k}"
            return out
        if float(ft) > float(t0) + PX_EPS:
            out["blocker"] = "FUTURE_BAR_USE"
            out["future_bar_use"] = 1
            out["all_3_completed_le_signal"] = False
            return out
        age = int(i_sig) - int(k)
        touched = float(lo) <= float(e9)
        bars.append(
            {
                "i": int(k),
                "age": int(age),
                "low": lo,
                "high": hi,
                "close": cl,
                "ema9": e9,
                "bb_lower": bb,
                "finalize_t": ft,
                "touch": bool(touched),
                "close_below_bb_lower": bool(float(cl) < float(bb)),
            }
        )
        lows.append(lo)
        ema9s.append(e9)
    touch = touch_age_from_window(lows, ema9s)
    out["bars"] = bars
    out["P2_TOUCH_COUNT"] = touch.get("P2_TOUCH_COUNT")
    out[PRIMARY_FIELD] = touch.get(PRIMARY_FIELD)
    out["SIGNAL_BAR_TOUCH"] = touch.get("SIGNAL_BAR_TOUCH")
    if not touch.get("ok"):
        out["blocker"] = touch.get("blocker")
        return out
    setup_low = float(ref["setup_low"])
    setup_high = float(ref["setup_high"])
    sig_close = float(ref["signal_bar_close"])
    span = setup_high - setup_low
    out["SETUP_RANGE_BPS"] = ((span / sig_close) * 10000.0) if sig_close > 0 else None
    out["SIGNAL_CLOSE_LOCATION"] = ((sig_close - setup_low) / span) if abs(span) > PX_EPS else None
    if any(b.get("close_below_bb_lower") for b in bars):
        out["blocker"] = "P2_BB_LOWER_VIOLATION"
        return out
    out["ok"] = True
    out["blocker"] = None
    return out


def _load_v1_day(day: str) -> dict[str, Any]:
    path = V1_DAY_CACHE / f"day_{day}.json"
    if not path.is_file():
        return {"ok": False, "blocker": "V1_DAY_CACHE_MISSING", "date": day}
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or not body.get("ok"):
        return {"ok": False, "blocker": "V1_DAY_CACHE_INVALID", "date": day}
    return body


def harvest_day(day: str, *, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    bad = assert_research_day(day, today=today)
    if bad:
        return {"ok": False, "blocker": bad, "date": day}
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE):
        return {"ok": False, "blocker": "FAIL_CLOSED_FUTURE_DATA", "date": day}
    path = DAY_CACHE / f"day_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    raw = _load_v1_day(day)
    if not raw.get("ok"):
        return raw
    pool = [copy.deepcopy(c) for c in list(raw.get("pool") or []) if in_pre_cap_pool(c)]
    symbols = {_bare(c.get("symbol")) for c in pool if _bare(c.get("symbol"))}
    cap = find_capture_dir(str(day))
    if cap is None:
        return {"ok": False, "blocker": "CAPTURE_MISSING", "date": day}
    cap_s = str(cap).replace("\\", "/")
    if any(tok in cap_s for tok in FORBIDDEN_INPUT_DAYS):
        return {"ok": False, "blocker": "FAIL_CLOSED_FORBIDDEN_CAPTURE_PATH", "date": day}
    leak = {"FUTURE_BAR_USE_N": 0, "SEQUENCE_FAIL_N": 0, "SEQUENCE_UNKNOWN_N": 0}
    packed = stream_day(str(day), cap, symbols, leak)
    seq_rows: list[dict[str, Any]] = []
    fail_rows: list[dict[str, Any]] = []
    for c in pool:
        t0 = _f(c.get("t0"))
        key = _tid(c)
        tid = c.get("trade_id") or (_tid_s(key) if key else None)
        if t0 is None:
            rec = {
                "ok": False,
                "blocker": "SIGNAL_T0_MISSING",
                "trade_id": tid,
                "date": c.get("date") or day,
                "symbol": c.get("symbol"),
            }
            c["p2_sequence"] = rec
            c[PRIMARY_FIELD] = None
            leak["SEQUENCE_FAIL_N"] += 1
            leak["SEQUENCE_UNKNOWN_N"] += 1
            fail_rows.append(rec)
            seq_rows.append(c)
            continue
        tf1 = ((packed.get("tf_by_sym") or {}).get(_bare(c.get("symbol"))) or {}).get("tf1") or {}
        seq = compute_p2_sequence(tf1, float(t0))
        c["p2_sequence"] = seq
        c[PRIMARY_FIELD] = seq.get(PRIMARY_FIELD)
        c["P2_TOUCH_COUNT"] = seq.get("P2_TOUCH_COUNT")
        c["SIGNAL_BAR_TOUCH"] = seq.get("SIGNAL_BAR_TOUCH")
        c["SETUP_LOW"] = seq.get("SETUP_LOW")
        c["SETUP_HIGH"] = seq.get("SETUP_HIGH")
        c["SETUP_RANGE_BPS"] = seq.get("SETUP_RANGE_BPS")
        c["SIGNAL_CLOSE_LOCATION"] = seq.get("SIGNAL_CLOSE_LOCATION")
        if int(seq.get("future_bar_use") or 0):
            leak["FUTURE_BAR_USE_N"] += 1
        if not seq.get("ok"):
            leak["SEQUENCE_FAIL_N"] += 1
            if str(seq.get("blocker") or "") in {"", "None"}:
                leak["SEQUENCE_UNKNOWN_N"] += 1
            fail_rows.append(
                {
                    "trade_id": tid,
                    "date": c.get("date") or day,
                    "symbol": c.get("symbol"),
                    "blocker": seq.get("blocker"),
                    "control_admitted": bool(c.get("control_admitted")),
                    "cap_only_blocked": bool(c.get("cap_only_blocked")),
                }
            )
        seq_rows.append(c)
    out = {
        "ok": True,
        "date": day,
        "block": block_of(day),
        "spec_sha": spec_sha,
        "leftover_ok": raw.get("leftover_ok"),
        "control_sot_ok": raw.get("control_sot_ok"),
        "control": raw.get("control"),
        "pool": seq_rows,
        "pool_n": len(seq_rows),
        "sequence_ok_n": sum(1 for r in seq_rows if (r.get("p2_sequence") or {}).get("ok")),
        "sequence_fail_n": len(fail_rows),
        "fail_rows": fail_rows,
        "capture_path": str(cap),
        "events_n": packed.get("events_n"),
        "leak": leak,
        "loaded_from": "V1_DAY_CACHE_READONLY_PLUS_SEALED_CAPTURE_TF1",
    }
    DAY_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(out), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    del packed
    gc.collect()
    return out


def harvest_blocks(*, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    bodies: dict[str, list[dict[str, Any]]] = {k: [] for k in BLOCK_KEYS}
    fail_all: list[dict[str, Any]] = []
    future_n = 0
    unknown_n = 0
    for day in all_research_days():
        body = harvest_day(day, spec_sha=spec_sha, today=today)
        if not body.get("ok"):
            return {"ok": False, "blocker": body.get("blocker"), "date": day, "bodies": bodies}
        blk = str(body.get("block") or block_of(day) or "")
        bodies[blk].append(body)
        fail_all.extend(list(body.get("fail_rows") or []))
        leak = dict(body.get("leak") or {})
        future_n += int(leak.get("FUTURE_BAR_USE_N") or 0)
        unknown_n += int(leak.get("SEQUENCE_UNKNOWN_N") or 0)
        print(
            f"{blk} {day} pool={body.get('pool_n')} seq_ok={body.get('sequence_ok_n')} "
            f"seq_fail={body.get('sequence_fail_n')} events={body.get('events_n')}",
            flush=True,
        )
    pool_n = {k: sum(int(b.get("pool_n") or 0) for b in bodies[k]) for k in BLOCK_KEYS}
    outcome_n = {}
    age_ok_n = {}
    for k in BLOCK_KEYS:
        n_out = 0
        n_age = 0
        for b in bodies[k]:
            for r in list(b.get("pool") or []):
                if _f(r.get("session_close_pnl")) is not None:
                    n_out += 1
                if r.get(PRIMARY_FIELD) in ALLOWED_AGES:
                    n_age += 1
        outcome_n[k] = n_out
        age_ok_n[k] = n_age
    attrition_n = {k: pool_n[k] - outcome_n[k] for k in BLOCK_KEYS}
    pool_ok = pool_n == dict(EXPECTED_POOL_N)
    outcome_ok = outcome_n == dict(EXPECTED_OUTCOME_EVALUABLE_N)
    attrition_ok = attrition_n == dict(EXPECTED_ATTRITION_N)
    recovered_all = age_ok_n == pool_n
    return {
        "ok": True,
        "bodies": bodies,
        "pool_identity": {
            "pool_n": pool_n,
            "outcome_evaluable_n": outcome_n,
            "attrition_n": attrition_n,
            "sequence_recovered_n": age_ok_n,
            "expected_pool_n": dict(EXPECTED_POOL_N),
            "expected_outcome_evaluable_n": dict(EXPECTED_OUTCOME_EVALUABLE_N),
            "expected_attrition_n": dict(EXPECTED_ATTRITION_N),
            "pool_identity_ok": bool(pool_ok),
            "outcome_n_ok": bool(outcome_ok),
            "attrition_n_ok": bool(attrition_ok),
            "sequence_recovered_all": bool(recovered_all),
        },
        "sequence_integrity": {
            "fail_n": len(fail_all),
            "fail_rows": fail_all,
            "fail_by_reason": dict(Counter(str(r.get("blocker") or "UNKNOWN") for r in fail_all)),
            "future_bar_use_n": int(future_n),
            "unknown_n": int(unknown_n),
            "recovered_all": bool(recovered_all and unknown_n == 0 and future_n == 0),
        },
    }
