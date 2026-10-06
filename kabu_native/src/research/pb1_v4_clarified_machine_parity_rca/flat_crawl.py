"""FLAT / CRAWL live-thesis RCA. No threshold changes."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite

CASES = (
    ("8058", "20250812"),
    ("6273", "20250120"),
    ("5802", "20250613"),
    ("3382", "20241115"),
    ("7182", "20251112"),
    ("8630", "20250911"),
)


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}


def scale_audit(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:3]
    clock = dict(snap.get("clock_snap") or {})
    clocks = list(clock.get("same_clock") or [])
    atr = snap.get("atr20")
    seed = dict(snap.get("seed_row") or {})
    first_o = bars[0].get("o") if bars else None
    last_c = bars[-1].get("c") if bars else None
    net = (float(last_c) - float(first_o)) if _finite(first_o) and _finite(last_c) else None
    gross = sum(float(b["range"]) for b in bars if _finite(b.get("range")))
    or_h, or_l = snap.get("or_high"), snap.get("or_low")
    or_rng = (float(or_h) - float(or_l)) if _finite(or_h) and _finite(or_l) else None
    per = []
    for i, b in enumerate(bars):
        med = clocks[i].get("median") if i < len(clocks) else None
        rng = b.get("range")
        per.append(
            {
                "t1": b.get("t1"),
                "range": rng,
                "range_over_same_clock": (float(rng) / float(med)) if _finite(rng) and _finite(med) and float(med) > 0 else None,
                "body_over_range": b.get("body_over_range"),
                "direction": b.get("direction"),
                "clock_obs": clocks[i].get("observation_n") if i < len(clocks) else None,
            }
        )
    net_atr = (abs(float(net)) / float(atr)) if _finite(net) and _finite(atr) and float(atr) > 0 else None
    gross_atr = (float(gross) / float(atr)) if gross and _finite(atr) and float(atr) > 0 else None
    or_atr = (float(or_rng) / float(atr)) if _finite(or_rng) and _finite(atr) and float(atr) > 0 else None
    layer = []
    if str(row.get("machine_SEED") or seed.get("seed") or "").startswith("FAILED_OPEN"):
        layer.append("FAILED_OPEN_overreach")
    if str(row.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED":
        if _finite(net_atr) and float(net_atr) < 0.40:
            layer.append("same_clock_or_TRUE_numeric_accepted_ordinary_move")
        else:
            layer.append("seed_taxonomy_TRUE_vs_human_FLAT")
    if str(row.get("location_t") or "") <= "09:15" and row.get("machine_THESIS_READY"):
        layer.append("location_timing_family_A_at_seed_bar")
    intent = dict(seed.get("intent") or {})
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "human_late": row.get("human_late"),
        "human_note": row.get("human_note"),
        "machine_SEED": row.get("machine_SEED") or seed.get("seed"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "location_t": row.get("location_t"),
        "location_family": row.get("location_family"),
        "atr20": atr,
        "mean_clock_median": clock.get("mean_clock_median"),
        "disp_over_scale": seed.get("disp_over_scale"),
        "max_range_over_own_clock": seed.get("max_range_over_own_clock"),
        "bars": per,
        "net": net,
        "gross": gross,
        "net_over_gross": (abs(float(net)) / float(gross)) if _finite(net) and gross else None,
        "net_over_atr20": net_atr,
        "gross_over_atr20": gross_atr,
        "opening_range_over_atr20": or_atr,
        "intent_ok": intent.get("ok"),
        "failing_layer": layer,
        "clock_obs": [p.get("clock_obs") for p in per],
    }


def flat_crawl_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = [scale_audit(_pick(rows, s, d)) for s, d in CASES]
    by = {(x.get("symbol"), x.get("date")): x for x in out}

    def judge(x: dict[str, Any], *, human_late: bool = False) -> str:
        net_atr = x.get("net_over_atr20")
        mx = x.get("max_range_over_own_clock")
        if human_late:
            return "label_ambiguity_or_timing"
        if x.get("machine_SEED") == "FAILED_OPEN_SEED":
            return "FAILED_OPEN_overreach_then_live_thesis"
        if _finite(net_atr) and float(net_atr) >= 0.50 and _finite(mx) and float(mx) >= 1.0:
            return "label_ambiguity_human_FLAT_on_a_visible_same_dir_move"
        if _finite(net_atr) and float(net_atr) < 0.35:
            return "machine_bug_same_clock_inflated_ordinary_move"
        return "mixed_taxonomy_disagreement"

    a8058 = by.get(("8058", "20250812")) or {}
    a8630 = by.get(("8630", "20250911")) or {}
    return {
        "rows": out,
        "8058_20250812": {**a8058, "verdict": judge(a8058)},
        "8630_20250911": {**a8630, "verdict": judge(a8630, human_late=True)},
        "do_not_lump": True,
        "threshold_changed": False,
    }
