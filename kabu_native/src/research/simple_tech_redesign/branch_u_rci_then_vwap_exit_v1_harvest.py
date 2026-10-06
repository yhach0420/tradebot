"""RCI ARM then strictly later VWAP CONFIRM sequence. Exact Lifecycle predicates. Not static AND."""
from __future__ import annotations

import ast
import gc
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

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
from research.simple_tech_redesign.branch_u_rci_then_vwap_exit_v1_spec import (
    ARM_PRIMITIVE,
    CANDIDATE_EXIT_REASON,
    CONFIRM_PRIMITIVE,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STATE_PROVEN_NO_U_EXIT,
    STATE_RCI_ARMED,
    STATE_TRIGGERED,
    STATE_WAIT_RCI,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    TRIGGER_NAME,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite
from research.simple_tech_redesign.v27_analyze import research_fill_tuples

PATH_CACHE = RESEARCH_CACHE / "branch_u_rci_then_vwap_exit_v1"
PX_EPS = 1e-12
LIFECYCLE_HARVEST_PATH = Path(lifecycle_mod.__file__).resolve()
STAGES_PATH = Path(__file__).resolve().parents[1] / "simple_tech_entry_family" / "stages.py"

REQUIRED_TEXTS = {
    "RCI_PRED": "return None if r is None else bool(r <= float(RCI_CROSS_LEVEL))",
    "VWAP_PRED": "return None if cl is None or vw is None else bool(cl < vw)",
    "TIE": "if end_t is not None and ft + 1e-12 >= float(end_t):",
    "SCAN": "_first_bar_flag(tf1, start_t=float(fill_t), end_t=u_end, pred=pred)",
    "ARM_KEY": "U_RCI_RE_OVERSOLD",
    "CONFIRM_KEY": "U_VWAP_CLOSE_LOSS",
}
VWAP_CUMULATIVE_TEXT = "num += float(close[i]) * vol"


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
    stages = STAGES_PATH.read_text(encoding="utf-8") if STAGES_PATH.is_file() else ""
    if VWAP_CUMULATIVE_TEXT not in stages or 'out["vwap"] = vwap' not in stages:
        return {"ok": False, "blocker": "VWAP_CUMULATIVE_SOURCE_MISSING"}
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
        "VWAP": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "eval_lifecycle.pred_vwap / stages.attach_indicators",
            "SOURCE_LINE": _lineno(life, CONFIRM_PRIMITIVE),
            "SOURCE_SHA": sha,
            "VWAP_EXACT_PREDICATE": "close[i] < vwap[i] on completed 1m bar; None if either missing",
            "VWAP_EVENT_SEMANTICS": (
                "finalize_t of first completed 1m bar with close<vwap strictly after RCI event_t "
                "(searchsorted side=right on rci_t) and strictly before be_t. Session cumulative 1m VWAP."
            ),
            "TIMEFRAME": "1m / tf1",
            "VWAP_DEFINITION": "session cumulative close*volume / volume in stages.attach_indicators",
            "STAGES_SOURCE_SHA": _sha(STAGES_PATH),
        },
        "NOT_STATIC_AND": True,
        "SAME_BAR_RCI_AND_VWAP_DOES_NOT_EXIT": True,
    }


def pred_rci_os(ind: dict[str, Any], i: int) -> Optional[bool]:
    r = _arr_at(ind, "rci9", i)
    return None if r is None else bool(r <= float(RCI_CROSS_LEVEL))


