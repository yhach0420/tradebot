"""Offline AM C0 reused-history stress. No Runtime write. No Paper. No refit on stress days."""
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

from research.am_c0_indicator_exit.isolation import advanced
from research.am_c0_reused_history_stress_20260828_20260902_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXPECTED_C0_SPEC_SHA256,
    LABEL,
    MAX_RESEARCH_DATE,
    MIN_AUGMENT_TRADE_N,
    PROSPECTIVE_HARVEST_SUSPENDED,
    STRESS_DAYS,
    TRUE_OOS,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.analyze import (
    build_answers,
    concentration,
    decide,
    economic_gates,
    fill_rate,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.harvest import (
    development_current_parity,
    eval_c0_overlay,
    file_sha256,
    harvest_stress,
    pin_identity,
    prior_search,
    score_arm,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.isolation import (
    CACHE,
    C0_DECISION_OUT,
    EXIT_DECISION_OUT,
    OUT,
    REGIME_LABELED,
    snapshot,
    write_overlap_n,
    set_research_priority_below_normal,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.publish import (
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.spec import canonical_spec, source_sha256, spec_sha256
from research.am_current_utility_augment.ensemble import attach_ensemble
from research.am_entry_architecture_final_reassessment import A3_REUSE_ARM, A5_REUSE_ARM, C0
from research.am_entry_architecture_final_reassessment.consensus import cohort_decisions, merge_model_fields, tag_consensus
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, REPRESENTATION_N, SUBMIT_N
from research.am_entry_research_final_decision import PROSPECTIVE_CHALLENGER_NAME, PROSPECTIVE_STATUS
from research.am_expanded_entry_risk_integration.ensemble import joint_map
from research.canonical_entry_performance_rebase.analyze import session_of

INTEGRITY_ZERO = (
    "STRESS_REFIT_N",
    "PM_ROWS_USED_N",
    "FUTURE_DATA_USED_N",
    "FORBIDDEN_INPUT_N",
    "WRITE_OVERLAP_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "CURRENT_ENTRY_MISMATCH_N",
    "CURRENT_FILL_LOST_N",
    "CURRENT_EXIT_MISMATCH_N",
    "CURRENT_PNL_MISMATCH_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _slim_pack(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "pin": kv_rows(report.get("pin") or {}),
        "current": kv_rows(_slim_pack(report.get("current") or {})),
        "augment": kv_rows(report.get("augment") or {}),
        "overlay": kv_rows(report.get("overlay") or {}),
        "paired": kv_rows({k: v for k, v in (report.get("paired") or {}).items() if k not in ("daily", "deltas")}),
        "daily_pnl": list((report.get("paired") or {}).get("daily") or [{"empty": True}]),
        "concentration": kv_rows(report.get("concentration") or {}),
        "preservation": kv_rows(report.get("preservation") or {}),
        "integrity": kv_rows(report.get("integrity_gates") or {}),
        "economic_gates": kv_rows((report.get("economic_gates") or {}) if isinstance(report.get("economic_gates"), dict) else {}),
        "decision": kv_rows(report.get("decision") or {}),
        "safety": kv_rows(report.get("safety") or {}),
    }
    write_artifacts(report, sheets)


def _case_e(msg: str, extra: dict[str, Any] | None = None, *, pin: dict | None = None, before: dict | None = None) -> int:
    decision = decide(integrity_ok=False, preservation_ok=False, gates={}, econ={})
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "LABEL": LABEL,
        "STRESS_DAYS": list(STRESS_DAYS),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "C0_SPEC_SHA256": (pin or {}).get("C0_SPEC_SHA256") or EXPECTED_C0_SPEC_SHA256,
        "STRESS_REFIT_N": int((extra or {}).get("STRESS_REFIT_N") or 0),
        "pin": pin or {},
        "STOP_REASON": msg,
        "required": {"VERDICT": decision["VERDICT"], "NEXT": decision["NEXT"], "CASE": "E"},
        "decision": decision,
        "integrity_gates": {"PASS": False, "STOP_REASON": msg},
        "economic_gates": {},
        "current": {},
        "augment": {},
        "overlay": {},
        "paired": {},
        "preservation": {},
        "concentration": {},
        "safety": {
            "SUBMIT_N": 0,
            "CANCEL_N": 0,
            "LIVE_ORDER_N": 0,
            "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        },
        **(extra or {}),
    }
    if before is not None:
        report["isolation_before"] = before
    _publish(report)
    print(msg, flush=True)
    print(f"VERDICT {decision['VERDICT']}", flush=True)
    return 2


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM C0 REUSED-HISTORY STRESS V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"LABEL {LABEL} TRUE_OOS={TRUE_OOS} CERTIFIED={CERTIFIED}", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        return _case_e("STOP. Research write overlaps live Capture/Paper.", extra={"WRITE_OVERLAP_N": overlap}, before=before)

    prior = prior_search()
    if prior.get("ALREADY_EXECUTED") and prior.get("reuse"):
        print("ALREADY_EXECUTED exact spec+days. Reusing existing OUT. No re-run.", flush=True)
        reuse = dict(prior["reuse"])
        print(f"VERDICT {((reuse.get('decision') or {}).get('VERDICT'))}", flush=True)
        print("STOP.", flush=True)
        return 0
    exact_hits = [h for h in (prior.get("hits") or []) if str(h.get("ANALYSIS_ID") or "") == ANALYSIS_ID]
    if exact_hits and not prior.get("reuse"):
        print(f"ALREADY_EXECUTED at {exact_hits[0].get('path')}. No re-run.", flush=True)
        print("STOP.", flush=True)
        return 0

    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    print(f"STRESS_SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)

    pin = pin_identity()
    print(f"C0_SPEC_SHA256 {pin.get('C0_SPEC_SHA256')} parity={pin.get('C0_SPEC_SHA_PARITY')}", flush=True)
    if not pin.get("ok"):
        return _case_e(
            "STOP. AM_C0_STRESS_INTEGRITY_FAILED: " + ",".join(pin.get("blockers") or []),
            extra={"pin": pin},
            pin=pin,
            before=before,
        )

    labeled_path = REGIME_LABELED
    if not labeled_path.is_file():
        return _case_e("STOP. Development labeled_am_regime.json missing.", pin=pin, before=before)
    dev_rows = list(_load(labeled_path).get("rows") or [])
    pin["DEVELOPMENT_ROW_N"] = len(dev_rows)
    parity = development_current_parity(dev_rows)
    pin["DEVELOPMENT_CURRENT_PARITY"] = parity
    pin["DEVELOPMENT_MODEL_FROZEN"] = bool(
        pin.get("B0_B1_OOF_CACHE_N") == int(REPRESENTATION_N) * 2 and parity.get("ok")
    )
    if not parity.get("ok"):
        return _case_e("STOP. Development CURRENT identity failed.", extra={"parity": parity}, pin=pin, before=before)

    c0_sha_before = pin.get("C0_DECISION_SHA256_BEFORE")
    exit_sha_before = file_sha256(EXIT_DECISION_OUT / "report.json")

    harvested = harvest_stress()
    if not harvested.get("ok"):
        return _case_e(
            f"STOP. Stress harvest failed: {harvested.get('blocker')}",
            extra={"harvest": {k: v for k, v in harvested.items() if k != "rows"}},
            pin=pin,
            before=before,
        )
    stress_rows = list(harvested.get("rows") or [])
    pm_n = sum(1 for r in stress_rows if session_of(r) != "AM")
    if pm_n:
        return _case_e("STOP. PM rows in stress labeled.", extra={"PM_ROWS_USED_N": pm_n}, pin=pin, before=before)

    CACHE.mkdir(parents=True, exist_ok=True)
    train_path = CACHE / "train_development.json"
    eval_path = CACHE / "eval_stress.json"
    train_path.write_text(json.dumps({"ok": True, "rows": dev_rows}, default=str), encoding="utf-8")
    eval_path.write_text(json.dumps({"ok": True, "rows": stress_rows}, default=str), encoding="utf-8")

    b0_scores, b0_reps, b0_leak = score_arm(A3_REUSE_ARM, train_path, eval_path, dev_rows)
    b1_scores, b1_reps, b1_leak = score_arm(A5_REUSE_ARM, train_path, eval_path, dev_rows)
    stress_refit = int(b0_leak.get("STRESS_REFIT_N") or 0) + int(b1_leak.get("STRESS_REFIT_N") or 0)
    if b0_leak.get("blockers") or b1_leak.get("blockers") or stress_refit:
        return _case_e(
            "STOP. B0/B1 stress scoring integrity failed.",
            extra={"b0_leak": b0_leak, "b1_leak": b1_leak, "STRESS_REFIT_N": stress_refit},
            pin=pin,
            before=before,
        )
    if len(b0_scores) != int(REPRESENTATION_N) or len(b1_scores) != int(REPRESENTATION_N):
        return _case_e("STOP. Missing B0/B1 representation scores.", pin=pin, before=before)

    b0_tagged = attach_ensemble(stress_rows, joint_map(b0_scores), b0_reps)
    b1_tagged = attach_ensemble(stress_rows, joint_map(b1_scores), b1_reps)
    merged = merge_model_fields(b0_tagged, b1_tagged)
    dec = cohort_decisions(merged)
    tagged = tag_consensus(merged, set(dec.get("c0_keys") or set()), score_prefix="B0")
    ev = eval_c0_overlay(tagged, list(STRESS_DAYS))
    cur_pack = ev["current_pack"]
    aug_pack = ev["augment_pack"]
    ov_pack = ev["overlay_pack"]
    paired = ev["paired"]
    pres = ev["preservation"]
    conc = concentration(list(aug_pack.get("trades") or []), list(aug_pack.get("daily") or []), paired)
    econ = economic_gates(cur_pack, aug_pack, ov_pack, paired)
    after = snapshot(phase="POST")
    iso = advanced(before, after)
    c0_sha_after = file_sha256(C0_DECISION_OUT / "report.json")
    exit_sha_after = file_sha256(EXIT_DECISION_OUT / "report.json")

    leak = {
        "STRESS_REFIT_N": stress_refit,
        "PM_ROWS_USED_N": pm_n,
        "FUTURE_DATA_USED_N": 0,
        "FORBIDDEN_INPUT_N": 0,
        "WRITE_OVERLAP_N": overlap,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "CURRENT_ENTRY_MISMATCH_N": int(pres.get("CURRENT_ENTRY_MISMATCH_N") or 0),
        "CURRENT_FILL_LOST_N": int(pres.get("CURRENT_FILL_LOST_N") or 0),
        "CURRENT_EXIT_MISMATCH_N": int(pres.get("CURRENT_EXIT_MISMATCH_N") or 0),
        "CURRENT_PNL_MISMATCH_N": int(pres.get("CURRENT_PNL_MISMATCH_N") or 0),
        "C0_DEVELOPMENT_ARTIFACT_MUTATED": c0_sha_before != c0_sha_after,
        "EXIT_DEVELOPMENT_ARTIFACT_MUTATED": exit_sha_before != exit_sha_after,
    }
    integrity_ok = (
        pin.get("ok")
        and parity.get("ok")
        and all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
        and not leak["C0_DEVELOPMENT_ARTIFACT_MUTATED"]
        and not leak["EXIT_DEVELOPMENT_ARTIFACT_MUTATED"]
        and bool(iso.get("RUNTIME_PID_UNCHANGED"))
        and bool(iso.get("CAPTURE_PID_UNCHANGED"))
    )
    preservation_ok = bool(pres.get("CURRENT_PRESERVATION_PASS"))
    decision = decide(
        integrity_ok=bool(integrity_ok),
        preservation_ok=preservation_ok,
        gates=econ.get("gates") or {},
        econ=econ,
    )
    if not integrity_ok:
        decision = decide(integrity_ok=False, preservation_ok=preservation_ok, gates={}, econ=econ)

    augment = {
        "candidate_n": ev.get("AUGMENT_CANDIDATE_N"),
        "admitted_n": ev.get("AUGMENT_ADMITTED_N"),
        "fill_n": ev.get("AUGMENT_FILL_N"),
        "expired_n": ev.get("AUGMENT_EXPIRED_N"),
        "trade_n": aug_pack.get("trade_count"),
        "PnL": aug_pack.get("net_pnl_yen_100"),
        "PF": aug_pack.get("profit_factor"),
        "MaxDD": aug_pack.get("max_drawdown_yen_100"),
        "win_n": aug_pack.get("win_n"),
        "loss_n": aug_pack.get("loss_n"),
        "flat_n": aug_pack.get("flat_n"),
        "fill_rate": fill_rate(int(ev.get("AUGMENT_ADMITTED_N") or 0), int(ev.get("AUGMENT_FILL_N") or 0)),
        "daily": list(aug_pack.get("daily") or []),
    }
    overlay = {
        "trade_n": ov_pack.get("trade_count"),
        "PnL": ov_pack.get("net_pnl_yen_100"),
        "PF": ov_pack.get("profit_factor"),
        "MaxDD": ov_pack.get("max_drawdown_yen_100"),
        "positive_day_n": ov_pack.get("positive_day_n"),
        "negative_day_n": ov_pack.get("negative_day_n"),
        "DELTA_NET": econ.get("DELTA_NET"),
        "DELTA_PF": econ.get("DELTA_PF"),
        "DELTA_DD": econ.get("DELTA_DD"),
        "daily": list(ov_pack.get("daily") or []),
    }
    current = dict(cur_pack)
    current["day_n"] = len(STRESS_DAYS)
    current["trade_n"] = current.get("trade_count")
    current["PnL"] = current.get("net_pnl_yen_100")
    current["PF"] = current.get("profit_factor")
    current["MaxDD"] = current.get("max_drawdown_yen_100")

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "LABEL": LABEL,
        "STRESS_DAYS": list(STRESS_DAYS),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "ENTRY": C0,
        "ENTRY_NAME": PROSPECTIVE_CHALLENGER_NAME,
        "ENTRY_STATUS": PROSPECTIVE_STATUS,
        "C0_SPEC_SHA256": pin.get("C0_SPEC_SHA256"),
        "EXPECTED_C0_SPEC_SHA256": EXPECTED_C0_SPEC_SHA256,
        "STRESS_SPEC_SHA256": sha,
        "SOURCE_SHA256": src_sha,
        "STRESS_REFIT_N": stress_refit,
        "MIN_AUGMENT_TRADE_N": MIN_AUGMENT_TRADE_N,
        "precommit": spec,
        "pin": pin,
        "geometry": dict(dec.get("geometry") or {}),
        "current": current,
        "augment": augment,
        "overlay": overlay,
        "paired": paired,
        "concentration": conc,
        "preservation": pres,
        "integrity_gates": {
            "PASS": bool(integrity_ok),
            "C0_SPEC_SHA_PARITY": pin.get("C0_SPEC_SHA_PARITY"),
            "C0_SOURCE_REPRODUCIBLE": pin.get("C0_SOURCE_REPRODUCIBLE"),
            "C14_REPRODUCIBLE": pin.get("C14_REPRODUCIBLE"),
            "DEVELOPMENT_MODEL_FROZEN": pin.get("DEVELOPMENT_MODEL_FROZEN"),
            "DEVELOPMENT_CURRENT_PARITY": parity.get("ok"),
            "CURRENT_PRESERVATION_PASS": preservation_ok,
            **leak,
            **iso,
        },
        "economic_gates": econ.get("gates") or {},
        "economic": econ,
        "harvest_day_meta": harvested.get("day_meta"),
        "decision": decision,
        "required": {
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "NEXT": decision.get("NEXT"),
            "C0_RESEARCH_PRIORITY_MAINTAINED": decision.get("C0_RESEARCH_PRIORITY_MAINTAINED"),
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": decision.get("NEW_ENTRY_FAMILY_DESIGN_ALLOWED"),
            "SIZING_ALLOWED": False,
        },
        "safety": {
            "SUBMIT_N": 0,
            "CANCEL_N": 0,
            "LIVE_ORDER_N": 0,
            "ENTRY_RUNTIME_CHANGED": False,
            "EXIT_RUNTIME_CHANGED": False,
            "CAP_CHANGED": False,
            "MODEL_CHANGED": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
            "TRUE_OOS": TRUE_OOS,
            "CERTIFIED": CERTIFIED,
        },
        "isolation_before": before,
        "isolation_after": after,
    }
    _publish(report)
    print(
        f"CURRENT n={current.get('trade_n')} net={current.get('PnL')} pf={current.get('PF')} dd={current.get('MaxDD')}",
        flush=True,
    )
    print(
        f"C0 aug n={augment.get('trade_n')} net={augment.get('PnL')} pf={augment.get('PF')} "
        f"cand={augment.get('candidate_n')} adm={augment.get('admitted_n')} fill={augment.get('fill_n')}",
        flush=True,
    )
    print(
        f"OVERLAY n={overlay.get('trade_n')} net={overlay.get('PnL')} pf={overlay.get('PF')} "
        f"delta={overlay.get('DELTA_NET')} paired_pos={paired.get('PAIRED_POS_DAYS')} neg={paired.get('PAIRED_NEG_DAYS')}",
        flush=True,
    )
    print(f"PRESERVATION {preservation_ok} INTEGRITY {integrity_ok}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
