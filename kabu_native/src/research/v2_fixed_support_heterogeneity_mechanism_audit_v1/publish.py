"""Symbol-heterogeneity mechanism audit. Descriptors were fixed before this replay."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import sha256_obj
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_fixed_support_heterogeneity_mechanism_audit_v1.descriptors import (
    ESTABLISHED_TOP10,
    FAMILIES,
    self_check,
    spearman,
    thirds,
)
from research.v2_fixed_support_heterogeneity_mechanism_audit_v1.scan import scan

OUT = Path("results/research/v2_fixed_support_heterogeneity_mechanism_audit_v1")
BASE_N, BASE_PNL, BASE_PF = 11902, 1189150.0, 1.409303686366296
CAND_N, CAND_PNL, CAND_PF = 11446, 2520850.0, 1.7935186351045076
COMPLETE_DELTA = 1331700.0
MEDIAN_FIELDS = (
    "entry_price", "tick_size_bps", "spread_ticks", "spread_bps", "fresh_fraction",
    "range_30_bps", "range_60_bps", "variability_30_bps", "variability_60_bps",
    "events_10", "events_30", "classified_10", "classified_30", "vol_10", "vol_30", "ticks_10",
    "buy_participation", "break_bps", "ratchet_n", "time_to_first_ratchet", "ratchet_interval",
    "ratchet_step_ticks", "ratchet_step_bps", "max_gap_ticks", "max_gap_bps",
)


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    loss = sum(-float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) < 0)
    gain = sum(float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) > 0)
    return gain / loss if loss else None


def _key(row: dict[str, Any]) -> tuple:
    return (row["date"], row["session"], row["symbol"], int(row["signal_index"]))


def _med(values: list[Any]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and v == v]
    if not clean:
        return None
    return float(np.median(clean))


def _p75(values: list[Any]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and v == v]
    if not clean:
        return None
    return float(np.percentile(np.array(clean), 75))


def _component(base: dict[str, Any], other: dict[str, Any]) -> str:
    later = float(other["exit_t"]) > float(base["exit_t"]) + 1e-9
    if base["reason"] == "BREAK_SUPPORT_FAILURE" and other["reason"] == "IMPULSE_EXHAUSTED":
        return "bsf_soft"
    if base["reason"] == "BREAK_SUPPORT_FAILURE" and other["reason"] == "SESSION_FLAT":
        return "bsf_flat"
    if later and base["reason"] == "BREAK_SUPPORT_FAILURE" and other["reason"] not in ("IMPULSE_EXHAUSTED", "SESSION_FLAT") and float(other["exit_px"]) < float(base["exit_px"]):
        return "further"
    if later and base["reason"] == "BREAK_SUPPORT_FAILURE":
        return "other_later"
    return "other_matched"


def _symbols(base: list[dict[str, Any]], cand: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cand_by = {_key(row): row for row in cand}
    grouped: dict[str, dict[str, Any]] = {}
    for row in base:
        item = grouped.setdefault(row["symbol"], {"base": [], "cand": [], "parts": {}})
        item["base"].append(row)
    for row in cand:
        item = grouped.setdefault(row["symbol"], {"base": [], "cand": [], "parts": {}})
        item["cand"].append(row)
    for row in base:
        other = cand_by.get(_key(row))
        if other is None:
            continue
        kind = _component(row, other)
        bucket = grouped[row["symbol"]]["parts"].setdefault(kind, {"n": 0, "delta": 0.0})
        bucket["n"] += 1
        bucket["delta"] += float(other["pnl_yen"]) - float(row["pnl_yen"])
    out = []
    for symbol, item in grouped.items():
        b, c = item["base"], item["cand"]
        base_pnl = float(sum(float(r["pnl_yen"]) for r in b))
        cand_pnl = float(sum(float(r["pnl_yen"]) for r in c))
        parts = item["parts"]
        soft = parts.get("bsf_soft", {"n": 0, "delta": 0.0})
        flat = parts.get("bsf_flat", {"n": 0, "delta": 0.0})
        later = parts.get("other_later", {"n": 0, "delta": 0.0})
        further = parts.get("further", {"n": 0, "delta": 0.0})
        positive = max(soft["delta"], 0.0) + max(flat["delta"], 0.0) + max(later["delta"], 0.0)
        denom = abs(min(further["delta"], 0.0))
        base_n = len(b)
        row = {
            "symbol": symbol,
            "baseline_n": base_n,
            "candidate_n": len(c),
            "delta_yen": cand_pnl - base_pnl,
            "delta_per_trade": None if base_n == 0 else (cand_pnl - base_pnl) / base_n,
            "delta_bps": None if not b or not c else float(np.mean([r["bps"] for r in c]) - np.mean([r["bps"] for r in b])),
            "net_extension": soft["delta"] + flat["delta"] + later["delta"] + further["delta"],
            "bsf_soft_n": soft["n"],
            "bsf_soft_delta": soft["delta"],
            "further_n": further["n"],
            "further_delta": further["delta"],
            "recovery_to_drawdown": None if denom == 0 else positive / denom,
            "spread_bps_p75": _p75([r.get("spread_bps") for r in b]),
        }
        for field in MEDIAN_FIELDS:
            row[field] = _med([r.get(field) for r in b])
        step, spread, span = row["ratchet_step_bps"], row["spread_bps"], row["range_60_bps"]
        row["ratchet_step_to_spread"] = None if not step or not spread or spread <= 0 else step / spread
        row["ratchet_step_to_range"] = None if not step or not span or span <= 0 else step / span
        out.append(row)
    return out


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"symbol_n": 0, "delta_yen": 0.0, "median_symbol_delta": None, "median_delta_per_trade": None, "positive_fraction": None}
    return {
        "symbol_n": len(rows),
        "delta_yen": float(sum(r["delta_yen"] for r in rows)),
        "median_symbol_delta": _med([r["delta_yen"] for r in rows]),
        "median_delta_per_trade": _med([r["delta_per_trade"] for r in rows]),
        "positive_fraction": float(np.mean([r["delta_yen"] > 0 for r in rows])),
    }


def _order(low: Optional[float], high: Optional[float]) -> int:
    if low is None or high is None or low == high:
        return 0
    return 1 if high > low else -1


def _assoc(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups = thirds(rows, field)
    economics = {name: _econ(chosen) for name, chosen in groups.items()}
    return {
        "spearman": {
            "delta_yen": spearman([r.get(field) for r in rows], [r["delta_yen"] for r in rows]),
            "delta_per_trade": spearman([r.get(field) for r in rows], [r["delta_per_trade"] for r in rows]),
            "delta_bps": spearman([r.get(field) for r in rows], [r["delta_bps"] for r in rows]),
            "bsf_soft_per_trade": spearman(
                [r.get(field) for r in rows],
                [None if r["baseline_n"] == 0 else r["bsf_soft_delta"] / r["baseline_n"] for r in rows],
            ),
        },
        "thirds": economics,
        "per_trade_order": _order(economics["low"]["median_delta_per_trade"], economics["high"]["median_delta_per_trade"]),
    }


def _stable(left: dict[str, Any], right: dict[str, Any], outcome: str) -> bool:
    a = left["spearman"][outcome]
    b = right["spearman"][outcome]
    if a is None or b is None or a == 0 or b == 0:
        return False
    if (a > 0) != (b > 0):
        return False
    return left["per_trade_order"] != 0 and left["per_trade_order"] == right["per_trade_order"]


def _std_diff(top: list[float], rest: list[float]) -> Optional[float]:
    if len(top) < 2 or len(rest) < 2:
        return None
    pooled = float(np.sqrt(0.5 * (np.var(top) + np.var(rest))))
    if pooled == 0:
        return None
    return float((np.median(top) - np.median(rest)) / pooled)


def _decide(repro: bool, associations: dict[str, Any], exposure: dict[str, Any]) -> tuple[str, str]:
    if not repro:
        return "V2_FIXED_SUPPORT_HETEROGENEITY_NOT_STABLE_V1", "KEEP_V2_UNCHANGED"
    ratios = ("ratchet_step_to_spread", "ratchet_step_to_range")
    ratio_rows = {name: associations[name] for name in ratios}
    hypothesized = all(
        _stable(row["ORIGINAL18"], row["EXTENSION17"], "delta_per_trade")
        and row["ORIGINAL18"]["spearman"]["delta_per_trade"] < 0
        and row["ORIGINAL18"]["per_trade_order"] < 0
        for row in ratio_rows.values()
    )
    recovery = all(
        (row["ORIGINAL18"]["spearman"]["bsf_soft_per_trade"] or 0) < 0
        and (row["EXTENSION17"]["spearman"]["bsf_soft_per_trade"] or 0) < 0
        for row in ratio_rows.values()
    )
    count_per_trade = exposure["delta_per_trade"]
    ratio_strength = [abs(row["ORIGINAL18"]["spearman"]["delta_per_trade"] or 0) for row in ratio_rows.values()]
    count_dominates = all(
        abs(count_per_trade["ORIGINAL18"] or 0) > strength and abs(count_per_trade["EXTENSION17"] or 0) > abs(row["EXTENSION17"]["spearman"]["delta_per_trade"] or 0)
        for row, strength in zip(ratio_rows.values(), ratio_strength)
    )
    any_stable = any(
        _stable(item["ORIGINAL18"], item["EXTENSION17"], "delta_per_trade")
        for item in associations.values()
    )
    if hypothesized and recovery and not count_dominates:
        return "V2_FIXED_SUPPORT_HETEROGENEITY_MECHANISM_FOUND_V1", "PRECOMMIT_CAUSAL_SUPPORT_MODE_RULE_V1"
    yen_same = exposure["delta_yen"]["ORIGINAL18"] is not None and exposure["delta_yen"]["EXTENSION17"] is not None and (exposure["delta_yen"]["ORIGINAL18"] > 0) == (exposure["delta_yen"]["EXTENSION17"] > 0)
    per_trade_flat = not _stable_pair(count_per_trade["ORIGINAL18"], count_per_trade["EXTENSION17"])
    if yen_same and per_trade_flat and not hypothesized:
        return "V2_FIXED_SUPPORT_CONCENTRATION_EXPOSURE_COUNT_DRIVEN_V1", "KEEP_V2_UNCHANGED"
    if not any_stable:
        return "V2_FIXED_SUPPORT_SYMBOL_IDIOSYNCRATIC_ONLY_V1", "KEEP_V2_UNCHANGED"
    return "V2_FIXED_SUPPORT_HETEROGENEITY_NOT_STABLE_V1", "KEEP_V2_UNCHANGED"


def _stable_pair(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None or a == 0 or b == 0:
        return False
    return (a > 0) == (b > 0)


def _sheet(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        return
    keys = list(rows[0].keys())
    ws.append(keys)
    for row in rows:
        ws.append([row.get(k) for k in keys])


def publish() -> dict[str, Any]:
    self_check()
    cache = OUT / "_scan.pkl"
    payload = pickle.loads(cache.read_bytes()) if cache.exists() else scan()
    if not cache.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(payload))
    base, cand = payload["baseline"], payload["candidate"]
    base_pnl = float(sum(r["pnl_yen"] for r in base))
    cand_pnl = float(sum(r["pnl_yen"] for r in cand))
    base_pf, cand_pf = _pf(base), _pf(cand)
    repro_base = len(base) == BASE_N and abs(base_pnl - BASE_PNL) < 1e-6 and base_pf is not None and abs(base_pf - BASE_PF) < 1e-12 and payload["identity_miss"] == 0
    repro_cand = len(cand) == CAND_N and abs(cand_pnl - CAND_PNL) < 1e-6 and cand_pf is not None and abs(cand_pf - CAND_PF) < 1e-12
    original, extension = set(ORIGINAL18), set(EXTENSION17)
    full = _symbols(base, cand)
    o18 = _symbols([r for r in base if r["date"] in original], [r for r in cand if r["date"] in original])
    ext = _symbols([r for r in base if r["date"] in extension], [r for r in cand if r["date"] in extension])
    symbol_sum = float(sum(r["delta_yen"] for r in full))
    reconciled = abs(symbol_sum - (cand_pnl - base_pnl)) < 1e-6 and abs(cand_pnl - base_pnl - COMPLETE_DELTA) < 1e-6
    fields = [name for names in FAMILIES.values() for name in names]
    associations = {}
    for field in dict.fromkeys(fields):
        associations[field] = {"ORIGINAL18": _assoc(o18, field), "EXTENSION17": _assoc(ext, field)}
    exposure = {
        "delta_yen": {
            "ORIGINAL18": spearman([r["baseline_n"] for r in o18], [r["delta_yen"] for r in o18]),
            "EXTENSION17": spearman([r["baseline_n"] for r in ext], [r["delta_yen"] for r in ext]),
        },
        "delta_per_trade": {
            "ORIGINAL18": spearman([r["baseline_n"] for r in o18], [r["delta_per_trade"] for r in o18]),
            "EXTENSION17": spearman([r["baseline_n"] for r in ext], [r["delta_per_trade"] for r in ext]),
        },
    }
    top = [r for r in full if r["symbol"] in ESTABLISHED_TOP10]
    rest = [r for r in full if r["symbol"] not in ESTABLISHED_TOP10]
    contrast = {}
    for field in fields + ["ratchet_n", "max_gap_bps", "recovery_to_drawdown", "baseline_n"]:
        a = [float(r[field]) for r in top if r.get(field) is not None and r[field] == r[field]]
        b = [float(r[field]) for r in rest if r.get(field) is not None and r[field] == r[field]]
        contrast[field] = {"top10_median": _med(a), "rest_median": _med(b), "standardized_difference": _std_diff(a, b)}
    verdict, nxt = _decide(bool(repro_base and repro_cand and reconciled), associations, exposure)
    flat_assoc = []
    for field, item in associations.items():
        flat_assoc.append({
            "descriptor": field,
            "original18_rho_per_trade": item["ORIGINAL18"]["spearman"]["delta_per_trade"],
            "extension17_rho_per_trade": item["EXTENSION17"]["spearman"]["delta_per_trade"],
            "original18_rho_bps": item["ORIGINAL18"]["spearman"]["delta_bps"],
            "extension17_rho_bps": item["EXTENSION17"]["spearman"]["delta_bps"],
            "original18_rho_yen": item["ORIGINAL18"]["spearman"]["delta_yen"],
            "extension17_rho_yen": item["EXTENSION17"]["spearman"]["delta_yen"],
            "same_direction_per_trade": _stable(item["ORIGINAL18"], item["EXTENSION17"], "delta_per_trade"),
        })
    tertile_rows = []
    for field, item in associations.items():
        for period, block in item.items():
            for band, econ in block["thirds"].items():
                tertile_rows.append({"descriptor": field, "period": period, "band": band, **econ})
    report = {
        "study": "V2_FIXED_SUPPORT_HETEROGENEITY_MECHANISM_AUDIT_V1",
        "verdict": verdict,
        "next": nxt,
        "v2_changed": False,
        "candidate_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": [0, 0, 0],
        "baseline_reproduction": bool(repro_base),
        "candidate_reproduction": bool(repro_cand),
        "delta_reconciliation": bool(reconciled),
        "complete_delta": cand_pnl - base_pnl,
        "exposure": exposure,
        "top10_vs_rest": contrast,
        "associations": flat_assoc,
        "tertiles": tertile_rows,
        "families": FAMILIES,
        "decision_rule": "A mechanism is found only if both tightness ratios have a negative per-trade association in ORIGINAL18 and EXTENSION17, the low-ratio third beats the high-ratio third in both periods, the soft-exit recovery association has the same sign, and trade count does not dominate that per-trade association. Otherwise the result is exposure-driven, idiosyncratic, or unstable. No descriptor was added after the replay.",
        "symbols": [{k: r[k] for k in r if k != "symbol" or True} for r in sorted(full, key=lambda r: r["delta_yen"], reverse=True)],
    }
    report["audit_sha256"] = sha256_obj({k: v for k, v in report.items() if k != "audit_sha256"})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    wb = Workbook()
    wb.active.title = "summary"
    wb.active.append(["verdict", verdict, nxt])
    _sheet(wb, "associations", flat_assoc)
    _sheet(wb, "tertiles", tertile_rows)
    _sheet(wb, "top10_vs_rest", [{"descriptor": k, **v} for k, v in contrast.items()])
    wb.save(OUT / "audit.xlsx")
    cache.unlink(missing_ok=True)
    print(f"VERDICT {verdict} NEXT {nxt}", flush=True)
    return report


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['study']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        f"Baseline reproduction {report['baseline_reproduction']}. Candidate reproduction {report['candidate_reproduction']}. Delta reconciliation {report['delta_reconciliation']}.",
        f"Complete delta {report['complete_delta']}.",
        "",
        f"Trade-count vs delta yen: ORIGINAL18 {report['exposure']['delta_yen']['ORIGINAL18']}, EXTENSION17 {report['exposure']['delta_yen']['EXTENSION17']}.",
        f"Trade-count vs delta per trade: ORIGINAL18 {report['exposure']['delta_per_trade']['ORIGINAL18']}, EXTENSION17 {report['exposure']['delta_per_trade']['EXTENSION17']}.",
        "",
        report["decision_rule"],
        "",
    ]
    for row in report["associations"]:
        if row["descriptor"] in ("ratchet_step_to_spread", "ratchet_step_to_range"):
            lines.append(f"{row['descriptor']}: O18 {row['original18_rho_per_trade']} EXT {row['extension17_rho_per_trade']} same {row['same_direction_per_trade']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    publish()
