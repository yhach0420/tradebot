"""RCI ARM then strictly later down-close + volume expansion. Exact Lifecycle RCI. New participation confirm. Not static AND."""
from __future__ import annotations

import ast
import gc
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family import RCI_CROSS_LEVEL
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_harvest import _pack_exit, incremental_causes
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign import exit_lifecycle_harvest as lifecycle_mod
from research.simple_tech_redesign.exit_lifecycle_harvest import (
    LIFECYCLE_CACHE,
    _arr_at,
    _first_bar_flag,
    eval_lifecycle,
    load_lifecycle_day_cache,
)
from research.simple_tech_redesign.exit_lifecycle_spec import (
    ADDED_FILL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    spec_sha256_lifecycle,
)
from research.simple_tech_redesign.exit_residual_rca_harvest import residual_class
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_spec import (
    ARM_PRIMITIVE,
    CANDIDATE_EXIT_REASON,
    CONFIRM_PRIMITIVE,
    CONCENTRATION_MAX_SHARE,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    PHASE_A_HIT_DAY_MIN,
    PHASE_A_U_EARLY_CONFIRM_MIN,
    STATE_PROVEN_NO_U_EXIT,
    STATE_RCI_ARMED,
    STATE_TRIGGERED,
    STATE_WAIT_RCI,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    TRIGGER_NAME,
    VOLUME_CONFIRM_PREDICATE,
    VOLUME_SOURCE_FILE,
    VOLUME_SOURCE_FUNCTION,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v27_analyze import research_fill_tuples

PATH_CACHE = RESEARCH_CACHE / "branch_u_rci_volume_confirm_exit_v1"
PX_EPS = 1e-12
LIFECYCLE_HARVEST_PATH = Path(lifecycle_mod.__file__).resolve()
THIS_PATH = Path(__file__).resolve()
BARS_PATH = Path(__file__).resolve().parents[1] / "simple_tech_entry_family" / "bars.py"
STAGES_PATH = Path(__file__).resolve().parents[1] / "simple_tech_entry_family" / "stages.py"
V26_HARVEST_PATH = Path(__file__).resolve().parent / "v26_harvest.py"
VWAP_HARVEST_PATH = Path(__file__).resolve().parent / "branch_u_rci_then_vwap_exit_v1_harvest.py"

REQUIRED_TEXTS = {
    "RCI_PRED": "return None if r is None else bool(r <= float(RCI_CROSS_LEVEL))",
    "TIE": "if end_t is not None and ft + 1e-12 >= float(end_t):",
    "SCAN": "_first_bar_flag(tf1, start_t=float(fill_t), end_t=u_end, pred=pred)",
    "ARM_KEY": "U_RCI_RE_OVERSOLD",
}
VOLUME_ACCUM_TEXT = 'slot["volume"] += dvol'
VOLUME_PASSTHROUGH_TEXT = 'vol = float(bars["volume"][i])'
THIS_PRED_TEXT = "bool(cl < clp and v > vp)"
F_VOLUME_DETERIORATION_TEXT = "float(vol) < float(prev_v)"
ENTRY_VOLUME_CONFIRM_TEXT = "return vol >= float(VOLUME_MULT) * med"
VWAP_CONFIRM_TEXT = "return None if cl is None or vw is None else bool(cl < vw)"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _lineno(text: str, value: str) -> Optional[int]:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == value:
            return int(node.lineno)
    return None


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def duplicate_architecture_audit() -> dict[str, Any]:
    """State-machine / predicate identity, not name match."""
    life = LIFECYCLE_HARVEST_PATH.read_text(encoding="utf-8") if LIFECYCLE_HARVEST_PATH.is_file() else ""
    v26 = V26_HARVEST_PATH.read_text(encoding="utf-8") if V26_HARVEST_PATH.is_file() else ""
    stages = STAGES_PATH.read_text(encoding="utf-8") if STAGES_PATH.is_file() else ""
    vwap_h = VWAP_HARVEST_PATH.read_text(encoding="utf-8") if VWAP_HARVEST_PATH.is_file() else ""
    this = THIS_PATH.read_text(encoding="utf-8") if THIS_PATH.is_file() else ""
    compared = [
        {
            "id": "V26_F_VOLUME_DETERIORATION",
            "predicate": "close>ema9 AND volume[i]<volume[i-1]",
            "same": False,
            "why": "opposite volume direction; includes EMA; not RCI ARM then later down-close; not UNPROVEN fill EXIT state machine",
        },
        {
            "id": "ENTRY_S5_VOLUME_CONFIRM",
            "predicate": "volume >= VOLUME_MULT * median(lookback)",
            "same": False,
            "why": "ENTRY filter with rolling median and multiplier; not post-fill UNPROVEN EXIT",
        },
        {
            "id": "LIFECYCLE_U_INVENTORY",
            "predicate": "no down-close AND volume-expansion U primitive",
            "same": False,
            "why": "Lifecycle U primitives are EMA/BB/RCI/VWAP/HHHL; DOWN_VOLUME_EXPANSION_1M is not among them",
        },
        {
            "id": "UNPROVEN_RCI_REOVERSOLD_THEN_VWAP_LOSS_V1",
            "predicate": "RCI ARM then later Close<VWAP",
            "same": False,
            "why": "same ARM, different confirm (VWAP not participation expansion); family CLOSED CASE C",
        },
        {
            "id": "VCIE_V2_VOLUME_CONFIRMED",
            "predicate": "VCIE entry arm",
            "same": False,
            "why": "different family; not Simple-Tech Branch U EXIT",
        },
        {
            "id": "V29_TERMINAL_SEQUENCE",
            "predicate": "3m EMA damage origin then reclaim/price/volume sequences",
            "same": False,
            "why": "origin is FIRST_3M_EMA_STRUCTURE_LOSS_ONSET, not FILL_IN_UNPROVEN_STATE",
        },
    ]
    this_has_pred = THIS_PRED_TEXT in this
    life_has_this = "DOWN_VOLUME_EXPANSION_1M" in life or THIS_PRED_TEXT in life
    v26_is_expansion_down = "Close[i] < Close[i-1]" in v26 and "Volume[i] > Volume[i-1]" in v26
    vwap_is_this = THIS_PRED_TEXT in vwap_h and "DOWN_VOLUME_EXPANSION_1M" in vwap_h
    stages_is_this = THIS_PRED_TEXT in stages
    duplicate = bool(life_has_this or v26_is_expansion_down or vwap_is_this or stages_is_this or (not this_has_pred))
    if not this_has_pred:
        return {"ok": False, "DUPLICATE_ARCHITECTURE": True, "blocker": "THIS_PRED_TEXT_MISSING", "compared": compared}
    return {
        "ok": True,
        "DUPLICATE_ARCHITECTURE": bool(duplicate),
        "compared": compared,
        "F_VOLUME_DETERIORATION_IN_V26": F_VOLUME_DETERIORATION_TEXT in v26,
        "ENTRY_VOLUME_CONFIRM_IN_STAGES": ENTRY_VOLUME_CONFIRM_TEXT in stages,
        "VWAP_CONFIRM_IN_PRIOR": VWAP_CONFIRM_TEXT in vwap_h,
        "THIS_PRED_PRESENT": this_has_pred,
        "name_match_ignored": True,
        "blocker": "DUPLICATE_ARCHITECTURE" if duplicate else None,
    }


def recover_sequence_predicates() -> dict[str, Any]:
    if not LIFECYCLE_HARVEST_PATH.is_file():
        return {"ok": False, "blocker": "LIFECYCLE_SOURCE_MISSING"}
    life = LIFECYCLE_HARVEST_PATH.read_text(encoding="utf-8")
    missing = [k for k, t in REQUIRED_TEXTS.items() if t not in life]
    if missing:
        return {"ok": False, "blocker": f"PREDICATE_TEXT_MISSING:{missing}"}
    flag_line = None
    tree = ast.parse(life)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_first_bar_flag":
            flag_line = int(node.lineno)
    if flag_line is None:
        return {"ok": False, "blocker": "FIRST_BAR_FLAG_FN_MISSING"}
    if not BARS_PATH.is_file() or VOLUME_ACCUM_TEXT not in BARS_PATH.read_text(encoding="utf-8"):
        return {"ok": False, "blocker": "VOLUME_BAR_SOURCE_MISSING"}
    stages = STAGES_PATH.read_text(encoding="utf-8") if STAGES_PATH.is_file() else ""
    if VOLUME_PASSTHROUGH_TEXT not in stages:
        return {"ok": False, "blocker": "VOLUME_TF1_PASSTHROUGH_MISSING"}
    this = THIS_PATH.read_text(encoding="utf-8")
    if THIS_PRED_TEXT not in this:
        return {"ok": False, "blocker": "VOLUME_CONFIRM_PRED_TEXT_MISSING"}
    sha = _sha(LIFECYCLE_HARVEST_PATH)
    return {
        "ok": True,
        "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
        "SOURCE_SHA": sha,
        "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
        "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        "FIRST_BAR_FLAG_LINE": flag_line,
        "RCI": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "eval_lifecycle.pred_rci_os",
            "SOURCE_LINE": _lineno(life, ARM_PRIMITIVE),
            "SOURCE_SHA": sha,
            "RCI_EXACT_PREDICATE": f"rci9[i] <= {float(RCI_CROSS_LEVEL)} on completed 1m bar; None if rci missing",
            "RCI_EVENT_SEMANTICS": "finalize_t of first completed 1m bar after fill_t and strictly before be_t with rci9<=-80",
            "TIMEFRAME": "1m / tf1",
        },
        "VOLUME": {
            "SOURCE_FILE": VOLUME_SOURCE_FILE,
            "SOURCE_FUNCTION": VOLUME_SOURCE_FUNCTION,
            "SOURCE_LINE": None,
            "SOURCE_SHA": _sha(BARS_PATH),
            "ARRAY_SOURCE": "src/research/simple_tech_redesign/ptf_post_be_rca_harvest.py::stream_day -> stages.attach_indicators passthrough of bars['volume']",
            "STAGES_SOURCE_SHA": _sha(STAGES_PATH),
            "CONFIRM_SOURCE_FILE": "src/research/simple_tech_redesign/branch_u_rci_volume_confirm_exit_v1_harvest.py",
            "CONFIRM_SOURCE_FUNCTION": "pred_down_volume_expansion",
            "VOLUME_EXACT_PREDICATE": VOLUME_CONFIRM_PREDICATE,
            "VOLUME_EVENT_SEMANTICS": (
                "finalize_t of first completed 1m bar with Close[i]<Close[i-1] AND Volume[i]>Volume[i-1] "
                "strictly after RCI event_t (searchsorted side=right on rci_t) and strictly before be_t. "
                "No multiplier, percentile, z-score, median, ATR, bps, or time-of-day correction."
            ),
            "TIMEFRAME": "1m / tf1",
            "FIXED_THRESHOLD": False,
        },
        "NOT_STATIC_AND": True,
        "SAME_BAR_RCI_AND_VOLUME_CONFIRM_DOES_NOT_EXIT": True,
        "VWAP_USED_AS_CONFIRM": False,
        "EMA_USED_AS_CONFIRM": False,
    }


