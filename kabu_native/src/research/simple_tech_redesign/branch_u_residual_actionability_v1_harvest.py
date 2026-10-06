"""Recover remaining Branch U predicates; Phase A from Lifecycle cache; optional one Full Causal EXIT."""
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
from research.simple_tech_entry_family import PULLBACK_LOOKBACK, RCI_CROSS_LEVEL
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_harvest import _pack_exit, incremental_causes
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign import exit_lifecycle_harvest as lifecycle_mod
from research.simple_tech_redesign.exit_lifecycle_harvest import LIFECYCLE_CACHE, eval_lifecycle, load_lifecycle_day_cache
from research.simple_tech_redesign.exit_lifecycle_spec import (
    ADDED_FILL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    spec_sha256_lifecycle,
)
from research.simple_tech_redesign.exit_residual_rca_harvest import residual_class
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.branch_u_residual_actionability_v1_spec import (
    CONCENTRATION_MAX_SHARE,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    REMAINING_PRIMITIVES,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    U_EARLY_MIN_HIT_FRAC,
    candidate_id_for,
    exit_reason_for,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite
from research.simple_tech_redesign.v27_analyze import research_fill_tuples

PATH_CACHE = RESEARCH_CACHE / "branch_u_residual_actionability_v1"
PX_EPS = 1e-12
STATE_UNPROVEN = "UNPROVEN"
STATE_PROVEN_NO_U_EXIT = "PROVEN_NO_U_EXIT"
STATE_TRIGGERED = "TRIGGERED"
LIFECYCLE_HARVEST_PATH = Path(lifecycle_mod.__file__).resolve()
FLOOR_HARVEST_PATH = Path(__file__).resolve().parent / "entry_anchored_floor_break_candidate_harvest.py"
FLOOR_RCA_HARVEST_PATH = Path(__file__).resolve().parent / "entry_anchored_pullback_structure_rca_harvest.py"
THESIS_HARVEST_PATH = Path(__file__).resolve().parent / "entry_thesis_invalidation_rca_harvest.py"
STAGES_PATH = Path(__file__).resolve().parents[1] / "simple_tech_entry_family" / "stages.py"

REQUIRED_TEXTS = {
    "U_TREND_LOST": '("U_TREND_LOST", lambda ind, i: not trend_up(ind, i))',
    "U_RCI_RE_OVERSOLD": "return None if r is None else bool(r <= float(RCI_CROSS_LEVEL))",
    "U_VWAP_CLOSE_LOSS": "return None if cl is None or vw is None else bool(cl < vw)",
    "U_HH_HL_LOST": "return None if v is None else (not v)",
    "U_PULLBACK_LOW_BID_BREAK": "bid < float(pullback_low) - 1e-12",
    "TIE": "if end_t is not None and ft + 1e-12 >= float(end_t):",
    "SCAN": "_first_bar_flag(tf1, start_t=float(fill_t), end_t=u_end, pred=pred)",
    "PULLBACK_CLEAR": "if float(out[\"pullback_low_break_t\"]) + 1e-12 >= float(out[\"be_t\"]):",
}


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


def recover_remaining_predicates() -> dict[str, Any]:
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
    if "def trend_up" not in stages or "def reversal_rci" not in stages:
        return {"ok": False, "blocker": "STAGES_SOURCE_MISSING"}
    primitives = {
        "U_TREND_LOST": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "eval_lifecycle / stages.trend_up",
            "SOURCE_LINE": _lineno(life, "U_TREND_LOST"),
            "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
            "EXACT_PREDICATE_TEXT": REQUIRED_TEXTS["U_TREND_LOST"] + "\ntrend_up: ema9>ema21 and ema21>ema21[i-EMA_SLOPE_BARS]",
            "TIMEFRAME": "1m / tf1",
            "EVENT_TIMESTAMP": "finalize_t of first completed 1m bar after fill_t and strictly before be_t where not trend_up",
            "SCAN": REQUIRED_TEXTS["SCAN"],
        },
        "U_PULLBACK_LOW_BID_BREAK": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "walk_break_even",
            "SOURCE_LINE": _lineno(life, "pullback_low_bid_break") or _lineno(life, "U_PULLBACK_LOW_BID_BREAK"),
            "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
            "EXACT_PREDICATE_TEXT": (
                f"_pullback_low_px: min(low) over last {int(PULLBACK_LOOKBACK)} 1m bars ending at signal bar. "
                "walk_break_even: first fresh Bid1 after fill_t with bid < pullback_low - 1e-12, "
                "cleared if break_t + 1e-12 >= be_t."
            ),
            "TIMEFRAME": "tick / executable Bid1",
            "EVENT_TIMESTAMP": "board quote time of first pre-BE bid break of signal-bar pullback_low",
            "SCAN": "walk_break_even concurrent with BE walk; not _first_bar_flag",
        },
        "U_RCI_RE_OVERSOLD": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "eval_lifecycle.pred_rci_os",
            "SOURCE_LINE": _lineno(life, "U_RCI_RE_OVERSOLD"),
            "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
            "EXACT_PREDICATE_TEXT": f"rci9[i] <= {float(RCI_CROSS_LEVEL)} on completed 1m bar; None if rci missing",
            "TIMEFRAME": "1m / tf1",
            "EVENT_TIMESTAMP": "finalize_t of first completed 1m bar after fill_t and strictly before be_t with rci9<=-80",
            "SCAN": REQUIRED_TEXTS["SCAN"],
        },
        "U_VWAP_CLOSE_LOSS": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "eval_lifecycle.pred_vwap",
            "SOURCE_LINE": _lineno(life, "U_VWAP_CLOSE_LOSS"),
            "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
            "EXACT_PREDICATE_TEXT": "close[i] < vwap[i] on completed 1m bar; None if either missing",
            "TIMEFRAME": "1m / tf1",
            "EVENT_TIMESTAMP": "finalize_t of first completed 1m bar after fill_t and strictly before be_t with close<vwap",
            "SCAN": REQUIRED_TEXTS["SCAN"],
        },
        "U_HH_HL_LOST": {
            "SOURCE_FILE": "src/research/simple_tech_redesign/exit_lifecycle_harvest.py",
            "SOURCE_FUNCTION": "eval_lifecycle.pred_hh_lost / _hh_hl",
            "SOURCE_LINE": _lineno(life, "U_HH_HL_LOST"),
            "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
            "EXACT_PREDICATE_TEXT": "_hh_hl: high[i]>=high[i-1] and low[i]>=low[i-1]; U hit when not that (adjacent 1-bar HH/HL lost)",
            "TIMEFRAME": "1m / tf1",
            "EVENT_TIMESTAMP": "finalize_t of first completed 1m bar after fill_t and strictly before be_t where adjacent HH/HL is false",
            "SCAN": REQUIRED_TEXTS["SCAN"],
        },
    }
    return {
        "ok": True,
        "SOURCE_SHA": _sha(LIFECYCLE_HARVEST_PATH),
        "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
        "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        "FIRST_BAR_FLAG_LINE": flag_line,
        "primitives": primitives,
    }