def pred_vwap(ind: dict[str, Any], i: int) -> Optional[bool]:
    cl, vw = _arr_at(ind, "close", i), _arr_at(ind, "vwap", i)
    return None if cl is None or vw is None else bool(cl < vw)


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
        "STATIC_AND_SAME_BAR_EXIT_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "PERSISTENCE_SEARCH_N": 0,
        "K_SEARCH_N": 0,
        "AND_OR_SEARCH_N": 0,
        "REVERSE_SEQUENCE_N": 0,
        "PAIR_ENUMERATION_N": 0,
        "PNL_SELECTION_N": 0,
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
        "vwap_confirm": False,
        "vwap_t": None,
        "be_after_rci_before_vwap": False,
        "triggered": False,
        "would_trigger": False,
        "trigger_t": None,
        "tf1_ok": True,
        "race_rci": "NEITHER",
        "race_vwap": "NOT_ARMED",
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
    vwap_hit, vwap_t = _first_bar_flag(tf1, start_t=float(rci_t), end_t=u_end, pred=pred_vwap)
    vwap_t = _f(vwap_t)
    race_vwap = _race(be_t, vwap_t if vwap_hit else None)
    out["race_vwap"] = race_vwap
    if vwap_hit and vwap_t is not None and abs(float(vwap_t) - float(rci_t)) <= PX_EPS:
        leak["STATIC_AND_SAME_BAR_EXIT_N"] = int(leak.get("STATIC_AND_SAME_BAR_EXIT_N") or 0) + 1
        out["same_bar_static_and_blocked"] = True
        vwap_hit = False
        vwap_t = None
        race_vwap = _race(be_t, None)
        out["race_vwap"] = race_vwap
    out["be_after_rci_before_vwap"] = bool(be_reached and (not vwap_hit))
    if (not vwap_hit) or vwap_t is None or race_vwap in ("BE_FIRST", "SAME_TIMESTAMP"):
        if be_reached:
            out["state_end"] = STATE_PROVEN_NO_U_EXIT
        out["treatment_exit"] = ctrl
        return out
    out["vwap_confirm"] = True
    out["vwap_t"] = vwap_t
    out["would_trigger"] = True
    out["trigger_t"] = vwap_t
    if float(vwap_t) + PX_EPS < float(fill_t):
        leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
        out["treatment_exit"] = ctrl
        return out
    if be_reached and be_t is not None and float(vwap_t) + PX_EPS >= float(be_t):
        leak["POST_BE_U_TRIGGER_N"] = int(leak.get("POST_BE_U_TRIGGER_N") or 0) + 1
        out["state_end"] = STATE_PROVEN_NO_U_EXIT
        out["would_trigger"] = False
        out["vwap_confirm"] = False
        out["treatment_exit"] = ctrl
        return out
    if not emit_exit:
        return out
    pack = first_causal_bid_evidence(
        board, meta, event_t=float(vwap_t), fill_px=float(fill_px), sess_end=float(sess_end), leak=leak
    )
    extra = {
        "trigger_event_time": vwap_t,
        "trigger_name": TRIGGER_NAME,
        "arm_primitive": ARM_PRIMITIVE,
        "confirm_primitive": CONFIRM_PRIMITIVE,
        "rci_t": rci_t,
        "vwap_t": vwap_t,
        "bid_search_start_t": vwap_t,
        "be_t": be_t,
        "race_rci": race_rci,
        "race_vwap": race_vwap,
    }
    tx = _pack_exit(reason=CANDIDATE_EXIT_REASON, fill_px=float(fill_px), pack=pack, extra=extra)
    if pack.get("exit_t") is not None:
        tx["latency_sec"] = float(pack["exit_t"]) - float(vwap_t)
        if float(pack["exit_t"]) + PX_EPS < float(vwap_t):
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
        print(f"UNCONSTRAINED {day} rci-then-vwap symbols={len(symbols)} filled={len(filled)}", flush=True)
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
                        "candidate_path": {"tf1_ok": False, "rci_armed": False, "vwap_confirm": False},
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
                        "vwap_confirm": seq.get("vwap_confirm"),
                        "vwap_t": seq.get("vwap_t"),
                        "be_after_rci_before_vwap": seq.get("be_after_rci_before_vwap"),
                        "tf1_ok": seq.get("tf1_ok"),
                        "predicate_parity_ok": seq.get("predicate_parity_ok"),
                        "race_rci": seq.get("race_rci"),
                        "race_vwap": seq.get("race_vwap"),
                    },
                }
            )
        del packed
        gc.collect()
    body = {"ok": True, "date": day, "spec_sha": spec_sha, "rows": slim, "leak": leak}
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return body


def unconstrained_class_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes = (
        "U_EARLY_NEVER_BE",
        "P_EARLY_AFTER_BE",
        "PROTECTED_GOOD",
        "PROTECTED_DIP",
        "P_PROFIT_THEN_FAILURE",
        "OTHER",
    )
    by: dict[str, dict[str, int]] = {c: {"N": 0, "RCI_ARM_N": 0, "RCI_THEN_VWAP_CONFIRM_N": 0, "BE_AFTER_RCI_BEFORE_VWAP_N": 0} for c in classes}
    for r in rows:
        cls = str(r.get("residual_class") or "OTHER")
        if cls not in by:
            cls = "OTHER"
        cp = dict(r.get("candidate_path") or {})
        by[cls]["N"] += 1
        if cp.get("rci_armed"):
            by[cls]["RCI_ARM_N"] += 1
        if cp.get("vwap_confirm"):
            by[cls]["RCI_THEN_VWAP_CONFIRM_N"] += 1
        if cp.get("be_after_rci_before_vwap"):
            by[cls]["BE_AFTER_RCI_BEFORE_VWAP_N"] += 1
    overall = {
        "N": len(rows),
        "RCI_ARM_N": sum(v["RCI_ARM_N"] for v in by.values()),
        "RCI_THEN_VWAP_CONFIRM_N": sum(v["RCI_THEN_VWAP_CONFIRM_N"] for v in by.values()),
        "BE_AFTER_RCI_BEFORE_VWAP_N": sum(v["BE_AFTER_RCI_BEFORE_VWAP_N"] for v in by.values()),
    }
    return {"overall": overall, "by_class": by, "SPEC_CHANGED_AFTER_AUDIT": False}


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
    path = eval_sequence(
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
    rec["candidate_path"] = path
    rec["treatment_exit"] = path.get("treatment_exit")
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
        print(f"{cohort} {day} rci-then-vwap symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
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
                    "vwap_confirm": cp.get("vwap_confirm"),
                    "vwap_t": cp.get("vwap_t"),
                    "be_after_rci_before_vwap": cp.get("be_after_rci_before_vwap"),
                    "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
                    "tf1_ok": cp.get("tf1_ok"),
                    "predicate_parity_ok": cp.get("predicate_parity_ok"),
                    "race_rci": cp.get("race_rci"),
                    "race_vwap": cp.get("race_vwap"),
                    "race": cp.get("race_vwap") if cp.get("rci_armed") else cp.get("race_rci"),
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