def pred_rci_os(ind: dict[str, Any], i: int) -> Optional[bool]:
    r = _arr_at(ind, "rci9", i)
    return None if r is None else bool(r <= float(RCI_CROSS_LEVEL))


def pred_down_volume_expansion(ind: dict[str, Any], i: int) -> Optional[bool]:
    if int(i) < 1:
        return None
    cl = _arr_at(ind, "close", i)
    clp = _arr_at(ind, "close", i - 1)
    v = _arr_at(ind, "volume", i)
    vp = _arr_at(ind, "volume", i - 1)
    if cl is None or clp is None or v is None or vp is None:
        return None
    return bool(cl < clp and v > vp)


def _race(be_t: Optional[float], ev_t: Optional[float]) -> str:
    if ev_t is None and be_t is None:
        return "NEITHER"
    if ev_t is None:
        return "BE_FIRST"
    if be_t is None:
        return "EVENT_FIRST"
    if float(ev_t) + PX_EPS < float(be_t):
        return "EVENT_FIRST"
    if float(be_t) + PX_EPS < float(ev_t):
        return "BE_FIRST"
    return "SAME_TIMESTAMP"


def volume_availability(tf1: dict[str, Any], *, start_t: float, end_t: Optional[float]) -> dict[str, Any]:
    fin = tf1.get("finalize_t")
    vol = tf1.get("volume")
    close = tf1.get("close")
    if fin is None or vol is None or close is None:
        return {"array_ok": False, "evaluable_n": 0, "skip_n": 0, "finite_volume_bar_n": 0, "bar_n": 0}
    n = int(fin.size)
    finite_vol = 0
    for i in range(n):
        if _arr_at(tf1, "volume", i) is not None:
            finite_vol += 1
    j = int(np.searchsorted(fin, float(start_t), side="right"))
    evaluable = 0
    skip = 0
    for i in range(j, n):
        ft = float(fin[i]) if fin[i] == fin[i] else None
        if ft is None:
            continue
        if end_t is not None and ft + PX_EPS >= float(end_t):
            break
        p = pred_down_volume_expansion(tf1, int(i))
        if p is None:
            skip += 1
        else:
            evaluable += 1
    return {
        "array_ok": True,
        "evaluable_n": evaluable,
        "skip_n": skip,
        "finite_volume_bar_n": finite_vol,
        "bar_n": n,
    }


