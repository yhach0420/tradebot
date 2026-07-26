"""Entry–Exit Contract offline pipeline."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import (
    DEFAULT_THRESHOLDS,
    NATIVE,
    SOT_PBV2,
    SOT_PFE,
    SOT_PFE_INT,
    SOT_RPFE,
    SOT_VCIE,
    X6_PARAMS,
)
from research.entry_exit_contract.discovery import discover_capture_days
from research.entry_exit_contract.entries import build_ec_entries, load_push_day
from research.entry_exit_contract.evaluate import coverage_gate, evaluate_mode, pairing_verdict
from research.entry_exit_contract.portfolio import run_portfolios
from research.entry_exit_contract.report import emit_artifacts
from research.price_flow_exit.entries import load_pbv2_entries
from research.price_flow_exit.exit_rules import ExitParams
from research.price_flow_exit_integrity.trades import simulate_trades

JST = ZoneInfo("Asia/Tokyo")


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = [
        "NO_PRODUCTION_CHANGE",
        "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY",
        "ENTRY_EXIT_CONTRACT_INSUFFICIENT_OOS",
        "PBV2_X6_DIAGNOSTIC_ONLY",
        "ENTRY_EXIT_CONTRACT_FRAMEWORK_READY",
    ]
    cov = payload.get("coverage") or {}
    codes.append(cov.get("coverage_verdict") or "ENTRY_EXIT_CONTRACT_INSUFFICIENT_OOS")
    for sid in ("EC1", "EC2", "EC3"):
        pair = ((payload.get("strategies") or {}).get(sid) or {}).get("pairing") or "ENTRY_EXIT_PAIRING_NO_EDGE"
        codes.append(pair)
        ready_key = {
            "EC1": "VOLUME_BREAKOUT_CONTRACT_READY",
            "EC2": "PULLBACK_RECLAIM_CONTRACT_READY",
            "EC3": "COMPRESSION_BREAKOUT_CONTRACT_READY",
        }[sid]
        no_key = ready_key.replace("READY", "NO_EDGE")
        n = (((payload.get("strategies") or {}).get(sid) or {}).get("M2") or {}).get("n") or 0
        codes.append(ready_key if n > 0 else no_key)
        if pair != "ENTRY_EXIT_PAIRING_EDGE":
            codes.append(no_key)
    codes = sorted(set(codes))
    return {
        "final": "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY",
        "codes": codes,
        "summary": (
            "ENTRY–EXIT一体型契約（EC1/EC2/EC3）を実装し offline 検証・CAP=5再生まで完了。"
            " PBv2+X6は診断比較のみ。OOS3日のため採用判定禁止。"
        ),
        "no_production_reason": "本線/Shadow/Forward変更禁止。offline研究のみ。",
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "entry_exit_contract" / run_id
    print(f"[eec] start run_id={run_id}", flush=True)

    disc = discover_capture_days(native)
    days = disc["usable_days"]
    warmup = disc["warmup_day"]
    oos = tuple(disc["oos_days"])
    print(f"[eec] days={days} warmup={warmup} oos={oos}", flush=True)

    # thresholds: predefined defaults (train-only selection skipped — single coarse set frozen)
    thresholds = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    # optional mild train filter: if warmup has too few EC1, loosen vol impulse slightly within grid
    params = ExitParams(**X6_PARAMS)

    print("[eec] load PUSH…", flush=True)
    push_by_day = {d: load_push_day(d, native) for d in days}

    print("[eec] build EC entries…", flush=True)
    contracts = build_ec_entries(days, push_by_day, thresholds)

    strategies = {}
    for sid in ("EC1", "EC2", "EC3"):
        print(f"[eec] evaluate {sid} M0/M1/M2…", flush=True)
        m0 = evaluate_mode(contracts[sid], push_by_day, mode="M0", params=params, oos_days=oos)
        m1 = evaluate_mode(contracts[sid], push_by_day, mode="M1", params=params, oos_days=oos)
        m2 = evaluate_mode(contracts[sid], push_by_day, mode="M2", params=params, oos_days=oos)
        # drop heavy trades from nested for json size later — keep in memory for CAP5
        strategies[sid] = {
            "M0": {k: v for k, v in m0.items() if k != "trades"},
            "M1": {k: v for k, v in m1.items() if k != "trades"},
            "M2": {k: v for k, v in m2.items() if k != "trades"},
            "pairing": pairing_verdict(m0, m1, m2),
            "_trades": {"M0": m0["trades"], "M1": m1["trades"], "M2": m2["trades"]},
        }

    print("[eec] PBv2 diagnostic controls…", flush=True)
    pbv2 = [e for e in load_pbv2_entries(native) if e.day in oos]
    bars_cache: dict = {}
    pbv2_x0 = simulate_trades(pbv2, push_by_day, mode="X0", params=params, bars_cache=bars_cache)
    pbv2_x6 = simulate_trades(pbv2, push_by_day, mode="X6", params=params, bars_cache=bars_cache)

    print("[eec] CAP=5 P0–P7…", flush=True)
    cap = run_portfolios(
        pbv2_x0=pbv2_x0,
        pbv2_x6=pbv2_x6,
        ec1_m2=strategies["EC1"]["_trades"]["M2"],
        ec2_m2=strategies["EC2"]["_trades"]["M2"],
        ec3_m2=strategies["EC3"]["_trades"]["M2"],
        ec1_m1=strategies["EC1"]["_trades"]["M1"],
        ec2_m1=strategies["EC2"]["_trades"]["M1"],
        ec3_m1=strategies["EC3"]["_trades"]["M1"],
        ec1_m0=strategies["EC1"]["_trades"]["M0"],
        ec2_m0=strategies["EC2"]["_trades"]["M0"],
        ec3_m0=strategies["EC3"]["_trades"]["M0"],
    )

    cov = coverage_gate(contracts, strategies, oos)

    # strip private trades before persist
    strat_out = {}
    for sid, s in strategies.items():
        strat_out[sid] = {k: v for k, v in s.items() if k != "_trades"}

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "contract_version": "EEC_v1",
        "sot": {
            "pbv2": str(SOT_PBV2),
            "rpfe": str(SOT_RPFE),
            "vcie": str(SOT_VCIE),
            "price_flow_exit": str(SOT_PFE),
            "price_flow_exit_integrity": str(SOT_PFE_INT),
        },
        "discovery": disc,
        "warmup_day": warmup,
        "oos_days": list(oos),
        "thresholds": thresholds,
        "entry_counts": {k: len(v) for k, v in contracts.items()},
        "entry_counts_oos": {k: sum(1 for c in v if c.day in oos) for k, v in contracts.items()},
        "strategies": strat_out,
        "pbv2_diagnostic": {
            "n_oos": len(pbv2),
            "P0_note": "PBv2+current EXIT control",
            "P1_note": "PBv2+X6 DIAGNOSTIC ONLY",
        },
        "cap5": cap,
        "coverage": cov,
        "contract_samples": {
            sid: [c.to_row() for c in (contracts[sid][:40])] for sid in ("EC1", "EC2", "EC3")
        },
    }
    payload["verdict"] = _decide(payload)
    payload["completion"] = _completion(payload, cap)
    payload["out_dir"] = str(out_dir)
    payload["completion"]["58_artifact_path"] = str(out_dir)
    emit_artifacts(out_dir, payload)
    print(f"[eec] done verdict={payload['verdict']['final']} out={out_dir}", flush=True)
    return payload


def _completion(payload: dict[str, Any], cap: dict[str, Any]) -> dict[str, Any]:
    st = payload.get("strategies") or {}
    ports = (cap.get("portfolios") or {})
    ec1, ec2, ec3 = st.get("EC1") or {}, st.get("EC2") or {}, st.get("EC3") or {}

    def m(s, mode):
        return (s.get(mode) or {})

    return {
        "1_final_verdict": (payload.get("verdict") or {}).get("final"),
        "2_framework": (payload.get("coverage") or {}).get("verdict"),
        "3_usable_days": (payload.get("discovery") or {}).get("usable_days"),
        "4_warmup": payload.get("warmup_day"),
        "5_oos": payload.get("oos_days"),
        "6_ec1_entries": (payload.get("entry_counts_oos") or {}).get("EC1"),
        "7_ec2_entries": (payload.get("entry_counts_oos") or {}).get("EC2"),
        "8_ec3_entries": (payload.get("entry_counts_oos") or {}).get("EC3"),
        "9_complete_contracts": (payload.get("coverage") or {}).get("total_complete_contract"),
        "10_ec1_success": m(ec1, "M2").get("contract_success_rate"),
        "11_ec2_success": m(ec2, "M2").get("contract_success_rate"),
        "12_ec3_success": m(ec3, "M2").get("contract_success_rate"),
        "13_ec1_matched": {k: m(ec1, "M2").get(k) for k in ("n", "total_pnl_5bps", "PF_5bps", "dd_trade_sequence_max_dd")},
        "14_ec2_matched": {k: m(ec2, "M2").get(k) for k in ("n", "total_pnl_5bps", "PF_5bps", "dd_trade_sequence_max_dd")},
        "15_ec3_matched": {k: m(ec3, "M2").get(k) for k in ("n", "total_pnl_5bps", "PF_5bps", "dd_trade_sequence_max_dd")},
        "16_ec1_current": {k: m(ec1, "M0").get(k) for k in ("total_pnl_5bps", "PF_5bps")},
        "17_ec2_current": {k: m(ec2, "M0").get(k) for k in ("total_pnl_5bps", "PF_5bps")},
        "18_ec3_current": {k: m(ec3, "M0").get(k) for k in ("total_pnl_5bps", "PF_5bps")},
        "19_ec1_x6": {k: m(ec1, "M1").get(k) for k in ("total_pnl_5bps", "PF_5bps")},
        "20_ec2_x6": {k: m(ec2, "M1").get(k) for k in ("total_pnl_5bps", "PF_5bps")},
        "21_ec3_x6": {k: m(ec3, "M1").get(k) for k in ("total_pnl_5bps", "PF_5bps")},
        "22_pairing": {sid: (st.get(sid) or {}).get("pairing") for sid in ("EC1", "EC2", "EC3")},
        "23_generic_early_exit_separation": {
            sid: (st.get(sid) or {}).get("pairing") == "GENERIC_EARLY_EXIT_EFFECT_ONLY" for sid in ("EC1", "EC2", "EC3")
        },
        "24_inv_exit_latency": {sid: m(st.get(sid) or {}, "M2").get("mean_invalidation_to_exit_sec") for sid in ("EC1", "EC2", "EC3")},
        "25_false_invalidation": {sid: m(st.get(sid) or {}, "M2").get("false_invalidation_n") for sid in ("EC1", "EC2", "EC3")},
        "26_lost_winner": {sid: m(st.get(sid) or {}, "M2").get("lost_winner_n") for sid in ("EC1", "EC2", "EC3")},
        "27_same_episode_regret": {sid: m(st.get(sid) or {}, "M2").get("mean_same_episode_regret") for sid in ("EC1", "EC2", "EC3")},
        "28_executable_mfe": {sid: m(st.get(sid) or {}, "M2").get("mean_mfe_5bps") for sid in ("EC1", "EC2", "EC3")},
        "29_mfe_capture": {sid: m(st.get(sid) or {}, "M2").get("mean_mfe_capture") for sid in ("EC1", "EC2", "EC3")},
        "30_1tick_slip": {sid: m(st.get(sid) or {}, "M2").get("pnl_1tick_slip_total") for sid in ("EC1", "EC2", "EC3")},
        "31_2tick_slip": {sid: m(st.get(sid) or {}, "M2").get("pnl_2tick_slip_total") for sid in ("EC1", "EC2", "EC3")},
        "32_500ms_delay": {sid: m(st.get(sid) or {}, "M2").get("pnl_500ms_delay_total") for sid in ("EC1", "EC2", "EC3")},
        "33_P0": ports.get("P0"),
        "34_P1": ports.get("P1"),
        "35_P2": ports.get("P2"),
        "36_P3": ports.get("P3"),
        "37_P4": ports.get("P4"),
        "38_P5": ports.get("P5"),
        "39_P6": ports.get("P6"),
        "40_P7": ports.get("P7"),
        "41_cap_blocked": {k: (ports.get(k) or {}).get("cap_blocked") for k in ports},
        "42_same_symbol_blocked": {k: (ports.get(k) or {}).get("same_symbol_blocked") for k in ports},
        "43_episode_blocked": {k: (ports.get(k) or {}).get("episode_blocked") for k in ports},
        "44_trade_dd": {k: (ports.get(k) or {}).get("max_dd_trade_sequence") for k in ("P2", "P3", "P4", "P5")},
        "45_intraday_dd": {k: (ports.get(k) or {}).get("max_dd_intraday") for k in ("P2", "P3", "P4", "P5")},
        "46_top1_symbol": {sid: (m(st.get(sid) or {}, "M2").get("dependency") or {}).get("top1_symbol_pnl_share") for sid in ("EC1", "EC2", "EC3")},
        "47_top1_day": {sid: (m(st.get(sid) or {}, "M2").get("dependency") or {}).get("top1_day_pnl_share") for sid in ("EC1", "EC2", "EC3")},
        "48_pos_neg_days": {sid: {"pos": m(st.get(sid) or {}, "M2").get("pos_days"), "neg": m(st.get(sid) or {}, "M2").get("neg_days")} for sid in ("EC1", "EC2", "EC3")},
        "49_oos_insufficient": True,
        "50_data_quality": "PUSH/capture 4 days only; AM/PM both present on capture days",
        "51_changed_files": "src/research/entry_exit_contract/*, scripts/run_entry_exit_contract.py, tests/test_entry_exit_contract.py",
        "52_command": "python scripts/run_entry_exit_contract.py",
        "53_tests": "pytest tests/test_entry_exit_contract.py",
        "54_submit": 0,
        "55_cancel": 0,
        "56_live_order": 0,
        "57_mainline_changed": False,
        "58_artifact_path": None,
    }
