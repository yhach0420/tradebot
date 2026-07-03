"""
Investigation D refined: first-blocker classification with volume-gate shadow join.

For sessions with volume_gate_shadow_eval.jsonl (6/29 AM/PM, 6/30, 7/1) join the
runtime vol_liq_score (exact suitability inputs at gate time) to candidates and
classify the first PBv2 blocker in gate order. Validate against 7/1
pbv2_internal_reason ground truth.
"""
import json
import sys
from collections import Counter

BASE = "results/small_paper"
SUIT_THRESHOLD = 54.695739
MOMENTUM_CUTOFF = 0.2546
BOARD_P33 = 0.437286

SESSIONS = [
    ("20260629", "080236", "AM"),
    ("20260629", "122526", "PM"),
    ("20260630", "091118", "AM"),
    ("20260701", "080616", "AM"),
]

STALE = {"data_stale_price", "data_stale_board", "event_stale_price"}
PRE = {"am_pm_entry_stop", "outside_refresh_universe", "outside_allowed_trading_window"}


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _dyn40(j):
    return any("dynamic" in str(j.get(k) or "") for k in ("universe_bucket", "universe_slot", "source_bucket"))


def high_drift_block(j):
    if not _dyn40(j):
        return False
    dist = abs(_f(j.get("day_high_distance_pct")) or _f(j.get("entry_near_day_high_pct")) or 0.0)
    r5, r10, r15 = (_f(j.get(k)) for k in ("entry_rise_5min_pct", "entry_rise_10min_pct", "entry_rise_15min_pct"))
    if dist < 1.2:
        return False
    if r10 is not None and r10 < -0.15:
        if r5 is None or (r5 > r10 and r5 <= 1.0):
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
    return dist is not None and dist <= 1.5 and (mom or 0.0) < 0.30


def board_ok(j):
    v = _f(j.get("entry_order_book_imbalance"))
    return v is not None and v > BOARD_P33


def load_shadow(sdir):
    m = {}
    stats = Counter()
    try:
        f = open(f"{sdir}/volume_gate_shadow_eval.jsonl", encoding="utf-8")
    except FileNotFoundError:
        return None, stats
    with f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = str(r.get("timestamp") or "")[:19]
            key = (r.get("symbol"), ts)
            score = r.get("vol_liq_score")
            p100 = bool(r.get("pass_v100"))
            m[key] = (score, p100)
            stats["rows"] += 1
            if score is None:
                stats["score_missing"] += 1
            elif p100:
                stats["pass_v100"] += 1
            else:
                stats["fail_v100"] += 1
    return m, stats


def classify(j, shadow):
    if high_drift_block(j):
        return "high_drift_pullback"
    if near_day_high_block(j):
        return "near_day_high_low_momentum_dynamic40_guard"
    if shadow is not None:
        key = (j.get("symbol"), str(j.get("event_time") or "")[:19])
        hit = shadow.get(key)
        if hit is None:
            # try entry_time
            hit = shadow.get((j.get("symbol"), str(j.get("entry_time") or "")[:19]))
        if hit is not None:
            score, p100 = hit
            if score is None:
                return "daytrade_suitability(score_missing)"
            if not p100:
                return "daytrade_suitability(below_threshold)"
        else:
            return "no_shadow_join"
    m = _f(j.get("momentum_continuation_score"))
    if m is None or m > MOMENTUM_CUTOFF:
        return "momentum_low_required"
    if not board_ok(j):
        return "entry_score_v2_below_threshold(board)"
    if str(j.get("reentry_rsi_guard_blocked")).lower() == "true":
        return "reentry_rsi_guard_below60"
    if str(j.get("entry_quality_guard_blocked")).lower() == "true":
        return str(j.get("entry_quality_guard_reject_reason") or "entry_quality_guard")
    return "residual(cluster/stop_low_mfe/cap/other)"


def run(day, sess, ampm):
    sdir = f"{BASE}/{day}/live_session_{sess}"
    shadow, sh_stats = load_shadow(sdir)
    dist = Counter()
    confusion = Counter()
    n = Counter()
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
            r = j.get("gate_reject_reason")
            if not r:
                n["gate_pass"] += 1
                continue
            if r in STALE:
                n["stale"] += 1
                continue
            if r in PRE:
                n["pre_gate"] += 1
                continue
            n["fresh_reject"] += 1
            c = classify(j, shadow)
            dist[c] += 1
            internal = j.get("pbv2_internal_reason")
            if internal:
                confusion[(internal, c)] += 1
    out = {
        "day": day, "ampm": ampm, "counts": dict(n),
        "shadow_stats": dict(sh_stats),
        "blockers": dict(dist.most_common()),
        "confusion": [[a, b, v] for (a, b), v in confusion.most_common(30)],
    }
    with open(f"temp/fresh_pbv2_reanalysis/agg/{day}_{sess}_blockers.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("===", day, ampm, dict(n))
    print(" shadow:", dict(sh_stats))
    for k, v in dist.most_common(15):
        print(f"   {k:55s} {v}")
    if confusion:
        print(" confusion(top):")
        for (a, b), v in confusion.most_common(15):
            print(f"   {a:40s} <- {b:50s} {v}")


if __name__ == "__main__":
    for i in ([int(x) for x in sys.argv[1:]] or range(len(SESSIONS))):
        run(*SESSIONS[i])
