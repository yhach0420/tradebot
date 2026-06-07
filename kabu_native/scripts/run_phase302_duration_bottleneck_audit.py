#!/usr/bin/env python3
"""
Phase302: Duration:high bottleneck audit for 20260604/20260605 live sessions.

Review only — no production/cutoff changes.
Output: kabu_native/results/reports/phase302_duration_bottleneck_audit.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase302_duration_bottleneck_audit.json"

TARGET_DAYS = ("20260604", "20260605")
DURATION_P66 = 406.0
MAX_EVENT_FILE_BYTES = 300_000_000
JST = ZoneInfo("Asia/Tokyo")

TOKEN_LABELS = ("HBRecent", "Duration", "Momentum", "Price", "TV", "Board")
MISS_REASON_LABELS = (
    "Duration_insufficient",
    "Momentum_insufficient",
    "Price_insufficient",
    "TV_insufficient",
    "Board_insufficient",
    "HBRecent_insufficient",
    "multi_factor_insufficient",
)


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


class PriceRingTracker:
    def __init__(self) -> None:
        self.rings: dict[str, list[tuple[float, float]]] = {}

    def observe(self, ev: dict[str, Any], p270: Any) -> None:
        from small_paper.extended_entry_shadow import append_price_tick
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        append_price_tick(self.rings.setdefault(sym, []), ts=ts, px=px)

    def hbrecent(self, ev: dict[str, Any], p270: Any) -> bool:
        from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        if not sym or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload={"CurrentPrice": px},
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _board_from_event(ev: dict[str, Any]) -> Optional[float]:
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field

    payload: dict[str, Any] = {}
    for key in ("BidQty", "AskQty"):
        if ev.get(key) is not None:
            payload[key] = ev[key]
    if payload:
        return compute_entry_order_book_imbalance_field(payload=payload).get("entry_order_book_imbalance")
    logged = ev.get("entry_order_book_imbalance")
    if logged is None:
        return None
    try:
        return float(logged)
    except (TypeError, ValueError):
        return None


def _active_tokens(
    work: dict[str, Any],
    score_points: dict[str, int],
    *,
    duration_p66: float,
) -> dict[str, bool]:
    from small_paper.entry_expectancy_score_shadow import TERTILE_CUTOFFS, _bin_tertile, _float, _feature_token

    active: dict[str, bool] = {}
    for token, pts in score_points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        if lbl == "HBRecent":
            hb = work.get("entry_high_break_recent")
            if hb is None:
                active[token] = False
                continue
            tok = f"HBRecent:{'yes' if str(hb).lower() in ('true', '1', 'yes') else 'no'}"
        elif lbl == "Duration":
            v = _float(work.get("max_continuation_duration"))
            if v is None:
                active[token] = False
                continue
            cuts = TERTILE_CUTOFFS["Duration"]
            level = _bin_tertile(v, cuts["p33"], duration_p66)
            tok = f"Duration:{level}"
        else:
            tok = _feature_token(lbl, work)
        active[token] = tok == token
    return active


def _score_from_active(active: dict[str, bool], score_points: dict[str, int]) -> int:
    return sum(score_points[t] for t, on in active.items() if on)


def _classify_miss(
    *,
    score_full: int,
    score_wo_dur: int,
    active_full: dict[str, bool],
    active_wo: dict[str, bool],
) -> str:
    if score_full >= 5:
        return "reached_score5"
    if score_wo_dur >= 3 and not active_full.get("Duration:high", False):
        return "Duration_insufficient"

    missing: list[str] = []
    checks = [
        ("HBRecent_insufficient", "HBRecent:no"),
        ("Momentum_insufficient", "Momentum:low"),
        ("Price_insufficient", "Price:high"),
        ("TV_insufficient", "TV:mid"),
        ("Board_insufficient", "Board:mid"),
    ]
    for reason, token in checks:
        if not active_wo.get(token, False):
            missing.append(reason)
    if len(missing) >= 2:
        return "multi_factor_insufficient"
    if missing:
        return missing[0]
    if not active_full.get("Duration:high", False):
        return "Duration_insufficient"
    return "multi_factor_insufficient"


def _audit_session(events: list[dict[str, Any]], p270: Any, *, day: str, sid: str) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    score_points_full = dict(SCORE_POINTS_V2)
    score_points_wo = {k: v for k, v in SCORE_POINTS_V2.items() if k != "Duration:high"}

    ring = PriceRingTracker()
    wo_dist: Counter[int] = Counter()
    full_dist: Counter[int] = Counter()
    miss_reasons: Counter[str] = Counter()
    duration_vals: list[float] = []
    symbol_wo34: Counter[str] = Counter()
    symbol_would5: Counter[str] = Counter()

    wo_34_count = 0
    would5_with_duration = 0
    full_score5 = 0
    duration_high_hits = 0
    decision_pool = 0
    v2_reject_pool = 0

    for ev in sorted(
        events,
        key=lambda e: (
            p270._parse_ts(str(e.get("event_time") or "")),
            int(p270._float(e.get("message_index")) or 0),
        ),
    ):
        ring.observe(ev, p270)
        if not p270._in_decision_pool(ev):
            continue
        decision_pool += 1
        gr = str(ev.get("gate_reject_reason") or "")
        et = str(ev.get("event_type") or "")
        if et == "rejected" and gr != "entry_score_v2_below_threshold":
            continue
        if et == "rejected":
            v2_reject_pool += 1

        work = dict(ev)
        work["entry_high_break_recent"] = ring.hbrecent(work, p270)
        imb = _board_from_event(ev)
        if imb is not None:
            work["entry_order_book_imbalance"] = imb

        active_full = _active_tokens(work, score_points_full, duration_p66=DURATION_P66)
        active_wo = _active_tokens(work, score_points_wo, duration_p66=DURATION_P66)
        score_full = _score_from_active(active_full, score_points_full)
        score_wo = _score_from_active(active_wo, score_points_wo)

        wo_dist[score_wo] += 1
        full_dist[score_full] += 1

        dv = work.get("max_continuation_duration")
        try:
            if dv is not None:
                duration_vals.append(float(dv))
        except (TypeError, ValueError):
            pass

        sym = str(ev.get("symbol") or "")
        if score_wo in (3, 4):
            wo_34_count += 1
            if sym:
                symbol_wo34[sym] += 1
        if score_wo >= 3 and not active_full.get("Duration:high", False):
            would5_with_duration += 1
            if sym:
                symbol_would5[sym] += 1
        if score_full >= 5:
            full_score5 += 1
        if active_full.get("Duration:high", False):
            duration_high_hits += 1

        reason = _classify_miss(
            score_full=score_full,
            score_wo_dur=score_wo,
            active_full=active_full,
            active_wo=active_wo,
        )
        if score_full < 5:
            miss_reasons[reason] += 1

    def _pct(vals: list[float], p: float) -> Optional[float]:
        if not vals:
            return None
        s = sorted(vals)
        idx = min(len(s) - 1, int(len(s) * p / 100.0))
        return round(float(s[idx]), 4)

    return {
        "session_id": sid,
        "day": day,
        "decision_pool_events": decision_pool,
        "v2_reject_pool_events": v2_reject_pool,
        "score_without_duration_distribution": {str(k): v for k, v in sorted(wo_dist.items()) if k <= 4},
        "score_full_distribution": {str(k): v for k, v in sorted(full_dist.items())},
        "score_without_duration_3_or_4_count": wo_34_count,
        "would_reach_score5_if_duration_high_added": would5_with_duration,
        "full_score5_count": full_score5,
        "score5_miss_reason_counts": dict(miss_reasons),
        "duration_stats": {
            "count": len(duration_vals),
            "max": round(max(duration_vals), 4) if duration_vals else None,
            "p95": _pct(duration_vals, 95),
            "above_cutoff_406": sum(1 for v in duration_vals if v > DURATION_P66),
            "duration_high_token_hits": duration_high_hits,
        },
        "top_symbols_score_without_duration_3_4": [
            {"symbol": s, "count": c} for s, c in symbol_wo34.most_common(15)
        ],
        "top_symbols_would_reach_5_with_duration": [
            {"symbol": s, "count": c} for s, c in symbol_would5.most_common(15)
        ],
        "_wo_dist": wo_dist,
        "_full_dist": full_dist,
        "_miss_reasons": miss_reasons,
        "_duration_high_hits": duration_high_hits,
    }


def main() -> int:
    p270 = _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions_out: list[dict[str, Any]] = []
    agg_wo = Counter()
    agg_full = Counter()
    agg_miss = Counter()
    agg_wo34 = 0
    agg_would5 = 0
    agg_full5 = 0
    agg_v2_reject = 0
    agg_decision = 0
    agg_duration_above = 0
    agg_duration_count = 0
    symbol_wo34: Counter[str] = Counter()
    symbol_would5: Counter[str] = Counter()

    for day in TARGET_DAYS:
        day_dir = SMALL_PAPER / day
        if not day_dir.is_dir():
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir() or "live_session" not in sess.name.lower():
                continue
            ev_path = sess / "small_paper_events.jsonl"
            if not ev_path.is_file():
                continue
            if ev_path.stat().st_size > MAX_EVENT_FILE_BYTES:
                continue
            events = p270._load_events(sess)
            if not events:
                continue
            sid = sess.relative_to(SMALL_PAPER).as_posix()
            row = _audit_session(events, p270, day=day, sid=sid)
            row.pop("_duration_high_hits", None)
            sessions_out.append({k: v for k, v in row.items() if not k.startswith("_")})
            agg_wo.update(row["_wo_dist"])
            agg_full.update(row["_full_dist"])
            agg_miss.update(row["_miss_reasons"])
            agg_wo34 += row["score_without_duration_3_or_4_count"]
            agg_would5 += row["would_reach_score5_if_duration_high_added"]
            agg_full5 += row["full_score5_count"]
            agg_v2_reject += row["v2_reject_pool_events"]
            agg_decision += row["decision_pool_events"]
            agg_duration_above += row["duration_stats"]["duration_high_token_hits"]
            agg_duration_count += row["duration_stats"]["count"] or 0
            for item in row["top_symbols_score_without_duration_3_4"]:
                symbol_wo34[item["symbol"]] += item["count"]
            for item in row["top_symbols_would_reach_5_with_duration"]:
                symbol_would5[item["symbol"]] += item["count"]

    wo34 = agg_wo34
    would5 = agg_would5
    duration_bottleneck_share = round(would5 / max(1, agg_v2_reject), 4)
    wo34_duration_share = round(would5 / max(1, wo34), 4) if wo34 else 0.0

    report = {
        "phase": 302,
        "title": "duration_bottleneck_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; duration cutoff 406 unchanged; HBRecent+Board pregate logic applied",
        "target_days": list(TARGET_DAYS),
        "method": {
            "pool": "decision_pool ∩ (accepted | entry_score_v2_below_threshold reject)",
            "score_without_duration": "SCORE_POINTS_V2 excluding Duration:high (+2)",
            "duration_cutoff_p66": DURATION_P66,
            "hbrecent_pregate": "price_ring compute_entry_high_break_recent_field",
            "board_pregate": "BidQty/AskQty from event if present else logged imbalance",
        },
        "1_score_without_duration_distribution": {
            str(k): agg_wo.get(k, 0) for k in range(5)
        },
        "2_score_without_duration_3_or_4_count": wo34,
        "3_would_reach_score5_if_duration_high_added": would5,
        "4_score5_miss_reason_classification": {
            "counts": dict(agg_miss),
            "definitions": {
                "Duration_insufficient": "score_without_duration>=3 and Duration:high false (would reach >=5 with +2)",
                "Momentum_insufficient": "primary missing Momentum:low among non-duration misses",
                "Price_insufficient": "primary missing Price:high",
                "TV_insufficient": "primary missing TV:mid",
                "Board_insufficient": "primary missing Board:mid",
                "HBRecent_insufficient": "primary missing HBRecent:no",
                "multi_factor_insufficient": "multiple non-duration tokens missing or score_without_duration<3",
            },
        },
        "5_top_symbols_score_without_duration_3_4": [
            {"symbol": s, "count": c} for s, c in symbol_wo34.most_common(20)
        ],
        "5b_top_symbols_would_reach_5_with_duration": [
            {"symbol": s, "count": c} for s, c in symbol_would5.most_common(20)
        ],
        "aggregate": {
            "sessions": len(sessions_out),
            "decision_pool_events": agg_decision,
            "v2_reject_pool_events": agg_v2_reject,
            "score_full_distribution": {str(k): v for k, v in sorted(agg_full.items())},
            "full_score5_count": agg_full5,
            "duration_high_token_hits": agg_duration_above,
            "duration_field_count": agg_duration_count,
            "would_reach_5_share_of_v2_rejects": duration_bottleneck_share,
            "would_reach_5_share_of_wo_score_3_4": wo34_duration_share,
        },
        "per_session": sessions_out,
        "verdict": {
            "duration_is_primary_bottleneck": would5 > 0 and agg_full5 == 0 and would5 >= wo34 * 0.9,
            "summary": "",
        },
    }

    if agg_full5 == 0 and would5 > 0 and wo34 > 0 and would5 >= wo34 * 0.9:
        report["verdict"]["summary"] = (
            f"score5=0 on target days. Among v2_reject pool ({agg_v2_reject}), "
            f"score_without_duration 3/4={wo34}; Duration+2 would lift {would5} to >=5 "
            f"({wo34_duration_share:.1%} of 3/4 band). Duration cutoff 406 is primary bottleneck."
        )
    elif wo34 == 0:
        report["verdict"]["summary"] = (
            "score_without_duration rarely reaches 3-4; score composition insufficient before Duration."
        )
    else:
        report["verdict"]["summary"] = (
            f"Mixed: wo_score_3_4={wo34}, would_reach_5_with_duration={would5}, "
            f"full_score5={agg_full5}. Duration is major factor but not sole gap."
        )

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(
        f"wo34={wo34} would5={would5} full5={agg_full5} duration_hits={agg_duration_above}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
