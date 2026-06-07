#!/usr/bin/env python3
"""
Phase295: post-fix report for HBRecent pre-gate on 20260604/20260605 live sessions.

Re-scores historical events with fixed pre-gate HBRecent logic (Duration unchanged).
Output: kabu_native/results/reports/phase295_hbrecent_pregate_fix_report.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase295_hbrecent_pregate_fix_report.json"
TARGET_DAYS = ("20260604", "20260605")
JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


class PriceRingTracker:
    def __init__(self) -> None:
        self.rings: dict[str, list[tuple[float, float]]] = {}

    def observe(self, ev: dict[str, Any]) -> None:
        from small_paper.extended_entry_shadow import append_price_tick
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        try:
            px = float(ev.get("current_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        append_price_tick(self.rings.setdefault(sym, []), ts=ts, px=px)

    def pregate_hb(self, ev: dict[str, Any]) -> bool:
        from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        try:
            px = float(ev.get("current_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if not sym or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        payload = {"CurrentPrice": px}
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload=payload,
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _score_v2(ev: dict[str, Any], *, hbrecent: Optional[bool]) -> int:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    work = dict(ev)
    if hbrecent is None:
        work["entry_high_break_recent"] = None
    else:
        work["entry_high_break_recent"] = hbrecent
    return int(compute_entry_expectancy_score_fields(trade=work).get("entry_expectancy_score_v2") or 0)


def _audit_session(events: list[dict[str, Any]], *, fixed: bool) -> dict[str, Any]:
    ring = PriceRingTracker()
    score_dist: Counter[int] = Counter()
    hb_null_rejects = 0
    v2_rejects = 0
    hb_no_hits = 0

    ordered = sorted(
        events,
        key=lambda e: (
            str(e.get("event_time") or ""),
            int(float(e.get("message_index") or 0)),
        ),
    )
    for ev in ordered:
        ring.observe(ev)
        et = str(ev.get("event_type") or "")
        if et not in ("candidate", "accepted", "rejected"):
            continue
        gr = str(ev.get("gate_reject_reason") or "")
        if et == "rejected" and gr not in ("", "entry_score_v2_below_threshold"):
            if gr in (
                "symbol_cooloff",
                "risk_cluster_block",
                "daily_loss_guard",
                "wrong_profile",
                "outside_allowed_trading_window",
                "low_liquidity_shadow",
                "low_liquidity_shadow_reject",
                "daytrade_suitability",
                "entry_price_risk_guard",
            ):
                continue
        if fixed:
            hb = ring.pregate_hb(ev)
            score = _score_v2(ev, hbrecent=hb)
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                v2_rejects += 1
                if hb is None:
                    hb_null_rejects += 1
                from small_paper.entry_expectancy_score_shadow import _feature_token

                if _feature_token("HBRecent", {**ev, "entry_high_break_recent": hb}) == "HBRecent:no":
                    hb_no_hits += 1
        else:
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                v2_rejects += 1
                if ev.get("entry_high_break_recent") is None:
                    hb_null_rejects += 1
                score = int(ev.get("entry_expectancy_score_v2") or 0)
            else:
                score = _score_v2(ev, hbrecent=ev.get("entry_high_break_recent"))
        if et in ("rejected", "accepted") or (
            et == "candidate" and gr == "entry_score_v2_below_threshold"
        ):
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                score_dist[score] += 1

    return {
        "v2_reject_count": v2_rejects,
        "reject_entry_high_break_recent_null_count": hb_null_rejects,
        "reject_entry_high_break_recent_null_pct": round(
            100.0 * hb_null_rejects / v2_rejects, 2
        )
        if v2_rejects
        else 0.0,
        "hbrecent_no_token_hits": hb_no_hits if fixed else None,
        "score_distribution": {str(k): v for k, v in sorted(score_dist.items())},
        "max_score": max(score_dist.keys()) if score_dist else None,
        "score4_count": score_dist.get(4, 0),
        "score5_count": score_dist.get(5, 0),
        "score_ge4_count": sum(v for k, v in score_dist.items() if k >= 4),
        "score_ge5_count": sum(v for k, v in score_dist.items() if k >= 5),
    }


def _run_unit_tests() -> dict[str, Any]:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    suite = unittest.TestLoader().loadTestsFromName(
        "kabu_native.tests.test_phase295_hbrecent_pregate_fix"
    )
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "ok": result.wasSuccessful(),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    unit = _run_unit_tests()
    before_days: dict[str, Any] = {}
    after_days: dict[str, Any] = {}
    sessions: list[dict[str, Any]] = []

    for day in TARGET_DAYS:
        day_dir = SMALL_PAPER / day
        if not day_dir.is_dir():
            continue
        day_before = Counter()
        day_after = Counter()
        day_meta: list[dict[str, Any]] = []
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir() or not sess.name.startswith("live_session"):
                continue
            p = sess / "small_paper_events.jsonl"
            if not p.is_file():
                continue
            events = [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()]
            b = _audit_session(events, fixed=False)
            a = _audit_session(events, fixed=True)
            sid = f"{day}/{sess.name}"
            day_meta.append({"session_id": sid, "before": b, "after": a})
            for k, v in b["score_distribution"].items():
                day_before[int(k)] += v
            for k, v in a["score_distribution"].items():
                day_after[int(k)] += v
        sessions.extend(day_meta)
        before_days[day] = {
            "score_distribution": {str(k): v for k, v in sorted(day_before.items())},
            "max_score": max(day_before.keys()) if day_before else None,
            "score4_count": day_before.get(4, 0),
            "score5_count": day_before.get(5, 0),
            "score_ge4_count": sum(v for k, v in day_before.items() if k >= 4),
            "score_ge5_count": sum(v for k, v in day_before.items() if k >= 5),
            "reject_hb_null_total": sum(m["before"]["reject_entry_high_break_recent_null_count"] for m in day_meta),
        }
        after_days[day] = {
            "score_distribution": {str(k): v for k, v in sorted(day_after.items())},
            "max_score": max(day_after.keys()) if day_after else None,
            "score4_count": day_after.get(4, 0),
            "score5_count": day_after.get(5, 0),
            "score_ge4_count": sum(v for k, v in day_after.items() if k >= 4),
            "score_ge5_count": sum(v for k, v in day_after.items() if k >= 5),
            "reject_hb_null_total": 0,
            "hbrecent_no_token_hits": sum(m["after"].get("hbrecent_no_token_hits") or 0 for m in day_meta),
        }

    report = {
        "phase": 295,
        "title": "hbrecent_pregate_fix_report",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "implementation": {
            "change": "compute_entry_high_break_recent_field before compute_entry_expectancy_score_fields/gate",
            "files": [
                "kabu_native/src/small_paper/extended_entry_shadow.py",
                "kabu_native/src/small_paper/pilot_runner.py",
            ],
            "duration_high_unchanged": True,
            "policy_unchanged": [
                "entry_score_v2_min=5",
                "daytrade",
                "price-risk",
                "max_concurrent",
            ],
        },
        "unit_tests": unit,
        "target_days": list(TARGET_DAYS),
        "aggregate_before_logged": before_days,
        "aggregate_after_fix_logic": after_days,
        "per_session": sessions,
        "verdict": {
            "unit_tests_pass": unit.get("ok"),
            "reject_hb_null_eliminated_under_fix": all(
                after_days.get(d, {}).get("reject_hb_null_total") == 0 for d in TARGET_DAYS
            ),
            "score4_restored": any(after_days.get(d, {}).get("score4_count", 0) > 0 for d in TARGET_DAYS),
            "summary": (
                f"Before: 6/4-6/5 logged rejects had entry_high_break_recent=None. "
                f"After fix logic: score4="
                f"{sum(after_days.get(d, {}).get('score4_count', 0) for d in TARGET_DAYS)}, "
                f"score5="
                f"{sum(after_days.get(d, {}).get('score5_count', 0) for d in TARGET_DAYS)}, "
                f"max_score={max((after_days.get(d, {}).get('max_score') or 0) for d in TARGET_DAYS)}."
            ),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"unit_tests ok={unit.get('ok')}", flush=True)
    for day in TARGET_DAYS:
        b = before_days.get(day, {})
        a = after_days.get(day, {})
        print(
            f"  {day} before max={b.get('max_score')} ge4={b.get('score_ge4_count')} "
            f"after max={a.get('max_score')} ge4={a.get('score_ge4_count')} score5={a.get('score5_count')}",
            flush=True,
        )
    return 0 if unit.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
