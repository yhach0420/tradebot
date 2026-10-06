"""R14 incrementality RCA. Frozen nested rules only. No PnL. No feature search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

import numpy as np

from research.r14_trend_incrementality_rca_v1 import (
    BOOT_N,
    BOOT_SEED,
    CASE_INCONCLUSIVE,
    CASE_INVALID,
    CASE_NOT_INC,
    CASE_R14,
    CASE_T15,
    EVAL_BLOCKS,
    LOGISTIC_C,
    NEXT_R14,
    NEXT_STOP,
    NEXT_T15,
    PRIOR5_RANGE_THR,
    PRIOR5_RET_THR,
    R15_THR,
    TRAIN_BLOCK,
)
from research.r14_trend_incrementality_rca_v1.match import NUISANCE, balance, find_control, index_fail
from research.r14_trend_incrementality_rca_v1.rules import FEATURE_SEMANTICS, RULES, hit_r14, hit_t15
from research.r14_trend_incrementality_rca_v1.walk import walk_events


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.mean(xs))


def _med(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.median(xs))


def _rate(rows: list[dict[str, Any]], key: str = "y_p40") -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([float(x) for x in xs]))


def _block(rows: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("block") or "") == block]


def summarize(rows: list[dict[str, Any]], base: list[dict[str, Any]]) -> dict[str, Any]:
    p40 = _rate(rows, "y_p40")
    b40 = _rate(base, "y_p40")
    return {
        "event_n": len(rows),
        "symbol_n": len({str(r.get("symbol") or "") for r in rows}),
        "day_n": len({str(r.get("date") or "") for r in rows}),
        "p40_before_m20": p40,
        "incremental_gap": None if p40 is None or b40 is None else float(p40) - float(b40),
        "p20_before_m20": _rate(rows, "y_p20"),
        "p80_before_m30": _rate(rows, "y_p80"),
        "median_mfe": _med(rows, "mfe"),
        "median_mae": _med(rows, "mae"),
        "ret_5m": _mean(rows, "ret5"),
        "ret_10m": _mean(rows, "ret10"),
        "ret_20m": _mean(rows, "ret20"),
    }


def by_blocks(events: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for b in (TRAIN_BLOCK, *EVAL_BLOCKS):
        base = _block(events, b)
        hit = [r for r in base if pred(r)]
        out[b] = summarize(hit, base)
    return out


def contrast(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def gap(key: str) -> float | None:
        av, bv = a.get(key), b.get(key)
        if av is None or bv is None:
            return None
        return float(av) - float(bv)

    return {
        "dn": (a.get("event_n") or 0) - (b.get("event_n") or 0),
        "dp40": gap("p40_before_m20"),
        "d_incremental_vs_same_base": gap("incremental_gap"),
        "dp20": gap("p20_before_m20"),
        "dp80": gap("p80_before_m30"),
        "dmfe": gap("median_mfe"),
        "dmae": gap("median_mae"),
        "d5": gap("ret_5m"),
        "d10": gap("ret_10m"),
        "d20": gap("ret_20m"),
        "positive_p40": bool(gap("p40_before_m20") is not None and float(gap("p40_before_m20")) > 0),
    }


def nested_contrasts(nested: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pairs = (("T15", "BASE"), ("T15_P5", "T15"), ("R14_FULL", "T15_P5"), ("R14_FULL", "T15"))
    for left, right in pairs:
        key = f"{left}_vs_{right}"
        per = {}
        pos = []
        for b in EVAL_BLOCKS:
            c = contrast((nested.get(left) or {}).get(b) or {}, (nested.get(right) or {}).get(b) or {})
            per[b] = c
            if c.get("positive_p40"):
                pos.append(b)
        out[key] = {"blocks": per, "positive_eval_blocks": pos, "all_eval_positive": len(pos) == 3}
    return out


def side_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side in ("BULLISH", "BEARISH"):
        xs = [r for r in events if str(r.get("direction") or "") == side]
        out[side] = by_blocks(xs, hit_r14)
        out[side]["base_n"] = {b: len(_block(xs, b)) for b in (TRAIN_BLOCK, *EVAL_BLOCKS)}
        out[side]["r14_share"] = {
            b: (None if not _block(xs, b) else len([r for r in _block(xs, b) if hit_r14(r)]) / len(_block(xs, b)))
            for b in (TRAIN_BLOCK, *EVAL_BLOCKS)
        }
    bull_eval = [r for r in events if r.get("direction") == "BULLISH" and str(r.get("block")) in EVAL_BLOCKS]
    bear_eval = [r for r in events if r.get("direction") == "BEARISH" and str(r.get("block")) in EVAL_BLOCKS]
    out["eval_r15_mean_bull_r14"] = _mean([r for r in bull_eval if hit_r14(r)], "r15")
    out["eval_r15_mean_bear_r14"] = _mean([r for r in bear_eval if hit_r14(r)], "r15")
    out["R15_DIRECTION_NORMALIZED"] = False
    out["PRIOR5_RET_DIRECTION_NORMALIZED"] = False
    out["R14_DIRECTION_SEMANTICS_INVALID"] = True
    out["reason"] = (
        "r15 > 0.0040059178 is an unsigned up-move over 15 clock minutes. "
        "For BEARISH displacement the economically aligned trend is negative r15. "
        "The frozen split therefore has opposite meaning by side."
    )
    return out


def matched_pack(events: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool], *, name: str) -> dict[str, Any]:
    eval_rows = [r for r in events if str(r.get("block") or "") in EVAL_BLOCKS]
    treated = [r for r in eval_rows if pred(r)]
    fail = [r for r in eval_rows if not pred(r)]
    by = index_fail(fail)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in treated:
        ctrl = find_control(row, by)
        if ctrl is not None:
            pairs.append((row, ctrl))
    tr = [a for a, _ in pairs]
    ct = [b for _, b in pairs]
    gaps_block: dict[str, Any] = {}
    for b in EVAL_BLOCKS:
        pb = [(a, c) for a, c in pairs if str(a.get("block") or "") == b]
        if not pb:
            gaps_block[b] = {"matched_n": 0, "gap_p40": None}
            continue
        ta, ca = [x[0] for x in pb], [x[1] for x in pb]
        gaps_block[b] = {
            "matched_n": len(pb),
            "treatment_p40": _rate(ta, "y_p40"),
            "control_p40": _rate(ca, "y_p40"),
            "gap_p40": None if _rate(ta, "y_p40") is None or _rate(ca, "y_p40") is None else float(_rate(ta, "y_p40")) - float(_rate(ca, "y_p40")),
            "gap_mfe": None if _med(ta, "mfe") is None or _med(ca, "mfe") is None else float(_med(ta, "mfe")) - float(_med(ca, "mfe")),
            "gap_mae": None if _med(ta, "mae") is None or _med(ca, "mae") is None else float(_med(ta, "mae")) - float(_med(ca, "mae")),
            "gap_5m": None if _mean(ta, "ret5") is None or _mean(ca, "ret5") is None else float(_mean(ta, "ret5")) - float(_mean(ca, "ret5")),
            "gap_10m": None if _mean(ta, "ret10") is None or _mean(ca, "ret10") is None else float(_mean(ta, "ret10")) - float(_mean(ca, "ret10")),
            "gap_20m": None if _mean(ta, "ret20") is None or _mean(ca, "ret20") is None else float(_mean(ta, "ret20")) - float(_mean(ca, "ret20")),
        }
    pos = [b for b in EVAL_BLOCKS if (gaps_block.get(b) or {}).get("gap_p40") is not None and float(gaps_block[b]["gap_p40"]) > 0]
    tp40, cp40 = _rate(tr, "y_p40"), _rate(ct, "y_p40")
    return {
        "name": name,
        "treated_n": len(treated),
        "fail_n": len(fail),
        "matched_n": len(pairs),
        "match_rate": (len(pairs) / len(treated)) if treated else None,
        "treatment_p40": tp40,
        "control_p40": cp40,
        "gap_p40": None if tp40 is None or cp40 is None else float(tp40) - float(cp40),
        "gap_mfe": None if _med(tr, "mfe") is None or _med(ct, "mfe") is None else float(_med(tr, "mfe")) - float(_med(ct, "mfe")),
        "gap_mae": None if _med(tr, "mae") is None or _med(ct, "mae") is None else float(_med(tr, "mae")) - float(_med(ct, "mae")),
        "gap_5m": None if _mean(tr, "ret5") is None or _mean(ct, "ret5") is None else float(_mean(tr, "ret5")) - float(_mean(ct, "ret5")),
        "gap_10m": None if _mean(tr, "ret10") is None or _mean(ct, "ret10") is None else float(_mean(tr, "ret10")) - float(_mean(ct, "ret10")),
        "gap_20m": None if _mean(tr, "ret20") is None or _mean(ct, "ret20") is None else float(_mean(tr, "ret20")) - float(_mean(ct, "ret20")),
        "blocks": gaps_block,
        "positive_eval_blocks": pos,
        "stable_d2_d3_d4": len(pos) == 3,
        "beats_matched": bool(tp40 is not None and cp40 is not None and float(tp40) - float(cp40) > 0),
        "balance": balance(treated, pairs),
        "future_outcomes_not_used": True,
        "r14_features_not_used": True,
    }


def _mat(rows: list[dict[str, Any]], names: tuple[str, ...], med: dict[str, float] | None = None) -> tuple[np.ndarray, dict[str, float]]:
    meds = dict(med or {})
    if not meds:
        for k in names:
            xs = [float(r[k]) for r in rows if _finite(r.get(k))]
            meds[k] = float(np.median(xs)) if xs else 0.0
    X = np.zeros((len(rows), len(names)), dtype=float)
    for i, r in enumerate(rows):
        for j, k in enumerate(names):
            v = r.get(k)
            X[i, j] = float(v) if _finite(v) else meds[k]
    return X, meds


def overlap_weighted(eval_rows: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool], *, name: str) -> dict[str, Any]:
    rows = list(eval_rows)
    y = np.asarray([1 if pred(r) else 0 for r in rows], dtype=int)
    if int(y.sum()) < 40 or int((1 - y).sum()) < 40:
        return {"ok": False, "reason": "n", "name": name}
    names = NUISANCE
    ehat_acc: list[list[float]] = [[] for _ in rows]
    for hold in EVAL_BLOCKS:
        fit_idx = [i for i, r in enumerate(rows) if str(r.get("block") or "") != hold]
        score_idx = [i for i, r in enumerate(rows) if str(r.get("block") or "") == hold]
        if len(fit_idx) < 80 or not score_idx:
            continue
        fit_rows = [rows[i] for i in fit_idx]
        Xf, med = _mat(fit_rows, names)
        yf = y[np.asarray(fit_idx, dtype=int)]
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
        except Exception as exc:
            return {"ok": False, "reason": f"sklearn:{exc}", "name": name}
        scaler = StandardScaler()
        Xs = scaler.fit_transform(Xf)
        clf = LogisticRegression(C=LOGISTIC_C, solver="lbfgs", max_iter=2000, penalty="l2")
        clf.fit(Xs, yf)
        Xe, _ = _mat([rows[i] for i in score_idx], names, med)
        pred_p = clf.predict_proba(scaler.transform(Xe))[:, 1]
        for k, i in enumerate(score_idx):
            ehat_acc[i].append(float(pred_p[k]))
    ehat = np.asarray([float(np.mean(v)) if v else 0.5 for v in ehat_acc], dtype=float)
    ehat = np.clip(ehat, 0.01, 0.99)
    w = np.where(y == 1, 1.0 - ehat, ehat)
    yt = np.asarray([float(r["y_p40"]) for r in rows], dtype=float)
    wt, wc = w * (y == 1), w * (y == 0)
    if float(wt.sum()) <= 0 or float(wc.sum()) <= 0:
        return {"ok": False, "reason": "weights", "name": name}
    mu_t = float(np.sum(wt * yt) / wt.sum())
    mu_c = float(np.sum(wc * yt) / wc.sum())
    ess = float((w.sum() ** 2) / np.sum(w ** 2)) if np.sum(w ** 2) > 0 else 0.0
    block_gap = {}
    for b in EVAL_BLOCKS:
        idx = np.asarray([i for i, r in enumerate(rows) if str(r.get("block") or "") == b], dtype=int)
        if idx.size < 20:
            block_gap[b] = None
            continue
        wtt = w[idx] * (y[idx] == 1)
        wcc = w[idx] * (y[idx] == 0)
        if float(wtt.sum()) <= 0 or float(wcc.sum()) <= 0:
            block_gap[b] = None
            continue
        block_gap[b] = float(np.sum(wtt * yt[idx]) / wtt.sum() - np.sum(wcc * yt[idx]) / wcc.sum())
    return {
        "ok": True,
        "name": name,
        "model": "l2_logistic",
        "C": LOGISTIC_C,
        "outcome_used_to_fit_propensity": False,
        "features": list(names),
        "cross_fit": "leave_one_eval_block_out",
        "n_treated": int(y.sum()),
        "n_control": int((1 - y).sum()),
        "effective_sample_size": ess,
        "max_weight": float(np.max(w)),
        "median_weight": float(np.median(w)),
        "p99_weight": float(np.percentile(w, 99)),
        "weight_concentration": bool(float(np.max(w)) > 20.0 * float(np.median(w))),
        "treated_p40": mu_t,
        "control_p40": mu_c,
        "gap_p40": mu_t - mu_c,
        "block_gap_p40": block_gap,
        "xgboost": False,
        "random_forest": False,
        "neural_net": False,
    }


def dose_response(events: list[dict[str, Any]]) -> dict[str, Any]:
    d1 = [r for r in events if str(r.get("block") or "") == TRAIN_BLOCK and _finite(r.get("r15"))]
    xs = np.asarray([float(r["r15"]) for r in d1], dtype=float)
    if xs.size < 50:
        return {"ok": False, "reason": "d1_n"}
    edges = np.quantile(xs, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    edges[0] = min(edges[0], -1e9)
    edges[-1] = max(edges[-1], 1e9)
    labels = ("Q1", "Q2", "Q3", "Q4", "Q5")

    def bucket(v: float) -> str:
        for i in range(5):
            lo, hi = float(edges[i]), float(edges[i + 1])
            if i == 4:
                if v >= lo:
                    return labels[i]
            elif v >= lo and v < hi:
                return labels[i]
        return labels[-1]

    per_block: dict[str, Any] = {}
    p40_seq_all = []
    for b in EVAL_BLOCKS:
        rows = [r for r in _block(events, b) if _finite(r.get("r15"))]
        cells = {}
        seq = []
        for lab in labels:
            hit = [r for r in rows if bucket(float(r["r15"])) == lab]
            cells[lab] = summarize(hit, rows)
            seq.append(cells[lab].get("p40_before_m20"))
        per_block[b] = {"cells": cells, "p40_sequence": seq}
        p40_seq_all.append(seq)
    pooled = [r for r in events if str(r.get("block") or "") in EVAL_BLOCKS and _finite(r.get("r15"))]
    pooled_cells = {}
    pooled_seq = []
    for lab in labels:
        hit = [r for r in pooled if bucket(float(r["r15"])) == lab]
        pooled_cells[lab] = summarize(hit, pooled)
        pooled_seq.append(pooled_cells[lab].get("p40_before_m20"))

    def mono(seq: list[Any]) -> bool:
        vals = [float(v) for v in seq if v is not None]
        if len(vals) < 5:
            return False
        return all(vals[i] <= vals[i + 1] + 1e-12 for i in range(len(vals) - 1))

    return {
        "ok": True,
        "quantile_source": "D1_r15",
        "edges": [float(x) for x in edges],
        "threshold_not_selected_from_quintiles": True,
        "blocks": per_block,
        "d2_d4_pooled": {"cells": pooled_cells, "p40_sequence": pooled_seq},
        "monotonic_pooled": mono(pooled_seq),
        "monotonic_each_eval_block": all(mono((per_block[b] or {}).get("p40_sequence") or []) for b in EVAL_BLOCKS),
    }


def threshold_locality(events: list[dict[str, Any]]) -> dict[str, Any]:
    lo_a, hi_a = 0.75 * float(R15_THR), 1.00 * float(R15_THR)
    lo_b, hi_b = 1.00 * float(R15_THR), 1.25 * float(R15_THR)
    out: dict[str, Any] = {"below_band": [lo_a, hi_a], "above_band": [lo_b, hi_b], "blocks": {}}
    pos = []
    for b in EVAL_BLOCKS:
        rows = [r for r in _block(events, b) if _finite(r.get("r15"))]
        below = [r for r in rows if lo_a <= float(r["r15"]) < hi_a]
        above = [r for r in rows if lo_b < float(r["r15"]) <= hi_b]
        pb, pa = _rate(below, "y_p40"), _rate(above, "y_p40")
        gap = None if pb is None or pa is None else float(pa) - float(pb)
        rec = {
            "below_n": len(below),
            "above_n": len(above),
            "below_p40": pb,
            "above_p40": pa,
            "gap_above_minus_below": gap,
            "below_mfe": _med(below, "mfe"),
            "above_mfe": _med(above, "mfe"),
        }
        out["blocks"][b] = rec
        if gap is not None and gap > 0:
            pos.append(b)
    out["positive_eval_blocks"] = pos
    out["looks_like_state_transition"] = False
    out["looks_like_smooth_continuation"] = True
    out["note"] = (
        "Fixed 0.75x-1.00x vs 1.00x-1.25x bands around the D1 split. "
        "A state transition would require a sharp, replicated jump; otherwise the relation is treated as smooth."
    )
    return out


def d1_stability(events: list[dict[str, Any]], parent_stab: dict[str, Any] | None) -> dict[str, Any]:
    d1 = _block(events, TRAIN_BLOCK)
    dates = sorted({str(r.get("date") or "") for r in d1 if r.get("date")})
    cuts = [0, len(dates) // 3, 2 * len(dates) // 3, len(dates)]
    folds = []
    pos = 0
    for k in range(3):
        keep = set(dates[cuts[k] : cuts[k + 1]])
        rows = [r for r in d1 if str(r.get("date") or "") in keep]
        base = summarize(rows, rows)
        hit = summarize([r for r in rows if hit_t15(r)], rows)
        gap = hit.get("incremental_gap")
        folds.append(
            {
                "fold": k + 1,
                "date_first": dates[cuts[k]] if keep else None,
                "date_last": dates[cuts[k + 1] - 1] if keep else None,
                "n": len(rows),
                "t15_n": hit.get("event_n"),
                "t15_p40": hit.get("p40_before_m20"),
                "base_p40": base.get("p40_before_m20"),
                "t15_gap": gap,
                "positive": bool(gap is not None and float(gap) > 0),
            }
        )
        if gap is not None and float(gap) > 0:
            pos += 1
    if pos == 3:
        cls = "D1_STRUCTURE_STABLE"
    elif pos == 2:
        cls = "D1_PARTIAL_FEATURE_STABILITY"
    else:
        cls = "D1_STRUCTURE_UNSTABLE"
    inter = list((parent_stab or {}).get("feature_intersection") or [])
    return {
        "parent_feature_intersection": inter,
        "parent_feature_intersection_empty": len(inter) == 0,
        "parent_unstable_flag_was": (parent_stab or {}).get("unstable"),
        "qualification": "empty feature_intersection across D1 tertile trees; topology is not retained as stable",
        "t15_folds": folds,
        "positive_fold_n": pos,
        "classification": cls,
        "core_test": "T15 gap vs BASE inside each chronological D1 tertile; identical tree topology not required",
    }


def decide(
    *,
    invalid: bool,
    nested: dict[str, Any],
    matched_t15: dict[str, Any],
    matched_r14: dict[str, Any],
) -> dict[str, Any]:
    if invalid:
        return {
            "VERDICT": CASE_INVALID,
            "NEXT": NEXT_STOP,
            "advance": False,
            "reason": "unsigned r15 split has opposite economic meaning for bearish displacement events",
        }
    r14_adds = bool(((nested.get("R14_FULL_vs_T15") or {}).get("all_eval_positive")))
    r14_beats = bool(matched_r14.get("beats_matched")) and bool(matched_r14.get("stable_d2_d3_d4"))
    t15_beats = bool(matched_t15.get("beats_matched")) and bool(matched_t15.get("stable_d2_d3_d4"))
    t15_uncond = bool(all(
        ((((nested.get("T15_vs_BASE") or {}).get("blocks") or {}).get(b) or {}).get("positive_p40"))
        for b in EVAL_BLOCKS
    ))
    if r14_beats and r14_adds:
        verd, nxt, adv = CASE_R14, NEXT_R14, True
    elif t15_beats:
        verd, nxt, adv = CASE_T15, NEXT_T15, True
    elif t15_uncond or (matched_t15.get("gap_p40") is not None):
        verd, nxt, adv = CASE_NOT_INC, NEXT_STOP, False
    else:
        verd, nxt, adv = CASE_INCONCLUSIVE, NEXT_STOP, False
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "advance": adv,
        "r14_full_beats_matched": r14_beats,
        "r14_adds_beyond_t15": r14_adds,
        "t15_matched_stable_d2_d3_d4": t15_beats,
        "drop_dead_predicates": bool(verd == CASE_T15),
        "complete_strategy_not_run": True,
        "threshold_retuned": False,
        "new_feature_added": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "current_judgment_of_parent": "NATIVE_CONTEXT_STACK_PARTIAL_MECHANISM_V1",
    }


def build_report_body(bind: dict[str, Any], parent_report: dict[str, Any]) -> dict[str, Any]:
    walked = walk_events(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_INCONCLUSIVE, "NEXT": NEXT_STOP}}
    events = list(walked.get("events") or [])
    nested = {name: by_blocks(events, pred) for name, pred in RULES.items()}
    contrasts = nested_contrasts(nested)
    sides = side_audit(events)
    invalid = bool(sides.get("R14_DIRECTION_SEMANTICS_INVALID"))
    print("MATCH T15", flush=True)
    matched_t15 = matched_pack(events, hit_t15, name="T15")
    print("MATCH R14", flush=True)
    matched_r14 = matched_pack(events, hit_r14, name="R14_FULL")
    eval_rows = [r for r in events if str(r.get("block") or "") in EVAL_BLOCKS]
    print("PROPENSITY", flush=True)
    ow_t15 = overlap_weighted(eval_rows, hit_t15, name="T15")
    ow_r14 = overlap_weighted(eval_rows, hit_r14, name="R14_FULL")
    dose = dose_response(events)
    local = threshold_locality(events)
    if local.get("blocks"):
        jumps = [
            abs(float((local["blocks"][b] or {}).get("gap_above_minus_below") or 0))
            for b in EVAL_BLOCKS
            if (local["blocks"][b] or {}).get("gap_above_minus_below") is not None
        ]
        t15_gaps = [
            abs(float((((nested.get("T15") or {}).get(b) or {}).get("incremental_gap") or 0)))
            for b in EVAL_BLOCKS
        ]
        sharp = bool(jumps) and bool(t15_gaps) and (float(np.mean(jumps)) >= 2.0 * max(float(np.mean(t15_gaps)), 1e-6))
        local["looks_like_state_transition"] = bool(sharp and len(local.get("positive_eval_blocks") or []) == 3)
        local["looks_like_smooth_continuation"] = not local["looks_like_state_transition"]
    stab = d1_stability(events, dict(parent_report.get("d1_internal_stability") or {}))
    decision = decide(invalid=invalid, nested=contrasts, matched_t15=matched_t15, matched_r14=matched_r14)
    prior5_adds = bool((contrasts.get("T15_P5_vs_T15") or {}).get("all_eval_positive"))
    range_adds = bool((contrasts.get("R14_FULL_vs_T15_P5") or {}).get("all_eval_positive"))
    full_adds = bool((contrasts.get("R14_FULL_vs_T15") or {}).get("all_eval_positive"))
    counts = dict(walked.get("counts") or {})
    return {
        "ok": True,
        "identity": {
            "native_event_n": int(counts.get("native_event_n") or 0),
            "kept_event_n": len(events),
            "d1_n": int(counts.get("D1_n") or 0),
            "d2_n": int(counts.get("D2_n") or 0),
            "d3_n": int(counts.get("D3_n") or 0),
            "d4_n": int(counts.get("D4_n") or 0),
        },
        "counts": counts,
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "feature_semantics": FEATURE_SEMANTICS,
        "R15_DIRECTION_NORMALIZED": False,
        "PRIOR5_RET_DIRECTION_NORMALIZED": False,
        "R14_DIRECTION_SEMANTICS_INVALID": invalid,
        "direction_audit": sides,
        "nested": nested,
        "nested_contrasts": contrasts,
        "prior5_ret_adds_beyond_t15": prior5_adds,
        "prior5_range_adds_beyond_t15_p5": range_adds,
        "full_adds_beyond_t15": full_adds,
        "matched_t15": matched_t15,
        "matched_r14": matched_r14,
        "overlap_t15": ow_t15,
        "overlap_r14": ow_r14,
        "r15_dose_response": dose,
        "threshold_locality": local,
        "d1_stability": stab,
        "frozen_thresholds": {"r15": R15_THR, "prior5_ret": PRIOR5_RET_THR, "prior5_range_rel": PRIOR5_RANGE_THR},
        "decision": decision,
        "new_feature_added": False,
        "threshold_retuned": False,
        "pnl_optimization": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    nested = dict(report.get("nested") or {})
    sides = dict(report.get("direction_audit") or {})

    def side_pack(side: str) -> dict[str, Any]:
        s = dict(sides.get(side) or {})
        return {
            b: {
                "event_n": (s.get(b) or {}).get("event_n"),
                "p40": (s.get(b) or {}).get("p40_before_m20"),
                "gap": (s.get(b) or {}).get("incremental_gap"),
            }
            for b in ("D1", "D2", "D3", "D4")
        }
    loc = dict(report.get("threshold_locality") or {})
    dose = dict(report.get("r15_dose_response") or {})
    t15 = nested.get("T15") or {}
    r14 = nested.get("R14_FULL") or {}
    return {
        "Is r15 direction-normalized?": False,
        "Is prior5_ret direction-normalized?": False,
        "Bullish R14 effect?": side_pack("BULLISH"),
        "Bearish R14 effect?": side_pack("BEARISH"),
        "T15 D2 gap?": ((t15.get("D2") or {}).get("incremental_gap")),
        "T15 D3 gap?": ((t15.get("D3") or {}).get("incremental_gap")),
        "T15 D4 gap?": ((t15.get("D4") or {}).get("incremental_gap")),
        "R14_FULL D2 gap?": ((r14.get("D2") or {}).get("incremental_gap")),
        "R14_FULL D3 gap?": ((r14.get("D3") or {}).get("incremental_gap")),
        "R14_FULL D4 gap?": ((r14.get("D4") or {}).get("incremental_gap")),
        "Does prior5_ret add beyond T15?": report.get("prior5_ret_adds_beyond_t15"),
        "Does prior5_range_rel add beyond T15_P5?": report.get("prior5_range_adds_beyond_t15_p5"),
        "Does FULL add beyond T15?": report.get("full_adds_beyond_t15"),
        "Matched T15 p40 gap?": (report.get("matched_t15") or {}).get("gap_p40"),
        "Matched R14 p40 gap?": (report.get("matched_r14") or {}).get("gap_p40"),
        "Overlap-weighted T15 gap?": (report.get("overlap_t15") or {}).get("gap_p40"),
        "Overlap-weighted R14 gap?": (report.get("overlap_r14") or {}).get("gap_p40"),
        "Is r15 dose-response monotonic?": dose.get("monotonic_pooled"),
        "Does the exact D1 threshold look like a state transition or smooth continuation?": (
            "state_transition" if loc.get("looks_like_state_transition") else "smooth_continuation"
        ),
        "D1 stability classification?": (report.get("d1_stability") or {}).get("classification"),
        "Any new feature added?": False,
        "Any threshold retuned?": False,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
    }
