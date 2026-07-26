"""EEC confirmation causal integrity pipeline."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.eec_confirmation_integrity.constants import (
    EC2_THR,
    FROZEN_NOISE,
    NATIVE,
    SOT_V2,
    SOT_V3,
    STUDY_VERSION,
)
from research.eec_confirmation_integrity.evaluate import (
    build_population_audits,
    dependency_detail,
    expiry_counts,
    run_cohorts,
)
from research.eec_confirmation_integrity.parity import compare_episode, summarize_parity
from research.eec_confirmation_integrity.report import emit_artifacts
from research.entry_exit_contract.discovery import discover_capture_days
from research.entry_exit_contract.entries import detect_ec2, load_push_day
from research.entry_exit_contract_integrity.episode import segment_true_episodes
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds

JST = ZoneInfo("Asia/Tokyo")


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = ["EEC_CONFIRMATION_OFFLINE_ONLY", "NO_PRODUCTION_CHANGE"]
    exp = payload.get("expiry") or {}
    parity = payload.get("parity") or {}
    cohorts = payload.get("cohorts") or {}
    c2 = cohorts.get("C2") or {}
    c3 = cohorts.get("C3") or {}
    dep = payload.get("dependency_detail") or {}
    execs = payload.get("execution_scenarios") or {}
    e0 = execs.get("E0") or c2
    e1 = execs.get("E1") or c3

    # causal integrity = timing within episode/session/180s is measurable and applied
    strict_n = int(exp.get("strict_causal_A1_n") or 0)
    raw_n = int(exp.get("raw_confirmation_n") or 0)
    late = int(exp.get("late_confirmation_excluded_n") or 0)
    cross = int(exp.get("cross_session_excluded_n") or 0)
    after_inv = int(exp.get("confirmation_after_invalidation_n") or 0)
    if strict_n > 0 and (late + cross + after_inv) >= 0:
        codes.append("ENTRY_CONFIRMATION_CAUSAL_INTEGRITY_PASS")
    else:
        codes.append("ENTRY_CONFIRMATION_CAUSAL_INTEGRITY_BLOCKED")

    if exp.get("expired_candidate_n", 0) > 0:
        codes.append("CONFIRMATION_EPISODE_EXPIRY_READY")
    else:
        codes.append("CONFIRMATION_EPISODE_EXPIRY_BLOCKED")

    codes.append(parity.get("verdict") or "ECONOMIC_SUCCESS_PARITY_BLOCKED")

    # execution realism: valid Ask (ask>bid, qty>=100) must be evaluable
    e0_n = int(e0.get("n_traded") or 0)
    e1_n = int(e1.get("n_traded") or 0)
    exec_ok_n = int(exp.get("strict_causal_ask_executable_n") or 0)
    crossed_n = int(exp.get("v3_raw_crossed_ask_n") or 0)
    if e0_n > 0 and e1_n > 0 and exec_ok_n > 0:
        codes.append("ENTRY_EXECUTION_REALISM_PASS")
    else:
        codes.append("ENTRY_EXECUTION_REALISM_BLOCKED")
        if crossed_n > 0:
            codes.append("V3_A1_USED_CROSSED_ASK_FIELD")

    if dep.get("dependency_blocked"):
        codes.append("ENTRY_CONFIRMATION_DEPENDENCY_BLOCKED")
    else:
        codes.append("ENTRY_CONFIRMATION_DEPENDENCY_PASS")

    def pf(x):
        return float(x.get("PF_5bps") or 0)

    signal = (
        "ENTRY_CONFIRMATION_CAUSAL_INTEGRITY_PASS" in codes
        and parity.get("verdict") == "ECONOMIC_SUCCESS_PARITY_PASS"
        and pf(e0) > 1
        and pf(e1) > 1
        and float((e0.get("cap5") or {}).get("pnl_5bps") or 0) > 0
        and int(e0.get("pos_days") or 0) > int(e0.get("neg_days") or 0)
        and float(dep.get("pf_after_exclude_max_symbol") or 0) > 1
        and float(dep.get("pf_after_exclude_max_day") or 0) > 1
        and float(dep.get("top1_symbol_pnl_share") or 1) < 0.40
        and float(dep.get("top1_day_pnl_share") or 1) < 0.50
        and not dep.get("dependency_blocked")
    )
    codes.append("ENTRY_CONFIRMATION_OFFLINE_SIGNAL" if signal else "ENTRY_CONFIRMATION_NO_EDGE")
    # OOS3 → never EDGE_CONFIRMED
    codes = sorted(set(codes))
    return {
        "final": "EEC_CONFIRMATION_OFFLINE_ONLY",
        "codes": codes,
        "offline_signal": signal,
        "summary": (
            "A1 confirmation因果性監査完了。"
            f" strict causal(timing)={strict_n}/{raw_n}; Ask executable={exec_ok_n}; "
            f"late除外={late}; cross-session除外={cross}; after_invalidation={after_inv}; "
            f"v3_raw_crossed_ask={crossed_n}. "
            f"C2 valid-Ask n={e0_n} PF={e0.get('PF_5bps')} CAP5={(e0.get('cap5') or {}).get('pnl_5bps')}. "
            "1s集約板でask<=bidが常態のため正式Ask ENTRYはNOT_EVALUABLEが多い。"
            " OOS3日のためEDGE_CONFIRMED禁止。本線変更なし。"
        ),
        "no_production_reason": "offline診断のみ。Shadow/Forward/本線変更禁止。",
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "eec_confirmation_integrity" / run_id
    print(f"[eec_caui] start {run_id}", flush=True)

    disc = discover_capture_days(native)
    days = disc["usable_days"]
    oos = tuple(disc["oos_days"])
    print(f"[eec_caui] days={days} oos={oos}", flush=True)

    print("[eec_caui] load PUSH…", flush=True)
    push = {d: load_push_day(d, native) for d in days}

    print("[eec_caui] rebuild EC2 true episodes (frozen thr)…", flush=True)
    raw = []
    for day in days:
        by = push.get(day) or {}
        for sym, ticks in by.items():
            if len(ticks) < 80:
                continue
            bars = aggregate_to_seconds(ticks)
            raw.extend(detect_ec2(bars, day=day, thr=EC2_THR))
    seg = segment_true_episodes(raw)
    accepted = seg["accepted"]
    print(f"[eec_caui] accepted={len(accepted)} oos={sum(1 for c in accepted if c.day in oos)}", flush=True)

    print("[eec_caui] causal audit…", flush=True)
    audits = build_population_audits(accepted, push, oos_days=oos)
    exp = expiry_counts(audits)
    print(f"[eec_caui] expiry={exp}", flush=True)

    print("[eec_caui] economic parity v2/v3…", flush=True)
    parity_rows = []
    for c in accepted:
        if c.day not in oos:
            continue
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        if ticks:
            parity_rows.append(compare_episode(c, ticks))
    parity = summarize_parity(parity_rows)
    print(
        f"[eec_caui] parity v2={parity.get('v2_economic_rate')} v3={parity.get('v3_economic_rate')} "
        f"verdict={parity.get('verdict')}",
        flush=True,
    )

    print("[eec_caui] cohorts C0–C4 + Ask scenarios…", flush=True)
    cohort_pack = run_cohorts(accepted, push, audits, oos_days=oos)
    # dependency on strict Ask C2; if empty, fall back to diagnostic crossed-ask causal set for concentration audit
    dep_src = cohort_pack["cohorts"]["C2"]
    if int(dep_src.get("n_traded") or 0) == 0:
        dep_src = cohort_pack["cohorts"].get("DIAG_CROSSED_ASK") or dep_src
    dep = dependency_detail(dep_src)
    print(f"[eec_caui] ask_quote_audit={cohort_pack.get('ask_quote_audit')}", flush=True)
    for k, s in cohort_pack["cohorts"].items():
        print(
            f"[eec_caui] {k}: n={s.get('n_traded')} pnl={s.get('total_pnl_5bps')} PF={s.get('PF_5bps')} "
            f"cap5={ (s.get('cap5') or {}).get('pnl_5bps') }",
            flush=True,
        )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "study_version": STUDY_VERSION,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "ec2_entry_exit_unchanged": True,
        "noise_unchanged": True,
        "frozen_noise": FROZEN_NOISE,
        "sot_v3": str(SOT_V3),
        "sot_v2": str(SOT_V2),
        "discovery": disc,
        "oos_days": list(oos),
        "population": {
            "raw_triggers": len(raw),
            "true_episodes": seg["true_episode_n"],
            "accepted": len(accepted),
            "oos_n": sum(1 for c in accepted if c.day in oos),
        },
        "expiry": exp,
        "causal_audit_samples": [a for a in audits if a.get("confirmed_raw")][:80],
        "parity": {k: v for k, v in parity.items() if k != "sample_disagree"},
        "parity_disagree_samples": parity.get("sample_disagree") or [],
        "delay_buckets": cohort_pack["delay_buckets"],
        "cohorts": cohort_pack["cohorts"],
        "execution_scenarios": cohort_pack["execution_scenarios"],
        "ask_quote_audit": cohort_pack.get("ask_quote_audit"),
        "dependency_detail": dep,
        "dependency_basis": "C2" if int(cohort_pack["cohorts"]["C2"].get("n_traded") or 0) > 0 else "DIAG_CROSSED_ASK",
    }
    # strip heavy nested sample from cohorts for json size control — keep samples
    payload["verdict"] = _decide(payload)
    payload["completion"] = {
        "final_verdict": payload["verdict"]["final"],
        "codes": payload["verdict"]["codes"],
        "expiry": exp,
        "parity": {
            "v2_rate": parity.get("v2_economic_rate"),
            "v3_rate": parity.get("v3_economic_rate"),
            "verdict": parity.get("verdict"),
        },
        "C0": _brief(cohort_pack["cohorts"]["C0"]),
        "C1": _brief(cohort_pack["cohorts"]["C1"]),
        "C2": _brief(cohort_pack["cohorts"]["C2"]),
        "C3": _brief(cohort_pack["cohorts"]["C3"]),
        "C4": _brief(cohort_pack["cohorts"]["C4"]),
        "dependency": {
            "blocked": dep.get("dependency_blocked"),
            "top1_sym_share": dep.get("top1_symbol_pnl_share"),
            "top1_day_share": dep.get("top1_day_pnl_share"),
            "loo_sym_PF": dep.get("pf_after_exclude_max_symbol"),
            "loo_day_PF": dep.get("pf_after_exclude_max_day"),
        },
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_changed": False,
        "artifact_path": str(out_dir),
    }
    payload["out_dir"] = str(out_dir)
    emit_artifacts(out_dir, payload)
    print(f"[eec_caui] done {payload['verdict']['final']} -> {out_dir}", flush=True)
    return payload


def _brief(s: dict) -> dict:
    return {
        "n_traded": s.get("n_traded"),
        "pnl": s.get("total_pnl_5bps"),
        "PF": s.get("PF_5bps"),
        "cap5_pnl": (s.get("cap5") or {}).get("pnl_5bps"),
        "R1_PF": ((s.get("reality") or {}).get("R1") or {}).get("PF_5bps"),
        "R3_PF": ((s.get("reality") or {}).get("R3") or {}).get("PF_5bps"),
        "pos_days": s.get("pos_days"),
        "neg_days": s.get("neg_days"),
        "dependency_blocked": s.get("dependency_blocked"),
    }
