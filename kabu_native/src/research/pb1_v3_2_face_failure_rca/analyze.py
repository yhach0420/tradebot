"""Semantic RCA summaries. No PnL. No threshold grid. No V3.3."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_2_face_failure_rca import (
    CASE_BIND,
    CASE_INCOMPLETE,
    CASE_LABELS,
    CASE_OR_WEAK,
    CASE_REBUILD,
    EXEMPLAR_EARLY_REVERSAL,
    KAPPA_UNSTABLE,
    NEXT_BIND,
    NEXT_LABEL,
    NEXT_STOP_OR,
    NEXT_V4,
    PARENT_V32_SHA,
)
from research.pb1_v3_2_face_failure_rca.kappa import layer_agreement
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256


def _median(xs: list[Any]) -> float | None:
    vals = [float(x) for x in xs if _finite(x)]
    if not vals:
        return None
    vals.sort()
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def _share(k: int, n: int) -> float | None:
    return float(k / n) if n else None


def _get(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def agreement_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    return {
        "pattern": layer_agreement(reviewed, "fp_pattern", "sp_pattern"),
        "opening": layer_agreement(reviewed, "fp_opening", "sp_opening"),
        "location": layer_agreement(reviewed, "fp_location", "sp_location"),
        "retest": layer_agreement(reviewed, "fp_retest", "sp_retest"),
        "reacceleration": layer_agreement(reviewed, "fp_trigger", "sp_trigger"),
    }


def early_reversal_contrast(rows: list[dict[str, Any]]) -> dict[str, Any]:
    early = [r for r in rows if str(r.get("machine_auction") or r.get("v32_auction") or "") == "EARLY_REVERSAL_THEN_DOMINANT_DRIVE"]
    pos = [r for r in early if (str(r.get("symbol")), str(r.get("date")), str(r.get("direction"))) == EXEMPLAR_EARLY_REVERSAL]
    fp = [r for r in early if r not in pos]
    def pack(rs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(rs),
            "median_net_disp_over_normal_5m": _median([_get(r, "opening_5m", "net_displacement_over_normal_5m") for r in rs]),
            "median_max_range_over_normal_5m": _median([_get(r, "opening_5m", "max_range_over_normal_5m") for r in rs]),
            "median_n_same_dir_5m": _median([_get(r, "opening_5m", "n_same_dir_5m") for r in rs]),
            "median_largest_counter_over_normal_5m": _median([_get(r, "opening_5m", "largest_counter_over_normal_5m") for r in rs]),
            "sequence_counts": dict(Counter(str(_get(r, "opening_5m", "sequence")) for r in rs)),
            "family_counts": dict(Counter(str(r.get("opening_descriptor_family")) for r in rs)),
            "median_or_close_loc": _median([r.get("or_close_loc_machine") or r.get("or_close_loc") for r in rs]),
            "median_first5_tv_pctl": _median(
                [((r.get("five_m_tv") or [{}])[0] or {}).get("pctl") for r in rs]
            ),
            "sp_opening_state_counts": dict(Counter(str(r.get("sp_opening_state")) for r in rs if r.get("sp_reviewed"))),
        }
    return {
        "early_reversal_machine_n": len(early),
        "exemplar_n": len(pos),
        "false_positive_n": len(fp),
        "exemplar": pack(pos),
        "other_early_reversal": pack(fp),
        "note": "3382 is a positive semantic exemplar, not a numeric template. No one-stock rule.",
        "visual_separation": (
            "Failed-open 5m counter-body is large and real (largest_counter/normal ~1.46 vs ~0.30 on FPs); "
            "bodies are directional, not doji crawls; the dump continues after 09:14 into a recognizable OR_LOW hold. "
            "FP EARLY_REVERSAL tags are OR-half closes after a tiny first-5m dip. 09:14 net/normal is not the separator."
        ),
    }


def family_vs_human(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    by_state: dict[str, Counter] = defaultdict(Counter)
    by_family: dict[str, Counter] = defaultdict(Counter)
    for r in reviewed:
        by_state[str(r.get("sp_opening_state"))][str(r.get("opening_descriptor_family"))] += 1
        by_family[str(r.get("opening_descriptor_family"))][str(r.get("sp_opening_state"))] += 1
    or_half_vs_drive = {
        "true_or_failed_drive_n": sum(
            1 for r in reviewed if r.get("sp_opening_state") in ("TRUE_OPENING_DRIVE", "FAILED_OPEN_THEN_REAL_DRIVE")
        ),
        "micro_or_flat_n": sum(1 for r in reviewed if r.get("sp_opening_state") in ("MICRO_OR_LEAK", "FLAT_OR_CRAWL")),
        "machine_allowed_auction_n": sum(
            1
            for r in reviewed
            if str(r.get("machine_auction") or "") in ("INITIAL_DIRECTION_CONTINUATION", "EARLY_REVERSAL_THEN_DOMINANT_DRIVE")
        ),
    }
    return {
        "family_by_human_opening_state": {k: dict(v) for k, v in by_state.items()},
        "human_state_by_family": {k: dict(v) for k, v in by_family.items()},
        "or_half_vs_5m_drive": or_half_vs_drive,
        "five_m_better_than_or_half": True,
        "reason": "OR-half close is a location statistic; human drive is a 5m sequence of directional bodies and displacement.",
    }


def location_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    valid = [r for r in reviewed if r.get("sp_location") == "CLEAR_DEFENDED_LOCATION"]
    kinds = Counter(str(r.get("sp_location_kind")) for r in valid)
    or_only = [r for r in reviewed if r.get("sp_location_kind") == "OR_ONLY_DEFENSE"]
    return {
        "human_valid_location_n": len(valid),
        "kind_counts_among_valid": dict(kinds),
        "or_only_n_all": len(or_only),
        "or_only_valid_n": int(kinds.get("OR_ONLY_DEFENSE") or 0),
        "or_boundary_alone_meaningful": False,
        "reason": "OR high/low is a session reference. Human-clear location required a visible hold plus confluence or prior structure, not the OR print itself.",
    }


def reaccel_families(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    valid = [r for r in reviewed if r.get("sp_trigger") == "VALID_REACCELERATION"]
    weak = [r for r in reviewed if r.get("sp_trigger") == "WEAK_REACCELERATION"]
    def pack(rs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(rs),
            "median_range_over_n1m": _median([_get(r, "reaccel", "trigger_range_over_NORMAL_1M_RANGE") for r in rs]),
            "median_body_over_n1m": _median([_get(r, "reaccel", "trigger_body_over_NORMAL_1M_RANGE") for r in rs]),
            "median_body_over_range": _median([_get(r, "reaccel", "trigger_body_over_range") for r in rs]),
            "median_range_over_prior3": _median([_get(r, "reaccel", "current_range_over_median_prior3_range") for r in rs]),
            "median_body_over_prior3": _median([_get(r, "reaccel", "current_body_over_median_prior3_body") for r in rs]),
            "median_3bar_net_over_n1m": _median([_get(r, "reaccel", "three_bar_dir_net_over_NORMAL_1M_RANGE") for r in rs]),
            "median_dir_closes_last3": _median([_get(r, "reaccel", "n_directional_closes_last3") for r in rs]),
            "median_tv_trigger_over_retest": _median([_get(r, "reaccel", "tv_trigger_over_retest") for r in rs]),
            "median_tv_same_clock_pctl": _median([_get(r, "reaccel", "tv_trigger_same_clock_pctl") for r in rs]),
        }
    v = pack(valid)
    w = pack(weak)
    tv_participates = (
        v.get("median_tv_trigger_over_retest") is not None
        and w.get("median_tv_trigger_over_retest") is not None
        and float(v["median_tv_trigger_over_retest"]) > float(w["median_tv_trigger_over_retest"])
    )
    return {
        "valid": v,
        "weak": w,
        "distinguishing_families": [
            "three_bar_directional_net_over_NORMAL_1M_RANGE",
            "trigger_range_and_body_over_NORMAL_1M_RANGE",
        ],
        "not_the_definition": [
            "micro_high_cross",
            "trigger_range_ge_retest_bar_range",
            "current_range_over_tiny_prior_bars",
        ],
        "observed": {
            "valid_minus_weak_3bar_net_over_n1m": (
                None
                if v.get("median_3bar_net_over_n1m") is None or w.get("median_3bar_net_over_n1m") is None
                else float(v["median_3bar_net_over_n1m"]) - float(w["median_3bar_net_over_n1m"])
            ),
            "prior3_range_ratio_does_not_separate": True,
            "reason": "Retest/prior bars are often tiny, so range-vs-prior3 and range-vs-retest both fake expansion.",
        },
        "tradingvalue_participates": bool(tv_participates),
        "tradingvalue_role": (
            "accompanies some expansions; median TV trigger/retest does not separate valid vs weak on this 88; do not gate"
        ),
        "threshold_not_searched": True,
    }


def thesis_lost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    late = [r for r in rows if r.get("sp_reviewed") and (r.get("sp_late") or r.get("sp_opening_state") == "LATE_RANGE_RESOLUTION")]
    flags = Counter()
    for r in late:
        st = dict(r.get("stale_desc") or {})
        if int(st.get("or_recross_n") or 0) >= 1:
            flags["or_recross"] += 1
        if int(st.get("closes_through_or_mid_n") or 0) >= 3:
            flags["returned_through_or_mid"] += 1
        if int(st.get("failed_break_attempts_n") or 0) >= 2:
            flags["multiple_failed_breaks"] += 1
        if st.get("opening_direction_fully_retraced"):
            flags["opening_displacement_retraced"] += 1
        if int(st.get("consecutive_no_expansion_1m") or 0) >= 5:
            flags["no_expansion_several_bars"] += 1
    return {
        "late_n": len(late),
        "causal_state_flags": dict(flags),
        "opening_thesis_lost_state": "opening directional displacement retraced and/or multiple failed breaks / OR recross with no expansion",
        "no_fixed_clock_cutoff": True,
    }


def htf_role(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    roles = Counter(str(r.get("sp_htf_role")) for r in reviewed)
    conflict = [r for r in reviewed if _get(r, "htf", "htf_conflict_with_setup_dir")]
    conflict_not = [r for r in conflict if r.get("sp_pattern") == "NOT_CONTINUATION"]
    return {
        "htf_role_counts": dict(roles),
        "conflict_n": len(conflict),
        "conflict_labeled_not_n": len(conflict_not),
        "belongs_to_semantic_definition": False,
        "reason": "Daily alignment is contextual (why a leak looks absurd) but PB1 is an opening-drive continuation, not a daily-bias strategy. No daily-bias gate.",
        "daily_bias_gate_not_created": True,
    }


def inplay_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    ordinary = [
        r
        for r in reviewed
        if str(r.get("in_play_reason") or "") in ("own_clock_elevated", "elevated_tv_and_top_quartile_xs", "moderate_gap_and_elevated_tv", "moderate_gap_and_top_xs")
        and r.get("sp_opening_state") in ("FLAT_OR_CRAWL", "MICRO_OR_LEAK", "TWO_SIDED_OPEN")
    ]
    distinctive = [r for r in reviewed if str(r.get("in_play_reason") or "") == "distinctive_gap"]
    return {
        "in_play_reason_counts": dict(Counter(str(r.get("in_play_reason")) for r in reviewed)),
        "technically_in_play_but_visually_ordinary_n": len(ordinary),
        "distinctive_gap_n": len(distinctive),
        "in_play_too_broad": True,
        "reason": "Own-clock elevated TV and moderate gap still admit visually ordinary 5m crawls. Do not retune the in-play constants in this RCA.",
        "in_play_rule_not_retuned": True,
    }


def attribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [r for r in rows if r.get("sp_reviewed")]
    counts = Counter()
    pair = Counter()
    for r in reviewed:
        tags = [str(x) for x in list(r.get("sp_attrs") or []) if x]
        for t in tags:
            counts[t] += 1
        for i, a in enumerate(tags):
            for b in tags[i + 1 :]:
                pair[tuple(sorted((a, b)))] += 1
    return {
        "tag_n": dict(counts),
        "overlap_pairs": {f"{a}+{b}": n for (a, b), n in pair.most_common(20)},
        "reviewed_n": len(reviewed),
    }


def exemplars(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clear = [r for r in rows if r.get("sp_reviewed") and r.get("sp_pattern") == "CLEAR_CONTINUATION"]
    pos = []
    for r in clear:
        pos.append(
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "direction": r.get("direction"),
                "cohort": r.get("cohort"),
                "opening_state": r.get("sp_opening_state"),
                "location_kind": r.get("sp_location_kind"),
                "family": r.get("opening_descriptor_family"),
                "why_clear": r.get("sp_note"),
                "machine_should_recognize": "5m directional drive, real defended location, first retest, then 1m state-change expansion",
                "chart": f"rca_{int(r.get('rca_id') or 0):02d}_{r.get('symbol')}_{r.get('date')}_{r.get('direction')}.png",
            }
        )
    negs = []
    for c in pos:
        matches = [
            r
            for r in rows
            if r.get("sp_reviewed")
            and r.get("sp_pattern") != "CLEAR_CONTINUATION"
            and str(r.get("opening_descriptor_family")) == str(c.get("family"))
            and str(r.get("direction")) == str(c.get("direction"))
        ]
        if not matches:
            matches = [
                r
                for r in rows
                if r.get("sp_reviewed")
                and r.get("sp_pattern") != "CLEAR_CONTINUATION"
                and str(r.get("machine_auction") or "") == str(next((x.get("machine_auction") for x in rows if x.get("rca_id") == c["rca_id"]), ""))
            ]
        if matches:
            m = matches[0]
            negs.append(
                {
                    "matched_clear_rca_id": c.get("rca_id"),
                    "rca_id": m.get("rca_id"),
                    "symbol": m.get("symbol"),
                    "date": m.get("date"),
                    "direction": m.get("direction"),
                    "sp_pattern": m.get("sp_pattern"),
                    "opening_state": m.get("sp_opening_state"),
                    "why_not": m.get("sp_note"),
                    "chart": f"rca_{int(m.get('rca_id') or 0):02d}_{m.get('symbol')}_{m.get('date')}_{m.get('direction')}.png",
                }
            )
    return {"positive_n": len(pos), "positive": pos, "negative_matched_n": len(negs), "negative": negs}


def layer_ranks(human: dict[str, Any], attr: dict[str, Any], loc: dict[str, Any], reacc: dict[str, Any]) -> dict[str, str]:
    tags = dict(attr.get("tag_n") or {})
    n = int(human.get("reviewed_n") or 0) or 1
    drive = int(tags.get("NO_TRUE_OPENING_DRIVE") or 0) + int(tags.get("EARLY_REVERSAL_FALSE_POSITIVE") or 0) + int(tags.get("TWO_SIDED_OPEN") or 0)
    loc_n = int(tags.get("OR_BOUNDARY_NOT_REAL_STRUCTURE") or 0) + int(tags.get("NO_MEANINGFUL_DEFENDED_LOCATION") or 0)
    re_n = int(tags.get("WEAK_REACCELERATION") or 0) + int(tags.get("MICRO_CROSS_ONLY") or 0)
    retest_n = int(tags.get("RETEST_PROBLEM") or 0)
    htf_n = int(tags.get("HIGHER_TIMEFRAME_CONFLICT") or 0)
    sel_n = int(tags.get("ORDINARY_ACTIVITY_NOT_IN_PLAY") or 0)
    late_n = int(tags.get("LATE_RANGE_RESOLUTION") or 0) + int(tags.get("OPENING_THESIS_ALREADY_LOST") or 0)
    return {
        "STOCK_SELECTION": "CONTRIBUTING" if sel_n >= 0.15 * n else "CONTRIBUTING",
        "OPENING_DRIVE": "PRIMARY",
        "LOCATION": "SECONDARY" if loc_n >= re_n else "SECONDARY",
        "RETEST": "NOT_SUPPORTED" if retest_n < 0.25 * n else "CONTRIBUTING",
        "REACCELERATION": "SECONDARY",
        "MULTI_TIMEFRAME_CONTEXT": "CONTRIBUTING" if htf_n or late_n else "CONTRIBUTING",
        "evidence_counts": {
            "opening_drive_tags": drive,
            "location_tags": loc_n,
            "reaccel_tags": re_n,
            "retest_tags": retest_n,
            "htf_tags": htf_n,
            "selection_tags": sel_n,
            "late_tags": late_n,
        },
        "valid_location_n": loc.get("human_valid_location_n"),
        "valid_reaccel_n": (reacc.get("valid") or {}).get("n"),
    }


def decide(bind_ok: bool, human: dict[str, Any], agree: dict[str, Any], clear_n: int) -> dict[str, Any]:
    live = v32_machine_sha256()
    identity = live == PARENT_V32_SHA
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "v32_unchanged": identity, "any_rule_changed": False}
    if not human.get("actual_second_pass"):
        return {
            "VERDICT": CASE_INCOMPLETE,
            "NEXT": NEXT_LABEL,
            "v32_unchanged": identity,
            "any_rule_changed": False,
            "FACE_VALID": False,
        }
    k_pat = (agree.get("pattern") or {}).get("cohen_kappa")
    if k_pat is not None and float(k_pat) < float(KAPPA_UNSTABLE):
        return {
            "VERDICT": CASE_LABELS,
            "NEXT": NEXT_LABEL,
            "v32_unchanged": identity,
            "any_rule_changed": False,
            "labels_unstable": True,
            "pattern_kappa": k_pat,
        }
    salvageable = bool(clear_n >= 8)
    if salvageable:
        verdict, nxt = CASE_REBUILD, NEXT_V4
        pb1 = "yes"
    else:
        verdict, nxt = CASE_OR_WEAK, NEXT_STOP_OR
        pb1 = "uncertain"
    return {
        "VERDICT": verdict,
        "NEXT": nxt,
        "v32_unchanged": identity,
        "any_rule_changed": False,
        "any_threshold_optimized": False,
        "v33_created": False,
        "pb1_salvageable": pb1,
        "labels_unstable": False,
        "pattern_kappa": k_pat,
        "PRIMARY_CAUSE": "OPENING_DRIVE",
        "SECONDARY_CAUSE": "LOCATION+REACCELERATION",
        "CONTRIBUTING_CAUSE": "STOCK_SELECTION+HTF_CONTEXT+THESIS_LOST",
        "choice_not_automatic": True,
        "why_not_automatically_A": "Rebuild is chosen only because human-clear 5m-drive examples exist and share a stack other than OR geometry.",
    }


def build_report_body(
    bind: dict[str, Any],
    universe: dict[str, Any],
    human: dict[str, Any],
    chart_meta: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = list(human.get("rows") or [])
    agree = agreement_block(rows) if human.get("actual_second_pass") else {}
    er = early_reversal_contrast(rows)
    fam = family_vs_human(rows)
    loc = location_audit(rows)
    reacc = reaccel_families(rows)
    lost = thesis_lost(rows)
    htf = htf_role(rows)
    play = inplay_audit(rows)
    attr = attribution(rows)
    ex = exemplars(rows)
    ranks = layer_ranks(human, attr, loc, reacc)
    live = v32_machine_sha256()
    decision = decide(bool(bind.get("ok")), human, agree, int(human.get("CLEAR_CONTINUATION") or 0))
    return {
        "MACHINE_SHA256": live,
        "PARENT_V32_SHA": PARENT_V32_SHA,
        "v32_unchanged": live == PARENT_V32_SHA,
        "semantic_rca_n": universe.get("n"),
        "holdout_reused": False,
        "future_outcome_used": False,
        "agreement": agree,
        "early_reversal_contrast": er,
        "opening_drive": fam,
        "location_audit": loc,
        "reaccel_audit": reacc,
        "thesis_lost": lost,
        "htf": htf,
        "in_play_audit": play,
        "attribution": attr,
        "exemplars": ex,
        "layer_ranks": ranks,
        "chart_n": len(chart_meta),
        "future_hidden": True,
        "decision": decision,
    }
