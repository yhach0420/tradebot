"""Phase A–G: reload X23, metric audit, reclassification, mask/family agg."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.exits import EXIT_SPECS
from research.e1_x23_diversified_bundle.prospective import (
    _agg,
    _apply_mask_on_day,
    _build_day_path_cache,
    build_prospective_population,
    judge_prospective,
    simulate_day_exits,
)

from . import (
    ACTUAL_EXITS,
    BUNDLE_ID,
    EXPECTED_BUNDLE_SHA,
    EXPECTED_MASK_N,
    EXPECTED_PAIR_N,
    SOURCE_X23,
    TARGET_DAY,
    TARGET_ROLE,
)

NATIVE = Path(__file__).resolve().parents[3]
X23_DIR = NATIVE / "results" / "research" / "e1_x23_diversified_bundle"
OUT = NATIVE / "results" / "research" / "e1_x24_executable_bridge"


def load_x23() -> dict[str, Any]:
    report = json.loads((X23_DIR / "report.json").read_text(encoding="utf-8"))
    pre = json.loads((X23_DIR / "_precommit.json").read_text(encoding="utf-8"))
    assert report["run_id"] == SOURCE_X23
    assert pre["bundle_id"] == BUNDLE_ID
    assert pre["bundle_sha256"] == EXPECTED_BUNDLE_SHA
    assert report["precommit"]["bundle_sha256"] == EXPECTED_BUNDLE_SHA
    assert len(pre["pair_list"]) == EXPECTED_PAIR_N
    masks = {p["decision_mask_sha256"] for p in pre["pair_list"]}
    cands = {p["candidate_id"] for p in pre["pair_list"]}
    assert len(cands) == EXPECTED_MASK_N
    return {
        "report": report,
        "precommit": pre,
        "pair_list": pre["pair_list"],
        "unique_masks": len(cands),
        "mask_shas": masks,
        "exit_specs_frozen": {
            eid: {f: getattr(EXIT_SPECS[eid], f) for f in EXIT_SPECS[eid].__dataclass_fields__}
            for eid in ACTUAL_EXITS
        },
        "unchanged": {
            "candidate_specification": True,
            "threshold": True,
            "EXIT_specification": True,
            "decision_mask_SHA": True,
            "precommit_SHA": True,
        },
        "role_20260804": TARGET_ROLE,
    }


def load_candidates_for_masks():
    """Rebuild candidate specs (thresholds Discovery-frozen) without changing them."""
    from research.e1_x22_actual_exit_factory.registry import (
        load_population_checked,
        rebuild_candidates_and_masks,
    )
    rows = load_population_checked()
    cands, masks = rebuild_candidates_and_masks(rows)
    return rows, cands, masks


def recompute_prospective_metrics(
    pair_list: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recompute 20260804 pair metrics including all 5 judgment fields."""
    day_rows = build_prospective_population()
    # ensure date
    for r in day_rows:
        r["date"] = TARGET_DAY
    cache = _build_day_path_cache(day_rows)
    mats = simulate_day_exits(day_rows, cache)
    parent = {c["candidate_id"]: c for c in candidates if c.get("n_features", 1) == 1}
    all_c = {c["candidate_id"]: c for c in candidates}

    full = np.ones(len(day_rows), dtype=bool)
    baselines = {eid: _agg(mats[eid], full, day_rows) for eid in ACTUAL_EXITS}

    out = []
    for p in pair_list:
        cid = p["candidate_id"]
        eid = p["actual_exit_id"]
        cand = all_c[cid]
        mask = _apply_mask_on_day(day_rows, cand, parent)
        m = _agg(mats[eid], mask, day_rows)
        b = baselines[eid]
        x23_status = judge_prospective(m, b)

        metrics = {
            "avg_return_bps": m.get("avg_return_bps"),
            "profit_factor_reference": m.get("profit_factor_reference"),
            "worst_trade": m.get("worst_trade"),
            "max_drawdown": m.get("max_drawdown_reference_yen_100"),
            "hard_stop_rate": m.get("hard_stop_rate"),
            "trades": m.get("trades"),
            "avg_reference_pnl_yen_100": m.get("avg_reference_pnl_yen_100"),
            "exit_reason_counts": m.get("exit_reason_counts"),
        }
        baseline = {
            "baseline_avg_return_bps": b.get("avg_return_bps"),
            "baseline_profit_factor_reference": b.get("profit_factor_reference"),
            "baseline_worst_trade": b.get("worst_trade"),
            "baseline_max_drawdown": b.get("max_drawdown_reference_yen_100"),
            "baseline_hard_stop_rate": b.get("hard_stop_rate"),
            "baseline_trades": b.get("trades"),
        }

        def better(a, bb, higher=True):
            if a is None or bb is None:
                return False
            return (a > bb) if higher else (a < bb)

        improved = {
            "avg_return_bps": better(metrics["avg_return_bps"], baseline["baseline_avg_return_bps"], True),
            "profit_factor_reference": better(metrics["profit_factor_reference"], baseline["baseline_profit_factor_reference"], True),
            "worst_trade": better(metrics["worst_trade"], baseline["baseline_worst_trade"], True),
            "max_drawdown": better(metrics["max_drawdown"], baseline["baseline_max_drawdown"], True),
            "hard_stop_rate": better(metrics["hard_stop_rate"], baseline["baseline_hard_stop_rate"], False),
        }
        improved_names = [k for k, v in improved.items() if v]

        flags = {
            "absolute_return_positive": (metrics["avg_return_bps"] is not None and metrics["avg_return_bps"] > 0),
            "return_beats_same_exit_baseline": improved["avg_return_bps"],
            "pf_beats_baseline": improved["profit_factor_reference"],
            "worst_trade_improved": improved["worst_trade"],
            "max_drawdown_improved": improved["max_drawdown"],
            "hard_stop_rate_improved": improved["hard_stop_rate"],
            "support_sufficient": (metrics["trades"] or 0) >= 10,
        }
        risk_n = sum([
            flags["worst_trade_improved"],
            flags["max_drawdown_improved"],
            flags["hard_stop_rate_improved"],
        ])
        status = classify_status(flags, risk_n)

        # per-trade returns for bootstrap
        idx = np.where(mask & mats[eid].valid)[0]
        trade_rets = mats[eid].ret_bps[idx].astype(float) if idx.size else np.array([])
        base_idx = np.where(full & mats[eid].valid)[0]
        base_rets = mats[eid].ret_bps[base_idx].astype(float)

        out.append({
            **p,
            "x23_original_status": x23_status,
            "metrics": metrics,
            "baseline": baseline,
            "improved": improved,
            "improved_metric_count": len(improved_names),
            "improved_metric_names": improved_names,
            "flags": flags,
            "risk_improve_count": risk_n,
            "x24_status": status,
            "trade_rets_bps": trade_rets,
            "baseline_rets_bps": base_rets,
            "mask": mask,
            "entry_epochs": np.array([float(day_rows[i]["grid_epoch"]) for i in idx]) if idx.size else np.array([]),
            "exit_epochs": mats[eid].exit_t[idx].astype(float) if idx.size else np.array([]),
            "entry_ref_px": mats[eid].entry_px[idx].astype(float) if idx.size else np.array([]),
            "exit_ref_px": mats[eid].exit_px[idx].astype(float) if idx.size else np.array([]),
            "symbols": np.array([day_rows[i]["symbol"] for i in idx]) if idx.size else np.array([]),
            "cluster_ids": np.array([day_rows[i]["cluster_id"] for i in idx]) if idx.size else np.array([]),
            "day_rows_n": len(day_rows),
        })
    return out, day_rows, mats, baselines


