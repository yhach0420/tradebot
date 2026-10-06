"""DEV-only existing primitive/state coverage. No composite class signals. No Capture restream."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.new_architecture_class_rethink_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.new_architecture_class_rethink_v1.isolation import RESEARCH_ROOT
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars, self_check_agg

V7_CACHE = RESEARCH_ROOT / "simple_tech_entry_family" / "_work" / "v7_timeframe_role_rca"
ST_CACHE = RESEARCH_ROOT / "_work" / "systematic_state_transition_full_strategy_v1"
CANARY_ROW_ID = "R2_X1_Z3"


def _forbid_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        raise RuntimeError(f"STRESS_READ:{d}")
    if d in BURNED_HOLDOUT_DAYS:
        raise RuntimeError(f"HOLDOUT_BURNED_READ:{d}")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE:
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        raise RuntimeError(f"NON_DEV:{d}")


def _load_dev_json(path: Path, day: str) -> dict[str, Any]:
    _forbid_day(day)
    name = path.name
    if day not in name:
        raise RuntimeError(f"DAY_FILENAME_MISMATCH:{path.name}:{day}")
    if not path.is_file():
        return {}
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def htf_completed_asof_proof() -> dict[str, Any]:
    """Prove completed-HTF asof at a 1m timestamp. Not a trigger. Not a count of C1 signals."""
    chk = self_check_agg()
    import numpy as np

    from research.simple_tech_entry_family.bars import BAR_FIELDS

    n = 6
    raw = {k: np.zeros(n, dtype=float) for k in BAR_FIELDS}
    raw["minute_epoch"] = np.asarray([0.0, 60.0, 120.0, 180.0, 240.0, 300.0], dtype=float)
    raw["open"] = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=float)
    raw["high"] = raw["open"] + 0.5
    raw["low"] = raw["open"] - 0.5
    raw["close"] = raw["open"] + 0.2
    raw["volume"] = np.asarray([10.0, 20.0, 30.0, 40.0, 50.0, 60.0], dtype=float)
    raw["n_events"] = np.ones(n, dtype=float)
    raw["first_t"] = raw["minute_epoch"]
    raw["last_t"] = raw["minute_epoch"] + 50.0
    raw["finalize_t"] = raw["minute_epoch"] + 60.0
    raw["up_vol"] = np.ones(n, dtype=float)
    raw["down_vol"] = np.ones(n, dtype=float)
    raw["ask_vol"] = np.ones(n, dtype=float)
    raw["bid_vol"] = np.ones(n, dtype=float)
    raw["vwap_num"] = raw["close"] * raw["volume"]
    out, leak = aggregate_bars(raw, width_sec=180.0, am_start=0.0, am_end=600.0)
    integ = agg_integrity(out, width_sec=180.0, am_start=0.0, am_end=600.0)
    htf_fin = [float(x) for x in out["finalize_t"]] if int(out["finalize_t"].size) else []

    def asof(t0: float) -> float | None:
        usable = [f for f in htf_fin if f <= float(t0) + 1e-12]
        return max(usable) if usable else None

    mid_bucket = asof(120.0)
    at_complete = asof(180.0)
    rejected_same_bucket_carryback = mid_bucket is None
    ok = (
        bool(chk.get("ok"))
        and int(leak.get("OK_BAR_N") or 0) == 2
        and int(leak.get("PARTIAL_BUCKET_N") or 0) == 0
        and int(integ.get("FUTURE_BAR_N") or 0) == 0
        and abs(float(out["finalize_t"][0]) - 180.0) < 1e-12
        and rejected_same_bucket_carryback
        and at_complete is not None
        and abs(float(at_complete) - 180.0) < 1e-12
    )
    return {
        "ok": bool(ok),
        "self_check_agg_ok": bool(chk.get("ok")),
        "OK_BAR_N": int(leak.get("OK_BAR_N") or 0),
        "PARTIAL_BUCKET_N": int(leak.get("PARTIAL_BUCKET_N") or 0),
        "FUTURE_BAR_N": int(integ.get("FUTURE_BAR_N") or 0),
        "FIRST_3M_FINALIZE_T": float(out["finalize_t"][0]) if int(out["finalize_t"].size) else None,
        "ASOF_AT_1M_T0_120_MID_BUCKET": mid_bucket,
        "ASOF_AT_1M_T0_180_COMPLETED": at_complete,
        "SAME_BUCKET_CARRYBACK_REJECTED": bool(rejected_same_bucket_carryback),
        "JOIN_RULE": "HTF bar usable at signal t iff HTF.finalize_t <= t. Partial buckets are not emitted.",
        "V7_SAME_BUCKET_DIAGNOSTIC_JOIN": "REJECTED_FOR_C1",
    }


def _v7_day_coverage(day: str, opened: list[Path]) -> dict[str, Any]:
    path = V7_CACHE / f"day_{day}.json"
    opened.append(path)
    obj = _load_dev_json(path, day)
    if not obj:
        return {
            "date": day,
            "ok": False,
            "missing": True,
            "TF1_s0": 0,
            "TF3_s0": 0,
            "TF5_s0": 0,
            "TF1_bar_n": 0,
            "TF3_bar_n": 0,
            "TF5_bar_n": 0,
            "FUTURE_BAR_N": None,
            "PARTIAL_BUCKET_N": None,
            "IN_PROGRESS_BAR_N": None,
        }
    fun = dict(obj.get("funnels") or {})
    leak = dict(obj.get("leak") or {})
    bars = {"TF1": 0, "TF3": 0, "TF5": 0}
    future_bar = 0
    inprog = 0
    for br in list(obj.get("bar_rows") or []):
        tf = str(br.get("tf") or "")
        if tf in bars:
            bars[tf] += int(br.get("OK_BAR_N") or br.get("bar_n") or 0)
        future_bar += int(br.get("FUTURE_BAR_N") or 0)
        inprog += int(br.get("IN_PROGRESS_BAR_N") or 0)
    return {
        "date": day,
        "ok": bool(obj.get("ok")),
        "missing": False,
        "TF1_s0": int((fun.get("TF1") or {}).get("s0") or 0),
        "TF3_s0": int((fun.get("TF3") or {}).get("s0") or 0),
        "TF5_s0": int((fun.get("TF5") or {}).get("s0") or 0),
        "TF1_bar_n": int(bars["TF1"]),
        "TF3_bar_n": int(bars["TF3"]),
        "TF5_bar_n": int(bars["TF5"]),
        "FUTURE_BAR_N": int(leak.get("FUTURE_BAR_N") or 0) + int(future_bar),
        "PARTIAL_BUCKET_N": int(leak.get("PARTIAL_BUCKET_N") or 0),
        "IN_PROGRESS_BAR_N": int(inprog),
        "GAP_BUCKET_N": int(leak.get("GAP_BUCKET_N") or 0),
        "LATE_FINALIZE_N": int(leak.get("LATE_FINALIZE_N") or 0),
        "MIXED_TF_STRATEGY_N": int(leak.get("MIXED_TF_STRATEGY_N") or 0),
    }


def _st_day_coverage(day: str, opened: list[Path]) -> dict[str, Any]:
    path = ST_CACHE / f"DEVELOPMENT_{day}_grid.json"
    opened.append(path)
    obj = _load_dev_json(path, day)
    if not obj:
        return {
            "date": day,
            "ok": False,
            "missing": True,
            "signal_row_n": 0,
            "unique_date_t0_n": 0,
            "fill_t_available_n": 0,
            "missing_t0_n": 0,
            "FUTURE_BAR_N": None,
        }
    rb = dict(obj.get("rows_by") or {})
    leak = dict(obj.get("leak") or {})
    t0s: set[float] = set()
    row_n = 0
    fill_n = 0
    missing_t0 = 0
    for cid, rows in rb.items():
        if str(cid) == CANARY_ROW_ID:
            continue
        for r in list(rows or []):
            row_n += 1
            t0 = r.get("t0")
            if t0 is None:
                t0 = r.get("signal_t0")
            if t0 is None:
                missing_t0 += 1
                continue
            t0s.add(float(t0))
            if r.get("fill_t") is not None:
                fill_n += 1
    return {
        "date": day,
        "ok": bool(obj.get("ok")),
        "missing": False,
        "signal_row_n": int(row_n),
        "unique_date_t0_n": int(len(t0s)),
        "fill_t_available_n": int(fill_n),
        "missing_t0_n": int(missing_t0),
        "FUTURE_BAR_N": int(leak.get("FUTURE_BAR_N") or 0),
        "library_cid_n": int(sum(1 for k in rb if str(k) != CANARY_ROW_ID)),
    }


def primitive_coverage() -> dict[str, Any]:
    opened: list[Path] = []
    v7_days = []
    st_days = []
    for day in DEVELOPMENT_DAYS:
        v7_days.append(_v7_day_coverage(str(day), opened))
        st_days.append(_st_day_coverage(str(day), opened))
    proof = htf_completed_asof_proof()
    v7_ok_days = [r for r in v7_days if r.get("ok") and not r.get("missing")]
    st_ok_days = [r for r in st_days if r.get("ok") and not r.get("missing")]
    tf3_days = [r for r in v7_ok_days if int(r.get("TF3_s0") or 0) > 0]
    tf5_days = [r for r in v7_ok_days if int(r.get("TF5_s0") or 0) > 0]
    tf1_days = [r for r in v7_ok_days if int(r.get("TF1_s0") or 0) > 0]
    stream_days = [r for r in st_ok_days if int(r.get("unique_date_t0_n") or 0) > 0]
    fill_days = [r for r in st_ok_days if int(r.get("fill_t_available_n") or 0) > 0]
    future_bar_n = sum(int(r.get("FUTURE_BAR_N") or 0) for r in v7_ok_days)
    inprog_n = sum(int(r.get("IN_PROGRESS_BAR_N") or 0) for r in v7_ok_days)
    mixed_n = sum(int(r.get("MIXED_TF_STRATEGY_N") or 0) for r in v7_ok_days)
    return {
        "opened_paths": [str(p) for p in opened],
        "htf_asof_proof": proof,
        "HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN": bool(proof.get("ok")) and future_bar_n == 0 and inprog_n == 0,
        "v7_days": v7_days,
        "st_days": st_days,
        "V7_MISSING_DAY_N": int(sum(1 for r in v7_days if r.get("missing"))),
        "ST_MISSING_DAY_N": int(sum(1 for r in st_days if r.get("missing"))),
        "TF1_AVAILABLE_DAY_N": len(tf1_days),
        "TF3_AVAILABLE_DAY_N": len(tf3_days),
        "TF5_AVAILABLE_DAY_N": len(tf5_days),
        "TF1_EVALUABLE_EVENT_N": int(sum(int(r.get("TF1_s0") or 0) for r in v7_ok_days)),
        "TF3_EVALUABLE_EVENT_N": int(sum(int(r.get("TF3_s0") or 0) for r in v7_ok_days)),
        "TF5_EVALUABLE_EVENT_N": int(sum(int(r.get("TF5_s0") or 0) for r in v7_ok_days)),
        "TF3_COMPLETED_BAR_N": int(sum(int(r.get("TF3_bar_n") or 0) for r in v7_ok_days)),
        "TF5_COMPLETED_BAR_N": int(sum(int(r.get("TF5_bar_n") or 0) for r in v7_ok_days)),
        "CANDIDATE_STREAM_DAY_N": len(stream_days),
        "CANDIDATE_STREAM_ROW_N": int(sum(int(r.get("signal_row_n") or 0) for r in st_ok_days)),
        "CANDIDATE_STREAM_UNIQUE_T0_N": int(sum(int(r.get("unique_date_t0_n") or 0) for r in st_ok_days)),
        "POST_FILL_FIELD_DAY_N": len(fill_days),
        "POST_FILL_FILL_T_AVAILABLE_N": int(sum(int(r.get("fill_t_available_n") or 0) for r in st_ok_days)),
        "FUTURE_BAR_N": int(future_bar_n),
        "IN_PROGRESS_BAR_N": int(inprog_n),
        "MIXED_TF_STRATEGY_N": int(mixed_n),
        "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": 0,
        "NEW_TRIGGER_DEFINITION_N": 0,
        "NEW_THRESHOLD_DEFINITION_N": 0,
        "note": (
            "Counts are existing 1m/3m/5m evaluable bars, existing candidate-stream rows, "
            "and existing fill_t field availability on DEVELOPMENT days only. "
            "Not composite C1-C4 trigger/trade estimates."
        ),
    }