def semantic_duplicate_audit(pred_pack: dict[str, Any]) -> dict[str, Any]:
    floor_h = FLOOR_HARVEST_PATH.read_text(encoding="utf-8") if FLOOR_HARVEST_PATH.is_file() else ""
    floor_rca = FLOOR_RCA_HARVEST_PATH.read_text(encoding="utf-8") if FLOOR_RCA_HARVEST_PATH.is_file() else ""
    thesis = THESIS_HARVEST_PATH.read_text(encoding="utf-8") if THESIS_HARVEST_PATH.is_file() else ""
    stages = STAGES_PATH.read_text(encoding="utf-8") if STAGES_PATH.is_file() else ""
    if "cl < setup_low" not in floor_h and "dn = cl < setup_low" not in floor_h:
        return {"ok": False, "blocker": "FLOOR_CLOSE_EVENT_NOT_FOUND"}
    if "def recover_p2_reference" not in floor_rca:
        return {"ok": False, "blocker": "P2_REFERENCE_NOT_FOUND"}
    if '("P3_REVERSAL_RCI", reversal_rci)' not in thesis:
        return {"ok": False, "blocker": "P3_REVERSAL_RCI_NOT_FOUND"}
    if "a <= float(RCI_CROSS_LEVEL) and b > float(RCI_CROSS_LEVEL)" not in stages:
        return {"ok": False, "blocker": "P3_CROSSING_TEXT_NOT_FOUND"}
    pullback = dict((pred_pack.get("primitives") or {}).get("U_PULLBACK_LOW_BID_BREAK") or {})
    rci = dict((pred_pack.get("primitives") or {}).get("U_RCI_RE_OVERSOLD") or {})
    rows = []
    pullback_dup = False
    rows.append(
        {
            "primitive": "U_PULLBACK_LOW_BID_BREAK",
            "closed_family": "ENTRY_ANCHORED_FLOOR_BREAK",
            "same_anchor": True,
            "same_floor_level_source": (
                "both use min(low) of last PULLBACK_LOOKBACK=3 1m bars ending at t0 signal bar "
                "(_pullback_low_px vs recover_p2_reference.setup_low)"
            ),
            "u_event": pullback.get("EXACT_PREDICATE_TEXT"),
            "closed_event": (
                "completed 1m Close < SETUP_LOW while ARMED; Close > SETUP_HIGH permanently locks; "
                "not Bid1; not walk_break_even."
            ),
            "SEMANTIC_DUPLICATE": False,
            "reason": (
                "Same lookback floor level, not the same event: Bid1 vs 1m Close, plus upside lock, "
                "plus different timestamp (quote vs finalize_t)."
            ),
        }
    )
    rows.append(
        {
            "primitive": "U_RCI_RE_OVERSOLD",
            "closed_family": "ENTRY_THESIS_INVALIDATION P3_REVERSAL_RCI",
            "same_anchor": False,
            "u_event": rci.get("EXACT_PREDICATE_TEXT"),
            "closed_event": (
                "reversal_rci: rci9[i-1] <= -80 AND rci9[i] > -80 (one-bar cross up). "
                "Thesis invalidation is NOT that crossing (valid→invalid of P3), not a level re-oversold."
            ),
            "SEMANTIC_DUPLICATE": False,
            "reason": (
                "U_RCI_RE_OVERSOLD is rci9 <= -80 level on a post-fill 1m bar. "
                "P3 is the opposite-direction one-bar crossing used at ENTRY. Not the same predicate."
            ),
        }
    )
    for pid in REMAINING_PRIMITIVES:
        if pid in ("U_PULLBACK_LOW_BID_BREAK", "U_RCI_RE_OVERSOLD"):
            continue
        rows.append(
            {
                "primitive": pid,
                "closed_family": None,
                "SEMANTIC_DUPLICATE": False,
                "reason": "no required closed-family exact-source identity match",
            }
        )
    return {
        "ok": True,
        "rows": rows,
        "duplicate_primitives": [r["primitive"] for r in rows if bool(r.get("SEMANTIC_DUPLICATE"))],
        "pullback_vs_floor_duplicate": bool(pullback_dup),
        "rci_vs_p3_duplicate": False,
        "FLOOR_SOURCE_SHA": _sha(FLOOR_HARVEST_PATH),
        "THESIS_SOURCE_SHA": _sha(THESIS_HARVEST_PATH),
        "STAGES_SOURCE_SHA": _sha(STAGES_PATH),
    }