def _leak0() -> dict[str, Any]:
    return {
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "CANDIDATE_EXIT_MISS_N": 0,
        "FILL_PACK_INCOMPLETE_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "PRE_BAR_EXIT_N": 0,
        "PRE_FILL_EXIT_N": 0,
        "POST_BE_U_TRIGGER_N": 0,
        "PREDICATE_DRIFT_N": 0,
        "TF1_MISSING_N": 0,
        "VOLUME_ARRAY_MISSING_N": 0,
        "STATIC_AND_SAME_BAR_EXIT_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "PERSISTENCE_SEARCH_N": 0,
        "K_SEARCH_N": 0,
        "AND_OR_SEARCH_N": 0,
        "REVERSE_SEQUENCE_N": 0,
        "PAIR_ENUMERATION_N": 0,
        "PNL_SELECTION_N": 0,
        "VWAP_CONFIRM_N": 0,
        "EMA_CONFIRM_N": 0,
    }


def eval_sequence(
    tf1: dict[str, Any],
    tf3: dict[str, Any],
    board: dict[str, Any],
    meta: dict[str, Any],
    *,
    t0: float,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    control_exit: Optional[dict[str, Any]],
    leak: dict[str, Any],
    emit_exit: bool,
) -> dict[str, Any]:
    ctrl = dict(control_exit) if control_exit else None
    out: dict[str, Any] = {
        "state_end": STATE_WAIT_RCI,
        "be_reached": False,
        "be_t": None,
        "rci_armed": False,
        "rci_t": None,
        "volume_confirm": False,
        "vol_t": None,
        "be_after_rci_before_confirm": False,
        "triggered": False,
        "would_trigger": False,
        "trigger_t": None,
        "tf1_ok": True,
        "volume_array_ok": True,
        "volume_availability": None,
        "race_rci": "NEITHER",
        "race_vol": "NOT_ARMED",
        "lifecycle_rci_hit": False,
        "predicate_parity_ok": True,
        "exit_miss_fallback_session_close": False,
        "treatment_exit": ctrl,
        "same_bar_static_and_blocked": False,
    }
    if (tf1 or {}).get("finalize_t") is None:
        out["tf1_ok"] = False
        leak["TF1_MISSING_N"] = int(leak.get("TF1_MISSING_N") or 0) + 1
        return out
    if (tf1 or {}).get("volume") is None:
        out["volume_array_ok"] = False
        leak["VOLUME_ARRAY_MISSING_N"] = int(leak.get("VOLUME_ARRAY_MISSING_N") or 0) + 1
    life = eval_lifecycle(
        tf1 or {},
        tf3 or {},
        board,
        meta,
        t0=float(t0),
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        am_end=float(sess_end),
        leak=leak,
    )
    be_reached = bool(life.get("break_even_reached"))
    be_t = _f(life.get("be_t"))
    out["be_reached"] = be_reached
    out["be_t"] = be_t
    u = dict(life.get("u") or {})
    life_rci = bool(u.get(ARM_PRIMITIVE))
    life_rci_t = _f(u.get(f"{ARM_PRIMITIVE}_t"))
    out["lifecycle_rci_hit"] = life_rci
    u_end = float(be_t) if be_reached and be_t is not None else None
    rci_hit, rci_t = _first_bar_flag(tf1, start_t=float(fill_t), end_t=u_end, pred=pred_rci_os)
    rci_t = _f(rci_t)
    if bool(rci_hit) != bool(life_rci) or (rci_t is None) != (life_rci_t is None):
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
        out["predicate_parity_ok"] = False
    elif rci_t is not None and life_rci_t is not None and abs(float(rci_t) - float(life_rci_t)) > PX_EPS:
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
        out["predicate_parity_ok"] = False
    race_rci = _race(be_t, rci_t if rci_hit else None)
    out["race_rci"] = race_rci
    if (not rci_hit) or rci_t is None or race_rci in ("BE_FIRST", "SAME_TIMESTAMP"):
        if be_reached:
            out["state_end"] = STATE_PROVEN_NO_U_EXIT
        out["treatment_exit"] = ctrl
        return out
    out["rci_armed"] = True
    out["rci_t"] = rci_t
    out["state_end"] = STATE_RCI_ARMED
    avail = volume_availability(tf1, start_t=float(rci_t), end_t=u_end)
    out["volume_availability"] = avail
    out["volume_array_ok"] = bool(avail.get("array_ok"))
    if not avail.get("array_ok"):
        leak["VOLUME_ARRAY_MISSING_N"] = int(leak.get("VOLUME_ARRAY_MISSING_N") or 0) + 1
    vol_hit, vol_t = _first_bar_flag(tf1, start_t=float(rci_t), end_t=u_end, pred=pred_down_volume_expansion)
    vol_t = _f(vol_t)
    race_vol = _race(be_t, vol_t if vol_hit else None)
    out["race_vol"] = race_vol
    if vol_hit and vol_t is not None and abs(float(vol_t) - float(rci_t)) <= PX_EPS:
        leak["STATIC_AND_SAME_BAR_EXIT_N"] = int(leak.get("STATIC_AND_SAME_BAR_EXIT_N") or 0) + 1
        out["same_bar_static_and_blocked"] = True
        vol_hit = False
        vol_t = None
        race_vol = _race(be_t, None)
        out["race_vol"] = race_vol
    out["be_after_rci_before_confirm"] = bool(be_reached and (not vol_hit))
    if (not vol_hit) or vol_t is None or race_vol in ("BE_FIRST", "SAME_TIMESTAMP"):
        if be_reached:
            out["state_end"] = STATE_PROVEN_NO_U_EXIT
        out["treatment_exit"] = ctrl
        return out
    out["volume_confirm"] = True
    out["vol_t"] = vol_t
    out["would_trigger"] = True
    out["trigger_t"] = vol_t
    if float(vol_t) + PX_EPS < float(fill_t):
        leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
        out["treatment_exit"] = ctrl
        return out
    if be_reached and be_t is not None and float(vol_t) + PX_EPS >= float(be_t):
        leak["POST_BE_U_TRIGGER_N"] = int(leak.get("POST_BE_U_TRIGGER_N") or 0) + 1
        out["state_end"] = STATE_PROVEN_NO_U_EXIT
        out["would_trigger"] = False
        out["volume_confirm"] = False
        out["treatment_exit"] = ctrl
        return out
    if not emit_exit:
        return out
    pack = first_causal_bid_evidence(
        board, meta, event_t=float(vol_t), fill_px=float(fill_px), sess_end=float(sess_end), leak=leak
    )
    extra = {
        "trigger_event_time": vol_t,
        "trigger_name": TRIGGER_NAME,
        "arm_primitive": ARM_PRIMITIVE,
        "confirm_primitive": CONFIRM_PRIMITIVE,
        "rci_t": rci_t,
        "vol_t": vol_t,
        "bid_search_start_t": vol_t,
        "be_t": be_t,
        "race_rci": race_rci,
        "race_vol": race_vol,
    }
    tx = _pack_exit(reason=CANDIDATE_EXIT_REASON, fill_px=float(fill_px), pack=pack, extra=extra)
    if pack.get("exit_t") is not None:
        tx["latency_sec"] = float(pack["exit_t"]) - float(vol_t)
        if float(pack["exit_t"]) + PX_EPS < float(vol_t):
            leak["FUTURE_QUOTE_CARRYBACK_N"] = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + 1
        if float(pack["exit_t"]) + PX_EPS < float(fill_t):
            leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
    if bool(tx.get("miss")):
        leak["CANDIDATE_EXIT_MISS_N"] = int(leak.get("CANDIDATE_EXIT_MISS_N") or 0) + 1
        out["treatment_exit"] = ctrl
        out["triggered"] = False
        out["exit_miss_fallback_session_close"] = True
        return out
    out["treatment_exit"] = tx
    out["triggered"] = True
    out["state_end"] = STATE_TRIGGERED
    return out


