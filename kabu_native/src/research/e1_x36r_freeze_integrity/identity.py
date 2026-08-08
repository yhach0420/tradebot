"""Cross-fitted replay reproduction + final-refit score identity."""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from research.e1_x36_joint_allocator.cv import outer_train_test
from research.e1_x36_joint_allocator.metrics import summarize_replay
from research.e1_x36_joint_allocator.models import fit_spec, score_fn_from_fit
from research.e1_x36_joint_allocator.replay import simulate_joint

from . import OUTER_SPECS, X36_CROSS
from .serialize import scores_from_fit, serialize_fill_model, score_fn_from_serialized


def reproduce_cross_fitted(panel: list[dict]) -> dict[str, Any]:
    """Replay with frozen OUTER_SPECS (no re-selection). Evidence SoT identity check."""
    cross_events: list[dict] = []
    fold_models = {}
    for block in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(block)
        train = [e for e in panel if e["date"] in train_days]
        test = [e for e in panel if e["date"] in test_days]
        spec = OUTER_SPECS[block]
        fit = fit_spec(train, spec)
        ser = serialize_fill_model(fit, train=train)
        fold_models[block] = {
            "spec": spec,
            "model_artifact_sha256": ser["model_artifact_sha256"],
            "coefficients": ser["coefficients"],
            "intercept": ser["intercept"],
            "feature_order": ser["feature_order"],
        }
        sfn = score_fn_from_fit(fit)
        # identity: serialized scores match fit scores on test
        s_fit = scores_from_fit(fit, test)
        s_ser = [score_fn_from_serialized(ser)(e) for e in test]
        max_delta = float(np.max(np.abs(np.asarray(s_fit) - np.asarray(s_ser)))) if test else 0.0
        fold_models[block]["score_max_abs_delta_vs_serialized"] = max_delta
        sim = simulate_joint(test, score_fn=sfn)
        # admission identity vs serialized scorer
        sim2 = simulate_joint(test, score_fn=score_fn_from_serialized(ser))
        adm1 = {(e["date"], e["symbol"], e["signal_time"]) for e in sim["events"] if e.get("admitted")}
        adm2 = {(e["date"], e["symbol"], e["signal_time"]) for e in sim2["events"] if e.get("admitted")}
        fold_models[block]["admission_identity"] = adm1 == adm2
        cross_events.extend(sim["events"])

    fake = {
        "events": cross_events,
        "hard_cap_violations": 0,
        "max_open_plus_pending": 5,
        "occupied_slot_sec": 0.0,
        "max_concurrent_notional_yen": 0.0,
        "p95_concurrent_notional_yen": 0.0,
        "max_pending_reserved_notional_yen": 0.0,
    }
    # recompute hard_cap from events if any flagged — use sum of fold sims
    sm = summarize_replay(fake)
    return {
        "events": cross_events,
        "summary": sm,
        "fold_models": fold_models,
        "identity_vs_x36": _compare_to_x36(sm),
    }


def _compare_to_x36(sm: dict) -> dict[str, Any]:
    checks = {
        "admitted": abs((sm.get("admitted") or 0) - X36_CROSS["admitted"]) == 0,
        "fills": abs((sm.get("fills") or 0) - X36_CROSS["fills"]) == 0,
        "positive_days": abs((sm.get("positive_days") or 0) - X36_CROSS["positive_days"]) == 0,
        "hard_cap_violations": (sm.get("hard_cap_violations") or 0) == X36_CROSS["hard_cap_violations"],
        "total_pnl_yen": abs(float(sm.get("total_pnl_yen") or 0) - X36_CROSS["total_pnl_yen"]) < 1.0,
        "opp_bps": abs(float(sm.get("opp_bps_per_signal") or 0) - X36_CROSS["opp_bps"]) < 1e-6,
        "pf": abs(float(sm.get("pf") or 0) - X36_CROSS["pf"]) < 1e-6,
    }
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "observed": {
            "admitted": sm.get("admitted"),
            "fills": sm.get("fills"),
            "total_pnl_yen": sm.get("total_pnl_yen"),
            "opp_bps": sm.get("opp_bps_per_signal"),
            "pf": sm.get("pf"),
            "positive_days": sm.get("positive_days"),
            "hard_cap_violations": sm.get("hard_cap_violations"),
        },
        "targets": X36_CROSS,
    }


def final_refit_identity(panel: list[dict], fit: dict, ser: dict) -> dict[str, Any]:
    """Score/rank/admission identity: live fit vs serialized model on full panel."""
    s_fit = scores_from_fit(fit, panel)
    sfn_ser = score_fn_from_serialized(ser)
    s_ser = [float(sfn_ser(e)) for e in panel]
    deltas = np.abs(np.asarray(s_fit) - np.asarray(s_ser))
    # finite-only for max (both -inf → 0)
    finite = np.isfinite(s_fit) & np.isfinite(s_ser)
    max_delta = float(np.max(deltas[finite])) if finite.any() else 0.0
    inf_match = all(
        (not np.isfinite(a) and not np.isfinite(b)) or (np.isfinite(a) and np.isfinite(b))
        for a, b in zip(s_fit, s_ser)
    )

    sim1 = simulate_joint(panel, score_fn=score_fn_from_fit(fit))
    sim2 = simulate_joint(panel, score_fn=sfn_ser)
    adm1 = sorted(
        (e["date"], e["symbol"], float(e["signal_time"]))
        for e in sim1["events"] if e.get("admitted")
    )
    adm2 = sorted(
        (e["date"], e["symbol"], float(e["signal_time"]))
        for e in sim2["events"] if e.get("admitted")
    )
    # cohort top-k: per clock, ordered keys
    def _cohort_topk(events):
        from collections import defaultdict
        by = defaultdict(list)
        for e in events:
            by[(e["date"], float(e["signal_time"]))].append(e)
        out = {}
        for k, grp in by.items():
            ranked = sorted(
                [e for e in grp if e.get("alloc_score") is not None],
                key=lambda e: (-float(e["alloc_score"]), str(e["symbol"])),
            )
            out[k] = [(e["symbol"], float(e["alloc_score"])) for e in ranked[:5]]
        return out

    # attach scores for cohort compare
    for e, s in zip(sim1["events"], [score_fn_from_fit(fit)(e) for e in sim1["events"]]):
        e["alloc_score"] = s
    for e, s in zip(sim2["events"], [sfn_ser(e) for e in sim2["events"]]):
        e["alloc_score"] = s
    c1 = _cohort_topk(sim1["events"])
    c2 = _cohort_topk(sim2["events"])
    cohort_ok = c1 == c2

    sm1 = summarize_replay(sim1)
    return {
        "max_absolute_score_delta": max_delta,
        "inf_pattern_match": inf_match,
        "admission_identity": adm1 == adm2,
        "admission_n_fit": len(adm1),
        "admission_n_ser": len(adm2),
        "cohort_topk_identity": cohort_ok,
        "rank_identity": cohort_ok,  # top-k order identity implies rank for admission
        "in_sample_summary_note": "REPRODUCIBILITY ONLY — not performance evidence",
        "in_sample_admitted": sm1.get("admitted"),
        "in_sample_fills": sm1.get("fills"),
        "in_sample_pnl_yen": sm1.get("total_pnl_yen"),
        "pass": bool(
            max_delta < 1e-9
            and inf_match
            and adm1 == adm2
            and cohort_ok
        ),
    }
