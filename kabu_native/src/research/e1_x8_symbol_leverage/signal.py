"""UPDATE first-touch signal vs matched parent (Bridge V2 contract)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.e1_x7_pfq.bridge_v2.stats import FT_KEYS, bootstrap_difference, compare_sets, entry_path_supported, rate_plus_first

from . import BOOTSTRAP_REPS, BOOTSTRAP_SEED


def _row_from_episode(ep: dict[str, Any]) -> dict[str, Any]:
    ft = {
        "plus5_vs_minus10": ep.get("ft_plus5_vs_minus10"),
        "plus5_vs_minus15": ep.get("ft_plus5_vs_minus15"),
        "plus10_vs_minus10": ep.get("ft_plus10_vs_minus10"),
        "plus10_vs_minus15": ep.get("ft_plus10_vs_minus15"),
    }
    return {
        "episode_id": ep["episode_id"],
        "day": ep["day"],
        "symbol": ep["symbol"],
        "session": ep.get("session"),
        "fixed_grid_ft": ft,
        "fixed_grid": {
            "evaluable": bool(ep.get("evaluable", True)),
            "best_net_pnl_bps_300s": ep.get("best_net_pnl_bps_300s"),
        },
        "update_eligible_parent": ep.get("update_eligible_parent", True),
    }


def evaluate_update_signal(
    cand_eps: list[dict[str, Any]],
    parent_eps: list[dict[str, Any]],
    *,
    bridge_reference: Optional[dict[str, Any]] = None,
    ft_keys: tuple[str, ...] = FT_KEYS,
) -> dict[str, Any]:
    cand = [_row_from_episode(e) for e in cand_eps]
    parent = [_row_from_episode(e) for e in parent_eps]
    base = compare_sets(cand, parent, mode="fixed_grid")
    for k in ft_keys:
        mkey = f"{k}_rate"
        boot = bootstrap_difference(
            cand, parent, mode="fixed_grid", metric_key=mkey,
            rate_fn=lambda rows, kk=k: rate_plus_first(rows, kk, "fixed_grid"),
            reps=BOOTSTRAP_REPS,
            seed=BOOTSTRAP_SEED,
        )
        base["metrics"][mkey]["bootstrap"] = boot
    # support uses all FT_KEYS present with bootstrap
    supported, reasons = entry_path_supported({"fixed_grid": base})
    out = {
        "n_candidate": len(cand),
        "n_parent": len(parent),
        "fixed_grid": base,
        "supported": supported,
        "support_reasons": reasons,
    }
    if bridge_reference is not None:
        # reproduce plus5_vs_minus10 difference / ci / pos days
        ref_m = ((bridge_reference.get("fixed_grid") or {}).get("metrics") or {}).get("plus5_vs_minus10_rate") or {}
        got_m = (base.get("metrics") or {}).get("plus5_vs_minus10_rate") or {}
        ref_ci = (ref_m.get("bootstrap") or {}).get("difference_ci95") or [None, None]
        got_ci = (got_m.get("bootstrap") or {}).get("difference_ci95") or [None, None]
        match = (
            got_m.get("difference") is not None
            and ref_m.get("difference") is not None
            and abs(float(got_m["difference"]) - float(ref_m["difference"])) < 1e-9
            and got_m.get("positive_difference_days") == ref_m.get("positive_difference_days")
            and ref_ci[0] is not None and got_ci[0] is not None
            and abs(float(got_ci[0]) - float(ref_ci[0])) < 1e-9
            and abs(float(got_ci[1]) - float(ref_ci[1])) < 1e-9
        )
        out["bridge_reproduction"] = {
            "match": match,
            "ref_difference": ref_m.get("difference"),
            "got_difference": got_m.get("difference"),
            "ref_pos_days": ref_m.get("positive_difference_days"),
            "got_pos_days": got_m.get("positive_difference_days"),
            "ref_ci95": ref_ci,
            "got_ci95": got_ci,
        }
    return out


def summarize_ft(signal: dict[str, Any], key: str = "plus5_vs_minus10") -> dict[str, Any]:
    m = ((signal.get("fixed_grid") or {}).get("metrics") or {}).get(f"{key}_rate") or {}
    boot = m.get("bootstrap") or {}
    return {
        "difference": m.get("difference"),
        "candidate_rate": m.get("candidate_rate"),
        "matched_parent_rate": m.get("matched_parent_rate"),
        "positive_difference_days": m.get("positive_difference_days"),
        "negative_difference_days": m.get("negative_difference_days"),
        "difference_ci95": boot.get("difference_ci95"),
        "difference_median": boot.get("difference_median"),
        "positive_fraction": boot.get("positive_fraction"),
        "support_metric": (
            boot.get("difference_ci95")
            and boot["difference_ci95"][0] is not None
            and boot["difference_ci95"][0] > 0
            and int(m.get("positive_difference_days") or 0) >= 7
        ),
    }