def classify_status(flags: dict[str, bool], risk_n: int) -> str:
    if not flags["support_sufficient"]:
        return "SUPPORT_INSUFFICIENT"
    if flags["absolute_return_positive"] and flags["return_beats_same_exit_baseline"]:
        return "RETURN_EDGE_POSITIVE"
    avg_pos = flags["absolute_return_positive"]
    # RETURN_RELATIVE_ONLY: avg <= 0 AND beats baseline
    # We need avg_return from caller — encode via absolute_return_positive false
    if (not flags["absolute_return_positive"]) and flags["return_beats_same_exit_baseline"]:
        return "RETURN_RELATIVE_ONLY"
    if (not flags["return_beats_same_exit_baseline"]) and risk_n >= 2:
        return "RISK_SHAPING_ONLY"
    return "MIXED_EVIDENCE"


def recount_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    x23_sup = [r for r in rows if r["x23_original_status"] == "PROSPECTIVE_SUPPORTED"]
    abs_pos = [r for r in rows if r["flags"]["absolute_return_positive"] and r["flags"]["support_sufficient"]]
    beat = [r for r in rows if r["flags"]["return_beats_same_exit_baseline"] and r["flags"]["support_sufficient"]]
    both = [r for r in rows if r["flags"]["absolute_return_positive"] and r["flags"]["return_beats_same_exit_baseline"] and r["flags"]["support_sufficient"]]
    sup_neg = [r for r in x23_sup if (r["metrics"]["avg_return_bps"] is not None and r["metrics"]["avg_return_bps"] <= 0)]
    sup_worse = [r for r in x23_sup if not r["flags"]["return_beats_same_exit_baseline"]]
    return {
        "x23_original_supported": len(x23_sup),
        "absolute_return_positive": len(abs_pos),
        "return_beats_same_exit_baseline": len(beat),
        "absolute_positive_AND_baseline_better": len(both),
        "original_supported_but_negative_average_return": len(sup_neg),
        "original_supported_but_worse_than_baseline": len(sup_worse),
        "x23_status_counts": dict(Counter(r["x23_original_status"] for r in rows)),
        "x24_status_counts": dict(Counter(r["x24_status"] for r in rows)),
        "reference_audit_expected": {
            "x23_original_supported": 160,
            "absolute_return_positive": 49,
            "return_beats_same_exit_baseline": 69,
            "absolute_positive_AND_baseline_better": 49,
            "original_supported_but_negative_average_return": 111,
            "original_supported_but_worse_than_baseline": 91,
        },
    }


