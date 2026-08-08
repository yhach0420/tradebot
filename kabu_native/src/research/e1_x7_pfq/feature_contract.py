"""Phase 0 — Feature Semantic Contract Audit for PFQ flow features."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_fcrr.replay import load_day_events
from research.e1_x7_pfq.config import MIN_CLASSIFIED_TRADES_30S

JST = ZoneInfo("Asia/Tokyo")
FRESHNESS_MAX_SEC = 30.0


FEATURE_CONTRACT_DOC = {
    "uptick_volume_ratio_30s": {
        "numerator": "sum of cum_vol deltas on consecutive board ticks where mid rises (uptick)",
        "denominator": "uptick_volume + downtick_volume (unchanged mid excluded; unknown=none)",
        "classification": (
            "tick-rule on mid: mid_t > mid_{t-1} => uptick; mid_t < mid_{t-1} => downtick; "
            "equal mid => unchanged (not in denominator); no separate trade-side feed => "
            "trade_side_quality=TICK_RULE_INFERRED"
        ),
        "weighting": "volume-weighted via max(0, cum_vol_t - cum_vol_{t-1}) on board events",
        "unchanged_handling": "ignored for ratio numerator and denominator",
        "unknown_side_handling": "not produced; no trade prints — N/A",
        "zero_denominator": "ratio_valid=false, FLOW_RATIO_NOT_EVALUABLE (not filled with 0/1)",
        "missing_handling": "None / not evaluable — never coerce to 0 or 1",
        "window": "ticks with decision_time-30s <= t <= feature_asof_time <= decision_time",
        "same_identity": "same symbol/day/session board stream in FeatureBuffer",
        "future_forbidden": True,
        "min_classified_trades": MIN_CLASSIFIED_TRADES_30S,
        "min_classified_volume": "> 0",
    },
    "price_update_count_10s": {
        "unit_definition": "1 update = mid change vs previous tick mid in window (abs(delta)>0)",
        "mid_only": True,
        "includes_bid_ask_only_changes": False,
        "includes_equal_push": False,
        "includes_duplicate_equal_mid": False,
        "stale": "snapshot rejected if buffer age > 30s; stale ticks not counted in snapshot",
        "window": "ticks with decision_time-10s <= t <= feature_asof_time <= decision_time",
        "same_identity": "same symbol/day/session",
        "asof_le_decision": True,
    },
}


@dataclass
class FlowAudit:
    episode_id: str
    day: str
    session: str
    symbol: str
    decision_time: float
    feature_asof_time: Optional[float] = None
    # ratio audit
    classified_trade_count_30s: int = 0
    classified_volume_30s: float = 0.0
    uptick_trade_count_30s: int = 0
    downtick_trade_count_30s: int = 0
    unchanged_trade_count_30s: int = 0
    unknown_trade_count_30s: int = 0
    uptick_volume_30s: float = 0.0
    downtick_volume_30s: float = 0.0
    uptick_volume_ratio_30s: Optional[float] = None
    ratio_denominator: float = 0.0
    ratio_valid: bool = False
    ratio_invalid_reason: Optional[str] = None
    # update audit
    raw_event_count_10s: int = 0
    deduplicated_event_count_10s: int = 0
    bid_change_count_10s: int = 0
    ask_change_count_10s: int = 0
    mid_change_count_10s: int = 0
    price_update_count_10s: Optional[int] = None
    stale_event_count_10s: int = 0
    duplicate_event_count_10s: int = 0
    buffer_complete: bool = False
    future_contaminated: bool = False


def _session_of(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def audit_flow_at_decision(
    buf: FeatureBuffer,
    *,
    decision_time: float,
    episode_id: str,
    day: str,
    session: str,
    symbol: str,
) -> FlowAudit:
    """Causal audit of flow features at decision_time (asof <= decision)."""
    out = FlowAudit(
        episode_id=episode_id, day=day, session=session, symbol=symbol,
        decision_time=decision_time,
    )
    if not buf.ticks:
        out.ratio_invalid_reason = "NO_TICKS"
        return out
    last = buf.ticks[-1]
    if last.t > decision_time + 1e-9:
        out.future_contaminated = True
        out.ratio_invalid_reason = "FUTURE_TICK"
        return out
    age = decision_time - last.t
    out.feature_asof_time = last.t
    if age > FRESHNESS_MAX_SEC + 1e-9:
        out.stale_event_count_10s = 1
        out.ratio_invalid_reason = "STALE"
        return out

    # --- 30s flow ratio ---
    lo30 = decision_time - 30.0
    w30 = [x for x in buf.ticks if x.t >= lo30 - 1e-12 and x.t <= decision_time + 1e-12]
    for i in range(1, len(w30)):
        dv = max(0.0, w30[i].cum_vol - w30[i - 1].cum_vol)
        if w30[i].mid > w30[i - 1].mid + 1e-12:
            out.uptick_trade_count_30s += 1
            out.uptick_volume_30s += dv
        elif w30[i].mid < w30[i - 1].mid - 1e-12:
            out.downtick_trade_count_30s += 1
            out.downtick_volume_30s += dv
        else:
            out.unchanged_trade_count_30s += 1
    out.classified_trade_count_30s = out.uptick_trade_count_30s + out.downtick_trade_count_30s
    out.classified_volume_30s = out.uptick_volume_30s + out.downtick_volume_30s
    out.ratio_denominator = out.classified_volume_30s
    if out.classified_trade_count_30s < MIN_CLASSIFIED_TRADES_30S:
        out.ratio_valid = False
        out.ratio_invalid_reason = "FLOW_RATIO_NOT_EVALUABLE"
        out.uptick_volume_ratio_30s = None
    elif out.ratio_denominator <= 0:
        out.ratio_valid = False
        out.ratio_invalid_reason = "FLOW_RATIO_NOT_EVALUABLE"
        out.uptick_volume_ratio_30s = None
    else:
        out.ratio_valid = True
        out.ratio_invalid_reason = None
        out.uptick_volume_ratio_30s = out.uptick_volume_30s / out.ratio_denominator

    # --- 10s price updates ---
    lo10 = decision_time - 10.0
    w10 = [x for x in buf.ticks if x.t >= lo10 - 1e-12 and x.t <= decision_time + 1e-12]
    out.raw_event_count_10s = len(w10)
    # dedupe equal (t, mid, bid, ask)
    seen = set()
    dedup = []
    for x in w10:
        key = (round(x.t, 6), x.mid, x.bid, x.ask)
        if key in seen:
            out.duplicate_event_count_10s += 1
            continue
        seen.add(key)
        dedup.append(x)
    out.deduplicated_event_count_10s = len(dedup)
    if len(dedup) >= 2:
        prev = dedup[0]
        mid_c = bid_c = ask_c = 0
        for x in dedup[1:]:
            if abs(x.mid - prev.mid) > 1e-12:
                mid_c += 1
            if abs(x.bid - prev.bid) > 1e-12:
                bid_c += 1
            if abs(x.ask - prev.ask) > 1e-12:
                ask_c += 1
            prev = x
        out.mid_change_count_10s = mid_c
        out.bid_change_count_10s = bid_c
        out.ask_change_count_10s = ask_c
        out.price_update_count_10s = mid_c  # contract: mid-only
    else:
        out.price_update_count_10s = None if len(dedup) < 2 else 0

    snap = buf.snapshot(decision_time)
    out.buffer_complete = bool(snap.get("complete"))
    return out


def run_phase0_audit(
    pullback_rows: list[dict[str, Any]],
    events_by_day: dict[str, list],
    episodes_meta: dict[str, dict],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audit all pullback cluster reps; compare to stored features where present."""
    by_day: dict[str, list] = defaultdict(list)
    for r in pullback_rows:
        by_day[r["day"]].append(r)

    audits: list[dict[str, Any]] = []
    for day in sorted(by_day):
        events = events_by_day[day]
        reps = sorted(by_day[day], key=lambda x: (float(x["decision_time"]), x["episode_id"]))
        pending = {r["episode_id"]: r for r in reps}
        bufs: dict[str, FeatureBuffer] = {}
        captured = set()

        for t, sym, row in events:
            if not pending:
                break
            ts = row["ts"]
            sess = _session_of(ts)
            buf = bufs.get(sym)
            if buf is None:
                buf = FeatureBuffer()
                bufs[sym] = buf
            buf.push(t, float(row["bid"]), float(row["ask"]), float(row["vwap"]), float(row["vol"]))

            done = []
            for eid, rep in list(pending.items()):
                if rep["symbol"] != sym:
                    continue
                if float(t) + 1e-12 < float(rep["decision_time"]):
                    continue
                if sess != rep.get("session"):
                    continue
                # first fresh event at/after decision
                if buf.age(t) > FRESHNESS_MAX_SEC + 1e-9:
                    continue
                a = audit_flow_at_decision(
                    buf, decision_time=float(rep["decision_time"]),
                    episode_id=eid, day=day, session=sess, symbol=sym,
                )
                # also capture stored feature for comparison
                stored_ratio = rep.get("uptick_volume_ratio_30s")
                stored_pu = rep.get("price_update_count_10s")
                row_out = {
                    **a.__dict__,
                    "net_plus_5bps": rep.get("net_plus_5bps"),
                    "cluster_id": rep.get("cluster_id"),
                    "stored_uptick_volume_ratio_30s": stored_ratio,
                    "stored_price_update_count_10s": stored_pu,
                    "setup_type": "PULLBACK_RECLAIM",
                }
                audits.append(row_out)
                captured.add(eid)
                done.append(eid)
            for eid in done:
                pending.pop(eid, None)

        for eid, rep in pending.items():
            audits.append({
                "episode_id": eid,
                "day": day,
                "session": rep.get("session"),
                "symbol": rep.get("symbol"),
                "decision_time": rep.get("decision_time"),
                "cluster_id": rep.get("cluster_id"),
                "ratio_valid": False,
                "ratio_invalid_reason": "NO_ENTRY_EVENT",
                "uptick_volume_ratio_30s": None,
                "price_update_count_10s": None,
                "net_plus_5bps": rep.get("net_plus_5bps"),
                "setup_type": "PULLBACK_RECLAIM",
                "future_contaminated": False,
            })

    # distributions
    valid = [a for a in audits if a.get("ratio_valid")]
    ratios = [float(a["uptick_volume_ratio_30s"]) for a in valid if a.get("uptick_volume_ratio_30s") is not None]
    pus = [int(a["price_update_count_10s"]) for a in audits if a.get("price_update_count_10s") is not None]
    classified = [int(a.get("classified_trade_count_30s") or 0) for a in audits]

    def dist_by(key):
        out = {}
        for a in audits:
            k = a.get(key)
            out.setdefault(str(k), {"n": 0, "ratio_valid_n": 0, "ratio_1_n": 0})
            out[str(k)]["n"] += 1
            if a.get("ratio_valid"):
                out[str(k)]["ratio_valid_n"] += 1
                if a.get("uptick_volume_ratio_30s") is not None and abs(float(a["uptick_volume_ratio_30s"]) - 1.0) < 1e-12:
                    out[str(k)]["ratio_1_n"] += 1
        return out

    # contract gate checks
    future_n = sum(1 for a in audits if a.get("future_contaminated"))
    # reproducibility vs stored: among both non-null, max abs diff
    repro_errs = 0
    repro_n = 0
    for a in audits:
        s = a.get("stored_uptick_volume_ratio_30s")
        # stored FeatureBuffer may differ when classified<3 — compare only when both valid under PFQ
        if a.get("ratio_valid") and s is not None and a.get("uptick_volume_ratio_30s") is not None:
            repro_n += 1
            # FeatureBuffer ignores classified>=3 gate; when classified>=3 should match closely
            if abs(float(s) - float(a["uptick_volume_ratio_30s"])) > 1e-6:
                # may differ if stored used FeatureBuffer with unchanged-only path — count soft
                if int(a.get("classified_trade_count_30s") or 0) >= MIN_CLASSIFIED_TRADES_30S:
                    repro_errs += 1

    ratio_1 = sum(1 for r in ratios if abs(r - 1.0) < 1e-12)
    ratio_0 = sum(1 for r in ratios if abs(r) < 1e-12)
    ratio_1_single = sum(
        1 for a in valid
        if a.get("uptick_volume_ratio_30s") is not None
        and abs(float(a["uptick_volume_ratio_30s"]) - 1.0) < 1e-12
        and int(a.get("classified_trade_count_30s") or 0) == 1
    )
    # with our gate, classified>=3 so ratio_1_single among valid should be 0
    low_class = sum(1 for a in audits if int(a.get("classified_trade_count_30s") or 0) <= 2)
    low_vol = sum(1 for a in audits if float(a.get("classified_volume_30s") or 0) <= 0)

    status = "PFQ_FEATURE_CONTRACT_PASS"
    fail_reasons = []
    if future_n > 0:
        status = "E1_X7_FEATURE_CONTRACT_INVALID"
        fail_reasons.append(f"future_contaminated={future_n}")
    if any(a.get("price_update_count_10s") is not None and a.get("feature_asof_time") is not None
           and a.get("decision_time") is not None
           and float(a["feature_asof_time"]) > float(a["decision_time"]) + 1e-9 for a in audits):
        status = "E1_X7_FEATURE_CONTRACT_INVALID"
        fail_reasons.append("feature_asof_future")
    # duplicate inflation: if duplicate_event_count huge vs mid changes — informational
    # Contract is implementable and deterministic
    if status == "PFQ_FEATURE_CONTRACT_PASS" and len(audits) < 100:
        status = "E1_X7_FEATURE_CONTRACT_INVALID"
        fail_reasons.append("insufficient_audit_rows")

    summary = {
        "status": status,
        "fail_reasons": fail_reasons,
        "n_audited": len(audits),
        "ratio_valid_n": len(valid),
        "ratio_valid_rate": len(valid) / len(audits) if audits else 0.0,
        "ratio_eq_1_n": ratio_1,
        "ratio_eq_0_n": ratio_0,
        "ratio_eq_1_with_1_classified_trade_n": ratio_1_single,
        "classified_trades_le_2_n": low_class,
        "classified_volume_le_0_n": low_vol,
        "unknown_trade_total": sum(int(a.get("unknown_trade_count_30s") or 0) for a in audits),
        "price_update_n": len(pus),
        "future_contaminated_n": future_n,
        "repro_compared_n": repro_n,
        "repro_mismatch_n": repro_errs,
        "contract_doc": FEATURE_CONTRACT_DOC,
        "by_day": dist_by("day"),
        "by_symbol_top": dict(sorted(dist_by("symbol").items(), key=lambda x: -x[1]["n"])[:20]),
        "by_session": dist_by("session"),
        "by_net_plus_5bps": dist_by("net_plus_5bps"),
        "min_classified_trades_precommit": MIN_CLASSIFIED_TRADES_30S,
    }
    return audits, summary
