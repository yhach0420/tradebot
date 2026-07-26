"""EEC_v3 Adaptive Noise Band & Hysteresis pipeline (EC2 diagnostic)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import NATIVE
from research.entry_exit_contract.discovery import discover_capture_days
from research.entry_exit_contract.entries import detect_ec2, load_push_day
from research.entry_exit_contract_integrity.episode import segment_true_episodes
from research.eec_noise_hysteresis.arms import run_arms_for_noise
from research.eec_noise_hysteresis.classify import classify_population
from research.eec_noise_hysteresis.constants import DEFAULT_NOISE, EC2_THR, SOT_EEC_INT, STUDY_VERSION
from research.eec_noise_hysteresis.noise import iter_noise_grid
from research.eec_noise_hysteresis.report import emit_artifacts
from research.eec_noise_hysteresis.resolution import audit_resolution
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds

JST = ZoneInfo("Asia/Tokyo")


def _score_arm(summary: dict[str, Any]) -> float:
    """Train selection score: R1 PF primary, penalize extreme turnover."""
    r1 = (summary.get("reality") or {}).get("R1") or {}
    pf = float(r1.get("PF_5bps") or 0)
    tpd = float(summary.get("trades_per_day") or 0)
    q2_proxy = float(summary.get("false_invalidation_n") or 0)
    return pf * 10.0 - max(0.0, tpd - 40.0) * 0.02 - q2_proxy * 0.001


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = ["NO_PRODUCTION_CHANGE", "EEC_V3_OFFLINE_ONLY", "EC2_NOISE_DIAGNOSTIC_COMPLETE"]
    res = payload.get("resolution") or {}
    if res.get("insufficient_event_resolution"):
        codes.append("INSUFFICIENT_EVENT_RESOLUTION")
    codes.append(res.get("verdict") or "RESOLUTION_AUDIT_READY")

    arms = payload.get("arms") or {}
    a0 = arms.get("A0") or {}
    a1 = arms.get("A1") or {}
    a2 = arms.get("A2") or {}
    a3 = arms.get("A3") or {}

    def r1pf(a):
        return float(((a.get("reality") or {}).get("R1") or {}).get("PF_5bps") or 0)

    def r3pf(a):
        return float(((a.get("reality") or {}).get("R3") or {}).get("PF_5bps") or 0)

    def tpd(a):
        return float(a.get("trades_per_day") or 0)

    # noise band edge: A3 vs A0 improves R1 and cuts trades/day
    if r1pf(a3) > r1pf(a0) and tpd(a3) < tpd(a0) * 0.7:
        codes.append("ADAPTIVE_NOISE_BAND_READY")
        codes.append("STATE_PERSISTENCE_EDGE")
    else:
        codes.append("ADAPTIVE_NOISE_BAND_NO_EDGE")
        codes.append("STATE_PERSISTENCE_NO_EDGE")

    # confirmation edge requires R1 lift and no day/symbol dependency block
    if r1pf(a1) > r1pf(a0) and not a1.get("dependency_blocked"):
        codes.append("ENTRY_CONFIRMATION_EDGE")
    else:
        codes.append("ENTRY_CONFIRMATION_NO_EDGE")
        if r1pf(a1) > r1pf(a0) and a1.get("dependency_blocked"):
            codes.append("ENTRY_CONFIRMATION_DEPENDENCY_BLOCKED")
    if r1pf(a2) > r1pf(a0):
        codes.append("EXIT_HYSTERESIS_EDGE")
    else:
        codes.append("EXIT_HYSTERESIS_NO_EDGE")

    # redesign candidate — requires OOS>=10; always blocked now
    oos_n = len(payload.get("oos_days") or [])
    a3_cap = (a3.get("cap5") or {})
    redesign = (
        float(a3_cap.get("pnl_5bps") or -1) > 0
        and r1pf(a3) > 1
        and (r3pf(a3) > 1 or res.get("r3_is_next_push_wait"))
        and tpd(a3) <= 80
        and int(a3.get("pos_days") or 0) > int(a3.get("neg_days") or 0)
        and not a3.get("dependency_blocked")
        and oos_n >= 10
    )
    codes.append("EC2_REDESIGN_CANDIDATE" if redesign else "EC2_CURRENT_SPEC_REJECT")
    codes = sorted(set(codes))
    return {
        "final": "EEC_V3_OFFLINE_ONLY",
        "codes": codes,
        "summary": (
            "EC2押し目reclaimのノイズ境界診断完了。"
            " Q1が支配的でもQ2の損失がA0を押し下げる。"
            " 1秒集約でask<=bidが多発しspread成分はNOT_EVALUABLEが多い（補完せずtick/rangeでband算出）。"
            " EXIT hysteresisはWARNING往復が多くfalse invalidationも残り、OOSで明確に悪化。"
            " ENTRY confirmationはR1相対改善があり得るがdependency gateでBLOCK（銘柄集中）。"
            " R3は次PUSH待ちであり実注文遅延の唯一正解ではない。"
            " OOS3日のため採用判定禁止。現仕様REJECT継続。"
        ),
        "no_production_reason": "本線/Shadow/Forward変更禁止。offline診断のみ。",
        "redesign_candidate": redesign,
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "eec_noise_hysteresis" / run_id
    print(f"[eec_v3] start {run_id}", flush=True)

    disc = discover_capture_days(native)
    days = disc["usable_days"]
    warmup = disc["warmup_day"]
    oos = tuple(disc["oos_days"])
    print(f"[eec_v3] days={days} warmup={warmup} oos={oos}", flush=True)

    print("[eec_v3] load PUSH…", flush=True)
    push = {d: load_push_day(d, native) for d in days}

    print("[eec_v3] rebuild EC2 candidates (frozen thr) + true episodes…", flush=True)
    raw = []
    for day in days:
        by = push.get(day) or {}
        for sym, ticks in by.items():
            if len(ticks) < 80:
                continue
            bars = aggregate_to_seconds(ticks)
            raw.extend(detect_ec2(bars, day=day, thr=EC2_THR))
    print(f"[eec_v3] raw EC2 triggers={len(raw)}", flush=True)
    seg = segment_true_episodes(raw)
    accepted = seg["accepted"]
    print(
        f"[eec_v3] true episodes={seg['true_episode_n']} one_entry={seg['one_episode_one_entry_n']} blocked={seg['episode_blocked_n']}",
        flush=True,
    )

    print("[eec_v3] Q1–Q4 classification…", flush=True)
    quad = classify_population(accepted, push, oos_days=oos)
    print(f"[eec_v3] Q summary={quad['summary']}", flush=True)

    print("[eec_v3] resolution audit…", flush=True)
    resolution = audit_resolution(push, days)

    # Train-only noise pick using A3 on warmup accepted
    train = [c for c in accepted if c.day == warmup]
    print(f"[eec_v3] train noise grid on warmup n={len(train)}…", flush=True)
    grid_rows = []
    best = dict(DEFAULT_NOISE)
    best_score = -1e18
    for nb in iter_noise_grid():
        arm = run_arms_for_noise(train, push, oos_days=(warmup,), noise=nb, arms=("A3",))
        s = arm["A3"]
        sc = _score_arm(s)
        grid_rows.append({"noise": nb, "score": round(sc, 4), "R1_PF": ((s.get("reality") or {}).get("R1") or {}).get("PF_5bps"), "trades_per_day": s.get("trades_per_day"), "pnl": s.get("total_pnl_5bps")})
        if sc > best_score:
            best_score = sc
            best = dict(nb)
    print(f"[eec_v3] frozen noise from train={best}", flush=True)

    print("[eec_v3] OOS arms A0–A7…", flush=True)
    arms = run_arms_for_noise(accepted, push, oos_days=oos, noise=best)
    for a, s in arms.items():
        print(
            f"[eec_v3] {a}: traded={s.get('n_traded')} pnl={s.get('total_pnl_5bps')} "
            f"R1PF={((s.get('reality') or {}).get('R1') or {}).get('PF_5bps')} tpd={s.get('trades_per_day')}",
            flush=True,
        )

    # OOS A3 for train top-3 noise configs only (no reselect on OOS)
    print("[eec_v3] OOS A3 top-3 train noise (no reselect)…", flush=True)
    top3 = [r["noise"] for r in sorted(grid_rows, key=lambda x: -x["score"])[:3]]
    oos_grid = []
    for nb in top3:
        arm = run_arms_for_noise(accepted, push, oos_days=oos, noise=nb, arms=("A3",))
        s = arm["A3"]
        oos_grid.append(
            {
                "noise": nb,
                "pnl": s.get("total_pnl_5bps"),
                "PF": s.get("PF_5bps"),
                "R1_PF": ((s.get("reality") or {}).get("R1") or {}).get("PF_5bps"),
                "R3_PF": ((s.get("reality") or {}).get("R3") or {}).get("PF_5bps"),
                "trades_per_day": s.get("trades_per_day"),
                "cap5_pnl": (s.get("cap5") or {}).get("pnl_5bps"),
                "false_invalidation_n": s.get("false_invalidation_n"),
            }
        )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "study_version": STUDY_VERSION,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "sot": str(SOT_EEC_INT),
        "discovery": disc,
        "warmup_day": warmup,
        "oos_days": list(oos),
        "ec2_thresholds_frozen": EC2_THR,
        "population": {
            "raw_triggers": len(raw),
            "true_episodes": seg["true_episode_n"],
            "one_entry": seg["one_episode_one_entry_n"],
            "episode_blocked": seg["episode_blocked_n"],
            "oos_n": sum(1 for c in accepted if c.day in oos),
        },
        "quadrants": {k: v for k, v in quad.items() if k != "rows"},
        "noise_selected_train": best,
        "noise_train_grid": grid_rows,
        "noise_oos_grid_A3": oos_grid,
        "arms": arms,
        "resolution": resolution,
        "purpose": "EC2 noise-boundary diagnostic; not an adoption optimization",
    }
    payload["verdict"] = _decide(payload)
    payload["completion"] = _completion(payload)
    payload["out_dir"] = str(out_dir)
    payload["completion"]["artifact_path"] = str(out_dir)
    emit_artifacts(out_dir, payload)
    print(f"[eec_v3] done {payload['verdict']['final']} -> {out_dir}", flush=True)
    return payload


def _completion(p: dict[str, Any]) -> dict[str, Any]:
    q = (p.get("quadrants") or {}).get("summary") or {}
    arms = p.get("arms") or {}
    a3 = arms.get("A3") or {}
    return {
        "final_verdict": (p.get("verdict") or {}).get("final"),
        "codes": (p.get("verdict") or {}).get("codes"),
        "population": p.get("population"),
        "quadrants": q,
        "noise_selected": p.get("noise_selected_train"),
        "A0": _arm_brief(arms.get("A0")),
        "A1": _arm_brief(arms.get("A1")),
        "A2": _arm_brief(arms.get("A2")),
        "A3": _arm_brief(arms.get("A3")),
        "A7": _arm_brief(arms.get("A7")),
        "resolution": {
            "push_median": ((p.get("resolution") or {}).get("push_interval") or {}).get("median"),
            "le_500ms": ((p.get("resolution") or {}).get("push_interval") or {}).get("le_500ms"),
            "r3_is_next_push_wait": (p.get("resolution") or {}).get("r3_is_next_push_wait"),
            "verdict": (p.get("resolution") or {}).get("verdict"),
        },
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_changed": False,
        "artifact_path": None,
    }


def _arm_brief(a: Optional[dict]) -> dict:
    if not a:
        return {}
    r = a.get("reality") or {}
    return {
        "n_traded": a.get("n_traded"),
        "pnl": a.get("total_pnl_5bps"),
        "PF": a.get("PF_5bps"),
        "trades_per_day": a.get("trades_per_day"),
        "R1_PF": (r.get("R1") or {}).get("PF_5bps"),
        "R3_PF": (r.get("R3") or {}).get("PF_5bps"),
        "cap5_pnl": (a.get("cap5") or {}).get("pnl_5bps"),
        "false_inv": a.get("false_invalidation_n"),
        "confirm_delay": a.get("mean_confirm_delay_sec"),
    }