def load_phase_a_fills() -> dict[str, Any]:
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


def _cls(row: dict[str, Any]) -> str:
    life = dict(row.get("lifecycle") or {})
    return residual_class(path_type=str(row.get("path_type") or "OTHER"), be_reached=bool(life.get("break_even_reached")))


def _hit(row: dict[str, Any], pid: str) -> bool:
    u = dict(dict(row.get("lifecycle") or {}).get("u") or {})
    return bool(u.get(pid))


def _share(counter: dict[str, int], total: int) -> dict[str, Any]:
    if int(total) <= 0 or not counter:
        return {"top": None, "n": 0, "abs_share": None, "warning_gt_50pct": False}
    top_k = max(counter, key=lambda k: counter[k])
    n = int(counter[top_k])
    share = float(n) / float(total)
    return {
        "top": top_k,
        "n": n,
        "abs_share": share,
        "warning_gt_50pct": bool(share > float(CONCENTRATION_MAX_SHARE) + 1e-15),
    }


def phase_a_table(fills: list[dict[str, Any]], *, duplicate_pids: set[str], pred_ok: bool) -> dict[str, Any]:
    by_cls: dict[str, list[dict[str, Any]]] = {}
    for r in fills:
        by_cls.setdefault(_cls(r), []).append(r)
    u_rows = list(by_cls.get("U_EARLY_NEVER_BE") or [])
    p_rows = list(by_cls.get("P_EARLY_AFTER_BE") or [])
    g_rows = list(by_cls.get("PROTECTED_GOOD") or [])
    d_rows = list(by_cls.get("PROTECTED_DIP") or [])
    table = []
    for pid in REMAINING_PRIMITIVES:
        u_hit = [r for r in u_rows if _hit(r, pid)]
        p_hit = [r for r in p_rows if _hit(r, pid)]
        g_hit = [r for r in g_rows if _hit(r, pid)]
        d_hit = [r for r in d_rows if _hit(r, pid)]
        keep_false = len(g_hit) + len(d_hit)
        u_n = len(u_rows)
        u_hit_n = len(u_hit)
        u_rate = (float(u_hit_n) / float(u_n)) if u_n else None
        p_n = len(p_rows)
        p_hit_n = len(p_hit)
        p_rate = (float(p_hit_n) / float(p_n)) if p_n else None
        all_hits = [r for r in fills if _hit(r, pid)]
        u_days = {}
        u_syms = {}
        for r in u_hit:
            u_days[str(r.get("date") or "")] = int(u_days.get(str(r.get("date") or ""), 0) or 0) + 1
            u_syms[_bare(r.get("symbol"))] = int(u_syms.get(_bare(r.get("symbol")), 0) or 0) + 1
        day_share = _share(u_days, u_hit_n)
        sym_share = _share(u_syms, u_hit_n)
        dup = pid in duplicate_pids
        min_u_hits = int((float(u_n) * float(U_EARLY_MIN_HIT_FRAC)) + 1e-12) if u_n else 0
        gates = {
            "A_predicate_recovery": bool(pred_ok),
            "B_not_semantic_duplicate": not dup,
            "C_u_early_hit_frac": bool(u_n > 0 and u_hit_n >= min_u_hits and (u_rate or 0.0) + 1e-15 >= float(U_EARLY_MIN_HIT_FRAC)),
            "D_p_early_false_hit_eq_0": p_hit_n == 0,
            "E_good_pre_be_eq_0": len(g_hit) == 0,
            "F_dip_pre_be_eq_0": len(d_hit) == 0,
            "G_top_u_hit_day_le_50pct": not bool(day_share.get("warning_gt_50pct")),
            "H_top_u_hit_symbol_le_50pct": not bool(sym_share.get("warning_gt_50pct")),
        }
        qualified = all(gates.values())
        table.append(
            {
                "primitive": pid,
                "SEMANTIC_DUPLICATE": bool(dup),
                "U_EARLY_N": u_n,
                "U_EARLY_HIT_N": u_hit_n,
                "U_EARLY_HIT_RATE": u_rate,
                "P_EARLY_N": p_n,
                "P_EARLY_PRE_BE_HIT_N": p_hit_n,
                "P_EARLY_FALSE_HIT_RATE": p_rate,
                "GOOD_N": len(g_rows),
                "GOOD_PRE_BE_HIT_N": len(g_hit),
                "DIP_N": len(d_rows),
                "DIP_PRE_BE_HIT_N": len(d_hit),
                "KEEP_FALSE_HIT_N": keep_false,
                "CORE_U_HIT_N": sum(1 for r in u_hit if str(r.get("fill_role") or "") == "CORE"),
                "ADDED_U_HIT_N": sum(1 for r in u_hit if str(r.get("fill_role") or "") == "ADDED"),
                "hit_day_n": len({str(r.get("date") or "") for r in all_hits}),
                "hit_symbol_n": len({_bare(r.get("symbol")) for r in all_hits}),
                "top_U_hit_day": day_share,
                "top_U_hit_symbol": sym_share,
                "gates": gates,
                "qualified": bool(qualified),
                "PNL_USED": False,
            }
        )
    class_n = {k: len(v) for k, v in by_cls.items()}
    return {
        "class_n": class_n,
        "U_EARLY_N": len(u_rows),
        "P_EARLY_N": len(p_rows),
        "GOOD_N": len(g_rows),
        "DIP_N": len(d_rows),
        "table": table,
        "PNL_USED_FOR_SELECTION": False,
    }


