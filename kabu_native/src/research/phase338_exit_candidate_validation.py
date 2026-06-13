"""
Phase338: Multi-session validation for top Phase337 EXIT candidates (incremental aggregation).
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

from research.phase337_exit_candidate_evaluation import (
    CONCENTRATION_THRESHOLD,
    _float,
    _is_concentrated,
    _memory_mb,
    _profit_factor,
    _top_share,
)
from small_paper.exit_candidate_shadow import PHASE338_CANDIDATE_IDS

JST = ZoneInfo("Asia/Tokyo")

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "symbol",
    "position_id",
    "entry_time",
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
    "vwap_coverage_pct",
]


@dataclass
class _CandidateAccum:
    shadow_total_pnl_yen_100: float = 0.0
    gross_profit_yen_100: float = 0.0
    gross_loss_yen_100: float = 0.0
    trigger_count: int = 0
    no_trigger_count: int = 0
    improved_trade_count: int = 0
    worsened_trade_count: int = 0
    stop_hit_reduction_count: int = 0
    profit_take_miss_yen_100: float = 0.0
    improved_session_count: int = 0
    worsened_session_count: int = 0
    unchanged_session_count: int = 0
    symbol_delta_yen: dict[str, float] = field(default_factory=dict)

    def pf(self) -> Optional[float]:
        if self.gross_loss_yen_100 <= 0:
            return None if self.gross_profit_yen_100 <= 0 else float("inf")
        return round(self.gross_profit_yen_100 / self.gross_loss_yen_100, 4)

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
        if (
            row.get("actual_exit_reason") == "stop_hit"
            and not row.get("no_candidate_trigger")
            and delta > 0
        ):
            self.stop_hit_reduction_count += 1
        if actual > 0 and delta < 0:
            self.profit_take_miss_yen_100 += delta
        if sym:
            self.symbol_delta_yen[sym] = round(self.symbol_delta_yen.get(sym, 0.0) + delta, 2)

    def ingest_session_delta(self, session_delta: float) -> None:
        if session_delta > 0:
            self.improved_session_count += 1
        elif session_delta < 0:
            self.worsened_session_count += 1
        else:
            self.unchanged_session_count += 1


def _adoption_checks(
    metrics: Mapping[str, Any],
    *,
    actual_total: float,
    actual_pf: Optional[float],
) -> dict[str, Any]:
    delta = float(metrics.get("delta_yen") or 0)
    pf = metrics.get("profit_factor")
    improved_s = int(metrics.get("improved_session_count") or 0)
    worsened_s = int(metrics.get("worsened_session_count") or 0)
    profit_miss = float(metrics.get("profit_take_miss_yen_100") or 0)
    stop_red = int(metrics.get("stop_hit_reduction_count") or 0)
    concentrated = bool(metrics.get("symbol_concentration"))

    checks = {
        "total_pnl_improved": delta > 0,
        "pf_improved": (
            pf is not None
            and actual_pf is not None
            and pf != float("inf")
            and actual_pf != float("inf")
            and float(pf) > float(actual_pf)
        ),
        "session_stability": improved_s >= worsened_s,
        "not_symbol_concentrated": not concentrated,
        "low_profit_take_miss": profit_miss >= -500.0,
        "stop_or_loss_compression": stop_red > 0 or delta > 0,
    }
    adopt_ready = all(checks.values())
    return {"adopt_ready": adopt_ready, "checks": checks}


@dataclass
class Phase338IncrementalAggregator:
    """Streaming multi-session aggregator — does not retain per-trade rows in memory."""

    reports_dir: Path
    candidate_ids: tuple[str, ...] = PHASE338_CANDIDATE_IDS
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    candidate_acc: dict[str, _CandidateAccum] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0
    _trades_header_written: bool = False
    _sessions_header_written: bool = False

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        for cid in self.candidate_ids:
            self.candidate_acc.setdefault(cid, _CandidateAccum())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase338_exit_candidate_validation_summary.json",
            "sessions": self.reports_dir / "phase338_exit_candidate_validation_sessions.csv",
            "trades": self.reports_dir / "phase338_exit_candidate_validation_trades.csv",
            "by_candidate": self.reports_dir
            / "phase338_exit_candidate_validation_by_candidate.csv",
            "by_symbol": self.reports_dir / "phase338_exit_candidate_validation_by_symbol.csv",
        }

    def note_memory(self) -> None:
        self.peak_memory_mb = max(self.peak_memory_mb, _memory_mb())

    def _append_csv(self, path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
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
        vwap_coverage_pct: Optional[float] = None,
        error: str = "",
    ) -> None:
        self.note_memory()
        if error:
            self.sessions_failed += 1
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return

        sid = str(session_meta.get("session_id") or "")
        day_key = str(session_meta.get("day_key") or "")
        seen_positions: dict[str, float] = {}
        for row in trade_rows:
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

        session_actual = round(sum(seen_positions.values()), 2)
        session_by_candidate: dict[str, float] = defaultdict(float)
        enriched_trades: list[dict[str, Any]] = []
        for row in trade_rows:
            cid = str(row.get("candidate_id") or "")
            if cid not in self.candidate_ids:
                continue
            enriched = {**dict(row), "session_id": sid, "day_key": day_key}
            enriched_trades.append(enriched)
            self.candidate_acc[cid].ingest_trade(enriched)
            session_by_candidate[cid] += float(_float(row.get("shadow_pnl_yen_100")) or 0.0)

        for cid in self.candidate_ids:
            session_delta = round(session_by_candidate.get(cid, 0.0) - session_actual, 2)
            self.candidate_acc[cid].ingest_session_delta(session_delta)

        session_row = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
            "vwap_coverage_pct": vwap_coverage_pct,
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
        if self.actual_gross_loss_yen_100 <= 0:
            return None if self.actual_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.actual_gross_profit_yen_100 / self.actual_gross_loss_yen_100, 4)

    def _candidate_metrics(self, cid: str) -> dict[str, Any]:
        acc = self.candidate_acc[cid]
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        sym_abs = {s: abs(v) for s, v in acc.symbol_delta_yen.items() if s}
        concentrated = _is_concentrated(sym_abs, delta)
        top_share = _top_share(sym_abs, delta)
        return {
            "candidate_id": cid,
            "shadow_total_pnl_yen_100": round(acc.shadow_total_pnl_yen_100, 2),
            "delta_yen": delta,
            "profit_factor": acc.pf(),
            "trigger_count": acc.trigger_count,
            "no_trigger_count": acc.no_trigger_count,
            "improved_trade_count": acc.improved_trade_count,
            "worsened_trade_count": acc.worsened_trade_count,
            "stop_hit_reduction_count": acc.stop_hit_reduction_count,
            "profit_take_miss_yen_100": round(acc.profit_take_miss_yen_100, 2),
            "improved_session_count": acc.improved_session_count,
            "worsened_session_count": acc.worsened_session_count,
            "unchanged_session_count": acc.unchanged_session_count,
            "symbol_concentration": concentrated,
            "top_symbol_delta_share": top_share,
        }

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        actual_pf = self.actual_pf()
        candidates = {cid: self._candidate_metrics(cid) for cid in self.candidate_ids}
        adoption: dict[str, Any] = {}
        for cid, met in candidates.items():
            adoption[cid] = _adoption_checks(met, actual_total=actual_total, actual_pf=actual_pf)

        best = max(self.candidate_ids, key=lambda c: candidates[c]["delta_yen"])
        adopt_ready = [cid for cid, a in adoption.items() if a.get("adopt_ready")]

        return {
            "phase": 338,
            "title": "exit_candidate_multi_session_validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "actual_total_pnl_yen_100": actual_total,
            "actual_pf": actual_pf,
            "evaluated_candidates": list(self.candidate_ids),
            "excluded_candidates": [
                "loss_acceleration_exit",
                "board_collapse_profit_exit",
            ],
            "candidates": candidates,
            "adoption_assessment": adoption,
            "adopt_ready_candidates": adopt_ready,
            "best_candidate_by_delta_yen": best,
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Pre-adoption validation only — no production changes",
        }

    def by_candidate_rows(self) -> list[dict[str, Any]]:
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        actual_pf = self.actual_pf()
        rows = []
        for cid in self.candidate_ids:
            met = self._candidate_metrics(cid)
            rows.append({**met, **_adoption_checks(met, actual_total=actual_total, actual_pf=actual_pf)})
        return rows

    def by_symbol_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for cid in self.candidate_ids:
            for sym, delta in sorted(
                self.candidate_acc[cid].symbol_delta_yen.items(),
                key=lambda x: abs(x[1]),
                reverse=True,
            ):
                rows.append(
                    {
                        "candidate_id": cid,
                        "symbol": sym,
                        "candidate_vs_actual_delta_yen": delta,
                    }
                )
        return rows

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()

        by_candidate = self.by_candidate_rows()
        by_symbol = self.by_symbol_rows()

        if by_candidate:
            fields = sorted({k for r in by_candidate for k in r})
            with paths["by_candidate"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in by_candidate:
                    flat = dict(row)
                    checks = flat.pop("checks", None)
                    if isinstance(checks, dict):
                        for ck, cv in checks.items():
                            flat[f"check_{ck}"] = cv
                    w.writerow(flat)

        if by_symbol:
            with paths["by_symbol"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["candidate_id", "symbol", "candidate_vs_actual_delta_yen"])
                w.writeheader()
                for row in by_symbol:
                    w.writerow(row)

        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}


def filter_trade_rows_by_candidate(
    rows: Sequence[Mapping[str, Any]],
    candidate_ids: Sequence[str],
) -> list[dict[str, Any]]:
    allowed = set(candidate_ids)
    return [dict(r) for r in rows if str(r.get("candidate_id") or "") in allowed]
