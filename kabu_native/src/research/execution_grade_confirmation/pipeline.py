"""Execution-grade confirmation reconstruction pipeline."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.entry_exit_contract.discovery import discover_capture_days
from research.entry_exit_contract.entries import detect_ec2, load_push_day
from research.entry_exit_contract.exits import path_for_contract, simulate_matched_exit
from research.entry_exit_contract_integrity.episode import segment_true_episodes
from research.execution_grade_confirmation.board import crossed_audit, load_quotes_for_symbols, sym_norm
from research.execution_grade_confirmation.constants import (
    EC2_THR,
    NATIVE,
    SOT_CAUSAL,
    SOT_V2,
    SOT_V3,
    STUDY_VERSION,
)
from research.execution_grade_confirmation.evaluate import (
    entry_scenario_summary,
    evaluate_confirmations,
    exit_scenario_summary,
    freeze_strict_confirmations,
    reconstruction_gate,
    run_historical_pairs,
    _summarize_trades,
)
from research.execution_grade_confirmation.fills import entry_fill, trade_pnl
from research.execution_grade_confirmation.lineage import lineage_report
from research.execution_grade_confirmation.prospective import prospective_spec_dict
from research.execution_grade_confirmation.report import emit_artifacts
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds

JST = ZoneInfo("Asia/Tokyo")


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = ["NO_PRODUCTION_CHANGE", "INSUFFICIENT_EXECUTION_GRADE_OOS"]
    lin = payload.get("lineage") or {}
    gate = payload.get("reconstruction_gate") or {}
    evalp = payload.get("evaluation") or {}
    pairs = payload.get("historical_pairs") or {}
    e1x1 = pairs.get("E1_X1") or {}

    codes.append(lin.get("raw_board_atomic") or "RAW_BOARD_ATOMIC_NOT_AVAILABLE")
    codes.append(lin.get("quote_lineage") or "QUOTE_LINEAGE_BLOCKED")
    codes.append(gate.get("verdict") or "HISTORICAL_EXECUTION_RECONSTRUCTION_BLOCKED")

    ask_n = int(evalp.get("ask_filled_n") or 0)
    if ask_n <= 0:
        codes.append("ENTRY_CONFIRMATION_EXECUTION_NOT_EVALUABLE")
        # explicitly do NOT emit ENTRY_CONFIRMATION_NO_EDGE
    else:
        codes.append("ENTRY_CONFIRMATION_EXECUTION_EVALUABLE")

    if gate.get("ready"):
        codes.append("EXECUTION_GRADE_CAPTURE_READY")
        pf = e1x1.get("PF_5bps")
        cap = (e1x1.get("cap5") or {}).get("pnl_5bps")
        dep = e1x1.get("dependency_blocked")
        if (
            pf is not None
            and float(pf) > 1
            and cap is not None
            and float(cap) > 0
            and int(e1x1.get("pos_days") or 0) > int(e1x1.get("neg_days") or 0)
            and not dep
        ):
            codes.append("ENTRY_CONFIRMATION_OFFLINE_SIGNAL")
        else:
            codes.append("ENTRY_CONFIRMATION_NO_EDGE")
    else:
        codes.append("EXECUTION_GRADE_CAPTURE_BLOCKED")

    codes.append((payload.get("prospective") or {}).get("verdict") or "PROSPECTIVE_CAPTURE_BLOCKED")
    codes = sorted(set(codes))
    return {
        "final": "NO_PRODUCTION_CHANGE",
        "codes": codes,
        "summary": (
            "Execution-grade quote reconstruction完了。"
            f" raw_atomic={lin.get('raw_board_atomic')}; mapping={lin.get('field_mapping')}; "
            f"crossed_root={lin.get('crossed_root_cause')}; "
            f"strict={evalp.get('n_strict')}; AskCov={evalp.get('ask_coverage_E1')}; "
            f"BidCov={evalp.get('bid_coverage_X1')}; gate={gate.get('verdict')}; "
            f"E1_X1_n={e1x1.get('n_traded')} PF={e1x1.get('PF_5bps')}."
            " v3 A1 PF2.40はcrossed ask参考値のみ。本線変更なし。"
        ),
        "no_production_reason": "offline再構築とcapture-only observer仕様のみ。",
    }


def _c0_true_ask(
    contracts,
    quotes_by_day,
    push,
    oos_days,
) -> dict[str, Any]:
    """Immediate EC2 entry filled at true Ask (E1) near original entry_time."""
    trades = []
    for c in contracts:
        if c.day not in oos_days:
            continue
        qs = (quotes_by_day.get(c.day) or {}).get(sym_norm(c.symbol)) or []
        if not qs:
            continue
        fr = entry_fill(qs, c.entry_time, scenario="E1")
        if fr.get("fill_status") != "FILLED":
            continue
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        path = path_for_contract(c, ticks)
        if not path:
            continue
        ex = simulate_matched_exit(c, path)
        # exit at X1 true bid
        xr = entry_fill  # noqa — use exit_fill
        from research.execution_grade_confirmation.fills import exit_fill

        xr = exit_fill(qs, ex.exit_time, scenario="X1")
        if xr.get("fill_status") != "FILLED":
            continue
        et = datetime.fromisoformat(fr["fill_event_time"])
        xt = datetime.fromisoformat(xr["fill_event_time"])
        trades.append(
            SimTrade(
                day=c.day,
                symbol=sym_norm(c.symbol),
                entry_time=et,
                exit_time=xt,
                entry_price=float(fr["fill_price"]),
                exit_price=float(xr["fill_price"]),
                exit_reason="C0_E1_X1",
                pnl_5bps=trade_pnl(float(fr["fill_price"]), float(xr["fill_price"])),
                hold_sec=(xt - et).total_seconds(),
                entry_method="EC2",
                cohort="C0",
                setup_id=c.setup_id,
                impulse_episode_id=c.episode_id,
                breakout_episode_id=c.episode_id,
                pbv2=False,
                vcie=True,
                mode="C0",
                session=c.session,
            )
        )
    return _summarize_trades(trades, oos_days=oos_days, mode="C0")


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "execution_grade_confirmation" / run_id
    print(f"[egc] start {run_id}", flush=True)

    disc = discover_capture_days(native)
    days = list(disc["usable_days"])
    oos = tuple(disc["oos_days"])
    print(f"[egc] days={days} oos={oos}", flush=True)

    print("[egc] lineage audit…", flush=True)
    lineage = lineage_report(days)
    print(
        f"[egc] atomic={lineage.get('raw_board_atomic')} mapping={lineage.get('field_mapping')} "
        f"true_valid={lineage.get('true_book_valid_rate')}",
        flush=True,
    )

    print("[egc] load PUSH cache + rebuild EC2 / freeze strict causal…", flush=True)
    push = {d: load_push_day(d, native) for d in days}
    raw = []
    for day in days:
        for sym, ticks in (push.get(day) or {}).items():
            if len(ticks) < 80:
                continue
            raw.extend(detect_ec2(aggregate_to_seconds(ticks), day=day, thr=EC2_THR))
    seg = segment_true_episodes(raw)
    accepted = seg["accepted"]
    frozen = freeze_strict_confirmations(accepted, push, oos_days=oos)
    print(f"[egc] strict causal frozen n={len(frozen)}", flush=True)

    # symbols needed
    syms_by_day: dict[str, set[str]] = {}
    for a in frozen:
        syms_by_day.setdefault(a["day"], set()).add(sym_norm(a["symbol"]))
    # also include C0 symbols on oos
    for c in accepted:
        if c.day in oos:
            syms_by_day.setdefault(c.day, set()).add(sym_norm(c.symbol))

    print("[egc] load event-grade boards from raw JSONL…", flush=True)
    quotes_by_day: dict[str, dict] = {}
    cross_parts = []
    for day in oos:
        syms = list(syms_by_day.get(day) or [])
        print(f"[egc]   {day} symbols={len(syms)}", flush=True)
        qmap = load_quotes_for_symbols(day, syms)
        quotes_by_day[day] = qmap
        cross_parts.append(crossed_audit(qmap))

    # merge cross audits
    cross = _merge_cross(cross_parts)
    print(
        f"[egc] board events={cross.get('total_board_events')} valid_rate={cross.get('valid_rate')} "
        f"true_crossed={cross.get('crossed_rate_true_book')} kabu_crossed={cross.get('kabu_named_crossed_rate')}",
        flush=True,
    )

    print("[egc] ENTRY/EXIT fills E0–E5 / X0–X5…", flush=True)
    eval_pack = evaluate_confirmations(frozen, quotes_by_day, push)
    print(
        f"[egc] AskCov={eval_pack['ask_coverage_E1']} BidCov={eval_pack['bid_coverage_X1']}",
        flush=True,
    )

    gate = reconstruction_gate(eval_pack, cross, lineage)
    print(f"[egc] gate={gate['verdict']} {gate['gates']}", flush=True)

    print("[egc] historical pairs…", flush=True)
    pairs = run_historical_pairs(eval_pack, oos_days=oos, gate_ready=bool(gate["ready"]))
    c0 = _c0_true_ask(accepted, quotes_by_day, push, oos)
    for k in ("E1_X1", "E0_X1", "E1_X0"):
        if k in pairs:
            s = pairs[k]
            print(f"[egc] {k}: n={s.get('n_traded')} pnl={s.get('total_pnl_5bps')} PF={s.get('PF_5bps')} formal={s.get('formal')}", flush=True)

    prospective = prospective_spec_dict()
    # historical capture quality proxy from cross + eval
    capture_quality = [
        {
            "day": day,
            "symbols": len(quotes_by_day.get(day) or {}),
            "events": sum(len(v) for v in (quotes_by_day.get(day) or {}).values()),
            "valid_rate": crossed_audit(quotes_by_day.get(day) or {}).get("valid_rate"),
            "crossed_rate": crossed_audit(quotes_by_day.get(day) or {}).get("crossed_rate_true_book"),
        }
        for day in oos
    ]

    # strip contracts from frozen for JSON
    frozen_public = []
    for a in frozen:
        r = {k: v for k, v in a.items() if k != "contract"}
        frozen_public.append(r)

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "study_version": STUDY_VERSION,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "ec2_confirmation_noise_exit_unchanged": True,
        "sot_causal": str(SOT_CAUSAL),
        "sot_v3": str(SOT_V3),
        "sot_v2": str(SOT_V2),
        "discovery": disc,
        "oos_days": list(oos),
        "lineage": lineage,
        "crossed_audit": cross,
        "frozen_confirmations_n": len(frozen_public),
        "confirmation_fixed_samples": frozen_public[:80],
        "evaluation": {
            "n_strict": eval_pack["n_strict"],
            "ask_coverage_E1": eval_pack["ask_coverage_E1"],
            "bid_coverage_X1": eval_pack["bid_coverage_X1"],
            "ask_filled_n": eval_pack["ask_filled_n"],
            "bid_filled_n": eval_pack["bid_filled_n"],
            "entry_scenarios": entry_scenario_summary(eval_pack["entry_rows"]),
            "exit_scenarios": exit_scenario_summary(eval_pack["exit_rows"]),
        },
        "reconstruction_gate": gate,
        "historical_pairs": pairs,
        "C0_true_ask": c0,
        "v3_A1_reference_only": {
            "note": "crossed ask PF — not formal",
            "pnl": 437307.86,
            "PF": 2.4034,
            "cap5": 407506.36,
        },
        "prospective": prospective,
        "capture_quality": capture_quality,
        "entry_fill_samples": (eval_pack["entry_rows"].get("E1") or [])[:40],
        "exit_fill_samples": (eval_pack["exit_rows"].get("X1") or [])[:40],
    }
    payload["verdict"] = _decide(payload)
    payload["completion"] = _completion(payload)
    payload["out_dir"] = str(out_dir)
    payload["completion"]["artifact_path"] = str(out_dir)
    emit_artifacts(out_dir, payload)
    print(f"[egc] done -> {out_dir}", flush=True)
    return payload


def _merge_cross(parts: list[dict]) -> dict[str, Any]:
    if not parts:
        return crossed_audit({})
    keys_sum = [
        "total_board_events",
        "valid_board_events",
        "crossed_board_events",
        "locked_board_events",
        "missing_bid",
        "missing_ask",
        "missing_qty",
        "stale_board",
        "non_monotonic_timestamp",
        "kabu_named_crossed",
        "true_book_crossed",
    ]
    out = {k: 0 for k in keys_sum}
    examples = []
    by_sym = []
    for p in parts:
        for k in keys_sum:
            out[k] += int(p.get(k) or 0)
        examples.extend(p.get("examples") or [])
        by_sym.extend(p.get("by_symbol") or [])
    n = max(1, out["total_board_events"])
    out["valid_rate"] = round(out["valid_board_events"] / n, 4)
    out["crossed_rate_true_book"] = round(out["true_book_crossed"] / n, 4)
    out["kabu_named_crossed_rate"] = round(out["kabu_named_crossed"] / n, 4)
    out["classification"] = {
        "TRUE_MARKET_CROSSED": out["true_book_crossed"],
        "ASYNC_FIELD_MERGE": 0,
        "ONE_SECOND_AGGREGATION_ARTIFACT": 0,
        "FIELD_MAPPING_ERROR": out["kabu_named_crossed"],
        "STALE_QUOTE_CARRY": out["stale_board"],
        "UNKNOWN": 0,
    }
    out["examples"] = examples[:20]
    out["by_symbol"] = sorted(by_sym, key=lambda r: -r["n"])[:40]
    return out


def _completion(p: dict[str, Any]) -> dict[str, Any]:
    v = p.get("verdict") or {}
    lin = p.get("lineage") or {}
    gate = p.get("reconstruction_gate") or {}
    ev = p.get("evaluation") or {}
    pairs = p.get("historical_pairs") or {}
    e1x1 = pairs.get("E1_X1") or {}
    dep = e1x1.get("dependency") or {}
    return {
        "1_final_verdict": v.get("final"),
        "2_raw_atomic_board": lin.get("raw_board_atomic"),
        "3_crossed_root_cause": lin.get("crossed_root_cause"),
        "4_field_mapping": lin.get("field_mapping"),
        "5_timestamp_integrity": gate.get("timestamp_monotonic_pass"),
        "6_historical_valid_Ask_coverage": ev.get("ask_coverage_E1"),
        "7_historical_valid_Bid_coverage": ev.get("bid_coverage_X1"),
        "8_strict_confirmation_n": ev.get("n_strict"),
        "9_executable_confirmation_n": ev.get("ask_filled_n"),
        "10_ENTRY_E0_E5": {k: (ev.get("entry_scenarios") or {}).get(k) for k in ("E0", "E1", "E2", "E3", "E4", "E5")},
        "11_EXIT_X0_X5": {k: (ev.get("exit_scenarios") or {}).get(k) for k in ("X0", "X1", "X2", "X3", "X4", "X5")},
        "12_CAP5": (e1x1.get("cap5") if gate.get("ready") else {"formal": False, "note": "gate_blocked", **(e1x1.get("cap5") or {})}),
        "13_dependency": {
            "blocked": e1x1.get("dependency_blocked"),
            "top1_symbol": dep.get("top1_symbol"),
            "top1_share": dep.get("top1_symbol_pnl_share"),
            "top1_day": dep.get("top1_day"),
            "top1_day_share": dep.get("top1_day_pnl_share"),
            "loo_sym_PF": dep.get("pf_after_exclude_max_symbol"),
            "loo_day_PF": dep.get("pf_after_exclude_max_day"),
        },
        "14_historical_reconstruction": gate.get("verdict"),
        "15_prospective_capture": (p.get("prospective") or {}).get("verdict"),
        "16_capture_quality": p.get("capture_quality"),
        "17_submit": 0,
        "18_cancel": 0,
        "19_live_order": 0,
        "20_mainline_changed": False,
        "21_artifact_path": None,
        "codes": v.get("codes"),
    }
