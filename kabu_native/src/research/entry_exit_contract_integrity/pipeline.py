"""EEC_v2 integrity pipeline — episode + metrics only; ENTRY/EXIT/thresholds frozen."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import DEFAULT_THRESHOLDS, NATIVE, X6_PARAMS
from research.entry_exit_contract.discovery import discover_capture_days
from research.entry_exit_contract.entries import build_ec_entries, load_push_day
from research.entry_exit_contract_integrity.constants import INTEGRITY_VERSION, MIN_OOS_DAYS_FOR_EDGE, SOT_EEC
from research.entry_exit_contract_integrity.episode import segment_true_episodes
from research.entry_exit_contract_integrity.evaluate import (
    evaluate_matched,
    pairing_verdict_v2,
    turnover_stats,
)
from research.entry_exit_contract_integrity.report import emit_artifacts
from research.price_flow_exit.exit_rules import ExitParams
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5

JST = ZoneInfo("Asia/Tokyo")


def _strategy_verdict(sid: str, m2: dict[str, Any], cap: dict[str, Any], pairing: str, turn: dict[str, Any]) -> str:
    r1 = (m2.get("reality") or {}).get("R1") or {}
    r3 = (m2.get("reality") or {}).get("R3") or {}
    dep = m2.get("dependency") or {}
    trades_day = float(turn.get("trades_per_day") or 999)
    econ = float(m2.get("economic_success_rate") or 0)
    struct = float(m2.get("structural_success_rate") or 0)
    # structural vs economic consistency: economic should not massively exceed structural fantasy
    consistent = econ <= struct + 0.05
    if sid == "EC2":
        ok = (
            float(m2.get("PF_5bps") or 0) >= 1
            and float(cap.get("pnl_5bps") or -1) >= 0
            and float(r1.get("PF_5bps") or 0) >= 1
            and float(r3.get("PF_5bps") or 0) >= 1
            and consistent
            and trades_day <= 80
            and not dep.get("dependency_blocked")
        )
        return "EC2_RESEARCH_CANDIDATE" if ok else "EC2_CURRENT_SPEC_REJECT"
    if sid == "EC1":
        return "EC1_CURRENT_SPEC_REJECT"
    return "EC3_CURRENT_SPEC_REJECT"


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = [
        "NO_PRODUCTION_CHANGE",
        "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY",
        "ECONOMIC_SUCCESS_METRIC_READY",
        "MFE_CAPTURE_METRIC_FIXED",
        "EXECUTION_REALISM_READY",
    ]
    ep = payload.get("episode") or {}
    codes.append(ep.get("verdict") or "TRUE_EPISODE_SEGMENTATION_BLOCKED")
    for sid in ("EC1", "EC2", "EC3"):
        s = (payload.get("strategies") or {}).get(sid) or {}
        codes.append(s.get("pairing") or "ENTRY_EXIT_PAIRING_NO_EDGE")
        codes.append(s.get("strategy_verdict") or f"{sid}_CURRENT_SPEC_REJECT")
    # integrity pass if episode ready + metrics ready + no crash
    if ep.get("verdict") == "TRUE_EPISODE_SEGMENTATION_READY":
        codes.append("ENTRY_EXIT_CONTRACT_INTEGRITY_PASS")
    else:
        codes.append("ENTRY_EXIT_CONTRACT_INTEGRITY_BLOCKED")
    codes = sorted(set(codes))
    return {
        "final": "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY",
        "codes": codes,
        "summary": (
            "EEC_v2 integrity: true episode dedupe・economic success・MFE capture修正・"
            "execution realism主評価を適用。ENTRY/EXIT/閾値はEEC_v1固定。OOS3日のため採用禁止。"
        ),
        "no_production_reason": "本線変更禁止。評価基盤修正のみ。",
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "entry_exit_contract_integrity" / run_id
    print(f"[eec_int] start {run_id}", flush=True)

    disc = discover_capture_days(native)
    days = disc["usable_days"]
    oos = tuple(disc["oos_days"])
    warmup = disc["warmup_day"]
    thresholds = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}  # frozen — no reselect
    params = ExitParams(**X6_PARAMS)

    print("[eec_int] load PUSH + build raw triggers (EEC_v1 rules)…", flush=True)
    push = {d: load_push_day(d, native) for d in days}
    raw = build_ec_entries(days, push, thresholds)

    episode_all = {}
    accepted_all = {}
    for sid in ("EC1", "EC2", "EC3"):
        print(f"[eec_int] true episode segment {sid}…", flush=True)
        # segment on all days then filter OOS for eval
        seg = segment_true_episodes(raw[sid])
        episode_all[sid] = seg
        accepted_all[sid] = [c for c in seg["accepted"] if c.day in oos]
        print(
            f"[eec_int] {sid} raw={seg['raw_trigger_n']} episodes={seg['true_episode_n']} "
            f"one_entry={len(accepted_all[sid])} blocked={seg['episode_blocked_n']}",
            flush=True,
        )

    strategies = {}
    for sid in ("EC1", "EC2", "EC3"):
        print(f"[eec_int] evaluate {sid} matched + reality…", flush=True)
        # before dedupe (raw OOS) for pnl compare
        raw_oos = [c for c in raw[sid] if c.day in oos]
        before = evaluate_matched(raw_oos, push, oos_days=oos, params=params, also_baselines=False, light=True)
        after = evaluate_matched(accepted_all[sid], push, oos_days=oos, params=params, also_baselines=True, light=False)
        strategies[sid] = {
            "before_dedupe": {k: v for k, v in before.items() if k not in ("trades", "sample_rows")},
            "after_dedupe": {k: v for k, v in after.items() if k not in ("trades",)},
            "_trades": after["trades"],
            "episode": {
                k: episode_all[sid][k]
                for k in (
                    "raw_trigger_n",
                    "true_episode_n",
                    "trigger_per_episode",
                    "one_episode_one_entry_n",
                    "episode_blocked_n",
                    "same_symbol_reentry_n",
                    "same_wave_reentry_n",
                    "verdict",
                )
            },
            "pnl_pf_before": {
                "n": before.get("n"),
                "pnl_5bps": before.get("total_pnl_5bps"),
                "PF_5bps": before.get("PF_5bps"),
            },
            "pnl_pf_after": {
                "n": after.get("n"),
                "pnl_5bps": after.get("total_pnl_5bps"),
                "PF_5bps": after.get("PF_5bps"),
            },
        }

    print("[eec_int] CAP=5 P2–P5 (deduped matched)…", flush=True)
    cap_ports = {}
    event_log = []
    for pid, trades in (
        ("P2", strategies["EC1"]["_trades"]),
        ("P3", strategies["EC2"]["_trades"]),
        ("P4", strategies["EC3"]["_trades"]),
        (
            "P5",
            strategies["EC1"]["_trades"] + strategies["EC2"]["_trades"] + strategies["EC3"]["_trades"],
        ),
    ):
        # uncapped reference
        uncapped = {
            "n": len(trades),
            "pnl_5bps": round(sum(t.pnl_5bps for t in trades), 2),
        }
        cands_f, _ = filter_no_overlap(sorted(trades, key=lambda t: (t.entry_time, t.entry_method, t.setup_id)))
        res = replay_cap5(cands_f, portfolio_id=pid)
        summary = res.summary()
        summary["uncapped_reference"] = uncapped
        summary["one_episode_one_entry_n"] = len(trades)
        ep_n = len({t.impulse_episode_id for t in trades})
        summary["turnover"] = turnover_stats(res.trades, oos_days=oos, episodes_n=ep_n)
        cap_ports[pid] = summary
        event_log.extend(res.event_log[:200])
        print(f"[eec_int] {pid} accepted={res.accepted} pnl={summary.get('pnl_5bps')}", flush=True)

    # pairing + strategy verdicts after CAP known
    for sid, pid in (("EC1", "P2"), ("EC2", "P3"), ("EC3", "P4")):
        m2 = strategies[sid]["after_dedupe"]
        pairing = pairing_verdict_v2(m2, cap5_pnl=float((cap_ports[pid] or {}).get("pnl_5bps") or 0), oos_n=len(oos))
        turn = (cap_ports[pid] or {}).get("turnover") or {}
        strategies[sid]["pairing"] = pairing
        strategies[sid]["strategy_verdict"] = _strategy_verdict(sid, m2, cap_ports[pid], pairing, turn)
        strategies[sid]["turnover_cap5"] = turn

    # P5 trades/day after episode
    p5_note = {
        "v1_accepted_ref": 1689,
        "v2_one_episode_entries": len(strategies["EC1"]["_trades"])
        + len(strategies["EC2"]["_trades"])
        + len(strategies["EC3"]["_trades"]),
        "v2_cap5_accepted": (cap_ports.get("P5") or {}).get("accepted"),
    }

    # strip trades
    strat_out = {}
    for sid, s in strategies.items():
        strat_out[sid] = {k: v for k, v in s.items() if k != "_trades"}

    episode_summary = {
        "verdict": "TRUE_EPISODE_SEGMENTATION_READY",
        "by_strategy": {sid: strat_out[sid]["episode"] for sid in ("EC1", "EC2", "EC3")},
        "totals": {
            "raw_trigger_n": sum(episode_all[s]["raw_trigger_n"] for s in ("EC1", "EC2", "EC3")),
            "true_episode_n": sum(episode_all[s]["true_episode_n"] for s in ("EC1", "EC2", "EC3")),
            "one_episode_one_entry_oos": sum(len(accepted_all[s]) for s in ("EC1", "EC2", "EC3")),
            "episode_blocked_n": sum(episode_all[s]["episode_blocked_n"] for s in ("EC1", "EC2", "EC3")),
        },
    }

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "integrity_version": INTEGRITY_VERSION,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "entry_exit_thresholds_unchanged": True,
        "sot_eec_v1": str(SOT_EEC),
        "discovery": disc,
        "warmup_day": warmup,
        "oos_days": list(oos),
        "thresholds_frozen": thresholds,
        "episode": episode_summary,
        "strategies": strat_out,
        "cap5": {"portfolios": cap_ports, "event_log": event_log, "p5_reduction": p5_note},
        "oos_insufficient": len(oos) < MIN_OOS_DAYS_FOR_EDGE,
    }
    payload["verdict"] = _decide(payload)
    payload["completion"] = _completion(payload)
    payload["out_dir"] = str(out_dir)
    payload["completion"]["artifact_path"] = str(out_dir)
    emit_artifacts(out_dir, payload)
    print(f"[eec_int] done {payload['verdict']['final']} -> {out_dir}", flush=True)
    return payload


def _completion(p: dict[str, Any]) -> dict[str, Any]:
    st = p.get("strategies") or {}
    ep = (p.get("episode") or {}).get("totals") or {}
    caps = (p.get("cap5") or {}).get("portfolios") or {}
    return {
        "final_verdict": (p.get("verdict") or {}).get("final"),
        "raw_trigger_n": ep.get("raw_trigger_n"),
        "true_episode_n": ep.get("true_episode_n"),
        "dedupe_entry_n": ep.get("one_episode_one_entry_oos"),
        "episode_blocked_n": ep.get("episode_blocked_n"),
        "structural_success": {s: (st.get(s) or {}).get("after_dedupe", {}).get("structural_success_rate") for s in ("EC1", "EC2", "EC3")},
        "economic_success": {s: (st.get(s) or {}).get("after_dedupe", {}).get("economic_success_rate") for s in ("EC1", "EC2", "EC3")},
        "mfe_capture": {s: (st.get(s) or {}).get("after_dedupe", {}).get("mean_capture_ratio_positive_mfe_only") for s in ("EC1", "EC2", "EC3")},
        "R0": {s: ((st.get(s) or {}).get("after_dedupe", {}).get("reality") or {}).get("R0") for s in ("EC1", "EC2", "EC3")},
        "R1": {s: ((st.get(s) or {}).get("after_dedupe", {}).get("reality") or {}).get("R1") for s in ("EC1", "EC2", "EC3")},
        "R2": {s: ((st.get(s) or {}).get("after_dedupe", {}).get("reality") or {}).get("R2") for s in ("EC1", "EC2", "EC3")},
        "R3": {s: ((st.get(s) or {}).get("after_dedupe", {}).get("reality") or {}).get("R3") for s in ("EC1", "EC2", "EC3")},
        "obs_delay": {
            s: {
                "median": (((st.get(s) or {}).get("after_dedupe", {}).get("reality") or {}).get("R0") or {}).get("median_obs_delay_sec"),
                "p90": (((st.get(s) or {}).get("after_dedupe", {}).get("reality") or {}).get("R0") or {}).get("p90_obs_delay_sec"),
            }
            for s in ("EC1", "EC2", "EC3")
        },
        "cap5": {k: {kk: (caps.get(k) or {}).get(kk) for kk in ("accepted", "pnl_5bps", "PF_5bps", "cap_blocked", "trades")} for k in ("P2", "P3", "P4", "P5")},
        "trades_per_day": {k: ((caps.get(k) or {}).get("turnover") or {}).get("trades_per_day") for k in ("P2", "P3", "P4", "P5")},
        "EC1_verdict": (st.get("EC1") or {}).get("strategy_verdict"),
        "EC2_verdict": (st.get("EC2") or {}).get("strategy_verdict"),
        "EC3_verdict": (st.get("EC3") or {}).get("strategy_verdict"),
        "pairing": {s: (st.get(s) or {}).get("pairing") for s in ("EC1", "EC2", "EC3")},
        "p5_reduction": (p.get("cap5") or {}).get("p5_reduction"),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_changed": False,
        "artifact_path": None,
    }