def select_qualifier(table: list[dict[str, Any]]) -> dict[str, Any]:
    quals = [r for r in table if bool(r.get("qualified"))]
    if not quals:
        return {"n": 0, "selected": None, "rule": "none", "PNL_USED_FOR_SELECTION": False}
    def key(r: dict[str, Any]) -> tuple:
        share = dict(r.get("top_U_hit_symbol") or {}).get("abs_share")
        share_v = float(share) if share is not None else 1.0
        return (-int(r.get("U_EARLY_HIT_N") or 0), -int(r.get("hit_day_n") or 0), share_v, str(r.get("primitive") or ""))
    ordered = sorted(quals, key=key)
    sel = ordered[0]
    pid = str(sel.get("primitive") or "")
    return {
        "n": len(quals),
        "selected": pid,
        "CANDIDATE_ID": candidate_id_for(pid),
        "rule": "1 max U_EARLY_HIT_N; 2 max hit_day_n; 3 min top U-hit symbol share",
        "qualified": [str(r.get("primitive")) for r in ordered],
        "PNL_USED_FOR_SELECTION": False,
    }


def _race(be_t: Optional[float], ev_t: Optional[float]) -> str:
    if ev_t is None and be_t is None:
        return "NEITHER"
    if ev_t is None:
        return "BE_FIRST"
    if be_t is None:
        return "EMA_FIRST"
    if float(ev_t) + PX_EPS < float(be_t):
        return "PRIM_FIRST"
    if float(be_t) + PX_EPS < float(ev_t):
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
    primitive: str,
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
        "tf1_ok": True,
        "race": "NEITHER",
        "exit_miss_fallback_session_close": False,
        "treatment_exit": ctrl,
        "lifecycle_primitive": primitive,
    }
    if (tf1 or {}).get("finalize_t") is None and primitive != "U_PULLBACK_LOW_BID_BREAK":
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
    life_hit = bool(u.get(primitive))
    life_t = _f(u.get(f"{primitive}_t"))
    out["lifecycle_hit"] = life_hit
    race = _race(be_t, life_t if life_hit else None)
    if race == "EMA_FIRST":
        race = "PRIM_FIRST"
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
        "trigger_name": f"UNPROVEN_{primitive}",
        "lifecycle_primitive": primitive,
        "bid_search_start_t": trigger_t,
        "be_t": be_t,
        "race": race,
    }
    tx = _pack_exit(reason=exit_reason_for(primitive), fill_px=float(fill_px), pack=pack, extra=extra)
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


def attach_row(row: dict[str, Any], packed: dict[str, Any], primitive: str, leak: dict[str, Any]) -> dict[str, Any]:
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
        primitive=primitive,
        leak=leak,
    )
    rec["candidate_path"] = path
    rec["treatment_exit"] = path.get("treatment_exit")
    return rec


def _arm_public(arm: dict[str, Any]) -> dict[str, Any]:
    body = dict(arm or {})
    body.pop("candidates", None)
    return body


def harvest_day(
    day: str,
    *,
    cohort: str,
    spec_sha: str,
    primitive: str,
    today: str = TODAY,
) -> dict[str, Any]:
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}_{primitive}.json"
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
        "PNL_SELECTION_N": 0,
    }
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"{cohort} {day} residual-u {primitive} symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
        packed = stream_day(day, capture, symbols, leak)
        rows = [attach_row(r, packed, primitive, leak) for r in rows]
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
                    "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
                    "tf1_ok": cp.get("tf1_ok"),
                    "race": cp.get("race"),
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
        "primitive": primitive,
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
