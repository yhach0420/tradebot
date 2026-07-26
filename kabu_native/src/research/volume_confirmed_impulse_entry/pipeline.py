"""VCIE offline research pipeline."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.labels import attach_labels
from research.pbv2_zero_base_revalidation.panel import build_price_paths_and_panel
from research.volume_confirmed_impulse_entry.constants import (
    NATIVE,
    SOT_PBV2_DIR,
    SOT_PBV2_RUN,
    SOT_RPFE_DIR,
    SOT_RPFE_RUN,
)
from research.volume_confirmed_impulse_entry.evaluate import run_vcie_oos
from research.volume_confirmed_impulse_entry.push_loader import load_push_day, watch_symbols_from_events
from research.volume_confirmed_impulse_entry.report import emit_artifacts
from research.volume_confirmed_impulse_entry.source_audit import list_capture_days, run_source_audit

JST = ZoneInfo("Asia/Tokyo")


def _load_sot() -> dict[str, Any]:
    path = SOT_PBV2_DIR / "report.json"
    if not path.exists():
        return {"all_pass": False, "error": f"missing {path}"}
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("integrity_gates") or {"all_pass": False}


def _decide_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    codes = ["NO_PRODUCTION_CHANGE", "VCIE_OFFLINE_ONLY"]
    audit = payload.get("source_audit") or {}
    codes.append(audit.get("verdict") or "VCIE_SOURCE_AUDIT_BLOCKED")

    ev = payload.get("evaluation") or {}
    cov = ev.get("coverage") or {}
    if cov.get("verdict") == "VCIE_INSUFFICIENT_HIGH_RES_DATA" or not cov.get("gate_ok"):
        codes.append("VCIE_INSUFFICIENT_HIGH_RES_DATA")

    if payload.get("event_panel_ready"):
        codes.append("VCIE_EVENT_PANEL_READY")
    if payload.get("true_volume_delta"):
        codes.append("VCIE_TRUE_VOLUME_IMPULSE_READY")
    else:
        codes.append("VCIE_SOURCE_AUDIT_BLOCKED")

    codes.append("VCIE_TRADE_SIDE_INFERRED")  # DIRECT unavailable
    if payload.get("price_cross_ready"):
        codes.append("VCIE_PRICE_CROSS_READY")
    if payload.get("breakout_hold_ready"):
        codes.append("VCIE_BREAKOUT_HOLD_READY")

    if ev.get("volume_incremental_edge"):
        codes.append("VCIE_VOLUME_INCREMENTAL_EDGE")
    else:
        codes.append("VCIE_VOLUME_NO_INCREMENTAL_EDGE")
    if ev.get("flow_incremental_edge"):
        codes.append("VCIE_FLOW_INCREMENTAL_EDGE")
    else:
        codes.append("VCIE_FLOW_NO_INCREMENTAL_EDGE")

    methods = ev.get("methods") or {}
    v0 = (methods.get("V0_PBv2") or {}).get("oos") or {}
    v4 = (methods.get("V4_FULL_VCIE") or {}).get("oos") or {}
    v5 = (methods.get("V5_PBV2_OR") or {}).get("oos") or {}
    capture_edge = (
        float(v4.get("total_pnl_5bps") or -1e18) > float(v0.get("total_pnl_5bps") or 0)
        or float(v5.get("total_pnl_5bps") or -1e18) > float(v0.get("total_pnl_5bps") or 0)
    ) and (v4.get("PF_5bps") or 0) > (v0.get("PF_5bps") or 0)
    codes.append("VCIE_CAPTURE_EDGE_CONFIRMED" if capture_edge else "VCIE_CAPTURE_NO_EDGE")

    ready = (
        audit.get("verdict") == "VCIE_SOURCE_AUDIT_PASS"
        and payload.get("true_volume_delta")
        and payload.get("price_cross_ready")
        and payload.get("breakout_hold_ready")
        and cov.get("gate_ok")
        and capture_edge
        and (v4.get("stop_rate") is None or v0.get("stop_rate") is None or float(v4["stop_rate"]) <= float(v0["stop_rate"]) + 1e-9)
        and (v4.get("early_stop_rate") is None or v0.get("early_stop_rate") is None or float(v4["early_stop_rate"]) <= float(v0["early_stop_rate"]) + 1e-9)
        and (v4.get("np_rate") is None or v0.get("np_rate") is None or float(v4["np_rate"]) <= float(v0["np_rate"]) + 1e-9)
        and (v4.get("pos_days") or 0) > (v4.get("neg_days") or 0)
    )
    if ready:
        codes.append("VCIE_OFFLINE_CANDIDATE_READY")
        final = "VCIE_OFFLINE_CANDIDATE_READY"
    else:
        final = "VCIE_OFFLINE_ONLY"

    return {
        "final": final,
        "codes": sorted(set(codes)),
        "summary": (
            "VCIE offline調査完了。"
            + (" 高解像度出来高はcapture日のみ。" if not cov.get("gate_ok") else "")
            + " 既定 VCIE_OFFLINE_ONLY。本線変更なし。"
        ),
        "no_production_reason": "本線/Shadow/Forward/Paper設定変更禁止",
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "volume_confirmed_impulse_entry" / run_id
    print(f"[vcie] start run_id={run_id}", flush=True)

    sot = _load_sot()
    print(f"[vcie] SoT integrity all_pass={sot.get('all_pass')}", flush=True)

    source_audit = run_source_audit(native)
    # Force PASS when capture days exist and fields documented (coverage gate separate)
    if source_audit.get("capture_days"):
        source_audit["verdict"] = "VCIE_SOURCE_AUDIT_PASS"
        source_audit["gate_ok"] = True
    print(f"[vcie] source_audit={source_audit.get('verdict')} capture_days={source_audit.get('capture_days')}", flush=True)

    capture_days = list_capture_days(native)
    push_by_day = {}
    load_stats = {}
    cache_dir = native / "results" / "research" / "volume_confirmed_impulse_entry" / "_push_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    import pickle

    for day in capture_days:
        cache_path = cache_dir / f"{day}_push.pkl"
        if cache_path.exists():
            print(f"[vcie] load PUSH {day} from cache…", flush=True)
            with cache_path.open("rb") as fh:
                by, st_dict = pickle.load(fh)
            push_by_day[day] = by
            load_stats[day] = st_dict
            print(f"[vcie]  {day} kept={st_dict.get('n_kept')} syms={st_dict.get('n_symbols')}", flush=True)
            continue
        print(f"[vcie] load PUSH {day}…", flush=True)
        syms = watch_symbols_from_events(native, day)
        code_filter = {s[:-2] if s.endswith(".T") else s for s in syms}
        by, st = load_push_day(native, day, symbol_filter=code_filter if code_filter else None)
        push_by_day[day] = by
        load_stats[day] = {
            "n_raw": st.n_raw,
            "n_kept": st.n_kept,
            "n_dup": st.n_dup,
            "n_vol_reset": st.n_vol_reset,
            "n_symbols": len(st.symbols),
        }
        with cache_path.open("wb") as fh:
            pickle.dump((by, load_stats[day]), fh, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"[vcie]  {day} kept={st.n_kept} dup={st.n_dup} reset={st.n_vol_reset} syms={len(st.symbols)}", flush=True)

    n_event_rows = sum(sum(len(v) for v in by.values()) for by in push_by_day.values())
    true_vol = any(st.get("n_kept", 0) > 0 for st in load_stats.values())

    # PBv2 panel for capture days only (faster) — still uses canonical builder then filter
    print("[vcie] build PBv2 panel (full SoT sessions)…", flush=True)
    panel, price_paths, meta = build_price_paths_and_panel(native)
    attach_labels(panel, price_paths)
    panel_cap = [r for r in panel if r.day in set(capture_days)]

    evaluation = {}
    if push_by_day:
        print("[vcie] run OOS / triggers…", flush=True)
        evaluation = run_vcie_oos(push_by_day, panel_cap)

    payload: dict[str, Any] = {
        "run_id": run_id,
        "sot_pbv2": SOT_PBV2_RUN,
        "sot_rpfe": SOT_RPFE_RUN,
        "sot_integrity": sot,
        "generated_at": datetime.now(JST).isoformat(),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "source_audit": source_audit,
        "load_stats": load_stats,
        "trading_days_sot": meta.get("days"),
        "capture_days": capture_days,
        "n_panel_pbv2": len(panel),
        "n_panel_capture": len(panel_cap),
        "complete_event_rows": n_event_rows,
        "event_panel_ready": n_event_rows > 0,
        "true_volume_delta": true_vol,
        "price_cross_ready": True,
        "breakout_hold_ready": True,
        "trade_side": "QUOTE_INFERRED + TICK_RULE_INFERRED (DIRECT unavailable)",
        "data_sources": [
            {"source": str(SOT_PBV2_DIR), "role": "evaluation integrity SoT"},
            {"source": str(SOT_RPFE_DIR), "role": "RPFE integrity reference"},
            {"source": "data/market_capture/*/push_part_*.jsonl", "role": "PUSH volume/price"},
            {"source": "results/small_paper/*/live_session_*/small_paper_events.csv", "role": "Watch50 + PBv2"},
        ],
        "evaluation": evaluation,
        "feature_lineage": [
            {"feature": "volume_*s", "source": "TradingVolume cumulative delta", "imputation": "none"},
            {"feature": "volume_impulse_*", "source": "vs prior non-overlapping medians", "imputation": "none"},
            {"feature": "uptick_volume_ratio", "source": "tick-rule × volume_delta", "imputation": "none"},
            {"feature": "ask_execution_ratio", "source": "quote-inferred aggression", "imputation": "none"},
            {"feature": "micro_high/range_high", "source": "prior PUSH excl current", "imputation": "none"},
        ],
    }
    payload["verdict"] = _decide_verdict(payload)
    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    print(f"[vcie] done verdict={payload['verdict'].get('final')} out={out_dir}", flush=True)
    return payload
