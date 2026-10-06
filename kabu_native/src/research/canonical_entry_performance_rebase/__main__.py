"""Offline Canonical ENTRY performance rebase. No Runtime write. No Paper. No C3 refit. No C4."""
from __future__ import annotations

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

from research.canonical_entry_performance_rebase import (
    ANALYSIS_ID,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_AUDIT_VERDICT_MAINTAINED,
    C3_MODEL_STATUS,
    C3_RECON_VERDICT_MAINTAINED,
    C3_REFIT,
    C3_RETRAIN_STARTED,
    C3_VERDICT_MAINTAINED,
    C4_STARTED,
    CLOCK_CHANGED,
    CONTRACT_VERDICT_MAINTAINED,
    ELIGIBLE_DAYS,
    EXECUTION_AWARE_MODEL_CREATED,
    EXIT_CHANGED,
    EXPECTED_A0,
    EXPECTED_A2,
    EXPECTED_C3_FINAL,
    EXPECTED_C3_OOF,
    FILL_CHANGED,
    NEW_FEATURE_CREATED,
    NEW_FORWARD_N,
    NEW_MODEL_CREATED,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    PARITY_VERDICT_MAINTAINED,
    PERFORMANCE_REBASE_STARTED,
    REENTRY_V2_FORMAL,
    RUNTIME_CHANGED,
    TRUE_OOS,
)
from research.canonical_entry_performance_rebase.analyze import (
    attach_would_fill,
    coverage_loss,
    decide,
    exit_classes_canonical,
    first_entry_block,
    join_target,
    pack_line,
    paired_daily,
    parity_against,
    populations,
    ranking_edge_present,
    ranking_no_backfill,
    row_key,
    slim_rank,
    target_integrity,
    topk_exec_block,
    wf_index,
)
from research.canonical_entry_performance_rebase.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_decision_population_contract.analyze import snap_key
from research.entry_objective_redesign_c3.oof import TARGET
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CANON_CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_panel_exact_reconciliation"
C3_CACHE = NATIVE / "results" / "research" / "entry_objective_redesign_c3" / "_work_cache"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"
COUPLE_CACHE = NATIVE / "results" / "research" / "c3_execution_coupling_audit" / "_work_cache"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_stage(cache_dir: Path, days: tuple[str, ...], stage: str) -> tuple[list[dict], list[dict], list[str]]:
    trades: list[dict] = []
    admits: list[dict] = []
    missing: list[str] = []
    for day in days:
        fp = cache_dir / f"{day}_{stage}.json"
        if not fp.is_file():
            missing.append(day)
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not body.get("ok"):
            missing.append(day)
            continue
        trades.extend(body.get("trades") or [])
        admits.extend(body.get("admits") or [])
    return trades, admits, missing