def _cls(row: dict[str, Any]) -> str:
    life = dict(row.get("lifecycle") or {})
    return residual_class(path_type=str(row.get("path_type") or "OTHER"), be_reached=bool(life.get("break_even_reached")))


def load_unconstrained_identity() -> dict[str, Any]:
    sha = spec_sha256_lifecycle()
    rows: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE):
            return {"ok": False, "blocker": f"FORBIDDEN_DAY:{day}"}
        body = load_lifecycle_day_cache(LIFECYCLE_CACHE / f"day_{day}.json", sha)
        if not body.get("ok"):
            return {"ok": False, "blocker": f"lifecycle_cache_missing:{day}"}
        rows.extend([dict(r) for r in list(body.get("rows") or [])])
    fills = [r for r in rows if r.get("actual_filled")]
    core = sum(1 for r in fills if str(r.get("fill_role") or "") == "CORE")
    added = sum(1 for r in fills if str(r.get("fill_role") or "") == "ADDED")
    fill_hash = set_hash(research_fill_tuples(rows))
    ok = (
        len(fills) == int(TOTAL_RESEARCH_FILL_N_EXPECTED)
        and core == int(CORE_E4_FILL_N_EXPECTED)
        and added == int(ADDED_FILL_N_EXPECTED)
        and str(fill_hash) == str(RESEARCH_FILL_SET_HASH_EXPECTED)
    )
    return {
        "ok": bool(ok),
        "blocker": None if ok else "RESEARCH_FILL_IDENTITY_MISMATCH",
        "rows": rows,
        "fills": fills,
        "TOTAL_RESEARCH_FILL_N": len(fills),
        "CORE_FILL_N": core,
        "ADDED_FILL_N": added,
        "RESEARCH_FILL_SET_HASH": fill_hash,
        "SIGNAL_N": len(rows),
    }


