"""
Phase356: Post-Phase355 EXIT rebaseline — incremental aggregation.

ENTRY population: Phase355 pullback_misread Dynamic40 guard ON (push replay).
Actual EXIT baseline: production Board Dynamic Trailing + hard stop -1.2%.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase336_realtime_board_full_replay import entry_session_bucket
from research.phase337_exit_candidate_evaluation import (
    CONCENTRATION_THRESHOLD,
    _float,
    _is_concentrated,
    _memory_mb,
    _top_share,
)
from small_paper.phase356_exit_rebaseline_pack import PHASE356_EXIT_CANDIDATE_IDS
from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe

JST = ZoneInfo("Asia/Tokyo")
MIN_DAY_KEY = "20260518"
FOCUS_DAY_AM = "20260612"

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "symbol",
    "position_id",
    "entry_time",
    "universe_slot",
    "universe_bucket",
    "candidate_id",
    "shadow_exit_reason",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_pnl_yen_100",
    "candidate_vs_actual_delta_yen",
    "no_candidate_trigger",
]

SESSION_FIELDS = [
    "session_id",
    "day_key",
    "push_dir",
    "positions",
    "push_rows",
    "runtime_sec",
    "actual_total_pnl_yen_100",
]


def _universe_group(row: Mapping[str, Any]) -> str:
    if is_dynamic40_universe(row):
        return "dynamic40"
    slot = str(row.get("universe_slot") or "").lower()
    if slot == "core":
        return "core10"
    return "other"


def _profit_factor_from_gross(gp: float, gl: float) -> Optional[float]:
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


@dataclass
class _CandidateAccum:
    shadow_total_pnl_yen_100: float = 0.0
    gross_profit_yen_100: float = 0.0
    gross_loss_yen_100: float = 0.0
    trigger_count: int = 0
    no_trigger_count: int = 0
    improved_trade_count: int = 0
    worsened_trade_count: int = 0
    stop_hit_count: int = 0
    trailing_mfe_exit_count: int = 0
    stop_hit_reduction_count: int = 0
    profit_take_miss_yen_100: float = 0.0
    improved_session_count: int = 0
    worsened_session_count: int = 0
    unchanged_session_count: int = 0
    symbol_delta_yen: dict[str, float] = field(default_factory=dict)
    by_session_kind: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    by_universe_group: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def pf(self) -> Optional[float]:
        return _profit_factor_from_gross(self.gross_profit_yen_100, self.gross_loss_yen_100)

    def ingest_trade(self, row: Mapping[str, Any]) -> None:
        shadow = float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
        actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
        delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
        sym = str(row.get("symbol") or "")
        self.shadow_total_pnl_yen_100 += shadow
        if shadow > 0:
            self.gross_profit_yen_100 += shadow
        elif shadow < 0:
            self.gross_loss_yen_100 += abs(shadow)
        if row.get("no_candidate_trigger"):
            self.no_trigger_count += 1
        else:
            self.trigger_count += 1
        if delta > 0:
            self.improved_trade_count += 1
        elif delta < 0:
            self.worsened_trade_count += 1
        actual_reason = str(row.get("actual_exit_reason") or "")
        shadow_reason = str(row.get("shadow_exit_reason") or "")
        if actual_reason == "stop_hit":
            self.stop_hit_count += 1
        if shadow_reason == "trailing_mfe_exit" or (
            not row.get("no_candidate_trigger") and "trailing" in shadow_reason
        ):
            self.trailing_mfe_exit_count += 1
        if actual_reason == "stop_hit" and not row.get("no_candidate_trigger") and delta > 0:
            self.stop_hit_reduction_count += 1
        if actual > 0 and delta < 0:
            self.profit_take_miss_yen_100 += delta
        if sym:
            self.symbol_delta_yen[sym] = round(self.symbol_delta_yen.get(sym, 0.0) + delta, 2)
        sk = str(row.get("session_kind") or "other")
        ug = str(row.get("universe_group") or "other")
        self.by_session_kind[sk] = round(self.by_session_kind.get(sk, 0.0) + delta, 2)
        self.by_universe_group[ug] = round(self.by_universe_group.get(ug, 0.0) + delta, 2)

    def ingest_session_delta(self, session_delta: float) -> None:
        if session_delta > 0:
            self.improved_session_count += 1
        elif session_delta < 0:
            self.worsened_session_count += 1
        else:
            self.unchanged_session_count += 1


@dataclass
class Phase356IncrementalAggregator:
    reports_dir: Path
    candidate_ids: tuple[str, ...] = PHASE356_EXIT_CANDIDATE_IDS
    min_day_key: str = MIN_DAY_KEY
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    actual_trailing_mfe_exit_count: int = 0
    candidate_acc: dict[str, _CandidateAccum] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0
    pullback_guard_reject_count_total: int = 0

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        for cid in self.candidate_ids:
            self.candidate_acc.setdefault(cid, _CandidateAccum())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase356_post_phase355_exit_rebaseline_summary.json",
            "sessions": self.reports_dir / "phase356_post_phase355_exit_rebaseline_sessions.csv",
            "trades": self.reports_dir / "phase356_post_phase355_exit_rebaseline_trades.csv",
            "by_candidate": self.reports_dir
            / "phase356_post_phase355_exit_rebaseline_by_candidate.csv",
        }

    def note_memory(self) -> None:
        self.peak_memory_mb = max(self.peak_memory_mb, _memory_mb())

    def _append_csv(
        self,
        path: Path,
        fieldnames: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        if not rows:
            return
        write_header = not path.is_file()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
            if write_header:
                w.writeheader()
            for row in rows:
                w.writerow(row)

    def ingest_session(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        error: str = "",
        session_summary: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.note_memory()
        day_key = str(session_meta.get("day_key") or "")
        if day_key and day_key < self.min_day_key:
            return
        if error and error not in ("", "no_kept_observer_exit_trades"):
            self.sessions_failed += 1
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return

        if not trade_rows:
            if error in ("", "no_kept_observer_exit_trades"):
                return

        sid = str(session_meta.get("session_id") or "")
        if session_summary:
            self.pullback_guard_reject_count_total += int(
                session_summary.get("pullback_misread_dynamic40_reject_count") or 0
            )

        seen_positions: dict[str, float] = {}
        for row in trade_rows:
            if str(row.get("candidate_id") or "") != "current_board_dynamic":
                continue
            pid = str(row.get("position_id") or "")
            if pid and pid not in seen_positions:
                actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
                seen_positions[pid] = actual
                self.positions_evaluated += 1
                self.actual_total_pnl_yen_100 += actual
                if actual > 0:
                    self.actual_gross_profit_yen_100 += actual
                elif actual < 0:
                    self.actual_gross_loss_yen_100 += abs(actual)
                reason = str(row.get("actual_exit_reason") or "")
                if reason == "stop_hit":
                    self.actual_stop_hit_count += 1
                if reason == "trailing_mfe_exit":
                    self.actual_trailing_mfe_exit_count += 1

        session_actual = round(sum(seen_positions.values()), 2)
        session_by_candidate: dict[str, float] = defaultdict(float)
        enriched_trades: list[dict[str, Any]] = []
        for row in trade_rows:
            cid = str(row.get("candidate_id") or "")
            if cid not in self.candidate_ids:
                continue
            entry_time = str(row.get("entry_time") or "")
            enriched = {
                **dict(row),
                "session_id": sid,
                "day_key": day_key,
                "session_kind": entry_session_bucket(entry_time),
                "universe_group": _universe_group(row),
            }
            enriched_trades.append(enriched)
            self.candidate_acc[cid].ingest_trade(enriched)
            session_by_candidate[cid] += float(_float(enriched.get("shadow_pnl_yen_100")) or 0.0)

        for cid in self.candidate_ids:
            session_delta = round(session_by_candidate.get(cid, 0.0) - session_actual, 2)
            self.candidate_acc[cid].ingest_session_delta(session_delta)

        session_row = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
            **{
                f"{cid}_session_delta_yen": round(
                    session_by_candidate.get(cid, 0.0) - session_actual, 2
                )
                for cid in self.candidate_ids
            },
        }
        session_fields = sorted({k for k in session_row})
        self._append_csv(self.paths()["sessions"], session_fields, [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched_trades)

        self.sessions_evaluated += 1
        self.note_memory()

    def actual_pf(self) -> Optional[float]:
        return _profit_factor_from_gross(
            self.actual_gross_profit_yen_100,
            self.actual_gross_loss_yen_100,
        )

    def _candidate_metrics(self, cid: str) -> dict[str, Any]:
        acc = self.candidate_acc[cid]
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        sym_abs = {s: abs(v) for s, v in acc.symbol_delta_yen.items() if s}
        concentrated = _is_concentrated(sym_abs, delta)
        top_share = _top_share(sym_abs, delta)
        return {
            "candidate_id": cid,
            "actual_total_pnl_yen_100": actual_total,
            "shadow_total_pnl_yen_100": round(acc.shadow_total_pnl_yen_100, 2),
            "delta_yen": delta,
            "profit_factor": acc.pf(),
            "trigger_count": acc.trigger_count,
            "no_trigger_count": acc.no_trigger_count,
            "improved_trade_count": acc.improved_trade_count,
            "worsened_trade_count": acc.worsened_trade_count,
            "stop_hit_count": acc.stop_hit_count,
            "trailing_mfe_exit_count": acc.trailing_mfe_exit_count,
            "stop_hit_reduction_count": acc.stop_hit_reduction_count,
            "profit_take_miss": round(acc.profit_take_miss_yen_100, 2),
            "improved_session_count": acc.improved_session_count,
            "worsened_session_count": acc.worsened_session_count,
            "unchanged_session_count": acc.unchanged_session_count,
            "symbol_concentration": concentrated,
            "top_symbol_delta_share": top_share,
            "delta_by_session_kind": dict(acc.by_session_kind),
            "delta_by_universe_group": dict(acc.by_universe_group),
        }

    def _focus_612_am(self) -> dict[str, Any]:
        """Subset metrics for 20260612 AM after Phase355 ENTRY improvement."""
        focus: dict[str, Any] = {}
        trades_path = self.paths()["trades"]
        if not trades_path.is_file():
            return focus
        by_cid: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"actual": 0.0, "shadow": 0.0, "delta": 0.0, "positions": set()}
        )
        with trades_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if str(row.get("day_key") or "") != FOCUS_DAY_AM:
                    continue
                if str(row.get("session_kind") or "") != "am":
                    continue
                cid = str(row.get("candidate_id") or "")
                if cid not in self.candidate_ids:
                    continue
                pid = str(row.get("position_id") or "")
                actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
                shadow = float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
                delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
                bucket = by_cid[cid]
                bucket["shadow"] += shadow
                bucket["delta"] += delta
                if cid == "current_board_dynamic":
                    bucket["actual"] += actual
                    if pid:
                        bucket["positions"].add(pid)

        actual_total = round(float(by_cid.get("current_board_dynamic", {}).get("actual", 0.0)), 2)
        pos_count = len(by_cid.get("current_board_dynamic", {}).get("positions", set()))
        for cid in self.candidate_ids:
            b = by_cid.get(cid, {})
            focus[cid] = {
                "actual_total_pnl_yen_100": actual_total if cid == "current_board_dynamic" else None,
                "shadow_total_pnl_yen_100": round(float(b.get("shadow", 0.0)), 2),
                "delta_yen": round(float(b.get("shadow", 0.0)) - actual_total, 2)
                if cid != "current_board_dynamic"
                else 0.0,
                "positions": pos_count if cid == "current_board_dynamic" else None,
            }
        return focus

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        actual_pf = self.actual_pf()
        candidates = {cid: self._candidate_metrics(cid) for cid in self.candidate_ids}

        shadow_candidates = [c for c in self.candidate_ids if c != "current_board_dynamic"]
        best = max(shadow_candidates, key=lambda c: candidates[c]["delta_yen"])
        adopt_candidates = [
            cid
            for cid in shadow_candidates
            if candidates[cid]["delta_yen"] > 0
            and (
                candidates[cid]["profit_factor"] is None
                or actual_pf is None
                or (
                    candidates[cid]["profit_factor"] != float("inf")
                    and actual_pf != float("inf")
                    and float(candidates[cid]["profit_factor"] or 0) >= float(actual_pf or 0)
                )
            )
            and candidates[cid]["improved_session_count"] >= candidates[cid]["worsened_session_count"]
            and not candidates[cid]["symbol_concentration"]
        ]

        bd_tuning = [c for c in shadow_candidates if c.startswith("bd_")]
        best_bd = max(bd_tuning, key=lambda c: candidates[c]["delta_yen"]) if bd_tuning else None

        return {
            "phase": 356,
            "title": "post_phase355_exit_rebaseline",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "entry_guard": "pullback_misread_dynamic40_guard_enabled",
            "actual_exit_policy": "board_dynamic_trailing_hard_stop_1p2pct",
            "min_day_key": self.min_day_key,
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "pullback_guard_reject_count_total": self.pullback_guard_reject_count_total,
            "actual_total_pnl_yen_100": actual_total,
            "actual_pf": actual_pf,
            "actual_stop_hit_count": self.actual_stop_hit_count,
            "actual_trailing_mfe_exit_count": self.actual_trailing_mfe_exit_count,
            "evaluated_candidates": list(self.candidate_ids),
            "candidates": candidates,
            "best_candidate_by_delta_yen": best,
            "best_board_dynamic_tuning": best_bd,
            "adopt_candidate_shortlist": adopt_candidates,
            "focus_20260612_am": self._focus_612_am(),
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Shadow validation only — Phase355 ENTRY guard ON; no Discord/summary changes",
        }

    def by_candidate_rows(self) -> list[dict[str, Any]]:
        actual_pf = self.actual_pf()
        rows = []
        for cid in self.candidate_ids:
            met = self._candidate_metrics(cid)
            rows.append(
                {
                    **met,
                    "actual_pf": actual_pf,
                    "pf_vs_actual": (
                        round(float(met["profit_factor"] or 0) - float(actual_pf or 0), 4)
                        if met.get("profit_factor") is not None and actual_pf is not None
                        and met["profit_factor"] != float("inf")
                        and actual_pf != float("inf")
                        else None
                    ),
                }
            )
        return rows

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        by_candidate = self.by_candidate_rows()
        if by_candidate:
            fields = sorted({k for r in by_candidate for k in r})
            with paths["by_candidate"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in by_candidate:
                    flat = dict(row)
                    for nested_key in ("delta_by_session_kind", "delta_by_universe_group"):
                        val = flat.pop(nested_key, None)
                        if isinstance(val, dict):
                            for sk, sv in val.items():
                                flat[f"{nested_key}_{sk}"] = sv
                    w.writerow(flat)

        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
