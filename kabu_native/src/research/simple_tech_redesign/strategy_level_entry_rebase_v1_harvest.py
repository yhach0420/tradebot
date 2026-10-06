"""Load existing Simple-Tech ENTRY inventory and reuse prior Ask/Bid markout artifacts. No recapture."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_entry_family.v3_analyze import concentration
from research.simple_tech_entry_family.v4_persistence_analyze import filter_arm as v4_filter
from research.simple_tech_entry_family.v4_persistence_harvest import PR_CACHE as V4_PR_CACHE
from research.simple_tech_entry_family.v6_pullback_analyze import filter_arm as v6_filter
from research.simple_tech_entry_family.v6_pullback_harvest import PR_CACHE as V6_PR_CACHE
from research.simple_tech_entry_family.v8_harvest import V8_CACHE, filter_arm as v8_filter
from research.simple_tech_entry_family.v9_harvest import arm_pass as v9_arm_pass, attach_bits
from research.simple_tech_entry_family.v10_harvest import arm_pass as v10_arm_pass
from research.simple_tech_redesign.isolation import TODAY
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_spec import (
    ELIGIBLE_ENTRY_IDS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    RESULTS_ENTRY,
    V10_B1_EXE_N,
    V10_B1_SIGNAL_N,
)

NATIVE = Path(__file__).resolve().parents[3]
STAGES_PATH = NATIVE / "src" / "research" / "simple_tech_entry_family" / "stages.py"
V8_HARVEST_PATH = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v8_harvest.py"
V10_HARVEST_PATH = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v10_harvest.py"
V9_HARVEST_PATH = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v9_harvest.py"
V4_PR_HARVEST = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v4_persistence_harvest.py"
V6_PR_HARVEST = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v6_pullback_harvest.py"
V3_HARVEST = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v3_harvest.py"
V7_HARVEST = NATIVE / "src" / "research" / "simple_tech_entry_family" / "v7_harvest.py"

MARKOUT_SEMANTICS = (
    "Ask1 at signal t0 (ask_entry_ok / executable continuous board). "
    "Horizon markout = last Bid1 before t0+h vs Ask1_t0, bps. Completed 1m bar t0=finalize_t."
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _fn_src(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        return {"SOURCE_FILE": str(path), "SOURCE_FUNCTION": name, "SOURCE_SHA": "MISSING", "SOURCE_LINE": None, "EXACT_RULE_TEXT": None}
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = text.splitlines()
            end = int(getattr(node, "end_lineno", node.lineno) or node.lineno)
            body = "\n".join(lines[node.lineno - 1 : end])
            return {
                "SOURCE_FILE": str(path.relative_to(NATIVE)).replace("\\", "/"),
                "SOURCE_FUNCTION": name,
                "SOURCE_SHA": _sha(path),
                "SOURCE_LINE": int(node.lineno),
                "EXACT_RULE_TEXT": body,
            }
    return {"SOURCE_FILE": str(path.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": name, "SOURCE_SHA": _sha(path), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None}


def inventory_rows() -> list[dict[str, Any]]:
    st = {n: _fn_src(STAGES_PATH, n) for n in ("trend_up", "pullback_setup", "reversal_rci", "price_action", "volume_confirm", "board_support")}
    v8 = _fn_src(V8_HARVEST_PATH, "arm_pass")
    v10 = _fn_src(V10_HARVEST_PATH, "arm_pass")
    v9 = _fn_src(V9_HARVEST_PATH, "arm_pass")
    v3m = _fn_src(V3_HARVEST, "markout_row")
    v7m = _fn_src(V7_HARVEST, "markout_slim")
    tf = "1m completed bar / tf1"
    ev = "t0 = completed 1m finalize_t; Ask1/Bid markout at t0 and t0+h"
    rows = [
        {"ENTRY_ID": "V1_FULL_STACK", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_ENTRY_FAMILY_V1", "SOURCE_STATUS": "selected_then_insufficient", "eligible": True,
         "rule": "TREND_UP AND PULLBACK AND REVERSAL_RCI AND PRICE_ACTION AND VOLUME_CONFIRM AND BOARD_VETO",
         "SOURCE_FILE": "src/research/simple_tech_entry_family/stages.py",
         "SOURCE_FUNCTION": "trend_up+pullback_setup+reversal_rci+price_action+volume_confirm+board_support",
         "SOURCE_SHA": st["trend_up"]["SOURCE_SHA"],
         "SOURCE_LINE": st["trend_up"]["SOURCE_LINE"],
         "EXACT_RULE_TEXT": "\n\n".join(str(st[k].get("EXACT_RULE_TEXT") or "") for k in ("trend_up", "pullback_setup", "reversal_rci", "price_action", "volume_confirm", "board_support")),
         "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev, "MARKOUT_SOURCE": v3m},
        {"ENTRY_ID": "V10_R1B0", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "cross-state diagnostic R1B0; not a standalone ENTRY policy; do not promote on positive markout",
         **v10, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V2_EVENT_TRIGGER", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_ENTRY_FAMILY_V2_EVENT_TRIGGER", "SOURCE_STATUS": "rejected", "eligible": False,
         "exclude_reason": "closed trigger family; event-cross t0 not completed-bar signal; V2_TRIGGER_MECHANISM_NOT_SUPPORTED",
         "SOURCE_FILE": "src/research/simple_tech_entry_family/v2_spec.py", "SOURCE_FUNCTION": "canonical_v2_spec", "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_entry_family/v2_spec.py"), "SOURCE_LINE": None,
         "EXACT_RULE_TEXT": "TREND+PULLBACK+RCI+VOLUME+CLOSE<=BB_UPPER then first price cross of HIGH[k]", "TIMEFRAME": "intrabar event", "EVENT_TIMESTAMP_SEMANTICS": "trigger event t0"},
        {"ENTRY_ID": "V3_EXIT_NEUTRAL_EVAL", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_ENTRY_FAMILY_V3_EXIT_NEUTRAL", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "evaluation method of V1, not a distinct ENTRY rule", **v3m, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V4_VOLUME_RCA", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V4_VOLUME_QUALITY_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "quartile/Spearman RCA, not standalone ENTRY policy", "SOURCE_FILE": "src/research/simple_tech_entry_family/v4_spec.py", "SOURCE_FUNCTION": "canonical_v4_spec", "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_entry_family/v4_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V4_P60", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V4_VOLUME_PERSISTENCE_RULE", "SOURCE_STATUS": "rejected", "eligible": True,
         "rule": "V1 stages with volume gate replaced by 300s persistence >=0.60 (18/30 positive 10s steps)",
         "SOURCE_FILE": str(V4_PR_HARVEST.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": "persistence band P60", "SOURCE_SHA": _sha(V4_PR_HARVEST), "SOURCE_LINE": None,
         "EXACT_RULE_TEXT": "min_positive_10s_steps=18 n_10s_steps=30", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V4_P80", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V4_VOLUME_PERSISTENCE_RULE", "SOURCE_STATUS": "rejected", "eligible": True,
         "rule": "V1 stages with volume persistence >=0.80 (24/30)", "SOURCE_FILE": str(V4_PR_HARVEST.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": "P80", "SOURCE_SHA": _sha(V4_PR_HARVEST), "SOURCE_LINE": None, "EXACT_RULE_TEXT": "min_positive_10s_steps=24", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V4_P90", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V4_VOLUME_PERSISTENCE_RULE", "SOURCE_STATUS": "rejected", "eligible": True,
         "rule": "V1 stages with volume persistence >=0.90 (27/30)", "SOURCE_FILE": str(V4_PR_HARVEST.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": "P90", "SOURCE_SHA": _sha(V4_PR_HARVEST), "SOURCE_LINE": None, "EXACT_RULE_TEXT": "min_positive_10s_steps=27", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V5_REVERSAL_RCA", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V5_REVERSAL_QUALITY_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "reversal quality RCA, not standalone ENTRY", "SOURCE_FILE": "src/research/simple_tech_entry_family/v5_spec.py", "SOURCE_FUNCTION": "canonical_v5_spec", "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_entry_family/v5_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V6_TREND_PULLBACK_RCA", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V6_TREND_PULLBACK_STAGE_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "stage RCA, not standalone ENTRY", "SOURCE_FILE": "src/research/simple_tech_entry_family/v6_spec.py", "SOURCE_FUNCTION": "canonical_v6_spec", "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_entry_family/v6_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V6_D10", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V6_PULLBACK_RULE", "SOURCE_STATUS": "rejected", "eligible": True,
         "rule": "V1 pullback replaced by PQ1<=-10bps EMA9 penetrate; other V1 gates held",
         "SOURCE_FILE": str(V6_PR_HARVEST.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": "D10", "SOURCE_SHA": _sha(V6_PR_HARVEST), "SOURCE_LINE": None, "EXACT_RULE_TEXT": "pq1_max_bps=-10", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V6_D20", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V6_PULLBACK_RULE", "SOURCE_STATUS": "rejected", "eligible": True,
         "rule": "PQ1<=-20bps", "SOURCE_FILE": str(V6_PR_HARVEST.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": "D20", "SOURCE_SHA": _sha(V6_PR_HARVEST), "SOURCE_LINE": None, "EXACT_RULE_TEXT": "pq1_max_bps=-20", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V6_D30", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V6_PULLBACK_RULE", "SOURCE_STATUS": "rejected", "eligible": True,
         "rule": "PQ1<=-30bps", "SOURCE_FILE": str(V6_PR_HARVEST.relative_to(NATIVE)).replace("\\", "/"), "SOURCE_FUNCTION": "D30", "SOURCE_SHA": _sha(V6_PR_HARVEST), "SOURCE_LINE": None, "EXACT_RULE_TEXT": "pq1_max_bps=-30", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V7_TF3_TF5", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V7_TIMEFRAME_ROLE_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "V7 is timeframe RCA; mixed-TF strategy forbidden; not a standalone ENTRY", **v7m, "TIMEFRAME": "1m/3m/5m RCA", "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V8_A1_NO_PRICE_ACTION", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "TREND AND PULLBACK AND RCI AND VOLUME AND BOARD (no PA)", **v8, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V8_A2_NO_VOLUME", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "TREND AND PULLBACK AND RCI AND PA AND BOARD (no volume)", **v8, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V8_A3_T3_PULLBACK_RCI_BOARD", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "TREND AND PULLBACK AND RCI AND BOARD (no PA, no volume)", **v8, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V8_A4_PULLBACK_RCI_BOARD", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "PULLBACK AND RCI AND BOARD (no trend/PA/volume)", **v8, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V8_A5_PULLBACK_BOARD", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "PULLBACK AND BOARD", **v8, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V8_A6_PULLBACK_RCI", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V8_ARCHITECTURE_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "PULLBACK AND RCI (board not hard)", **v8, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V9_T1_CROSS_ONLY", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V9_TREND_CONTEXT_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "PULLBACK AND RCI AND BOARD AND EMA9>EMA21 (slope not required)", **v9, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V9_T2_SLOPE_ONLY", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V9_TREND_CONTEXT_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "PULLBACK AND RCI AND BOARD AND EMA21 slope up (cross not required)", **v9, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V9_STATES_Sxx", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V9_TREND_CONTEXT_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "cross-state diagnostic S11/S10/S01/S00; not standalone ENTRY", **v9, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V10_B0_T3_PULLBACK", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA", "SOURCE_STATUS": "candidate", "eligible": True,
         "rule": "T3 (EMA9>EMA21 AND EMA21[t]>EMA21[t-3]) AND PULLBACK", **v10, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V10_B1_T3_PULLBACK_RCI", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA", "SOURCE_STATUS": "selected", "eligible": True,
         "rule": "T3 AND PULLBACK AND REVERSAL_RCI", **v10, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V10_STATES_RxBy", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA", "SOURCE_STATUS": "diagnostic-only", "eligible": False,
         "exclude_reason": "R1B0/R1B1/R0B1/R0B0 diagnostic-only; do not promote on positive markout", **v10, "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "V11_V13_EXECUTION", "SOURCE_ANALYSIS_ID": "SIMPLE_TECH_V11_SIGNAL_VS_EXECUTION_COST_RCA", "SOURCE_STATUS": "closed", "eligible": False,
         "exclude_reason": "execution research on frozen B1; ENTRY_RULE_CHANGED=false", "SOURCE_FILE": "src/research/simple_tech_entry_family/v13_spec.py", "SOURCE_FUNCTION": "canonical_v13_spec", "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_entry_family/v13_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": "T3_AND_PULLBACK_AND_RCI frozen", "TIMEFRAME": tf, "EVENT_TIMESTAMP_SEMANTICS": ev},
        {"ENTRY_ID": "PRECAP_BOARD_SINGLE_FEATURE_P2", "SOURCE_ANALYSIS_ID": "PRE_CAP families", "SOURCE_STATUS": "closed", "eligible": False,
         "exclude_reason": "closed: Pre-CAP Board, single-feature, P2_TOUCH_AGE; no reentry", "SOURCE_FILE": "src/research/simple_tech_redesign/pre_cap_candidate_spec.py", "SOURCE_FUNCTION": None, "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_redesign/pre_cap_candidate_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": "board/event", "EVENT_TIMESTAMP_SEMANTICS": "pre-CAP"},
        {"ENTRY_ID": "E4_NO_ADVERSE", "SOURCE_ANALYSIS_ID": "E4_FALLBACK_NO_ADVERSE_PRICE_V1", "SOURCE_STATUS": "closed", "eligible": False,
         "exclude_reason": "execution retune closed; not ENTRY rebase", "SOURCE_FILE": "src/research/simple_tech_redesign/e4_fallback_no_adverse_price_v1_spec.py", "SOURCE_FUNCTION": None, "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_redesign/e4_fallback_no_adverse_price_v1_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": "execution", "EVENT_TIMESTAMP_SEMANTICS": "E4"},
        {"ENTRY_ID": "TECHNICAL_EXIT_FAMILIES", "SOURCE_ANALYSIS_ID": "V14-V29 / Branch U/P", "SOURCE_STATUS": "closed", "eligible": False,
         "exclude_reason": "EXIT families; CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED=true", "SOURCE_FILE": "src/research/simple_tech_redesign/branch_u_rci_volume_confirm_exit_v1_spec.py", "SOURCE_FUNCTION": None, "SOURCE_SHA": _sha(NATIVE / "src/research/simple_tech_redesign/branch_u_rci_volume_confirm_exit_v1_spec.py"), "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": "n/a", "EVENT_TIMESTAMP_SEMANTICS": "n/a"},
        {"ENTRY_ID": "DYNAMIC_ANCHOR_TRAIL10", "SOURCE_ANALYSIS_ID": "prior runtime EXIT overlays", "SOURCE_STATUS": "closed", "eligible": False,
         "exclude_reason": "Dynamic Anchor fixed replacement and TRAIL10 closed; not ENTRY", "SOURCE_FILE": None, "SOURCE_FUNCTION": None, "SOURCE_SHA": None, "SOURCE_LINE": None, "EXACT_RULE_TEXT": None, "TIMEFRAME": "n/a", "EVENT_TIMESTAMP_SEMANTICS": "n/a"},
    ]
    for r in rows:
        r.setdefault("exclude_reason", None)
        r["MARKOUT_SEMANTICS"] = MARKOUT_SEMANTICS
    return rows


def _arm_from_report(path: Path, key: str) -> dict[str, Any]:
    body = _load(NATIVE / path if not path.is_absolute() else path)
    arms = dict(body.get("arms") or {})
    return dict(arms.get(key) or {})


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d.get(k)
    return None


def normalize_metrics(raw: dict[str, Any], *, artifact: str, arm_key: str) -> dict[str, Any]:
    exe = _pick(raw, "EXECUTABLE_SIGNAL_N", "FINAL_EXECUTABLE_N")
    sig = _pick(raw, "SIGNAL_N", "BOARD_PASS_N")
    return {
        "ARTIFACT": artifact,
        "ARM_KEY": arm_key,
        "SIGNAL_N": sig,
        "EXECUTION_EVALUABLE_N": exe,
        "MEAN_60": _pick(raw, "MARKOUT60_MEAN", "MARKOUT_60_MEAN"),
        "MEDIAN_60": _pick(raw, "MARKOUT60_MEDIAN", "MARKOUT_60_MEDIAN"),
        "MEAN_180": _pick(raw, "MARKOUT180_MEAN", "MARKOUT_180_MEAN", "FINAL_MARKOUT_180_MEAN"),
        "MEDIAN_180": _pick(raw, "MARKOUT180_MEDIAN", "MARKOUT_180_MEDIAN", "FINAL_MARKOUT_180_MEDIAN"),
        "MEAN_300": _pick(raw, "MARKOUT300_MEAN", "MARKOUT_300_MEAN", "FINAL_MARKOUT_300_MEAN"),
        "MEDIAN_300": _pick(raw, "MARKOUT300_MEDIAN", "MARKOUT_300_MEDIAN", "FINAL_MARKOUT_300_MEDIAN"),
        "MFE300_MEAN": _pick(raw, "MFE_MEAN"),
        "MAE300_MEAN": _pick(raw, "MAE_MEAN"),
        "POSITIVE_DAY_N_180": _pick(raw, "POSITIVE_DAY_N_180"),
        "NEGATIVE_DAY_N_180": _pick(raw, "NEGATIVE_DAY_N_180"),
        "POSITIVE_DAY_N_300": _pick(raw, "POSITIVE_DAY_N_300"),
        "NEGATIVE_DAY_N_300": _pick(raw, "NEGATIVE_DAY_N_300"),
        "EX_BEST_DAY_MEAN_180": _pick(raw, "EX_BEST_180", "EX_BEST_DAY_MARKOUT_180"),
        "EX_BEST_DAY_MEAN_300": _pick(raw, "EX_BEST_300", "EX_BEST_DAY_MARKOUT_300"),
        "DROP_TOP_SYMBOL_MEAN_180": _pick(raw, "DROP_TOP_SYMBOL_180", "DROP_TOP_SYMBOL_MARKOUT_180", "FINAL_DROP_TOP_SYMBOL_MARKOUT_180"),
        "DROP_TOP_SYMBOL_MEAN_300": _pick(raw, "DROP_TOP_SYMBOL_300", "DROP_TOP_SYMBOL_MARKOUT_300", "FINAL_DROP_TOP_SYMBOL_MARKOUT_300"),
        "TOP_DAY_SHARE": _pick(raw, "BEST_DAY_CONTRIBUTION", "TOP_DAY_SHARE"),
        "TOP_SYMBOL_SHARE": _pick(raw, "TOP_SYMBOL_SHARE", "TOP_SYMBOL_CONTRIBUTION"),
        "TOP_SYMBOL": raw.get("TOP_SYMBOL"),
        "REUSE_PRIOR_METRICS": True,
        "RECOMPUTE_REASON": None,
    }


def _load_day_folder(cache: Path, *, today: str = TODAY) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or str(day) == str(today):
            return {"ok": False, "blocker": f"FORBIDDEN_DAY:{day}"}
        path = cache / f"day_{day}.json"
        body = _load(path)
        if not body.get("ok") and not list(body.get("rows") or []):
            return {"ok": False, "blocker": f"cache_missing:{cache.name}:{day}"}
        rows.extend([dict(r) for r in list(body.get("rows") or [])])
    return {"ok": True, "rows": rows, "N": len(rows), "cache": str(cache)}


def load_v8_rows(*, today: str = TODAY) -> dict[str, Any]:
    return _load_day_folder(V8_CACHE, today=today)


def load_v4_pr_rows(*, today: str = TODAY) -> dict[str, Any]:
    return _load_day_folder(V4_PR_CACHE, today=today)


def load_v6_pr_rows(*, today: str = TODAY) -> dict[str, Any]:
    return _load_day_folder(V6_PR_CACHE, today=today)


def recover_concentration(exe_rows: list[dict[str, Any]]) -> dict[str, Any]:
    c180 = concentration(exe_rows, "markout_180")
    c300 = concentration(exe_rows, "markout_300")
    n = len(exe_rows)
    by_sym: dict[str, int] = {}
    for r in exe_rows:
        s = _bare(r.get("symbol"))
        by_sym[s] = int(by_sym.get(s, 0) or 0) + 1
    top_sym = max(by_sym, key=lambda k: by_sym[k]) if by_sym else None
    share = (float(by_sym[top_sym]) / float(n)) if n and top_sym else None
    return {
        "TOP_DAY_SHARE": c180.get("BEST_DAY_CONTRIBUTION"),
        "TOP_DAY": c180.get("BEST_DAY"),
        "TOP_SYMBOL_CONTRIBUTION_180": c180.get("TOP_SYMBOL_CONTRIBUTION"),
        "TOP_SYMBOL_CONTRIBUTION_300": c300.get("TOP_SYMBOL_CONTRIBUTION"),
        "TOP_SYMBOL_COUNT_SHARE": share,
        "EX_BEST_DAY_MEAN_180": c180.get("EX_BEST_DAY_MARKOUT"),
        "EX_BEST_DAY_MEAN_300": c300.get("EX_BEST_DAY_MARKOUT"),
    }


def attach_cache_concentration(metrics: dict[str, Any], exe_rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(metrics)
    conc = recover_concentration(exe_rows)
    if out.get("TOP_DAY_SHARE") is None:
        out["TOP_DAY_SHARE"] = conc.get("TOP_DAY_SHARE")
        out["TOP_DAY_SHARE_RECOVERED_FROM_V8_CACHE"] = True
    if out.get("TOP_SYMBOL_SHARE") is None:
        out["TOP_SYMBOL_SHARE"] = conc.get("TOP_SYMBOL_COUNT_SHARE")
    out["TOP_SYMBOL_CONTRIBUTION"] = conc.get("TOP_SYMBOL_CONTRIBUTION_180")
    out["TOP_SYMBOL_COUNT_SHARE"] = conc.get("TOP_SYMBOL_COUNT_SHARE")
    out["CACHE_EXE_N"] = len(exe_rows)
    return out


def load_eligible_metrics(
    *,
    v8_rows: list[dict[str, Any]],
    v4_rows: list[dict[str, Any]] | None = None,
    v6_rows: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    v8_rep = RESULTS_ENTRY / "v8_architecture_role_rca" / "report.json"
    v10_rep = RESULTS_ENTRY / "v10_rci_board_role_rca" / "report.json"
    v9_rep = RESULTS_ENTRY / "v9_trend_context_rca" / "report.json"
    v4_rep = RESULTS_ENTRY / "v4_persistence_rule" / "report.json"
    v6_rep = RESULTS_ENTRY / "v6_pullback_rule" / "report.json"
    v8_arms = dict(_load(NATIVE / v8_rep).get("arms") or {})
    v10_arms = dict(_load(NATIVE / v10_rep).get("arms") or {})
    v9_arms = dict(_load(NATIVE / v9_rep).get("arms") or {})
    v4_req = dict(_load(NATIVE / v4_rep).get("required") or {})
    v6_arms = dict(_load(NATIVE / v6_rep).get("arms") or {})
    bits = [attach_bits(r) for r in v8_rows]
    out: dict[str, dict[str, Any]] = {}

    def v8m(eid: str, arm: str) -> dict[str, Any]:
        m = normalize_metrics(dict(v8_arms.get(arm) or {}), artifact=str(v8_rep), arm_key=arm)
        pack = v8_filter(v8_rows, arm)
        m = attach_cache_concentration(m, list(pack.get("exe_rows") or []))
        m["CACHE_SIGNAL_N"] = pack.get("SIGNAL_N")
        m["CACHE_EXE_N"] = pack.get("EXECUTABLE_SIGNAL_N")
        return m

    out["V1_FULL_STACK"] = v8m("V1_FULL_STACK", "A0_V1")
    out["V8_A1_NO_PRICE_ACTION"] = v8m("V8_A1", "A1_NO_PRICE_ACTION")
    out["V8_A2_NO_VOLUME"] = v8m("V8_A2", "A2_NO_VOLUME")
    out["V8_A3_T3_PULLBACK_RCI_BOARD"] = v8m("V8_A3", "A3_NO_PA_NO_VOLUME")
    out["V8_A4_PULLBACK_RCI_BOARD"] = v8m("V8_A4", "A4_CORE")
    out["V8_A5_PULLBACK_BOARD"] = v8m("V8_A5", "A5_CORE_NO_RCI")
    out["V8_A6_PULLBACK_RCI"] = v8m("V8_A6", "A6_CORE_NO_BOARD")

    for eid, arm in (("V10_B0_T3_PULLBACK", "B0_T3_PULLBACK"), ("V10_B1_T3_PULLBACK_RCI", "B1_RCI")):
        m = normalize_metrics(dict(v10_arms.get(arm) or {}), artifact=str(v10_rep), arm_key=arm)
        hit = [r for r in v8_rows if v10_arm_pass(r, arm)]
        exe = [r for r in hit if r.get("executable_signal")]
        m = attach_cache_concentration(m, exe)
        m["CACHE_SIGNAL_N"] = len(hit)
        m["CACHE_EXE_N"] = len(exe)
        out[eid] = m

    for eid, arm in (("V9_T1_CROSS_ONLY", "T1_CROSS_ONLY"), ("V9_T2_SLOPE_ONLY", "T2_SLOPE_ONLY")):
        m = normalize_metrics(dict(v9_arms.get(arm) or {}), artifact=str(v9_rep), arm_key=arm)
        hit = [r for r in bits if v9_arm_pass(r, arm)]
        exe = [r for r in hit if r.get("executable_signal")]
        m = attach_cache_concentration(m, exe)
        m["CACHE_SIGNAL_N"] = len(hit)
        m["CACHE_EXE_N"] = len(exe)
        out[eid] = m

    for eid, key in (("V4_P60", "P60"), ("V4_P80", "P80"), ("V4_P90", "P90")):
        raw = dict(v4_req.get(key) or {})
        m = normalize_metrics(raw, artifact=str(v4_rep), arm_key=key)
        if v4_rows:
            pack = v4_filter(v4_rows, key)
            m = attach_cache_concentration(m, list(pack.get("exe_rows") or []))
            m["CACHE_SIGNAL_N"] = pack.get("BOARD_PASS_N") or pack.get("VOLUME_PASS_N")
            m["CACHE_EXE_N"] = pack.get("EXECUTABLE_SIGNAL_N")
        out[eid] = m

    for eid, key in (("V6_D10", "D10"), ("V6_D20", "D20"), ("V6_D30", "D30")):
        raw = dict(v6_arms.get(key) or {})
        mapped = {
            "SIGNAL_N": raw.get("BOARD_PASS_N") or raw.get("FINAL_EXECUTABLE_N"),
            "EXECUTABLE_SIGNAL_N": raw.get("FINAL_EXECUTABLE_N") or raw.get("EXECUTABLE_SIGNAL_N"),
            "MARKOUT_60_MEAN": raw.get("FINAL_MARKOUT_60_MEAN") or raw.get("STAGE_MARKOUT_60_MEAN"),
            "MARKOUT_60_MEDIAN": raw.get("FINAL_MARKOUT_60_MEDIAN"),
            "MARKOUT_180_MEAN": raw.get("FINAL_MARKOUT_180_MEAN"),
            "MARKOUT_180_MEDIAN": raw.get("FINAL_MARKOUT_180_MEDIAN"),
            "MARKOUT_300_MEAN": raw.get("FINAL_MARKOUT_300_MEAN"),
            "MARKOUT_300_MEDIAN": raw.get("FINAL_MARKOUT_300_MEDIAN"),
            "POSITIVE_DAY_N_180": raw.get("FINAL_POSITIVE_DAY_N_180"),
            "NEGATIVE_DAY_N_180": raw.get("FINAL_NEGATIVE_DAY_N_180"),
            "POSITIVE_DAY_N_300": raw.get("FINAL_POSITIVE_DAY_N_300"),
            "NEGATIVE_DAY_N_300": raw.get("FINAL_NEGATIVE_DAY_N_300"),
            "EX_BEST_DAY_MARKOUT_180": raw.get("FINAL_EX_BEST_DAY_MARKOUT_180"),
            "EX_BEST_DAY_MARKOUT_300": raw.get("FINAL_EX_BEST_DAY_MARKOUT_300"),
            "DROP_TOP_SYMBOL_MARKOUT_180": raw.get("FINAL_DROP_TOP_SYMBOL_MARKOUT_180"),
            "DROP_TOP_SYMBOL_MARKOUT_300": raw.get("FINAL_DROP_TOP_SYMBOL_MARKOUT_300"),
            "MFE_MEAN": raw.get("FINAL_MFE_MEAN"),
            "MAE_MEAN": raw.get("FINAL_MAE_MEAN"),
            "TOP_SYMBOL_SHARE": raw.get("FINAL_TOP_SYMBOL_SHARE"),
        }
        m = normalize_metrics(mapped, artifact=str(v6_rep), arm_key=key)
        m["POPULATION"] = "V1_FULL_STACK_WITH_DEPTH_PULLBACK"
        if v6_rows:
            pack = v6_filter(v6_rows, key)
            m = attach_cache_concentration(m, list(pack.get("final_exe_rows") or pack.get("exe_rows") or []))
            m["CACHE_SIGNAL_N"] = pack.get("BOARD_PASS_N")
            m["CACHE_EXE_N"] = pack.get("EXECUTABLE_SIGNAL_N")
        out[eid] = m
    if set(out) != set(ELIGIBLE_ENTRY_IDS):
        missing = [eid for eid in ELIGIBLE_ENTRY_IDS if eid not in out]
        extra = [eid for eid in out if eid not in set(ELIGIBLE_ENTRY_IDS)]
        raise RuntimeError(f"eligible metric key mismatch missing={missing} extra={extra}")
    return out


def reuse_audit(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reused = 0
    recomputed = 0
    reasons: list[dict[str, Any]] = []
    for eid in ELIGIBLE_ENTRY_IDS:
        m = dict(metrics.get(eid) or {})
        if bool(m.get("REUSE_PRIOR_METRICS")) and not m.get("RECOMPUTE_REASON"):
            reused += 1
        else:
            recomputed += 1
            reasons.append({"ENTRY_ID": eid, "RECOMPUTE_REASON": m.get("RECOMPUTE_REASON") or "REUSE_FLAG_FALSE"})
    return {
        "REUSED_RULE_N": int(reused),
        "RECOMPUTED_RULE_N": int(recomputed),
        "RECOMPUTE_REASONS": reasons,
        "RESTREAM_N": 0,
        "RECAPTURE_N": 0,
        "note": "Join-only concentration recovery from existing day caches is reuse, not markout recompute.",
    }


def parity_ok(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    b1 = metrics.get("V10_B1_T3_PULLBACK_RCI") or {}
    sig_ok = int(b1.get("CACHE_SIGNAL_N") or 0) == int(V10_B1_SIGNAL_N)
    exe_ok = int(b1.get("CACHE_EXE_N") or 0) == int(V10_B1_EXE_N)
    return {
        "ok": bool(sig_ok and exe_ok),
        "B1_CACHE_SIGNAL_N": b1.get("CACHE_SIGNAL_N"),
        "B1_CACHE_EXE_N": b1.get("CACHE_EXE_N"),
        "B1_PUB_SIGNAL_N": b1.get("SIGNAL_N"),
        "B1_PUB_EXE_N": b1.get("EXECUTION_EVALUABLE_N"),
        "EXPECTED_SIGNAL": V10_B1_SIGNAL_N,
        "EXPECTED_EXE": V10_B1_EXE_N,
    }