def harvest_unconstrained_day(day: str, rows: list[dict[str, Any]], *, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"unconst_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    leak = _leak0()
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    slim = []
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"UNCONSTRAINED {day} rci-then-volume symbols={len(symbols)} filled={len(filled)}", flush=True)
        packed = stream_day(day, capture, symbols, leak)
        for rec in filled:
            t0 = _f(rec.get("t0") or rec.get("signal_time"))
            fill_t = _f(rec.get("fill_t") or rec.get("fill_time"))
            fill_px = _f(rec.get("fill_price"))
            if t0 is None or fill_t is None or fill_px is None:
                leak["FILL_PACK_INCOMPLETE_N"] = int(leak.get("FILL_PACK_INCOMPLETE_N") or 0) + 1
                slim.append(
                    {
                        "date": rec.get("date") or day,
                        "symbol": rec.get("symbol"),
                        "t0": rec.get("t0"),
                        "fill_role": rec.get("fill_role"),
                        "path_type": rec.get("path_type"),
                        "residual_class": _cls(rec),
                        "candidate_path": {"tf1_ok": False, "rci_armed": False, "volume_confirm": False},
                    }
                )
                continue
            rec["fill_t"] = float(fill_t)
            rec["t0"] = float(t0)
            rec["date"] = rec.get("date") or day
            sym = _bare(rec.get("symbol"))
            by_tf = (packed.get("tf_by_sym") or {}).get(sym) or {}
            seq = eval_sequence(
                by_tf.get("tf1") or {},
                by_tf.get("tf3") or {},
                (packed.get("views") or {}).get(sym) or {},
                (packed.get("metas") or {}).get(sym) or {},
                t0=float(t0),
                fill_t=float(fill_t),
                fill_px=float(fill_px),
                sess_end=float(packed["am_end"]),
                control_exit=None,
                leak=leak,
                emit_exit=False,
            )
            slim.append(
                {
                    "date": rec.get("date"),
                    "symbol": rec.get("symbol"),
                    "t0": rec.get("t0"),
                    "fill_role": rec.get("fill_role"),
                    "path_type": rec.get("path_type"),
                    "residual_class": _cls(rec),
                    "candidate_path": {
                        "state_end": seq.get("state_end"),
                        "be_reached": seq.get("be_reached"),
                        "be_t": seq.get("be_t"),
                        "rci_armed": seq.get("rci_armed"),
                        "rci_t": seq.get("rci_t"),
                        "volume_confirm": seq.get("volume_confirm"),
                        "vol_t": seq.get("vol_t"),
                        "be_after_rci_before_confirm": seq.get("be_after_rci_before_confirm"),
                        "tf1_ok": seq.get("tf1_ok"),
                        "volume_array_ok": seq.get("volume_array_ok"),
                        "volume_availability": seq.get("volume_availability"),
                        "predicate_parity_ok": seq.get("predicate_parity_ok"),
                        "race_rci": seq.get("race_rci"),
                        "race_vol": seq.get("race_vol"),
                    },
                }
            )
        del packed
        gc.collect()
    body = {"ok": True, "date": day, "spec_sha": spec_sha, "rows": slim, "leak": leak}
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return body


