#!/usr/bin/env python3
"""
Phase299: post-fix report for Board:mid pre-gate on 20260604/20260605 live sessions.

Re-scores historical events with HBRecent + Board pre-gate logic (Duration unchanged).
Output: kabu_native/results/reports/phase299_board_pregate_fix_report.json
"""

from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase299_board_pregate_fix_report.json"
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
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload={"CurrentPrice": px},
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _board_payload_from_event(ev: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("BidQty", "AskQty"):
        if ev.get(key) is not None:
            payload[key] = ev[key]
    for i in range(1, 11):
        for side in ("Buy", "Sell"):
            k = f"{side}{i}"
            if ev.get(k) is not None:
                payload[k] = ev[k]
    return payload


def _pregate_board(ev: dict[str, Any]) -> tuple[Optional[float], bool]:
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field

    payload = _board_payload_from_event(ev)
    if not payload:
        logged = ev.get("entry_order_book_imbalance")
        if logged is not None:
            try:
                val = float(logged)
            except (TypeError, ValueError):
                return None, False
            from small_paper.board_imbalance_shadow import board_mid_token_active

            return val, board_mid_token_active(val)
        return None, False
    out = compute_entry_order_book_imbalance_field(payload=payload)
    return out.get("entry_order_book_imbalance"), bool(out.get("entry_board_mid_token_active"))


def _score_v2(
    ev: dict[str, Any],
    *,
    hbrecent: Optional[bool],
    imbalance: Optional[float],
) -> int:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    work = dict(ev)
    work["entry_high_break_recent"] = hbrecent
    work["entry_order_book_imbalance"] = imbalance
    return int(compute_entry_expectancy_score_fields(trade=work).get("entry_expectancy_score_v2") or 0)


def _audit_session(events: list[dict[str, Any]], *, fixed: bool) -> dict[str, Any]:
    ring = PriceRingTracker()
    score_dist: Counter[int] = Counter()
    imb_null_rejects = 0
    v2_rejects = 0
    board_mid_hits = 0
    score_delta_vs_hb_only = 0

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
            imb, board_mid = _pregate_board(ev)
            score = _score_v2(ev, hbrecent=hb, imbalance=imb)
            hb_only = _score_v2(ev, hbrecent=hb, imbalance=None)
            if score != hb_only:
                score_delta_vs_hb_only += 1
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                v2_rejects += 1
                if imb is None:
                    imb_null_rejects += 1
                if board_mid:
                    board_mid_hits += 1
        else:
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                v2_rejects += 1
                if ev.get("entry_order_book_imbalance") is None:
                    imb_null_rejects += 1
                score = int(ev.get("entry_expectancy_score_v2") or 0)
                from small_paper.board_imbalance_shadow import board_mid_token_active

                if board_mid_token_active(ev.get("entry_order_book_imbalance")):
                    board_mid_hits += 1
            else:
                score = _score_v2(
                    ev,
                    hbrecent=ev.get("entry_high_break_recent"),
                    imbalance=ev.get("entry_order_book_imbalance"),
                )
        if et in ("rejected", "accepted") or (
            et == "candidate" and gr == "entry_score_v2_below_threshold"
        ):
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                score_dist[score] += 1

    return {
        "v2_reject_count": v2_rejects,
        "reject_entry_order_book_imbalance_null_count": imb_null_rejects,
        "reject_entry_order_book_imbalance_null_pct": round(
            100.0 * imb_null_rejects / v2_rejects, 2
        )
        if v2_rejects
        else 0.0,
        "board_mid_token_hits": board_mid_hits,
        "score_delta_vs_hb_only_pregate": score_delta_vs_hb_only if fixed else None,
        "score_distribution": {str(k): v for k, v in sorted(score_dist.items())},
        "max_score": max(score_dist.keys()) if score_dist else None,
        "score4_count": score_dist.get(4, 0),
        "score5_count": score_dist.get(5, 0),
        "score_ge4_count": sum(v for k, v in score_dist.items() if k >= 4),
        "score_ge5_count": sum(v for k, v in score_dist.items() if k >= 5),
    }


def _run_unit_tests() -> dict[str, Any]:
    suite = unittest.TestLoader().loadTestsFromName(
        "kabu_native.tests.test_phase299_board_pregate_fix"
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
            before_days[day] = {"skipped": True, "reason": "day_dir_missing"}
            after_days[day] = {"skipped": True, "reason": "day_dir_missing"}
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
        if not day_meta:
            before_days[day] = {"skipped": True, "reason": "no_live_sessions"}
            after_days[day] = {"skipped": True, "reason": "no_live_sessions"}
            continue
        before_days[day] = {
            "score_distribution": {str(k): v for k, v in sorted(day_before.items())},
            "max_score": max(day_before.keys()) if day_before else None,
            "score4_count": day_before.get(4, 0),
            "score5_count": day_before.get(5, 0),
            "score_ge4_count": sum(v for k, v in day_before.items() if k >= 4),
            "score_ge5_count": sum(v for k, v in day_before.items() if k >= 5),
            "reject_imb_null_total": sum(
                m["before"]["reject_entry_order_book_imbalance_null_count"] for m in day_meta
            ),
            "board_mid_hits": sum(m["before"]["board_mid_token_hits"] for m in day_meta),
        }
        after_days[day] = {
            "score_distribution": {str(k): v for k, v in sorted(day_after.items())},
            "max_score": max(day_after.keys()) if day_after else None,
            "score4_count": day_after.get(4, 0),
            "score5_count": day_after.get(5, 0),
            "score_ge4_count": sum(v for k, v in day_after.items() if k >= 4),
            "score_ge5_count": sum(v for k, v in day_after.items() if k >= 5),
            "reject_imb_null_total": sum(
                m["after"]["reject_entry_order_book_imbalance_null_count"] for m in day_meta
            ),
            "board_mid_token_hits": sum(m["after"]["board_mid_token_hits"] for m in day_meta),
            "score_delta_vs_hb_only": sum(
                m["after"].get("score_delta_vs_hb_only_pregate") or 0 for m in day_meta
            ),
        }

    total_after_score5 = sum(
        after_days.get(d, {}).get("score5_count", 0) or 0
        for d in TARGET_DAYS
        if not after_days.get(d, {}).get("skipped")
    )

    report = {
        "phase": 299,
        "title": "board_pregate_fix_report",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "implementation": {
            "change": "compute_entry_order_book_imbalance_field before compute_entry_expectancy_score_fields/gate",
            "files": [
                "kabu_native/src/small_paper/board_imbalance_shadow.py",
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
            "pregate_shadow_value_match": unit.get("ok"),
            "reject_imb_null_reduced_on_archived_replay": any(
                (after_days.get(d, {}).get("reject_imb_null_total") or 0)
                < (before_days.get(d, {}).get("reject_imb_null_total") or 0)
                for d in TARGET_DAYS
                if not after_days.get(d, {}).get("skipped")
            ),
            "archived_replay_note": (
                "6/4-6/5 events lack board depth columns unless BidQty/AskQty present; "
                "replay may not recompute board from logs"
            ),
            "board_mid_reflected_in_gate_score_on_replay": any(
                (after_days.get(d, {}).get("score_delta_vs_hb_only") or 0) > 0
                for d in TARGET_DAYS
                if not after_days.get(d, {}).get("skipped")
            ),
            "score_delta_vs_hb_only_on_replay": sum(
                after_days.get(d, {}).get("score_delta_vs_hb_only") or 0
                for d in TARGET_DAYS
                if not after_days.get(d, {}).get("skipped")
            ),
            "score4_restored_via_hbrecent_pregate": any(
                (after_days.get(d, {}).get("score4_count") or 0) > 0
                for d in TARGET_DAYS
                if not after_days.get(d, {}).get("skipped")
            ),
            "score5_restored": total_after_score5 > 0,
            "summary": (
                f"Board pre-gate fix implemented. score5_after={total_after_score5}. "
                "Duration cutoff unchanged."
            ),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"unit_tests ok={unit.get('ok')} score5_after={total_after_score5}", flush=True)
    return 0 if unit.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
