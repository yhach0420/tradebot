"""
Fresh PBv2 collapse re-analysis: main per-session streaming aggregation.

Covers Investigation A (funnel), C (input distribution), D (internal blocker
reconstruction + score3 fresh trace), F (freshness counterfactual from audit).

Outputs partial JSON per session into temp/fresh_pbv2_reanalysis/agg/, plus
score3 trace rows. A combiner script assembles final CSVs.
"""
import csv
import gzip
import json
import math
import os
import sys
from collections import Counter

BASE = "results/small_paper"
OUT = "temp/fresh_pbv2_reanalysis/agg"

SESSIONS = [
    ("20260624", "081514", "AM"), ("20260624", "122521", "PM"),
    ("20260625", "080340", "AM"), ("20260625", "122535", "PM"),
    ("20260629", "080236", "AM"), ("20260629", "122526", "PM"),
    ("20260630", "091118", "AM"),
    ("20260701", "080616", "AM"),
]

STALE_REASONS = {"data_stale_price", "data_stale_board", "event_stale_price"}
PRE_GATE_REASONS = {"am_pm_entry_stop", "outside_refresh_universe", "outside_allowed_trading_window"}

SUIT_THRESHOLD = 54.695739
MOMENTUM_CUTOFF = 0.2546
BOARD_P33 = 0.437286
BOARD_P66 = 0.527869

DIST_FIELDS = [
    "momentum_continuation_score", "entry_order_book_imbalance", "atr_pct",
    "trading_value", "spread_bps", "update_count_before_entry", "current_price",
    "day_high_distance_pct", "continuation_quality_score", "entry_rise_5min_pct",
    "entry_vwap_dev_pct", "rsi14",
]


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _dyn40(j):
    b = str(j.get("universe_bucket") or "")
    s = str(j.get("universe_slot") or "")
    sb = str(j.get("source_bucket") or "")
    return "dynamic" in b or "dynamic" in s or "dynamic" in sb


def high_drift_block(j):
    if not _dyn40(j):
        return False
    dist = _f(j.get("day_high_distance_pct")) or _f(j.get("entry_near_day_high_pct")) or 0.0
    dist = abs(dist)
    r5 = _f(j.get("entry_rise_5min_pct"))
    r10 = _f(j.get("entry_rise_10min_pct"))
    r15 = _f(j.get("entry_rise_15min_pct"))
    if dist < 1.2:
        return False
    if r10 is not None and r10 < -0.15:
        if r5 is None:
            return True
        if r5 > r10 and r5 <= 1.0:
            return True
    if dist >= 1.5:
        if r15 is not None and r15 < -0.5 and (r5 is None or r5 < 0.2):
            return True
        if r5 is not None and r5 < -0.5 and (r10 is None or r10 < -0.2):
            return True
    return False


def near_day_high_block(j):
    if not _dyn40(j):
        return False
    dist = _f(j.get("day_high_distance_pct")) or _f(j.get("entry_near_day_high_pct"))
    mom = (_f(j.get("entry_momentum_score")) or _f(j.get("entry_momentum_continuation_score"))
           or _f(j.get("momentum_continuation_score")))
    if dist is None:
        return False
    return dist <= 1.5 and (mom or 0.0) < 0.30


def suitability_block(j):
    atr = _f(j.get("atr_pct"))
    tv = _f(j.get("trading_value"))
    if atr is None or tv is None or tv <= 0:
        return True, "missing_inputs"
    score = atr * math.log10(max(tv, 1.0))
    return (score < SUIT_THRESHOLD), ("below_threshold" if score < SUIT_THRESHOLD else "")


def board_token(j):
    v = _f(j.get("entry_order_book_imbalance"))
    if v is None:
        return "missing"
    if v <= BOARD_P33:
        return "low"
    if v <= BOARD_P66:
        return "mid"
    return "high"


