"""Harvest POST_BE_BB_STRUCTURE_LOSS_3M_V1 using exact Lifecycle P_BB_STRUCTURE_LOSS_3M."""
from __future__ import annotations

import ast
import gc
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from replay.pnl_yen import compute_pnl_yen_100
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign import exit_lifecycle_harvest as lifecycle_mod
from research.simple_tech_redesign.exit_lifecycle_harvest import _first_bar_flag, eval_lifecycle
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_spec import (
    CANDIDATE_EXIT_REASON,
    FORBIDDEN_INPUT_DAYS,
    LIFECYCLE_PRIMITIVE,
    MAX_RESEARCH_DATE,
    TRIGGER_NAME,
    V26_PRIMITIVE_ID,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite
from research.simple_tech_redesign.v26_harvest import primitive_true
from research.simple_tech_redesign.v27_harvest import primitive_known

PATH_CACHE = RESEARCH_CACHE / "branch_p_bb_structure_exit_v1"
PX_EPS = 1e-9
STATE_UNPROVEN = "UNPROVEN"
STATE_PROVEN = "PROVEN"
STATE_TRIGGERED = "TRIGGERED"
LIFECYCLE_HARVEST_PATH = Path(lifecycle_mod.__file__).resolve()
V27_HARVEST_PATH = Path(__file__).resolve().parent / "v27_harvest.py"
V26_HARVEST_PATH = Path(__file__).resolve().parent / "v26_harvest.py"

LIFECYCLE_SCAN_TEXT = (
    '("P_BB_STRUCTURE_LOSS_3M", lambda ind, i: primitive_known("D_BB_STRUCTURE_LOSS", ind, i), tf3)'
)
FIRST_BAR_FLAG_TEXT = '_first_bar_flag(tf, start_t=float(be["be_t"]), end_t=None, pred=pred)'
TF3_BUILD_TEXT = '("tf3", 180.0)'


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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _lineno_const(text: str, value: str) -> Optional[int]:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == value:
            return int(node.lineno)
    return None


def _extract_pid_branch(text: str, pid: str) -> Optional[str]:
    marker = f'pid == "{pid}"'
    i = text.find(marker)
    if i < 0:
        return None
    line_start = text.rfind("\n", 0, i)
    chunk = text[line_start + 1 if line_start >= 0 else 0 :]
    lines = chunk.splitlines()
    out = []
    for ln in lines[:8]:
        out.append(ln)
        if "return" in ln and pid in "".join(out):
            break
        if len(out) > 1 and ln.strip().startswith("elif pid") or (len(out) > 1 and ln.strip().startswith("if pid ==")):
            out = out[:-1]
            break
    return "\n".join(out).rstrip()


def recover_lifecycle_bb_predicate() -> dict[str, Any]:
    if not LIFECYCLE_HARVEST_PATH.is_file() or not V27_HARVEST_PATH.is_file() or not V26_HARVEST_PATH.is_file():
        return {"ok": False, "blocker": "SOURCE_FILE_MISSING"}
    life_text = LIFECYCLE_HARVEST_PATH.read_text(encoding="utf-8")
    v27_text = V27_HARVEST_PATH.read_text(encoding="utf-8")
    v26_text = V26_HARVEST_PATH.read_text(encoding="utf-8")
    if LIFECYCLE_SCAN_TEXT not in life_text:
        return {"ok": False, "blocker": "P_BB_SCAN_NOT_FOUND"}
    if FIRST_BAR_FLAG_TEXT not in life_text:
        return {"ok": False, "blocker": "FIRST_BAR_FLAG_SCAN_MISMATCH"}
    if TF3_BUILD_TEXT not in life_text:
        return {"ok": False, "blocker": "TF3_CONSTRUCTION_NOT_FOUND"}
    if 'ok("close", i) and ok("bb_mid", i)' not in v27_text:
        return {"ok": False, "blocker": "PRIMITIVE_KNOWN_D_BB_MISMATCH"}
    if "float(close) < float(bb_mid)" not in v26_text:
        return {"ok": False, "blocker": "PRIMITIVE_TRUE_D_BB_COMPARE_MISMATCH"}
    if f'pid == "{V26_PRIMITIVE_ID}"' not in v26_text or f'pid == "{V26_PRIMITIVE_ID}"' not in v27_text:
        return {"ok": False, "blocker": "D_BB_PID_NOT_FOUND"}
    life_line = _lineno_const(life_text, LIFECYCLE_PRIMITIVE)
    v27_line = _lineno_const(v27_text, V26_PRIMITIVE_ID)
    v26_line = _lineno_const(v26_text, V26_PRIMITIVE_ID)
    if life_line is None or v27_line is None or v26_line is None:
        return {"ok": False, "blocker": "LINE_RESTORE_FAILED"}
    known_branch = _extract_pid_branch(v27_text, V26_PRIMITIVE_ID)
    true_branch = _extract_pid_branch(v26_text, V26_PRIMITIVE_ID)
    if not known_branch or not true_branch:
        return {"ok": False, "blocker": "PID_BRANCH_EXTRACT_FAILED"}
    if "bb_mid" not in known_branch or "close" not in known_branch:
        return {"ok": False, "blocker": "KNOWN_BRANCH_BODY_MISMATCH", "found": known_branch}
    if "float(close) < float(bb_mid)" not in true_branch:
        return {"ok": False, "blocker": "TRUE_BRANCH_BODY_MISMATCH", "found": true_branch}

    def pred_bb(ind: dict[str, Any], i: int) -> Optional[bool]:
        return primitive_known(V26_PRIMITIVE_ID, ind, i)

    exact = (
        LIFECYCLE_SCAN_TEXT
        + "\n"
        + FIRST_BAR_FLAG_TEXT
        + "\n"
        + known_branch
        + "\n"
        + true_branch
    )
    return {
        "ok": True,
        "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
        "SOURCE_FUNCTION": "eval_lifecycle.keys_tf / v27_harvest.primitive_known / v26_harvest.primitive_true",
        "SOURCE_LINE_OR_SYMBOL": f"{LIFECYCLE_PRIMITIVE}@{life_line}; primitive_known@{v27_line}; primitive_true@{v26_line}",
        "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
        "V27_SOURCE_SHA": _sha(V27_HARVEST_PATH),
        "V26_SOURCE_SHA": _sha(V26_HARVEST_PATH),
        "EXACT_PREDICATE_TEXT": exact,
        "BB_DEFINITION": (
            "research.simple_tech_entry_family.stages.attach_indicators: bollinger(close) → "
            "bb_mid=rolling_mean(close, BB_PERIOD), bb_upper/lower = mid ± BB_SIGMA * rolling_std_pop(close, BB_PERIOD, ddof=0)"
        ),
        "BB_PERIOD": 20,
        "BB_STD": 2.0,
        "TF3_BAR_CONSTRUCTION": (
            "exit_lifecycle_harvest / stream_day: aggregate_bars(raw, width_sec=180.0, am_start, am_end) "
            "then attach_indicators; session-anchored buckets of 3 completed 1m bars; incomplete/gap buckets dropped"
        ),
        "BAR_FINALIZATION_SEMANTICS": (
            "_first_bar_flag on tf3: searchsorted(finalize_t, start_t, side='right'); "
            "only completed 3m bars; event_t = finalize_t[i] of last 1m bar in the 3m bucket"
        ),
        "EVENT_TIMESTAMP_SEMANTICS": (
            "P_BB_STRUCTURE_LOSS_3M_t = finalize_t of first completed 3m bar after be_t where "
            "primitive_known('D_BB_STRUCTURE_LOSS') is True (close and bb_mid finite and close < bb_mid). "
            "start_t=be_t, end_t=None."
        ),
        "pred": pred_bb,
        "lineno": life_line,
        "v27_lineno": v27_line,
        "v26_lineno": v26_line,
        "primitive_true_probe": primitive_true,
    }


def _pack_exit(*, reason: str, fill_px: float, pack: dict[str, Any], extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    miss = bool(pack.get("miss"))
    exit_bid = pack.get("exit_bid")
    yen = None
    if (not miss) and _finite(exit_bid) and _finite(fill_px):
        yen = float(compute_pnl_yen_100(float(fill_px), float(exit_bid), side="long"))
    body = {
        "reason": reason,
        "exit_t": pack.get("exit_t"),
        "exit_bid": float(exit_bid) if _finite(exit_bid) else None,
        "pnl_yen_100": yen,
        "miss": bool(miss),
        "freshness_evidence": pack.get("freshness_evidence"),
        "latency_sec": None,
    }
    if extra:
        body.update(extra)
    return body


def eval_candidate(
    tf1: dict[str, Any],
    tf3: dict[str, Any],
    board: dict[str, Any],
    meta: dict[str, Any],
    *,
    t0: float,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    control_exit: dict[str, Any],
    pred_bb,
    leak: dict[str, Any],
) -> dict[str, Any]:
    ctrl = dict(control_exit) if control_exit else None
    out: dict[str, Any] = {
        "state_end": STATE_UNPROVEN,
        "be_reached": False,
        "be_t": None,
        "triggered": False,
        "would_trigger": False,
        "trigger_t": None,
        "lifecycle_hit": False,
        "independent_hit": False,
        "predicate_parity_ok": True,
        "tf1_ok": True,
        "tf3_ok": True,
        "exit_miss_fallback_session_close": False,
        "treatment_exit": ctrl,
        "lifecycle_primitive": LIFECYCLE_PRIMITIVE,
    }
    tf3_use = tf3 or {}
    fin3 = tf3_use.get("finalize_t")
    if fin3 is None and (tf1 or {}).get("finalize_t") is not None:
        out["tf3_ok"] = False
        leak["TF3_MISSING_N"] = int(leak.get("TF3_MISSING_N") or 0) + 1
    life = eval_lifecycle(
        tf1 or {},
        tf3_use,
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
    if not be_reached or be_t is None:
        out["state_end"] = STATE_UNPROVEN
        out["treatment_exit"] = ctrl
        return out
    out["state_end"] = STATE_PROVEN
    p = dict(life.get("p") or {})
    life_hit = bool(p.get(LIFECYCLE_PRIMITIVE))
    life_t = _f(p.get(f"{LIFECYCLE_PRIMITIVE}_t"))
    ind_hit, ind_t = _first_bar_flag(tf3_use, start_t=float(be_t), end_t=None, pred=pred_bb)
    out["lifecycle_hit"] = life_hit
    out["independent_hit"] = bool(ind_hit)
    if bool(life_hit) != bool(ind_hit):
        out["predicate_parity_ok"] = False
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
    if life_t is not None and ind_t is not None and abs(float(life_t) - float(ind_t)) > PX_EPS:
        out["predicate_parity_ok"] = False
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
    if bool(life_hit) and life_t is None:
        out["predicate_parity_ok"] = False
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
    trigger_t = life_t if life_hit else None
    out["trigger_t"] = trigger_t
    out["would_trigger"] = bool(life_hit and trigger_t is not None)
    if trigger_t is not None and float(trigger_t) + PX_EPS < float(fill_t):
        leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
        out["treatment_exit"] = ctrl
        return out
    if trigger_t is not None and float(trigger_t) + PX_EPS < float(be_t):
        leak["PRE_BE_TRIGGER_N"] = int(leak.get("PRE_BE_TRIGGER_N") or 0) + 1
        out["treatment_exit"] = ctrl
        return out
    if not out["would_trigger"] or trigger_t is None:
        out["treatment_exit"] = ctrl
        return out
    pack = first_causal_bid_evidence(
        board, meta, event_t=float(trigger_t), fill_px=float(fill_px), sess_end=float(sess_end), leak=leak
    )
    extra = {
        "trigger_event_time": trigger_t,
        "trigger_name": TRIGGER_NAME,
        "lifecycle_primitive": LIFECYCLE_PRIMITIVE,
        "bid_search_start_t": trigger_t,
        "be_t": be_t,
    }
    tx = _pack_exit(reason=CANDIDATE_EXIT_REASON, fill_px=float(fill_px), pack=pack, extra=extra)
    if pack.get("exit_t") is not None:
        tx["latency_sec"] = float(pack["exit_t"]) - float(trigger_t)
        if float(pack["exit_t"]) + PX_EPS < float(trigger_t):
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


def attach_row(row: dict[str, Any], packed: dict[str, Any], pred_bb, leak: dict[str, Any]) -> dict[str, Any]:
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
        rec["candidate_path"] = {"tf1_ok": False, "tf3_ok": False, "state_end": STATE_UNPROVEN, "triggered": False}
        rec["treatment_exit"] = ctrl or None
        return rec
    rec["fill_t"] = float(fill_t)
    rec["t0"] = float(t0)
    sym = _bare(rec.get("symbol"))
    by_tf = (packed.get("tf_by_sym") or {}).get(sym) or {}
    tf1 = by_tf.get("tf1") or {}
    tf3 = by_tf.get("tf3") or {}
    board = (packed.get("views") or {}).get(sym) or {}
    meta = (packed.get("metas") or {}).get(sym) or {}
    path = eval_candidate(
        tf1,
        tf3,
        board,
        meta,
        t0=float(t0),
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        sess_end=float(packed["am_end"]),
        control_exit=ctrl,
        pred_bb=pred_bb,
        leak=leak,
    )
    rec["candidate_path"] = path
    rec["treatment_exit"] = path.get("treatment_exit")
    return rec


def _tid(row: dict[str, Any]) -> Optional[tuple[str, str, float]]:
    t0 = _f(row.get("t0") or row.get("signal_time"))
    if t0 is None:
        return None
    return str(row.get("date") or ""), _bare(row.get("symbol")), float(t0)


def incremental_causes(day: str, occ: dict[str, Any]) -> list[dict[str, Any]]:
    ctrl_ids = set()
    for t in list((occ.get("control") or {}).get("trades") or []):
        key = _tid(t)
        if key is not None:
            ctrl_ids.add(key)
    last_exit: Optional[dict[str, Any]] = None
    out: list[dict[str, Any]] = []
    treat = dict(occ.get("treatment") or {})
    treat_ids = set()
    for t in list(treat.get("trades") or []):
        key = _tid(t)
        if key is not None:
            treat_ids.add(key)
    incr = treat_ids - ctrl_ids
    admitted: set[tuple[str, str, float]] = set()
    for ev in list(treat.get("event_log") or []):
        kind = str(ev[1]) if len(ev) > 1 else ""
        if kind == "EXIT":
            last_exit = {
                "exit_t": ev[0],
                "release_symbol": ev[2] if len(ev) > 2 else None,
                "release_t0": ev[3] if len(ev) > 3 else None,
                "release_reason": ev[4] if len(ev) > 4 else None,
            }
            continue
        if kind != "ADMIT" or len(ev) < 4:
            continue
        key = (str(day), _bare(ev[2]), float(ev[3]))
        admitted.add(key)
        if key not in incr:
            continue
        cause = dict(last_exit or {})
        cause.update({"date": day, "symbol": _bare(ev[2]), "t0": float(ev[3]), "admit_t": ev[0]})
        out.append(cause)
    for key in sorted(incr):
        if key in admitted:
            continue
        out.append({"date": key[0], "symbol": key[1], "t0": key[2], "release_symbol": None, "note": "ADMIT_NOT_IN_LOG"})
    return out


def _arm_public(arm: dict[str, Any]) -> dict[str, Any]:
    body = dict(arm or {})
    body.pop("candidates", None)
    return body


def harvest_day(
    day: str,
    *,
    cohort: str,
    spec_sha: str,
    pred_pack: dict[str, Any],
    today: str = TODAY,
) -> dict[str, Any]:
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    if not pred_pack.get("ok"):
        return {"ok": False, "blocker": pred_pack.get("blocker") or "PRED_RECOVERY_FAILED", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    ubody = load_u_day(day, cohort=cohort)
    if not ubody.get("ok") and not list(ubody.get("rows") or []):
        return {"ok": False, "blocker": f"u_cache_missing:{day}", "date": day}
    rows = [dict(r) for r in list(ubody.get("rows") or [])]
    leak = {
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "CANDIDATE_EXIT_MISS_N": 0,
        "FILL_PACK_INCOMPLETE_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "PRE_BAR_EXIT_N": 0,
        "PRE_FILL_EXIT_N": 0,
        "PRE_BE_TRIGGER_N": 0,
        "PREDICATE_DRIFT_N": 0,
        "TF1_MISSING_N": 0,
        "TF3_MISSING_N": 0,
        "BE_REACHED_FLAG_MISMATCH_N": 0,
    }
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"{cohort} {day} bb-exit symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
        packed = stream_day(day, capture, symbols, leak)
        rows = [attach_row(r, packed, pred_pack["pred"], leak) for r in rows]
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
                    "lifecycle_hit": cp.get("lifecycle_hit"),
                    "independent_hit": cp.get("independent_hit"),
                    "predicate_parity_ok": cp.get("predicate_parity_ok"),
                    "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
                    "tf1_ok": cp.get("tf1_ok"),
                    "tf3_ok": cp.get("tf3_ok"),
                    "lifecycle_primitive": cp.get("lifecycle_primitive"),
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
