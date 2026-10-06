"""Audit the already-tested fixed-entry-support candidate. No new rule."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import bind, sha256_obj
from research.v2_fixed_entry_support_candidate_audit_v1.risk import distribution
from research.v2_fixed_entry_support_candidate_audit_v1.scan import scan
from research.v2_support_ratchet_causal_ablation_v1.ablation_sim import self_check

OUT = Path("results/research/v2_fixed_entry_support_candidate_audit_v1")
BASE_N, BASE_PNL, BASE_PF = 11902, 1189150.0, 1.409303686366296
CAND_N, CAND_PNL, CAND_PF = 11446, 2520850.0, 1.7935186351045076
MATCHED_DELTA, NEW_PNL, LOST_PNL, COMPLETE_DELTA = 1280050.0, -2000.0, -53650.0, 1331700.0
ASOF_BASE = (9798, 741300.0, 1.2622028862478778)
ASOF_CAND = (9639, 1287500.0, 1.378787878787879)
STRATEGY_SHA = "1d5ac587d4a67a7da4de9def64a6471428a136e4e30e1d26ca904983a8bcf505"
SIGNAL_SHA = "06345e9f7ddd2fcdf21a7beac49241495d3a4b6607715c879cf2ef0a307b4f68"
BUCKETS = ((0, 1, "<1s"), (1, 5, "1-5s"), (5, 10, "5-10s"), (10, 30, "10-30s"), (30, 60, "30-60s"), (60, 300, "1-5min"), (300, 600, "5-10min"), (600, 1e18, ">10min"))


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    loss = sum(-float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) < 0)
    gain = sum(float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) > 0)
    return gain / loss if loss else None


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"trade_n": 0, "pnl_yen": 0.0, "pf": None, "gross_profit_yen": 0.0, "gross_loss_yen": 0.0, "mean_hold_sec": None, "p95_hold_sec": None}
    pnl = np.array([float(r["pnl_yen"]) for r in rows], dtype=np.float64)
    hold = [float(r["hold_sec"]) for r in rows if r.get("hold_sec") is not None]
    return {
        "trade_n": int(len(rows)),
        "pnl_yen": float(pnl.sum()),
        "pf": _pf(rows),
        "gross_profit_yen": float(pnl[pnl > 0].sum()) if np.any(pnl > 0) else 0.0,
        "gross_loss_yen": float((-pnl[pnl < 0]).sum()) if np.any(pnl < 0) else 0.0,
        "mean_hold_sec": None if not hold else float(np.mean(hold)),
        "p95_hold_sec": None if not hold else float(np.percentile(np.array(hold), 95)),
        "mean_bps": float(np.mean([float(r["bps"]) for r in rows])),
    }


def _bps(row: dict[str, Any], yen_key: str) -> Optional[float]:
    yen = row.get(yen_key)
    px = row.get("entry_px")
    if yen is None or px is None or float(px) <= 0:
        return None
    return float(yen) / float(px) * 100.0


def _adverse(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mae = [abs(float(r["mae_yen"])) for r in rows if r.get("mae_yen") is not None]
    mfe = [float(r["mfe_yen"]) for r in rows if r.get("mfe_yen") is not None]
    give = [float(r["max_giveback_yen"]) for r in rows if r.get("max_giveback_yen") is not None]
    mae_bps = [abs(_bps(r, "mae_yen")) for r in rows if _bps(r, "mae_yen") is not None]
    give_bps = [_bps(r, "max_giveback_yen") for r in rows if _bps(r, "max_giveback_yen") is not None]
    return {"mae_yen": distribution(mae), "mae_bps": distribution(mae_bps), "mfe_yen": distribution(mfe), "giveback_yen": distribution(give), "giveback_bps": distribution(give_bps)}


def _hold(rows: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    vals = [float(r["hold_sec"]) for r in rows if r.get("hold_sec") is not None]
    out = distribution(vals)
    out["p50"] = out.pop("median")
    return out


def _bucket(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lo, hi, name in BUCKETS:
        chosen = [r for r in rows if r.get("hold_sec") is not None and lo <= float(r["hold_sec"]) < hi]
        stats = _stats(chosen)
        out.append({"bucket": name, "trade_n": stats["trade_n"], "pnl_yen": stats["pnl_yen"], "pf": stats["pf"], "mean_bps": stats["mean_bps"]})
    return out


def _top_share(rows: list[dict[str, Any]], k: int, total: float, soft_total: float) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: float(r["pnl_yen"]), reverse=True)[:k]
    pnl = float(sum(float(r["pnl_yen"]) for r in ordered))
    return {
        "n": len(ordered), "pnl_yen": pnl,
        "fraction_of_strategy": None if total == 0 else pnl / total,
        "fraction_of_soft": None if soft_total == 0 else pnl / soft_total,
    }


def _group_dates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, float] = {}
    for row in rows:
        by[str(row["date"])] = by.get(str(row["date"]), 0.0) + float(row.get("delta", row.get("pnl_yen", 0.0)))
    ordered = sorted(by.items(), key=lambda item: item[1], reverse=True)
    total = float(sum(by.values()))
    return {"top1": ordered[:1], "top5": ordered[:5], "top5_share": None if total == 0 else sum(v for _, v in ordered[:5]) / total, "dates": len(by)}


def _decide(facts: dict[str, Any]) -> tuple[str, str]:
    if not facts["gates"]["all_integrity"]:
        nxt = "REPAIR_SOFT_EXIT_INTEGRITY_ONLY" if not facts["gates"]["soft_exit_causal_integrity"] else "KEEP_V2_UNCHANGED"
        return "V2_FIXED_ENTRY_SUPPORT_INTEGRITY_BLOCKED_V1", nxt
    if not facts["gates"]["period_improvement"] or not facts["gates"]["asof_better"]:
        return "V2_FIXED_ENTRY_SUPPORT_NOT_ROBUST_V1", "KEEP_V2_UNCHANGED"
    if not facts["gates"]["improvement_is_broad"]:
        return "V2_FIXED_ENTRY_SUPPORT_CONCENTRATION_DEPENDENT_V1", "KEEP_V2_UNCHANGED"
    if not facts["gates"]["open_risk_acceptable"]:
        return "V2_FIXED_ENTRY_SUPPORT_RETURN_RISK_TRADEOFF_V1", "KEEP_V2_UNCHANGED"
    return "V2_FIXED_ENTRY_SUPPORT_COMPLETE_STRATEGY_CANDIDATE_SUPPORTED_V1", "FREEZE_FIXED_ENTRY_SUPPORT_CANDIDATE_FOR_CONFIRMATION_V1"


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
    bound = bind()
    if bound["strategy_sha256"] != STRATEGY_SHA or bound["signal_sha256"] != SIGNAL_SHA:
        raise RuntimeError("frozen_identity_mismatch")
    cache = OUT / "_scan.pkl"
    payload = pickle.loads(cache.read_bytes()) if cache.exists() else scan()
    if not cache.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(payload))
    base, cand = payload["baseline"], payload["candidate"]
    b100, c100 = payload["baseline_100"], payload["candidate_100"]
    base_stats, cand_stats = _stats(base), _stats(cand)
    uid_ok = payload["uids"] == payload["uids2"] and len(payload["uids"]) == len(set(payload["uids"]))
    parity = base_stats["trade_n"] == BASE_N and abs(base_stats["pnl_yen"] - BASE_PNL) < 1e-6 and abs(float(base_stats["pf"]) - BASE_PF) < 1e-12
    repro = cand_stats["trade_n"] == CAND_N and abs(cand_stats["pnl_yen"] - CAND_PNL) < 1e-6 and abs(float(cand_stats["pf"]) - CAND_PF) < 1e-12
    matched = payload["transitions"]
    matched_delta = float(sum(float(r["cand_pnl"]) - float(r["base_pnl"]) for r in matched))
    lost = payload["lost_rows"]
    lost_pnl = float(sum(float(r["pnl_yen"]) for r in lost))
    base_keys = {(r["date"], r["session"], r["symbol"], int(r["signal_index"])) for r in base}
    new_rows = [r for r in cand if (r["date"], r["session"], r["symbol"], int(r["signal_index"])) not in base_keys]
    new_pnl = float(sum(float(r["pnl_yen"]) for r in new_rows))
    complete = cand_stats["pnl_yen"] - base_stats["pnl_yen"]
    decomp = abs(matched_delta - MATCHED_DELTA) < 1e-6 and abs(new_pnl - NEW_PNL) < 1e-6 and abs(lost_pnl - LOST_PNL) < 1e-6 and abs(complete - COMPLETE_DELTA) < 1e-6
    decomp = decomp and abs(matched_delta + new_pnl - lost_pnl - complete) < 1e-6
    soft_ok = payload["soft_mismatch"] == 0 and payload["soft_checked"] > 0
    b100s, c100s = _stats(b100), _stats(c100)
    asof_exact = (
        b100s["trade_n"] == ASOF_BASE[0] and abs(b100s["pnl_yen"] - ASOF_BASE[1]) < 1e-6 and abs(float(b100s["pf"]) - ASOF_BASE[2]) < 1e-12
        and c100s["trade_n"] == ASOF_CAND[0] and abs(c100s["pnl_yen"] - ASOF_CAND[1]) < 1e-6 and abs(float(c100s["pf"]) - ASOF_CAND[2]) < 1e-12
    )
    soft = [r for r in cand if str(r["reason"]) == "IMPULSE_EXHAUSTED"]
    hard = [r for r in cand if str(r["reason"]) == "BREAK_SUPPORT_FAILURE"]
    flat = [r for r in cand if str(r["reason"]) == "SESSION_FLAT"]
    soft_stats = _stats(soft)
    winners = sorted(cand, key=lambda r: float(r["pnl_yen"]), reverse=True)
    stress = {}
    for name, k in (("top1", 1), ("top5", 5), ("top10", 10), ("top1pct", max(1, int(round(len(winners) * 0.01))))):
        dropped = float(sum(float(r["pnl_yen"]) for r in winners[:k]))
        stress[name] = {"excluded_n": k, "excluded_pnl": dropped, "remaining_pnl": cand_stats["pnl_yen"] - dropped, "remaining_positive": cand_stats["pnl_yen"] - dropped > 0}
    by_date: dict[str, dict[str, float]] = {}
    for row in base:
        item = by_date.setdefault(str(row["date"]), {"base": 0.0, "cand": 0.0, "soft": 0.0, "hard": 0.0, "base_n": 0, "cand_n": 0, "soft_n": 0})
        item["base"] += float(row["pnl_yen"])
        item["base_n"] += 1
    for row in cand:
        item = by_date.setdefault(str(row["date"]), {"base": 0.0, "cand": 0.0, "soft": 0.0, "hard": 0.0, "base_n": 0, "cand_n": 0, "soft_n": 0})
        item["cand"] += float(row["pnl_yen"])
        item["cand_n"] += 1
        if str(row["reason"]) == "IMPULSE_EXHAUSTED":
            item["soft"] += float(row["pnl_yen"])
            item["soft_n"] += 1
        if str(row["reason"]) == "BREAK_SUPPORT_FAILURE":
            item["hard"] += float(row["pnl_yen"])
    day_rows = [{"date": day, **vals, "delta": vals["cand"] - vals["base"]} for day, vals in sorted(by_date.items())]
    day_delta = sorted(day_rows, key=lambda r: r["delta"], reverse=True)
    top_day_delta = {name: float(sum(r["delta"] for r in day_delta[:k])) for name, k in (("top1", 1), ("top3", 3), ("top5", 5))}
    outside5 = complete - top_day_delta["top5"]
    by_sym: dict[str, dict[str, float]] = {}
    for row in cand:
        item = by_sym.setdefault(str(row["symbol"]), {"n": 0, "pnl": 0.0, "soft": 0.0, "hard": 0.0})
        item["n"] += 1
        item["pnl"] += float(row["pnl_yen"])
        if str(row["reason"]) == "IMPULSE_EXHAUSTED":
            item["soft"] += float(row["pnl_yen"])
        if str(row["reason"]) == "BREAK_SUPPORT_FAILURE":
            item["hard"] += float(row["pnl_yen"])
    sym_rows = sorted(({"symbol": s, **v} for s, v in by_sym.items()), key=lambda r: r["pnl"], reverse=True)
    top_sym = {name: float(sum(r["pnl"] for r in sym_rows[:k])) for name, k in (("top1", 1), ("top5", 5), ("top10", 10))}
    exclude_sym = {
        "without_top1": cand_stats["pnl_yen"] - top_sym["top1"],
        "without_top5": cand_stats["pnl_yen"] - top_sym["top5"],
    }
    periods = {}
    for name, dates in (
        ("ORIGINAL18", set(payload["original"])), ("EXTENSION17", set(payload["extension"])),
        ("FOLD1", set(payload["folds"][0])), ("FOLD2", set(payload["folds"][1])), ("FOLD3", set(payload["folds"][2])),
    ):
        bsel = [r for r in base if str(r["date"]) in dates]
        csel = [r for r in cand if str(r["date"]) in dates]
        periods[name] = {
            "baseline": _stats(bsel), "candidate": _stats(csel),
            "baseline_soft_pnl": float(sum(float(r["pnl_yen"]) for r in bsel if str(r["reason"]) == "IMPULSE_EXHAUSTED")),
            "candidate_soft_pnl": float(sum(float(r["pnl_yen"]) for r in csel if str(r["reason"]) == "IMPULSE_EXHAUSTED")),
            "mtm": payload["period_trackers"][name],
        }
    trans = {}
    for row in matched:
        label = f"{row['base_reason']}->{row['cand_reason']}"
        if row["base_reason"] == row["cand_reason"]:
            label = "same_exit"
        bucket = trans.setdefault(label, {"n": 0, "delta": 0.0, "deltas": []})
        delta = float(row["cand_pnl"]) - float(row["base_pnl"])
        bucket["n"] += 1
        bucket["delta"] += delta
        bucket["deltas"].append(delta)
    trans_out = {k: {"n": v["n"], "pnl_delta": v["delta"], "mean_pnl_delta": float(np.mean(v["deltas"])), "median_pnl_delta": float(np.median(v["deltas"]))} for k, v in trans.items()}
    primary = [r for r in matched if r["base_reason"] == "BREAK_SUPPORT_FAILURE" and r["cand_reason"] == "IMPULSE_EXHAUSTED"]
    holds = [float(r["extra_hold"]) for r in primary]
    draw = [r for r in matched if r["later"] and r["base_reason"] == "BREAK_SUPPORT_FAILURE" and r["cand_reason"] not in ("IMPULSE_EXHAUSTED", "SESSION_FLAT") and float(r["cand_exit"]) < float(r["base_exit"])]
    soft_extra = [r for r in matched if r["later"] and r["base_reason"] == "BREAK_SUPPORT_FAILURE" and r["cand_reason"] == "IMPULSE_EXHAUSTED"]
    lost_by = {}
    for row in lost:
        item = lost_by.setdefault(row["reason"], {"n": 0, "pnl_yen": 0.0})
        item["n"] += 1
        item["pnl_yen"] += float(row["pnl_yen"])
    base_re = [r for r in base if r.get("reentry")]
    cand_re = [r for r in cand if r.get("reentry")]
    lost_re = [r for r in lost if r["reentry"]]
    mtm_base = payload["trackers"]["base"]["mtm_max_drawdown_yen"]
    mtm_cand = payload["trackers"]["cand"]["mtm_max_drawdown_yen"]
    extra_hole = float(mtm_base) - float(mtm_cand)
    broad = (
        stress["top10"]["remaining_pnl"] > base_stats["pnl_yen"]
        and stress["top1pct"]["remaining_pnl"] > base_stats["pnl_yen"]
        and outside5 > 0
        and exclude_sym["without_top5"] > base_stats["pnl_yen"]
    )
    risk_ok = extra_hole <= complete
    period_ok = all(periods[name]["candidate"]["pnl_yen"] > periods[name]["baseline"]["pnl_yen"] for name in periods)
    facts_gates = {
        "baseline_parity": bool(parity and payload["identity_miss"] == 0 and payload["observer_mismatch"] == 0 and payload["walk_mismatch"] == 0 and uid_ok),
        "candidate_reproduction": bool(repro),
        "decomposition": bool(decomp),
        "soft_exit_causal_integrity": bool(soft_ok),
        "period_improvement": bool(period_ok),
        "asof_exact": bool(asof_exact),
        "asof_better": bool(asof_exact and c100s["pnl_yen"] > 0 and c100s["pnl_yen"] > b100s["pnl_yen"]),
        "improvement_is_broad": bool(broad),
        "open_risk_acceptable": bool(risk_ok),
    }
    facts_gates["all_integrity"] = all(facts_gates[k] for k in ("baseline_parity", "candidate_reproduction", "decomposition", "soft_exit_causal_integrity"))
    verdict, nxt = _decide({"gates": facts_gates})
    report = {
        "study": "V2_SUPPORT_RATCHET_REMOVAL_CANDIDATE_AUDIT_V1",
        "verdict": verdict,
        "next": nxt,
        "v2_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": [0, 0, 0],
        "strategy_sha256": STRATEGY_SHA,
        "gates": facts_gates,
        "baseline": {**base_stats, "mtm": payload["trackers"]["base"]},
        "candidate": {**cand_stats, "mtm": payload["trackers"]["cand"]},
        "delta_pnl": complete,
        "decomposition": {"matched_delta": matched_delta, "newly_admitted_n": len(new_rows), "newly_admitted_pnl": new_pnl, "lost_n": len(lost), "lost_pnl": lost_pnl},
        "mae_giveback": {"baseline": _adverse(base), "candidate": _adverse(cand)},
        "hold": {"baseline": _hold(base), "candidate": _hold(cand), "candidate_buckets": _bucket(cand)},
        "soft_exit": {
            **soft_stats,
            "checked_n": payload["soft_checked"],
            "mismatch_n": payload["soft_mismatch"],
            "distribution_yen": distribution([float(r["pnl_yen"]) for r in soft]),
            "concentration": {
                "top1": _top_share(soft, 1, cand_stats["pnl_yen"], soft_stats["pnl_yen"]),
                "top5": _top_share(soft, 5, cand_stats["pnl_yen"], soft_stats["pnl_yen"]),
                "top10": _top_share(soft, 10, cand_stats["pnl_yen"], soft_stats["pnl_yen"]),
                "top1pct": _top_share(soft, max(1, int(round(len(soft) * 0.01))), cand_stats["pnl_yen"], soft_stats["pnl_yen"]),
                "top5pct": _top_share(soft, max(1, int(round(len(soft) * 0.05))), cand_stats["pnl_yen"], soft_stats["pnl_yen"]),
                "top10pct": _top_share(soft, max(1, int(round(len(soft) * 0.10))), cand_stats["pnl_yen"], soft_stats["pnl_yen"]),
            },
        },
        "remove_top_winner": stress,
        "dates": {
            "best": day_delta[0]["delta"] if day_delta else None,
            "worst": day_delta[-1]["delta"] if day_delta else None,
            "top_delta": top_day_delta,
            "outside_top5_delta": outside5,
            "baseline_profitable_day_fraction": float(np.mean([r["base"] > 0 for r in day_rows])),
            "candidate_profitable_day_fraction": float(np.mean([r["cand"] > 0 for r in day_rows])),
            "rows": day_rows,
        },
        "symbols": {"top_pnl": top_sym, "exclude": exclude_sym, "top10": sym_rows[:10]},
        "periods": periods,
        "transitions": trans_out,
        "bsf_to_soft": {
            "n": len(primary),
            "baseline_pnl": float(sum(r["base_pnl"] for r in primary)),
            "candidate_pnl": float(sum(r["cand_pnl"] for r in primary)),
            "delta": float(sum(r["cand_pnl"] - r["base_pnl"] for r in primary)),
            "baseline_exit_bps_mean": float(np.mean([r["base_bps"] for r in primary])) if primary else None,
            "candidate_exit_bps_mean": float(np.mean([r["cand_bps"] for r in primary])) if primary else None,
            "extra_hold": distribution(holds),
            "mae_after": distribution([float(r["mae_after"]) for r in primary if r["mae_after"] is not None]),
            "mfe_after": distribution([float(r["mfe_after"]) for r in primary if r["mfe_after"] is not None]),
        },
        "further_drawdown": _tail_group(draw),
        "soft_exit_extra": _tail_group(soft_extra),
        "session_flat": _flat(flat),
        "occupancy": lost_by,
        "reentry": {
            "baseline_n": len(base_re), "baseline_pnl": float(sum(float(r["pnl_yen"]) for r in base_re)),
            "candidate_n": len(cand_re), "candidate_pnl": float(sum(float(r["pnl_yen"]) for r in cand_re)),
            "lost_reentry_n": len(lost_re), "lost_reentry_pnl": float(sum(float(r["pnl_yen"]) for r in lost_re)),
        },
        "asof_100ms": {"baseline": {**b100s, "mtm": payload["trackers"]["b100"]}, "candidate": {**c100s, "mtm": payload["trackers"]["c100"]},
                       "soft_pnl": float(sum(float(r["pnl_yen"]) for r in c100 if str(r["reason"]) == "IMPULSE_EXHAUSTED")),
                       "hard_pnl": float(sum(float(r["pnl_yen"]) for r in c100 if str(r["reason"]) == "BREAK_SUPPORT_FAILURE"))},
        "decision_rule": {
            "broad_if": "remaining PnL after removing top 10 and top 1% winners still beats baseline, top-5-day delta does not exhaust the complete delta, and PnL without the top 5 symbols still beats baseline",
            "risk_acceptable_if": "extra portfolio MTM drawdown versus baseline does not exceed the complete-strategy PnL gain",
        },
        "audit_sha256": "",
    }
    report["audit_sha256"] = sha256_obj({k: v for k, v in report.items() if k != "audit_sha256"})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    _xlsx(report)
    cache.unlink(missing_ok=True)
    print(f"VERDICT {verdict} NEXT {nxt}", flush=True)
    return report


def _tail_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(r["cand_pnl"]) - float(r["base_pnl"]) for r in rows]
    by_reason: dict[str, int] = {}
    for row in rows:
        by_reason[str(row["cand_reason"])] = by_reason.get(str(row["cand_reason"]), 0) + 1
    return {
        "n": len(rows),
        "delta": float(sum(deltas)),
        "delta_distribution": distribution(deltas),
        "mae_after": distribution([float(r["mae_after"]) for r in rows if r.get("mae_after") is not None]),
        "mfe_after": distribution([float(r["mfe_after"]) for r in rows if r.get("mfe_after") is not None]),
        "extra_hold": distribution([float(r["extra_hold"]) for r in rows]),
        "exit_reasons": by_reason,
        "date_concentration": _group_dates([{"date": r["date"], "delta": float(r["cand_pnl"]) - float(r["base_pnl"])} for r in rows]),
        "symbol_concentration": _group_dates([{"date": r["symbol"], "delta": float(r["cand_pnl"]) - float(r["base_pnl"])} for r in rows]),
    }


def _flat(rows: list[dict[str, Any]]) -> dict[str, Any]:
    am = [r for r in rows if str(r["session"]) == "AM"]
    pm = [r for r in rows if str(r["session"]) == "PM"]
    return {"n": len(rows), "pnl": float(sum(float(r["pnl_yen"]) for r in rows)), "am": _stats(am), "pm": _stats(pm), "mae": distribution([abs(float(r["mae_yen"])) for r in rows if r.get("mae_yen") is not None])}


def _markdown(report: dict[str, Any]) -> str:
    b, c = report["baseline"], report["candidate"]
    return "\n".join([
        f"# {report['study']}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        f"Baseline {b['trade_n']} / {b['pnl_yen']} / {b['pf']} / MTM {b['mtm']['mtm_max_drawdown_yen']}",
        f"Candidate {c['trade_n']} / {c['pnl_yen']} / {c['pf']} / MTM {c['mtm']['mtm_max_drawdown_yen']}",
        f"Delta {report['delta_pnl']}",
        "",
        "The audited change is the frozen rule that raises ACTIVE_SUPPORT_LEVEL on every reconfirm. Reconfirm still updates the soft-exit clock.",
        "",
        f"Integrity {json.dumps(report['gates'])}",
        "",
    ])


def _xlsx(report: dict[str, Any]) -> None:
    wb = Workbook()
    wb.active.title = "summary"
    wb.active.append(["verdict", report["verdict"], report["next"]])
    _sheet(wb, "dates", report["dates"]["rows"])
    _sheet(wb, "symbols", report["symbols"]["top10"])
    wb.save(OUT / "audit.xlsx")


if __name__ == "__main__":
    publish()
