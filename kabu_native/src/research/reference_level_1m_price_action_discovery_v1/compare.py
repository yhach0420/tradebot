"""Primary mechanism comparisons. Path labels only; no PnL ranking."""
from __future__ import annotations

from typing import Any

from research.reference_level_1m_price_action_discovery_v1.playbooks import path_stats

PAIRS = (
    {
        "id": "PDH_BREAK_VS_ACCEPT2",
        "question": "Does previous-day-high break + accept differ from simple previous-day-high break?",
        "a": ("PDH", "BREAK_ABOVE", None),
        "b": ("PDH", "ACCEPT2_ABOVE", None),
    },
    {
        "id": "PDH_ACCEPT_VS_RETEST_HOLD",
        "question": "Does previous-day-high break + retest hold differ from break + acceptance?",
        "a": ("PDH", "ACCEPT2_ABOVE", None),
        "b": ("PDH", "RETEST_HOLD_ABOVE", None),
    },
    {
        "id": "PDH_BREAK_VS_FAILED_BREAK",
        "question": "Does previous-day-high break differ from break + immediate failure?",
        "a": ("PDH", "BREAK_ABOVE", None),
        "b": ("PDH", "FAILED_BREAK_ABOVE", None),
    },
    {
        "id": "GAP_FILL_RECLAIM_VS_FAILURE",
        "question": "Does gap fill + reclaim differ from gap fill + continuation through?",
        "a": ("GAP_UP_PDC", "GAP_FILL_RECLAIM", None),
        "b": ("GAP_UP_PDC", "GAP_FILL_FAILURE", None),
    },
    {
        "id": "VWAP_ISOLATED_VS_NEAR_PDH",
        "question": "Does VWAP reclaim near previous-day high differ from isolated VWAP reclaim?",
        "a": ("VWAP", "VWAP_RECLAIM", "isolated"),
        "b": ("VWAP", "VWAP_RECLAIM", "near_pdh"),
    },
    {
        "id": "OR15_ACCEPT_VS_FALSE_BREAK",
        "question": "Does opening-range break + acceptance differ from opening-range false break?",
        "a": ("OR15H", "ACCEPT2_ABOVE", None),
        "b": ("OR15L", "FAILED_BREAK_BELOW", None),
    },
    {
        "id": "D20H_ACCEPT_VS_PDH_ACCEPT",
        "question": "Does 20-day high acceptance differ from previous-day high acceptance?",
        "a": ("PDH", "ACCEPT2_ABOVE", None),
        "b": ("D20H", "ACCEPT2_ABOVE", None),
    },
)


def _filter(hits: list[dict[str, Any]], spec: tuple[str, str, str | None]) -> list[dict[str, Any]]:
    lid, kind, extra = spec
    out = [h for h in hits if str(h.get("level_id")) == lid and str(h.get("event_kind")) == kind]
    if extra == "near_pdh":
        out = [h for h in out if h.get("near_pdh")]
    elif extra == "isolated":
        out = [h for h in out if (not h.get("near_pdh"))]
    return out


def run_comparisons(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in PAIRS:
        a = path_stats(_filter(hits, p["a"]))
        b = path_stats(_filter(hits, p["b"]))
        ca = a.get("continuation_rate")
        cb = b.get("continuation_rate")
        sep = abs(float(ca) - float(cb)) if ca is not None and cb is not None else None
        improves = bool(sep is not None and sep >= 0.05 and min(int(a.get("event_n") or 0), int(b.get("event_n") or 0)) >= 80)
        rows.append(
            {
                "id": p["id"],
                "question": p["question"],
                "a_level_kind": f"{p['a'][0]}:{p['a'][1]}:{p['a'][2]}",
                "b_level_kind": f"{p['b'][0]}:{p['b'][1]}:{p['b'][2]}",
                "a": a,
                "b": b,
                "continuation_rate_gap": sep,
                "path_separation_improved": improves,
            }
        )
    return rows
