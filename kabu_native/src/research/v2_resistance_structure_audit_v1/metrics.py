"""Descriptive resistance anatomy. No threshold search and no new rule."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

STRATEGY_SHA256 = "1d5ac587d4a67a7da4de9def64a6471428a136e4e30e1d26ca904983a8bcf505"
SIGNAL_SHA256 = "06345e9f7ddd2fcdf21a7beac49241495d3a4b6607715c879cf2ef0a307b4f68"
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18

EXPECTED_N = 11902
EXPECTED_PNL = 1189150.0
EXPECTED_PF = 1.409303686366296


def _pf(values: list[float]) -> Optional[float]:
    gain = sum(v for v in values if v > 0)
    loss = -sum(v for v in values if v < 0)
    if loss <= 0:
        return None if gain <= 0 else float("inf")
    return gain / loss


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(r["pnl_yen"]) for r in rows]
    bps = [float(r["bps"]) for r in rows]
    mfe = [float(r["mfe"]) for r in rows if r.get("mfe") == r.get("mfe")]
    mae = [float(r["mae"]) for r in rows if r.get("mae") == r.get("mae")]
    return {
        "trade_n": len(rows),
        "pnl_yen": float(sum(pnl)),
        "pf": _pf(pnl),
        "mean_bps": float(np.mean(bps)) if bps else None,
        "median_bps": float(np.median(bps)) if bps else None,
        "mfe_mean": float(np.mean(mfe)) if mfe else None,
        "mae_mean": float(np.mean(mae)) if mae else None,
        "ratchet_ge1": float(np.mean([int(r["ratchets"]) >= 1 for r in rows])) if rows else None,
        "ratchet_ge2": float(np.mean([int(r["ratchets"]) >= 2 for r in rows])) if rows else None,
    }


def _spear(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 30:
        return None
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if float(np.std(rx)) == 0 or float(np.std(ry)) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _pair(rows: list[dict[str, Any]], key: str, outcome: str) -> Optional[float]:
    xs, ys = [], []
    for row in rows:
        left = row.get(key)
        right = row.get(outcome)
        if left is None or right is None or left != left or right != right:
            continue
        xs.append(float(left))
        ys.append(float(right))
    return _spear(xs, ys)


def _deciles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = [r for r in rows if r.get("headroom_ticks") is not None]
    if len(usable) < 10:
        return []
    ordered = sorted(usable, key=lambda r: float(r["headroom_ticks"]))
    out = []
    size = len(ordered) / 10.0
    for i in range(10):
        chunk = ordered[int(i * size):int((i + 1) * size)]
        out.append({"decile": i + 1, **_econ(chunk)})
    return out


def _folds(rows: list[dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
    chunks = [dates[i * len(dates) // 3:(i + 1) * len(dates) // 3] for i in range(3)]
    out = []
    for i, chunk in enumerate(chunks, start=1):
        chosen = [r for r in rows if r["date"] in chunk]
        multi = [r for r in chosen if r.get("touch_class") in ("TWO_TOUCHES", "THREE_PLUS_TOUCHES")]
        thin = [r for r in chosen if r.get("touch_class") in ("NO_PRIOR_TOUCH", "ONE_TOUCH")]
        out.append({
            "fold": i,
            "multi_touch": _econ(multi),
            "thin_touch": _econ(thin),
            "headroom_bps": _pair(chosen, "headroom_ticks", "bps"),
            "headroom_mfe": _pair(chosen, "headroom_ticks", "mfe"),
        })
    return out


def _supported(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, bool]:
    def _delta(sample: list[dict[str, Any]]) -> tuple[Optional[float], Optional[float], Optional[float]]:
        multi = [r for r in sample if r.get("touch_class") in ("TWO_TOUCHES", "THREE_PLUS_TOUCHES")]
        thin = [r for r in sample if r.get("touch_class") in ("NO_PRIOR_TOUCH", "ONE_TOUCH")]
        if len(multi) < 30 or len(thin) < 30:
            return None, None, None
        return (
            float(np.mean([r["bps"] for r in multi]) - np.mean([r["bps"] for r in thin])),
            float(np.mean([r["mfe"] for r in multi if r["mfe"] == r["mfe"]]) - np.mean([r["mfe"] for r in thin if r["mfe"] == r["mfe"]])),
            float(np.mean([int(r["ratchets"]) >= 1 for r in multi]) - np.mean([int(r["ratchets"]) >= 1 for r in thin])),
        )

    quality_parts = [_delta(rows), _delta([r for r in rows if r["date"] in ORIGINAL18]), _delta([r for r in rows if r["date"] in EXTENSION17])]
    quality_parts.extend(_delta([r for r in rows if r["date"] in chunk]) for chunk in [dates[i * len(dates) // 3:(i + 1) * len(dates) // 3] for i in range(3)])
    quality_ok = all(part[0] is not None and part[0] > 0 and part[1] is not None and part[1] > 0 and part[2] is not None and part[2] > 0 for part in quality_parts)
    head_parts = []
    for sample in (rows, [r for r in rows if r["date"] in ORIGINAL18], [r for r in rows if r["date"] in EXTENSION17]):
        head_parts.append((_pair(sample, "headroom_ticks", "bps"), _pair(sample, "headroom_ticks", "mfe"), _pair(sample, "headroom_ticks", "ratchets")))
    head_ok = all(part[0] is not None and part[0] > 0 and part[1] is not None and part[1] > 0 and part[2] is not None and part[2] > 0 for part in head_parts)
    return {"quality": bool(quality_ok), "headroom": bool(head_ok)}


def build(raw: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in raw["baseline"] if r.get("pnl_yen") is not None]
    for row in rows:
        row["immediate"] = 1.0 if float(row["exit_t"]) - float(row["entry_t"]) < 0.1 else 0.0
    pnl = [float(r["pnl_yen"]) for r in rows]
    pf = _pf(pnl)
    parity = len(rows) == EXPECTED_N and abs(sum(pnl) - EXPECTED_PNL) < 1e-6 and pf is not None and abs(pf - EXPECTED_PF) < 1e-12
    classes = {}
    for name in ("NO_PRIOR_TOUCH", "ONE_TOUCH", "TWO_TOUCHES", "THREE_PLUS_TOUCHES"):
        classes[name] = _econ([r for r in rows if r.get("touch_class") == name])
    ratchet_groups = {}
    for name, pred in (
        ("RATCHET_0", lambda r: int(r["ratchets"]) == 0),
        ("RATCHET_1", lambda r: int(r["ratchets"]) == 1),
        ("RATCHET_2", lambda r: int(r["ratchets"]) == 2),
        ("RATCHET_3", lambda r: int(r["ratchets"]) == 3),
        ("RATCHET_4_PLUS", lambda r: int(r["ratchets"]) >= 4),
    ):
        chosen = [r for r in rows if pred(r)]
        ratchet_groups[name] = {**_econ(chosen), "structured_fraction": float(np.mean([r.get("status") == "STRUCTURED_ZONE" for r in chosen])) if chosen else None}
    conversion = {name: _econ([r for r in rows if r.get("conversion") == name]) for name in ("CLEAN_HOLD", "RETEST_AND_HOLD", "FAILED_SUPPORT", "UNRESOLVED", "UNSTRUCTURED")}
    ge2 = [r for r in rows if int(r["ratchets"]) >= 2]
    fail = [r for r in rows if float(r["exit_t"]) - float(r["entry_t"]) < 0.1]
    known = [r for r in rows if r.get("next_status") == "KNOWN"]
    flags = _supported(rows, list(raw["dates"])) if parity else {"quality": False, "headroom": False}
    conv_ge2 = float(np.mean([r.get("conversion") in ("CLEAN_HOLD", "RETEST_AND_HOLD") for r in ge2])) if ge2 else None
    conv_0 = float(np.mean([r.get("conversion") in ("CLEAN_HOLD", "RETEST_AND_HOLD") for r in rows if int(r["ratchets"]) == 0])) if rows else None
    support_ok = conv_ge2 is not None and conv_0 is not None and conv_ge2 > conv_0 + 0.10 and conversion["CLEAN_HOLD"]["mean_bps"] is not None and conversion["FAILED_SUPPORT"]["mean_bps"] is not None and conversion["CLEAN_HOLD"]["mean_bps"] > conversion["FAILED_SUPPORT"]["mean_bps"]
    if flags["quality"] and flags["headroom"]:
        verdict, nxt = "V2_RESISTANCE_AND_HEADROOM_MECHANISM_SUPPORTED_V1", "PRECOMMIT_V3_CANDIDATE_LATER"
    elif flags["quality"]:
        verdict, nxt = "V2_RESISTANCE_QUALITY_MECHANISM_SUPPORTED_V1", "PRECOMMIT_V3_CANDIDATE_LATER"
    elif flags["headroom"]:
        verdict, nxt = "V2_HEADROOM_MECHANISM_SUPPORTED_V1", "PRECOMMIT_V3_CANDIDATE_LATER"
    else:
        verdict, nxt = "V2_RESISTANCE_STRUCTURE_NOT_EXPLANATORY_V1", "KEEP_V2_UNCHANGED"
    return {
        "study": "V2_RESISTANCE_STRUCTURE_AUDIT_V1",
        "verdict": verdict,
        "next": nxt,
        "support_conversion_mechanism_supported": bool(support_ok and (flags["quality"] or flags["headroom"])),
        "v3_justified": bool(flags["quality"] or flags["headroom"]),
        "parity": parity,
        "trade_n": len(rows),
        "pnl_yen": float(sum(pnl)),
        "pf": pf,
        "strategy_sha256": STRATEGY_SHA256,
        "signal_sha256": SIGNAL_SHA256,
        "exit_sha256": None,
        "classes": classes,
        "spearman": {
            "touch_bps": _pair(rows, "touch_n", "bps"),
            "touch_mfe": _pair(rows, "touch_n", "mfe"),
            "touch_ratchet": _pair(rows, "touch_n", "ratchets"),
            "rejection_bps": _pair(rows, "rejection_n", "bps"),
            "rejection_mfe": _pair(rows, "rejection_n", "mfe"),
            "rejection_ratchet": _pair(rows, "rejection_n", "ratchets"),
            "pull_bps": _pair(rows, "pull3", "bps"),
            "pull_mfe": _pair(rows, "pull3", "mfe"),
            "pull_ratchet": _pair(rows, "pull3", "ratchets"),
            "head_bps": _pair(known, "headroom_ticks", "bps"),
            "head_mfe": _pair(known, "headroom_ticks", "mfe"),
            "head_ratchet": _pair(known, "headroom_ticks", "ratchets"),
            "head_fail": _pair(known, "headroom_ticks", "immediate"),
        },
        "known_next_n": len(known),
        "open_above_n": sum(1 for r in rows if r.get("next_status") == "OPEN_ABOVE"),
        "deciles": _deciles(rows),
        "conversion": conversion,
        "ratchet_groups": ratchet_groups,
        "ratchet_ge2": {
            **_econ(ge2),
            "successive_structured_fraction": float(np.mean([int(r.get("distinct_structured_zones") or 0) >= 2 for r in ge2])) if ge2 else None,
            "structured_fraction": float(np.mean([r.get("status") == "STRUCTURED_ZONE" for r in ge2])) if ge2 else None,
            "touch_distribution": dict(Counter(str(r.get("touch_class")) for r in ge2)),
            "headroom_median": float(np.median([r["headroom_ticks"] for r in ge2 if r.get("headroom_ticks") is not None])) if any(r.get("headroom_ticks") is not None for r in ge2) else None,
            "known_fraction": float(np.mean([r.get("next_status") == "KNOWN" for r in ge2])) if ge2 else None,
        },
        "immediate": {
            **_econ(fail),
            "structured_fraction": float(np.mean([r.get("status") == "STRUCTURED_ZONE" for r in fail])) if fail else None,
            "touch_distribution": dict(Counter(str(r.get("touch_class")) for r in fail)),
            "headroom_median": float(np.median([r["headroom_ticks"] for r in fail if r.get("headroom_ticks") is not None])) if any(r.get("headroom_ticks") is not None for r in fail) else None,
            "known_fraction": float(np.mean([r.get("next_status") == "KNOWN" for r in fail])) if fail else None,
        },
        "original18": _econ([r for r in rows if r["date"] in ORIGINAL18]),
        "extension17": _econ([r for r in rows if r["date"] in EXTENSION17]),
        "folds": _folds(rows, list(raw["dates"])),
        "quality_explanatory": flags["quality"],
        "headroom_explanatory": flags["headroom"],
        "entry_changed": False,
        "exit_changed": False,
        "complete_strategy_changed": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "new_data_acquired": False,
        "rows": rows,
        "charts": raw.get("charts", []),
    }
