"""Pair-specific denominator / repairable / mechanism / Gate evaluation."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from . import (
    CANDIDATE_ID,
    GATE_MIN_REPAIRABLE_DAYS,
    GATE_MIN_REPAIRABLE_FRACTION,
    GATE_MIN_REPAIRABLE_N,
    GATE_MIN_TOP_MECH_FRACTION,
    KNOWN,
    MECH_GIVEBACK,
    MECH_SOFT,
    PAIRS,
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
    """+5 before actual exit, then non-positive realized."""
    tp = _f(t_plus5)
    et = _f(exit_time)
    r = _f(realized)
    if tp is None or et is None or r is None:
        return False
    return tp <= et + 1e-12 and r <= 0.0


def is_soft_premature(cf_label: Any) -> bool:
    """Soft exit then +5 before hard; recovery-after-invalidation excluded by label."""
    return cf_label == MECH_SOFT


def assign_mechanism(*, giveback: bool, soft_premature: bool) -> Optional[str]:
    """Exactly one mechanism; giveback wins when +5 occurred before exit."""
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

    for t in rows:
        eid = t["episode_id"]
        fg = fixed_grid_by_eid.get(eid) or {}
        cf = cf_by_key.get((pair_id, eid)) or {}
        best = fg.get("best_net_pnl_bps_300s")
        realized = t.get("exit_net_pnl_bps")
        if is_denominator(best, realized):
            denom_eids.append(eid)

        giveback = is_giveback(
            t_plus5=fg.get("t_plus5"),
            exit_time=t.get("exit_time"),
            realized=realized,
        )
        soft_prem = is_soft_premature(cf.get("label"))
        mech = assign_mechanism(giveback=giveback, soft_premature=soft_prem)
        if mech is not None:
            repair_rows.append({
                "pair_id": pair_id,
                "episode_id": eid,
                "day": t.get("day"),
                "symbol": t.get("symbol"),
                "exit_reason": t.get("exit_reason"),
                "realized_net_pnl_bps": realized,
                "best_net_pnl_bps_300s": best,
                "in_denominator": eid in denom_eids or is_denominator(best, realized),
                "mechanism": mech,
                "cf_label": cf.get("label"),
            })

    mech_counts = Counter(r["mechanism"] for r in repair_rows)
    top_mech, top_n = (mech_counts.most_common(1)[0] if mech_counts else (None, 0))
    repairable_n = len(repair_rows)
    denom_n = len(denom_eids)
    days = {r["day"] for r in repair_rows}
    frac = (repairable_n / denom_n) if denom_n else 0.0
    top_frac = (top_n / repairable_n) if repairable_n else 0.0

    return {
        "pair_id": pair_id,
        "exit_candidate": pair_id.split("|", 1)[1],
        "unique_episode_n": len(set(eids)),
        "trade_rows": len(rows),
        "duplicate_episode_within_pair": dup,
        "oracle_plus5_realized_loss_n": denom_n,
        "denominator_episode_ids": sorted(denom_eids),
        "repairable_n": repairable_n,
        "repairable_episode_ids": sorted(r["episode_id"] for r in repair_rows),
        "repairable_fraction": frac,
        "repairable_days": len(days),
        "repairable_day_list": sorted(days),
        "failure_mechanism_counts": dict(mech_counts),
        "top_mechanism": top_mech,
        "top_mechanism_fraction": top_frac,
        "repairable_rows": repair_rows,
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
        "repairable_n_ge_20": pair_res["repairable_n"] >= GATE_MIN_REPAIRABLE_N,
        "repairable_days_ge_5": pair_res["repairable_days"] >= GATE_MIN_REPAIRABLE_DAYS,
        "repairable_fraction_ge_050": pair_res["repairable_fraction"] >= GATE_MIN_REPAIRABLE_FRACTION - 1e-15,
        "top_mechanism_fraction_ge_050": pair_res["top_mechanism_fraction"] >= GATE_MIN_TOP_MECH_FRACTION - 1e-15,
        "identity_integrity_pass": identity_ok and pair_res["duplicate_episode_within_pair"] == 0,
        "ab_determinism_pass": ab_ok,
    }
    return {
        "pair_id": pair_res["pair_id"],
        "pass": all(checks.values()),
        "checks": checks,
    }


def combined_reference(
    pair_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Mixed-pair reference only — never used for Gate."""
    all_rep_rows = []
    all_denom = []
    for pid in PAIRS:
        pr = pair_results[pid]
        all_rep_rows.extend(pr["repairable_rows"])
        all_denom.extend(pr["denominator_episode_ids"])
    unique_rep = sorted({r["episode_id"] for r in all_rep_rows})
    unique_den = sorted(set(all_denom))
    frac = (len(unique_rep) / len(unique_den)) if unique_den else 0.0
    return {
        "combined_pair_trade_repairable_n": len(all_rep_rows),
        "combined_unique_episode_repairable_n": len(unique_rep),
        "combined_unique_episode_denominator_n": len(unique_den),
        "combined_unique_episode_fraction": frac,
        "used_for_gate": False,
        "note": "reference_only; Gate is pair-specific",
    }


def check_identity(trades: list[dict[str, Any]]) -> dict[str, Any]:
    upd = [t for t in trades if t.get("candidate_id") == CANDIDATE_ID and t.get("integrity_status") == "PASS"]
    by_pair = {pid: [t for t in upd if t["pair_id"] == pid] for pid in PAIRS}
    unique_eps = sorted({t["episode_id"] for t in upd})
    dups = {
        pid: len(rows) - len({t["episode_id"] for t in rows})
        for pid, rows in by_pair.items()
    }
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
            "repairable_n": pr["repairable_n"],
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


def reference_expectation_delta(pair_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Document vs audit reference expectations; do not force-fit."""
    expected = {
        PAIRS[0]: {
            "repairable_n": 27,
            "oracle_plus5_realized_loss_n": 62,
            "repairable_fraction": 0.435,
            "top_mechanism": MECH_GIVEBACK,
            "note": "bridge failure_class rows (ENTRY_PATH_FAILURE excluded from repairable)",
        },
        PAIRS[1]: {
            "repairable_n": 33,
            "oracle_plus5_realized_loss_n": 46,
            "repairable_fraction": 0.717,
            "top_mechanism": MECH_SOFT,
            "note": "bridge failure_class rows (ENTRY_PATH_FAILURE excluded from repairable)",
        },
    }
    deltas = {}
    for pid, exp in expected.items():
        got = pair_results[pid]
        deltas[pid] = {
            "expected": exp,
            "actual": {
                "repairable_n": got["repairable_n"],
                "oracle_plus5_realized_loss_n": got["oracle_plus5_realized_loss_n"],
                "repairable_fraction": got["repairable_fraction"],
                "top_mechanism": got["top_mechanism"],
            },
            "cause": (
                "Reconciliation uses EXIT-path definitions (giveback / soft-premature) without "
                "Bridge V2 ENTRY_PATH_FAILURE_MINUS10_FIRST priority suppression. "
                "Denom matches expected; repairable_n differs because episodes previously "
                "labeled ENTRY_PATH_FAILURE can still meet EXIT giveback/soft-premature rules."
            ),
        }
    return deltas
