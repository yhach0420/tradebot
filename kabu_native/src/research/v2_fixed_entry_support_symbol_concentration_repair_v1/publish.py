"""Fair symbol-delta comparison. Does not change the candidate or the baseline."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import sha256_obj
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_fixed_entry_support_symbol_concentration_repair_v1.scan import scan

OUT = Path("results/research/v2_fixed_entry_support_symbol_concentration_repair_v1")
BASE_N, BASE_PNL, BASE_PF = 11902, 1189150.0, 1.409303686366296
CAND_N, CAND_PNL, CAND_PF = 11446, 2520850.0, 1.7935186351045076
COMPLETE_DELTA = 1331700.0
MATCHED_DELTA = 1280050.0


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    loss = sum(-float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) < 0)
    gain = sum(float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) > 0)
    return gain / loss if loss else None


def _key(row: dict[str, Any]) -> tuple:
    return (row["date"], row["session"], row["symbol"], int(row["signal_index"]))


def _blank() -> dict[str, Any]:
    return {
        "baseline_pnl": 0.0, "candidate_pnl": 0.0, "baseline_n": 0, "candidate_n": 0,
        "baseline_soft": 0.0, "baseline_hard": 0.0, "candidate_soft": 0.0, "candidate_hard": 0.0,
        "matched_delta": 0.0, "new_pnl": 0.0, "lost_pnl": 0.0,
        "bsf_soft": 0.0, "bsf_flat": 0.0, "other_later": 0.0, "further": 0.0, "other_matched": 0.0,
        "bsf_soft_n": 0, "further_n": 0,
        "original_delta": 0.0, "extension_delta": 0.0,
        "fold1_delta": 0.0, "fold2_delta": 0.0, "fold3_delta": 0.0,
        "original_n": 0, "extension_n": 0,
    }


def _add_reason(bucket: dict[str, Any], side: str, reason: str, pnl: float) -> None:
    if reason == "IMPULSE_EXHAUSTED":
        bucket[f"{side}_soft"] += pnl
    elif reason == "BREAK_SUPPORT_FAILURE":
        bucket[f"{side}_hard"] += pnl


def _component(row: dict[str, Any]) -> str:
    if row["base_reason"] == "BREAK_SUPPORT_FAILURE" and row["cand_reason"] == "IMPULSE_EXHAUSTED":
        return "bsf_soft"
    if row["base_reason"] == "BREAK_SUPPORT_FAILURE" and row["cand_reason"] == "SESSION_FLAT":
        return "bsf_flat"
    later_hard = (
        row["later"]
        and row["base_reason"] == "BREAK_SUPPORT_FAILURE"
        and row["cand_reason"] not in ("IMPULSE_EXHAUSTED", "SESSION_FLAT")
        and float(row["cand_exit"]) < float(row["base_exit"])
    )
    if later_hard:
        return "further"
    if row["later"] and row["base_reason"] == "BREAK_SUPPORT_FAILURE":
        return "other_later"
    return "other_matched"


def _winner(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: float(r["pnl_yen"]), reverse=True)
    total = float(sum(float(r["pnl_yen"]) for r in ordered))
    out = {"pnl_yen": total, "trade_n": len(ordered)}
    cuts = {"top1": 1, "top5": 5, "top10": 10, "top1pct": max(1, int(round(len(ordered) * 0.01)))}
    for name, k in cuts.items():
        taken = float(sum(float(r["pnl_yen"]) for r in ordered[:k]))
        out[name] = {
            "n": min(k, len(ordered)),
            "pnl_yen": taken,
            "share_of_book": None if total == 0 else taken / total,
            "remaining_pnl": total - taken,
        }
    return out


def _exclude(base: list[dict[str, Any]], cand: list[dict[str, Any]], symbols: list[str]) -> dict[str, float]:
    banned = set(symbols)
    b = float(sum(float(r["pnl_yen"]) for r in base if r["symbol"] not in banned))
    c = float(sum(float(r["pnl_yen"]) for r in cand if r["symbol"] not in banned))
    return {"symbols": symbols, "baseline_pnl": b, "candidate_pnl": c, "delta": c - b}


def _decide(ok: bool, exclusion: dict[str, Any]) -> tuple[str, str]:
    if not ok:
        return "V2_FIXED_ENTRY_SUPPORT_SYMBOL_CONCENTRATION_REPAIR_BLOCKED_V1", "REPAIR_SYMBOL_DELTA_ACCOUNTING_ONLY"
    kept = [exclusion[name]["delta"] for name in ("exclude_top1", "exclude_top3", "exclude_top5", "exclude_top10")]
    if all(delta > 0 for delta in kept):
        return "V2_FIXED_ENTRY_SUPPORT_SYMBOL_CONCENTRATION_CLEARED_V1", "FREEZE_FIXED_ENTRY_SUPPORT_CANDIDATE_FOR_CONFIRMATION_V1"
    return "V2_FIXED_ENTRY_SUPPORT_TRUE_SYMBOL_CONCENTRATION_DEPENDENCE_V1", "KEEP_V2_UNCHANGED"


def _sheet(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        return
    keys = list(rows[0].keys())
    ws.append(keys)
    for row in rows:
        ws.append([row.get(k) for k in keys])


def publish() -> dict[str, Any]:
    cache = OUT / "_scan.pkl"
    payload = pickle.loads(cache.read_bytes()) if cache.exists() else scan()
    if not cache.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(payload))
    base, cand = payload["baseline"], payload["candidate"]
    dates = list(payload["dates"])
    folds = [set(dates[i * len(dates) // 3:(i + 1) * len(dates) // 3]) for i in range(3)]
    original, extension = set(ORIGINAL18), set(EXTENSION17)
    base_pnl = float(sum(float(r["pnl_yen"]) for r in base))
    cand_pnl = float(sum(float(r["pnl_yen"]) for r in cand))
    base_pf, cand_pf = _pf(base), _pf(cand)
    repro_base = len(base) == BASE_N and abs(base_pnl - BASE_PNL) < 1e-6 and base_pf is not None and abs(base_pf - BASE_PF) < 1e-12 and payload["identity_miss"] == 0
    repro_cand = len(cand) == CAND_N and abs(cand_pnl - CAND_PNL) < 1e-6 and cand_pf is not None and abs(cand_pf - CAND_PF) < 1e-12
    by: dict[str, dict[str, Any]] = {}
    base_by = {_key(r): r for r in base}
    cand_by = {_key(r): r for r in cand}
    matched_delta = new_pnl = lost_pnl = 0.0
    new_n = lost_n = 0
    for row in base:
        item = by.setdefault(row["symbol"], _blank())
        item["baseline_pnl"] += float(row["pnl_yen"])
        item["baseline_n"] += 1
        _add_reason(item, "baseline", row["reason"], float(row["pnl_yen"]))
        _period_add(item, row["date"],  -float(row["pnl_yen"]), original, extension, folds)
        if _key(row) not in cand_by:
            lost_n += 1
            lost_pnl += float(row["pnl_yen"])
            item["lost_pnl"] += float(row["pnl_yen"])
    for row in cand:
        item = by.setdefault(row["symbol"], _blank())
        item["candidate_pnl"] += float(row["pnl_yen"])
        item["candidate_n"] += 1
        _add_reason(item, "candidate", row["reason"], float(row["pnl_yen"]))
        _period_add(item, row["date"], float(row["pnl_yen"]), original, extension, folds)
        if _key(row) not in base_by:
            new_n += 1
            new_pnl += float(row["pnl_yen"])
            item["new_pnl"] += float(row["pnl_yen"])
    for key, row in base_by.items():
        other = cand_by.get(key)
        if other is None:
            continue
        delta = float(other["pnl_yen"]) - float(row["pnl_yen"])
        matched_delta += delta
        item = by[row["symbol"]]
        item["matched_delta"] += delta
        kind = _component({
            "base_reason": row["reason"], "cand_reason": other["reason"],
            "later": float(other["exit_t"]) > float(row["exit_t"]) + 1e-9,
            "base_exit": float(row["exit_px"]), "cand_exit": float(other["exit_px"]),
        })
        item[kind] += delta
        if kind == "bsf_soft":
            item["bsf_soft_n"] += 1
        if kind == "further":
            item["further_n"] += 1
    rows = []
    for symbol, item in by.items():
        delta = float(item["candidate_pnl"]) - float(item["baseline_pnl"])
        net = float(item["bsf_soft"] + item["bsf_flat"] + item["other_later"] + item["further"])
        rows.append({
            "symbol": symbol, "delta": delta, "net_extension": net,
            "occupancy_delta": float(item["new_pnl"]) - float(item["lost_pnl"]),
            **item,
        })
    symbol_sum = float(sum(r["delta"] for r in rows))
    complete = cand_pnl - base_pnl
    accounting = abs(symbol_sum - complete) < 1e-6 and abs(matched_delta + new_pnl - lost_pnl - complete) < 1e-6
    accounting = accounting and abs(complete - COMPLETE_DELTA) < 1e-6 and abs(matched_delta - MATCHED_DELTA) < 1e-6
    net_sum = float(sum(r["net_extension"] for r in rows))
    other_matched = float(sum(r["other_matched"] for r in rows))
    ranked = sorted(rows, key=lambda r: (r["delta"], r["symbol"]), reverse=True)
    by_cand = sorted(rows, key=lambda r: (r["candidate_pnl"], r["symbol"]), reverse=True)
    deltas = [float(r["delta"]) for r in ranked]
    positive = [r for r in ranked if r["delta"] > 0]
    negative = [r for r in ranked if r["delta"] < 0]
    zero = [r for r in ranked if r["delta"] == 0]
    weights = np.array([r["baseline_n"] + r["candidate_n"] for r in ranked], dtype=np.float64)
    weight_sum = float(weights.sum())
    exclusion = {f"exclude_top{k}": _exclude(base, cand, [r["symbol"] for r in ranked[:k]]) for k in (1, 3, 5, 10)}
    prior_five = [r["symbol"] for r in by_cand[:5]]
    prior = {
        "symbols": prior_five,
        "baseline_pnl": float(sum(r["baseline_pnl"] for r in by_cand[:5])),
        "candidate_pnl": float(sum(r["candidate_pnl"] for r in by_cand[:5])),
        "delta": float(sum(r["delta"] for r in by_cand[:5])),
        "same_universe_remaining": _exclude(base, cand, prior_five),
    }
    net_ranked = sorted(rows, key=lambda r: (r["net_extension"], r["symbol"]), reverse=True)
    total = COMPLETE_DELTA
    def _share(chosen: list[dict[str, Any]], field: str) -> dict[str, Any]:
        value = float(sum(r[field] for r in chosen))
        return {"symbols": [r["symbol"] for r in chosen], "delta": value, "share_of_total_delta": value / total}
    top_delta = {f"top{k}": _share(ranked[:k], "delta") for k in (1, 3, 5, 10)}
    top_net = {f"top{k}": _share(net_ranked[:k], "net_extension") for k in (1, 5, 10)}
    both_periods = []
    for row in ranked[:10]:
        both_periods.append({
            "symbol": row["symbol"],
            "delta": row["delta"],
            "original18": row["original_delta"],
            "extension17": row["extension_delta"],
            "positive_in_both": row["original_delta"] > 0 and row["extension_delta"] > 0,
            "fold1": row["fold1_delta"],
            "fold2": row["fold2_delta"],
            "fold3": row["fold3_delta"],
            "baseline_n": row["baseline_n"],
            "candidate_n": row["candidate_n"],
            "bsf_soft_n": row["bsf_soft_n"],
            "further_n": row["further_n"],
        })
    ok = bool(repro_base and repro_cand and accounting)
    verdict, nxt = _decide(ok, exclusion)
    report = {
        "study": "V2_FIXED_ENTRY_SUPPORT_SYMBOL_CONCENTRATION_REPAIR_V1",
        "verdict": verdict,
        "next": nxt,
        "v2_changed": False,
        "candidate_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": [0, 0, 0],
        "previous_comparator_invalid": True,
        "baseline_reproduction": bool(repro_base),
        "candidate_reproduction": bool(repro_cand),
        "accounting": {
            "complete_delta": complete,
            "sum_symbol_delta": symbol_sum,
            "matched_delta": matched_delta,
            "newly_admitted_n": new_n,
            "newly_admitted_pnl": new_pnl,
            "lost_n": lost_n,
            "lost_pnl": lost_pnl,
            "reconciled": bool(accounting),
            "net_extension_sum": net_sum,
            "other_matched_delta": other_matched,
            "identity": "sum(candidate symbol pnl - baseline symbol pnl) = complete delta; matched + new - lost = complete delta",
        },
        "breadth": {
            "symbol_n": len(ranked),
            "positive_n": len(positive),
            "negative_n": len(negative),
            "zero_n": len(zero),
            "fraction_positive": len(positive) / len(ranked) if ranked else None,
            "median_delta": float(np.median(deltas)) if deltas else None,
            "unweighted_mean_delta": float(np.mean(deltas)) if deltas else None,
            "trade_weighted_mean_delta": float(np.dot(np.array(deltas), weights) / weight_sum) if weight_sum else None,
        },
        "top_delta": top_delta,
        "same_universe_exclusion": exclusion,
        "previous_top5_candidate_pnl_symbols": prior,
        "net_extension": top_net,
        "top10_period_stability": both_periods,
        "period_breadth": {
            "ORIGINAL18": _breadth(ranked, "original_delta", "original_n"),
            "EXTENSION17": _breadth(ranked, "extension_delta", "extension_n"),
        },
        "winner_concentration": {"baseline": _winner(base), "candidate": _winner(cand)},
        "decision_rule": "Cleared only when the same-universe delta stays positive after removing the top 1, 3, 5, and 10 delta symbols from both books. A non-positive remaining delta is dependence. The old candidate-without-top5 versus full-baseline comparison is not used.",
        "symbols": [
            {k: row[k] for k in (
                "symbol", "delta", "baseline_pnl", "candidate_pnl", "baseline_n", "candidate_n",
                "baseline_soft", "baseline_hard", "candidate_soft", "candidate_hard",
                "matched_delta", "new_pnl", "lost_pnl", "net_extension",
                "bsf_soft", "bsf_soft_n", "bsf_flat", "other_later", "further", "further_n",
                "original_delta", "extension_delta", "fold1_delta", "fold2_delta", "fold3_delta",
            )}
            for row in ranked
        ],
    }
    report["audit_sha256"] = sha256_obj({k: v for k, v in report.items() if k != "audit_sha256"})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    wb = Workbook()
    wb.active.title = "summary"
    wb.active.append(["verdict", verdict, nxt])
    _sheet(wb, "symbols", report["symbols"])
    _sheet(wb, "top10_periods", both_periods)
    wb.save(OUT / "audit.xlsx")
    cache.unlink(missing_ok=True)
    print(f"VERDICT {verdict} NEXT {nxt}", flush=True)
    return report


def _period_add(item: dict[str, Any], day: str, signed: float, original: set[str], extension: set[str], folds: list[set[str]]) -> None:
    if day in original:
        item["original_delta"] += signed
        item["original_n"] += 1
    if day in extension:
        item["extension_delta"] += signed
        item["extension_n"] += 1
    for name, fold, key in (("f1", folds[0], "fold1_delta"), ("f2", folds[1], "fold2_delta"), ("f3", folds[2], "fold3_delta")):
        if day in fold:
            item[key] += signed


def _breadth(rows: list[dict[str, Any]], field: str, nfield: str) -> dict[str, Any]:
    active = [r for r in rows if int(r[nfield]) > 0]
    pos = sum(1 for r in active if float(r[field]) > 0)
    neg = sum(1 for r in active if float(r[field]) < 0)
    return {"symbols_with_trades": len(active), "positive_delta_n": pos, "negative_delta_n": neg, "fraction_positive": None if not active else pos / len(active)}


def _markdown(report: dict[str, Any]) -> str:
    ex = report["same_universe_exclusion"]
    lines = [
        f"# {report['study']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "The repaired comparison removes the same symbols from both books. Candidate absolute PnL with those symbols removed is not compared with the full baseline.",
        "",
        f"Complete delta {report['accounting']['complete_delta']}. Symbol-delta sum {report['accounting']['sum_symbol_delta']}. Reconciled {report['accounting']['reconciled']}.",
        "",
        f"Positive-delta symbols {report['breadth']['positive_n']}. Negative {report['breadth']['negative_n']}. Median {report['breadth']['median_delta']}.",
        "",
    ]
    for name in ("exclude_top1", "exclude_top3", "exclude_top5", "exclude_top10"):
        row = ex[name]
        lines.append(f"{name}: baseline {row['baseline_pnl']} / candidate {row['candidate_pnl']} / delta {row['delta']}")
    lines.append("")
    lines.append(report["decision_rule"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    publish()
