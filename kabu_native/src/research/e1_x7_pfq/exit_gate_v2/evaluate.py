"""Corrected pair evaluation: repairable ⊆ denominator."""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Optional

from . import (
    CANDIDATE_ID,
    GATE_MIN_REPAIRABLE_DAYS,
    GATE_MIN_REPAIRABLE_FRACTION,
    GATE_MIN_REPAIRABLE_N,
    GATE_MIN_TOP_MECH_FRACTION,
    KNOWN,
    KNOWN_DENOM,
    MECH_GIVEBACK,
    MECH_SOFT,
    PAIRS,
    REF_PROFITABLE_SOFT,
)


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def is_denominator(best_net: Any, realized: Any) -> bool:
    b = _f(best_net)
    r = _f(realized)
    return b is not None and b >= 5.0 - 1e-12 and r is not None and r < 0.0


def is_giveback(*, t_plus5: Any, exit_time: Any, realized: Any) -> bool:
    tp = _f(t_plus5)
    et = _f(exit_time)
    r = _f(realized)
    if tp is None or et is None or r is None:
        return False
    return tp <= et + 1e-12 and r <= 0.0


def is_soft_premature(cf_label: Any) -> bool:
    return cf_label == MECH_SOFT


def assign_mechanism(*, giveback: bool, soft_premature: bool) -> Optional[str]:
    if giveback:
        return MECH_GIVEBACK
    if soft_premature:
        return MECH_SOFT
    return None


