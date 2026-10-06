"""Offline SIMPLE_TECH_STRATEGY_LEVEL_ENTRY_REBASE_V1. Inventory + reuse prior markouts. No recapture. No EXIT add."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, ELIGIBLE_DAYS, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.isolation import (
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT,
    BRANCH_U_OUT,
    BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT,
    BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT,
    BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    ENTRY_THESIS_INVALIDATION_RCA_OUT,
    LIFECYCLE_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
    STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import capture_reporting_state
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_analyze import decide, score_all, select_one
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_harvest import (
    inventory_rows,
    load_eligible_metrics,
    load_v4_pr_rows,
    load_v6_pr_rows,
    load_v8_rows,
    parity_ok,
    reuse_audit,
)
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_spec import (
    ALIASES,
    ANALYSIS_ID,
    AUTO_HOP_NEXT_CANDIDATE,
    CAP_CHANGED,
    CONTROL_EXIT,
    ELIGIBLE_ENTRY_IDS,
    ENTRY_CHANGED,
    EXECUTION,
    EXECUTION_CHANGED,
    EXIT_ADDED,
    EXIT_CHANGED,
    FEATURE_SEARCH,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    FUTURE_DATA_USED,
    MAX_RESEARCH_DATE,
    ML_USED,
    NEW_COMBINATION,
    NEW_INDICATOR,
    NEW_THRESHOLD,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_SELECTION,
    PROSPECTIVE_HARVEST_SUSPENDED,
    RANKING_OPTIMIZATION,
    RESULTS_ENTRY,
    SESSION_CLOSE_PNL_HARD_REJECT,
    SIGNAL,
    SIZING_CHANGED,
    TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED,
    TRUE_OOS,
    V10_B1_EXE_N,
    V10_B1_MEAN_180,
    V10_B1_MEAN_300,
    V10_B1_MEDIAN_180,
    V10_B1_MEDIAN_300,
    V10_B1_NEG_DAY_180,
    V10_B1_POS_DAY_180,
    V10_B1_SIGNAL_N,
    V10_SELECTED_STACK,
    VOLUME_EXIT_PRIOR_VERDICT,
    canonical_spec,
    source_sha256_rebase,
    spec_sha256_rebase,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
    "PRIOR_ARTIFACT_MUTATED_N",
    "RECAPTURE_N",
    "RESTREAM_N",
    "NEW_INDICATOR_N",
    "NEW_THRESHOLD_N",
    "NEW_COMBINATION_N",
    "ML_N",
    "RANKING_N",
    "FEATURE_SEARCH_N",
    "EXIT_ADDED_N",
    "PNL_SELECTION_N",
    "AUTO_HOP_N",
    "SESSION_CLOSE_HARD_REJECT_N",
    "BURNED_USED_FOR_SELECTION_N",
    "R1B0_PROMOTED_N",
    "RECOMPUTE_N",
)

PRIOR_OUTS = (
    LIFECYCLE_OUT,
    BRANCH_U_OUT,
    BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT,
    BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT,
    BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT,
    BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    ENTRY_THESIS_INVALIDATION_RCA_OUT,
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
)

ENTRY_REPORTS = (
    RESULTS_ENTRY / "v8_architecture_role_rca" / "report.json",
    RESULTS_ENTRY / "v9_trend_context_rca" / "report.json",
    RESULTS_ENTRY / "v10_rci_board_role_rca" / "report.json",
    RESULTS_ENTRY / "v4_persistence_rule" / "report.json",
    RESULTS_ENTRY / "v6_pullback_rule" / "report.json",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _prior_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    for folder in PRIOR_OUTS:
        for name in ("report.json", "report.md", "audit.xlsx"):
            out[f"{folder.name}/{name}"] = _sha_file(folder / name)
    for rel in ENTRY_REPORTS:
        p = NATIVE / rel
        out[str(rel).replace("\\", "/")] = _sha_file(p)
    return out


def _public_inv(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = (
        "ENTRY_ID",
        "SOURCE_ANALYSIS_ID",
        "SOURCE_FILE",
        "SOURCE_FUNCTION",
        "SOURCE_SHA",
        "SOURCE_LINE",
        "EXACT_RULE_TEXT",
        "TIMEFRAME",
        "EVENT_TIMESTAMP_SEMANTICS",
        "SOURCE_STATUS",
        "eligible",
        "exclude_reason",
        "rule",
        "MARKOUT_SEMANTICS",
    )
    out = []
    for r in rows:
        out.append({k: r.get(k) for k in keep})
    return out


def _cand_line(r: dict[str, Any]) -> str:
    m = dict(r.get("metrics") or {})
    return (
        f"{r.get('ENTRY_ID')} sig={m.get('SIGNAL_N')} exe={m.get('EXECUTION_EVALUABLE_N')} "
        f"mean/med180={m.get('MEAN_180')}/{m.get('MEDIAN_180')} "
        f"mean/med300={m.get('MEAN_300')}/{m.get('MEDIAN_300')} "
        f"day180={m.get('POSITIVE_DAY_N_180')}/{m.get('NEGATIVE_DAY_N_180')} "
        f"day300={m.get('POSITIVE_DAY_N_300')}/{m.get('NEGATIVE_DAY_N_300')} "
        f"ex-best180/300={m.get('EX_BEST_DAY_MEAN_180')}/{m.get('EX_BEST_DAY_MEAN_300')} "
        f"drop-sym180/300={m.get('DROP_TOP_SYMBOL_MEAN_180')}/{m.get('DROP_TOP_SYMBOL_MEAN_300')} "
        f"top-day={m.get('TOP_DAY_SHARE')} top-sym-share={m.get('TOP_SYMBOL_SHARE')} "
        f"top-sym-contrib={m.get('TOP_SYMBOL_CONTRIBUTION')} qualified={r.get('qualified')} gates={r.get('gates')}"
    )


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    reuse = dict(report.get("reuse") or {})
    sel = dict(report.get("selection") or {})
    burned = dict(report.get("burned") or {})
    occ = dict(report.get("occupancy") or {})
    inv = list(report.get("inventory") or [])
    cands = list(report.get("candidates") or [])
    eligible = [r for r in inv if bool(r.get("eligible"))]
    excluded = [r for r in inv if not bool(r.get("eligible"))]
    na = "not_evaluated"
    return {
        "1": (
            "PRIMARY_GOAL remains fill coverage, then tolerate some ENTRY dilution, then Technical EXIT for failure, "
            "then Full Causal Portfolio, then Sizing. This run is not ENTRY-precision maximization and not EXIT search. "
            "It re-examines already-existing exact causal ENTRY rules because Technical EXIT on the current T3 population is exhausted."
        ),
        "2": (
            f"CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED={TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED}. "
            f"Prior volume-confirm EXIT verdict={VOLUME_EXIT_PRIOR_VERDICT}. No EXIT family is reopened this run."
        ),
        "3": (
            f"V10 selected stack={V10_SELECTED_STACK} B1_RCI SIGNAL_N={V10_B1_SIGNAL_N} exe={V10_B1_EXE_N} "
            f"mean180={V10_B1_MEAN_180} mean300={V10_B1_MEAN_300} median180={V10_B1_MEDIAN_180} median300={V10_B1_MEDIAN_300} "
            f"day180 pos/neg={V10_B1_POS_DAY_180}/{V10_B1_NEG_DAY_180}. ENTRY_SIGNAL_EDGE_SUPPORTED=false. "
            "Relative improvement existed; absolute ENTRY edge was not confirmed."
        ),
        "4": f"inventory_n={len(inv)} eligible_n={len(eligible)} excluded_n={len(excluded)} ids={[r.get('ENTRY_ID') for r in inv]}",
        "5": f"eligible={list(ELIGIBLE_ENTRY_IDS)} n={len(ELIGIBLE_ENTRY_IDS)}",
        "6": "; ".join(f"{r.get('ENTRY_ID')}:{r.get('exclude_reason')}" for r in excluded) or "none",
        "7": str(reuse.get("REUSED_RULE_N")),
        "8": f"RECOMPUTED_RULE_N={reuse.get('RECOMPUTED_RULE_N')} reasons={reuse.get('RECOMPUTE_REASONS')} restream=0 recapture=0",
        "9": " | ".join(_cand_line(r) for r in cands),
        "10": "see 9 EXECUTION_EVALUABLE_N",
        "11": "see 9 mean/median180",
        "12": "see 9 mean/median300",
        "13": "see 9 day signs",
        "14": "see 9 ex-best",
        "15": "see 9 drop-top-symbol",
        "16": "see 9 top-day / top-symbol contribution",
        "17": "see 9 gates; median is diagnostic only and is not a hard fail",
        "18": str(sel.get("n")),
        "19": str(sel.get("SELECTED_ENTRY_ID")),
        "20": str(sel.get("rule")),
        "21": "false",
        "22": str(bool(burned.get("evaluated"))),
        "23": f"mean180={burned.get('MEAN_180')} mean300={burned.get('MEAN_300')}" if burned.get("evaluated") else na,
        "24": str(burned.get("direction")) if burned.get("evaluated") else na,
        "25": str(bool(dec.get("ENTRY_BASE_CANDIDATE_FROZEN"))),
        "26": str(occ.get("E4_BINDING_COMPATIBLE")) if occ.get("checked") else na,
        "27": str(bool(dec.get("FULL_CAUSAL_DIAGNOSTIC_RAN"))),
        "28": str(occ.get("fill_n")) if occ.get("ran") else na,
        "29": str(occ.get("PnL")) if occ.get("ran") else na,
        "30": str(occ.get("PF")) if occ.get("ran") else na,
        "31": str(occ.get("MaxDD")) if occ.get("ran") else na,
        "32": str(occ.get("coverage")) if occ.get("ran") else na,
        "33": (
            "SESSION_CLOSE PnL is occupancy diagnostic only and is not an ENTRY hard reject. "
            "Not run because no DEV qualifier."
            if not occ.get("ran")
            else str(occ.get("session_close_interpretation"))
        ),
        "34": f"{dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "35": str(bool(dec.get("CURRENT_T3_STACK_CLOSED"))),
        "36": str(bool(dec.get("CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED"))),
        "37": str(bool(dec.get("NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED"))),
        "38": str(bool(dec.get("ENTRY_EXECUTION_REBIND_REQUIRED"))),
        "39": "false",
        "40": "false",
        "41": "false",
        "42": "false",
        "43": "false",
        "44": "false",
        "45": MAX_RESEARCH_DATE,
        "46": "true",
        "47": "false",
        "48": "false",
        "49": f"{int(SUBMIT_N)}/{int(CANCEL_N)}/{int(LIVE_ORDER_N)}",
        "50": str(dec.get("NEXT")),
    }


def _write(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))


def _base_report(
    *,
    pre: dict[str, Any],
    leak: dict[str, Any],
    sha: str,
    source_sha: str,
    decision: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "ENTRY_BASE_CANDIDATE_FROZEN": decision.get("ENTRY_BASE_CANDIDATE_FROZEN"),
            "CANDIDATE_FROZEN": decision.get("ENTRY_BASE_CANDIDATE_FROZEN"),
            "CURRENT_T3_STACK_CLOSED": decision.get("CURRENT_T3_STACK_CLOSED"),
            "CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED": decision.get("CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED"),
            "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256_EXPECTED,
            "first_eligible_prospective_date": FIRST_PROSPECTIVE_DAY,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "EXECUTION_CHANGED": False,
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "CONTROL_EXIT": CONTROL_EXIT,
            "PNL_USED_FOR_SELECTION": False,
            "SESSION_CLOSE_PNL_HARD_REJECT": False,
            "AUTO_HOP_NEXT_CANDIDATE": False,
        },
        "decision": decision,
        "leak": leak,
        "preflight": pre,
        "submit_cancel_live": {"submit": int(SUBMIT_N), "cancel": int(CANCEL_N), "live_order": int(LIVE_ORDER_N)},
        "answers": {},
        "_markdown": "",
    }
    if extra:
        report.update(extra)
    return report


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, extra: dict[str, Any] | None = None) -> int:
    decision = decide(integrity_ok=False, qualified_n=0, burned=None, e4_ok=None, session_close_pnl=None)
    decision["NEXT"] = msg
    report = _base_report(pre=pre, leak=leak, sha=sha, source_sha=source_sha, decision=decision, extra=extra)
    report["blocker"] = msg
    _write(report)
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def _day_blocker(day: str) -> str | None:
    d = str(day)
    if d in FORBIDDEN_INPUT_DAYS:
        return "FAIL_CLOSED_FORBIDDEN_INPUT"
    if d > str(MAX_RESEARCH_DATE):
        return "FAIL_CLOSED_FUTURE_DATA"
    if d >= str(TODAY):
        return "FAIL_CLOSED_ACTIVE_OR_FUTURE"
    return None


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_rebase()
    source_sha = source_sha256_rebase()
    pre = snapshot(phase="PRE")
    prior = _prior_hashes()
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_INDICATOR_N"] = int(bool(NEW_INDICATOR))
    leak["NEW_THRESHOLD_N"] = int(bool(NEW_THRESHOLD))
    leak["NEW_COMBINATION_N"] = int(bool(NEW_COMBINATION))
    leak["ML_N"] = int(bool(ML_USED))
    leak["RANKING_N"] = int(bool(RANKING_OPTIMIZATION))
    leak["FEATURE_SEARCH_N"] = int(bool(FEATURE_SEARCH))
    leak["EXIT_ADDED_N"] = int(bool(EXIT_ADDED))
    leak["PNL_SELECTION_N"] = int(bool(PNL_SELECTION))
    leak["AUTO_HOP_N"] = int(bool(AUTO_HOP_NEXT_CANDIDATE))
    leak["SESSION_CLOSE_HARD_REJECT_N"] = int(bool(SESSION_CLOSE_PNL_HARD_REJECT))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY} signal={SIGNAL} exec={EXECUTION} session={SESSION}", flush=True)

    if str(v1_spec_sha256()) != str(PARENT_SPEC_SHA256_EXPECTED):
        return _stop("STOP. Parent V1 spec SHA mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    vol = dict(_load(BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT / "report.json").get("required") or {})
    if str(vol.get("VERDICT") or "") != VOLUME_EXIT_PRIOR_VERDICT:
        return _stop("STOP. Volume-confirm EXIT prior verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    if not bool(vol.get("CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED")):
        return _stop("STOP. Technical EXIT is not marked exhausted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    for day in list(ELIGIBLE_DAYS):
        blk = _day_blocker(str(day))
        if blk:
            leak["FUTURE_DATA_N"] = 1 if "FUTURE" in blk else leak["FUTURE_DATA_N"]
            leak["INPUT_20260903_N"] = int(str(day) == "20260903")
            leak["INPUT_20260904_N"] = int(str(day) == "20260904")
            return _stop(f"STOP. DEV day blocked {day}: {blk}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)
    for day in list(LOCKED_SERIES_DAYS):
        # Burned days are listed for protocol only; not read unless a qualifier exists.
        blk = _day_blocker(str(day))
        if blk:
            return _stop(f"STOP. Burned day label blocked {day}: {blk}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha)

    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n([], str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))

    inv = inventory_rows()
    if any(str(r.get("ENTRY_ID")) == "V10_R1B0" and bool(r.get("eligible")) for r in inv):
        leak["R1B0_PROMOTED_N"] = 1
        return _stop("STOP. R1B0 was treated as eligible.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, extra={"inventory": _public_inv(inv)})

    v8 = load_v8_rows()
    if not v8.get("ok"):
        return _stop(f"STOP. V8 cache join failed: {v8.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, extra={"inventory": _public_inv(inv)})
    v4 = load_v4_pr_rows()
    if not v4.get("ok"):
        return _stop(f"STOP. V4 persistence cache join failed: {v4.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, extra={"inventory": _public_inv(inv)})
    v6 = load_v6_pr_rows()
    if not v6.get("ok"):
        return _stop(f"STOP. V6 pullback cache join failed: {v6.get('blocker')}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, extra={"inventory": _public_inv(inv)})

    metrics = load_eligible_metrics(v8_rows=list(v8.get("rows") or []), v4_rows=list(v4.get("rows") or []), v6_rows=list(v6.get("rows") or []))
    par = parity_ok(metrics)
    if not par.get("ok"):
        return _stop(f"STOP. B1 cache parity failed: {par}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, extra={"inventory": _public_inv(inv), "parity": par})

    reuse = reuse_audit(metrics)
    leak["RECOMPUTE_N"] = int(reuse.get("RECOMPUTED_RULE_N") or 0)
    leak["RESTREAM_N"] = int(reuse.get("RESTREAM_N") or 0)
    leak["RECAPTURE_N"] = int(reuse.get("RECAPTURE_N") or 0)

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    scored = score_all(metrics, integrity_ok=leak_ok)
    sel = select_one(scored)
    if bool(sel.get("PNL_USED_FOR_SELECTION")):
        leak["PNL_SELECTION_N"] = 1
        leak_ok = False
        scored = score_all(metrics, integrity_ok=False)
        sel = select_one(scored)

    burned = {"evaluated": False, "reason": "no_dev_qualifier"}
    occ = {"checked": False, "ran": False}
    if int(sel.get("n") or 0) > 0:
        leak["BURNED_USED_FOR_SELECTION_N"] = 0
        burned = {"evaluated": False, "reason": "burned_join_only_metrics_absent_restream_forbidden"}

    decision = decide(
        integrity_ok=leak_ok,
        qualified_n=int(sel.get("n") or 0),
        burned=burned if int(sel.get("n") or 0) > 0 else None,
        e4_ok=None,
        session_close_pnl=None,
        full_causal_ran=False,
    )
    decision["CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"] = True
    decision["PNL_USED_FOR_SELECTION"] = False
    decision["SESSION_CLOSE_USED_AS_ENTRY_REJECT"] = False
    decision["AUTO_HOP_NEXT_CANDIDATE"] = False
    decision["SELECTED_ENTRY_ID"] = sel.get("SELECTED_ENTRY_ID")
    decision["SELECTED_RULE_SHA"] = None
    decision["SELECTED_SOURCE_SHA"] = source_sha if sel.get("SELECTED_ENTRY_ID") else None

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        decision = decide(integrity_ok=False, qualified_n=0, burned=None, e4_ok=None, session_close_pnl=None)

    post = snapshot(phase="POST")
    report = _base_report(
        pre=pre,
        leak=leak,
        sha=sha,
        source_sha=source_sha,
        decision=decision,
        extra={
            "inventory": _public_inv(inv),
            "candidates": scored,
            "selection": sel,
            "reuse": reuse,
            "parity": par,
            "burned": burned,
            "occupancy": occ,
            "aliases": ALIASES,
            "prior_hashes": prior,
            "postflight": post,
            "reporting_semantics": reporting_semantics(pre, post),
            "capture_reporting_state": capture_reporting_state(pre, post),
            "runtime_flags": {
                "ENTRY_CHANGED": ENTRY_CHANGED,
                "EXIT_CHANGED": EXIT_CHANGED,
                "EXECUTION_CHANGED": EXECUTION_CHANGED,
                "CAP_CHANGED": CAP_CHANGED,
                "SIZING_CHANGED": SIZING_CHANGED,
                "FUTURE_DATA_USED": FUTURE_DATA_USED,
                "TRUE_OOS": TRUE_OOS,
                "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
            },
        },
    )
    _write(report)
    names = sorted(p.name for p in STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT.iterdir() if p.is_file())
    if names != ["audit.xlsx", "report.json", "report.md"]:
        print(f"WARN extra artifacts {names}", flush=True)
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"QUALIFIED_N={sel.get('n')} REUSED={reuse.get('REUSED_RULE_N')} RECOMPUTED={reuse.get('RECOMPUTED_RULE_N')} "
        f"T3_CLOSED={decision.get('CURRENT_T3_STACK_CLOSED')} FAMILY_CLOSED={decision.get('CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED')} "
        f"out={STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
