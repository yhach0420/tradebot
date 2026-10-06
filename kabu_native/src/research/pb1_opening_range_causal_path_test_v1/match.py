"""Risk-set controls at T. Do not match treatment-defining variables. Do not drop later PB1 triggers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.pb1_opening_range_causal_path_test_v1 import MATCH_ATR_REL_TOL, MATCH_LEVEL_ATR_TOL
from research.pb1_opening_range_causal_path_test_v1.path import loc_features, next_open_from, signed_path, vwap_at
from research.pb1_opening_range_continuation_face_valid_v2.inplay import daily_bias
from research.pb1_opening_range_continuation_face_valid_v2.walk import _sma


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _rel(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) or not _finite(b):
        return False
    den = max(abs(float(b)), 1e-9)
    return abs(float(a) - float(b)) / den <= float(tol)


def _close(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) <= float(tol)


def pos_at(rec: dict[str, Any], t: str) -> int | None:
    mp = rec.get("t_to_pos") or {}
    if str(t) in mp:
        return int(mp[str(t)])
    for i in rec.get("session_idx") or []:
        if str(rec["t"][i]) == str(t):
            return i
    return None


def _atr_ok(a: Any, b: Any) -> bool:
    return _rel(a, b, MATCH_ATR_REL_TOL)


def _level_ok(tf: dict[str, Any], cf: dict[str, Any]) -> bool:
    if tf.get("side_pdh") != cf.get("side_pdh"):
        return False
    if tf.get("side_pdl") != cf.get("side_pdl"):
        return False
    if _finite(tf.get("dist_pdh_atr")) and _finite(cf.get("dist_pdh_atr")):
        if not _close(tf.get("dist_pdh_atr"), cf.get("dist_pdh_atr"), MATCH_LEVEL_ATR_TOL):
            return False
    return True


def _score(tf: dict[str, Any], cf: dict[str, Any], *, same_symbol: bool) -> float:
    d = 0.0 if same_symbol else 2.0
    if _finite(tf.get("atr20")) and _finite(cf.get("atr20")) and float(tf["atr20"]) != 0:
        d += abs(float(cf["atr20"]) / float(tf["atr20"]) - 1.0)
    if _finite(tf.get("dist_pdh_atr")) and _finite(cf.get("dist_pdh_atr")):
        d += 0.25 * abs(float(cf["dist_pdh_atr"]) - float(tf["dist_pdh_atr"]))
    if _finite(tf.get("dist_pdl_atr")) and _finite(cf.get("dist_pdl_atr")):
        d += 0.25 * abs(float(cf["dist_pdl_atr"]) - float(tf["dist_pdl_atr"]))
    return d


def _control_bias(e: dict[str, Any], rec: dict[str, Any], pos: int) -> str:
    px = rec["c"][pos]
    prior = list(e.get("prior_closes") or [])
    sma5 = _sma(prior, float(px) if _finite(px) else float("nan"), 5)
    sma25 = _sma(prior, float(px) if _finite(px) else float("nan"), 25)
    sma75 = _sma(prior, float(px) if _finite(px) else float("nan"), 75)
    return daily_bias(sma5, sma25, sma75)


def find_control(tf: dict[str, Any], pool: list[dict[str, Any]], recs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    """Match confounders only. Later PB1 trigger on the control remains eligible."""
    t = str(tf.get("trigger_t") or "")
    sign = int(tf.get("DIR") or 0)
    best_e = None
    best_rec = None
    best_nxt = None
    best_vw = None
    best_d = 1e9
    n_at_risk = 0
    n_pass = 0
    tf_loc = {
        "side_pdh": tf.get("side_pdh"),
        "side_pdl": tf.get("side_pdl"),
        "dist_pdh_atr": tf.get("dist_pdh_atr"),
        "dist_pdl_atr": tf.get("dist_pdl_atr"),
    }
    for e in pool:
        if str(e.get("symbol")) == str(tf.get("symbol")) and str(e.get("date")) == str(tf.get("date")):
            continue
        if int(e.get("DIR") or 0) != sign:
            continue
        trig = e.get("trigger_t")
        if trig is not None and str(trig) <= t:
            continue
        rec = recs.get((str(e["symbol"]), str(e["date"])))
        if rec is None:
            continue
        pos = pos_at(rec, t)
        if pos is None:
            continue
        n_at_risk += 1
        nxt = next_open_from(rec, rec["session_idx"], pos)
        if nxt is None:
            continue
        vw = vwap_at(rec, pos, sign)
        if vw != str(tf.get("vwap_trigger") or ""):
            continue
        if _control_bias(e, rec, pos) != str(tf.get("daily_bias") or ""):
            continue
        if not _atr_ok(e.get("atr20"), tf.get("atr20")):
            continue
        cf_loc = loc_features(nxt["entry_px"], e.get("atr20"), e.get("pdh"), e.get("pdl"), e.get("d5h"), e.get("d5l"))
        if not _level_ok(tf_loc, cf_loc):
            continue
        n_pass += 1
        same = str(e.get("symbol")) == str(tf.get("symbol"))
        d = _score({**tf, "atr20": tf.get("atr20")}, {**e, **cf_loc}, same_symbol=same)
        if d < best_d:
            best_d = d
            best_e = e
            best_rec = rec
            best_nxt = nxt
            best_vw = vw
    if best_e is None or best_rec is None or best_nxt is None:
        return None
    trig = best_e.get("trigger_t")
    path = signed_path(
        best_rec,
        session_idx=best_rec["session_idx"],
        entry_pos=int(best_nxt["entry_pos"]),
        sign=sign,
        or_high=float(best_e["or_high"]),
        or_low=float(best_e["or_low"]),
        retest_high=None,
        retest_low=None,
        target_px=None,
    )
    return {
        "control_symbol": best_e.get("symbol"),
        "control_date": best_e.get("date"),
        "control_block": best_e.get("block"),
        "control_trigger_t": trig,
        "control_later_pb1": bool(trig is not None and str(trig) > t),
        "control_entry_t": best_nxt.get("entry_t"),
        "control_entry_px": best_nxt.get("entry_px"),
        "control_daily_bias": best_e.get("daily_bias"),
        "control_vwap": best_vw,
        "control_atr20": best_e.get("atr20"),
        "match_score": best_d,
        "same_symbol": str(best_e.get("symbol")) == str(tf.get("symbol")),
        "n_at_risk": n_at_risk,
        "n_confounder_pass": n_pass,
        "ct_r5_bps": path.get("r5_bps"),
        "ct_r10_bps": path.get("r10_bps"),
        "ct_r20_bps": path.get("r20_bps"),
        "ct_MFE_bps": path.get("MFE_bps"),
        "ct_MAE_bps": path.get("MAE_bps"),
        "ct_or_accept_fail": path.get("or_accept_fail"),
        "future_control_selection": False,
    }


def match_all(events: list[dict[str, Any]], eligible: list[dict[str, Any]], recs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    by_dir: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in eligible:
        by_dir[int(e.get("DIR") or 0)].append(e)
    pairs: list[dict[str, Any]] = []
    matched_n = 0
    same_sym_n = 0
    later_ok_n = 0
    for ev in events:
        ctl = find_control(ev, by_dir.get(int(ev.get("DIR") or 0), []), recs)
        row = {
            "symbol": ev.get("symbol"),
            "date": ev.get("date"),
            "block": ev.get("block"),
            "direction": ev.get("direction"),
            "trigger_t": ev.get("trigger_t"),
            "trigger_primary": ev.get("trigger_primary"),
            "matched": bool(ctl),
            "tr_r5_bps": ev.get("r5_bps"),
            "tr_r10_bps": ev.get("r10_bps"),
            "tr_r20_bps": ev.get("r20_bps"),
            "tr_MFE_bps": ev.get("MFE_bps"),
            "tr_MAE_bps": ev.get("MAE_bps"),
            "tr_MFE_over_R": ev.get("MFE_over_R"),
            "tr_atr20": ev.get("atr20"),
            "tr_daily_bias": ev.get("daily_bias"),
            "tr_dist_pdh_atr": ev.get("dist_pdh_atr"),
            "future_control_selection": False,
            "treatment_matched_away": False,
        }
        if ctl:
            matched_n += 1
            if ctl.get("same_symbol"):
                same_sym_n += 1
            if ctl.get("control_later_pb1"):
                later_ok_n += 1
            row.update(ctl)
            row["gap_r5_bps"] = (
                float(ev["r5_bps"]) - float(ctl["ct_r5_bps"])
                if _finite(ev.get("r5_bps")) and _finite(ctl.get("ct_r5_bps"))
                else float("nan")
            )
            row["gap_r10_bps"] = (
                float(ev["r10_bps"]) - float(ctl["ct_r10_bps"])
                if _finite(ev.get("r10_bps")) and _finite(ctl.get("ct_r10_bps"))
                else float("nan")
            )
            row["gap_r20_bps"] = (
                float(ev["r20_bps"]) - float(ctl["ct_r20_bps"])
                if _finite(ev.get("r20_bps")) and _finite(ctl.get("ct_r20_bps"))
                else float("nan")
            )
            row["gap_MFE_bps"] = (
                float(ev["MFE_bps"]) - float(ctl["ct_MFE_bps"])
                if _finite(ev.get("MFE_bps")) and _finite(ctl.get("ct_MFE_bps"))
                else float("nan")
            )
            row["gap_MAE_bps"] = (
                float(ev["MAE_bps"]) - float(ctl["ct_MAE_bps"])
                if _finite(ev.get("MAE_bps")) and _finite(ctl.get("ct_MAE_bps"))
                else float("nan")
            )
        pairs.append(row)
    n = len(events)
    return {
        "treated_n": n,
        "matched_n": matched_n,
        "match_rate": float(matched_n / n) if n else None,
        "same_symbol_match_n": same_sym_n,
        "control_later_pb1_kept_n": later_ok_n,
        "future_control_selection": False,
        "treatment_variable_matched_away": False,
        "pairs": pairs,
    }
