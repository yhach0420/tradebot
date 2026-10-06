"""V8 join-only: V7 TF1 rows + V6 PQ3/TQ diagnostics. No recapture. No C14. No EXIT."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_entry_family.harvest import CACHE
from research.simple_tech_entry_family.v6_harvest import V6_CACHE
from research.simple_tech_entry_family.v7_harvest import V7_CACHE
from research.simple_tech_entry_family.v8_spec import ARM_ORDER

V8_CACHE = CACHE / "v8_architecture_role_rca"
SLIM_KEYS = (
    "date",
    "symbol",
    "t0",
    "trend",
    "pullback",
    "rci",
    "pa",
    "volume",
    "board_ok",
    "s6",
    "executable_signal",
    "markout_60",
    "markout_180",
    "markout_300",
    "mfe_bps",
    "mae_bps",
    "cost_recovered_300",
    "vol_accel",
    "VQ2",
    "PQ3",
    "TQ1",
    "TQ2",
    "ema9_gt_ema21",
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _key(row: dict[str, Any]) -> tuple[str, str, float]:
    return (str(row.get("date") or ""), str(row.get("symbol") or "").replace(".T", ""), float(row.get("t0") or 0.0))


def arm_pass(row: dict[str, Any], arm_id: str) -> bool:
    t = bool(row.get("trend"))
    p = bool(row.get("pullback"))
    c = bool(row.get("rci"))
    a = bool(row.get("pa"))
    v = bool(row.get("volume"))
    b = bool(row.get("board_ok"))
    if arm_id == "A0_V1":
        return t and p and c and a and v and b
    if arm_id == "A1_NO_PRICE_ACTION":
        return t and p and c and v and b
    if arm_id == "A2_NO_VOLUME":
        return t and p and c and a and b
    if arm_id == "A3_NO_PA_NO_VOLUME":
        return t and p and c and b
    if arm_id == "A4_CORE":
        return p and c and b
    if arm_id == "A5_CORE_NO_RCI":
        return p and b
    if arm_id == "A6_CORE_NO_BOARD":
        return p and c
    raise KeyError(arm_id)


def join_v8_day(*, v7_body: dict[str, Any], v6_body: dict[str, Any], spec_sha: str) -> dict[str, Any]:
    leak = {
        "JOIN_MISS_N": 0,
        "TF_NON1_SKIP_N": 0,
        "RECAPTURE_N": 0,
        "C14_REPLAY_N": 0,
        "EXIT_SIM_N": 0,
        "PQ3_GATE_N": 0,
        "PERSISTENCE_AND_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "NEW_TF_N": 0,
        "MIXED_TF_STRATEGY_N": 0,
        "EXTRA_ARM_N": 0,
    }
    v6_by: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in list(v6_body.get("rows") or []):
        k = _key(r)
        v6_by[(k[0], k[1], int(round(k[2] * 1000.0)))] = r
    rows: list[dict[str, Any]] = []
    for r in list(v7_body.get("rows") or []):
        if str(r.get("tf") or "") != "TF1":
            leak["TF_NON1_SKIP_N"] += 1
            continue
        k = _key(r)
        v6 = v6_by.get((k[0], k[1], int(round(k[2] * 1000.0)))) or {}
        if not v6:
            leak["JOIN_MISS_N"] += 1
        tq1 = v6.get("TQ1")
        rec = {
            "date": r.get("date"),
            "symbol": str(r.get("symbol") or "").replace(".T", ""),
            "t0": r.get("t0"),
            "trend": bool(r.get("trend")),
            "pullback": bool(r.get("pullback")),
            "rci": bool(r.get("rci")),
            "pa": bool(r.get("pa")),
            "volume": bool(r.get("volume")),
            "board_ok": bool(r.get("board_ok")),
            "s6": bool(r.get("s6")),
            "executable_signal": bool(r.get("executable_signal")),
            "markout_60": r.get("markout_60"),
            "markout_180": r.get("markout_180"),
            "markout_300": r.get("markout_300"),
            "mfe_bps": r.get("mfe_bps"),
            "mae_bps": r.get("mae_bps"),
            "cost_recovered_300": bool(_finite(r.get("mfe_bps")) and float(r["mfe_bps"]) >= 0.0),
            "vol_accel": r.get("vol_accel"),
            "VQ2": r.get("VQ2"),
            "PQ3": v6.get("PQ3"),
            "TQ1": tq1,
            "TQ2": v6.get("TQ2"),
            "ema9_gt_ema21": bool(_finite(tq1) and float(tq1) > 0.0) if v6 else bool(r.get("trend")),
        }
        rows.append(rec)
    return {
        "ok": True,
        "date": v7_body.get("date"),
        "spec_sha": spec_sha,
        "rows": rows,
        "leak": leak,
        "blocker": None,
    }


def save_v8_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim_rows = [{k: r.get(k) for k in SLIM_KEYS} for r in list(body.get("rows") or [])]
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "leak": body.get("leak"),
            "rows": slim_rows,
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def filter_arm(rows: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    if arm_id not in ARM_ORDER:
        raise KeyError(arm_id)
    hit = [r for r in rows if arm_pass(r, arm_id)]
    exe = [r for r in hit if r.get("executable_signal")]
    return {"ARM_ID": arm_id, "SIGNAL_N": len(hit), "EXECUTABLE_SIGNAL_N": len(exe), "rows": hit, "exe_rows": exe}