def _share(counts: dict[str, int], n: int) -> dict[str, Any]:
    if not n:
        return {"top": None, "n": 0, "abs_share": None, "warning_gt_50pct": False}
    top_k = max(counts, key=lambda k: int(counts[k]))
    share = float(counts[top_k]) / float(n)
    return {
        "top": top_k,
        "n": int(counts[top_k]),
        "abs_share": share,
        "warning_gt_50pct": bool(share > float(CONCENTRATION_MAX_SHARE) + 1e-15),
    }


def unconstrained_class_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes = (
        "U_EARLY_NEVER_BE",
        "P_EARLY_AFTER_BE",
        "PROTECTED_GOOD",
        "PROTECTED_DIP",
        "P_PROFIT_THEN_FAILURE",
        "OTHER",
    )
    by: dict[str, dict[str, int]] = {
        c: {"N": 0, "RCI_ARM_N": 0, "RCI_THEN_VOLUME_CONFIRM_N": 0, "BE_AFTER_RCI_BEFORE_CONFIRM_N": 0} for c in classes
    }
    vol_ok_n = 0
    vol_eval_n = 0
    vol_skip_n = 0
    array_fail_n = 0
    for r in rows:
        cls = str(r.get("residual_class") or "OTHER")
        if cls not in by:
            cls = "OTHER"
        cp = dict(r.get("candidate_path") or {})
        by[cls]["N"] += 1
        if cp.get("rci_armed"):
            by[cls]["RCI_ARM_N"] += 1
        if cp.get("volume_confirm"):
            by[cls]["RCI_THEN_VOLUME_CONFIRM_N"] += 1
        if cp.get("be_after_rci_before_confirm"):
            by[cls]["BE_AFTER_RCI_BEFORE_CONFIRM_N"] += 1
        if cp.get("volume_array_ok") is False or cp.get("tf1_ok") is False:
            array_fail_n += 1
        else:
            vol_ok_n += 1
        avail = dict(cp.get("volume_availability") or {})
        vol_eval_n += int(avail.get("evaluable_n") or 0)
        vol_skip_n += int(avail.get("skip_n") or 0)
    overall = {
        "N": len(rows),
        "RCI_ARM_N": sum(v["RCI_ARM_N"] for v in by.values()),
        "RCI_THEN_VOLUME_CONFIRM_N": sum(v["RCI_THEN_VOLUME_CONFIRM_N"] for v in by.values()),
        "BE_AFTER_RCI_BEFORE_CONFIRM_N": sum(v["BE_AFTER_RCI_BEFORE_CONFIRM_N"] for v in by.values()),
    }
    u_confirms = [
        r
        for r in rows
        if str(r.get("residual_class") or "") == "U_EARLY_NEVER_BE" and dict(r.get("candidate_path") or {}).get("volume_confirm")
    ]
    day_c: dict[str, int] = {}
    sym_c: dict[str, int] = {}
    for r in u_confirms:
        day_c[str(r.get("date") or "")] = int(day_c.get(str(r.get("date") or ""), 0) or 0) + 1
        sym_c[_bare(r.get("symbol"))] = int(sym_c.get(_bare(r.get("symbol")), 0) or 0) + 1
    u_n = int(by["U_EARLY_NEVER_BE"]["RCI_THEN_VOLUME_CONFIRM_N"])
    concentration = {
        "hit_day_n": len({str(r.get("date") or "") for r in u_confirms if str(r.get("date") or "")}),
        "hit_symbol_n": len({_bare(r.get("symbol")) for r in u_confirms if _bare(r.get("symbol"))}),
        "top_U_EARLY_confirm_day": _share(day_c, u_n),
        "top_U_EARLY_confirm_symbol": _share(sym_c, u_n),
        "population": "U_EARLY_NEVER_BE confirms only",
    }
    return {
        "overall": overall,
        "by_class": by,
        "concentration": concentration,
        "volume_availability": {
            "fills_volume_array_ok_n": vol_ok_n,
            "fills_volume_array_fail_n": array_fail_n,
            "post_arm_evaluable_bar_n": vol_eval_n,
            "post_arm_skip_unevaluable_bar_n": vol_skip_n,
        },
        "SPEC_CHANGED_AFTER_AUDIT": False,
        "PNL_USED_FOR_SELECTION": False,
    }


