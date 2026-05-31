#!/usr/bin/env python3
"""
Phase218: Guard attribution audit — decompose TV / VWAP / Board overlap on stop_hit.

Review only; fixed Phase217 thresholds; no hard reject, no YAML changes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase218_guard_attribution_audit.json"

GUARD_LABELS = ("TV", "VWAP", "Board")


def _load_phase217() -> Any:
    path = REPO / "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py"
    name = "phase217_loader_p218"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _guard_flags(p217: Any, row: dict[str, Any]) -> dict[str, bool]:
    imb = p217._float(row.get("entry_order_book_imbalance"))
    board = imb is not None and imb < p217.IMBALANCE_30PCT
    return {
        "TV": p217._low_liq_reject(row),
        "VWAP": p217._vwap_reject(row),
        "Board": board,
    }


def _venn_region(flags: dict[str, bool]) -> str:
    active = [k for k in GUARD_LABELS if flags[k]]
    if not active:
        return "none"
    if len(active) == 1:
        return f"only_{active[0]}"
    if len(active) == 2:
        return "+".join(active)
    return "TV+VWAP+Board"


def _jaccard(a: set[str], b: set[str]) -> float | None:
    if not a and not b:
        return None
    u = a | b
    if not u:
        return None
    return round(len(a & b) / len(u), 4)


def _overlap_rate(ids_a: set[str], ids_b: set[str], base: set[str]) -> dict[str, Any]:
    inter = ids_a & ids_b
    return {
        "intersection_count": len(inter),
        "union_count": len(ids_a | ids_b),
        "jaccard": _jaccard(ids_a, ids_b),
        "pct_of_stops_intersection": round(100.0 * len(inter) / max(1, len(base)), 2),
        "pct_a_of_intersection": round(100.0 * len(inter) / max(1, len(ids_a)), 2) if ids_a else None,
        "pct_b_of_intersection": round(100.0 * len(inter) / max(1, len(ids_b)), 2) if ids_b else None,
    }


def _stop_reduction(stops: list[dict[str, Any]], flag_fn) -> dict[str, Any]:
    n = len(stops)
    hit = [s for s in stops if flag_fn(s)]
    rem = len(hit)
    return {
        "stop_hit_removed": rem,
        "stop_hit_remaining": n - rem,
        "stop_hit_remove_pct": round(100.0 * rem / max(1, n), 2),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_phase217()
    mod = p217._load_phase213c_module()
    print("loading trades (Phase217 pipeline)...", flush=True)
    rows = p217._build_all(mod)
    stops = [r for r in rows if r.get("stop_hit")]
    total_stops = len(stops)

    enriched: list[dict[str, Any]] = []
    for r in stops:
        flags = _guard_flags(p217, r)
        cls = p217._classify_stop(r)
        tags = set(cls.get("tags") or [])
        key = f"{r.get('symbol')}|{r.get('entry_time')}"
        enriched.append(
            {
                "key": key,
                "symbol": r.get("symbol"),
                "entry_time": r.get("entry_time"),
                "session_id": r.get("session_id"),
                "flags": flags,
                "venn_region": _venn_region(flags),
                "primary_cause": cls.get("primary"),
                "tags": sorted(tags),
                "pnl_pct": r.get("pnl_pct"),
                "mfe_pct": r.get("mfe_pct"),
            }
        )

    # Per-guard ID sets
    ids: dict[str, set[str]] = {
        g: {e["key"] for e in enriched if e["flags"][g]} for g in GUARD_LABELS
    }
    ids_none = {e["key"] for e in enriched if _venn_region(e["flags"]) == "none"}

    # Venn region counts
    region_counts: dict[str, int] = {}
    for e in enriched:
        region_counts[e["venn_region"]] = region_counts.get(e["venn_region"], 0) + 1

    # Pairwise / triple overlap
    stop_keys = {e["key"] for e in enriched}
    pairwise: dict[str, Any] = {}
    for a, b in combinations(GUARD_LABELS, 2):
        pairwise[f"{a}_and_{b}"] = _overlap_rate(ids[a], ids[b], stop_keys)

    triple_inter = ids["TV"] & ids["VWAP"] & ids["Board"]

    # Primary-cause tag overlap (Phase217 classification labels)
    cause_ids = {
        "entry_bad_liquidity": {e["key"] for e in enriched if "entry_bad_liquidity" in e["tags"]},
        "entry_bad_vwap": {e["key"] for e in enriched if "entry_bad_vwap" in e["tags"]},
        "entry_bad_board": {e["key"] for e in enriched if "entry_bad_board" in e["tags"]},
    }
    cause_pairwise: dict[str, Any] = {}
    for a, b in combinations(cause_ids.keys(), 2):
        cause_pairwise[f"{a}_x_{b}"] = _overlap_rate(cause_ids[a], cause_ids[b], stop_keys)

    # Exclusive vs shared summary
    exclusive = {
        "only_TV": region_counts.get("only_TV", 0),
        "only_VWAP": region_counts.get("only_VWAP", 0),
        "only_Board": region_counts.get("only_Board", 0),
    }
    shared = {
        "two_way_only": sum(
            region_counts.get(k, 0)
            for k in ("TV+VWAP", "TV+Board", "VWAP+Board")
        ),
        "all_three": region_counts.get("TV+VWAP+Board", 0),
        "none_flagged": region_counts.get("none", 0),
    }

    def _union_flag(*names: str):
        def fn(e: dict[str, Any]) -> bool:
            return any(e["flags"][n] for n in names)

        return fn

    stop_reduction = {
        "single_guard": {
            "TV_only": _stop_reduction(enriched, lambda e: e["flags"]["TV"]),
            "VWAP_only": _stop_reduction(enriched, lambda e: e["flags"]["VWAP"]),
            "Board_only": _stop_reduction(enriched, lambda e: e["flags"]["Board"]),
        },
        "pair_guard_union": {
            "TV_plus_VWAP": _stop_reduction(enriched, _union_flag("TV", "VWAP")),
            "VWAP_plus_Board": _stop_reduction(enriched, _union_flag("VWAP", "Board")),
            "TV_plus_Board": _stop_reduction(enriched, _union_flag("TV", "Board")),
        },
        "triple_guard_union": {
            "TV_plus_VWAP_plus_Board": _stop_reduction(
                enriched, _union_flag("TV", "VWAP", "Board")
            ),
        },
        "incremental_marginal": {
            "TV_exclusive_stops": len(ids["TV"] - ids["VWAP"] - ids["Board"]),
            "VWAP_exclusive_stops": len(ids["VWAP"] - ids["TV"] - ids["Board"]),
            "Board_exclusive_stops": len(ids["Board"] - ids["TV"] - ids["VWAP"]),
            "marginal_TV_after_VWAP_Board": len(ids["TV"] - (ids["VWAP"] | ids["Board"])),
            "marginal_VWAP_after_TV_Board": len(ids["VWAP"] - (ids["TV"] | ids["Board"])),
            "marginal_Board_after_TV_VWAP": len(ids["Board"] - (ids["TV"] | ids["VWAP"])),
        },
    }

    # Interpretation: same vs distinct stops
    union_all = ids["TV"] | ids["VWAP"] | ids["Board"]
    overlap_density = round(
        100.0 * (len(ids["TV"] & ids["VWAP"]) + len(ids["VWAP"] & ids["Board"]) + len(ids["TV"] & ids["Board"]))
        / max(1, 3 * total_stops),
        2,
    )

    report = {
        "phase": 218,
        "mode": "guard_attribution_audit",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "production_yaml_changes_forbidden": True,
            "fixed_thresholds_from_phase217": True,
        },
        "thresholds": {
            "TV": f"low_liq: tv<{p217.TV_MIN} OR turnover<{p217.TURNOVER_MIN} OR shadow_rejected",
            "VWAP": f"entry_vwap_dev_pct>={p217.VWAP_DEV_REJECT} OR vwap_shadow_reject_candidate",
            "Board": f"entry_order_book_imbalance<{p217.IMBALANCE_30PCT} (30pct tier)",
        },
        "population": {
            "total_trades": len(rows),
            "stop_hit_count": total_stops,
            "phase217_parity_expected_stops": 106,
        },
        "guard_flag_counts": {g: len(ids[g]) for g in GUARD_LABELS},
        "venn_regions": {
            **region_counts,
            "region_share_pct": {
                k: round(100.0 * v / max(1, total_stops), 2) for k, v in region_counts.items()
            },
        },
        "exclusive_vs_shared": {**exclusive, **shared},
        "pairwise_overlap": pairwise,
        "triple_overlap": {
            "TV_and_VWAP_and_Board_count": len(triple_inter),
            "pct_of_stops": round(100.0 * len(triple_inter) / max(1, total_stops), 2),
        },
        "primary_cause_tag_overlap": {
            "counts": {k: len(v) for k, v in cause_ids.items()},
            "pairwise": cause_pairwise,
        },
        "stop_reduction_contribution": stop_reduction,
        "interpretation": {
            "stops_flagged_by_any_guard": len(union_all),
            "stops_unflagged": len(ids_none),
            "pct_stops_with_multiple_guards": round(
                100.0
                * sum(1 for e in enriched if sum(e["flags"].values()) >= 2)
                / max(1, total_stops),
                2,
            ),
            "pct_stops_with_all_three_guards": round(
                100.0 * len(triple_inter) / max(1, total_stops), 2
            ),
            "overlap_density_index_pct": overlap_density,
            "verdict": (
                "mostly_distinct"
                if exclusive["only_TV"] + exclusive["only_VWAP"] + exclusive["only_Board"]
                > shared["two_way_only"] + shared["all_three"]
                else "substantially_shared"
            ),
            "note": (
                "High pairwise overlap => same stops explained by multiple guards; "
                "high exclusive counts => guards catch different stop subsets."
            ),
        },
        "notes": [
            "stop_reduction single_guard counts stops matching that flag (not other guards required).",
            "pair/triple union = OR logic (reject if any guard in set fires).",
            "Primary cause tags from Phase217; guard flags use same underlying thresholds.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} stops={total_stops}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
