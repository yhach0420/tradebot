"""Semantic calibration records. Human labels + causal descriptors only. No future return."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_machine_implementation import (
    E1_BODY_N1M,
    E1_NET_N1M,
    E1_RANGE_N1M,
    S0_ATR_RANGE_MIN,
    S0_GAP_ATR_MIN,
    S0_GAP_RANGE_MIN,
    S0_RANGE_MIN,
    TRUE_BODY_FRAC_MIN,
    TRUE_COUNTER_FRAC,
    TRUE_DISP_MIN,
    TRUE_N_SAME_MIN,
    TRUE_RANGE_MIN,
)
from research.pb1_v4_machine_implementation.isolation import RCA_CACHE
from research.pb1_v4_machine_implementation.s0 import classify_s0
from research.pb1_v4_opening_drive_location_reaccel_spec import INVALID_OPENING_STATES, VALID_OPENING_STATES

S0_CANDIDATES = (
    {
        "id": "C1",
        "rule": f"max first-three 5m range / NORMAL_OPENING_5M_RANGE >= {S0_RANGE_MIN}",
        "kept": True,
        "why": "Primary: actual price movement versus the stock's own normal opening 5m bar.",
    },
    {
        "id": "C2",
        "rule": f"|gap|/ATR20 >= {S0_GAP_ATR_MIN} AND max range/normal >= {S0_GAP_RANGE_MIN}",
        "kept": True,
        "why": "Gap assist only when the opening 5m still shows visible range. Gap without movement fails.",
    },
    {
        "id": "C3",
        "rule": "cross-sectional TradingValue/xs rank alone",
        "kept": False,
        "why": "Cross-sectional activity alone can never pass S0.",
    },
    {
        "id": "C4",
        "rule": "own-clock TradingValue elevated on a visually flat stock",
        "kept": False,
        "why": "TradingValue alone on a flat stock can never pass S0.",
    },
    {
        "id": "C5",
        "rule": "reuse old GENUINELY_IN_PLAY as final truth",
        "kept": False,
        "why": "RCA: 24 technically in-play but visually ordinary. Too broad.",
    },
)


def _confusion(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for h, m in pairs if h and m)
    tn = sum(1 for h, m in pairs if (not h) and (not m))
    fp = sum(1 for h, m in pairs if (not h) and m)
    fn = sum(1 for h, m in pairs if h and (not m))
    n = len(pairs)
    return {
        "n": n,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": (tp + tn) / n if n else None,
        "note": "DEVELOPMENT FIT ONLY. Not face validation.",
    }


def calibrate_from_descriptors() -> dict[str, Any]:
    path = RCA_CACHE / "descriptor_slim.json"
    rows = json.loads(Path(path).read_text(encoding="utf-8")) if path.is_file() else []
    s0_pairs: list[tuple[bool, bool]] = []
    s1_pairs: list[tuple[bool, bool]] = []
    s1_state = Counter()
    for r in rows:
        lab = HUMAN_LABELS.get(int(r.get("rca_id") or 0)) or {}
        o = dict(r.get("opening_5m") or {})
        human_s0 = str(lab.get("sp_opening_state") or "") != "FLAT_OR_CRAWL"
        mx = o.get("max_range_over_normal_5m")
        # Reconstruct minimal 5m sequence stats via a synthetic 3-bar list is not available here.
        # S0 uses max range + gap only from persisted descriptors.
        s0 = classify_s0(
            bars_open=[
                {
                    "o": 100.0,
                    "h": 100.0 + float(mx or 0.0),
                    "l": 100.0,
                    "c": 100.0 + float(mx or 0.0) * 0.5,
                    "range": float(mx or 0.0),
                    "body": float(mx or 0.0) * 0.5,
                    "body_over_range": 0.5,
                    "direction": 1,
                    "net": float(mx or 0.0) * 0.5,
                    "t0": "09:00",
                    "t1": "09:04",
                }
            ]
            * 3
            if _finite(mx)
            else [],
            normal_opening_5m=1.0 if _finite(mx) else None,
            abs_gap_atr=r.get("abs_gap_atr"),
            xs_rank_pct=None,
            tv_0915_pctl=None,
        )
        s0_pairs.append((human_s0, bool(s0.get("ok"))))
        human_s1 = str(lab.get("sp_opening_state") or "") in VALID_OPENING_STATES
        # Descriptor-level S1 uses persisted sequence fields, not OR-half.
        disp = o.get("net_displacement_over_normal_5m")
        n_same = int(o.get("n_same_dir_5m") or 0)
        ctr = o.get("largest_counter_over_normal_5m") or 0.0
        body = o.get("mean_body_over_range")
        mxv = o.get("max_range_over_normal_5m")
        true_ok = (
            _finite(disp)
            and float(disp) >= float(TRUE_DISP_MIN)
            and _finite(mxv)
            and float(mxv) >= float(TRUE_RANGE_MIN)
            and n_same >= int(TRUE_N_SAME_MIN)
            and float(ctr) <= float(TRUE_COUNTER_FRAC) * float(disp)
            and _finite(body)
            and float(body) >= float(TRUE_BODY_FRAC_MIN)
        )
        fail_seed = _finite(ctr) and float(ctr) >= 0.80 and str(o.get("sequence") or "") in ("reversal", "two-sided")
        machine_s1 = bool(true_ok or fail_seed)
        s1_pairs.append((human_s1, machine_s1))
        s1_state[str(lab.get("sp_opening_state") or "")] += 1

    return {
        "semantic_development_data_n": len(rows),
        "not_face_validation": True,
        "not_holdout": True,
        "future_return_used": False,
        "S0": {
            "candidates_considered": list(S0_CANDIDATES),
            "chosen_rule": (
                f"(max opening 5m range / NORMAL_OPENING_5M_RANGE >= {S0_RANGE_MIN}) OR "
                f"(|gap|/ATR20 >= {S0_GAP_ATR_MIN} AND max range/normal >= {S0_GAP_RANGE_MIN}) OR "
                f"(own normal not yet available AND max range/ATR20 >= {S0_ATR_RANGE_MIN})"
            ),
            "why_chosen": (
                "Separates actually-moving-today from technically-in-play but visually ordinary. "
                "Requires price movement vs own normal. xs/TV/old IN-PLAY never sufficient."
            ),
            "confusion_vs_not_FLAT": _confusion(s0_pairs),
        },
        "S1": {
            "chosen_rule": (
                f"TRUE: disp/normal>={TRUE_DISP_MIN}, max_range/normal>={TRUE_RANGE_MIN}, "
                f"n_same>={TRUE_N_SAME_MIN}, counter<={TRUE_COUNTER_FRAC}*disp, body/range>={TRUE_BODY_FRAC_MIN}. "
                f"FAILED_OPEN: visible counter 5m then opposite auction measured from the failed-open extreme, not session open. OR-half unused."
            ),
            "why_chosen": (
                "Encodes the 5m taxonomy. Tiny first-5m dip + OR-half cannot pass. "
                "3382 is the failed-open exemplar, not a numeric template."
            ),
            "descriptor_confusion_pass_vs_human_valid_states": _confusion(s1_pairs),
            "human_state_counts": dict(s1_state),
            "or_half_in_rule": False,
            "note": "FAILED_OPEN extension uses later 5m bars in the walk; descriptor confusion is first-three only.",
        },
        "E1": {
            "chosen_rule": (
                f"3-bar dir net / N1M >= {E1_NET_N1M} AND range/N1M >= {E1_RANGE_N1M} "
                f"AND body/N1M >= {E1_BODY_N1M} AND body>=opp wick AND micro-cross last"
            ),
            "why_chosen": (
                "1.0 N1M is one normal 1-minute of directional progress: distinguishable from that stock's own noise. "
                "Not copied from RCA valid medians (2.34 / ~2 / 1.5). Forbidden median-copy gates unused."
            ),
            "rca_median_copied": False,
            "tv_gated": False,
        },
        "invalid_opening_states": list(INVALID_OPENING_STATES),
        "valid_opening_states": list(VALID_OPENING_STATES),
    }