def entry_mask_aggregation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = defaultdict(list)
    for r in rows:
        by[r["candidate_id"]].append(r)
    out = []
    for cid, plist in by.items():
        counts = Counter(p["x24_status"] for p in plist)
        n_edge = counts.get("RETURN_EDGE_POSITIVE", 0)
        if n_edge >= 2:
            est = "ENTRY_RETURN_MULTI_EXIT"
        elif n_edge == 1:
            est = "ENTRY_RETURN_SINGLE_EXIT"
        elif counts.get("RISK_SHAPING_ONLY", 0) > 0 and n_edge == 0:
            est = "ENTRY_RISK_ONLY"
        else:
            est = "ENTRY_UNRESOLVED"
        out.append({
            "candidate_id": cid,
            "evaluated_exit_count": len(plist),
            "RETURN_EDGE_POSITIVE": n_edge,
            "RETURN_RELATIVE_ONLY": counts.get("RETURN_RELATIVE_ONLY", 0),
            "RISK_SHAPING_ONLY": counts.get("RISK_SHAPING_ONLY", 0),
            "MIXED_EVIDENCE": counts.get("MIXED_EVIDENCE", 0),
            "SUPPORT_INSUFFICIENT": counts.get("SUPPORT_INSUFFICIENT", 0),
            "entry_mask_status": est,
            "logic_depth": plist[0].get("logic_depth"),
            "signature": plist[0].get("component_family_signature"),
        })
    return out


def family_reaggregation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def bucket(key_fn):
        d = defaultdict(Counter)
        for r in rows:
            d[key_fn(r)][r["x24_status"]] += 1
        return {k: dict(v) for k, v in d.items()}

    edge = [r for r in rows if r["x24_status"] == "RETURN_EDGE_POSITIVE"]
    risk = [r for r in rows if r["x24_status"] == "RISK_SHAPING_ONLY"]
    answers = {
        "single_RETURN_EDGE_POSITIVE": sum(1 for r in edge if r.get("logic_depth") == "SINGLE"),
        "two_feature_RETURN_EDGE_POSITIVE": sum(1 for r in edge if r.get("logic_depth") == "TWO_FEATURE"),
        "families_with_return_edge": sorted({r.get("component_family_signature") for r in edge}),
        "families_risk_only": sorted({r.get("component_family_signature") for r in risk}),
        "exits_return_edge_counts": dict(Counter(r["actual_exit_id"] for r in edge)),
        "HIGH_MID_retention_return_edge": sum(
            1 for r in edge if r.get("retention_band") in ("HIGH_RETENTION", "MID_RETENTION")
        ),
        "TAIL_select_excluding_insufficient": {
            "RETURN_EDGE_POSITIVE": sum(1 for r in rows if r.get("retention_band") == "TAIL_SELECT" and r["x24_status"] == "RETURN_EDGE_POSITIVE"),
            "other_sufficient": sum(
                1 for r in rows
                if r.get("retention_band") == "TAIL_SELECT" and r["x24_status"] not in ("SUPPORT_INSUFFICIENT",)
            ),
            "insufficient": sum(1 for r in rows if r.get("retention_band") == "TAIL_SELECT" and r["x24_status"] == "SUPPORT_INSUFFICIENT"),
        },
    }
    return {
        "by_logic_depth": bucket(lambda r: r.get("logic_depth")),
        "by_signature": bucket(lambda r: r.get("component_family_signature")),
        "by_exit": bucket(lambda r: r.get("actual_exit_id")),
        "by_retention": bucket(lambda r: r.get("retention_band")),
        "by_period_tag": bucket(lambda r: r.get("period_bundle_tag")),
        "required_answers": answers,
    }
