"""
Phase337: Aggregate independent EXIT candidate shadow results across sessions.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.exit_candidate_shadow import EXIT_CANDIDATE_IDS, EXTEND_CANDIDATE_ID

JST = ZoneInfo("Asia/Tokyo")
CONCENTRATION_THRESHOLD = 0.60


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _profit_factor(yens: Sequence[float]) -> Optional[float]:
    wins = sum(y for y in yens if y > 0)
    losses = abs(sum(y for y in yens if y < 0))
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / losses, 4)


def _top_share(abs_map: dict[str, float], total: float) -> Optional[float]:
    if not abs_map or abs(total) < 1e-9:
        return None
    top = max(abs_map.values())
    return round(top / abs(total), 4)


def _is_concentrated(abs_map: dict[str, float], total: float) -> bool:
    share = _top_share(abs_map, total)
    return share is not None and share >= CONCENTRATION_THRESHOLD


def _memory_mb() -> float:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "win32":
            return round(usage / (1024 * 1024), 2)
        return round(usage / 1024, 2)
    except (ImportError, AttributeError, ValueError):
        try:
            import os

            if sys.platform == "win32":
                import ctypes

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.c_ulong),
                        ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                ctypes.windll.psapi.GetProcessMemoryInfo(
                    ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(counters),
                    counters.cb,
                )
                return round(counters.WorkingSetSize / (1024 * 1024), 2)
            return round(os.getpid() and 0.0, 2)
        except (OSError, AttributeError):
            try:
                import tracemalloc

                if not tracemalloc.is_tracing():
                    tracemalloc.start()
                _cur, peak = tracemalloc.get_traced_memory()
                return round(peak / (1024 * 1024), 2)
            except (ImportError, RuntimeError):
                return 0.0


def _classify_delta(delta: float) -> str:
    if delta > 0:
        return "improved"
    if delta < 0:
        return "worsened"
    return "unchanged"


@dataclass
class Phase337Aggregator:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    evaluated_candidates: list[str] = field(default_factory=lambda: list(EXIT_CANDIDATE_IDS))
    skipped_candidates: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0

    def note_memory(self) -> None:
        self.peak_memory_mb = max(self.peak_memory_mb, _memory_mb())

    def add_session_result(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        vwap_coverage_pct: Optional[float] = None,
        error: str = "",
    ) -> None:
        self.note_memory()
        if error:
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return
        sid = str(session_meta.get("session_id") or "")
        day_key = str(session_meta.get("day_key") or "")
        actual_yen = 0.0
        seen_positions: set[str] = set()
        for row in trade_rows:
            pid = str(row.get("position_id") or "")
            if pid and pid not in seen_positions:
                seen_positions.add(pid)
                actual_yen += float(_float(row.get("actual_pnl_yen_100")) or 0.0)
            self.trades.append({**dict(row), "session_id": sid, "day_key": day_key})

        by_candidate: dict[str, float] = defaultdict(float)
        for row in trade_rows:
            cid = str(row.get("candidate_id") or "")
            by_candidate[cid] += float(_float(row.get("shadow_pnl_yen_100")) or 0.0)

        self.sessions.append(
            {
                **dict(session_meta),
                "trades": len(seen_positions),
                "trade_rows": len(trade_rows),
                "push_rows": push_rows,
                "runtime_sec": round(runtime_sec, 1),
                "actual_total_pnl_yen_100": round(actual_yen, 2),
                "vwap_coverage_pct": vwap_coverage_pct,
                **{
                    f"{cid}_shadow_total_pnl_yen_100": round(by_candidate.get(cid, 0.0), 2)
                    for cid in EXIT_CANDIDATE_IDS
                },
            }
        )

    def _candidate_trades(self, candidate_id: str) -> list[dict[str, Any]]:
        return [t for t in self.trades if str(t.get("candidate_id") or "") == candidate_id]

    def _candidate_metrics(self, candidate_id: str, actual_total: float) -> dict[str, Any]:
        rows = self._candidate_trades(candidate_id)
        shadow_yens = [float(_float(r.get("shadow_pnl_yen_100")) or 0) for r in rows]
        actual_yens = [float(_float(r.get("actual_pnl_yen_100")) or 0) for r in rows]
        deltas = [float(_float(r.get("candidate_vs_actual_delta_yen")) or 0) for r in rows]
        shadow_total = round(sum(shadow_yens), 2)
        trigger_count = sum(1 for r in rows if not r.get("no_candidate_trigger"))
        no_trigger_count = sum(1 for r in rows if r.get("no_candidate_trigger"))
        improved = sum(1 for d in deltas if d > 0)
        worsened = sum(1 for d in deltas if d < 0)

        stop_reduction = sum(
            1
            for r in rows
            if r.get("actual_exit_reason") == "stop_hit"
            and not r.get("no_candidate_trigger")
            and float(_float(r.get("candidate_vs_actual_delta_yen")) or 0) > 0
        )

        profit_miss = round(
            sum(
                float(_float(r.get("candidate_vs_actual_delta_yen")) or 0)
                for r in rows
                if float(_float(r.get("actual_pnl_yen_100")) or 0) > 0
                and float(_float(r.get("candidate_vs_actual_delta_yen")) or 0) < 0
            ),
            2,
        )

        return {
            "candidate_id": candidate_id,
            "shadow_total_pnl_yen_100": shadow_total,
            "delta_yen": round(shadow_total - actual_total, 2),
            "profit_factor": _profit_factor(shadow_yens),
            "trigger_count": trigger_count,
            "no_trigger_count": no_trigger_count,
            "improved_trade_count": improved,
            "worsened_trade_count": worsened,
            "stop_hit_reduction_count": stop_reduction,
            "profit_take_miss_yen_100": profit_miss,
        }

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        position_ids = {str(t.get("position_id") or "") for t in self.trades}
        actual_yens = []
        for pid in position_ids:
            if not pid:
                continue
            row = next(
                (t for t in self.trades if t.get("position_id") == pid),
                None,
            )
            if row:
                actual_yens.append(float(_float(row.get("actual_pnl_yen_100")) or 0))
        actual_total = round(sum(actual_yens), 2)

        candidates = {
            cid: self._candidate_metrics(cid, actual_total) for cid in EXIT_CANDIDATE_IDS
        }

        extend_positions = {
            str(t.get("position_id") or "")
            for t in self.trades
            if t.get("strength_hold_extend")
        }
        extend_reason_dist: Counter[str] = Counter()
        extend_pnl_buckets: Counter[str] = Counter()
        for pid in extend_positions:
            row = next((t for t in self.trades if t.get("position_id") == pid), None)
            if not row:
                continue
            extend_reason_dist[str(row.get("actual_exit_reason") or "")] += 1
            pnl = float(_float(row.get("actual_pnl_pct")) or 0)
            if pnl > 0.5:
                extend_pnl_buckets["gt_0.5pct"] += 1
            elif pnl > 0:
                extend_pnl_buckets["gt_0"] += 1
            elif pnl > -0.5:
                extend_pnl_buckets["gt_-0.5pct"] += 1
            else:
                extend_pnl_buckets["le_-0.5pct"] += 1

        daily_delta: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        session_delta: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        symbol_delta: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for cid in EXIT_CANDIDATE_IDS:
            for row in self._candidate_trades(cid):
                d = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0)
                daily_delta[str(row.get("day_key") or "")][cid] += d
                session_delta[str(row.get("session_id") or "")][cid] += d
                symbol_delta[str(row.get("symbol") or "")][cid] += d

        best_cid = max(EXIT_CANDIDATE_IDS, key=lambda c: candidates[c]["delta_yen"])
        worst_cid = min(EXIT_CANDIDATE_IDS, key=lambda c: candidates[c]["delta_yen"])

        vwap_rows = self._candidate_trades("vwap_assisted_loss_exit")
        vwap_triggers = sum(1 for r in vwap_rows if not r.get("no_candidate_trigger"))
        skipped = list(self.skipped_candidates)
        if vwap_triggers == 0 and vwap_rows:
            skipped.append(
                {
                    "candidate_id": "vwap_assisted_loss_exit",
                    "reason": "no_triggers_in_sample",
                    "note": "VWAP requires board deterioration + below VWAP; may need more sessions",
                }
            )

        return {
            "phase": 337,
            "title": "exit_candidate_shadow_experiment_pack",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "sessions_evaluated": len(self.sessions),
            "sessions_failed": len(self.failed_sessions),
            "positions_evaluated": len(position_ids),
            "trade_rows": len(self.trades),
            "actual_total_pnl_yen_100": actual_total,
            "actual_pf": _profit_factor(actual_yens),
            "candidates": candidates,
            "strength_hold_extend": {
                "extend_candidate_count": len(extend_positions),
                "extend_candidate_trade_count": len(extend_positions),
                "actual_exit_reason_distribution": dict(extend_reason_dist),
                "actual_exit_pnl_distribution": dict(extend_pnl_buckets),
            },
            "evaluated_candidates": list(self.evaluated_candidates),
            "skipped_candidates": skipped,
            "best_candidate_by_delta_yen": best_cid,
            "worst_candidate_by_delta_yen": worst_cid,
            "stability": {
                "daily_delta_by_candidate": {
                    day: dict(per_c) for day, per_c in sorted(daily_delta.items())
                },
                "session_delta_by_candidate": {
                    sid: dict(per_c) for sid, per_c in sorted(session_delta.items())
                },
                "symbol_dependency": {
                    sym: dict(per_c)
                    for sym, per_c in sorted(
                        symbol_delta.items(),
                        key=lambda x: abs(sum(x[1].values())),
                        reverse=True,
                    )[:20]
                },
                "symbol_concentration": {
                    cid: {
                        "single_symbol_dominant": _is_concentrated(
                            {s: abs(v.get(cid, 0)) for s, v in symbol_delta.items()},
                            candidates[cid]["delta_yen"],
                        ),
                        "top_symbol_delta_share": _top_share(
                            {s: abs(v.get(cid, 0)) for s, v in symbol_delta.items()},
                            candidates[cid]["delta_yen"],
                        ),
                    }
                    for cid in EXIT_CANDIDATE_IDS
                },
            },
            "comparison_highlights": {
                "stop_hit_reduction_leader": max(
                    EXIT_CANDIDATE_IDS,
                    key=lambda c: candidates[c]["stop_hit_reduction_count"],
                ),
                "lowest_profit_take_miss": min(
                    EXIT_CANDIDATE_IDS,
                    key=lambda c: candidates[c]["profit_take_miss_yen_100"],
                ),
                "pf_leader": max(
                    EXIT_CANDIDATE_IDS,
                    key=lambda c: _float(candidates[c].get("profit_factor")) or -1,
                ),
            },
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Research only — no production adoption; actual EXIT unchanged",
        }

    def by_reason_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for cid in EXIT_CANDIDATE_IDS:
            reason_counts: Counter[str] = Counter()
            for r in self._candidate_trades(cid):
                if not r.get("no_candidate_trigger"):
                    reason_counts[cid] += 1
            actual_stops = sum(
                1
                for r in self._candidate_trades(cid)
                if r.get("actual_exit_reason") == "stop_hit"
            )
            rows.append(
                {
                    "candidate_id": cid,
                    "shadow_trigger_count": int(reason_counts.get(cid, 0)),
                    "actual_stop_hit_count": actual_stops,
                    **self._candidate_metrics(
                        cid,
                        round(
                            sum(
                                float(_float(t.get("actual_pnl_yen_100")) or 0)
                                for t in self._candidate_trades(cid)
                            ),
                            2,
                        ),
                    ),
                }
            )
        return rows


def write_phase337_outputs(
    agg: Phase337Aggregator,
    reports_dir: Path,
) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    summary = agg.build_summary()
    paths = {
        "summary": reports_dir / "phase337_exit_candidate_summary.json",
        "sessions": reports_dir / "phase337_exit_candidate_sessions.csv",
        "trades": reports_dir / "phase337_exit_candidate_trades.csv",
        "by_reason": reports_dir / "phase337_exit_candidate_by_reason.csv",
    }
    paths["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    session_fields = sorted({k for s in agg.sessions for k in s}) if agg.sessions else ["session_id"]
    with paths["sessions"].open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=session_fields, extrasaction="ignore")
        w.writeheader()
        for row in agg.sessions:
            w.writerow(row)

    trade_fields = [
        "session_id",
        "day_key",
        "symbol",
        "position_id",
        "entry_time",
        "candidate_id",
        "shadow_exit_reason",
        "shadow_exit_time",
        "shadow_pnl_yen_100",
        "actual_exit_reason",
        "actual_pnl_yen_100",
        "candidate_vs_actual_delta_yen",
        "no_candidate_trigger",
        "strength_hold_extend",
    ]
    with paths["trades"].open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_fields, extrasaction="ignore")
        w.writeheader()
        for row in agg.trades:
            w.writerow(row)

    reason_rows = agg.by_reason_rows()
    reason_fields = (
        sorted({k for r in reason_rows for k in r}) if reason_rows else ["candidate_id"]
    )
    with paths["by_reason"].open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=reason_fields, extrasaction="ignore")
        w.writeheader()
        for row in reason_rows:
            w.writerow(row)

    sizes = {k: paths[k].stat().st_size for k in paths}
    summary["output_file_sizes_bytes"] = sizes
    paths["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {k: str(v) for k, v in paths.items()}
