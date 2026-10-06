"""Publish the single 500ms no-bid-uptick early-abort test."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import Workbook

from research.event_time_impulse_complete_strategy_v2.identity import bind
from research.v2_500ms_no_bid_uptick_early_abort_v1.scan import scan

OUT = Path("results/research/v2_500ms_no_bid_uptick_early_abort_v1")
BASE_N, BASE_PNL, BASE_PF = 11902, 1189150.0, 1.409303686366296


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
        return {"trade_n": 0, "pnl_yen": 0.0, "pf": None, "mean_yen": None, "median_yen": None, "mean_bps": None, "median_bps": None, "win_rate": None, "max_drawdown_yen": 0.0}
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
    }


def _key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (str(row["date"]), str(row["session"]), str(row["symbol"]), int(row.get("signal_index", -1)))


def _period(rows: list[dict[str, Any]], dates: set[str]) -> dict[str, Any]:
    chosen = [row for row in rows if str(row["date"]) in dates]
    stats = _stats(chosen)
    return {k: stats[k] for k in ("trade_n", "pnl_yen", "pf", "mean_bps", "max_drawdown_yen")}


def _group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name, pred in (
        ("ratchet0", lambda n: n == 0),
        ("ratchet1", lambda n: n == 1),
        ("ratchet2", lambda n: n == 2),
        ("ratchet3", lambda n: n == 3),
        ("ratchet4plus", lambda n: n >= 4),
        ("ratchet_ge1", lambda n: n >= 1),
    ):
        chosen = [row for row in rows if pred(int(row["ratchets"]))]
        out[name] = {"n": len(chosen), "pnl_yen": float(sum(float(r["pnl_yen"]) for r in chosen))}
    return out


def publish() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
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
    integrity = bool(parity and uid_ok and mapped == BASE_N and got["observer_mismatch"] == 0)
    pop = got["population"]
    exited = sum(1 for row in pop if row["bucket"] == "already_exited")
    ratcheted = sum(1 for row in pop if row["bucket"] == "already_ratcheted")
    open0 = [row for row in pop if row["bucket"] == "open_ratchet0"]
    none = [row for row in open0 if int(row["bid_upticks"] or 0) == 0]
    some = [row for row in open0 if int(row["bid_upticks"] or 0) >= 1]

    def _eventual(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        r0 = sum(1 for row in rows if int(row["ratchets"]) == 0)
        return {"n": n, "eventual_ratchet0": r0, "eventual_ratchet_ge1": n - r0}

    base_stats = _stats(base)
    cand_stats = _stats(cand)
    base_map = {_key(row): row for row in base}
    cand_map = {_key(row): row for row in cand}
    aborts = [row for row in cand if row["reason"] == "EARLY_ABORT"]
    original_aborts = [row for row in aborts if _key(row) in base_map]
    counter = [base_map[_key(row)] for row in original_aborts]
    new_rows = [row for key, row in cand_map.items() if key not in base_map]
    lost_rows = [row for key, row in base_map.items() if key not in cand_map]
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
    base_groups = _group(base)
    cand_groups = _group(cand)
    pnl_up = cand_stats["pnl_yen"] > base_stats["pnl_yen"]
    pf_up = (cand_stats["pf"] or 0) > (base_stats["pf"] or 0)
    period_deltas = [block["candidate"]["pnl_yen"] - block["baseline"]["pnl_yen"] for block in periods.values()]
    mixed = min(period_deltas) < 0 and max(period_deltas) > 0
    lat_ok = c100["pnl_yen"] >= b100["pnl_yen"]
    if pnl_up and pf_up and all(v >= 0 for v in period_deltas) and lat_ok:
        verdict, nxt = "V2_500MS_NO_BID_UPTICK_EARLY_ABORT_SUPPORTED_V1", "AUDIT_ENTRY_ALIGNED_EXIT_INTEGRATION_V1"
    elif pf_up and cand_stats["pnl_yen"] < base_stats["pnl_yen"]:
        verdict, nxt = "V2_500MS_NO_BID_UPTICK_PRECISION_RECALL_TRADEOFF_V1", "KEEP_V2_UNCHANGED"
    elif mixed:
        verdict, nxt = "V2_500MS_NO_BID_UPTICK_NOT_ROBUST_V1", "KEEP_V2_UNCHANGED"
    else:
        verdict, nxt = "V2_500MS_NO_BID_UPTICK_NOT_ACTIONABLE_V1", "KEEP_V2_UNCHANGED"
    report = {
        "study": "V2_500MS_NO_BID_UPTICK_EARLY_ABORT_V1",
        "research_only": True,
        "v2_changed": False,
        "new_data_acquired": False,
        "submit_cancel_live": "0/0/0",
        "strategy_sha256": bind()["strategy_sha256"],
        "baseline_parity_exact": parity,
        "signal_uid_integrity": integrity,
        "observer_mismatch": got["observer_mismatch"],
        "boundary": "events with t<=signal+500ms resolve exit then ratchet; abort only if still open and ratchet_n==0 and fresh bid upticks==0",
        "population": {
            "already_exited": exited,
            "already_ratcheted": ratcheted,
            "open_ratchet0": len(open0),
            "no_bid_uptick": _eventual(none),
            "bid_uptick": _eventual(some),
        },
        "baseline_0ms": base_stats,
        "candidate_0ms": cand_stats,
        "delta": {
            "pnl_yen": cand_stats["pnl_yen"] - base_stats["pnl_yen"],
            "pf": None if cand_stats["pf"] is None or base_stats["pf"] is None else cand_stats["pf"] - base_stats["pf"],
            "mean_bps": None if cand_stats["mean_bps"] is None else cand_stats["mean_bps"] - base_stats["mean_bps"],
            "max_drawdown_yen": cand_stats["max_drawdown_yen"] - base_stats["max_drawdown_yen"],
        },
        "early_abort": {
            "n": len(aborts),
            "original_path_n": len(original_aborts),
            "realized_pnl_yen": float(sum(float(r["pnl_yen"]) for r in aborts)),
            "original_path_realized_pnl_yen": float(sum(float(r["pnl_yen"]) for r in original_aborts)),
            "counterfactual_frozen_pnl_yen": float(sum(float(r["pnl_yen"]) for r in counter)),
            "saved_or_lost_yen": float(sum(float(r["pnl_yen"]) for r in original_aborts) - sum(float(r["pnl_yen"]) for r in counter)),
        },
        "ratchet_groups": {"baseline": base_groups, "candidate": cand_groups},
        "slot_release": {
            "newly_admitted": _stats(new_rows),
            "lost_or_reordered_n": len(lost_rows),
            "lost_or_reordered_pnl_yen": float(sum(float(r["pnl_yen"]) for r in lost_rows)),
        },
        "periods": periods,
        "asof_100ms": {"baseline": b100, "candidate": c100},
        "complete_strategy_improvement": bool(pnl_up and pf_up),
        "ratchet0_loss_reduced": cand_groups["ratchet0"]["pnl_yen"] > base_groups["ratchet0"]["pnl_yen"],
        "ratchet_ge1_profit_preserved": cand_groups["ratchet_ge1"]["pnl_yen"] >= base_groups["ratchet_ge1"]["pnl_yen"],
        "robust_across_periods": not mixed and all(v >= 0 for v in period_deltas),
        "latency_100ms_maintained": lat_ok,
        "actionable": verdict.endswith("SUPPORTED_V1"),
        "verdict": verdict,
        "next": nxt,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# V2_500MS_NO_BID_UPTICK_EARLY_ABORT_V1",
        "",
        f"Verdict: {verdict}",
        f"Next: {nxt}",
        "",
        "Baseline parity exact: " + str(parity),
        "SIGNAL_UID integrity: " + str(integrity),
        "",
        f"Already exited: {exited}",
        f"Already ratcheted: {ratcheted}",
        f"Open ratchet0: {len(open0)}",
        f"No bid uptick: {report['population']['no_bid_uptick']}",
        f"Bid uptick: {report['population']['bid_uptick']}",
        "",
        f"Baseline: {base_stats}",
        f"Candidate: {cand_stats}",
        f"Delta: {report['delta']}",
        f"Early abort: {report['early_abort']}",
        f"Ratchet groups baseline: {base_groups}",
        f"Ratchet groups candidate: {cand_groups}",
        f"Slot release: {report['slot_release']}",
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
    for key, value in (
        ("verdict", verdict),
        ("next", nxt),
        ("parity", parity),
        ("integrity", integrity),
        ("already_exited", exited),
        ("already_ratcheted", ratcheted),
        ("open_ratchet0", len(open0)),
        ("no_uptick_n", len(none)),
        ("uptick_n", len(some)),
        ("baseline_n", base_stats["trade_n"]),
        ("baseline_pnl", base_stats["pnl_yen"]),
        ("baseline_pf", base_stats["pf"]),
        ("candidate_n", cand_stats["trade_n"]),
        ("candidate_pnl", cand_stats["pnl_yen"]),
        ("candidate_pf", cand_stats["pf"]),
        ("abort_n", len(aborts)),
        ("new_admitted_n", len(new_rows)),
    ):
        ws.append([key, value])
    wb.save(OUT / "audit.xlsx")
    cache.unlink(missing_ok=True)
    print(json.dumps({"verdict": verdict, "next": nxt, "parity": parity, "integrity": integrity}, sort_keys=True), flush=True)
    return report


if __name__ == "__main__":
    publish()
