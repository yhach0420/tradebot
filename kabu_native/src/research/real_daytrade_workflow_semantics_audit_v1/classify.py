"""Human-semantic labels. Filled after blinded chart review. No outcome used."""
from __future__ import annotations

from typing import Any

# Populated after viewing charts with future hidden. Keys: sample_id int.
HUMAN_LABELS: dict[int, dict[str, str]] = {
    1: {"class": "NOT_THE_INTENDED_SETUP", "name": "range"},
    2: {"class": "QUESTIONABLE", "name": "mature trend"},
    3: {"class": "NOT_THE_INTENDED_SETUP", "name": "reversal"},
    4: {"class": "NOT_THE_INTENDED_SETUP", "name": "range"},
    5: {"class": "NOT_THE_INTENDED_SETUP", "name": "opening impulse"},
    6: {"class": "QUESTIONABLE", "name": "range"},
    7: {"class": "NOT_THE_INTENDED_SETUP", "name": "exhaustion"},
    8: {"class": "NOT_THE_INTENDED_SETUP", "name": "range"},
    9: {"class": "NOT_THE_INTENDED_SETUP", "name": "other"},
    10: {"class": "NOT_THE_INTENDED_SETUP", "name": "range"},
    11: {"class": "NOT_THE_INTENDED_SETUP", "name": "other"},
    12: {"class": "NOT_THE_INTENDED_SETUP", "name": "range"},
}


def classify_events(sampled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in sampled:
        sid = int(r.get("sample_id") or 0)
        human = dict(HUMAN_LABELS.get(sid) or {})
        struct = "QUESTIONABLE"
        name = "other"
        if r.get("family") == "mtf_5m_sma" and not r.get("daily_stack"):
            struct = "NOT_THE_INTENDED_SETUP"
            name = "range" if float(r.get("tod_min") or 0) > 120 else "opening impulse"
        elif r.get("family") == "sma_1m":
            struct = "NOT_THE_INTENDED_SETUP"
            name = "other"
        elif float(r.get("tod_min") or 0) < 20:
            struct = "QUESTIONABLE"
            name = "opening impulse"
        else:
            struct = "QUESTIONABLE"
            name = "pullback"
        row = dict(r)
        row["structured_class"] = struct
        row["structured_name"] = name
        row["human_class"] = human.get("class") or struct
        row["human_name"] = human.get("name") or name
        row["human_reviewed"] = sid in HUMAN_LABELS
        row["outcome_used_for_label"] = False
        out.append(row)
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    def share(key: str, val: str) -> float | None:
        if not n:
            return None
        return len([r for r in rows if r.get(key) == val]) / n
    intended = [r for r in rows if r.get("family") == "mtf_5m_sma"]
    ni = len(intended)
    realistic = len([r for r in intended if r.get("human_class") == "REALISTIC_DAYTRADE_SETUP"])
    return {
        "sample_n": n,
        "human_reviewed_n": len([r for r in rows if r.get("human_reviewed")]),
        "realistic_n": len([r for r in rows if r.get("human_class") == "REALISTIC_DAYTRADE_SETUP"]),
        "questionable_n": len([r for r in rows if r.get("human_class") == "QUESTIONABLE"]),
        "not_intended_n": len([r for r in rows if r.get("human_class") == "NOT_THE_INTENDED_SETUP"]),
        "mtf_n": ni,
        "mtf_realistic_share": (realistic / ni) if ni else None,
        "realistic_share": share("human_class", "REALISTIC_DAYTRADE_SETUP"),
        "would_trader_call_intended_pct": (100.0 * realistic / ni) if ni else None,
    }