def recon_blocker(j):
    """Reconstruct first PBv2 blocker in gate order for fresh candidates."""
    if high_drift_block(j):
        return "high_drift_pullback"
    if near_day_high_block(j):
        return "near_day_high_low_momentum_dynamic40_guard"
    sb, why = suitability_block(j)
    if sb:
        return "daytrade_suitability(" + why + ")"
    m = _f(j.get("momentum_continuation_score"))
    if m is None or m > MOMENTUM_CUTOFF:
        return "momentum_low_required"
    bt = board_token(j)
    if bt not in ("mid", "high"):
        return "entry_score_v2_below_threshold(board_" + bt + ")"
    # v2 score = momentum(2) + board(1) = 3 here, so score passes
    if str(j.get("reentry_rsi_guard_blocked")).lower() == "true":
        return "reentry_rsi_guard_below60"
    if str(j.get("entry_quality_guard_blocked")).lower() == "true":
        rr = str(j.get("entry_quality_guard_reject_reason") or "entry_quality_guard")
        return rr
    return "passed_all_recon"


def process(day, sess, ampm):
    sdir = f"{BASE}/{day}/live_session_{sess}"
    agg = {"day": day, "session": sess, "ampm": ampm}

    final_reasons = Counter()
    recon_reasons = Counter()          # fresh & finally-rejected candidates
    confusion = Counter()              # (internal, recon) on 7/1
    internal_reasons = Counter()
    v2_hist = Counter()
    hourly = Counter()                 # (hour, category)
    n = dict(candidates=0, fresh=0, pre_gate=0, stale=0, accepted=0,
             score3=0, score3_fresh=0, score3_fresh_rejected=0,
             fresh_mom_low_board_ok=0, fresh_mom_low_board_ok_rejected=0)
    dist = {k: {"fresh": [], "stale": []} for k in DIST_FIELDS}
    missing = Counter()
    board_tok = Counter()
    sym_counter = Counter()
    trace_rows = []
    TRACE_CAP = 4000

    with open(f"{sdir}/small_paper_events.jsonl", encoding="utf-8") as f:
        for line in f:
            if '"candidate"' not in line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("event_type") != "candidate":
                continue
            n["candidates"] += 1
            accepted = not j.get("gate_reject_reason")
            reason = j.get("gate_reject_reason") or ("ACCEPT" if accepted else "")
            final_reasons[reason] += 1
            sym_counter[j.get("symbol")] += 1
            hour = str(j.get("event_time") or "")[11:13]

            if reason in STALE_REASONS:
                cat = "stale"
            elif reason in PRE_GATE_REASONS:
                cat = "pre_gate"
            else:
                cat = "fresh"
            n[cat if cat != "fresh" else "fresh"] += 1
            hourly[(hour, cat if cat != "fresh" else ("accept" if accepted else "fresh_reject"))] += 1
            if accepted:
                n["accepted"] += 1

            v2 = j.get("entry_expectancy_score_v2")
            try:
                v2i = int(v2)
            except (TypeError, ValueError):
                v2i = None
            v2_hist[str(v2i)] += 1
            if v2i is not None and v2i >= 3:
                n["score3"] += 1
                if cat == "fresh":
                    n["score3_fresh"] += 1
                    if not accepted:
                        n["score3_fresh_rejected"] += 1

            bucket = "fresh" if cat == "fresh" else "stale"
            for k in DIST_FIELDS:
                v = _f(j.get(k))
                if v is None:
                    missing[(k, bucket)] += 1
                else:
                    dist[k][bucket].append(v)
            if cat == "fresh":
                board_tok[board_token(j)] += 1

            if cat == "fresh" and not accepted:
                rec = recon_blocker(j)
                recon_reasons[rec] += 1
                internal = j.get("pbv2_internal_reason")
                if internal:
                    internal_reasons[internal] += 1
                    confusion[(internal, rec)] += 1
                m = _f(j.get("momentum_continuation_score"))
                if m is not None and m <= MOMENTUM_CUTOFF and board_token(j) in ("mid", "high"):
                    n["fresh_mom_low_board_ok"] += 1
                    n["fresh_mom_low_board_ok_rejected"] += 1
                if v2i is not None and v2i >= 3 and len(trace_rows) < TRACE_CAP:
                    trace_rows.append([
                        day, ampm, j.get("event_time"), j.get("symbol"),
                        reason, internal or "", rec,
                        j.get("momentum_continuation_score"),
                        j.get("entry_order_book_imbalance"),
                        j.get("atr_pct"), j.get("trading_value"),
                        j.get("day_high_distance_pct"), j.get("entry_rise_5min_pct"),
                        j.get("entry_rise_10min_pct"),
                        j.get("spread_bps"), j.get("update_count_before_entry"),
                        j.get("universe_bucket"), j.get("continuation_quality_score"),
                    ])
            elif cat == "fresh" and accepted:
                m = _f(j.get("momentum_continuation_score"))
                if m is not None and m <= MOMENTUM_CUTOFF and board_token(j) in ("mid", "high"):
                    n["fresh_mom_low_board_ok"] += 1

    # ---- audit pass: freshness ages + counterfactual ----
    ages = {"price": [], "board": []}
    cf = Counter()
    audit_score3 = Counter()
    with open(f"{sdir}/entry_scan_audit.jsonl", encoding="utf-8") as f:
        for line in f:
            if '"entry_symbol_eval"' not in line:
                continue
            try:
                a = json.loads(line)
            except Exception:
                continue
            if a.get("audit_type") != "entry_symbol_eval":
                continue
            pa = _f(a.get("price_age_sec"))
            ba = _f(a.get("board_age_sec"))
            if pa is not None:
                ages["price"].append(pa)
            if ba is not None:
                ages["board"].append(ba)
            v1_pass = pa is not None and pa <= 3.0 and ba is not None and ba <= 3.0
            v2_pass = ba is not None and ba <= 3.0  # event_stale unknown pre-7/1 (approx fresh)
            cf["evals"] += 1
            if v1_pass:
                cf["v1_pass"] += 1
            if v2_pass:
                cf["v2_pass"] += 1
            if v2_pass and not v1_pass:
                cf["rescued_v1_to_v2"] += 1
            sc = a.get("entry_score_v2")
            try:
                sc = int(sc)
            except (TypeError, ValueError):
                sc = None
            if sc is not None and sc >= 3:
                audit_score3["score3"] += 1
                if v1_pass:
                    audit_score3["score3_v1_pass"] += 1
                if v2_pass:
                    audit_score3["score3_v2_pass"] += 1
                if v2_pass and not v1_pass:
                    audit_score3["score3_rescued"] += 1
            if a.get("fallback_used") in (True, "true", "True"):
                cf["fallback_used_recorded"] += 1

    def pct(vals, p):
        if not vals:
            return None
        vals = sorted(vals)
        return round(vals[min(len(vals) - 1, int(len(vals) * p))], 4)

    agg["counts"] = n
    agg["final_reasons"] = dict(final_reasons.most_common(30))
    agg["recon_reasons"] = dict(recon_reasons.most_common(30))
    agg["internal_reasons"] = dict(internal_reasons.most_common(30))
    agg["confusion_top"] = [[k[0], k[1], v] for k, v in confusion.most_common(40)]
    agg["v2_hist"] = dict(v2_hist)
    agg["board_token_fresh"] = dict(board_tok)
    agg["hourly"] = {f"{h}:{c}": v for (h, c), v in sorted(hourly.items())}
    agg["top_symbols"] = sym_counter.most_common(10)
    agg["dist"] = {
        k: {
            b: {"n": len(vs), "p10": pct(vs, 0.10), "p50": pct(vs, 0.50), "p90": pct(vs, 0.90)}
            for b, vs in d.items()
        }
        for k, d in dist.items()
    }
    agg["missing"] = {f"{k}:{b}": v for (k, b), v in missing.items()}
    agg["ages"] = {
        k: {"n": len(v), "p50": pct(v, 0.5), "p75": pct(v, 0.75), "p90": pct(v, 0.9)}
        for k, v in ages.items()
    }
    agg["freshness_cf"] = dict(cf)
    agg["freshness_cf_score3"] = dict(audit_score3)

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{day}_{sess}.json", "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=1)
    with gzip.open(f"{OUT}/{day}_{sess}_score3_trace.csv.gz", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "ampm", "event_time", "symbol", "final_reason", "internal_reason",
                    "recon_reason", "momentum_continuation_score", "entry_order_book_imbalance",
                    "atr_pct", "trading_value", "day_high_distance_pct", "entry_rise_5min_pct",
                    "entry_rise_10min_pct", "spread_bps", "update_count_before_entry",
                    "universe_bucket", "continuation_quality_score"])
        w.writerows(trace_rows)
    print("done", day, sess, n)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        idx = [int(x) for x in sys.argv[1:]]
    else:
        idx = range(len(SESSIONS))
    for i in idx:
        d, s, ap = SESSIONS[i]
        process(d, s, ap)
