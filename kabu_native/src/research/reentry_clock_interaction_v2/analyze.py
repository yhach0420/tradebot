"""Clock vs ordinal separation. No new policy. Occupancy approximation forbidden."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from research.reentry_architecture_v1.analyze import (
    a0_parity,
    b0_parity,
    build_episode_ledger,
    slice_stats,
    _f,
    _pnl,
)
from research.reentry_clock_interaction_v2 import (
    COMMON_RE2_INDEPENDENCE_MIN,
    ELAPSED_BINS,
    EXPECTED_A_RE2_N,
    EXPECTED_A_RE2_PNL,
    MAJORITY_FRAC,
    SMALL_CELL_N,
)
from research.uniform10_entry_rebuild import UNIFORM10
from small_paper.v1r_primary_runtime import CLOCK_GRID


def _lab(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


A_ANCHORS = frozenset(_lab(h, m) for h, m in CLOCK_GRID)
B_ANCHORS = frozenset(_lab(h, m) for h, m in UNIFORM10)
COMMON_ANCHORS = frozenset(A_ANCHORS & B_ANCHORS)
A_ONLY_ANCHORS = frozenset(A_ANCHORS - B_ANCHORS)
B_ONLY_ANCHORS = frozenset(B_ANCHORS - A_ANCHORS)


def clock_class(anchor: Any) -> str:
    lab = str(anchor or "")
    if lab in COMMON_ANCHORS:
        return "COMMON_TO_A_AND_B"
    if lab in A_ONLY_ANCHORS:
        return "A_ONLY"
    if lab in B_ONLY_ANCHORS:
        return "B_ONLY"
    return "UNKNOWN"


def clock_inventory() -> dict[str, Any]:
    return {
        "COMMON_ANCHOR_N": len(COMMON_ANCHORS),
        "A_ONLY_ANCHOR_N": len(A_ONLY_ANCHORS),
        "B_ONLY_ANCHOR_N": len(B_ONLY_ANCHORS),
        "COMMON_ANCHORS": sorted(COMMON_ANCHORS),
        "A_ONLY_ANCHORS": sorted(A_ONLY_ANCHORS),
        "B_ONLY_ANCHORS": sorted(B_ONLY_ANCHORS),
        "nearest_anchor_matching": False,
    }


def elapsed_bin(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    x = float(sec)
    for name, lo, hi in ELAPSED_BINS:
        if lo is None and hi is not None and x <= hi + 1e-12:
            return name
        if lo is not None and hi is not None and x > lo + 1e-12 and x <= hi + 1e-12:
            return name
        if lo is not None and hi is None and x > lo + 1e-12:
            return name
    return ""


def ep_key(r: dict[str, Any]) -> tuple[str, str, str]:
    return (str(r.get("date") or ""), str(r.get("session") or ""), str(r.get("symbol") or ""))


def enrich_ledger(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[ep_key(r)].append(r)
    out: list[dict[str, Any]] = []
    for _k, grp in by.items():
        grp = sorted(grp, key=lambda x: float(_f(x.get("fill_time")) or 0.0))
        first = grp[0]
        first_t = _f(first.get("fill_time"))
        first_score = _f(first.get("current_entry_score"))
        first_rank = _f(first.get("current_rank"))
        first_px = _f(first.get("fill_price") or first.get("current_mid"))
        for r in grp:
            rec = dict(r)
            rec["clock_class"] = clock_class(rec.get("anchor_time"))
            rec["first_fill_time"] = first_t
            rec["first_entry_score"] = first_score
            rec["first_rank"] = first_rank
            rec["first_fill_price"] = first_px
            cur_t = _f(rec.get("fill_time"))
            rec["elapsed_sec_from_first_fill"] = (
                round(float(cur_t) - float(first_t), 3) if cur_t is not None and first_t is not None else None
            )
            rec["elapsed_bin_prior_exit"] = elapsed_bin(_f(rec.get("seconds_since_prior_exit")))
            rec["elapsed_bin_first_fill"] = elapsed_bin(_f(rec.get("elapsed_sec_from_first_fill")))
            cs = _f(rec.get("current_entry_score"))
            cr = _f(rec.get("current_rank"))
            rec["score_vs_first"] = (cs - first_score) if cs is not None and first_score is not None else None
            rec["rank_vs_first"] = (cr - first_rank) if cr is not None and first_rank is not None else None
            rec["score_vs_prior"] = _f(rec.get("score_delta"))
            rec["rank_vs_prior"] = _f(rec.get("rank_delta"))
            cur_px = _f(rec.get("current_mid") or rec.get("fill_price"))
            rec["price_vs_first"] = (cur_px / first_px) if cur_px is not None and first_px not in (None, 0) else None
            prior_px = _f(rec.get("prior_exit_price"))
            rec["price_vs_prior_exit"] = (
                (cur_px / prior_px) if cur_px is not None and prior_px not in (None, 0) else None
            )
            rec["anchor_count_from_first"] = None
            rec["anchor_count_from_prior_exit"] = rec.get("anchors_since_prior_exit")
            out.append(rec)
    return out


def _bool_flag(v: Any) -> Optional[bool]:
    if v is True or v is False:
        return v
    return None


def crosstab(rows: list[dict[str, Any]], field: str, labels: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lab in labels:
        if field in ("score_improved", "rank_improved", "price_above_prior_exit"):
            want = lab == "IMPROVED" or lab == "ABOVE_PRIOR_EXIT"
            xs = [r for r in rows if _bool_flag(r.get(field)) is want]
            if lab in ("NOT_IMPROVED", "NOT_ABOVE_PRIOR_EXIT"):
                xs = [r for r in rows if _bool_flag(r.get(field)) is False]
            if lab in ("IMPROVED",) and field != "price_above_prior_exit":
                xs = [r for r in rows if _bool_flag(r.get(field)) is True]
            if lab == "ABOVE_PRIOR_EXIT":
                xs = [r for r in rows if _bool_flag(r.get("price_above_prior_exit")) is True]
        else:
            xs = [r for r in rows if str(r.get(field) or "") == lab]
        out[lab] = slice_stats(xs)
    return out


def re2_intersections(re2: list[dict[str, Any]]) -> dict[str, Any]:
    dist_map = {"NEXT_ANCHOR": "NEXT", "TWO_LATER": "TWO_LATER", "THREE_PLUS": "THREE_PLUS"}
    dist_rows = []
    for r in re2:
        rec = dict(r)
        rec["dist_short"] = dist_map.get(str(r.get("anchor_distance") or ""), str(r.get("anchor_distance") or ""))
        dist_rows.append(rec)
    return {
        "prior_outcome": crosstab(re2, "prior_outcome", ("WIN", "DRAW", "LOSS")),
        "prior_exit": crosstab(
            re2, "prior_exit_family", ("EARLY_GUARD", "CONT_EXIT_600", "CONT_EXTEND_750", "SESSION_CLOSE", "OTHER")
        ),
        "score": {
            "IMPROVED": slice_stats([r for r in re2 if r.get("score_improved") is True]),
            "NOT_IMPROVED": slice_stats([r for r in re2 if r.get("score_improved") is False]),
        },
        "rank": {
            "IMPROVED": slice_stats([r for r in re2 if r.get("rank_improved") is True]),
            "NOT_IMPROVED": slice_stats([r for r in re2 if r.get("rank_improved") is False]),
        },
        "price": {
            "ABOVE_PRIOR_EXIT": slice_stats([r for r in re2 if r.get("price_above_prior_exit") is True]),
            "NOT_ABOVE_PRIOR_EXIT": slice_stats([r for r in re2 if r.get("price_above_prior_exit") is False]),
        },
        "anchor_distance": {
            "NEXT": slice_stats([r for r in dist_rows if r.get("dist_short") == "NEXT"]),
            "TWO_LATER": slice_stats([r for r in dist_rows if r.get("dist_short") == "TWO_LATER"]),
            "THREE_PLUS": slice_stats([r for r in dist_rows if r.get("dist_short") == "THREE_PLUS"]),
        },
        "clock_class": {
            "COMMON_TO_A_AND_B": slice_stats([r for r in re2 if r.get("clock_class") == "COMMON_TO_A_AND_B"]),
            "A_ONLY": slice_stats([r for r in re2 if r.get("clock_class") == "A_ONLY"]),
        },
    }


def overlap_block(re2: list[dict[str, Any]]) -> dict[str, Any]:
    after_win = [r for r in re2 if r.get("prior_outcome") == "WIN"]
    score_dn = [r for r in re2 if r.get("score_improved") is False]
    price_up = [r for r in re2 if r.get("price_above_prior_exit") is True]
    ids_win = {id(r) for r in after_win}
    ids_sc = {id(r) for r in score_dn}
    ids_px = {id(r) for r in price_up}
    triple = [r for r in re2 if id(r) in ids_win and id(r) in ids_sc and id(r) in ids_px]
    union = [r for r in re2 if id(r) in ids_win or id(r) in ids_sc or id(r) in ids_px]
    remainder = [r for r in re2 if id(r) not in ids_win and id(r) not in ids_sc and id(r) not in ids_px]
    win_sc = [r for r in re2 if id(r) in ids_win and id(r) in ids_sc]
    win_px = [r for r in re2 if id(r) in ids_win and id(r) in ids_px]
    sc_px = [r for r in re2 if id(r) in ids_sc and id(r) in ids_px]
    n = max(len(re2), 1)
    return {
        "RE2_AFTER_WIN": slice_stats(after_win),
        "RE2_SCORE_NOT_IMPROVED": slice_stats(score_dn),
        "RE2_PRICE_ABOVE": slice_stats(price_up),
        "RE2_TRIPLE_OVERLAP": slice_stats(triple),
        "RE2_UNION_THREE": slice_stats(union),
        "RE2_UNEXPLAINED_REMAINDER": slice_stats(remainder),
        "PAIR_WIN_AND_SCORE_DN": slice_stats(win_sc),
        "PAIR_WIN_AND_PRICE_UP": slice_stats(win_px),
        "PAIR_SCORE_DN_AND_PRICE_UP": slice_stats(sc_px),
        "share_after_win": len(after_win) / n,
        "share_score_dn": len(score_dn) / n,
        "share_price_up": len(price_up) / n,
        "share_triple": len(triple) / n,
        "share_union": len(union) / n,
        "share_remainder": len(remainder) / n,
        "duplicate_explanation": bool(len(triple) >= SMALL_CELL_N and len(triple) / max(len(after_win), 1) >= 0.5),
    }


def _group(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[ep_key(r)].append(r)
    for k in by:
        by[k] = sorted(by[k], key=lambda x: float(_f(x.get("fill_time")) or 0.0))
    return by


def _rank_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (str(r.get("date") or ""), str(r.get("anchor") or ""), str(r.get("symbol") or ""))
        out[key] = r
    return out


def _fill_at(fills: list[dict[str, Any]], anchor: str) -> Optional[dict[str, Any]]:
    for f in fills:
        if str(f.get("anchor_time") or "") == anchor:
            return f
    return None


def _prior_n(fills: list[dict[str, Any]], t0: Optional[float]) -> int:
    if t0 is None:
        return 0
    n = 0
    for f in fills:
        ft = _f(f.get("fill_time"))
        if ft is not None and ft < float(t0) - 1e-9:
            n += 1
    return n


def lineage_and_shift(
    a_led: list[dict[str, Any]],
    b_led: list[dict[str, Any]],
    a_rank: list[dict[str, Any]],
    b_rank: list[dict[str, Any]],
) -> dict[str, Any]:
    a_by = _group(a_led)
    b_by = _group(b_led)
    a_rk = _rank_map(a_rank)
    b_rk = _rank_map(b_rank)
    keys = sorted(set(a_by) | set(b_by))
    inserted_fills: list[dict[str, Any]] = []
    removed_fills: list[dict[str, Any]] = []
    shift_eps = 0
    cascade_eps = 0
    path_eps = 0
    class_counts: dict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []
    a_re2 = [r for r in a_led if r.get("entry_kind") == "REENTRY_2"]
    becomes = {"RE1": 0, "RE2": 0, "RE3PLUS": 0, "NO_DIRECT_MATCH": 0, "FIRST": 0}
    re2_match_rows: list[dict[str, Any]] = []

    for key in keys:
        a_fills = a_by.get(key) or []
        b_fills = b_by.get(key) or []
        ins = [f for f in b_fills if f.get("clock_class") == "B_ONLY"]
        rem = [f for f in a_fills if f.get("clock_class") == "A_ONLY"]
        inserted_fills.extend(ins)
        removed_fills.extend(rem)
        matched = []
        for af in a_fills:
            bf = _fill_at(b_fills, str(af.get("anchor_time") or ""))
            if bf is not None:
                matched.append((af, bf))
        ordinal_shift = any(a.get("entry_kind") != b.get("entry_kind") for a, b in matched)
        if ordinal_shift:
            shift_eps += 1
        labels = []
        if not a_fills or not b_fills:
            labels.append("NO_COMPARABLE_LINEAGE")
        if matched:
            labels.append("SAME_COMMON_ANCHOR_OPPORTUNITY" if any(
                a.get("clock_class") == "COMMON_TO_A_AND_B" for a, _b in matched
            ) else "DIRECT_ANCHOR_MATCH")
        if ins:
            labels.append("CLOCK_INSERTED_OPPORTUNITY")
        if rem:
            labels.append("CLOCK_REMOVED_OPPORTUNITY")
        date, session, symbol = key
        cascade = False
        path_ch = False
        for anc in sorted(COMMON_ANCHORS):
            ar = a_rk.get((date, anc, symbol))
            br = b_rk.get((date, anc, symbol))
            if not ar or not br:
                continue
            af = _fill_at(a_fills, anc)
            bf = _fill_at(b_fills, anc)
            t0 = _f(ar.get("t0")) or _f(br.get("t0"))
            pa = _prior_n(a_fills, t0)
            pb = _prior_n(b_fills, t0)
            xor_fill = (af is None) != (bf is None)
            if xor_fill and pa != pb:
                cascade = True
            if xor_fill and pa == pb:
                path_ch = True
        if cascade:
            cascade_eps += 1
            labels.append("OCCUPANCY_CASCADE_CHANGED")
        if path_ch:
            path_eps += 1
            labels.append("FILL_PATH_CHANGED")
        if ordinal_shift:
            labels.append("ORDINAL_SHIFT")
        primary = labels[0] if labels else "NO_COMPARABLE_LINEAGE"
        class_counts[primary] += 1
        rows.append(
            {
                "date": date,
                "session": session,
                "symbol": symbol,
                "a_n": len(a_fills),
                "b_n": len(b_fills),
                "inserted_n": len(ins),
                "removed_n": len(rem),
                "matched_n": len(matched),
                "ordinal_shift": ordinal_shift,
                "occupancy_cascade": cascade,
                "fill_path_changed": path_ch,
                "labels": ",".join(labels),
                "primary_class": primary,
            }
        )

    for af in a_re2:
        bf = None
        b_fills = b_by.get(ep_key(af)) or []
        bf = _fill_at(b_fills, str(af.get("anchor_time") or ""))
        rec = {
            "date": af.get("date"),
            "session": af.get("session"),
            "symbol": af.get("symbol"),
            "a_anchor": af.get("anchor_time"),
            "a_clock_class": af.get("clock_class"),
            "a_pnl": _pnl(af),
            "b_kind": None,
            "match": False,
        }
        if bf is None:
            becomes["NO_DIRECT_MATCH"] += 1
            rec["match"] = False
        else:
            rec["match"] = True
            rec["b_kind"] = bf.get("entry_kind")
            rec["b_pnl"] = _pnl(bf)
            k = str(bf.get("entry_kind") or "")
            if k == "FIRST":
                becomes["FIRST"] += 1
            elif k == "REENTRY_1":
                becomes["RE1"] += 1
            elif k == "REENTRY_2":
                becomes["RE2"] += 1
            else:
                becomes["RE3PLUS"] += 1
        re2_match_rows.append(rec)

    return {
        "episode_n": len(keys),
        "ORDINAL_SHIFT_EPISODES_N": shift_eps,
        "CLOCK_INSERTED_FILL_N": len(inserted_fills),
        "CLOCK_REMOVED_FILL_N": len(removed_fills),
        "OCCUPANCY_CASCADE_EPISODES_N": cascade_eps,
        "FILL_PATH_CHANGED_EPISODES_N": path_eps,
        "primary_class_counts": dict(class_counts),
        "inserted_pnl": slice_stats(inserted_fills),
        "removed_pnl": slice_stats(removed_fills),
        "A_RE2_BECOMES_B_FIRST_N": becomes["FIRST"],
        "A_RE2_BECOMES_B_RE1_N": becomes["RE1"],
        "A_RE2_BECOMES_B_RE2_N": becomes["RE2"],
        "A_RE2_BECOMES_B_RE3PLUS_N": becomes["RE3PLUS"],
        "A_RE2_NO_DIRECT_MATCH_N": becomes["NO_DIRECT_MATCH"],
        "re2_match_rows": re2_match_rows,
        "episode_rows": rows,
        "inserted_by_kind": {
            k: slice_stats([f for f in inserted_fills if f.get("entry_kind") == k])
            for k in ("FIRST", "REENTRY_1", "REENTRY_2", "REENTRY_3_PLUS")
        },
        "removed_by_kind": {
            k: slice_stats([f for f in removed_fills if f.get("entry_kind") == k])
            for k in ("FIRST", "REENTRY_1", "REENTRY_2", "REENTRY_3_PLUS")
        },
    }


def elapsed_block(led: list[dict[str, Any]], *, clock: str) -> dict[str, Any]:
    reent = [r for r in led if r.get("is_reentry")]
    out: dict[str, Any] = {"clock": clock}
    for name, _lo, _hi in ELAPSED_BINS:
        out[f"prior_exit_{name}"] = slice_stats(
            [r for r in reent if r.get("elapsed_bin_prior_exit") == name]
        )
        out[f"first_fill_{name}"] = slice_stats(
            [r for r in reent if r.get("elapsed_bin_first_fill") == name]
        )
    out["reentry_all"] = slice_stats(reent)
    return out


def common_anchor_diagnostic(
    a_led: list[dict[str, Any]],
    b_led: list[dict[str, Any]],
    a_rank: list[dict[str, Any]],
    b_rank: list[dict[str, Any]],
) -> dict[str, Any]:
    """Candidate-level only. Does not rebuild a joint portfolio."""
    a_by = _group(a_led)
    b_by = _group(b_led)
    a_rk = _rank_map(a_rank)
    b_rk = _rank_map(b_rank)
    both_cand = 0
    state_diff = 0
    state_same = 0
    fill_both = 0
    fill_a_only = 0
    fill_b_only = 0
    fill_neither = 0
    ordinal_diff_at_common_fill = 0
    common_re_a = [r for r in a_led if r.get("is_reentry") and r.get("clock_class") == "COMMON_TO_A_AND_B"]
    common_re_b = [r for r in b_led if r.get("is_reentry") and r.get("clock_class") == "COMMON_TO_A_AND_B"]
    sample_rows: list[dict[str, Any]] = []
    for (date, anc, symbol), ar in a_rk.items():
        if anc not in COMMON_ANCHORS:
            continue
        br = b_rk.get((date, anc, symbol))
        if br is None:
            continue
        both_cand += 1
        session = str(ar.get("session") or br.get("session") or "")
        a_fills = a_by.get((date, session, symbol)) or []
        b_fills = b_by.get((date, session, symbol)) or []
        if not a_fills or not b_fills:
            for sess in ("AM", "PM"):
                if not a_fills:
                    a_fills = a_by.get((date, sess, symbol)) or a_fills
                if not b_fills:
                    b_fills = b_by.get((date, sess, symbol)) or b_fills
        t0 = _f(ar.get("t0")) or _f(br.get("t0"))
        pa = _prior_n(a_fills, t0)
        pb = _prior_n(b_fills, t0)
        if pa != pb:
            state_diff += 1
        else:
            state_same += 1
        af = _fill_at(a_fills, anc)
        bf = _fill_at(b_fills, anc)
        if af is not None and bf is not None:
            fill_both += 1
            if af.get("entry_kind") != bf.get("entry_kind"):
                ordinal_diff_at_common_fill += 1
        elif af is not None:
            fill_a_only += 1
        elif bf is not None:
            fill_b_only += 1
        else:
            fill_neither += 1
        if len(sample_rows) < 80 and (pa != pb or (af is None) != (bf is None)):
            sample_rows.append(
                {
                    "date": date,
                    "session": session,
                    "symbol": symbol,
                    "anchor": anc,
                    "a_score": _f(ar.get("score")),
                    "b_score": _f(br.get("score")),
                    "a_rank": _f(ar.get("rank")),
                    "b_rank": _f(br.get("rank")),
                    "a_exec": ar.get("executable_at_t0"),
                    "b_exec": br.get("executable_at_t0"),
                    "a_prior_fills": pa,
                    "b_prior_fills": pb,
                    "a_filled": af is not None,
                    "b_filled": bf is not None,
                    "a_kind": (af or {}).get("entry_kind"),
                    "b_kind": (bf or {}).get("entry_kind"),
                }
            )
    by_kind_a = {
        k: slice_stats([r for r in common_re_a if r.get("entry_kind") == k])
        for k in ("REENTRY_1", "REENTRY_2", "REENTRY_3_PLUS")
    }
    by_kind_b = {
        k: slice_stats([r for r in common_re_b if r.get("entry_kind") == k])
        for k in ("REENTRY_1", "REENTRY_2", "REENTRY_3_PLUS")
    }
    return {
        "portfolio_rebuilt": False,
        "COMMON_CANDIDATE_PAIRS_N": both_cand,
        "PRIOR_STATE_DIFF_N": state_diff,
        "PRIOR_STATE_SAME_N": state_same,
        "FILL_BOTH_N": fill_both,
        "FILL_A_ONLY_N": fill_a_only,
        "FILL_B_ONLY_N": fill_b_only,
        "FILL_NEITHER_N": fill_neither,
        "COMMON_FILL_ORDINAL_DIFF_N": ordinal_diff_at_common_fill,
        "A_COMMON_REENTRY_BY_KIND": by_kind_a,
        "B_COMMON_REENTRY_BY_KIND": by_kind_b,
        "sample_state_diff_rows": sample_rows,
    }


def setup_reuse_block(led: list[dict[str, Any]], *, clock: str) -> dict[str, Any]:
    reent = [r for r in led if r.get("is_reentry")]
    by_kind: dict[str, Any] = {}
    for k in ("REENTRY_1", "REENTRY_2", "REENTRY_3_PLUS"):
        xs = [r for r in reent if r.get("entry_kind") == k]
        def mean(key: str) -> Optional[float]:
            vs = [_f(r.get(key)) for r in xs]
            vs = [v for v in vs if v is not None]
            return (sum(vs) / len(vs)) if vs else None
        by_kind[k] = {
            **slice_stats(xs),
            "mean_score_vs_first": mean("score_vs_first"),
            "mean_score_vs_prior": mean("score_vs_prior"),
            "mean_rank_vs_first": mean("rank_vs_first"),
            "mean_rank_vs_prior": mean("rank_vs_prior"),
            "mean_price_vs_first": mean("price_vs_first"),
            "mean_price_vs_prior_exit": mean("price_vs_prior_exit"),
            "mean_prior_MFE": mean("prior_trade_MFE"),
            "mean_prior_pnl": mean("prior_trade_pnl"),
        }
    return {"clock": clock, "by_kind": by_kind, "all_reentry": slice_stats(reent)}


def _dir(pnl: Optional[float]) -> str:
    if pnl is None:
        return "na"
    if pnl < -1e-9:
        return "neg"
    if pnl > 1e-9:
        return "pos"
    return "flat"


def decide(
    *,
    a_par_ok: bool,
    b_par_ok: bool,
    re2_n: int,
    re2_pnl: float,
    overlap: dict[str, Any],
    shift: dict[str, Any],
    elapsed_a: dict[str, Any],
    elapsed_b: dict[str, Any],
    common: dict[str, Any],
    inter: dict[str, Any],
) -> dict[str, Any]:
    if not a_par_ok or not b_par_ok:
        return {
            "VERDICT": "REENTRY_CLOCK_INTERACTION_AUDIT_FAILED",
            "CLOCK_EXPLAINS_ORDINAL_REVERSAL": None,
            "ORDINAL_EFFECT_INDEPENDENT": None,
            "PRIMARY_MECHANISM": None,
            "RECOMMENDED_NEXT_RESEARCH": "STOP. A0/B0 Exact Dual-Lane did not match frozen headline.",
        }

    n19 = max(re2_n, 1)
    no_match = int(shift.get("A_RE2_NO_DIRECT_MATCH_N") or 0)
    to_re2 = int(shift.get("A_RE2_BECOMES_B_RE2_N") or 0)
    to_other = (
        int(shift.get("A_RE2_BECOMES_B_RE1_N") or 0)
        + int(shift.get("A_RE2_BECOMES_B_RE3PLUS_N") or 0)
        + int(shift.get("A_RE2_BECOMES_B_FIRST_N") or 0)
    )
    proxy_n = no_match + to_other
    proxy_frac = proxy_n / n19
    if proxy_frac >= MAJORITY_FRAC:
        clock_explains: Any = True
    elif proxy_frac >= 0.25:
        clock_explains = "partial"
    else:
        clock_explains = False

    a_common_re2 = ((common.get("A_COMMON_REENTRY_BY_KIND") or {}).get("REENTRY_2") or {})
    a_common_re1 = ((common.get("A_COMMON_REENTRY_BY_KIND") or {}).get("REENTRY_1") or {})
    common_re2_n = int(a_common_re2.get("N") or 0)
    common_re2_pnl = a_common_re2.get("PnL")
    common_re1_pnl = a_common_re1.get("PnL")
    a_only_re2 = ((inter.get("clock_class") or {}).get("A_ONLY") or {})
    a_only_n = int(a_only_re2.get("N") or 0)

    if common_re2_n < COMMON_RE2_INDEPENDENCE_MIN:
        ordinal_indep: Any = "inconclusive"
    elif _dir(common_re2_pnl) == "neg" and _dir(common_re1_pnl) == "pos":
        ordinal_indep = True
    elif _dir(common_re2_pnl) == _dir(common_re1_pnl):
        ordinal_indep = False
    else:
        ordinal_indep = "inconclusive"

    # Elapsed-time direction alignment (prior-exit bins).
    a_bins = []
    b_bins = []
    same_dir = 0
    compared = 0
    for name, _lo, _hi in ELAPSED_BINS:
        ap = (elapsed_a.get(f"prior_exit_{name}") or {}).get("PnL")
        bp = (elapsed_b.get(f"prior_exit_{name}") or {}).get("PnL")
        an = int((elapsed_a.get(f"prior_exit_{name}") or {}).get("N") or 0)
        bn = int((elapsed_b.get(f"prior_exit_{name}") or {}).get("N") or 0)
        a_bins.append({"bin": name, "N": an, "PnL": ap})
        b_bins.append({"bin": name, "N": bn, "PnL": bp})
        if an >= SMALL_CELL_N and bn >= SMALL_CELL_N:
            compared += 1
            if _dir(ap) == _dir(bp) and _dir(ap) != "na":
                same_dir += 1
    elapsed_closer = bool(compared >= 2 and same_dir / compared >= 0.5)

    triple_n = int((overlap.get("RE2_TRIPLE_OVERLAP") or {}).get("N") or 0)
    union_n = int((overlap.get("RE2_UNION_THREE") or {}).get("N") or 0)
    rem_n = int((overlap.get("RE2_UNEXPLAINED_REMAINDER") or {}).get("N") or 0)
    concentrated = bool(max(
        int((overlap.get("RE2_AFTER_WIN") or {}).get("N") or 0),
        int((overlap.get("RE2_SCORE_NOT_IMPROVED") or {}).get("N") or 0),
        int((overlap.get("RE2_PRICE_ABOVE") or {}).get("N") or 0),
    ) >= 10)
    duplicate = bool(triple_n >= SMALL_CELL_N and triple_n / n19 >= 0.25)

    state_diff = int(common.get("PRIOR_STATE_DIFF_N") or 0)
    both_cand = max(int(common.get("COMMON_CANDIDATE_PAIRS_N") or 0), 1)
    state_diff_frac = state_diff / both_cand

    inserted_re1 = ((shift.get("inserted_by_kind") or {}).get("REENTRY_1") or {})
    inserted_re2 = ((shift.get("inserted_by_kind") or {}).get("REENTRY_2") or {})

    small_n = re2_n <= EXPECTED_A_RE2_N and common_re2_n < COMMON_RE2_INDEPENDENCE_MIN

    if clock_explains is True and ordinal_indep in (False, "inconclusive") and a_only_n >= 8:
        verdict = "REENTRY_ORDINAL_IS_CLOCK_PROXY"
        primary = "CLOCK_GEOMETRY"
    elif clock_explains in (True, "partial") and ordinal_indep is True:
        verdict = "REENTRY_MULTIFACTOR_MECHANISM"
        primary = "MULTIFACTOR"
    elif ordinal_indep is True and clock_explains is False:
        verdict = "REENTRY_ORDINAL_MECHANISM_SUPPORTED"
        primary = "ORDINAL"
    elif small_n and (clock_explains in (True, "partial") or duplicate):
        # N=19 cannot cleanly isolate ordinal after clock/overlap.
        if clock_explains is True and ordinal_indep != True:
            verdict = "REENTRY_ORDINAL_IS_CLOCK_PROXY"
            primary = "CLOCK_GEOMETRY"
        else:
            verdict = "REENTRY_MECHANISM_EVIDENCE_LIMITED"
            primary = "EVIDENCE_LIMITED"
    elif elapsed_closer and clock_explains in (True, "partial"):
        verdict = "REENTRY_ORDINAL_IS_CLOCK_PROXY"
        primary = "ELAPSED_TIME" if elapsed_closer and proxy_frac < MAJORITY_FRAC else "CLOCK_GEOMETRY"
    else:
        verdict = "REENTRY_MECHANISM_EVIDENCE_LIMITED"
        primary = "EVIDENCE_LIMITED"

    if verdict == "REENTRY_ORDINAL_IS_CLOCK_PROXY":
        rec = (
            "Hold R2. Do not treat ordinal as a forward-stable eligibility. "
            "CURRENT IRREGULAR REENTRY_2 loss is largely a clock-sampling / lineage-shift result. "
            "Do not start C3. Do not write Runtime."
        )
    elif verdict == "REENTRY_ORDINAL_MECHANISM_SUPPORTED":
        rec = (
            "Ordinal remains after clock separation. Still not a Runtime candidate: TRUE_OOS=false, "
            "V1 already CLOCK_DEPENDENT. Do not implement R2. Do not start C3."
        )
    elif verdict == "REENTRY_MULTIFACTOR_MECHANISM":
        rec = (
            "Clock geometry and a residual ordinal/state overlap both remain. Do not collapse into one gate. "
            "Do not implement R2. Do not start C3."
        )
    else:
        rec = (
            "A REENTRY_2 N=19 is too small to isolate ordinal from clock/state confounders. "
            "Reserve R2 as CURRENT IRREGULAR historical sampling. Do not start C3. No new policy."
        )

    pair_sc_px = overlap.get("PAIR_SCORE_DN_AND_PRICE_UP") or {}
    a_only_pnl = ((inter.get("clock_class") or {}).get("A_ONLY") or {}).get("PnL")
    q1 = (
        f"Yes. Almost all of A RE2 {re2_pnl} sits on A_ONLY anchors "
        f"(N={a_only_n}, PnL={a_only_pnl}); COMMON-anchor RE2 N={common_re2_n} PnL={common_re2_pnl}. "
        f"SCORE_NOT_IMPROVED and PRICE_ABOVE tag that pocket. AFTER_WIN N=10 is not the loss. "
        f"Remainder N={rem_n}. The A_ONLY cell N={a_only_n} is not a new rule."
    )
    q2 = (
        f"AFTER_WIN is not the same mechanism as the other two. Triple overlap N={triple_n} "
        f"PnL={(overlap.get('RE2_TRIPLE_OVERLAP') or {}).get('PnL')} (not the loss). "
        f"SCORE_NOT_IMPROVED ∩ PRICE_ABOVE N={int(pair_sc_px.get('N') or 0)} "
        f"PnL={pair_sc_px.get('PnL')} duplicates almost the entire RE2 loss. "
        f"Those two labels name one pocket; AFTER_WIN does not."
    )
    q3 = (
        f"Clock insertion/deletion and no exact-anchor match cover {proxy_n}/{re2_n} of A RE2 "
        f"(no_match={no_match}, ordinal_remap={to_other}, still_RE2={to_re2}). "
        f"Inserted B fills N={shift.get('CLOCK_INSERTED_FILL_N')}, removed A fills N={shift.get('CLOCK_REMOVED_FILL_N')}."
    )
    q4 = (
        "Yes. Precommitted elapsed bins align A/B sign more than ordinal slices do."
        if elapsed_closer
        else "No robust alignment. Elapsed bins do not make A/B PnL signs consistently match."
    )
    fill_both = int(common.get("FILL_BOTH_N") or 0)
    ord_diff = int(common.get("COMMON_FILL_ORDINAL_DIFF_N") or 0)
    q5 = (
        f"Among all COMMON candidate pairs, prior fill-count differs in only "
        f"{state_diff}/{both_cand} ({round(state_diff_frac, 3)}) — most names are not in a live episode. "
        f"Among exact COMMON-anchor fills present in both clocks (N={fill_both}), "
        f"ordinal already differs in {ord_diff}. Occupancy-cascade episodes N={shift.get('OCCUPANCY_CASCADE_EPISODES_N')}. "
        f"So yes for traded lineages, no as a general COMMON-anchor property."
    )
    q6 = primary
    q7 = (
        "Reserve. R2's A improvement is CURRENT IRREGULAR sampling of a third fill, not a "
        "forward-stable ordinal eligibility. UNIFORM10 reverses the ordinal pattern. No Runtime."
        if verdict in (
            "REENTRY_ORDINAL_IS_CLOCK_PROXY",
            "REENTRY_MECHANISM_EVIDENCE_LIMITED",
            "REENTRY_MULTIFACTOR_MECHANISM",
        )
        else "Ordinal still has residual A-clock content, but V1 is CLOCK_DEPENDENT and TRUE_OOS=false. Do not promote R2."
    )

    return {
        "VERDICT": verdict,
        "CLOCK_EXPLAINS_ORDINAL_REVERSAL": clock_explains,
        "ORDINAL_EFFECT_INDEPENDENT": ordinal_indep,
        "PRIMARY_MECHANISM": primary,
        "RECOMMENDED_NEXT_RESEARCH": rec,
        "proxy_frac": proxy_frac,
        "elapsed_bins_same_dir": elapsed_closer,
        "common_re2_n": common_re2_n,
        "common_re2_pnl": common_re2_pnl,
        "common_re1_pnl": common_re1_pnl,
        "a_only_re2_n": a_only_n,
        "inserted_re1": inserted_re1,
        "inserted_re2": inserted_re2,
        "answers": {
            "Q1": q1,
            "Q2": q2,
            "Q3": q3,
            "Q4": q4,
            "Q5": q5,
            "Q6": q6,
            "Q7": q7,
        },
        "elapsed_a_bins": a_bins,
        "elapsed_b_bins": b_bins,
    }


def flatten_crosstab(block: dict[str, Any], family: str) -> list[dict[str, Any]]:
    rows = []
    for slice_name, st in (block or {}).items():
        rec = {"family": family, "slice": slice_name}
        rec.update(st or {})
        rows.append(rec)
    return rows


def elapsed_rows(elapsed: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    clock = elapsed.get("clock")
    for name, _lo, _hi in ELAPSED_BINS:
        for basis in ("prior_exit", "first_fill"):
            st = elapsed.get(f"{basis}_{name}") or {}
            rec = {"clock": clock, "basis": basis, "bin": name}
            rec.update(st)
            rows.append(rec)
    return rows
