"""P4-3 continuation-value aggregates. No threshold search / no best-checkpoint pick."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.mid_hold_continuation_value_p4_3 import (
    CLASS_DATA,
    CLASS_MIXED,
    CLASS_NEG,
    CLASS_NOT_ACTIONABLE,
    CLASS_NOT_HARM,
    FALSE_RECOVERY_KNOWN_ID,
    FLAT_YEN,
    FULL14,
    P4_1_WINNER_IDS,
    P4_2_ADVERSE_DRAW,
    P4_2_ADVERSE_LOSS,
    P4_2_ADVERSE_N,
    P4_2_ADVERSE_WIN,
    PRIMARY_CHECKPOINTS,
    STATE_NON_RECOVERING,
    STATE_RECOVERING,
)


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _sign_flat(cv: float) -> str:
    if cv > FLAT_YEN:
        return "helped"
    if cv < -FLAT_YEN:
        return "hurt"
    return "flat"


def yen_stats(xs: list[Any]) -> dict[str, Any]:
    vals = [_f(x) for x in xs]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}
    arr = np.asarray(vals, dtype=float)
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
    }


def slice_days(rows: list[dict[str, Any]], days) -> list[dict[str, Any]]:
    want = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date")) in want]


def evaluable(rows: list[dict[str, Any]], *, h: int, state: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if int(r.get("horizon_sec") or 0) != int(h):
            continue
        if r.get("evaluable") is not True:
            continue
        if r.get("cohort_B_adverse120") is not True:
            continue
        if state is not None and r.get("state") != state:
            continue
        if r.get("continuation_value_yen_100") is None:
            continue
        out.append(r)
    return out


def adverse_ids(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("cohort_B_adverse120") is not True:
            continue
        tid = str(r.get("trade_id") or "")
        if tid and tid not in by:
            by[tid] = r
    return by


def cohort_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = adverse_ids(rows)
    n = len(by)
    w = sum(1 for r in by.values() if r.get("FINAL_WIN"))
    l = sum(1 for r in by.values() if r.get("FINAL_LOSS"))
    d = sum(1 for r in by.values() if r.get("FINAL_DRAW"))
    return {
        "n": n,
        "WIN": w,
        "LOSS": l,
        "DRAW": d,
        "match_p4_2": n == P4_2_ADVERSE_N and w == P4_2_ADVERSE_WIN and l == P4_2_ADVERSE_LOSS and d == P4_2_ADVERSE_DRAW,
    }


def block_for(rows: list[dict[str, Any]], *, h: int, state: str) -> dict[str, Any]:
    rs = evaluable(rows, h=h, state=state)
    cvs = [_f(r.get("continuation_value_yen_100")) for r in rs]
    cvs = [v for v in cvs if v is not None]
    exits = [_f(r.get("checkpoint_exit_pnl_yen_100")) for r in rs]
    cans = [_f(r.get("canonical_final_pnl_yen_100")) for r in rs]
    helped = sum(1 for v in cvs if _sign_flat(v) == "helped")
    hurt = sum(1 for v in cvs if _sign_flat(v) == "hurt")
    flat = sum(1 for v in cvs if _sign_flat(v) == "flat")
    n = len(cvs)
    win_n = sum(1 for r in rs if r.get("FINAL_WIN"))
    loss_n = sum(1 for r in rs if r.get("FINAL_LOSS"))
    draw_n = sum(1 for r in rs if r.get("FINAL_DRAW"))
    wait_better_win = sum(1 for r in rs if r.get("FINAL_WIN") and _sign_flat(float(r["continuation_value_yen_100"])) == "helped")
    exit_better_win = sum(1 for r in rs if r.get("FINAL_WIN") and _sign_flat(float(r["continuation_value_yen_100"])) == "hurt")
    wait_better_loss = sum(1 for r in rs if r.get("FINAL_LOSS") and _sign_flat(float(r["continuation_value_yen_100"])) == "helped")
    exit_better_loss = sum(1 for r in rs if r.get("FINAL_LOSS") and _sign_flat(float(r["continuation_value_yen_100"])) == "hurt")
    wait_better_draw = sum(1 for r in rs if r.get("FINAL_DRAW") and _sign_flat(float(r["continuation_value_yen_100"])) == "helped")
    exit_better_draw = sum(1 for r in rs if r.get("FINAL_DRAW") and _sign_flat(float(r["continuation_value_yen_100"])) == "hurt")
    return {
        "horizon_sec": int(h),
        "state": state,
        "n": n,
        "checkpoint_exit_pnl": yen_stats(exits),
        "canonical_final_pnl": yen_stats(cans),
        "continuation_value": yen_stats(cvs),
        "continuation_helped_n": helped,
        "continuation_hurt_n": hurt,
        "continuation_flat_n": flat,
        "positive_rate": (helped / n) if n else None,
        "negative_rate": (hurt / n) if n else None,
        "FINAL_WIN_n": win_n,
        "FINAL_LOSS_n": loss_n,
        "FINAL_DRAW_n": draw_n,
        "WIN_wait_better_n": wait_better_win,
        "WIN_exit_now_better_n": exit_better_win,
        "LOSS_wait_better_n": wait_better_loss,
        "LOSS_exit_now_better_n": exit_better_loss,
        "DRAW_wait_better_n": wait_better_draw,
        "DRAW_exit_now_better_n": exit_better_draw,
    }


def strat_600(rows: list[dict[str, Any]], *, h: int, state: str, extend: bool) -> dict[str, Any]:
    rs = [
        r
        for r in evaluable(rows, h=h, state=state)
        if bool(r.get("EXTEND_TO_750")) is bool(extend)
    ]
    cvs = [_f(r.get("continuation_value_yen_100")) for r in rs]
    cvs = [v for v in cvs if v is not None]
    n = len(cvs)
    helped = sum(1 for v in cvs if _sign_flat(v) == "helped")
    return {
        "horizon_sec": int(h),
        "state": state,
        "label": "EXTEND_TO_750" if extend else "EXIT_AT_600",
        "n": n,
        "continuation_value": yen_stats(cvs),
        "positive_rate": (helped / n) if n else None,
    }


def strat_final(rows: list[dict[str, Any]], *, h: int, state: str, outcome: str) -> dict[str, Any]:
    key = {"WIN": "FINAL_WIN", "LOSS": "FINAL_LOSS", "DRAW": "FINAL_DRAW"}[outcome]
    rs = [r for r in evaluable(rows, h=h, state=state) if r.get(key)]
    cvs = [_f(r.get("continuation_value_yen_100")) for r in rs]
    cvs = [v for v in cvs if v is not None]
    return {
        "horizon_sec": int(h),
        "state": state,
        "FINAL": outcome,
        "n": len(cvs),
        "continuation_value": yen_stats(cvs),
    }


def winner_cost_rows(rows: list[dict[str, Any]], *, h: int) -> list[dict[str, Any]]:
    out = []
    for r in evaluable(rows, h=h, state=STATE_NON_RECOVERING):
        if not r.get("FINAL_WIN"):
            continue
        out.append(
            {
                "trade_id": r.get("trade_id"),
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "checkpoint": int(h),
                "checkpoint_exit_pnl": r.get("checkpoint_exit_pnl_yen_100"),
                "canonical_pnl": r.get("canonical_final_pnl_yen_100"),
                "continuation_value": r.get("continuation_value_yen_100"),
                "TOP10": bool(r.get("TOP10")),
                "TOP20": bool(r.get("TOP20")),
                "EXTEND": bool(r.get("EXTEND_TO_750")),
            }
        )
    out.sort(key=lambda x: float(x.get("continuation_value") or 0.0), reverse=True)
    return out


def loss_saving(rows: list[dict[str, Any]], *, h: int) -> dict[str, Any]:
    rs = [r for r in evaluable(rows, h=h, state=STATE_NON_RECOVERING) if r.get("FINAL_LOSS")]
    reduced = worsened = 0
    yen_saved = 0.0
    yen_lost = 0.0
    for r in rs:
        cv = _f(r.get("continuation_value_yen_100"))
        if cv is None:
            continue
        # continuation < 0: canonical worse than checkpoint exit → waiting hurt → EXIT now saved yen
        if cv < -FLAT_YEN:
            reduced += 1
            yen_saved += -cv
        elif cv > FLAT_YEN:
            worsened += 1
            yen_lost += cv
    return {
        "horizon_sec": int(h),
        "n_loss": len(rs),
        "loss_reduced_n": reduced,
        "loss_worsened_n": worsened,
        "yen_saved_total": round(yen_saved, 2),
        "yen_lost_total": round(yen_lost, 2),
        "note": "Not used to pick a best checkpoint.",
    }


def economic_mass(rows: list[dict[str, Any]], *, h: int) -> dict[str, Any]:
    nr = evaluable(rows, h=h, state=STATE_NON_RECOVERING)
    destroyed = 0.0
    saved = 0.0
    top_win = 0
    for r in nr:
        cv = _f(r.get("continuation_value_yen_100"))
        if cv is None:
            continue
        if r.get("FINAL_WIN") and cv > FLAT_YEN:
            destroyed += cv
            if r.get("TOP20") or r.get("EXTEND_TO_750"):
                top_win += 1
        if r.get("FINAL_LOSS") and cv < -FLAT_YEN:
            saved += -cv
    return {
        "horizon_sec": int(h),
        "yen_destroyed_on_wins": round(destroyed, 2),
        "yen_saved_on_losses": round(saved, 2),
        "destroyed_ge_saved": destroyed + 1e-12 >= saved,
        "top20_or_extend_win_in_nr": top_win,
        "offsets": bool(destroyed + 1e-12 >= saved and top_win > 0),
    }


def false_recovery(rows: list[dict[str, Any]], *, h: int) -> dict[str, Any]:
    rec = evaluable(rows, h=h, state=STATE_RECOVERING)
    bad = [r for r in rec if _f(r.get("continuation_value_yen_100")) is not None and float(r["continuation_value_yen_100"]) < -FLAT_YEN]
    ids = [str(r.get("trade_id")) for r in bad]
    return {
        "horizon_sec": int(h),
        "recovering_n": len(rec),
        "false_recovery_n": len(bad),
        "false_recovery_rate": (len(bad) / len(rec)) if rec else None,
        "includes_9984": FALSE_RECOVERY_KNOWN_ID in ids,
        "trade_ids": ids,
    }


def p41_winner_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    want = set(P4_1_WINNER_IDS)
    out = []
    for r in rows:
        if str(r.get("trade_id")) not in want:
            continue
        if int(r.get("horizon_sec") or 0) not in PRIMARY_CHECKPOINTS:
            continue
        out.append(
            {
                "trade_id": r.get("trade_id"),
                "checkpoint": r.get("horizon_sec"),
                "state": r.get("state"),
                "delta_bid_120_to_t": r.get("delta_bid_120_to_t"),
                "evaluable": r.get("evaluable"),
                "checkpoint_exit_pnl": r.get("checkpoint_exit_pnl_yen_100"),
                "canonical_pnl": r.get("canonical_final_pnl_yen_100"),
                "continuation_value": r.get("continuation_value_yen_100"),
                "execution_latency": r.get("execution_latency"),
                "TOP20": bool(r.get("TOP20")),
                "EXTEND": bool(r.get("EXTEND_TO_750")),
            }
        )
    out.sort(key=lambda x: (str(x.get("trade_id")), int(x.get("checkpoint") or 0)))
    return out


def day_stability(rows: list[dict[str, Any]], *, h: int) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in evaluable(rows, h=h, state=STATE_NON_RECOVERING):
        cv = _f(r.get("continuation_value_yen_100"))
        if cv is None:
            continue
        by_day[str(r.get("date"))].append(cv)
    neg = pos = zero = 0
    insuff = 0
    for day in FULL14:
        xs = by_day.get(str(day)) or []
        if not xs:
            insuff += 1
            continue
        med = float(np.median(xs))
        if med < -FLAT_YEN:
            neg += 1
        elif med > FLAT_YEN:
            pos += 1
        else:
            zero += 1
    return {
        "horizon_sec": int(h),
        "negative_days": neg,
        "positive_days": pos,
        "zero_days": zero,
        "insufficient_days": insuff,
    }


def lodo(rows: list[dict[str, Any]], *, h: int) -> dict[str, Any]:
    means = []
    meds = []
    for leave in FULL14:
        rs = [r for r in evaluable(rows, h=h, state=STATE_NON_RECOVERING) if str(r.get("date")) != str(leave)]
        cvs = [_f(r.get("continuation_value_yen_100")) for r in rs]
        cvs = [v for v in cvs if v is not None]
        if not cvs:
            continue
        arr = np.asarray(cvs, dtype=float)
        means.append(float(np.mean(arr)))
        meds.append(float(np.median(arr)))
    def pack(xs):
        if not xs:
            return {"median": None, "min": None, "max": None, "n": 0}
        return {"median": float(np.median(xs)), "min": float(np.min(xs)), "max": float(np.max(xs)), "n": len(xs)}
    return {
        "horizon_sec": int(h),
        "mean": pack(means),
        "median": pack(meds),
    }


def classify(
    *,
    all_rows: list[dict[str, Any]],
    rest_rows: list[dict[str, Any]],
    integrity: list[str],
) -> dict[str, Any]:
    if integrity:
        return {
            "ACTIONABILITY_CLASSIFICATION": CLASS_DATA,
            "A": False,
            "B": False,
            "C": False,
            "override": False,
            "why": ";".join(integrity),
            "primary_detail": {},
        }
    all_med = {}
    rest_med = {}
    masses = {}
    for h in PRIMARY_CHECKPOINTS:
        ab = block_for(all_rows, h=h, state=STATE_NON_RECOVERING)
        rb = block_for(rest_rows, h=h, state=STATE_NON_RECOVERING)
        all_med[h] = (ab.get("continuation_value") or {}).get("median")
        rest_med[h] = (rb.get("continuation_value") or {}).get("median")
        masses[h] = economic_mass(all_rows, h=h)

    def _lt0(m):
        return m is not None and float(m) < 0.0

    def _ge0(m):
        return m is not None and float(m) >= 0.0

    n_neg_all = sum(1 for h in PRIMARY_CHECKPOINTS if _lt0(all_med[h]))
    n_neg_rest = sum(1 for h in PRIMARY_CHECKPOINTS if _lt0(rest_med[h]))
    n_ge_all = sum(1 for h in PRIMARY_CHECKPOINTS if _ge0(all_med[h]))
    n_ge_rest = sum(1 for h in PRIMARY_CHECKPOINTS if _ge0(rest_med[h]))
    a = n_neg_all >= 3 and n_neg_rest >= 3
    b = n_ge_all >= 3 or n_ge_rest >= 3
    n_offset = sum(1 for h in PRIMARY_CHECKPOINTS if masses[h].get("offsets"))
    override = (not b) and n_offset >= 3 and (a or n_neg_all >= 1)
    if override:
        klass = CLASS_NOT_ACTIONABLE
    elif a:
        klass = CLASS_NEG
    elif b:
        klass = CLASS_NOT_HARM
    else:
        klass = CLASS_MIXED
    return {
        "ACTIONABILITY_CLASSIFICATION": klass,
        "A": a,
        "B": b,
        "C": (not a) and (not b) and (not override),
        "override": override,
        "n_neg_all": n_neg_all,
        "n_neg_rest": n_neg_rest,
        "n_ge0_all": n_ge_all,
        "n_ge0_rest": n_ge_rest,
        "n_offset_checkpoints": n_offset,
        "all_median": {str(h): all_med[h] for h in PRIMARY_CHECKPOINTS},
        "rest_median": {str(h): rest_med[h] for h in PRIMARY_CHECKPOINTS},
        "economic_mass": {str(h): masses[h] for h in PRIMARY_CHECKPOINTS},
        "why": (
            f"A={a} ({n_neg_all}/4 ALL median<0, {n_neg_rest}/4 REST11 median<0); "
            f"B={b} ({n_ge_all}/4 ALL >=0, {n_ge_rest}/4 REST11 >=0); "
            f"override={override} (offset_cps={n_offset}/4)"
        ),
    }


def compact_cv(blk: dict[str, Any]) -> dict[str, Any]:
    cv = blk.get("continuation_value") or {}
    return {
        "n": blk.get("n"),
        "mean": cv.get("mean"),
        "median": cv.get("median"),
        "p25": cv.get("p25"),
        "p75": cv.get("p75"),
        "negative_rate": blk.get("negative_rate"),
        "positive_rate": blk.get("positive_rate"),
        "helped_n": blk.get("continuation_helped_n"),
        "hurt_n": blk.get("continuation_hurt_n"),
        "flat_n": blk.get("continuation_flat_n"),
    }


