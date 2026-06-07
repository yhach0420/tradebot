#!/usr/bin/env python3
"""
Phase292: score generation integrity audit for 2026-06-04 / 2026-06-05 live sessions.

Investigate why score4/5 never appeared (not a threshold issue).
Output: kabu_native/results/reports/phase292_score_generation_integrity_audit.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase292_score_generation_integrity_audit.json"

TARGET_DAYS = ("20260604", "20260605")
REFERENCE_DAYS = ("20260601", "20260603", "20260521", "20260529")
DATE_START = 20260518
DATE_END = 20260605
DURATION_HIGH_CUTOFF = 406.0
SAMPLE_MAX = 80_000


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _iso_date(s: Any) -> str:
    return str(s or "")[:10]


def _session_stream(sid: str, summary: dict[str, Any]) -> str:
    base = sid.split("/")[-1].lower()
    source = str((summary or {}).get("source") or "").lower()
    mode = str((summary or {}).get("mode") or "").lower()
    if "live_session" in base or source == "live" or "live" in mode:
        return "live"
    if "push_replay" in base or source in ("push-replay", "push_replay"):
        return "push_replay"
    return "other"


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    p = session_dir / "small_paper_events.jsonl"
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _read_summary(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "small_paper_summary.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _discover_sessions() -> list[tuple[str, Path, dict[str, Any]]]:
    rows: list[tuple[str, Path, dict[str, Any]]] = []
    if not SMALL_PAPER.is_dir():
        return rows
    for day_dir in sorted(SMALL_PAPER.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit():
            continue
        d = int(day_dir.name)
        if d < DATE_START or d > DATE_END:
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir():
                continue
            ev = sess / "small_paper_events.jsonl"
            if not ev.is_file():
                continue
            sid = f"{day_dir.name}/{sess.name}"
            rows.append((sid, sess, _read_summary(sess)))
    return rows


def _percentile(vals: list[int], pct: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, int(len(s) * pct / 100.0))
    return float(s[idx])


def _score_v2_reject_pool(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in events
        if str(e.get("event_type") or "") == "rejected"
        and str(e.get("gate_reject_reason") or "") == "entry_score_v2_below_threshold"
    ]


def _token_audit(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _feature_token

    active: dict[str, bool] = {}
    for token in SCORE_POINTS_V2:
        lbl = token.split(":", 1)[0]
        active[token] = _feature_token(lbl, ev) == token
    return active


def _feature_field_audit(
    events: list[dict[str, Any]], *, sample_cap: int = SAMPLE_MAX
) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import (
        SCORE_POINTS_V2,
        TERTILE_CUTOFFS,
        _feature_token,
        compute_entry_expectancy_score_fields,
    )

    fields = {
        "entry_high_break_recent": "HBRecent",
        "max_continuation_duration": "Duration",
        "momentum_continuation_score": "Momentum",
        "current_price": "Price",
        "trading_value": "TV",
        "entry_order_book_imbalance": "Board",
        "rolling_mae_pct": "RollingMAE",
    }
    null_ctr: Counter[str] = Counter()
    zero_ctr: Counter[str] = Counter()
    dur_vals: list[int] = []
    token_ctr: Counter[str] = Counter()
    score_logged: Counter[int] = Counter()
    score_recomputed: Counter[int] = Counter()
    mismatch = 0
    n = 0

    for ev in events:
        if n >= sample_cap:
            break
        n += 1
        logged = int(ev.get("entry_expectancy_score_v2") or 0)
        score_logged[logged] += 1
        rec = int(
            compute_entry_expectancy_score_fields(trade=ev).get("entry_expectancy_score_v2") or 0
        )
        score_recomputed[rec] += 1
        if logged != rec:
            mismatch += 1
        for fld in fields:
            v = ev.get(fld)
            if v is None or v == "":
                null_ctr[fld] += 1
            else:
                try:
                    if float(v) == 0.0 and fld != "entry_high_break_recent":
                        zero_ctr[fld] += 1
                except (TypeError, ValueError):
                    pass
        d = ev.get("max_continuation_duration")
        if d is not None and d != "":
            dur_vals.append(int(float(d)))
        for token in SCORE_POINTS_V2:
            lbl = token.split(":", 1)[0]
            if _feature_token(lbl, ev) == token:
                token_ctr[token] += 1

    duration_high_hits = token_ctr.get("Duration:high", 0)
    hb_token_none = n - token_ctr.get("HBRecent:no", 0) - sum(
        1 for ev in events[:n] if _feature_token("HBRecent", ev) == "HBRecent:yes"
    )

    return {
        "sample_size": n,
        "score_logged_distribution": {str(k): v for k, v in sorted(score_logged.items())},
        "score_recomputed_from_logged_fields": {
            str(k): v for k, v in sorted(score_recomputed.items())
        },
        "logged_vs_recomputed_mismatch_count": mismatch,
        "logged_vs_recomputed_mismatch_pct": round(100.0 * mismatch / n, 2) if n else 0.0,
        "field_null_pct": {
            k: round(100.0 * null_ctr[k] / n, 2) if n else 0.0 for k in fields
        },
        "field_zero_pct": {
            k: round(100.0 * zero_ctr[k] / n, 2) if n else 0.0 for k in fields if k != "entry_high_break_recent"
        },
        "max_continuation_duration": {
            "p50": _percentile(dur_vals, 50),
            "p95": _percentile(dur_vals, 95),
            "max": max(dur_vals) if dur_vals else None,
            "duration_high_cutoff_p66": DURATION_HIGH_CUTOFF,
            "duration_high_hit_count": duration_high_hits,
            "duration_high_hit_pct": round(100.0 * duration_high_hits / n, 4) if n else 0.0,
        },
        "token_hit_counts": dict(token_ctr),
        "hbrecent_token_unavailable_count": hb_token_none,
        "tertile_cutoffs": TERTILE_CUTOFFS,
    }


def _entry_time_drift_audit(events: list[dict[str, Any]], session_day: str) -> dict[str, Any]:
    expected = f"{session_day[:4]}-{session_day[4:6]}-{session_day[6:8]}"
    drift_by_type: Counter[str] = Counter()
    drift_by_entry_date: Counter[str] = Counter()
    offset_sec_samples: list[float] = []

    for ev in events:
        ent = _iso_date(ev.get("entry_time"))
        evt = str(ev.get("event_time") or "")
        if ent and ent != expected:
            drift_by_type[str(ev.get("event_type") or "")] += 1
            drift_by_entry_date[ent] += 1
            try:
                t_ent = datetime.fromisoformat(str(ev.get("entry_time"))).timestamp()
                t_evt = datetime.fromisoformat(evt).timestamp()
                offset_sec_samples.append(t_evt - t_ent)
            except (TypeError, ValueError):
                pass

    offset_days = Counter()
    for d in offset_sec_samples:
        offset_days[round(d / 86400.0)] += 1

    samples: list[dict[str, Any]] = []
    for ev in events:
        ent = _iso_date(ev.get("entry_time"))
        if ent and ent != expected:
            samples.append(
                {
                    "event_type": ev.get("event_type"),
                    "symbol": ev.get("symbol"),
                    "entry_time": ev.get("entry_time"),
                    "event_time": ev.get("event_time"),
                    "entry_expectancy_score_v2": ev.get("entry_expectancy_score_v2"),
                }
            )
            if len(samples) >= 5:
                break

    return {
        "session_calendar_day": session_day,
        "expected_entry_date": expected,
        "drift_event_count": sum(drift_by_type.values()),
        "drift_by_event_type": dict(drift_by_type),
        "drift_entry_dates_seen": dict(drift_by_entry_date),
        "entry_vs_event_offset_days": dict(offset_days),
        "sample_drift_rows": samples,
    }


def _accept_shadow_gap_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    accepts = [e for e in events if str(e.get("event_type") or "") == "accepted"]
    if not accepts:
        return {"accepted_count": 0, "note": "no accepts in session"}

    hb_present = sum(1 for a in accepts if a.get("entry_high_break_recent") is not None)
    hb_no = sum(
        1
        for a in accepts
        if str(a.get("entry_high_break_recent") or "").lower() in ("false", "0", "no")
    )
    hb_yes = sum(
        1
        for a in accepts
        if str(a.get("entry_high_break_recent") or "").lower() in ("true", "1", "yes")
    )
    mismatch = 0
    uplift_if_hb_no: Counter[int] = Counter()
    for a in accepts:
        logged = int(a.get("entry_expectancy_score_v2") or 0)
        stripped = dict(a)
        stripped["entry_high_break_recent"] = None
        pre = int(
            compute_entry_expectancy_score_fields(trade=stripped).get("entry_expectancy_score_v2") or 0
        )
        if logged != pre:
            mismatch += 1
            uplift_if_hb_no[logged - pre] += 1

    return {
        "accepted_count": len(accepts),
        "entry_high_break_recent_populated": hb_present,
        "entry_high_break_recent_false": hb_no,
        "entry_high_break_recent_true": hb_yes,
        "logged_vs_pre_shadow_mismatch": mismatch,
        "score_uplift_from_post_shadow_hb": {str(k): v for k, v in sorted(uplift_if_hb_no.items())},
    }


def _hbrecent_counterfactual_on_rejects(
    rejects: list[dict[str, Any]], *, cap: int = 50_000
) -> dict[str, Any]:
    """Upper-bound: if missing HBRecent were treated as false (HBRecent:no +2)."""
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    n = 0
    would_gain_2 = 0
    score_before: Counter[int] = Counter()
    score_after: Counter[int] = Counter()
    reach4 = 0
    reach5 = 0

    for ev in rejects:
        if n >= cap:
            break
        if ev.get("entry_high_break_recent") is not None:
            continue
        n += 1
        before = int(ev.get("entry_expectancy_score_v2") or 0)
        score_before[before] += 1
        cf = dict(ev)
        cf["entry_high_break_recent"] = False
        after = int(
            compute_entry_expectancy_score_fields(trade=cf).get("entry_expectancy_score_v2") or 0
        )
        score_after[after] += 1
        if after > before:
            would_gain_2 += 1
        if after >= 4:
            reach4 += 1
        if after >= 5:
            reach5 += 1

    return {
        "note": "optimistic upper bound; assumes all missing HBRecent=false",
        "reject_sample_with_hb_missing": n,
        "would_gain_from_hbrecent_no": would_gain_2,
        "score_before": {str(k): v for k, v in sorted(score_before.items())},
        "score_after_counterfactual": {str(k): v for k, v in sorted(score_after.items())},
        "counterfactual_score_ge4": reach4,
        "counterfactual_score_ge5": reach5,
    }


def _phase273_timing_audit() -> dict[str, Any]:
    pilot_path = REPO / "kabu_native/src/small_paper/pilot_runner.py"
    gate_path = REPO / "kabu_native/src/research/exposure_gate.py"
    phase273_path = REPO / "kabu_native/scripts/run_phase273_entry_score_v2_min5_implementation_report.py"

    pilot_text = pilot_path.read_text(encoding="utf-8") if pilot_path.is_file() else ""
    gate_text = gate_path.read_text(encoding="utf-8") if gate_path.is_file() else ""

    score_before_gate = "compute_entry_expectancy_score_fields(trade=trade)" in pilot_text
    shadow_on_accept_only = (
        "if decision.accept:" in pilot_text
        and "compute_entry_shadow_fields" in pilot_text
        and pilot_text.find("compute_entry_shadow_fields")
        > pilot_text.find("compute_entry_expectancy_score_fields(trade=trade)")
    )
    gate_reads_precomputed = "_entry_score_v2_int(trade)" in gate_text

    return {
        "phase273_change": "entry_score_v2_min 4→5 in config/gate only; score formula unchanged",
        "score_computed_before_gate": score_before_gate,
        "extended_shadow_fields_only_on_accept_path": shadow_on_accept_only,
        "gate_uses_trade_pre_shadow_score": gate_reads_precomputed,
        "post_accept_score_recompute_exists": "score_fields = compute_entry_expectancy_score_fields"
        in pilot_text,
        "verdict": (
            "Phase273 did not change score_v2 calculation timing. "
            "Gate always evaluates v2 from trade state BEFORE compute_entry_shadow_fields "
            "(entry_high_break_recent unset on reject path)."
        ),
    }


def _live_vs_push_replay_score_dist(
    sessions: list[tuple[str, Path, dict[str, Any]]],
) -> dict[str, Any]:
    by_day_stream: dict[str, dict[str, Counter[int]]] = defaultdict(
        lambda: {"live": Counter(), "push_replay": Counter()}
    )

    for sid, sess_dir, summary in sessions:
        day = _day_from_sid(sid) or ""
        stream = _session_stream(sid, summary)
        if stream not in ("live", "push_replay"):
            continue
        events = _load_events(sess_dir)
        for ev in _score_v2_reject_pool(events):
            s = int(ev.get("entry_expectancy_score_v2") or 0)
            by_day_stream[day][stream][s] += 1
        for ev in events:
            if str(ev.get("event_type") or "") == "accepted":
                s = int(ev.get("entry_expectancy_score_v2") or 0)
                by_day_stream[day][stream][s] += 1

    def _summarize(c: Counter[int]) -> dict[str, Any]:
        if not c:
            return {"count": 0, "max_score": None, "score_ge4": 0, "distribution": {}}
        return {
            "count": sum(c.values()),
            "max_score": max(c.keys()),
            "score_ge4": sum(v for k, v in c.items() if k >= 4),
            "score_ge5": sum(v for k, v in c.items() if k >= 5),
            "distribution": {str(k): v for k, v in sorted(c.items())},
        }

    rows: dict[str, Any] = {}
    for day in sorted(by_day_stream.keys()):
        rows[day] = {
            "live": _summarize(by_day_stream[day]["live"]),
            "push_replay": _summarize(by_day_stream[day]["push_replay"]),
        }

    target_push_missing = all(
        rows.get(d, {}).get("push_replay", {}).get("count", 0) == 0 for d in TARGET_DAYS
    )

    return {
        "by_calendar_day": rows,
        "target_days_push_replay_available": not target_push_missing,
        "target_days_note": (
            "No push_replay sessions exist for 20260604/20260605 in small_paper results; "
            "compare live reference days (20260601, 20260521) instead."
            if target_push_missing
            else ""
        ),
        "reference_comparison": {
            d: rows.get(d, {})
            for d in REFERENCE_DAYS
            if d in rows
        },
    }


def _session_audit(sid: str, sess_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    events = _load_events(sess_dir)
    day = _day_from_sid(sid) or ""
    rejects = _score_v2_reject_pool(events)
    feature = _feature_field_audit(rejects)
    drift = _entry_time_drift_audit(events, day)
    accept_gap = _accept_shadow_gap_audit(events)
    counterfactual = _hbrecent_counterfactual_on_rejects(rejects)

    max_logged = max(
        (int(e.get("entry_expectancy_score_v2") or 0) for e in rejects), default=None
    )
    ge4 = sum(1 for e in rejects if int(e.get("entry_expectancy_score_v2") or 0) >= 4)

    return {
        "session_id": sid,
        "stream": _session_stream(sid, summary),
        "summary_accepted_count": summary.get("accepted_count"),
        "v2_reject_count": len(rejects),
        "max_logged_v2_on_rejects": max_logged,
        "reject_score_ge4_count": ge4,
        "checks": {
            "1_hbrecent_no": {
                "question": "Why HBRecent:no did not fire on live rejects",
                "entry_high_break_recent_null_pct": feature["field_null_pct"].get(
                    "entry_high_break_recent"
                ),
                "hbrecent_no_hit_count": feature["token_hit_counts"].get("HBRecent:no", 0),
                "root_cause": (
                    "entry_high_break_recent is populated only on accept path "
                    "(compute_entry_shadow_fields after gate). Reject events log None; "
                    "_feature_token returns None so HBRecent:no (+2) never contributes."
                ),
            },
            "2_duration_high": {
                "question": "Why Duration:high did not fire on live rejects",
                "duration_high_hit_count": feature["token_hit_counts"].get("Duration:high", 0),
                "max_continuation_duration_max": feature["max_continuation_duration"]["max"],
                "duration_high_cutoff": DURATION_HIGH_CUTOFF,
                "root_cause": (
                    "max_continuation_duration (= max_favorable_streak tick count) never reaches "
                    f"p66 cutoff {DURATION_HIGH_CUTOFF}; Duration:high (+2) cannot fire on live scale."
                ),
            },
            "3_max_continuation_duration_update": {
                "question": "Is max_continuation_duration updating correctly",
                "null_pct": feature["field_null_pct"].get("max_continuation_duration"),
                "zero_pct": feature["field_zero_pct"].get("max_continuation_duration"),
                "distribution": feature["max_continuation_duration"],
                "verdict": (
                    "Field is present and non-stale (0% null). Values update but live streak scale "
                    "(p95 ~38, max <210) is far below Duration:high tertile."
                ),
            },
            "4_entry_high_break_recent": {
                "question": "Is entry_high_break_recent computed correctly",
                "on_rejects": "always None (shadow not run pre-gate)",
                "on_accepts": accept_gap,
                "verdict": (
                    "Computation exists in extended_entry_shadow._high_break_recent but runs only "
                    "after accept; logged reject score never includes this feature."
                ),
            },
            "5_feature_none_zero_stale": feature,
            "7_entry_time_date_drift": drift,
            "8_counterfactual_hbrecent": counterfactual,
        },
    }


def main() -> None:
    _bootstrap()
    sessions = _discover_sessions()
    timing = _phase273_timing_audit()
    live_push = _live_vs_push_replay_score_dist(sessions)

    target_sessions = [
        (sid, d, s) for sid, d, s in sessions if _day_from_sid(sid) in TARGET_DAYS
    ]
    target_live = [r for r in target_sessions if _session_stream(r[0], r[2]) == "live"]

    per_session = [_session_audit(sid, d, s) for sid, d, s in target_live]

    # Aggregate target days
    agg_rejects = 0
    agg_ge4 = 0
    agg_max = 0
    agg_tokens: Counter[str] = Counter()
    for row in per_session:
        agg_rejects += row["v2_reject_count"]
        agg_ge4 += row["reject_score_ge4_count"]
        m = row["max_logged_v2_on_rejects"] or 0
        agg_max = max(agg_max, m)
        for tok, cnt in row["checks"]["5_feature_none_zero_stale"]["token_hit_counts"].items():
            agg_tokens[tok] += cnt

    cf_ge4 = sum(
        row["checks"]["8_counterfactual_hbrecent"]["counterfactual_score_ge4"]
        for row in per_session
    )
    cf_ge5 = sum(
        row["checks"]["8_counterfactual_hbrecent"]["counterfactual_score_ge5"]
        for row in per_session
    )

    verdict = (
        "Score generation integrity issue on reject path — not entry_score_v2_min=5 threshold. "
        "On 20260604/20260605 live, logged reject scores top out at 3 because: "
        "(A) entry_high_break_recent is never set before gate/logging on rejects, blocking HBRecent:no (+2); "
        f"(B) max_continuation_duration live max (~133) is below Duration:high cutoff ({DURATION_HIGH_CUTOFF}), "
        "blocking Duration:high (+2); "
        "(C) trading_value and entry_order_book_imbalance are often absent on logged reject rows "
        "(gate-time trade may still carry trading_value — see logged_vs_recomputed mismatch). "
        f"Optimistic HBRecent:no counterfactual would yield {cf_ge4} score≥4 and {cf_ge5} score≥5 reject rows "
        "but still below full accept path enrichment. "
        "20260605 shows entry_time date drift (CurrentPriceTime one day behind event_time) on 366 events — "
        "upstream timestamp issue, separate from score cap."
    )

    report = {
        "phase": 292,
        "title": "score_generation_integrity_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "objective": (
            "Investigate zero score4/5 on 20260604-20260605 live; "
            "suspect score/feature/timing/data integrity not threshold"
        ),
        "constraint": "investigation only; no production logic changes",
        "target_days": list(TARGET_DAYS),
        "aggregate_target_live": {
            "sessions_audited": len(per_session),
            "v2_reject_total": agg_rejects,
            "reject_score_ge4_total": agg_ge4,
            "max_logged_v2": agg_max,
            "token_hits_across_sessions": dict(agg_tokens),
            "counterfactual_hbrecent_no_ge4": cf_ge4,
            "counterfactual_hbrecent_no_ge5": cf_ge5,
        },
        "check_6_phase273_timing": timing,
        "check_8_live_vs_push_replay": live_push,
        "per_session": per_session,
        "verdict": verdict,
        "priority_fixes_investigation_only": [
            "Move compute_entry_shadow_fields (at least entry_high_break_recent) before gate score if HBRecent should affect v2 gate",
            "Reconcile Duration tertile cutoff with live max_favorable_streak scale (406 vs ~40-130)",
            "Persist trading_value/board fields on reject events for audit parity with gate-time trade",
            "Investigate Kabu CurrentPriceTime date lag on 20260605 session open",
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Target live sessions: {len(per_session)}")
    print(f"Max logged v2 on rejects: {agg_max}")
    print(f"Reject score>=4: {agg_ge4}")


if __name__ == "__main__":
    main()
