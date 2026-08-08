"""Quality-layer BASE aggregation (CORE_VALID separate from PARTIAL / STRESS)."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


QUALITY_CLASSES = (
    "CORE_VALID",
    "PARTIAL_VALID_WINDOW",
    "STRESS_RECOVERABLE",
    "INVALID_SOURCE",
)

QUALITY_LAYERS = (
    "CORE_VALID",
    "PARTIAL_VALID_WINDOW",
    "STRESS_RECOVERABLE",
    "ALL_USABLE",
)

# Trades / windows counted in ALL_USABLE — INVALID_SOURCE explicitly excluded
ALL_USABLE_CLASSES = frozenset(
    {"CORE_VALID", "PARTIAL_VALID_WINDOW", "STRESS_RECOVERABLE"}
)


def include_in_core_base(quality_class: str) -> bool:
    """CORE economic BASE includes ONLY CORE_VALID windows/days.

    PARTIAL_VALID_WINDOW must NEVER be added into CORE_VALID aggregates.
    INVALID_SOURCE is never CORE.
    """
    return quality_class == "CORE_VALID"


def _trade_quality(t: Mapping[str, Any], day: str, day_quality: Mapping[str, str]) -> str:
    qc = t.get("quality_class")
    if qc:
        return str(qc)
    return str(day_quality.get(day, "UNKNOWN"))


def layer_trades(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    day_quality: Mapping[str, str],
) -> dict[str, list[Mapping[str, Any]]]:
    """Split completed trades into quality layers.

    CORE_VALID empty => caller should mark metrics NOT_EVALUABLE (not invent 0 PASS).
    INVALID_SOURCE is excluded from ALL_USABLE.
    UNKNOWN is never treated as usable and never invented as 0 PnL PASS.
    """
    core: list[Mapping[str, Any]] = []
    partial: list[Mapping[str, Any]] = []
    stress: list[Mapping[str, Any]] = []
    invalid: list[Mapping[str, Any]] = []
    all_usable: list[Mapping[str, Any]] = []
    for day, trades in trades_by_day.items():
        for t in trades:
            qc = _trade_quality(t, day, day_quality)
            if qc == "CORE_VALID":
                core.append(t)
            elif qc == "PARTIAL_VALID_WINDOW":
                partial.append(t)
            elif qc == "STRESS_RECOVERABLE":
                stress.append(t)
            elif qc == "INVALID_SOURCE":
                invalid.append(t)
            if qc in ALL_USABLE_CLASSES:
                all_usable.append(t)
    return {
        "CORE_VALID": core,
        "PARTIAL_VALID_WINDOW": partial,
        "STRESS_RECOVERABLE": stress,
        "INVALID_SOURCE": invalid,
        "ALL_USABLE": all_usable,
    }


def summarize_quality_layers(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    day_quality: Mapping[str, str],
    *,
    summarize_pnls,
) -> dict[str, Any]:
    layers = layer_trades(trades_by_day, day_quality)
    out: dict[str, Any] = {
        "banner_note": "PARTIAL must not be labeled CORE; INVALID_SOURCE excluded from ALL_USABLE"
    }
    for name in QUALITY_LAYERS:
        trades = list(layers[name])
        pnls = [float(t["net_pnl_yen_100"]) for t in trades]
        if name == "CORE_VALID" and not trades:
            out[name] = {
                "status": "NOT_EVALUABLE",
                "reason": "CORE_VALID windows == 0 in Source Manifest",
                "trades_n": 0,
                "pnl": None,
                "metrics": None,
                "include_in_core_base": True,
            }
            continue
        metrics = summarize_pnls(pnls)
        out[name] = {
            "status": "OK",
            "trades_n": len(trades),
            "pnl": metrics["pnl"],
            "metrics": metrics,
            "include_in_core_base": name == "CORE_VALID",
        }
    out["INVALID_SOURCE"] = {
        "status": "EXCLUDED",
        "trades_n": len(layers["INVALID_SOURCE"]),
        "pnl": None,
        "metrics": None,
        "include_in_core_base": False,
        "note": "INVALID_SOURCE excluded from ALL_USABLE and CORE",
    }
    # Cross-check helper: do not rename PARTIAL aggregate as CORE
    out["naming_guard"] = {
        "forbid_calling_partial_core": True,
        "partial_is_not_core": True,
        "invalid_excluded_from_all_usable": True,
    }
    return out


def window_quality_counts(windows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in QUALITY_CLASSES}
    counts["OTHER"] = 0
    for w in windows:
        qc = str(w.get("quality_class") or "OTHER")
        if qc in counts:
            counts[qc] += 1
        else:
            counts["OTHER"] += 1
    counts["ALL_USABLE"] = (
        counts.get("CORE_VALID", 0)
        + counts.get("PARTIAL_VALID_WINDOW", 0)
        + counts.get("STRESS_RECOVERABLE", 0)
    )
    # INVALID_SOURCE is counted above but NOT in ALL_USABLE
    return counts


def window_quality_map(windows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], str]:
    """(day, AM|PM) -> quality_class from Source Manifest windows."""
    out: dict[tuple[str, str], str] = {}
    for w in windows:
        day = str(w.get("day") or "")
        am_pm = str(w.get("am_pm") or "")
        if day and am_pm:
            out[(day, am_pm)] = str(w.get("quality_class") or "UNKNOWN")
    return out


def day_quality_from_windows(windows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Derive a day-level quality label from AM/PM window classes.

    Preference for day-level summary (not for trade tagging):
    CORE > PARTIAL > STRESS > INVALID. Trade tagging must use window AM/PM.
    """
    rank = {
        "CORE_VALID": 3,
        "PARTIAL_VALID_WINDOW": 2,
        "STRESS_RECOVERABLE": 1,
        "INVALID_SOURCE": 0,
        "UNKNOWN": -1,
    }
    by_day: dict[str, list[str]] = {}
    for w in windows:
        day = str(w.get("day") or "")
        if not day:
            continue
        by_day.setdefault(day, []).append(str(w.get("quality_class") or "UNKNOWN"))
    out: dict[str, str] = {}
    for day, qcs in by_day.items():
        usable = [q for q in qcs if q in ALL_USABLE_CLASSES]
        if usable:
            out[day] = max(usable, key=lambda q: rank.get(q, -1))
        else:
            out[day] = max(qcs, key=lambda q: rank.get(q, -1)) if qcs else "UNKNOWN"
    return out
