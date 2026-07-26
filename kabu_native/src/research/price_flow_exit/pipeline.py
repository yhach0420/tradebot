"""Price-Flow EXIT offline pipeline."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.price_flow_exit.constants import (
    NATIVE,
    OOS_DAYS,
    SOT_PBV2,
    SOT_RPFE,
    SOT_VCIE,
)
from research.price_flow_exit.entries import build_cohorts
from research.price_flow_exit.evaluate import run_evaluation
from research.price_flow_exit.report import emit_artifacts

JST = ZoneInfo("Asia/Tokyo")


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = ["NO_PRODUCTION_CHANGE", "PRICE_FLOW_EXIT_OFFLINE_ONLY"]
    ev = payload.get("evaluation") or {}
    codes.append(ev.get("bottleneck") or "MIXED_ENTRY_EXIT_BOTTLENECK")
    if ev.get("insufficient_oos"):
        codes.append("PRICE_FLOW_EXIT_INSUFFICIENT_OOS")

    # parity from E0
    cohorts = ev.get("cohorts") or {}
    parity = (cohorts.get("E0") or {}).get("parity") or {}
    codes.append(parity.get("verdict") or "EXIT_BASELINE_REPRODUCTION_BLOCKED")

    e0 = cohorts.get("E0") or {}
    if e0.get("abcd"):
        codes.append("EXECUTABLE_MFE_AUDIT_READY")
    else:
        codes.append("EXECUTABLE_MFE_AUDIT_BLOCKED")

    # per-mode edge vs X0 on E0 OOS
    modes = e0.get("modes") or {}
    x0 = modes.get("X0") or {}
    for mid, edge_code, no_code in (
        ("X1", "FAILED_BREAKOUT_EXIT_EDGE", "FAILED_BREAKOUT_EXIT_NO_EDGE"),
        ("X2", "NO_FOLLOW_THROUGH_EXIT_EDGE", "NO_FOLLOW_THROUGH_EXIT_NO_EDGE"),
        ("X3", "BREAK_EVEN_PROTECTION_EDGE", "BREAK_EVEN_PROTECTION_NO_EDGE"),
        ("X4", "IMPULSE_DECAY_EXIT_EDGE", "IMPULSE_DECAY_EXIT_NO_EDGE"),
        ("X5", "VOLUME_EXHAUSTION_EXIT_EDGE", "VOLUME_EXHAUSTION_EXIT_NO_EDGE"),
        ("X6", "COMPOSITE_PRICE_FLOW_EXIT_EDGE", "COMPOSITE_PRICE_FLOW_EXIT_NO_EDGE"),
    ):
        m = modes.get(mid) or {}
        edge = (
            float(m.get("total_pnl_5bps") or -1e18) > float(x0.get("total_pnl_5bps") or 0)
            and (m.get("PF_5bps") or 0) > (x0.get("PF_5bps") or 0)
            and float(m.get("max_dd_5bps") or -1e18) >= float(x0.get("max_dd_5bps") or 0) - 1e-9
        )
        codes.append(edge_code if edge else no_code)

    ready = (
        parity.get("gate_ok")
        and not ev.get("insufficient_oos")
        and "COMPOSITE_PRICE_FLOW_EXIT_EDGE" in codes
    )
    if ready:
        codes.append("PRICE_FLOW_EXIT_OFFLINE_CANDIDATE_READY")
        final = "PRICE_FLOW_EXIT_OFFLINE_CANDIDATE_READY"
    else:
        final = "PRICE_FLOW_EXIT_OFFLINE_ONLY"

    return {
        "final": final,
        "codes": sorted(set(codes)),
        "summary": (
            "Price-Flow EXIT offline監査完了。"
            + (" 高解像度OOS不足のため採用不可。" if ev.get("insufficient_oos") else "")
            + f" bottleneck={ev.get('bottleneck')}。"
        ),
        "no_production_reason": "本線/Shadow/Forward変更禁止。EXIT研究のみ。",
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "price_flow_exit" / run_id
    print(f"[pfe] start run_id={run_id}", flush=True)

    cohorts = build_cohorts(native)
    evaluation = run_evaluation(cohorts)

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "entry_unchanged": True,
        "sot": {
            "pbv2": str(SOT_PBV2),
            "rpfe": str(SOT_RPFE),
            "vcie": str(SOT_VCIE),
        },
        "oos_days": list(OOS_DAYS),
        "cohort_sizes": {k: len(v) for k, v in cohorts.items()},
        "evaluation": evaluation,
        "data_sources": [
            {"source": str(SOT_PBV2), "role": "PBv2 SoT"},
            {"source": str(SOT_VCIE), "role": "VCIE SoT / thresholds"},
            {"source": "results/research/volume_confirmed_impulse_entry/_push_cache/*.pkl", "role": "PUSH bid/volume"},
        ],
    }
    payload["verdict"] = _decide(payload)
    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    print(f"[pfe] done verdict={payload['verdict'].get('final')} out={out_dir}", flush=True)
    return payload