def _load_canon(days: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    snaps: list[dict] = []
    missing: list[str] = []
    for day in days:
        fp = CANON_CACHE / f"{day}_CANONICAL.json"
        if not fp.is_file():
            missing.append(day)
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not body.get("ok") or body.get("c3_series_policy") != "collected_only":
            missing.append(day)
            continue
        snaps.extend(body.get("snaps") or [])
    return snaps, missing


def _load_would_fill(days: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    missing: list[str] = []
    for day in days:
        oof = _load(COUPLE_CACHE / f"{day}_OOF_EXACT.json")
        fin = _load(COUPLE_CACHE / f"{day}_C3_FINAL_TRACE.json")
        wf = list(oof.get("would_fill") or []) or list(fin.get("would_fill") or [])
        if not oof.get("ok") and not fin.get("ok"):
            missing.append(day)
            continue
        rows.extend(wf)
    return rows, missing


def _f(v: Any):
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _failed(required: dict, extra: dict | None = None, sheets_extra: dict | None = None) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": {
            "VERDICT": required.get("VERDICT"),
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": required.get("CANONICAL_ENTRY_BASELINE_ESTABLISHED"),
            "OLD_C3_INTERPRETATION": required.get("OLD_C3_INTERPRETATION"),
            "NEXT_RESEARCH": required.get("NEXT_RESEARCH"),
            "note": required.get("note") or "STOP. Exact parity / integrity failed. Values not adopted as new SoT.",
        },
        "C3_MODEL_STATUS": C3_MODEL_STATUS,
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
            "C3_RECON": C3_RECON_VERDICT_MAINTAINED,
            "CONTRACT": CONTRACT_VERDICT_MAINTAINED,
            "PARITY": PARITY_VERDICT_MAINTAINED,
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
        },
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows({"ANALYSIS_ID": ANALYSIS_ID, "STOP": True, "C3_MODEL_STATUS": C3_MODEL_STATUS}),
        "Parity": kv_rows(required),
        "Populations": [{"empty": True}],
        "TargetIntegrity": [{"empty": True}],
        "MatchedRanking": [{"empty": True}],
        "PairedDays": [{"empty": True}],
        "ActualExec": [{"empty": True}],
        "MatchedExec": [{"empty": True}],
        "Coverage": [{"empty": True}],
        "ExitClasses": [{"empty": True}],
        "FirstEntry": [{"empty": True}],
        "Decision": kv_rows(report["decision"]),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "C3_REFIT": C3_REFIT,
                "C3_RETRAIN_STARTED": C3_RETRAIN_STARTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP CANONICAL_ENTRY_REBASE_FAILED. No C3 retrain. No C4. submit/cancel/live=0/0/0.", flush=True)
    return 2


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE CANONICAL ENTRY PERFORMANCE REBASE V2", flush=True)
    print("No C3 refit. No C4. Exact Dual-Lane is PARITY ANCHOR.", flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2

    days = list(ELIGIBLE_DAYS)
    a0_trades, _a0_ad, a0_miss = _load_stage(A_CACHE, ELIGIBLE_DAYS, "A0")
    a2_trades, _a2_ad, a2_miss = _load_stage(A_CACHE, ELIGIBLE_DAYS, "A2")
    c3_trades, c3_admits, c3_miss = _load_stage(C3_CACHE, ELIGIBLE_DAYS, "C3_EXACT")
    oof_trades, _oof_ad, oof_miss = _load_stage(COUPLE_CACHE, ELIGIBLE_DAYS, "OOF_EXACT")
    if not c3_admits:
        _tr_trades, c3_admits, tr_miss = _load_stage(COUPLE_CACHE, ELIGIBLE_DAYS, "C3_FINAL_TRACE")
        if tr_miss:
            print("WARN C3_FINAL_TRACE admits missing", tr_miss, flush=True)
    for t in c3_trades:
        if not t.get("anchor"):
            t["anchor"] = t.get("anchor_time")
    if a0_miss or a2_miss or c3_miss or oof_miss:
        print("STOP Exact cache missing", a0_miss, a2_miss, c3_miss, oof_miss, flush=True)
        return _failed(
            {
                "A0_PARITY": False,
                "A2_PARITY": False,
                "C3_FINAL_PARITY": False,
                "C3_OOF_EXACT_PARITY": False,
                "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
                "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
                "OLD_C3_INTERPRETATION": "INVALIDATED",
                "NEXT_RESEARCH": "NONE",
                "note": "Exact Dual-Lane cache missing. Values not adopted as new SoT.",
            }
        )

    a0_pack = pack_metrics(a0_trades, days)
    a2_pack = pack_metrics(a2_trades, days)
    c3_pack = pack_metrics(c3_trades, days)
    oof_pack = pack_metrics(oof_trades, days)
    p_a0 = parity_against(a0_pack, EXPECTED_A0)
    p_a2 = parity_against(a2_pack, EXPECTED_A2)
    p_c3 = parity_against(c3_pack, EXPECTED_C3_FINAL)
    p_oof = parity_against(oof_pack, EXPECTED_C3_OOF)
    print(f"A0_PARITY={p_a0.get('ok')} {pack_line(a0_pack)}", flush=True)
    print(f"A2_PARITY={p_a2.get('ok')} {pack_line(a2_pack)}", flush=True)
    print(f"C3_FINAL_PARITY={p_c3.get('ok')} {pack_line(c3_pack)}", flush=True)
    print(f"C3_OOF_EXACT_PARITY={p_oof.get('ok')} {pack_line(oof_pack)}", flush=True)
    parity_ok = bool(p_a0.get("ok") and p_a2.get("ok") and p_c3.get("ok") and p_oof.get("ok"))
    if not parity_ok:
        return _failed(
            {
                "A0_PARITY": bool(p_a0.get("ok")),
                "A0_CANONICAL": pack_line(a0_pack),
                "A2_PARITY": bool(p_a2.get("ok")),
                "A2_CANONICAL": pack_line(a2_pack),
                "C3_FINAL_PARITY": bool(p_c3.get("ok")),
                "C3_FINAL_CANONICAL": pack_line(c3_pack),
                "C3_OOF_EXACT_PARITY": bool(p_oof.get("ok")),
                "C3_OOF_EXACT_CANONICAL": pack_line(oof_pack),
                "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
                "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
                "OLD_C3_INTERPRETATION": "INVALIDATED",
                "NEXT_RESEARCH": "NONE",
                "note": "Exact parity anchor missed. Observed values not adopted as new SoT.",
            },
            extra={"parity": {"A0" : p_a0, "A2": p_a2, "C3_FINAL": p_c3, "C3_OOF": p_oof}},
            sheets_extra={"Parity": kv_rows({"A0": p_a0, "A2": p_a2, "C3_FINAL": p_c3, "C3_OOF": p_oof})},
        )

    canon, canon_miss = _load_canon(ELIGIBLE_DAYS)
    if canon_miss:
        print("STOP canonical snaps missing", canon_miss, flush=True)
        return _failed(
            {
                "A0_PARITY": True,
                "A2_PARITY": True,
                "C3_FINAL_PARITY": True,
                "C3_OOF_EXACT_PARITY": True,
                "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
                "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
                "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
                "NEXT_RESEARCH": "NONE",
                "note": "Canonical snaps missing (collected_only). No new harvest this run.",
            }
        )

    panel: list[dict] = []
    for day in ELIGIBLE_DAYS:
        body = _load(C3_CACHE / f"{day}_C3_PANEL.json")
        if not body.get("ok"):
            print("STOP panel missing", day, flush=True)
            return 2
        panel.extend(body.get("rows") or [])
    panel_by = {snap_key(r): r for r in panel}
    print(f"canonical_snaps={len(canon)} panel={len(panel)}", flush=True)

    joined = []
    join_miss = 0
    for s in canon:
        p = panel_by.get(row_key(s))
        if p is None:
            join_miss += 1
        joined.append(join_target(s, p))
    print(f"panel_join_miss={join_miss}", flush=True)

    pops = populations(joined)
    matched = pops["MATCHED_RANKING_POPULATION"]
    current_actual = pops["CURRENT_ACTUAL_SCORABLE"]
    c3_oof_actual = pops["C3_OOF_ACTUAL_SCORABLE"]
    c3_final_actual = pops["C3_FINAL_ACTUAL_SCORABLE"]
    exec_rows = [r for r in joined if r.get("exact_executable") or r.get("canonical_executable")]

    integ_matched = target_integrity(matched)
    integ_exec = target_integrity(exec_rows)
    integ_panel = target_integrity(panel)
    print(
        f"TARGET matched unexpected={integ_matched.get('TARGET_UNEXPECTED_MISSING_N')} "
        f"contam={integ_matched.get('TARGET_CONTAMINATION_N')} "
        f"valid={integ_matched.get('TARGET_V4_VALID_N')} missing={integ_matched.get('TARGET_V4_MISSING_N')}",
        flush=True,
    )
    target_ok = bool(integ_matched.get("ok") and integ_exec.get("ok"))
    if not target_ok:
        return _failed(
            {
                "A0_PARITY": True,
                "A0_CANONICAL": pack_line(a0_pack),
                "A2_PARITY": True,
                "A2_CANONICAL": pack_line(a2_pack),
                "C3_FINAL_PARITY": True,
                "C3_FINAL_CANONICAL": pack_line(c3_pack),
                "C3_OOF_EXACT_PARITY": True,
                "C3_OOF_EXACT_CANONICAL": pack_line(oof_pack),
                "MATCHED_RANKING_ROWS": len(matched),
                "TARGET_UNEXPECTED_MISSING_N": integ_matched.get("TARGET_UNEXPECTED_MISSING_N"),
                "TARGET_CONTAMINATION_N": integ_matched.get("TARGET_CONTAMINATION_N"),
                "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
                "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
                "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
                "NEXT_RESEARCH": "NONE",
                "note": "TARGET V4 integrity failed. STOP.",
            },
            extra={"target_integrity": {"matched": integ_matched, "exec": integ_exec, "panel": integ_panel}},
            sheets_extra={
                "TargetIntegrity": kv_rows({"matched": integ_matched, "exec": integ_exec, "panel": integ_panel})
            },
        )

    wf_rows, wf_miss = _load_would_fill(ELIGIBLE_DAYS)
    if wf_miss:
        print("WARN would_fill days missing", wf_miss, flush=True)
    wf_by = wf_index(wf_rows)
    attach_would_fill(joined, wf_by)
    print(f"would_fill_rows={len(wf_rows)} indexed={len(wf_by)}", flush=True)

    cov = coverage_loss(current_actual, c3_oof_actual)
    rank_cur = ranking_no_backfill(matched, "current_score")
    rank_oof = ranking_no_backfill(matched, "c3_oof_score")
    rank_fin = ranking_no_backfill(matched, "c3_live_score")
    paired = paired_daily(rank_cur, rank_oof)
    print(
        f"MATCHED={len(matched)} CURRENT_ACTUAL={len(current_actual)} "
        f"C3_OOF_ACTUAL={len(c3_oof_actual)} coverage_loss={cov.get('C3_COVERAGE_LOSS_ROWS')}",
        flush=True,
    )
    print(
        f"CURRENT TOP3_UPLIFT={rank_cur.get('TOP3_UPLIFT')} "
        f"C3_OOF TOP3_UPLIFT={rank_oof.get('TOP3_UPLIFT')} "
        f"delta={paired.get('TOP3_DELTA_MEAN')}",
        flush=True,
    )

    actual_cur = {k: topk_exec_block(current_actual, "current_score", k) for k in (1, 3, 5)}
    actual_oof = {k: topk_exec_block(c3_oof_actual, "c3_oof_score", k) for k in (1, 3, 5)}
    actual_fin = {k: topk_exec_block(c3_final_actual, "c3_live_score", k) for k in (1, 3, 5)}
    matched_cur = {k: topk_exec_block(matched, "current_score", k) for k in (1, 3, 5)}
    matched_oof = {k: topk_exec_block(matched, "c3_oof_score", k) for k in (1, 3, 5)}
    matched_fin = {k: topk_exec_block(matched, "c3_live_score", k) for k in (1, 3, 5)}

    exits = exit_classes_canonical(c3_trades, c3_admits, panel_by)
    first = first_entry_block(c3_trades, c3_admits, panel_by)
    print(
        f"EXIT A/B/C/D={exits.get('C3_EXIT_CLASS_A_N')}/{exits.get('C3_EXIT_CLASS_B_N')}/"
        f"{exits.get('C3_EXIT_CLASS_C_N')}/{exits.get('C3_EXIT_CLASS_D_N')} ok={exits.get('ok')} "
        f"FIRST_N={first.get('FIRST_N')}",
        flush=True,
    )
    if not exits.get("ok"):
        return _failed(
            {
                "A0_PARITY": True,
                "A0_CANONICAL": pack_line(a0_pack),
                "A2_PARITY": True,
                "A2_CANONICAL": pack_line(a2_pack),
                "C3_FINAL_PARITY": True,
                "C3_FINAL_CANONICAL": pack_line(c3_pack),
                "C3_OOF_EXACT_PARITY": True,
                "C3_OOF_EXACT_CANONICAL": pack_line(oof_pack),
                "C3_EXIT_CLASS_A_N": exits.get("C3_EXIT_CLASS_A_N"),
                "C3_EXIT_CLASS_B_N": exits.get("C3_EXIT_CLASS_B_N"),
                "C3_EXIT_CLASS_C_N": exits.get("C3_EXIT_CLASS_C_N"),
                "C3_EXIT_CLASS_D_N": exits.get("C3_EXIT_CLASS_D_N"),
                "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
                "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
                "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
                "NEXT_RESEARCH": "NONE",
                "note": "C3 FINAL 53 EXIT class partition failed.",
            },
            extra={"exit_classes": {k: v for k, v in exits.items() if k != "rows"}},
            sheets_extra={"ExitClasses": exits.get("rows") or [{"empty": True}]},
        )

    rank_edge = ranking_edge_present(rank_cur, rank_oof, paired)
    exact_fail = bool(_f(c3_pack.get("PnL")) is not None and float(c3_pack.get("PnL")) < 0)
    oof_fail = bool(_f(oof_pack.get("PnL")) is not None and float(oof_pack.get("PnL")) < 0)
    cur_f6 = _f((matched_cur[3] or {}).get("FILL_TO_600S_RETURN_MEAN"))
    oof_f6 = _f((matched_oof[3] or {}).get("FILL_TO_600S_RETURN_MEAN"))
    adverse = bool(cur_f6 is not None and oof_f6 is not None and float(oof_f6) < float(cur_f6))
    dec = decide(
        parity_ok=True,
        target_ok=True,
        exit_ok=True,
        rank_edge=rank_edge,
        exact_fail=bool(exact_fail and oof_fail),
        adverse=adverse,
    )

    required = {
        "A0_PARITY": True,
        "A0_CANONICAL": pack_line(a0_pack),
        "A2_PARITY": True,
        "A2_CANONICAL": pack_line(a2_pack),
        "C3_FINAL_PARITY": True,
        "C3_FINAL_CANONICAL": pack_line(c3_pack),
        "C3_OOF_EXACT_PARITY": True,
        "C3_OOF_EXACT_CANONICAL": pack_line(oof_pack),
        "MATCHED_RANKING_ROWS": len(matched),
        "CURRENT_ACTUAL_SCORABLE_ROWS": len(current_actual),
        "C3_ACTUAL_SCORABLE_ROWS": len(c3_oof_actual),
        "C3_COVERAGE_LOSS_ROWS": cov.get("C3_COVERAGE_LOSS_ROWS"),
        "CURRENT_MATCHED_TOP1_UPLIFT": rank_cur.get("TOP1_UPLIFT"),
        "CURRENT_MATCHED_TOP3_UPLIFT": rank_cur.get("TOP3_UPLIFT"),
        "CURRENT_MATCHED_TOP5_UPLIFT": rank_cur.get("TOP5_UPLIFT"),
        "C3_OOF_MATCHED_TOP1_UPLIFT": rank_oof.get("TOP1_UPLIFT"),
        "C3_OOF_MATCHED_TOP3_UPLIFT": rank_oof.get("TOP3_UPLIFT"),
        "C3_OOF_MATCHED_TOP5_UPLIFT": rank_oof.get("TOP5_UPLIFT"),
        "C3_OOF_TOP3_DELTA_VS_CURRENT": paired.get("TOP3_DELTA_MEAN"),
        "TOP3_DELTA_POSITIVE_DAYS": paired.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": paired.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "CURRENT_ACTUAL_TOP3_WOULD_FILL": (actual_cur[3] or {}).get("WOULD_FILL_1S_RATE"),
        "C3_OOF_ACTUAL_TOP3_WOULD_FILL": (actual_oof[3] or {}).get("WOULD_FILL_1S_RATE"),
        "C3_FINAL_ACTUAL_TOP3_WOULD_FILL": (actual_fin[3] or {}).get("WOULD_FILL_1S_RATE"),
        "CURRENT_MATCHED_TOP3_FILL_TO_600": (matched_cur[3] or {}).get("FILL_TO_600S_RETURN_MEAN"),
        "C3_OOF_MATCHED_TOP3_FILL_TO_600": (matched_oof[3] or {}).get("FILL_TO_600S_RETURN_MEAN"),
        "C3_FINAL_MATCHED_TOP3_FILL_TO_600": (matched_fin[3] or {}).get("FILL_TO_600S_RETURN_MEAN"),
        "C3_COVERAGE_SELECTION_DISPLACEMENT_N": cov.get("C3_COVERAGE_SELECTION_DISPLACEMENT_N"),
        "C3_EXIT_CLASS_A_N": exits.get("C3_EXIT_CLASS_A_N"),
        "C3_EXIT_CLASS_B_N": exits.get("C3_EXIT_CLASS_B_N"),
        "C3_EXIT_CLASS_C_N": exits.get("C3_EXIT_CLASS_C_N"),
        "C3_EXIT_CLASS_D_N": exits.get("C3_EXIT_CLASS_D_N"),
        "OLD_C3_INTERPRETATION": dec.get("OLD_C3_INTERPRETATION"),
        "CANONICAL_ENTRY_BASELINE_ESTABLISHED": dec.get("CANONICAL_ENTRY_BASELINE_ESTABLISHED"),
        "NEXT_RESEARCH": dec.get("NEXT_RESEARCH"),
        "VERDICT": dec.get("VERDICT"),
        "C3_MODEL_STATUS": C3_MODEL_STATUS,
        "TARGET_V4_VALID_N": integ_matched.get("TARGET_V4_VALID_N"),
        "TARGET_V4_MISSING_N": integ_matched.get("TARGET_V4_MISSING_N"),
        "TOP1_TARGET_MISSING_N": rank_cur.get("TOP1_TARGET_MISSING_N"),
        "TOP3_POSITION_TARGET_MISSING_N": rank_cur.get("TOP3_POSITION_TARGET_MISSING_N"),
        "TOP5_POSITION_TARGET_MISSING_N": rank_cur.get("TOP5_POSITION_TARGET_MISSING_N"),
        "TARGET_UNEXPECTED_MISSING_N": integ_matched.get("TARGET_UNEXPECTED_MISSING_N"),
        "TARGET_CONTAMINATION_N": integ_matched.get("TARGET_CONTAMINATION_N"),
        "C3_SCORE_UNAVAILABLE_N": cov.get("C3_SCORE_UNAVAILABLE_N"),
        "C3_FINAL_ACTUAL_SCORABLE_ROWS": len(c3_final_actual),
        "FIRST_N": first.get("FIRST_N"),
        "FIRST_FILL_TO_600": first.get("FIRST_FILL_TO_600"),
        "FIRST_PNL": first.get("FIRST_PNL"),
        "FIRST_PF": first.get("FIRST_PF"),
        "C3_OOF_MEAN_DAILY_SPEARMAN": rank_oof.get("MEAN_DAILY_SPEARMAN"),
        "C3_FINAL_MATCHED_TOP3_UPLIFT": rank_fin.get("TOP3_UPLIFT"),
        "C3_FINAL_RANKING_IS_INSAMPLE_DIAGNOSTIC": True,
        "PANEL_JOIN_MISS_N": join_miss,
        "C3_FORMAL_VERDICT_MAINTAINED": C3_VERDICT_MAINTAINED,
        "C3_AUDIT_VERDICT_MAINTAINED": C3_AUDIT_VERDICT_MAINTAINED,
        "C3_RECON_VERDICT_MAINTAINED": C3_RECON_VERDICT_MAINTAINED,
        "CONTRACT_VERDICT_MAINTAINED": CONTRACT_VERDICT_MAINTAINED,
        "PARITY_VERDICT_MAINTAINED": PARITY_VERDICT_MAINTAINED,
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": dec,
        "C3_MODEL_STATUS": C3_MODEL_STATUS,
        "parity": {"A0": p_a0, "A2": p_a2, "C3_FINAL": p_c3, "C3_OOF": p_oof},
        "packs": {
            "A0": a0_pack,
            "A2": a2_pack,
            "C3_FINAL": c3_pack,
            "C3_OOF": oof_pack,
        },
        "populations": {
            "MATCHED_RANKING_ROWS": len(matched),
            "CURRENT_ACTUAL_SCORABLE_ROWS": len(current_actual),
            "C3_ACTUAL_SCORABLE_ROWS": len(c3_oof_actual),
            "C3_FINAL_ACTUAL_SCORABLE_ROWS": len(c3_final_actual),
            "C3_COVERAGE_LOSS_ROWS": cov.get("C3_COVERAGE_LOSS_ROWS"),
            "note": (
                "MATCHED = Exact executable AND CURRENT score AND C3 OOF score. "
                "CURRENT actual = CURRENT score availability (A0 eligibility, no new exec gate). "
                "C3 actual = C3 OOF score availability on Exact executable names."
            ),
        },
        "target_integrity": {
            "matched": {k: v for k, v in integ_matched.items() if k != "counts"},
            "exec": {k: v for k, v in integ_exec.items() if k != "counts"},
            "panel": {k: v for k, v in integ_panel.items() if k != "counts"},
            "PANEL_JOIN_MISS_N": join_miss,
        },
        "ranking_matched": {
            "CURRENT": slim_rank(rank_cur),
            "C3_OOF": slim_rank(rank_oof),
            "C3_FINAL_INSAMPLE": slim_rank(rank_fin),
            "paired": {k: v for k, v in paired.items() if k != "days"},
            "rank_then_join": True,
            "NO_TARGET_BACKFILL": True,
        },
        "execution_actual": {"CURRENT": actual_cur, "C3_OOF": actual_oof, "C3_FINAL": actual_fin},
        "execution_matched": {"CURRENT": matched_cur, "C3_OOF": matched_oof, "C3_FINAL": matched_fin},
        "coverage": cov,
        "exit_classes": {k: v for k, v in exits.items() if k != "rows"},
        "first_entry": first,
        "adverse": {
            "rank_edge": rank_edge,
            "exact_fail": bool(exact_fail and oof_fail),
            "passive_fill_adverse_matched_top3": adverse,
            "CURRENT_MATCHED_TOP3_FILL_TO_600": cur_f6,
            "C3_OOF_MATCHED_TOP3_FILL_TO_600": oof_f6,
        },
        "frozen": {
            "C3": C3_VERDICT_MAINTAINED,
            "C3_AUDIT": C3_AUDIT_VERDICT_MAINTAINED,
            "C3_RECON": C3_RECON_VERDICT_MAINTAINED,
            "CONTRACT": CONTRACT_VERDICT_MAINTAINED,
            "PARITY": PARITY_VERDICT_MAINTAINED,
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
            "C14_CHANGED": C14_CHANGED,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_OPERATED": PAPER_OPERATED,
            "OPVAL_OPERATED": OPVAL_OPERATED,
            "C4_STARTED": C4_STARTED,
            "C3_REFIT": C3_REFIT,
            "C3_RETRAIN_STARTED": C3_RETRAIN_STARTED,
            "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
            "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
            "NEW_FEATURE_CREATED": NEW_FEATURE_CREATED,
            "PERFORMANCE_REBASE_STARTED": PERFORMANCE_REBASE_STARTED,
            "CLOCK_CHANGED": CLOCK_CHANGED,
            "EXIT_CHANGED": EXIT_CHANGED,
            "FILL_CHANGED": FILL_CHANGED,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
        },
        "canonical_key": "date|session|decision_anchor_time|symbol",
        "origin": "PENDING.anchor / CLOCK fire last t<=t0. No fill_time, nearest-anchor, or exit_time ranking key.",
        "target": TARGET,
    }
    report["_markdown"] = build_markdown(report)

    def _flat_exec(name: str, blk: dict) -> dict:
        return {"name": name, **blk}

    cov_day = [{"date": k, "n": v} for k, v in sorted((cov.get("by_day") or {}).items())]
    cov_an = [{"anchor": k, "n": v} for k, v in sorted((cov.get("by_anchor") or {}).items())]
    cov_sess = [{"session": k, "n": v} for k, v in sorted((cov.get("by_session") or {}).items())]

    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "C3_MODEL_STATUS": C3_MODEL_STATUS,
                "CLOCK": "CURRENT IRREGULAR",
                "Fill": "Corrected Passive Fill",
                "TARGET": TARGET,
                "NO_TARGET_BACKFILL": True,
                "A0": "CURRENT ENTRY + CURRENT eligibility. No new executable-at-decision gate.",
                "A2": "CURRENT ENTRY + Exact executable-at-decision.",
                "MATCHED": "Exact executable AND CURRENT score AND C3 OOF score",
                "ORIGIN": "PENDING.anchor",
            }
        ),
        "Parity": kv_rows({"A0": p_a0, "A2": p_a2, "C3_FINAL": p_c3, "C3_OOF": p_oof}),
        "Populations": kv_rows(report["populations"]),
        "TargetIntegrity": kv_rows(
            {
                "matched": integ_matched,
                "exec": integ_exec,
                "panel": integ_panel,
                "PANEL_JOIN_MISS_N": join_miss,
            }
        ),
        "MatchedRanking": kv_rows(
            {
                "CURRENT": slim_rank(rank_cur),
                "C3_OOF": slim_rank(rank_oof),
                "C3_FINAL_INSAMPLE": slim_rank(rank_fin),
                "paired": {k: v for k, v in paired.items() if k != "days"},
            }
        ),
        "PairedDays": paired.get("days") or [{"empty": True}],
        "ActualExec": [
            _flat_exec("CURRENT_TOP1", actual_cur[1]),
            _flat_exec("CURRENT_TOP3", actual_cur[3]),
            _flat_exec("CURRENT_TOP5", actual_cur[5]),
            _flat_exec("C3_OOF_TOP1", actual_oof[1]),
            _flat_exec("C3_OOF_TOP3", actual_oof[3]),
            _flat_exec("C3_OOF_TOP5", actual_oof[5]),
            _flat_exec("C3_FINAL_TOP1", actual_fin[1]),
            _flat_exec("C3_FINAL_TOP3", actual_fin[3]),
            _flat_exec("C3_FINAL_TOP5", actual_fin[5]),
        ],
        "MatchedExec": [
            _flat_exec("CURRENT_TOP1", matched_cur[1]),
            _flat_exec("CURRENT_TOP3", matched_cur[3]),
            _flat_exec("CURRENT_TOP5", matched_cur[5]),
            _flat_exec("C3_OOF_TOP1", matched_oof[1]),
            _flat_exec("C3_OOF_TOP3", matched_oof[3]),
            _flat_exec("C3_OOF_TOP5", matched_oof[5]),
            _flat_exec("C3_FINAL_TOP1", matched_fin[1]),
            _flat_exec("C3_FINAL_TOP3", matched_fin[3]),
            _flat_exec("C3_FINAL_TOP5", matched_fin[5]),
        ],
        "Coverage": cov_day + cov_an + cov_sess + [kv_rows(cov)[0]],
        "ExitClasses": exits.get("rows") or [{"empty": True}],
        "FirstEntry": kv_rows(first),
        "Decision": kv_rows(dec),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "C3_REFIT": C3_REFIT,
                "C3_RETRAIN_STARTED": C3_RETRAIN_STARTED,
                "NEW_MODEL_CREATED": NEW_MODEL_CREATED,
                "EXECUTION_AWARE_MODEL_CREATED": EXECUTION_AWARE_MODEL_CREATED,
                "PERFORMANCE_REBASE_STARTED": PERFORMANCE_REBASE_STARTED,
            }
        ),
    }
    # Coverage sheet: flatten counters properly
    sheets["Coverage"] = (
        [{"kind": "by_day", **r} for r in cov_day]
        + [{"kind": "by_anchor", **r} for r in cov_an]
        + [{"kind": "by_session", **r} for r in cov_sess]
        + kv_rows(
            {
                "C3_SCORE_UNAVAILABLE_N": cov.get("C3_SCORE_UNAVAILABLE_N"),
                "C3_COVERAGE_LOSS_ROWS": cov.get("C3_COVERAGE_LOSS_ROWS"),
                "C3_COVERAGE_SELECTION_DISPLACEMENT_N": cov.get("C3_COVERAGE_SELECTION_DISPLACEMENT_N"),
                "displacement_def": cov.get("displacement_def"),
            }
        )
    )
    write_artifacts(report, sheets)
    print(f"VERDICT={dec.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. No C3 retrain. No C4. Runtime unchanged. submit/cancel/live=0/0/0.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
