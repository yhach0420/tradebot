"""S1 FP/FN audit and dimension comparison. No threshold search."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_implementation_correction_rca import MICRO_CASES

DIMS = (
    "directional_efficiency",
    "n_same_direction_bodies",
    "close_to_close_same_sign_n",
    "mean_body_over_range",
    "net_directional_displacement_over_normal",
    "max_range_over_normal",
    "counter_over_displacement",
    "one_bar_range_share",
    "first_to_second_progression",
    "second_to_third_progression",
)


def _med(xs: list[Any]) -> float | None:
    vs = sorted(float(x) for x in xs if _finite(x))
    if not vs:
        return None
    n = len(vs)
    mid = n // 2
    if n % 2:
        return float(vs[mid])
    return 0.5 * (float(vs[mid - 1]) + float(vs[mid]))


def _seq(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("sequence") or {})


def classify_micro_cause(row: dict[str, Any]) -> str:
    seq = _seq(row)
    base = dict(row.get("baseline") or {})
    eff = seq.get("directional_efficiency")
    share = seq.get("one_bar_range_share")
    n_opp = int(seq.get("n_opposite_bodies") or 0)
    first = int(seq.get("first_direction") or 0)
    last = int(seq.get("last_direction") or 0)
    p23 = seq.get("second_to_third_progression")
    net = seq.get("session_open_to_0914_displacement")
    atr = base.get("atr20")
    abs_net = abs(float(net)) if _finite(net) else None
    vs_atr = (abs_net / float(atr)) if _finite(abs_net) and _finite(atr) and float(atr) > 0 else None
    if base.get("insufficient_prior_obs") or base.get("split_or_level_jump_flag"):
        return "BASELINE_ERROR"
    if _finite(vs_atr) and float(vs_atr) < 0.12 and _finite(seq.get("net_directional_displacement_over_normal")) and float(seq.get("net_directional_displacement_over_normal")) >= 0.80:
        return "BASELINE_ERROR"
    if n_opp >= 1 and first != 0 and last != 0 and first != last:
        return "TWO_SIDED_INTERNAL_PATH"
    if _finite(share) and float(share) >= 0.55:
        return "ONE_BAR_DOMINATED"
    if _finite(eff) and float(eff) < 0.45:
        return "PATH_NOT_DIRECTIONAL"
    if last != first or (_finite(p23) and _finite(net) and float(p23) * float(net) < 0):
        return "NO_CONTINUED_INTENT"
    return "OTHER"


def s1_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fps = [r for r in rows if r.get("s1_fp")]
    fns = [r for r in rows if r.get("s1_fn")]
    human_fp = dict(Counter(str(r.get("human_opening_state")) for r in fps))
    clause_counts = Counter()
    fp_detail = []
    for r in fps:
        cl = dict(r.get("true_clauses") or {})
        passing = list(cl.get("passing_clause_ids") or [])
        for c in passing:
            clause_counts[c] += 1
        fp_detail.append(
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "human_opening_state": r.get("human_opening_state"),
                "machine_opening_state": r.get("machine_opening_state"),
                "machine_death": r.get("machine_death"),
                "passed_because": passing,
                "all_true_clauses": cl.get("all_true_clauses"),
                "sequence": (r.get("sequence") or {}).get("sequence"),
                "efficiency": (r.get("sequence") or {}).get("directional_efficiency"),
                "one_bar_share": (r.get("sequence") or {}).get("one_bar_range_share"),
                "baseline_obs": (r.get("baseline") or {}).get("observation_n"),
                "current_max3_over_baseline": (r.get("baseline") or {}).get("current_max3_over_baseline"),
                "human_note": r.get("human_note"),
            }
        )

    def grp(pred) -> list[dict[str, Any]]:
        return [r for r in rows if pred(r)]

    groups = {
        "human_TRUE_machine_pass": grp(lambda r: r.get("human_opening_state") == "TRUE_OPENING_DRIVE" and r.get("machine_S1")),
        "human_TRUE_machine_fail": grp(lambda r: r.get("human_opening_state") == "TRUE_OPENING_DRIVE" and not r.get("machine_S1")),
        "human_FAILED_OPEN": grp(lambda r: r.get("human_opening_state") == "FAILED_OPEN_THEN_REAL_DRIVE"),
        "human_MICRO_machine_pass": grp(lambda r: r.get("human_opening_state") == "MICRO_OR_LEAK" and r.get("machine_S1")),
        "human_MICRO_all": grp(lambda r: r.get("human_opening_state") == "MICRO_OR_LEAK"),
        "human_TWO_SIDED_machine_pass": grp(lambda r: r.get("human_opening_state") == "TWO_SIDED_OPEN" and r.get("machine_S1")),
        "human_FLAT_machine_pass": grp(lambda r: r.get("human_opening_state") == "FLAT_OR_CRAWL" and r.get("machine_S1")),
        "human_LATE_machine_pass": grp(lambda r: r.get("human_opening_state") == "LATE_RANGE_RESOLUTION" and r.get("machine_S1")),
        "machine_TRUE_pass": grp(lambda r: r.get("machine_opening_state") == "TRUE_OPENING_DRIVE" and r.get("machine_S1")),
    }
    dim_table = {}
    for gname, grows in groups.items():
        dim_table[gname] = {"n": len(grows)}
        for d in DIMS:
            dim_table[gname][d] = _med([(_seq(r).get(d)) for r in grows])

    # Separation: MICRO-pass vs TRUE-pass on each dim. Diagnostic overlap, not a cutoff search.
    sep = {}
    a = groups["human_TRUE_machine_pass"]
    b = groups["human_MICRO_machine_pass"]
    for d in DIMS:
        ma, mb = _med([_seq(r).get(d) for r in a]), _med([_seq(r).get(d) for r in b])
        sep[d] = {
            "true_pass_median": ma,
            "micro_pass_median": mb,
            "same_direction_gap": None if not (_finite(ma) and _finite(mb)) else abs(float(ma) - float(mb)),
        }

    micro_rows = []
    for sym, date in MICRO_CASES:
        rec = next((r for r in rows if str(r.get("symbol")) == sym and str(r.get("date")) == date), None)
        if rec is None:
            micro_rows.append({"symbol": sym, "date": date, "missing": True})
            continue
        seq = _seq(rec)
        base = dict(rec.get("baseline") or {})
        cause = classify_micro_cause(rec)
        micro_rows.append(
            {
                "symbol": sym,
                "date": date,
                "rca_id": rec.get("rca_id"),
                "why_human_MICRO": rec.get("human_note"),
                "why_machine_TRUE": rec.get("true_clauses"),
                "machine_opening_state": rec.get("machine_opening_state"),
                "machine_death": rec.get("machine_death"),
                "reached_S2": rec.get("machine_S2"),
                "baseline": base,
                "baseline_valid": (not base.get("insufficient_prior_obs")) and (not base.get("split_or_level_jump_flag")),
                "bars": rec.get("bars"),
                "sequence": seq,
                "missing_from_machine": [
                    "continued directional intent (close-to-close / last-bar persistence)",
                    "path efficiency / net vs gross",
                    "one-bar domination",
                ],
                "cause": cause,
            }
        )

    encoding = {
        "meaningful_directional_displacement": "TRUE_DISP_MIN via net/normal — encoded",
        "directional_5m_bodies": "TRUE_BODY_FRAC_MIN mean + TRUE_N_SAME_MIN count — partial; ignores progression",
        "limited_counter_auction": "TRUE_COUNTER_FRAC — encoded",
        "movement_vs_normal_opening_5m": "TRUE_RANGE_MIN + NORMAL_OPENING_5M_RANGE — encoded, but baseline is first 5m only",
        "continued_directional_intent": "NOT encoded",
    }

    path_needed = "uncertain"
    if _finite(sep["directional_efficiency"].get("true_pass_median")) and _finite(sep["directional_efficiency"].get("micro_pass_median")):
        gap = float(sep["directional_efficiency"]["same_direction_gap"] or 0)
        path_needed = "yes" if gap >= 0.15 else ("uncertain" if gap >= 0.05 else "no")

    baseline_problem = False
    for m in micro_rows:
        if m.get("missing"):
            continue
        rec = next((r for r in rows if str(r.get("symbol")) == m.get("symbol") and str(r.get("date")) == m.get("date")), None)
        if rec is None:
            continue
        if classify_micro_cause(rec) == "BASELINE_ERROR":
            baseline_problem = True
        b = dict(m.get("baseline") or {})
        if b.get("insufficient_prior_obs") or b.get("split_or_level_jump_flag"):
            baseline_problem = True

    return {
        "s1_fp_n": len(fps),
        "s1_fn_n": len(fns),
        "human_state_composition_of_s1_fps": human_fp,
        "fp_clause_counts": dict(clause_counts),
        "fp_rows": fp_detail,
        "fn_rows": [
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "human_opening_state": r.get("human_opening_state"),
                "machine_opening_state": r.get("machine_opening_state"),
                "machine_death": r.get("machine_death"),
                "true_clauses": r.get("true_clauses"),
                "human_note": r.get("human_note"),
            }
            for r in fns
        ],
        "dimension_medians_by_group": dim_table,
        "dimension_separation_micro_vs_true": sep,
        "micro_case_rca": micro_rows,
        "continued_intent_encoded": "no",
        "path_efficiency_necessary": path_needed,
        "concept_to_rule_map": encoding,
        "implementation_encoding_gap": [
            "continued directional intent",
            "crawl vs drive beyond mean body fraction",
            "close-to-close progression",
        ],
        "NORMAL_OPENING_5M_RANGE_part_of_problem": baseline_problem,
        "note": "Dimension comparison uses human labels only. No 0.8 vs 0.9 search.",
    }
