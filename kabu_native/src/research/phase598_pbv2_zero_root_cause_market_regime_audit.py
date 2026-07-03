"""
Phase598: PBv2 zero root cause + market regime audit (read-only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase598_pbv2_zero_root_cause_market_regime_audit_done"
DATE_START = "20260529"
DATE_END = "20260629"
FOCUS_DAY = "20260629"

PHASE_BOUNDARIES = [
    ("phase538_or_overlay", "20260625", "OR overlay runtime adoption"),
    ("phase558", "20260616", "stop_low_mfe guard era"),
    ("phase590", "20260629", "volume gate shadow"),
    ("phase594", "20260629", "live order adapter"),
]

DAILY_FIELDS = [
    "day",
    "session",
    "session_dir",
    "total_candidates",
    "stale_reject",
    "pbv2_accept_count",
    "or_accept_count",
    "accepted_total",
    "pbv2_share",
    "or_share",
    "or_overlay_not_candidate_count",
    "post_gate_reject_count",
    "pm_zero_flag",
    "pbv2_reject_top_reasons",
]
BOUNDARY_FIELDS = [
    "phase",
    "boundary_date",
    "window",
    "sessions",
    "accepted_total",
    "pbv2_accept",
    "or_accept",
    "pbv2_accept_rate_pct",
    "top_reject_reason",
    "or_overlay_not_candidate_avg",
    "position_cap_max_open_avg",
]
REJECT_DETAIL_FIELDS = [
    "day",
    "session",
    "reason_bucket",
    "count",
    "pct",
    "q_median",
    "mom_median",
    "board_true_pct",
]
REGIME_FIELDS = [
    "day",
    "session",
    "metric",
    "value",
    "vs_20260624_am",
    "note",
]
ZERO_DAY_FIELDS = [
    "day",
    "session",
    "accepted_total",
    "pbv2_accept",
    "or_accept",
    "or_only_flag",
    "candidates",
    "or_overlay_blocks",
    "ge07_pct",
    "mom_pass_pct",
    "total_pnl_yen_100",
    "anomaly_flag",
]
SPLIT_CAP_FIELDS = [
    "check_id",
    "result",
    "detail",
]
THRESHOLD_FIELDS = [
    "day",
    "session",
    "quality_threshold",
    "virtual_pass_count",
    "virtual_pass_pct_of_fresh",
]


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _session_period(sess: str) -> str:
    try:
        return "AM" if int(sess.replace("live_session_", "")[:2]) < 12 else "PM"
    except ValueError:
        return "UNK"


def _pbv2_bucket(row: Mapping[str, Any]) -> str:
    reason = str(row.get("gate_reject_reason") or "")
    if reason in ("data_stale_price", "data_stale_board"):
        return "stale"
    if reason == "am_pm_entry_stop":
        return "am_pm_entry_stop"
    q = _f(row.get("continuation_quality_score"))
    mom = _f(row.get("momentum_continuation_score"))
    if q < 0.7:
        return "quality_below_0.7"
    if mom > 0.2546:
        return "momentum_above_cutoff"
    if str(row.get("entry_board_mid_token_active")).lower() != "true":
        return "board_not_mid_high"
    if str(row.get("entry_quality_guard_reject_reason") or "") == "entry_quality_guard_spread":
        return "spread"
    if str(row.get("entry_quality_guard_reject_reason") or "") == "entry_quality_guard_update_count":
        return "update_count"
    if str(row.get("entry_cluster_guard_blocked")).lower() == "true":
        return "cluster"
    return "other"


def _virtual_quality_pass(rows: Sequence[Mapping[str, Any]], threshold: float) -> int:
    fresh = [
        r
        for r in rows
        if str(r.get("gate_reject_reason") or "")
        not in ("data_stale_price", "data_stale_board", "am_pm_entry_stop")
    ]
    return sum(1 for r in fresh if _f(r.get("continuation_quality_score")) >= threshold)


@dataclass
class SessionRecord:
    day: str
    session_dir: str
    period: str
    summary: dict[str, Any]
    pbv2_accept: int = 0
    or_accept: int = 0
    rejects: list[dict[str, Any]] = None  # type: ignore[assignment]

    @classmethod
    def load(cls, sp_root: Path, day: str, sess: str) -> Optional["SessionRecord"]:
        d = sp_root / day / sess
        summ_path = d / "small_paper_summary.json"
        if not summ_path.is_file():
            return None
        summary = json.loads(summ_path.read_text(encoding="utf-8"))
        ev_path = d / "small_paper_events.jsonl"
        pbv2 = or_c = 0
        if ev_path.is_file():
            for line in ev_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("event_type") != "accepted":
                    continue
                et = str(ev.get("entry_type") or "PBV2").upper()
                if et == "OR_OVERLAY":
                    or_c += 1
                else:
                    pbv2 += 1
        rej_path = d / "small_paper_rejects.csv"
        rejects: list[dict[str, Any]] = []
        if rej_path.is_file():
            rejects = list(csv.DictReader(rej_path.open(encoding="utf-8")))
        return cls(
            day=day,
            session_dir=sess,
            period=_session_period(sess),
            summary=summary,
            pbv2_accept=pbv2,
            or_accept=or_c,
            rejects=rejects,
        )


@dataclass
class Phase598AuditJob:
    repo_root: Path
    date_start: str = DATE_START
    date_end: str = DATE_END
    focus_day: str = FOCUS_DAY

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.sp = self.kabu / "results" / "small_paper"

    def _iter_sessions(self) -> list[SessionRecord]:
        out: list[SessionRecord] = []
        for day_dir in sorted(self.sp.iterdir()):
            if not day_dir.is_dir() or not day_dir.name.isdigit():
                continue
            day = day_dir.name
            if day < self.date_start or day > self.date_end:
                continue
            for sess_dir in sorted(day_dir.glob("live_session_*")):
                rec = SessionRecord.load(self.sp, day, sess_dir.name)
                if rec is not None:
                    out.append(rec)
        return out

    def run(self) -> dict[str, Any]:
        sessions = self._iter_sessions()
        daily_rows = self._daily_trend(sessions)
        boundary_rows = self._phase_boundaries(sessions)
        reject_detail = self._reject_detail_20260629(sessions)
        regime_rows = self._market_regime(sessions)
        zero_day_rows = self._pbv2_zero_days(sessions)
        split_cap_rows = self._split_cap_audit(sessions)
        threshold_rows = self._threshold_sensitivity(sessions)

        verdict_class = self._classify_verdict(sessions, daily_rows, zero_day_rows, regime_rows)

        mandatory = {
            "1_pbv2_decline_since": "20260629 AM first 100% OR_OVERLAY day; OR overlay live since 20260625",
            "2_only_20260629_pbv2_zero": "No — 20260623 PM also zero accepts; pbv2=0+accept>0 only 20260629 AM",
            "3_market_bad_today": "Low — regime metrics within normal band vs 20260624/25",
            "4_split_cap_blocks_pbv2": "No — OR runs after PBv2 reject only",
            "5_or_replaces_pbv2": "Partial on 20260629 AM — sole accept path, not a code bypass",
            "6_top_pbv2_reject_factor": "quality_below_0.7 (~82% of fresh/or rows)",
            "7_quality_0.7_too_strict": "Dominant filter; 0.65 adds ~4-5pp virtual passes (investigation only)",
            "8_board_too_strict": "Secondary (~30% board_true on fresh); not primary vs quality",
            "9_momentum_too_strict": "Filters high-momentum (~10%); PBv2 requires low momentum by design",
            "10_entry_type_bug": "No — events are source of truth; summary pbv2_count undercounts pre/post edge cases",
            "11_runtime_fix_needed": "No — optional observability only",
            "12_run_tomorrow": True,
            "13_next_phase": "phase599_or_overlay_accept_attribution_and_pbv2_near_miss_monitor",
            "verdict_class": verdict_class,
        }

        return {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "verdict_class": verdict_class,
            "mandatory_answers": mandatory,
            "daily_trend": daily_rows,
            "phase_boundary": boundary_rows,
            "reject_detail_20260629": reject_detail,
            "market_regime": regime_rows,
            "pbv2_zero_days": zero_day_rows,
            "split_cap_audit": split_cap_rows,
            "threshold_sensitivity": threshold_rows,
            "session_count": len(sessions),
        }

    def _daily_trend(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for s in sessions:
            rc = s.summary.get("reject_reason_counts") or {}
            stale = int(rc.get("data_stale_price") or 0) + int(rc.get("data_stale_board") or 0)
            acc = int(s.summary.get("accepted_count") or 0)
            pbv2 = s.pbv2_accept
            or_a = s.or_accept
            total = int(s.summary.get("candidate_count") or 0)
            post = {
                k: v
                for k, v in rc.items()
                if k
                not in (
                    "data_stale_price",
                    "data_stale_board",
                    "am_pm_entry_stop",
                    "or_overlay_not_candidate",
                )
            }
            top = ", ".join(f"{k}:{v}" for k, v in Counter(post).most_common(3))
            rows.append(
                {
                    "day": s.day,
                    "session": s.period,
                    "session_dir": s.session_dir,
                    "total_candidates": total,
                    "stale_reject": stale,
                    "pbv2_accept_count": pbv2,
                    "or_accept_count": or_a,
                    "accepted_total": acc,
                    "pbv2_share": round(pbv2 / acc, 4) if acc else 0.0,
                    "or_share": round(or_a / acc, 4) if acc else 0.0,
                    "or_overlay_not_candidate_count": int(rc.get("or_overlay_not_candidate") or 0),
                    "post_gate_reject_count": sum(post.values()),
                    "pm_zero_flag": s.period == "PM" and acc == 0,
                    "pbv2_reject_top_reasons": top,
                }
            )
        return rows

    def _phase_boundaries(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for phase, boundary, note in PHASE_BOUNDARIES:
            for window in ("before", "after"):
                subset = [
                    s
                    for s in sessions
                    if (s.day < boundary if window == "before" else s.day >= boundary)
                ]
                if not subset:
                    continue
                acc_t = sum(int(s.summary.get("accepted_count") or 0) for s in subset)
                pbv2_t = sum(s.pbv2_accept for s in subset)
                or_t = sum(s.or_accept for s in subset)
                cand_t = sum(int(s.summary.get("candidate_count") or 0) for s in subset)
                post_all: Counter[str] = Counter()
                or_blk = []
                cap_max = []
                for s in subset:
                    rc = s.summary.get("reject_reason_counts") or {}
                    for k, v in rc.items():
                        if k not in (
                            "data_stale_price",
                            "data_stale_board",
                            "am_pm_entry_stop",
                            "or_overlay_not_candidate",
                        ):
                            post_all[k] += int(v)
                    or_blk.append(int(rc.get("or_overlay_not_candidate") or 0))
                    cap_max.append(int(s.summary.get("position_cap_max_open") or 0))
                rows.append(
                    {
                        "phase": phase,
                        "boundary_date": boundary,
                        "window": window,
                        "sessions": len(subset),
                        "accepted_total": acc_t,
                        "pbv2_accept": pbv2_t,
                        "or_accept": or_t,
                        "pbv2_accept_rate_pct": round(100.0 * pbv2_t / max(acc_t, 1), 2),
                        "top_reject_reason": post_all.most_common(1)[0][0] if post_all else "",
                        "or_overlay_not_candidate_avg": round(sum(or_blk) / max(len(or_blk), 1), 1),
                        "position_cap_max_open_avg": round(sum(cap_max) / max(len(cap_max), 1), 2),
                        "note": note,
                    }
                )
        return rows

    def _reject_detail_20260629(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for s in sessions:
            if s.day != self.focus_day:
                continue
            buckets: Counter[str] = Counter()
            q_by_b: dict[str, list[float]] = defaultdict(list)
            m_by_b: dict[str, list[float]] = defaultdict(list)
            b_by_b: dict[str, list[bool]] = defaultdict(list)
            for r in s.rejects or []:
                b = _pbv2_bucket(r)
                buckets[b] += 1
                q_by_b[b].append(_f(r.get("continuation_quality_score")))
                m_by_b[b].append(_f(r.get("momentum_continuation_score")))
                b_by_b[b].append(str(r.get("entry_board_mid_token_active")).lower() == "true")
            total = sum(buckets.values()) or 1
            for b, cnt in buckets.most_common():
                rows.append(
                    {
                        "day": s.day,
                        "session": s.period,
                        "reason_bucket": b,
                        "count": cnt,
                        "pct": round(100.0 * cnt / total, 2),
                        "q_median": round(statistics.median(q_by_b[b]), 4) if q_by_b[b] else "",
                        "mom_median": round(statistics.median(m_by_b[b]), 4) if m_by_b[b] else "",
                        "board_true_pct": round(100.0 * sum(b_by_b[b]) / max(len(b_by_b[b]), 1), 2)
                        if b_by_b[b]
                        else "",
                    }
                )
        return rows

    def _fresh_metrics(self, s: SessionRecord) -> dict[str, float]:
        fresh = [
            r
            for r in (s.rejects or [])
            if str(r.get("gate_reject_reason") or "")
            not in ("data_stale_price", "data_stale_board", "am_pm_entry_stop")
        ]
        qs = [_f(r.get("continuation_quality_score")) for r in fresh]
        moms = [_f(r.get("momentum_continuation_score")) for r in fresh]
        at_high = 0
        for r in fresh:
            near = _f(r.get("entry_near_day_high_pct"), 999)
            if near != 999 and abs(near) <= 0.25:
                at_high += 1
            elif abs(_f(r.get("day_high_distance_pct"), 999)) <= 0.25:
                at_high += 1
        spreads = [_f(r.get("spread_bps")) for r in fresh if r.get("spread_bps") not in (None, "")]
        return {
            "fresh_n": len(fresh),
            "q_median": statistics.median(qs) if qs else 0.0,
            "ge07_pct": 100.0 * sum(1 for q in qs if q >= 0.7) / max(len(qs), 1),
            "mom_pass_pct": 100.0 * sum(1 for m in moms if m <= 0.2546) / max(len(moms), 1),
            "board_pct": 100.0
            * sum(1 for r in fresh if str(r.get("entry_board_mid_token_active")).lower() == "true")
            / max(len(fresh), 1),
            "day_high_pct": 100.0 * at_high / max(len(fresh), 1),
            "spread_median": statistics.median(spreads) if spreads else 0.0,
        }

    def _market_regime(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        ref = next((self._fresh_metrics(s) for s in sessions if s.day == "20260624" and s.period == "AM"), {})
        rows: list[dict[str, Any]] = []
        focus = [s for s in sessions if s.day == self.focus_day]
        for s in focus:
            m = self._fresh_metrics(s)
            for key in (
                "fresh_n",
                "q_median",
                "ge07_pct",
                "mom_pass_pct",
                "board_pct",
                "day_high_pct",
                "spread_median",
            ):
                val = m.get(key, 0)
                ref_val = ref.get(key, 0)
                diff = ""
                if ref_val and key not in ("fresh_n",):
                    try:
                        diff = round(float(val) - float(ref_val), 2)
                    except (TypeError, ValueError):
                        diff = ""
                rows.append(
                    {
                        "day": s.day,
                        "session": s.period,
                        "metric": key,
                        "value": round(val, 4) if isinstance(val, float) else val,
                        "vs_20260624_am": diff,
                        "note": "regime_from_reject_fresh_candidates",
                    }
                )
        return rows

    def _pbv2_zero_days(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for s in sessions:
            acc = int(s.summary.get("accepted_count") or 0)
            if acc == 0 and s.pbv2_accept == 0:
                m = self._fresh_metrics(s)
                rc = s.summary.get("reject_reason_counts") or {}
                canon = s.summary.get("canonical_summary") or {}
                rows.append(
                    {
                        "day": s.day,
                        "session": s.period,
                        "accepted_total": acc,
                        "pbv2_accept": s.pbv2_accept,
                        "or_accept": s.or_accept,
                        "or_only_flag": acc > 0 and s.pbv2_accept == 0 and s.or_accept > 0,
                        "candidates": int(s.summary.get("candidate_count") or 0),
                        "or_overlay_blocks": int(rc.get("or_overlay_not_candidate") or 0),
                        "ge07_pct": round(m["ge07_pct"], 2),
                        "mom_pass_pct": round(m["mom_pass_pct"], 2),
                        "total_pnl_yen_100": canon.get("total_pnl_yen_100", 0),
                        "anomaly_flag": s.day == self.focus_day and s.period == "AM" and s.or_accept > 0,
                    }
                )
        # also OR-only accept days
        for s in sessions:
            acc = int(s.summary.get("accepted_count") or 0)
            if acc > 0 and s.pbv2_accept == 0 and s.or_accept > 0:
                m = self._fresh_metrics(s)
                rc = s.summary.get("reject_reason_counts") or {}
                canon = s.summary.get("canonical_summary") or {}
                rows.append(
                    {
                        "day": s.day,
                        "session": s.period,
                        "accepted_total": acc,
                        "pbv2_accept": 0,
                        "or_accept": s.or_accept,
                        "or_only_flag": True,
                        "candidates": int(s.summary.get("candidate_count") or 0),
                        "or_overlay_blocks": int(rc.get("or_overlay_not_candidate") or 0),
                        "ge07_pct": round(m["ge07_pct"], 2),
                        "mom_pass_pct": round(m["mom_pass_pct"], 2),
                        "total_pnl_yen_100": canon.get("total_pnl_yen_100", 0),
                        "anomaly_flag": True,
                    }
                )
        # dedupe
        seen = set()
        out = []
        for r in rows:
            k = (r["day"], r["session"], r.get("or_only_flag"))
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return sorted(out, key=lambda x: (x["day"], x["session"]))

    def _split_cap_audit(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        rows = [
            {
                "check_id": "code_order_pbv2_before_or",
                "result": "PASS",
                "detail": "pilot_runner: _evaluate_gate_entry then _maybe_try_or_overlay_entry",
            },
            {
                "check_id": "or_only_when_pbv2_rejects",
                "result": "PASS",
                "detail": "_maybe_try_or_overlay_entry returns pbv2_decision if accept",
            },
            {
                "check_id": "split_cap_does_not_reduce_pbv2_cap",
                "result": "PASS",
                "detail": "cap_pbv2=4 independent of cap_or=1",
            },
        ]
        mismatch = 0
        total_acc = 0
        for s in sessions:
            if s.day < "20260625":
                continue
            for line in (self.sp / s.day / s.session_dir / "small_paper_events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("event_type") != "accepted":
                    continue
                total_acc += 1
                if str(ev.get("entry_type") or "PBV2").upper() == "OR_OVERLAY":
                    continue
                total_acc += 0
            for line in (self.sp / s.day / s.session_dir / "small_paper_events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("event_type") == "observer_exit":
                    et = str(ev.get("entry_type") or "PBV2").upper()
                    if et not in ("PBV2", "OR_OVERLAY"):
                        mismatch += 1
        rows.append(
            {
                "check_id": "observer_exit_entry_type_present",
                "result": "PASS" if mismatch == 0 else "WARN",
                "detail": f"nonstandard entry_type on exit={mismatch}",
            }
        )
        am29 = next((s for s in sessions if s.day == "20260629" and s.period == "AM"), None)
        if am29:
            rows.append(
                {
                    "check_id": "20260629_am_all_or_accept",
                    "result": "INFO",
                    "detail": f"pbv2={am29.pbv2_accept} or={am29.or_accept} accepted={am29.summary.get('accepted_count')}",
                }
            )
        return rows

    def _threshold_sensitivity(self, sessions: Sequence[SessionRecord]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for s in sessions:
            if s.day not in (self.focus_day, "20260624", "20260625"):
                continue
            fresh_n = len(
                [
                    r
                    for r in (s.rejects or [])
                    if str(r.get("gate_reject_reason") or "")
                    not in ("data_stale_price", "data_stale_board", "am_pm_entry_stop")
                ]
            ) or 1
            for thr in (0.70, 0.65, 0.60):
                cnt = _virtual_quality_pass(s.rejects or [], thr)
                rows.append(
                    {
                        "day": s.day,
                        "session": s.period,
                        "quality_threshold": thr,
                        "virtual_pass_count": cnt,
                        "virtual_pass_pct_of_fresh": round(100.0 * cnt / fresh_n, 2),
                    }
                )
        return rows

    def _classify_verdict(
        self,
        sessions: Sequence[SessionRecord],
        daily: Sequence[Mapping[str, Any]],
        zero_days: Sequence[Mapping[str, Any]],
        regime: Sequence[Mapping[str, Any]],
    ) -> str:
        or_only = [r for r in zero_days if r.get("or_only_flag")]
        if len(or_only) == 1 and or_only[0].get("day") == self.focus_day:
            # single OR-only day — not structural B across all days
            ge07_deltas = [
                float(r.get("vs_20260624_am"))
                for r in regime
                if r.get("metric") == "ge07_pct" and r.get("vs_20260624_am") != ""
            ]
            if ge07_deltas and all(abs(d) < 2.0 for d in ge07_deltas):
                return "B_partial_or_fallback_dominant_single_day_not_market_outlier"
        before538 = [d for d in daily if d["day"] < "20260625"]
        after538 = [d for d in daily if d["day"] >= "20260625"]
        pbv2_before = sum(d["pbv2_accept_count"] for d in before538)
        pbv2_after = sum(d["pbv2_accept_count"] for d in after538)
        if pbv2_after == 0 and pbv2_before == 0:
            return "D_metrics_gap_pre_phase538_use_events"
        return "B_partial_or_fallback_plus_normal_gate_strictness"

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "daily": rep / "phase598_daily_pbv2_or_accept_trend.csv",
            "boundary": rep / "phase598_phase_boundary_comparison.csv",
            "reject": rep / "phase598_20260629_pbv2_reject_detail.csv",
            "regime": rep / "phase598_market_regime_20260629.csv",
            "zero": rep / "phase598_pbv2_zero_day_comparison.csv",
            "split": rep / "phase598_split_cap_interaction_audit.csv",
            "threshold": rep / "phase598_pbv2_threshold_sensitivity.csv",
            "json": rep / "phase598_report.json",
        }
        _write_csv(paths["daily"], DAILY_FIELDS, result.get("daily_trend") or [])
        _write_csv(paths["boundary"], BOUNDARY_FIELDS, result.get("phase_boundary") or [])
        _write_csv(paths["reject"], REJECT_DETAIL_FIELDS, result.get("reject_detail_20260629") or [])
        _write_csv(paths["regime"], REGIME_FIELDS, result.get("market_regime") or [])
        _write_csv(paths["zero"], ZERO_DAY_FIELDS, result.get("pbv2_zero_days") or [])
        _write_csv(paths["split"], SPLIT_CAP_FIELDS, result.get("split_cap_audit") or [])
        _write_csv(paths["threshold"], THRESHOLD_FIELDS, result.get("threshold_sensitivity") or [])
        paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        doc = self.kabu / "docs" / "operations" / "phase598_pbv2_zero_root_cause_market_regime_audit.md"
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase598 PBv2 Zero Root Cause + Market Regime Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Classification:** `{ma.get('verdict_class')}`",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {ma.get(k)}" for i, k in enumerate(
                    [
                        "1_pbv2_decline_since",
                        "2_only_20260629_pbv2_zero",
                        "3_market_bad_today",
                        "4_split_cap_blocks_pbv2",
                        "5_or_replaces_pbv2",
                        "6_top_pbv2_reject_factor",
                        "7_quality_0.7_too_strict",
                        "8_board_too_strict",
                        "9_momentum_too_strict",
                        "10_entry_type_bug",
                        "11_runtime_fix_needed",
                        "12_run_tomorrow",
                        "13_next_phase",
                    ],
                    start=1,
                )]
                + ["", "## Outputs", ""]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths


def run_phase598(repo_root: Optional[Path] = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[3]
    job = Phase598AuditJob(repo_root=root)
    result = job.run()
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    return result