def phase_a_gates(audit: dict[str, Any], *, identity_ok: bool, leak_ok: bool, pred_ok: bool, dup_ok: bool) -> dict[str, Any]:
    byc = dict(audit.get("by_class") or {})
    u_arm = int((byc.get("U_EARLY_NEVER_BE") or {}).get("RCI_ARM_N") or 0)
    u_conf = int((byc.get("U_EARLY_NEVER_BE") or {}).get("RCI_THEN_VOLUME_CONFIRM_N") or 0)
    p_conf = int((byc.get("P_EARLY_AFTER_BE") or {}).get("RCI_THEN_VOLUME_CONFIRM_N") or 0)
    g_conf = int((byc.get("PROTECTED_GOOD") or {}).get("RCI_THEN_VOLUME_CONFIRM_N") or 0)
    d_conf = int((byc.get("PROTECTED_DIP") or {}).get("RCI_THEN_VOLUME_CONFIRM_N") or 0)
    conc = dict(audit.get("concentration") or {})
    hit_day_n = int(conc.get("hit_day_n") or 0)
    day_share = dict(conc.get("top_U_EARLY_confirm_day") or {})
    sym_share = dict(conc.get("top_U_EARLY_confirm_symbol") or {})
    vol = dict(audit.get("volume_availability") or {})
    vol_ok = int(vol.get("fills_volume_array_fail_n") or 0) == 0
    integrity = bool(identity_ok and leak_ok and pred_ok and dup_ok and vol_ok)
    gates = {
        "A_integrity": integrity,
        "B_U_EARLY_CONFIRM_N_ge_6": u_conf >= int(PHASE_A_U_EARLY_CONFIRM_MIN),
        "C_P_EARLY_CONFIRM_N_eq_0": p_conf == 0,
        "D_GOOD_CONFIRM_N_eq_0": g_conf == 0,
        "E_DIP_CONFIRM_N_eq_0": d_conf == 0,
        "F_hit_day_n_ge_3": hit_day_n >= int(PHASE_A_HIT_DAY_MIN),
        "G_top_hit_day_share_le_50pct": not bool(day_share.get("warning_gt_50pct")),
        "H_top_hit_symbol_share_le_50pct": not bool(sym_share.get("warning_gt_50pct")),
    }
    passed = all(gates.values())
    return {
        "gates": gates,
        "passed": bool(passed),
        "U_EARLY_ARM_N": u_arm,
        "U_EARLY_CONFIRM_N": u_conf,
        "P_EARLY_ARM_N": int((byc.get("P_EARLY_AFTER_BE") or {}).get("RCI_ARM_N") or 0),
        "P_EARLY_CONFIRM_N": p_conf,
        "GOOD_CONFIRM_N": g_conf,
        "DIP_CONFIRM_N": d_conf,
        "BE_AFTER_RCI_BEFORE_CONFIRM_N": int((byc.get("U_EARLY_NEVER_BE") or {}).get("BE_AFTER_RCI_BEFORE_CONFIRM_N") or 0),
        "P_EARLY_BE_AFTER_RCI_BEFORE_CONFIRM_N": int((byc.get("P_EARLY_AFTER_BE") or {}).get("BE_AFTER_RCI_BEFORE_CONFIRM_N") or 0),
        "GOOD_BE_AFTER_RCI_BEFORE_CONFIRM_N": int((byc.get("PROTECTED_GOOD") or {}).get("BE_AFTER_RCI_BEFORE_CONFIRM_N") or 0),
        "DIP_BE_AFTER_RCI_BEFORE_CONFIRM_N": int((byc.get("PROTECTED_DIP") or {}).get("BE_AFTER_RCI_BEFORE_CONFIRM_N") or 0),
        "hit_day_n": hit_day_n,
        "hit_symbol_n": int(conc.get("hit_symbol_n") or 0),
        "top_hit_day": day_share,
        "top_hit_symbol": sym_share,
        "PNL_USED_FOR_SELECTION": False,
        "PHASE_B_PROCEED": bool(passed),
    }


