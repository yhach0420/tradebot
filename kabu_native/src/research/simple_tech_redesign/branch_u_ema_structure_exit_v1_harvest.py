"""Harvest UNPROVEN_EMA_STRUCTURE_LOSS_V1 from exact Lifecycle U_EMA_STRUCTURE_LOSS."""
from __future__ import annotations

import ast
import gc
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_harvest import (
    _pack_exit,
    incremental_causes,
)
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign import exit_lifecycle_harvest as lifecycle_mod
from research.simple_tech_redesign.exit_lifecycle_harvest import _first_bar_flag, eval_lifecycle
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_spec import (
    CANDIDATE_EXIT_REASON,
    FORBIDDEN_INPUT_DAYS,
    LIFECYCLE_PRIMITIVE,
    MAX_RESEARCH_DATE,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    TRIGGER_NAME,
    V26_PRIMITIVE_ID,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite
from research.simple_tech_redesign.v26_harvest import primitive_true
from research.simple_tech_redesign.v27_harvest import primitive_known

PATH_CACHE = RESEARCH_CACHE / "branch_u_ema_structure_exit_v1"
PX_EPS = 1e-12
STATE_UNPROVEN = "UNPROVEN"
STATE_PROVEN_NO_U_EXIT = "PROVEN_NO_U_EXIT"
STATE_TRIGGERED = "TRIGGERED"
LIFECYCLE_HARVEST_PATH = Path(lifecycle_mod.__file__).resolve()
V27_HARVEST_PATH = Path(__file__).resolve().parent / "v27_harvest.py"
V26_HARVEST_PATH = Path(__file__).resolve().parent / "v26_harvest.py"

LIFECYCLE_SCAN_TEXT = (
    '("U_EMA_STRUCTURE_LOSS", lambda ind, i: primitive_known("A_EMA_STRUCTURE_LOSS", ind, i))'
)
FIRST_BAR_FLAG_TIE_TEXT = "if end_t is not None and ft + 1e-12 >= float(end_t):"
U_SCAN_TEXT = "_first_bar_flag(tf1, start_t=float(fill_t), end_t=u_end, pred=pred)"
TF1_BUILD_TEXT = "tf_by_sym"


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
    for ln in lines[:10]:
        out.append(ln)
        if "return" in ln and ("ema9" in "".join(out) or pid in "".join(out)):
            if "return" in ln:
                break
        if len(out) > 1 and (ln.strip().startswith("elif pid") or ln.strip().startswith("if pid ==")):
            out = out[:-1]
            break
    return "\n".join(out).rstrip()


def recover_lifecycle_u_ema_predicate() -> dict[str, Any]:
    if not LIFECYCLE_HARVEST_PATH.is_file() or not V27_HARVEST_PATH.is_file() or not V26_HARVEST_PATH.is_file():
        return {"ok": False, "blocker": "SOURCE_FILE_MISSING"}
    life_text = LIFECYCLE_HARVEST_PATH.read_text(encoding="utf-8")
    v27_text = V27_HARVEST_PATH.read_text(encoding="utf-8")
    v26_text = V26_HARVEST_PATH.read_text(encoding="utf-8")
    if LIFECYCLE_SCAN_TEXT not in life_text:
        return {"ok": False, "blocker": "U_EMA_SCAN_NOT_FOUND"}
    if U_SCAN_TEXT not in life_text:
        return {"ok": False, "blocker": "U_FIRST_BAR_FLAG_SCAN_MISMATCH"}
    if FIRST_BAR_FLAG_TIE_TEXT not in life_text:
        return {"ok": False, "blocker": "TIE_RULE_NOT_FOUND"}
    if 'ok("ema9", i) and ok("ema21", i)' not in v27_text:
        return {"ok": False, "blocker": "PRIMITIVE_KNOWN_A_EMA_MISMATCH"}
    if "float(ema9) <= float(ema21)" not in v26_text:
        return {"ok": False, "blocker": "PRIMITIVE_TRUE_A_EMA_COMPARE_MISMATCH"}
    if f'pid == "{V26_PRIMITIVE_ID}"' not in v26_text or f'pid == "{V26_PRIMITIVE_ID}"' not in v27_text:
        return {"ok": False, "blocker": "A_EMA_PID_NOT_FOUND"}
    if "def _first_bar_flag" not in life_text:
        return {"ok": False, "blocker": "FIRST_BAR_FLAG_FN_MISSING"}
    life_line = _lineno_const(life_text, LIFECYCLE_PRIMITIVE)
    v27_line = _lineno_const(v27_text, V26_PRIMITIVE_ID)
    v26_line = _lineno_const(v26_text, V26_PRIMITIVE_ID)
    flag_line = None
    tree = ast.parse(life_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_first_bar_flag":
            flag_line = int(node.lineno)
    if life_line is None or v27_line is None or v26_line is None or flag_line is None:
        return {"ok": False, "blocker": "LINE_RESTORE_FAILED"}
    known_branch = _extract_pid_branch(v27_text, V26_PRIMITIVE_ID)
    true_branch = _extract_pid_branch(v26_text, V26_PRIMITIVE_ID)
    if not known_branch or not true_branch:
        return {"ok": False, "blocker": "PID_BRANCH_EXTRACT_FAILED"}
    if "ema9" not in known_branch or "ema21" not in known_branch:
        return {"ok": False, "blocker": "KNOWN_BRANCH_BODY_MISMATCH", "found": known_branch}
    if "float(ema9) <= float(ema21)" not in true_branch:
        return {"ok": False, "blocker": "TRUE_BRANCH_BODY_MISMATCH", "found": true_branch}

    def pred_ema(ind: dict[str, Any], i: int) -> Optional[bool]:
        return primitive_known(V26_PRIMITIVE_ID, ind, i)

    exact = (
        LIFECYCLE_SCAN_TEXT
        + "\n"
        + U_SCAN_TEXT
        + "\n"
        + FIRST_BAR_FLAG_TIE_TEXT
        + "\n"
        + known_branch
        + "\n"
        + true_branch
    )
    return {
        "ok": True,
        "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
        "SOURCE_FUNCTION": "eval_lifecycle.U_EMA_STRUCTURE_LOSS / v27_harvest.primitive_known / v26_harvest.primitive_true",
        "SOURCE_LINE_OR_SYMBOL": (
            f"{LIFECYCLE_PRIMITIVE}@{life_line}; _first_bar_flag@{flag_line}; "
            f"primitive_known@{v27_line}; primitive_true@{v26_line}"
        ),
        "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
        "V27_SOURCE_SHA": _sha(V27_HARVEST_PATH),
        "V26_SOURCE_SHA": _sha(V26_HARVEST_PATH),
        "EXACT_U_EMA_PREDICATE_TEXT": exact,
        "TIMEFRAME": "1m / tf1",
        "EMA_PERIODS": (9, 21),
        "EMA_DEFINITION": (
            "research.simple_tech_entry_family.stages.attach_indicators: "
            "ema9=ema(close, 9), ema21=ema(close, 21). "
            "primitive_true(A_EMA_STRUCTURE_LOSS)= ema9<=ema21 when both finite. "
            "primitive_known returns None if ema9/ema21 not finite."
        ),
        "BAR_CONSTRUCTION": (
            "1m SymbolBarBuilder native bars; indicators attached on completed 1m arrays. "
            "Not 3m persistence. Not V27 K-duration."
        ),
        "BAR_FINALIZATION": (
            "_first_bar_flag: j = searchsorted(finalize_t, fill_t, side='right'); "
            "scan completed bars with finalize_t > fill_t."
        ),
        "EVENT_TIMESTAMP": (
            "U_EMA_STRUCTURE_LOSS_t = finalize_t of first completed 1m bar after fill_t "
            "and strictly before be_t (if BE reached) where primitive_known(A_EMA_STRUCTURE_LOSS) is True."
        ),
        "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
        "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        "BE_SEMANTICS": (
            "walk_break_even: first fresh executable Bid1 after fill_t with "
            "compute_pnl_yen_100(fill, bid, long) >= 0, 100 shares, fees excluded. BE is not an EXIT."
        ),
        "pred": pred_ema,
        "primitive_true_probe": primitive_true,
    }


def _race(be_t: Optional[float], ema_t: Optional[float]) -> str:
    if ema_t is None and be_t is None:
        return "NEITHER"
    if ema_t is None:
        return "BE_FIRST"
    if be_t is None:
        return "EMA_FIRST"
    if float(ema_t) + PX_EPS < float(be_t):
        return "EMA_FIRST"
    if float(be_t) + PX_EPS < float(ema_t):
        return "BE_FIRST"
    return "SAME_TIMESTAMP"


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
    pred_ema,
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
        "race": "NEITHER",
        "unconstrained_ema_t": None,
        "exit_miss_fallback_session_close": False,
        "treatment_exit": ctrl,
        "lifecycle_primitive": LIFECYCLE_PRIMITIVE,
    }
    tf1_use = tf1 or {}
    if tf1_use.get("finalize_t") is None:
        out["tf1_ok"] = False
        leak["TF1_MISSING_N"] = int(leak.get("TF1_MISSING_N") or 0) + 1
        return out
    life = eval_lifecycle(
        tf1_use,
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
    life_hit = bool(u.get(LIFECYCLE_PRIMITIVE))
    life_t = _f(u.get(f"{LIFECYCLE_PRIMITIVE}_t"))
    u_end = float(be_t) if be_reached and be_t is not None else None
    ind_hit, ind_t = _first_bar_flag(tf1_use, start_t=float(fill_t), end_t=u_end, pred=pred_ema)
    free_hit, free_t = _first_bar_flag(tf1_use, start_t=float(fill_t), end_t=None, pred=pred_ema)
    out["lifecycle_hit"] = life_hit
    out["independent_hit"] = bool(ind_hit)
    out["unconstrained_ema_t"] = float(free_t) if free_hit and free_t is not None else None
    if bool(life_hit) != bool(ind_hit):
        out["predicate_parity_ok"] = False
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
    if life_t is not None and ind_t is not None and abs(float(life_t) - float(ind_t)) > PX_EPS:
        out["predicate_parity_ok"] = False
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
    if bool(life_hit) and life_t is None:
        out["predicate_parity_ok"] = False
        leak["PREDICATE_DRIFT_N"] = int(leak.get("PREDICATE_DRIFT_N") or 0) + 1
    race = _race(be_t, life_t if life_hit else (float(free_t) if free_hit and free_t is not None else None))
    if (not life_hit) and be_reached and be_t is not None and free_hit and free_t is not None:
        if abs(float(free_t) - float(be_t)) <= PX_EPS:
            race = "SAME_TIMESTAMP"
        elif float(free_t) + PX_EPS >= float(be_t):
            race = "BE_FIRST"
    out["race"] = race
    trigger_t = life_t if life_hit else None
    out["trigger_t"] = trigger_t
    out["would_trigger"] = bool(life_hit and trigger_t is not None)
    if trigger_t is not None and float(trigger_t) + PX_EPS < float(fill_t):
        leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
        out["treatment_exit"] = ctrl
        return out
    if be_reached and trigger_t is not None and be_t is not None and float(trigger_t) + PX_EPS >= float(be_t):
        leak["POST_BE_U_TRIGGER_N"] = int(leak.get("POST_BE_U_TRIGGER_N") or 0) + 1
        out["state_end"] = STATE_PROVEN_NO_U_EXIT
        out["would_trigger"] = False
        out["treatment_exit"] = ctrl
        return out
    if be_reached and not out["would_trigger"]:
        out["state_end"] = STATE_PROVEN_NO_U_EXIT
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
        "race": race,
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


def attach_row(row: dict[str, Any], packed: dict[str, Any], pred_ema, leak: dict[str, Any]) -> dict[str, Any]:
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
        rec["candidate_path"] = {"tf1_ok": False, "triggered": False, "state_end": STATE_UNPROVEN, "race": "NEITHER"}
        rec["treatment_exit"] = ctrl or None
        return rec
    rec["fill_t"] = float(fill_t)
    rec["t0"] = float(t0)
    sym = _bare(rec.get("symbol"))
    by_tf = (packed.get("tf_by_sym") or {}).get(sym) or {}
    path = eval_candidate(
        by_tf.get("tf1") or {},
        by_tf.get("tf3") or {},
        (packed.get("views") or {}).get(sym) or {},
        (packed.get("metas") or {}).get(sym) or {},
        t0=float(t0),
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        sess_end=float(packed["am_end"]),
        control_exit=ctrl,
        pred_ema=pred_ema,
        leak=leak,
    )
    rec["candidate_path"] = path
    rec["treatment_exit"] = path.get("treatment_exit")
    return rec


def _arm_public(arm: dict[str, Any]) -> dict[str, Any]:
    body = dict(arm or {})
    body.pop("candidates", None)
    return body


def harvest_day(day: str, *, cohort: str, spec_sha: str, pred_pack: dict[str, Any], today: str = TODAY) -> dict[str, Any]:
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
        "POST_BE_U_TRIGGER_N": 0,
        "PREDICATE_DRIFT_N": 0,
        "TF1_MISSING_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "PERSISTENCE_SEARCH_N": 0,
    }
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"{cohort} {day} u-ema-exit symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
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
                    "race": cp.get("race"),
                    "unconstrained_ema_t": cp.get("unconstrained_ema_t"),
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
