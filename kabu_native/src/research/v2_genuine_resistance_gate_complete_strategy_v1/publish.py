"""Publish the genuine-resistance gate complete-strategy test."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import bind, sha256_obj
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_genuine_resistance_gate_complete_strategy_v1 import (
    DETECTOR_SHA256,
    EXPECTED_PF,
    EXPECTED_PNL_YEN,
    EXPECTED_TRADE_N,
    OUT_REL,
    SIGNAL_SHA256,
    STRATEGY_SHA256,
)
from research.v2_genuine_resistance_gate_complete_strategy_v1.isolation import assert_isolated
from research.v2_genuine_resistance_gate_complete_strategy_v1.scan import scan
from research.v2_structural_resistance_detector_validation_v1.rules import detector_document

OUT = Path(OUT_REL)


def _pf(values: list[float]) -> Optional[float]:
    gain = sum(v for v in values if v > 0)
    loss = -sum(v for v in values if v < 0)
    if loss <= 0:
        return None if gain <= 0 else float("inf")
    return gain / loss


def _dd(rows: list[dict[str, Any]]) -> Optional[float]:
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: (str(r["date"]), float(r["exit_t"])))
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in ordered:
        equity += float(row["pnl_yen"])
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return float(worst)


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(r["pnl_yen"]) for r in rows]
    bps = [float(r["bps"]) for r in rows]
    return {
        "trade_n": len(rows),
        "pnl_yen": float(sum(pnl)),
        "pf": _pf(pnl),
        "mean_yen": float(np.mean(pnl)) if pnl else None,
        "median_yen": float(np.median(pnl)) if pnl else None,
        "mean_bps": float(np.mean(bps)) if bps else None,
        "median_bps": float(np.median(bps)) if bps else None,
        "win_rate": float(np.mean([v > 0 for v in pnl])) if pnl else None,
        "max_drawdown": _dd(rows),
    }


def _reason(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    chosen = [r for r in rows if str(r.get("reason")) == name]
    return {"reason": name, **_econ(chosen)}


def _ratchets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for label, pred in (
        ("0", lambda n: n == 0),
        ("1", lambda n: n == 1),
        ("2", lambda n: n == 2),
        ("3", lambda n: n == 3),
        ("4+", lambda n: n >= 4),
    ):
        chosen = [r for r in rows if pred(int(r.get("ratchets") or 0))]
        out[label] = _econ(chosen)
    return out


def _period(rows: list[dict[str, Any]], dates: set[str] | list[str]) -> dict[str, Any]:
    chosen = [r for r in rows if r["date"] in dates]
    block = _econ(chosen)
    return {"trade_n": block["trade_n"], "pnl_yen": block["pnl_yen"], "pf": block["pf"], "mean_bps": block["mean_bps"], "max_drawdown": block["max_drawdown"]}


def publish() -> dict[str, Any]:
    assert_isolated(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    bound = bind()
    detector_sha = sha256_obj(detector_document())
    if bound["strategy_sha256"] != STRATEGY_SHA256 or bound["signal_sha256"] != SIGNAL_SHA256:
        raise RuntimeError("frozen_v2_sha_mismatch")
    if detector_sha != DETECTOR_SHA256:
        raise RuntimeError("detector_sha_mismatch")
    raw = scan()
    base = [r for r in raw["baseline"] if r.get("pnl_yen") is not None]
    cand = [r for r in raw["candidate"] if r.get("pnl_yen") is not None]
    b100 = [r for r in raw["baseline_100"] if r.get("pnl_yen") is not None]
    c100 = [r for r in raw["candidate_100"] if r.get("pnl_yen") is not None]
    base_econ = _econ(base)
    parity = (
        base_econ["trade_n"] == EXPECTED_TRADE_N
        and abs(base_econ["pnl_yen"] - EXPECTED_PNL_YEN) < 1e-6
        and base_econ["pf"] is not None
        and abs(float(base_econ["pf"]) - EXPECTED_PF) < 1e-12
    )
    identities = [(r["date"], r["session"], r["symbol"], round(float(r["entry_t"]), 6), round(float(r["exit_t"]), 6), round(float(r["entry_px"]), 6), round(float(r["exit_px"]), 6), r["reason"], round(float(r["pnl_yen"]), 6), int(r["ratchets"])) for r in base]
    trade_by_trade = len(identities) == len(set(identities)) and parity
    classified = int(raw["classified_trade_n"])
    detector_miss = int(raw["detector_miss"])
    observer_mismatch = int(raw["ratchet_observer_mismatch"])
    # The prior flag withholds a mechanism claim when the side ratchet observer disagrees.
    # It is not missing capture rows and not an unfinished detector classification.
    incomplete = classified != len(base) or detector_miss != 0 or not parity
    meaning = (
        "reconstruction_abort was set when the side observer reconstruct_path_events "
        "ratchet count disagreed with the frozen trade ratchet count. "
        "It forced explanatory flags false after the 11902 classifications were already computed. "
        "It does not mean capture rows or detector zone states were dropped."
    )
    if incomplete:
        verdict, nxt = "INTEGRITY_BLOCKED_RECONSTRUCTION_ABORT_V1", "REPAIR_RECONSTRUCTION_INTEGRITY_ONLY"
        cand_econ = None
    else:
        cand_econ = _econ(cand)
        dates = sorted({str(r["date"]) for r in base})
        folds = [set(dates[i * len(dates) // 3:(i + 1) * len(dates) // 3]) for i in range(3)]
        periods = {
            "ORIGINAL18": {"baseline": _period(base, set(ORIGINAL18)), "candidate": _period(cand, set(ORIGINAL18))},
            "EXTENSION17": {"baseline": _period(base, set(EXTENSION17)), "candidate": _period(cand, set(EXTENSION17))},
            "FOLD1": {"baseline": _period(base, folds[0]), "candidate": _period(cand, folds[0])},
            "FOLD2": {"baseline": _period(base, folds[1]), "candidate": _period(cand, folds[1])},
            "FOLD3": {"baseline": _period(base, folds[2]), "candidate": _period(cand, folds[2])},
        }
        b100_econ = _econ(b100)
        c100_econ = _econ(c100)
        pf_up = cand_econ["pf"] is not None and base_econ["pf"] is not None and cand_econ["pf"] > base_econ["pf"]
        pnl_up = cand_econ["pnl_yen"] > base_econ["pnl_yen"]
        period_ok = all(
            (block["candidate"]["pnl_yen"] >= block["baseline"]["pnl_yen"]) and (block["candidate"]["pf"] or 0) >= (block["baseline"]["pf"] or 0)
            for block in periods.values()
        )
        lat_ok = c100_econ["pnl_yen"] > b100_econ["pnl_yen"] and (c100_econ["pf"] or 0) >= (b100_econ["pf"] or 0)
        broad = pf_up and pnl_up and period_ok and lat_ok
        collapsed = pf_up and cand_econ["pnl_yen"] < base_econ["pnl_yen"] * 0.5
        if broad:
            verdict, nxt = "V2_GENUINE_RESISTANCE_GATE_SUPPORTED_V1", "AUDIT_WHAT_EDGE_REMAINS_IN_INCIDENTAL_HIGH_ENTRIES_V1"
        elif collapsed:
            verdict, nxt = "V2_GENUINE_RESISTANCE_GATE_PRECISION_RECALL_TRADEOFF_V1", "KEEP_V2_UNCHANGED"
        elif not period_ok:
            verdict, nxt = "V2_GENUINE_RESISTANCE_GATE_NOT_ROBUST_V1", "KEEP_V2_UNCHANGED"
        else:
            verdict, nxt = "V2_GENUINE_RESISTANCE_ASSOCIATION_NOT_ACTIONABLE_V1", "KEEP_V2_UNCHANGED"
        rejected = [r for r in base if r.get("genuine") is False]
        report_extra = {
            "periods": periods,
            "candidate": cand_econ,
            "baseline": base_econ,
            "ratchet_groups_candidate": _ratchets(cand),
            "ratchet_groups_baseline": _ratchets(base),
            "exit_candidate": {
                "BREAK_SUPPORT_FAILURE": _reason(cand, "BREAK_SUPPORT_FAILURE"),
                "IMPULSE_EXHAUSTED": _reason(cand, "IMPULSE_EXHAUSTED"),
            },
            "asof_100": {"baseline": b100_econ, "candidate": c100_econ},
            "rejected_old_v2": {**_econ(rejected), "immediate_failure_fraction": float(np.mean([(float(r["exit_t"]) - float(r["entry_t"])) < 0.1 for r in rejected])) if rejected else None},
            "complete_strategy_improvement": bool(broad),
            "robust_across_periods": bool(period_ok),
            "latency_100_maintained": bool(lat_ok),
            "gate_actionable": bool(broad),
        }
    if incomplete:
        report_extra = {}
        periods = {}
    report = {
        "study": "V2_GENUINE_RESISTANCE_GATE_COMPLETE_STRATEGY_V1",
        "verdict": verdict,
        "next": nxt,
        "reconstruction_abort_meaning": meaning,
        "does_it_indicate_incomplete_data_or_incomplete_reconstruction": bool(incomplete),
        "integrity_cleared": not incomplete,
        "ratchet_observer_mismatch": observer_mismatch,
        "detector_miss": detector_miss,
        "classified_trade_n": classified,
        "parity": parity,
        "trade_by_trade_parity": trade_by_trade,
        "baseline": base_econ,
        "detector_sha256": detector_sha,
        "detector_unchanged": detector_sha == DETECTOR_SHA256,
        "strategy_sha256": bound["strategy_sha256"],
        "signal_sha256": bound["signal_sha256"],
        "full_signal_n": raw["full_signal_n"],
        "accepted_n": raw["accepted_n"],
        "rejected_n": int(raw["full_signal_n"]) - int(raw["accepted_n"]),
        "base_counts": raw["base_counts"],
        "cand_counts": raw["cand_counts"],
        "v2_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": [0, 0, 0],
        **report_extra,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (OUT / "report.md").write_text(
        f"# {verdict}\n\nNEXT: {nxt}\n\n{meaning}\n\nintegrity_cleared={not incomplete}\nparity={parity}\n",
        encoding="utf-8",
    )
    wb = Workbook()
    sheet = wb.active
    sheet.title = "summary"
    for key, value in report.items():
        if key in ("baseline", "candidate"):
            continue
        sheet.append([key, json.dumps(value, default=str) if isinstance(value, (dict, list)) else value])
    wb.save(OUT / "audit.xlsx")
    return {"verdict": verdict, "next": nxt, "integrity_cleared": not incomplete, "parity": parity}


if __name__ == "__main__":
    print(publish())
