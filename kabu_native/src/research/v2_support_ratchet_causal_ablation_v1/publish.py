"""Publish the support-ratchet ablation. One mechanism, no retune."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import bind
from research.v2_support_ratchet_causal_ablation_v1.ablation_sim import self_check
from research.v2_support_ratchet_causal_ablation_v1.scan import scan

OUT = Path("results/research/v2_support_ratchet_causal_ablation_v1")
BASE_N, BASE_PNL, BASE_PF = 11902, 1189150.0, 1.409303686366296
STRATEGY_SHA = "1d5ac587d4a67a7da4de9def64a6471428a136e4e30e1d26ca904983a8bcf505"
SIGNAL_SHA = "06345e9f7ddd2fcdf21a7beac49241495d3a4b6607715c879cf2ef0a307b4f68"
# Predeclared before the scan. Equal exit prices are the only "no meaningful recovery" case.
# Reason classes take priority over the price comparison.
EXTRA_HOLD_DOC = (
    "Eligible when the same SIGNAL_UID exits later in the ablation and the frozen reason is BREAK_SUPPORT_FAILURE. "
    "SESSION_CLOSE if the ablated reason is SESSION_FLAT. SOFT_EXIT if it is IMPULSE_EXHAUSTED. "
    "Otherwise RECOVERED_ABOVE_BASELINE_EXIT when the ablated exit bid is above the frozen exit bid, "
    "FURTHER_DRAWDOWN when it is below, and NO_MEANINGFUL_RECOVERY when the bids are equal."
)


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    gain = sum(float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) > 0)
    loss = sum(-float(r["pnl_yen"]) for r in rows if float(r["pnl_yen"]) < 0)
    return gain / loss if loss else None


def _dd(rows: list[dict[str, Any]]) -> float:
    ordered = sorted(rows, key=lambda r: (str(r["date"]), str(r["session"]), float(r["exit_t"]), str(r["symbol"])))
    equity = peak = 0.0
    worst = 0.0
    for row in ordered:
        equity += float(row["pnl_yen"])
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _q(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    return float(np.percentile(np.array(values, dtype=np.float64), pct))


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"trade_n": 0, "pnl_yen": 0.0, "pf": None, "gross_profit_yen": 0.0, "gross_loss_yen": 0.0, "mean_yen": None, "median_yen": None, "mean_bps": None, "median_bps": None, "win_rate": None, "max_drawdown_yen": 0.0, "mean_hold_sec": None, "median_hold_sec": None}
    pnl = np.array([float(r["pnl_yen"]) for r in rows], dtype=np.float64)
    bps = np.array([float(r["bps"]) for r in rows], dtype=np.float64)
    hold = [float(r["hold_sec"]) for r in rows if r.get("hold_sec") is not None]
    return {
        "trade_n": int(len(rows)),
        "pnl_yen": float(pnl.sum()),
        "pf": _pf(rows),
        "gross_profit_yen": float(pnl[pnl > 0].sum()) if np.any(pnl > 0) else 0.0,
        "gross_loss_yen": float((-pnl[pnl < 0]).sum()) if np.any(pnl < 0) else 0.0,
        "mean_yen": float(pnl.mean()),
        "median_yen": float(np.median(pnl)),
        "mean_bps": float(bps.mean()),
        "median_bps": float(np.median(bps)),
        "win_rate": float(np.mean(pnl > 0)),
        "max_drawdown_yen": _dd(rows),
        "mean_hold_sec": None if not hold else float(np.mean(hold)),
        "median_hold_sec": _q(hold, 50),
    }


def _giveback(rows: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    values = [float(r["giveback"]) for r in rows if r.get("giveback") is not None]
    return {"mean": None if not values else float(np.mean(values)), "median": _q(values, 50), "p75": _q(values, 75), "p90": _q(values, 90), "p95": _q(values, 95), "n": len(values)}


def _key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (str(row["date"]), str(row["session"]), str(row["symbol"]), int(row["signal_index"]))


def _reason_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    reasons = sorted({str(r["reason"]) for r in rows})
    for reason in reasons:
        chosen = [r for r in rows if str(r["reason"]) == reason]
        stats = _stats(chosen)
        out[reason] = {k: stats[k] for k in ("trade_n", "pnl_yen", "pf", "mean_bps", "median_hold_sec")}
    return out


def _ratchet_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name, pred in (
        ("ratchet0", lambda n: n == 0),
        ("ratchet1", lambda n: n == 1),
        ("ratchet2", lambda n: n == 2),
        ("ratchet3", lambda n: n == 3),
        ("ratchet4plus", lambda n: n >= 4),
    ):
        chosen = [r for r in rows if pred(int(r["ratchets"]))]
        stats = _stats(chosen)
        out[name] = {k: stats[k] for k in ("trade_n", "pnl_yen", "pf", "mean_bps")}
        out[name]["giveback"] = _giveback(chosen)
    return out


def _period(rows: list[dict[str, Any]], dates: set[str]) -> dict[str, Any]:
    chosen = [r for r in rows if str(r["date"]) in dates]
    stats = _stats(chosen)
    kept = {k: stats[k] for k in ("trade_n", "pnl_yen", "pf", "mean_bps", "max_drawdown_yen")}
    kept["giveback_median"] = _giveback(chosen)["median"]
    return kept


def publish() -> dict[str, Any]:
    self_check()
    OUT.mkdir(parents=True, exist_ok=True)
    bound = bind()
    if bound["strategy_sha256"] != STRATEGY_SHA or bound["signal_sha256"] != SIGNAL_SHA:
        raise RuntimeError("frozen_sha_mismatch")
    cache = OUT / "_scan.pkl"
    if cache.exists():
        got = pickle.loads(cache.read_bytes())
    else:
        got = scan()
        cache.write_bytes(pickle.dumps(got, protocol=pickle.HIGHEST_PROTOCOL))
    base = got["baseline"]
    cand = got["candidate"]
    base_stats = _stats(base)
    cand_stats = _stats(cand)
    parity = (
        base_stats["trade_n"] == BASE_N
        and abs(base_stats["pnl_yen"] - BASE_PNL) < 1e-6
        and base_stats["pf"] is not None
        and abs(base_stats["pf"] - BASE_PF) < 1e-12
        and got["identity_miss"] == 0
    )
    uid_ok = len(got["uids"]) == 11930 and len(set(got["uids"])) == 11930 and got["uids"] == got["uids2"]
    mapped = len({r["signal_uid"] for r in base})
    multi = len(base) - mapped
    integrity = bool(parity and uid_ok and mapped == BASE_N and multi == 0 and got["observer_mismatch"] == 0 and got["walk_mismatch"] == 0)
    base_map = {_key(r): r for r in base}
    cand_map = {_key(r): r for r in cand}
    matched_keys = [key for key in base_map if key in cand_map]
    new_rows = [row for key, row in cand_map.items() if key not in base_map]
    lost_rows = [row for key, row in base_map.items() if key not in cand_map]
    matched_delta = sum(float(cand_map[key]["pnl_yen"]) - float(base_map[key]["pnl_yen"]) for key in matched_keys)
    new_pnl = sum(float(r["pnl_yen"]) for r in new_rows)
    lost_pnl = sum(float(r["pnl_yen"]) for r in lost_rows)
    complete_delta = cand_stats["pnl_yen"] - base_stats["pnl_yen"]
    reconciled = abs((matched_delta + new_pnl - lost_pnl) - complete_delta) < 1e-4
    same_exit = later_exit = different_reason = session_conversion = 0
    classes = {"RECOVERED_ABOVE_BASELINE_EXIT": [], "NO_MEANINGFUL_RECOVERY": [], "FURTHER_DRAWDOWN": [], "SESSION_CLOSE": [], "SOFT_EXIT": []}
    for key in matched_keys:
        old, new = base_map[key], cand_map[key]
        same_time = abs(float(new["exit_t"]) - float(old["exit_t"])) <= 1e-3 and abs(float(new["exit_px"]) - float(old["exit_px"])) <= 1e-6
        if str(old["reason"]) == str(new["reason"]) and same_time:
            same_exit += 1
        if float(new["exit_t"]) > float(old["exit_t"]) + 1e-3:
            later_exit += 1
        if str(old["reason"]) != str(new["reason"]):
            different_reason += 1
        if str(old["reason"]) != "SESSION_FLAT" and str(new["reason"]) == "SESSION_FLAT":
            session_conversion += 1
        if str(old["reason"]) == "BREAK_SUPPORT_FAILURE" and float(new["exit_t"]) > float(old["exit_t"]) + 1e-3:
            if str(new["reason"]) == "SESSION_FLAT":
                label = "SESSION_CLOSE"
            elif str(new["reason"]) == "IMPULSE_EXHAUSTED":
                label = "SOFT_EXIT"
            elif float(new["exit_px"]) > float(old["exit_px"]):
                label = "RECOVERED_ABOVE_BASELINE_EXIT"
            elif float(new["exit_px"]) < float(old["exit_px"]):
                label = "FURTHER_DRAWDOWN"
            else:
                label = "NO_MEANINGFUL_RECOVERY"
            classes[label].append(float(new["pnl_yen"]) - float(old["pnl_yen"]))
    dates = got["dates"]
    folds = [set(dates[i * len(dates) // 3 : (i + 1) * len(dates) // 3]) for i in range(3)]
    period_sets = {
        "ORIGINAL18": set(got["original"]),
        "EXTENSION17": set(got["extension"]),
        "FOLD1": folds[0],
        "FOLD2": folds[1],
        "FOLD3": folds[2],
    }
    periods = {name: {"baseline": _period(base, chosen), "candidate": _period(cand, chosen)} for name, chosen in period_sets.items()}
    period_deltas = [block["candidate"]["pnl_yen"] - block["baseline"]["pnl_yen"] for block in periods.values()]
    mixed = min(period_deltas) < 0 and max(period_deltas) > 0
    b100 = _stats(got["baseline_100"])
    c100 = _stats(got["candidate_100"])
    base_gb = _giveback(base)
    cand_gb = _giveback(cand)
    worsens = cand_stats["pnl_yen"] < base_stats["pnl_yen"] and (cand_stats["pf"] or 0) < (base_stats["pf"] or 0)
    improves = cand_stats["pnl_yen"] > base_stats["pnl_yen"] and (cand_stats["pf"] or 0) > (base_stats["pf"] or 0)
    unchanged = abs(cand_stats["pnl_yen"] - base_stats["pnl_yen"]) <= 0.02 * abs(base_stats["pnl_yen"]) and abs((cand_stats["pf"] or 0) - (base_stats["pf"] or 0)) <= 0.02
    periods_not_better = all(delta <= 0 for delta in period_deltas)
    latency_not_better = c100["pnl_yen"] <= b100["pnl_yen"]
    if not integrity:
        verdict, nxt = "INTEGRITY_BLOCKED_V1", "REPAIR_RECONSTRUCTION_INTEGRITY_ONLY"
    elif mixed:
        verdict, nxt = "V2_SUPPORT_RATCHET_EFFECT_NOT_STABLE_V1", "KEEP_V2_UNCHANGED"
    elif worsens and periods_not_better and latency_not_better:
        verdict, nxt = "V2_SUPPORT_RATCHET_ECONOMICALLY_CAUSAL_V1", "FREEZE_SUPPORT_RATCHET_AS_CORE_EXIT_MECHANISM"
    elif improves and all(delta >= 0 for delta in period_deltas):
        verdict, nxt = "V2_SUPPORT_RATCHET_HARMFUL_V1", "AUDIT_SUPPORT_RATCHET_REMOVAL_AS_COMPLETE_STRATEGY_CANDIDATE"
    elif unchanged:
        verdict, nxt = "V2_SUPPORT_RATCHET_ECONOMICALLY_REDUNDANT_V1", "KEEP_V2_UNCHANGED"
    else:
        verdict, nxt = "V2_SUPPORT_RATCHET_EFFECT_NOT_STABLE_V1", "KEEP_V2_UNCHANGED"
    gap_by_ratchet = {}
    for name, pred in (("ratchet0", lambda n: n == 0), ("ratchet1", lambda n: n == 1), ("ratchet2", lambda n: n == 2), ("ratchet3", lambda n: n == 3), ("ratchet4plus", lambda n: n >= 4)):
        chosen = [r for r in base if pred(int(r["ratchets"]))]
        gap_by_ratchet[name] = {
            "n": len(chosen),
            "median_max_gap_ticks": _q([float(r["max_gap_ticks"]) for r in chosen if r.get("max_gap_ticks") is not None], 50),
            "median_max_gap_bps": _q([float(r["max_gap_bps"]) for r in chosen if r.get("max_gap_bps") is not None], 50),
        }
    extra = {}
    for label, deltas in classes.items():
        extra[label] = {"n": len(deltas), "pnl_delta_yen": float(sum(deltas))}
    report = {
        "study": "V2_SUPPORT_RATCHET_CAUSAL_ABLATION_V1",
        "research_only": True,
        "v2_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": "0/0/0",
        "strategy_sha256": bound["strategy_sha256"],
        "signal_sha256": bound["signal_sha256"],
        "baseline_parity_exact": parity,
        "signal_uid_integrity": integrity,
        "observer_mismatch": got["observer_mismatch"],
        "walk_mismatch": got["walk_mismatch"],
        "mapped_trade_n": mapped,
        "unmapped_trade_n": len(base) - mapped,
        "multi_mapped_trade_n": multi,
        "extra_hold_definitions": EXTRA_HOLD_DOC,
        "baseline_0ms": base_stats,
        "candidate_0ms": cand_stats,
        "baseline_giveback_bps": base_gb,
        "candidate_giveback_bps": cand_gb,
        "delta": {
            "pnl_yen": complete_delta,
            "pf": None if base_stats["pf"] is None or cand_stats["pf"] is None else cand_stats["pf"] - base_stats["pf"],
            "gross_profit_yen": cand_stats["gross_profit_yen"] - base_stats["gross_profit_yen"],
            "gross_loss_yen": cand_stats["gross_loss_yen"] - base_stats["gross_loss_yen"],
            "mean_bps": None if base_stats["mean_bps"] is None or cand_stats["mean_bps"] is None else cand_stats["mean_bps"] - base_stats["mean_bps"],
            "max_drawdown_yen": cand_stats["max_drawdown_yen"] - base_stats["max_drawdown_yen"],
            "giveback_median_bps": None if base_gb["median"] is None or cand_gb["median"] is None else cand_gb["median"] - base_gb["median"],
        },
        "exit_reasons": {"baseline": _reason_block(base), "candidate": _reason_block(cand)},
        "ratchet_groups": {"baseline": _ratchet_block(base), "candidate": _ratchet_block(cand)},
        "support_gap_by_frozen_ratchet_count": gap_by_ratchet,
        "matched": {
            "n": len(matched_keys),
            "same_exit_n": same_exit,
            "later_exit_n": later_exit,
            "different_exit_reason_n": different_reason,
            "session_close_conversion_n": session_conversion,
            "pnl_delta_yen": matched_delta,
        },
        "extra_hold": extra,
        "decomposition": {
            "matched_pnl_delta_yen": matched_delta,
            "newly_admitted_pnl_yen": new_pnl,
            "lost_pnl_yen": lost_pnl,
            "complete_pnl_delta_yen": complete_delta,
            "reconciled": reconciled,
        },
        "occupancy": {"newly_admitted": _stats(new_rows), "lost_or_displaced": _stats(lost_rows)},
        "periods": periods,
        "asof_100ms": {"baseline": {k: b100[k] for k in ("trade_n", "pnl_yen", "pf")}, "candidate": {k: c100[k] for k in ("trade_n", "pnl_yen", "pf")}},
        "disabling_worsens_complete_strategy": bool(integrity and worsens),
        "support_ratchet_reduces_giveback": bool(base_gb["median"] is not None and cand_gb["median"] is not None and base_gb["median"] < cand_gb["median"]),
        "support_ratchet_improves_max_drawdown": bool(base_stats["max_drawdown_yen"] > cand_stats["max_drawdown_yen"]),
        "robust_across_periods": bool(integrity and not mixed and ((worsens and periods_not_better) or (improves and all(d >= 0 for d in period_deltas)) or unchanged)),
        "economically_causal": verdict.endswith("ECONOMICALLY_CAUSAL_V1"),
        "supported_as_prediction_or_confirmation": False,
        "verdict": verdict,
        "next": nxt,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        f"# {verdict}",
        "",
        f"NEXT: {nxt}",
        "",
        "Ablation: reconfirm still updates the soft-exit clock. ACTIVE_SUPPORT_LEVEL stays at the entry PRE_BREAK_HIGH.",
        "",
        EXTRA_HOLD_DOC,
        "",
        f"parity={parity} integrity={integrity} reconciled={reconciled}",
        f"baseline={base_stats}",
        f"candidate={cand_stats}",
        f"delta={report['delta']}",
        f"giveback baseline={base_gb}",
        f"giveback candidate={cand_gb}",
        f"matched={report['matched']}",
        f"extra_hold={extra}",
        f"decomposition={report['decomposition']}",
        f"occupancy newly={report['occupancy']['newly_admitted']}",
        f"occupancy lost={report['occupancy']['lost_or_displaced']}",
        "",
    ]
    for name, block in periods.items():
        lines.append(f"{name}: {block}")
    lines.extend(["", f"ASOF 100ms: {report['asof_100ms']}", "", "V2 changed: false", "submit/cancel/live: 0/0/0", ""])
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    wb = Workbook()
    ws = wb.active
    ws.title = "summary"
    for key in ("verdict", "next", "baseline_parity_exact", "signal_uid_integrity", "disabling_worsens_complete_strategy", "support_ratchet_reduces_giveback", "support_ratchet_improves_max_drawdown", "robust_across_periods", "economically_causal"):
        ws.append([key, report[key]])
    ws.append(["baseline_pnl", base_stats["pnl_yen"]])
    ws.append(["candidate_pnl", cand_stats["pnl_yen"]])
    ws.append(["baseline_pf", base_stats["pf"]])
    ws.append(["candidate_pf", cand_stats["pf"]])
    ws.append(["reconciled", reconciled])
    table = wb.create_sheet("comparison")
    table.append(["book", "trade_n", "pnl_yen", "pf", "gross_profit", "gross_loss", "mean_bps", "max_drawdown", "median_giveback_bps"])
    table.append(["baseline", base_stats["trade_n"], base_stats["pnl_yen"], base_stats["pf"], base_stats["gross_profit_yen"], base_stats["gross_loss_yen"], base_stats["mean_bps"], base_stats["max_drawdown_yen"], base_gb["median"]])
    table.append(["ratchet_off", cand_stats["trade_n"], cand_stats["pnl_yen"], cand_stats["pf"], cand_stats["gross_profit_yen"], cand_stats["gross_loss_yen"], cand_stats["mean_bps"], cand_stats["max_drawdown_yen"], cand_gb["median"]])
    wb.save(OUT / "audit.xlsx")
    cache.unlink(missing_ok=True)
    print(json.dumps({"verdict": verdict, "next": nxt, "parity": parity, "integrity": integrity, "reconciled": reconciled}, sort_keys=True), flush=True)
    return report


if __name__ == "__main__":
    publish()