def evaluate_pair(
    trades: list[dict[str, Any]],
    *,
    pair_id: str,
    fixed_grid_by_eid: dict[str, dict[str, Any]],
    cf_by_key: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    rows = [t for t in trades if t.get("pair_id") == pair_id and t.get("integrity_status") == "PASS"]
    eids = [t["episode_id"] for t in rows]
    dup = len(eids) - len(set(eids))

    denom_eids: list[str] = []
    repair_rows: list[dict[str, Any]] = []
    opp_cost_rows: list[dict[str, Any]] = []
    integrity_rows: list[dict[str, Any]] = []

    for t in rows:
        eid = t["episode_id"]
        fg = fixed_grid_by_eid.get(eid) or {}
        cf = cf_by_key.get((pair_id, eid)) or {}
        best = fg.get("best_net_pnl_bps_300s")
        realized = t.get("exit_net_pnl_bps")
        in_denom = is_denominator(best, realized)
        if in_denom:
            denom_eids.append(eid)

        giveback = is_giveback(
            t_plus5=fg.get("t_plus5"),
            exit_time=t.get("exit_time"),
            realized=realized,
        )
        soft_prem = is_soft_premature(cf.get("label"))
        mech = assign_mechanism(giveback=giveback, soft_premature=soft_prem)

        # Profitable soft-exit opportunity cost (reference only)
        r_f = _f(realized)
        profitable_soft = (
            (not in_denom)
            and r_f is not None
            and r_f >= 0.0
            and soft_prem
        )

        in_repairable_gate = bool(in_denom and mech is not None)
        exclusion_reason = None
        if mech is not None and not in_denom:
            if profitable_soft:
                exclusion_reason = REF_PROFITABLE_SOFT
            else:
                exclusion_reason = "OUT_OF_DENOMINATOR_NOT_REALIZED_LOSS"
        elif mech is None:
            exclusion_reason = "NO_REPAIRABLE_MECHANISM"

        additional_oracle = None
        if best is not None and r_f is not None:
            additional_oracle = float(best) - float(r_f)

        integrity_rows.append({
            "pair_id": pair_id,
            "episode_id": eid,
            "day": t.get("day"),
            "symbol": t.get("symbol"),
            "fixed_grid_best_net_pnl_bps_300s": best,
            "realized_net_pnl_bps": realized,
            "in_denominator": in_denom,
            "mechanism": mech if in_repairable_gate else (REF_PROFITABLE_SOFT if profitable_soft else mech),
            "in_repairable_gate": in_repairable_gate,
            "profitable_soft_exit_opportunity_cost": profitable_soft,
            "exclusion_reason": None if in_repairable_gate else exclusion_reason,
            "exit_reason": t.get("exit_reason"),
            "cf_label": cf.get("label"),
            "additional_oracle_bps": additional_oracle,
        })

        if in_repairable_gate:
            repair_rows.append({
                "pair_id": pair_id,
                "episode_id": eid,
                "day": t.get("day"),
                "symbol": t.get("symbol"),
                "exit_reason": t.get("exit_reason"),
                "realized_net_pnl_bps": realized,
                "best_net_pnl_bps_300s": best,
                "in_denominator": True,
                "mechanism": mech,
                "cf_label": cf.get("label"),
            })
        if profitable_soft:
            opp_cost_rows.append({
                "pair_id": pair_id,
                "episode_id": eid,
                "day": t.get("day"),
                "symbol": t.get("symbol"),
                "realized_net_pnl_bps": realized,
                "best_net_pnl_bps_300s": best,
                "additional_oracle_bps": additional_oracle,
                "cf_label": cf.get("label"),
                "exit_reason": t.get("exit_reason"),
                "classification": REF_PROFITABLE_SOFT,
            })

    # subset invariant
    repair_ids = {r["episode_id"] for r in repair_rows}
    denom_set = set(denom_eids)
    assert repair_ids <= denom_set, f"subset invariant violated for {pair_id}"

    mech_counts = Counter(r["mechanism"] for r in repair_rows)
    top_mech, top_n = (mech_counts.most_common(1)[0] if mech_counts else (None, 0))
    repairable_n = len(repair_rows)
    denom_n = len(denom_eids)
    days = {r["day"] for r in repair_rows}
    frac = (repairable_n / denom_n) if denom_n else 0.0
    top_frac = (top_n / repairable_n) if repairable_n else 0.0

    realized_opp = [_f(r["realized_net_pnl_bps"]) for r in opp_cost_rows]
    add_opp = [_f(r["additional_oracle_bps"]) for r in opp_cost_rows]
    realized_opp = [x for x in realized_opp if x is not None]
    add_opp = [x for x in add_opp if x is not None]

    return {
        "pair_id": pair_id,
        "exit_candidate": pair_id.split("|", 1)[1],
        "unique_episode_n": len(set(eids)),
        "trade_rows": len(rows),
        "duplicate_episode_within_pair": dup,
        "denominator_n": denom_n,
        "oracle_plus5_realized_loss_n": denom_n,
        "denominator_episode_ids": sorted(denom_eids),
        "repairable_in_denominator_n": repairable_n,
        "repairable_n": repairable_n,  # alias = in-denominator only
        "repairable_episode_ids": sorted(r["episode_id"] for r in repair_rows),
        "repairable_fraction": frac,
        "repairable_days": len(days),
        "repairable_day_list": sorted(days),
        "failure_mechanism_counts": dict(mech_counts),
        "top_mechanism": top_mech,
        "top_mechanism_fraction": top_frac,
        "profitable_soft_exit_opportunity_cost_n": len(opp_cost_rows),
        "median_realized_net_pnl_bps": float(median(realized_opp)) if realized_opp else None,
        "median_additional_oracle_bps": float(median(add_opp)) if add_opp else None,
        "subset_invariant_ok": repair_ids <= denom_set,
        "known_denominator_expected": KNOWN_DENOM.get(pair_id),
        "denominator_matches_known": denom_n == KNOWN_DENOM.get(pair_id),
        "repairable_rows": repair_rows,
        "opp_cost_rows": opp_cost_rows,
        "integrity_rows": integrity_rows,
    }


def gate_pair(
    pair_res: dict[str, Any],
    *,
    entry_path_support: bool,
    identity_ok: bool,
    ab_ok: bool,
) -> dict[str, Any]:
    checks = {
        "entry_path_support": entry_path_support,
        "repairable_in_denominator_n_ge_20": pair_res["repairable_in_denominator_n"] >= GATE_MIN_REPAIRABLE_N,
        "repairable_days_ge_5": pair_res["repairable_days"] >= GATE_MIN_REPAIRABLE_DAYS,
        "repairable_fraction_ge_050": pair_res["repairable_fraction"] >= GATE_MIN_REPAIRABLE_FRACTION - 1e-15,
        "top_mechanism_fraction_ge_050": pair_res["top_mechanism_fraction"] >= GATE_MIN_TOP_MECH_FRACTION - 1e-15,
        "identity_integrity_pass": identity_ok and pair_res["duplicate_episode_within_pair"] == 0 and pair_res.get("subset_invariant_ok", False),
        "ab_determinism_pass": ab_ok,
    }
    return {"pair_id": pair_res["pair_id"], "pass": all(checks.values()), "checks": checks}


def check_identity(trades: list[dict[str, Any]]) -> dict[str, Any]:
    upd = [t for t in trades if t.get("candidate_id") == CANDIDATE_ID and t.get("integrity_status") == "PASS"]
    by_pair = {pid: [t for t in upd if t["pair_id"] == pid] for pid in PAIRS}
    unique_eps = sorted({t["episode_id"] for t in upd})
    dups = {pid: len(rows) - len({t["episode_id"] for t in rows}) for pid, rows in by_pair.items()}
    ok = (
        len(unique_eps) == KNOWN["unique_episode_n"]
        and len(by_pair[PAIRS[0]]) == KNOWN["progress_trade_rows"]
        and len(by_pair[PAIRS[1]]) == KNOWN["protect_trade_rows"]
        and len(upd) == KNOWN["total_pair_trade_rows"]
        and all(v == 0 for v in dups.values())
    )
    return {
        "ok": ok,
        "unique_episode_n": len(unique_eps),
        "progress_trade_rows": len(by_pair[PAIRS[0]]),
        "protect_trade_rows": len(by_pair[PAIRS[1]]),
        "total_pair_trade_rows": len(upd),
        "duplicate_within_pair": dups,
        "known": KNOWN,
    }


def decide_verdict(gates: dict[str, dict[str, Any]], pair_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    passed = [pid for pid, g in gates.items() if g["pass"]]
    if len(passed) == 0:
        return {
            "verdict": "E1_X7_PFQ_NO_QUALIFIED_EXIT_REVISION_BASELINE",
            "pfq_close": True,
            "exit_revision": False,
            "selected_baseline_pair": None,
            "selected_exit_candidate": None,
            "passed_pairs": [],
        }
    if len(passed) == 1:
        pid = passed[0]
        pr = pair_results[pid]
        return {
            "verdict": "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED",
            "pfq_close": False,
            "exit_revision": True,
            "exit_revision_implemented": False,
            "selected_baseline_pair": pid,
            "selected_exit_candidate": pr["exit_candidate"],
            "dominant_failure_mechanism": pr["top_mechanism"],
            "repairable_n": pr["repairable_in_denominator_n"],
            "repairable_fraction": pr["repairable_fraction"],
            "repairable_days": pr["repairable_days"],
            "passed_pairs": passed,
        }
    return {
        "verdict": "E1_X7_PFQ_MULTIPLE_EXIT_BASELINES_REVIEW_REQUIRED",
        "pfq_close": False,
        "exit_revision": False,
        "selected_baseline_pair": None,
        "selected_exit_candidate": None,
        "passed_pairs": passed,
        "note": "no automatic selection",
    }


def expected_delta(pair_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected = {
        PAIRS[0]: {
            "denominator_n": 62,
            "repairable_in_denominator_n": 35,
            "repairable_fraction": 35 / 62,
            "mechanisms": {MECH_GIVEBACK: 31, MECH_SOFT: 4},
            "gate": "PASS",
        },
        PAIRS[1]: {
            "denominator_n": 46,
            "repairable_in_denominator_n": 19,
            "repairable_fraction": 19 / 46,
            "profitable_soft_exit_opportunity_cost_n": 21,
            "mechanisms": {MECH_GIVEBACK: 19},
            "gate": "FAIL",
        },
    }
    out = {}
    for pid, exp in expected.items():
        got = pair_results[pid]
        mismatch = []
        if got["denominator_n"] != exp["denominator_n"]:
            mismatch.append("denominator_n")
        if got["repairable_in_denominator_n"] != exp["repairable_in_denominator_n"]:
            mismatch.append("repairable_in_denominator_n")
        if abs(got["repairable_fraction"] - exp["repairable_fraction"]) > 1e-9:
            mismatch.append("repairable_fraction")
        if got["failure_mechanism_counts"] != exp.get("mechanisms"):
            mismatch.append("mechanisms")
        if pid == PAIRS[1] and got["profitable_soft_exit_opportunity_cost_n"] != exp.get("profitable_soft_exit_opportunity_cost_n"):
            mismatch.append("profitable_soft_exit_opportunity_cost_n")
        out[pid] = {"expected": exp, "actual_summary": {
            "denominator_n": got["denominator_n"],
            "repairable_in_denominator_n": got["repairable_in_denominator_n"],
            "repairable_fraction": got["repairable_fraction"],
            "mechanisms": got["failure_mechanism_counts"],
            "profitable_soft_exit_opportunity_cost_n": got["profitable_soft_exit_opportunity_cost_n"],
        }, "mismatch_fields": mismatch, "match": len(mismatch) == 0}
    return out
