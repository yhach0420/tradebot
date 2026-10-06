"""Publish the first-reconfirm entry test. One rule, no threshold search."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import bind
from research.v2_first_ratchet_transition_audit_v1.features import jpx_tick_size_yen
from research.v2_first_reconfirm_entry_asof_reevaluation_v1.scan import scan

OUT = Path("results/research/v2_first_reconfirm_entry_asof_reevaluation_v1")
BASE_N, BASE_PNL, BASE_PF = 11902, 1189150.0, 1.409303686366296
STRATEGY_SHA = "1d5ac587d4a67a7da4de9def64a6471428a136e4e30e1d26ca904983a8bcf505"
SIGNAL_SHA = "06345e9f7ddd2fcdf21a7beac49241495d3a4b6607715c879cf2ef0a307b4f68"


def _pf(rows: list[dict[str, Any]]) -> float | None:
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


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"trade_n": 0, "pnl_yen": 0.0, "pf": None, "mean_yen": None, "median_yen": None, "mean_bps": None, "median_bps": None, "win_rate": None, "max_drawdown_yen": 0.0, "gross_profit_yen": 0.0}
    pnl = np.array([float(r["pnl_yen"]) for r in rows], dtype=np.float64)
    bps = np.array([float(r["bps"]) for r in rows], dtype=np.float64)
    return {
        "trade_n": int(len(rows)),
        "pnl_yen": float(pnl.sum()),
        "pf": _pf(rows),
        "mean_yen": float(pnl.mean()),
        "median_yen": float(np.median(pnl)),
        "mean_bps": float(bps.mean()),
        "median_bps": float(np.median(bps)),
        "win_rate": float(np.mean(pnl > 0)),
        "max_drawdown_yen": _dd(rows),
        "gross_profit_yen": float(pnl[pnl > 0].sum()) if np.any(pnl > 0) else 0.0,
    }


def _key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (str(row["date"]), str(row["session"]), str(row["symbol"]), int(row.get("signal_index", -1)))


def _period(rows: list[dict[str, Any]], dates: set[str]) -> dict[str, Any]:
    stats = _stats([row for row in rows if str(row["date"]) in dates])
    return {k: stats[k] for k in ("trade_n", "pnl_yen", "pf", "mean_bps", "max_drawdown_yen")}


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p25": None, "p75": None, "p90": None, "p95": None}
    arr = np.array(values, dtype=np.float64)
    return {name: float(np.percentile(arr, q)) for name, q in (("median", 50), ("p25", 25), ("p75", 75), ("p90", 90), ("p95", 95))}


def _ratchet_group(rows: list[dict[str, Any]], name: str, pred) -> dict[str, Any]:
    chosen = [row for row in rows if pred(int(row["ratchets"]))]
    stats = _stats(chosen)
    return {"name": name, "n": stats["trade_n"], "pnl_yen": stats["pnl_yen"], "pf": stats["pf"], "mean_bps": stats["mean_bps"]}


def publish() -> dict[str, Any]:
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
    parity = (
        len(base) == BASE_N
        and abs(sum(float(r["pnl_yen"]) for r in base) - BASE_PNL) < 1e-6
        and abs((_pf(base) or 0) - BASE_PF) < 1e-12
        and got["identity_miss"] == 0
    )
    uid_ok = len(got["uids"]) == 11930 and len(set(got["uids"])) == 11930 and got["uids"] == got["uids2"]
    mapped = len({row["signal_uid"] for row in base})
    multi = len(base) - mapped
    integrity = bool(parity and uid_ok and mapped == BASE_N and multi == 0 and got["observer_mismatch"] == 0 and got["locate_mismatch"] == 0)
    base_stats = _stats(base)
    cand_stats = _stats(cand)
    with_rec = sum(1 for row in got["episodes"] if row["has_reconfirm"])
    without_rec = len(got["episodes"]) - with_rec
    delays = [float(r["delay_ms"]) for r in cand]
    tick_slip: list[float] = []
    bps_slip: list[float] = []
    for row in cand:
        orig = float(row["signal_ask"])
        ask = float(row["entry_px"])
        if not (orig == orig and orig > 0 and ask == ask):
            continue
        tick = jpx_tick_size_yen(orig)
        tick_slip.append((ask - orig) / tick)
        bps_slip.append((ask / orig - 1.0) * 10000.0)
    missed = [row for row in base if int(row["ratchets"]) == 0]
    groups = [
        _ratchet_group(cand, "post_entry_ratchet0", lambda n: n == 0),
        _ratchet_group(cand, "post_entry_ratchet1", lambda n: n == 1),
        _ratchet_group(cand, "post_entry_ratchet2", lambda n: n == 2),
        _ratchet_group(cand, "post_entry_ratchet3", lambda n: n == 3),
        _ratchet_group(cand, "post_entry_ratchet4plus", lambda n: n >= 4),
    ]
    r0 = groups[0]
    # Precommitted: the ratchet0 problem moved later if post-entry ratchet0 is still
    # at least a quarter of the candidate book and that book loses money.
    moved_later = bool(cand_stats["trade_n"] and r0["n"] >= 0.25 * cand_stats["trade_n"] and r0["pnl_yen"] < 0)
    base_map = {_key(row): row for row in base}
    cand_map = {_key(row): row for row in cand}
    new_rows = [row for key, row in cand_map.items() if key not in base_map]
    lost_rows = [row for key, row in base_map.items() if key not in cand_map and int(row["ratchets"]) > 0]
    dates = got["dates"]
    folds = [set(dates[i * len(dates) // 3 : (i + 1) * len(dates) // 3]) for i in range(3)]
    periods = {
        "ORIGINAL18": {"baseline": _period(base, set(got["original"])), "candidate": _period(cand, set(got["original"]))},
        "EXTENSION17": {"baseline": _period(base, set(got["extension"])), "candidate": _period(cand, set(got["extension"]))},
        "FOLD1": {"baseline": _period(base, folds[0]), "candidate": _period(cand, folds[0])},
        "FOLD2": {"baseline": _period(base, folds[1]), "candidate": _period(cand, folds[1])},
        "FOLD3": {"baseline": _period(base, folds[2]), "candidate": _period(cand, folds[2])},
    }
    b100 = _stats(got["baseline_100"])
    c100 = _stats(got["candidate_100"])
    pnl_up = cand_stats["pnl_yen"] > base_stats["pnl_yen"]
    pf_up = (cand_stats["pf"] or 0) > (base_stats["pf"] or 0)
    period_deltas = [block["candidate"]["pnl_yen"] - block["baseline"]["pnl_yen"] for block in periods.values()]
    mixed = min(period_deltas) < 0 and max(period_deltas) > 0
    lat_ok = c100["pnl_yen"] >= b100["pnl_yen"] and (c100["pf"] or 0) >= (b100["pf"] or 0)
    period_ok = all(block["candidate"]["pnl_yen"] >= block["baseline"]["pnl_yen"] and (block["candidate"]["pf"] or 0) >= (block["baseline"]["pf"] or 0) for block in periods.values())
    if not integrity:
        verdict, nxt = "INTEGRITY_BLOCKED_V1", "REPAIR_RECONSTRUCTION_INTEGRITY_ONLY"
    elif pnl_up and pf_up and period_ok and lat_ok:
        verdict, nxt = "V2_FIRST_RECONFIRM_ENTRY_SUPPORTED_V1", "COMPARE_FROZEN_V2_VS_CONFIRMED_ENTRY_ARCHITECTURE_V1"
    elif mixed:
        verdict, nxt = "V2_FIRST_RECONFIRM_ENTRY_NOT_ROBUST_V1", "KEEP_V2_UNCHANGED"
    elif pf_up and cand_stats["pnl_yen"] < base_stats["pnl_yen"]:
        verdict, nxt = "V2_FIRST_RECONFIRM_ENTRY_PRECISION_RECALL_TRADEOFF_V1", "KEEP_V2_UNCHANGED"
    else:
        verdict, nxt = "V2_FIRST_RECONFIRM_ENTRY_NOT_ACTIONABLE_V1", "KEEP_V2_UNCHANGED"
    report = {
        "study": "V2_FIRST_RECONFIRM_ENTRY_ASOF_REEVALUATION_V1",
        "research_only": True,
        "v2_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": "0/0/0",
        "strategy_sha256": bound["strategy_sha256"],
        "signal_sha256": bound["signal_sha256"],
        "baseline_parity_exact": parity,
        "signal_uid_integrity": integrity,
        "observer_mismatch": got["observer_mismatch"],
        "locate_mismatch": got["locate_mismatch"],
        "unmapped_trade_n": BASE_N - mapped if mapped <= BASE_N else 0,
        "multi_mapped_trade_n": multi,
        "support_semantics": "entry support is the level after the first frozen ratchet; that ratchet is pre_entry_confirmation_n=1 and is not counted again",
        "first_reconfirm": {"full_episodes_n": len(got["episodes"]), "with_first_reconfirm_n": with_rec, "without_first_reconfirm_n": without_rec},
        "delay_ms": _quantiles(delays),
        "entry_slippage_ticks": _quantiles(tick_slip),
        "entry_slippage_bps": _quantiles(bps_slip),
        "baseline_0ms": base_stats,
        "candidate_0ms": cand_stats,
        "delta": {
            "pnl_yen": cand_stats["pnl_yen"] - base_stats["pnl_yen"],
            "pf": None if cand_stats["pf"] is None or base_stats["pf"] is None else cand_stats["pf"] - base_stats["pf"],
            "mean_bps": None if cand_stats["mean_bps"] is None or base_stats["mean_bps"] is None else cand_stats["mean_bps"] - base_stats["mean_bps"],
            "max_drawdown_yen": cand_stats["max_drawdown_yen"] - base_stats["max_drawdown_yen"],
        },
        "profit_retention": {
            "pnl": None if base_stats["pnl_yen"] == 0 else cand_stats["pnl_yen"] / base_stats["pnl_yen"],
            "gross_profit": None if base_stats["gross_profit_yen"] == 0 else cand_stats["gross_profit_yen"] / base_stats["gross_profit_yen"],
        },
        "no_first_reconfirm_frozen_v2": _stats(missed),
        "post_entry_ratchet_groups": groups,
        "ratchet0_problem_moved_later": moved_later,
        "periods": periods,
        "asof_100ms": {"baseline": b100, "candidate": c100},
        "occupancy": {
            "baseline_first_entries": sum(1 for row in base if not row["reentry"]),
            "candidate_first_entries": sum(1 for row in cand if not row["reentry"]),
            "baseline_reentries": sum(1 for row in base if row["reentry"]),
            "candidate_reentries": sum(1 for row in cand if row["reentry"]),
            "newly_admitted": _stats(new_rows),
            "lost_or_reordered_eligible": _stats(lost_rows),
        },
        "genuine_structural_resistance_fraction": None if got["genuine_seen"] == 0 else got["genuine_hits"] / got["genuine_seen"],
        "complete_strategy_improvement": bool(integrity and pnl_up and pf_up),
        "robust_across_periods": bool(integrity and not mixed and period_ok),
        "latency_100ms_maintained": bool(integrity and lat_ok),
        "actionable": verdict.endswith("SUPPORTED_V1"),
        "verdict": verdict,
        "next": nxt,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# V2_FIRST_RECONFIRM_ENTRY_ASOF_REEVALUATION_V1",
        "",
        f"Verdict: {verdict}",
        f"Next: {nxt}",
        "",
        "Entry support is the frozen support after the first ratchet. That confirmation is not counted as a post-entry ratchet.",
        "",
        f"Baseline parity exact: {parity}",
        f"SIGNAL_UID integrity: {integrity}",
        f"FULL episodes: {len(got['episodes'])} with reconfirm {with_rec} without {without_rec}",
        f"Delay ms: {report['delay_ms']}",
        f"Slippage ticks: {report['entry_slippage_ticks']}",
        f"Slippage bps: {report['entry_slippage_bps']}",
        "",
        f"Baseline: {base_stats}",
        f"Candidate: {cand_stats}",
        f"Delta: {report['delta']}",
        f"Profit retention: {report['profit_retention']}",
        f"No first reconfirm frozen book: {report['no_first_reconfirm_frozen_v2']}",
        f"Post-entry groups: {groups}",
        f"Ratchet0 moved later: {moved_later}",
        f"Occupancy: {report['occupancy']}",
        f"Genuine fraction: {report['genuine_structural_resistance_fraction']}",
        "",
        "## Periods",
        "",
    ]
    for name, block in periods.items():
        lines.append(f"{name}: {block}")
    lines.extend(["", f"ASOF 100ms: {report['asof_100ms']}", ""])
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    wb = Workbook()
    ws = wb.active
    ws.title = "summary"
    for key in ("verdict", "next", "baseline_parity_exact", "signal_uid_integrity", "ratchet0_problem_moved_later", "genuine_structural_resistance_fraction"):
        ws.append([key, report[key]])
    ws.append(["candidate_n", cand_stats["trade_n"]])
    ws.append(["candidate_pnl", cand_stats["pnl_yen"]])
    ws.append(["candidate_pf", cand_stats["pf"]])
    ws.append(["baseline_n", base_stats["trade_n"]])
    ws.append(["baseline_pnl", base_stats["pnl_yen"]])
    ws.append(["baseline_pf", base_stats["pf"]])
    hold = wb.create_sheet("groups")
    hold.append(["group", "n", "pnl_yen", "pf", "mean_bps"])
    for group in groups:
        hold.append([group["name"], group["n"], group["pnl_yen"], group["pf"], group["mean_bps"]])
    wb.save(OUT / "audit.xlsx")
    cache.unlink(missing_ok=True)
    print(json.dumps({"verdict": verdict, "next": nxt, "parity": parity, "integrity": integrity}, sort_keys=True), flush=True)
    return report


if __name__ == "__main__":
    publish()
