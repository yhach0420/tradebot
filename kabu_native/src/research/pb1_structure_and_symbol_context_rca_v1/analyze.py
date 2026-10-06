"""Structural RCA summaries. No filter search. No PnL selection."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from research.pb1_causal_path_failure_rca_v1.analyze import control_fp, fp_pack, market_by_block, path_pack
from research.pb1_opening_range_causal_path_test_v1.analyze import _finite, pct_summary
from research.pb1_opening_range_causal_path_test_v1.match import match_all
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_structure_and_symbol_context_rca_v1 import (
    CASE_BIND,
    CASE_MAPPED,
    EXPECTED_SETUP_N,
    FLIP_CLASSES,
    NEXT_BIND,
    NEXT_REDESIGN,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
    STRUCT_CLASSES,
)
from research.pb1_structure_and_symbol_context_rca_v1.classify import apply_labels


def _subset(rows: list[dict[str, Any]], **eq: Any) -> list[dict[str, Any]]:
    out = rows
    for k, v in eq.items():
        out = [r for r in out if r.get(k) == v]
    return out


def _p50(rows: list[dict[str, Any]], key: str) -> Any:
    return pct_summary([r.get(key) for r in rows]).get("p50")


def _frac(rows: list[dict[str, Any]], key: str) -> Any:
    n = len(rows)
    if not n:
        return None
    return float(sum(1 for r in rows if r.get(key)) / n)


def _pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = path_pack(rows)
    out.update(fp_pack(rows))
    out["or_accept_fail"] = _frac(rows, "or_accept_fail")
    out["reclaim_share"] = _frac(_subset(rows, trigger_primary="RECLAIM_RETEST_MICRO_HIGH"), "trigger_primary") if False else (
        float(sum(1 for r in rows if r.get("trigger_primary") == "RECLAIM_RETEST_MICRO_HIGH") / len(rows)) if rows else None
    )
    out["failed_push_share"] = (
        float(sum(1 for r in rows if r.get("trigger_primary") == "FAILED_PUSH_THEN_CLOSE_BACK") / len(rows)) if rows else None
    )
    out["r10_p50"] = _p50(rows, "r10_bps")
    out["r20_p50"] = _p50(rows, "r20_bps")
    out["MFE_over_R_p50"] = _p50(rows, "MFE_over_R")
    out["MAE_over_R_p50"] = _p50(rows, "MAE_over_R")
    out["P_plus_1_0R_before_fail"] = out.get("P_plus_1_0R_before_fail")
    return out


def class_counts(events: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter(str(e.get("structural_class") or "na") for e in events)
    n = len(events) or 1
    out = {"n": len(events)}
    for k in STRUCT_CLASSES:
        out[k] = int(c.get(k) or 0)
        out[f"{k}_share"] = float((c.get(k) or 0) / n)
    out["other"] = int(sum(v for k, v in c.items() if k not in STRUCT_CLASSES))
    return out


def by_class(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {k: _pack(_subset(events, structural_class=k)) for k in STRUCT_CLASSES}


def by_flip(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {k: _pack(_subset(events, flip_class=k)) for k in FLIP_CLASSES}


def by_trigger_structure(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for trig in ("RECLAIM_RETEST_MICRO_HIGH", "FAILED_PUSH_THEN_CLOSE_BACK"):
        vs = _subset(events, trigger_primary=trig)
        out[trig] = {
            **_pack(vs),
            "class_mix": class_counts(vs),
            "flip_mix": dict(Counter(str(e.get("flip_class")) for e in vs)),
            "room_mix": dict(Counter(str(e.get("room_class")) for e in vs)),
            "congested_share": _frac(_subset(vs, structural_class="STRUCTURALLY_CONGESTED"), "structural_class")
            if False
            else (float(sum(1 for e in vs if e.get("structural_class") == "STRUCTURALLY_CONGESTED") / len(vs)) if vs else None),
            "into_share": float(sum(1 for e in vs if e.get("structural_class") == "BREAK_INTO_RESISTANCE") / len(vs)) if vs else None,
            "flip_share": float(sum(1 for e in vs if e.get("structural_class") == "RESISTANCE_TO_SUPPORT_FLIP") / len(vs)) if vs else None,
        }
    reclaim = _subset(events, trigger_primary="RECLAIM_RETEST_MICRO_HIGH")
    reclaim_flip = [e for e in reclaim if e.get("structural_class") == "RESISTANCE_TO_SUPPORT_FLIP" or e.get("flip_class") == "CLEAN_FLIP"]
    reclaim_sup = [e for e in reclaim if e.get("st_salient_support") or e.get("room_class") == "HAS_SPACE"]
    out["reclaim_in_structural_support"] = _pack(reclaim_flip or reclaim_sup)
    return out


def by_block(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for b in ("D1", "D2", "D3", "D4"):
        vs = _subset(events, block=b)
        out[b] = {**_pack(vs), "class_mix": class_counts(vs), "flip_mix": dict(Counter(str(e.get("flip_class")) for e in vs))}
    return out


def or_vs_structure(events: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(events)
    return {
        "n": n,
        "or_overlaps_prior_zone_share": _frac(events, "st_or_overlaps_prior_zone"),
        "or_in_open_space_share": _frac(events, "st_or_in_open_space"),
        "or_vs_pd": dict(Counter(str(e.get("st_or_vs_pd")) for e in events)),
        "by_class_overlap": {
            k: _frac(_subset(events, structural_class=k), "st_or_overlaps_prior_zone") for k in STRUCT_CLASSES
        },
    }


def reward_geometry(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_room = {k: _pack(_subset(events, room_class=k)) for k in ("NO_KNOWN_RESISTANCE_AHEAD", "RESISTANCE_VERY_CLOSE", "HAS_SPACE")}
    next_atr = pct_summary(
        [
            ((e.get("st_distance_to_next_opposing") or {}).get("atr") if isinstance(e.get("st_distance_to_next_opposing"), dict) else None)
            for e in events
        ]
    )
    next_over_r = pct_summary([e.get("st_next_zone_over_R") for e in events])
    return {"by_room": by_room, "next_opposing_atr": next_atr, "next_zone_over_R": next_over_r}


def archetypes(events: list[dict[str, Any]]) -> dict[str, Any]:
    names = sorted({str(e.get("archetype") or "na") for e in events})
    out: dict[str, Any] = {}
    for a in names:
        vs = _subset(events, archetype=a)
        out[a] = {**_pack(vs), "class_mix": class_counts(vs)}
    return out


def sectors(events: list[dict[str, Any]]) -> dict[str, Any]:
    names = sorted({str(e.get("sector") or "na") for e in events})
    out: dict[str, Any] = {"n_sectors": len(names)}
    by_arch: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        by_arch[str(e.get("archetype") or "na")][str(e.get("sector") or "na")] += 1
    out["archetype_sector_top"] = {
        a: dict(c.most_common(3)) for a, c in by_arch.items()
    }
    out["by_sector"] = {s: {"n": len(_subset(events, sector=s)), "archetype_mix": dict(Counter(str(e.get("archetype")) for e in _subset(events, sector=s)))} for s in names}
    return out


def market_x_class(events: list[dict[str, Any]]) -> dict[str, Any]:
    def _regime(e: dict[str, Any]) -> str:
        b = e.get("market_breadth_0915")
        if not _finite(b):
            return "UNKNOWN"
        bf = float(b)
        sign = int(e.get("DIR") or 0)
        two = 0.40 < bf < 0.60
        if two:
            return "TWO_SIDED"
        agree = (sign > 0 and bf >= 0.60) or (sign < 0 and bf <= 0.40)
        return "BROAD_CONTINUATION" if agree else "BROAD_COUNTER"

    tagged = []
    for e in events:
        row = dict(e)
        row["mkt_regime"] = _regime(e)
        tagged.append(row)
    out: dict[str, Any] = {"by_regime": {}, "class_x_regime": {}}
    for r in ("BROAD_CONTINUATION", "TWO_SIDED", "BROAD_COUNTER", "UNKNOWN"):
        vs = [e for e in tagged if e.get("mkt_regime") == r]
        out["by_regime"][r] = {**_pack(vs), "class_mix": class_counts(vs)}
    for k in STRUCT_CLASSES:
        out["class_x_regime"][k] = dict(Counter(str(e.get("mkt_regime")) for e in tagged if e.get("structural_class") == k))
    return out


def plus1r_differs(byc: dict[str, Any]) -> bool:
    vals = []
    for k in STRUCT_CLASSES:
        v = (byc.get(k) or {}).get("P_plus_1_0R_before_fail")
        if _finite(v):
            vals.append(float(v))
    if len(vals) < 2:
        return False
    return (max(vals) - min(vals)) >= 0.08


def hierarchy(body: dict[str, Any], parent_h: dict[str, Any]) -> dict[str, Any]:
    ev_n = int((body.get("class_counts") or {}).get("n") or 0)
    byc = dict(body.get("by_class") or {})
    into_share = float((body.get("class_counts") or {}).get("BREAK_INTO_RESISTANCE_share") or 0)
    cong_share = float((body.get("class_counts") or {}).get("STRUCTURALLY_CONGESTED_share") or 0)
    p_into = (byc.get("BREAK_INTO_RESISTANCE") or {}).get("P_plus_1_0R_before_fail")
    p_open = (byc.get("OPEN_SPACE_BREAK") or {}).get("P_plus_1_0R_before_fail")
    p_thr = (byc.get("BREAK_THROUGH_RESISTANCE") or {}).get("P_plus_1_0R_before_fail")
    p_flip = (byc.get("RESISTANCE_TO_SUPPORT_FLIP") or {}).get("P_plus_1_0R_before_fail")
    room = dict((body.get("reward") or {}).get("by_room") or {})
    p_close = (room.get("RESISTANCE_VERY_CLOSE") or {}).get("P_plus_1_0R_before_fail")
    p_known_none = (room.get("NO_KNOWN_RESISTANCE_AHEAD") or {}).get("P_plus_1_0R_before_fail")
    p_space = (room.get("HAS_SPACE") or {}).get("P_plus_1_0R_before_fail")
    trig = dict(body.get("trigger_structure") or {})
    fp_into_worse = bool(
        _finite(p_into)
        and (
            (_finite(p_open) and float(p_into) + 0.08 <= float(p_open))
            or (_finite(p_thr) and float(p_into) + 0.08 <= float(p_thr))
        )
    )
    struct_block = bool(into_share >= 0.30 and fp_into_worse)
    failed_flip = dict((body.get("by_flip") or {}).get("FAILED_FLIP") or {})
    clean_flip = dict((body.get("by_flip") or {}).get("CLEAN_FLIP") or {})
    flip_matters = bool(
        _finite(clean_flip.get("P_plus_1_0R_before_fail"))
        and _finite(failed_flip.get("P_plus_1_0R_before_fail"))
        and abs(float(clean_flip["P_plus_1_0R_before_fail"]) - float(failed_flip["P_plus_1_0R_before_fail"])) >= 0.08
    )
    no_room = bool(
        _finite(p_close)
        and (
            (_finite(p_space) and float(p_close) + 0.08 <= float(p_space))
            or (_finite(p_known_none) and float(p_close) + 0.08 <= float(p_known_none))
        )
    )
    arch = dict(body.get("archetypes") or {})
    arch_vals = [float(v["P_plus_1_0R_before_fail"]) for v in arch.values() if _finite((v or {}).get("P_plus_1_0R_before_fail")) and int((v or {}).get("n") or 0) >= 20]
    arch_diff = bool(len(arch_vals) >= 2 and (max(arch_vals) - min(arch_vals)) >= 0.10)
    mkt = dict(body.get("market_x_class") or {}).get("by_regime") or {}
    mkt_vals = [float(v["P_plus_1_0R_before_fail"]) for k, v in mkt.items() if k != "UNKNOWN" and _finite((v or {}).get("P_plus_1_0R_before_fail")) and int((v or {}).get("n") or 0) >= 20]
    mkt_diff = bool(len(mkt_vals) >= 2 and (max(mkt_vals) - min(mkt_vals)) >= 0.08)
    blocks = dict(body.get("by_block") or {})
    d2_into = float(((blocks.get("D2") or {}).get("class_mix") or {}).get("BREAK_INTO_RESISTANCE_share") or 0)
    d3_into = float(((blocks.get("D3") or {}).get("class_mix") or {}).get("BREAK_INTO_RESISTANCE_share") or 0)
    d1_into = float(((blocks.get("D1") or {}).get("class_mix") or {}).get("BREAK_INTO_RESISTANCE_share") or 0)
    d4_into = float(((blocks.get("D4") or {}).get("class_mix") or {}).get("BREAK_INTO_RESISTANCE_share") or 0)
    d23_blocked = bool(d2_into >= d1_into + 0.05 or d3_into >= d1_into + 0.05)
    parent_rank = dict(parent_h.get("rank") or {})
    fp_all = dict(body.get("first_passage") or {}).get("all") or {}
    fp_ct = dict(body.get("control_first_passage") or {}).get("all") or {}
    p1 = fp_all.get("P_plus_1_0R_before_fail")
    p1c = fp_ct.get("P_plus_1_0R_before_fail")
    fp_beats = bool(_finite(p1) and _finite(p1c) and float(p1) > float(p1c) + 0.05)
    human = dict(body.get("human") or {})
    contamination = bool((human.get("structural_classification_human_valid_share") or 1) < 0.60)

    rank = {
        "TRIGGER_SEMANTICS_MIXED": parent_rank.get("TRIGGER SEMANTICS MIXED") or "PRIMARY_CAUSE",
        "RETEST_EXHAUSTION": parent_rank.get("RETEST TOO STALE") or "SECONDARY_CAUSE",
        "INVALIDATION_SEMANTICS": parent_rank.get("INVALIDATION SEMANTICS WRONG") or "CONTRIBUTING_CAUSE",
        "STRUCTURAL_RESISTANCE_BLOCKING": "PRIMARY_CAUSE" if struct_block and into_share >= 0.45 else ("SECONDARY_CAUSE" if struct_block else ("CONTRIBUTING_CAUSE" if into_share >= 0.25 else "NOT_SUPPORTED")),
        "FAILED_SR_FLIP": "SECONDARY_CAUSE" if flip_matters and _finite(p_flip) else ("CONTRIBUTING_CAUSE" if flip_matters else "NOT_SUPPORTED"),
        "NO_REWARD_SPACE": "SECONDARY_CAUSE" if no_room else "NOT_SUPPORTED",
        "SYMBOL_BEHAVIOR_MISMATCH": "CONTRIBUTING_CAUSE" if arch_diff else "NOT_SUPPORTED",
        "MARKET_REGIME": "CONTRIBUTING_CAUSE" if mkt_diff else "NOT_SUPPORTED",
        "SEMANTIC_CONTAMINATION": "CONTRIBUTING_CAUSE" if contamination else (parent_rank.get("SEMANTIC CONTAMINATION") or "CONTRIBUTING_CAUSE"),
        "NO_TRUE_EDGE": "NOT_SUPPORTED" if fp_beats else (parent_rank.get("NO TRUE EDGE") or "NOT_SUPPORTED"),
    }
    # Trigger mix remains the established primary unless structure is the majority mechanism.
    if rank["STRUCTURAL_RESISTANCE_BLOCKING"] == "PRIMARY_CAUSE" and rank["TRIGGER_SEMANTICS_MIXED"] == "PRIMARY_CAUSE":
        rank["STRUCTURAL_RESISTANCE_BLOCKING"] = "SECONDARY_CAUSE"
    primaries = [k for k, v in rank.items() if v == "PRIMARY_CAUSE"]
    secondaries = [k for k, v in rank.items() if v == "SECONDARY_CAUSE"]
    contributing = [k for k, v in rank.items() if v == "CONTRIBUTING_CAUSE"]
    unknown = [k for k, v in rank.items() if v == "UNKNOWN"]
    not_sup = [k for k, v in rank.items() if v == "NOT_SUPPORTED"]
    return {
        "rank": rank,
        "PRIMARY_CAUSE": primaries[0] if len(primaries) == 1 else (" + ".join(primaries) if primaries else None),
        "SECONDARY_CAUSE": secondaries[0] if len(secondaries) == 1 else (" + ".join(secondaries) if secondaries else None),
        "CONTRIBUTING_CAUSE": contributing,
        "NOT_SUPPORTED": not_sup,
        "UNKNOWN": unknown,
        "flags": {
            "into_share": into_share,
            "congested_share": cong_share,
            "fp_into": p_into,
            "fp_open": p_open,
            "fp_through": p_thr,
            "fp_flip": p_flip,
            "struct_block": struct_block,
            "flip_matters": flip_matters,
            "no_room": no_room,
            "arch_diff": arch_diff,
            "mkt_diff": mkt_diff,
            "d2_into": d2_into,
            "d3_into": d3_into,
            "d4_into": d4_into,
            "d23_more_blocked": d23_blocked,
            "fp_beats_controls": fp_beats,
            "plus1r_differs_by_class": plus1r_differs(byc),
            "or_overlap_share": (body.get("or_vs_structure") or {}).get("or_overlaps_prior_zone_share"),
            "failed_push_into": (trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {}).get("into_share"),
            "failed_push_congested": (trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {}).get("congested_share"),
            "reclaim_flip": (trig.get("RECLAIM_RETEST_MICRO_HIGH") or {}).get("flip_share"),
            "n": ev_n,
            "parent_primary": parent_h.get("PRIMARY_CAUSE"),
        },
        "parent": {
            "PRIMARY_CAUSE": parent_h.get("PRIMARY_CAUSE"),
            "SECONDARY_CAUSE": parent_h.get("SECONDARY_CAUSE"),
            "CONTRIBUTING_CAUSE": parent_h.get("CONTRIBUTING_CAUSE"),
        },
    }


def decide(bind_ok: bool, identity_ok: bool, hier: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok or not identity_ok:
        return {
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "STOP_PB1": False,
            "eligibility_changed": False,
            "redesign_from_trigger_rca_alone": False,
        }
    return {
        "VERDICT": CASE_MAPPED,
        "NEXT": NEXT_REDESIGN,
        "STOP_PB1": False,
        "is_strategy": False,
        "eligibility_changed": False,
        "threshold_optimized": False,
        "best_subgroup_selected": False,
        "pnl_optimization": False,
        "failed_push_purged": False,
        "reclaim_selected": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "redesign_from_trigger_rca_alone": False,
        "PRIMARY_CAUSE": hier.get("PRIMARY_CAUSE"),
        "SECONDARY_CAUSE": hier.get("SECONDARY_CAUSE"),
        "CONTRIBUTING_CAUSE": hier.get("CONTRIBUTING_CAUSE"),
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    sample: list[dict[str, Any]],
    chart_meta: list[dict[str, Any]],
) -> dict[str, Any]:
    events = list(walked.get("events") or [])
    eligible = list(walked.get("eligible") or [])
    recs = dict(walked.get("recs") or {})
    matched = match_all(events, eligible, recs)
    pairs = list(matched.get("pairs") or [])
    cfp = control_fp(pairs, events, eligible, recs)
    human = apply_labels(sample)
    counts = class_counts(events)
    byc = by_class(events)
    body: dict[str, Any] = {
        "MACHINE_SHA256": v2_machine_sha256(),
        "setup_n": len(events),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "identity_ok": len(events) == int(EXPECTED_SETUP_N) and v2_machine_sha256() == PARENT_PLAYBOOK_MACHINE_SHA256,
        "class_counts": counts,
        "by_class": byc,
        "by_flip": by_flip(events),
        "trigger_structure": by_trigger_structure(events),
        "by_block": by_block(events),
        "or_vs_structure": or_vs_structure(events),
        "reward": reward_geometry(events),
        "archetypes": archetypes(events),
        "sectors": sectors(events),
        "market_x_class": market_x_class(events),
        "market_by_block": market_by_block(list(walked.get("market_days") or [])),
        "first_passage": {"all": fp_pack(events), "by_class": {k: fp_pack(_subset(events, structural_class=k)) for k in STRUCT_CLASSES}},
        "control_first_passage": cfp,
        "treated_vs_control": {
            "matched_n": matched.get("matched_n"),
            "match_rate": matched.get("match_rate"),
            "structure_matched_away": False,
            "new_overmatching": False,
        },
        "plus1r_differs_by_class": plus1r_differs(byc),
        "human": human,
        "chart_meta": chart_meta,
        "sample_n": len(sample),
        "walk_counts": walked.get("counts"),
        "parent_verdict": PARENT_VERDICT,
    }
    hier = hierarchy(body, dict(bind.get("parent_hierarchy") or {}))
    body["hierarchy"] = hier
    body["decision"] = decide(bool(bind.get("ok")), bool(body.get("identity_ok")), hier)
    return body