def attach_row(row: dict[str, Any], packed: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    rec = dict(row)
    rec["candidate_path"] = None
    if not rec.get("actual_filled"):
        rec["treatment_exit"] = None
        return rec
    t0 = _f(rec.get("t0") or rec.get("signal_time"))
    fill_t = _f(rec.get("fill_t") or rec.get("fill_time"))
    fill_px = _f(rec.get("fill_price"))
    ctrl = dict(rec.get("control_exit") or {})
    if t0 is None or fill_t is None or fill_px is None or not ctrl:
        leak["FILL_PACK_INCOMPLETE_N"] = int(leak.get("FILL_PACK_INCOMPLETE_N") or 0) + 1
        rec["candidate_path"] = {"tf1_ok": False, "triggered": False, "state_end": STATE_WAIT_RCI, "rci_armed": False}
        rec["treatment_exit"] = ctrl or None
        return rec
    rec["fill_t"] = float(fill_t)
    rec["t0"] = float(t0)
    rec["date"] = rec.get("date")
    sym = _bare(rec.get("symbol"))
    by_tf = (packed.get("tf_by_sym") or {}).get(sym) or {}
    seq = eval_sequence(
        by_tf.get("tf1") or {},
        by_tf.get("tf3") or {},
        (packed.get("views") or {}).get(sym) or {},
        (packed.get("metas") or {}).get(sym) or {},
        t0=float(t0),
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        sess_end=float(packed["am_end"]),
        control_exit=ctrl,
        leak=leak,
        emit_exit=True,
    )
    rec["candidate_path"] = seq
    rec["treatment_exit"] = seq.get("treatment_exit")
    return rec


def _arm_public(arm: dict[str, Any]) -> dict[str, Any]:
    body = dict(arm or {})
    body.pop("candidates", None)
    return body


def harvest_day(day: str, *, cohort: str, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    ubody = load_u_day(day, cohort=cohort)
    if not ubody.get("ok") and not list(ubody.get("rows") or []):
        return {"ok": False, "blocker": f"u_cache_missing:{day}", "date": day}
    rows = [dict(r) for r in list(ubody.get("rows") or [])]
    leak = _leak0()
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"{cohort} {day} rci-then-volume symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
        packed = stream_day(day, capture, symbols, leak)
        rows = [attach_row(r, packed, leak) for r in rows]
        del packed
        gc.collect()
    occ = replay_causal_day(rows)
    if not occ.get("control_sot_ok"):
        return {"ok": False, "blocker": f"occupancy_sot_fail:{day}", "date": day, "leak": leak}
    causes = incremental_causes(day, occ)
    slim = []
    for r in rows:
        cp = dict(r.get("candidate_path") or {})
        slim.append(
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "t0": r.get("t0"),
                "fill_role": r.get("fill_role"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "actual_filled": r.get("actual_filled"),
                "executable_signal": r.get("executable_signal"),
                "path_type": r.get("path_type"),
                "control_exit": r.get("control_exit"),
                "treatment_exit": r.get("treatment_exit"),
                "candidate_path": {
                    "state_end": cp.get("state_end"),
                    "be_reached": cp.get("be_reached"),
                    "be_t": cp.get("be_t"),
                    "triggered": cp.get("triggered"),
                    "would_trigger": cp.get("would_trigger"),
                    "trigger_t": cp.get("trigger_t"),
                    "rci_armed": cp.get("rci_armed"),
                    "rci_t": cp.get("rci_t"),
                    "volume_confirm": cp.get("volume_confirm"),
                    "vol_t": cp.get("vol_t"),
                    "be_after_rci_before_confirm": cp.get("be_after_rci_before_confirm"),
                    "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
                    "tf1_ok": cp.get("tf1_ok"),
                    "volume_array_ok": cp.get("volume_array_ok"),
                    "predicate_parity_ok": cp.get("predicate_parity_ok"),
                    "race_rci": cp.get("race_rci"),
                    "race_vol": cp.get("race_vol"),
                    "race": cp.get("race_vol") if cp.get("rci_armed") else cp.get("race_rci"),
                    "same_bar_static_and_blocked": cp.get("same_bar_static_and_blocked"),
                }
                if cp
                else None,
            }
        )
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "rows": slim,
        "leak": leak,
        "incremental_causes": causes,
        "control": _arm_public(dict(occ.get("control") or {})),
        "treatment": _arm_public(dict(occ.get("treatment") or {})),
        "control_sot_ok": occ.get("control_sot_ok"),
        "treatment_sot_ok": occ.get("treatment_sot_ok"),
        "leftover_ok": occ.get("leftover_ok"),
        "pre_divergence_ok": occ.get("pre_divergence_ok"),
    }
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return body
