"""
Phase357: Actual EXIT audit on Phase355 post-population (live observer_exit trades).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase336_realtime_board_full_replay import entry_session_bucket
from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe
from small_paper.pullback_misread_entry_guard_shadow import (
    _stream_events_csv,
    enrich_trade_features_for_review,
    would_block_pullback_dynamic40_shadow,
)

JST = ZoneInfo("Asia/Tokyo")

MIN_DAY = "20260529"
MAX_DAY = "20260612"
FOCUS_DAY_AM = "20260612"

CANONICAL_EXIT_BUCKETS = (
    "trailing_mfe_exit",
    "stop_hit",
    "overlap_replaced",
    "push_replay_end",
    "session_end",
    "board_dynamic_exit",
    "other",
)

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "exit_reason_canonical",
    "structural_exit_reason",
    "exit_reason",
    "pnl_pct",
    "pnl_yen_100",
    "peak_mfe_pct",
    "mfe_capture_ratio",
    "mfe_left_pct",
    "trailing_mfe_activated",
    "board_dynamic_trailing_tier",
    "board_dynamic_trailing_activate_pct",
    "board_dynamic_trailing_giveback_frac",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "entry_imbalance_percentile",
    "pullback_guard_would_block",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _universe_group(row: Mapping[str, Any]) -> str:
    if is_dynamic40_universe(row):
        return "dynamic40"
    slot = str(row.get("universe_slot") or "").lower()
    if slot == "core":
        return "core10"
    return "other"


def classify_exit_reason(row: Mapping[str, Any]) -> str:
    """Map observer_exit row to audit bucket."""
    struct = str(row.get("structural_exit_reason") or "").strip()
    exit_r = str(row.get("exit_reason") or "").strip()
    reason = struct or exit_r

    if _bool(row.get("stop_hit")) or reason == "stop_hit":
        return "stop_hit"
    if reason == "trailing_mfe_exit":
        return "trailing_mfe_exit"
    if _bool(row.get("overlap_replaced_review")) or reason == "overlap_replaced_review":
        return "overlap_replaced"
    if reason == "push_replay_end":
        return "push_replay_end"
    if _bool(row.get("session_close")) or reason in (
        "session_end",
        "morning_session_close",
        "afternoon_session_close",
    ):
        return "session_end"
    if "board_dynamic" in reason.lower():
        return "board_dynamic_exit"
    if reason in CANONICAL_EXIT_BUCKETS:
        return reason
    if reason:
        return "other"
    return "other"


def _trade_row_from_exit(
    *,
    session_meta: Mapping[str, Any],
    acc: Mapping[str, str],
    ex: Mapping[str, str],
    universe: Mapping[str, Mapping[str, str]],
    kept: Optional[bool] = None,
) -> dict[str, Any]:
    from small_paper.limit_up_proximity_entry_guard_shadow import _infer_session_kind

    sess_dir = Path(str(session_meta["session_dir"]))
    day_key = str(session_meta.get("day_key") or session_meta.get("day") or "")
    session_kind = str(
        session_meta.get("session_kind")
        or _infer_session_kind(sess_dir, {})
    )
    trade = enrich_trade_features_for_review(acc, ex, universe)
    entry_shadow = {
        "entry_rise_5min_pct": trade.get("entry_rise_5min_pct"),
        "entry_vwap_dev_pct": trade.get("entry_vwap_dev_pct"),
        "universe_slot": trade.get("universe_slot"),
        "source_bucket": trade.get("source_bucket"),
    }
    would_block = would_block_pullback_dynamic40_shadow(entry_shadow)
    ep = _float(ex.get("entry_price")) or _float(trade.get("entry_price"))
    xp = _float(ex.get("exit_price")) or _float(trade.get("exit_price"))
    pnl_pct = _float(ex.get("pnl_pct"))
    yen = round((xp - ep) * 100.0, 2) if ep and xp else _float(trade.get("pnl_yen_100"))
    peak_mfe = _float(ex.get("peak_mfe_pct")) or _float(ex.get("rolling_mfe_pct"))
    capture = None
    mfe_left = None
    if peak_mfe is not None and peak_mfe > 0 and pnl_pct is not None:
        capture = round(pnl_pct / peak_mfe, 4)
        mfe_left = round(peak_mfe - pnl_pct, 4)

    row = {
        **dict(ex),
        "session_id": str(session_meta.get("session_id") or ""),
        "day_key": day_key,
        "session_kind": session_kind,
        "universe_group": _universe_group(trade),
        "universe_slot": trade.get("universe_slot") or "",
        "exit_reason_canonical": classify_exit_reason(ex),
        "pnl_yen_100": yen,
        "pnl_pct": pnl_pct,
        "peak_mfe_pct": peak_mfe,
        "mfe_capture_ratio": capture,
        "mfe_left_pct": mfe_left,
        "hold_sec": _float(ex.get("hold_sec")),
        "pullback_guard_would_block": would_block,
        "kept_in_population": kept if kept is not None else (not would_block),
        "entry_rise_5min_pct": trade.get("entry_rise_5min_pct"),
        "entry_vwap_dev_pct": trade.get("entry_vwap_dev_pct"),
        "entry_imbalance_percentile": _float(
            ex.get("entry_imbalance_percentile") or acc.get("entry_imbalance_percentile")
        ),
    }
    return row


def _load_session_trades(session_meta: Mapping[str, Any], *, reports_dir: Path) -> dict[str, Any]:
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _load_universe,
        _universe_path_for_session,
    )

    sess_dir = Path(str(session_meta["session_dir"]))
    events_path = sess_dir / "small_paper_events.csv"
    if not events_path.is_file():
        return {"error": "missing_events_csv", "kept": [], "excluded": [], "all": []}

    summary = _load_session_summary(sess_dir)
    session_kind = str(session_meta.get("session_kind") or _infer_session_kind(sess_dir, summary))
    day = str(session_meta.get("day_key") or session_meta.get("day") or sess_dir.parent.name)
    universe = _load_universe(_universe_path_for_session(day, session_kind, summary, reports_dir))

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(events_path):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for row in _stream_events_csv(events_path):
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        acc = accepted.get(key, {})
        trade_row = _trade_row_from_exit(
            session_meta={**session_meta, "session_kind": session_kind},
            acc=acc,
            ex=row,
            universe=universe,
        )
        is_blocked = bool(trade_row.get("pullback_guard_would_block"))
        trade_row["kept_in_population"] = not is_blocked
        all_rows.append(trade_row)
        if is_blocked:
            excluded.append(trade_row)
        else:
            kept.append(trade_row)

    return {
        "session_meta": dict(session_meta),
        "session_kind": session_kind,
        "kept": kept,
        "excluded": excluded,
        "all": all_rows,
        "error": "",
    }


def trade_row_would_block(
    acc: Mapping[str, str],
    ex: Mapping[str, str],
    universe: Mapping[str, Mapping[str, str]],
) -> bool:
    trade = enrich_trade_features_for_review(acc, ex, universe)
    return would_block_pullback_dynamic40_shadow(
        {
            "entry_rise_5min_pct": trade.get("entry_rise_5min_pct"),
            "entry_vwap_dev_pct": trade.get("entry_vwap_dev_pct"),
            "universe_slot": trade.get("universe_slot"),
            "source_bucket": trade.get("source_bucket"),
        }
    )


@dataclass
class _ReasonAccum:
    count: int = 0
    total_pnl_yen_100: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    peak_mfe_sum: float = 0.0
    peak_mfe_count: int = 0
    hold_sec_sum: float = 0.0
    hold_sec_count: int = 0
    mfe_left_sum: float = 0.0
    mfe_left_count: int = 0

    def ingest(self, row: Mapping[str, Any]) -> None:
        self.count += 1
        yen = float(_float(row.get("pnl_yen_100")) or 0.0)
        self.total_pnl_yen_100 += yen
        if yen > 0:
            self.gross_profit += yen
        elif yen < 0:
            self.gross_loss += abs(yen)
        peak = _float(row.get("peak_mfe_pct"))
        if peak is not None:
            self.peak_mfe_sum += peak
            self.peak_mfe_count += 1
        hold = _float(row.get("hold_sec"))
        if hold is not None:
            self.hold_sec_sum += hold
            self.hold_sec_count += 1
        mfe_left = _float(row.get("mfe_left_pct"))
        if mfe_left is not None:
            self.mfe_left_sum += mfe_left
            self.mfe_left_count += 1

    def metrics(self) -> dict[str, Any]:
        if self.gross_loss <= 0:
            pf: Optional[float] = None if self.gross_profit <= 0 else float("inf")
        else:
            pf = round(self.gross_profit / self.gross_loss, 4)
        return {
            "count": self.count,
            "total_pnl_yen_100": round(self.total_pnl_yen_100, 2),
            "profit_factor": pf,
            "avg_pnl_yen_100": round(self.total_pnl_yen_100 / self.count, 2) if self.count else 0.0,
            "avg_peak_mfe_pct": round(self.peak_mfe_sum / self.peak_mfe_count, 4)
            if self.peak_mfe_count
            else None,
            "avg_hold_sec": round(self.hold_sec_sum / self.hold_sec_count, 1)
            if self.hold_sec_count
            else None,
            "avg_mfe_left_pct": round(self.mfe_left_sum / self.mfe_left_count, 4)
            if self.mfe_left_count
            else None,
            "share_of_total_pnl": None,
        }


@dataclass
class Phase357ExitAudit:
    reports_dir: Path
    min_day: str = MIN_DAY
    max_day: str = MAX_DAY
    kept_trades: list[dict[str, Any]] = field(default_factory=list)
    excluded_trades: list[dict[str, Any]] = field(default_factory=list)
    sessions_loaded: int = 0

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase357_actual_exit_audit_summary.json",
            "breakdown": self.reports_dir / "phase357_exit_reason_breakdown.csv",
            "stop_hit": self.reports_dir / "phase357_stop_hit_trades.csv",
            "trailing_mfe": self.reports_dir / "phase357_trailing_mfe_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.sessions_loaded += 1
        self.kept_trades.extend(result.get("kept") or [])
        self.excluded_trades.extend(result.get("excluded") or [])

    def _reason_breakdown(self, trades: Sequence[Mapping[str, Any]]) -> dict[str, _ReasonAccum]:
        acc: dict[str, _ReasonAccum] = defaultdict(_ReasonAccum)
        for row in trades:
            bucket = str(row.get("exit_reason_canonical") or "other")
            acc[bucket].ingest(row)
        return acc

    def _dominance_analysis(self, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        total_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades)
        stop_trades = [t for t in trades if t.get("exit_reason_canonical") == "stop_hit"]
        trail_trades = [t for t in trades if t.get("exit_reason_canonical") == "trailing_mfe_exit"]
        stop_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in stop_trades)
        trail_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trail_trades)

        # ENTRY signal: stop_hit with low peak MFE (never went green)
        stop_low_mfe = [t for t in stop_trades if (_float(t.get("peak_mfe_pct")) or 0.0) < 0.3]
        stop_high_mfe = [t for t in stop_trades if (_float(t.get("peak_mfe_pct")) or 0.0) >= 0.3]
        stop_low_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in stop_low_mfe)
        stop_high_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in stop_high_mfe)

        # MFE left on table (exit timing)
        mfe_left_total = sum(
            float(_float(t.get("mfe_left_pct")) or 0.0)
            for t in trades
            if (_float(t.get("peak_mfe_pct")) or 0.0) > 0 and (_float(t.get("pnl_pct")) or 0.0) < 0
        )

        excluded_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.excluded_trades)

        entry_loss_proxy = stop_low_pnl
        exit_loss_proxy = stop_high_pnl + sum(
            float(_float(t.get("pnl_yen_100")) or 0.0)
            for t in trail_trades
            if (_float(t.get("pnl_yen_100")) or 0.0) < 0
        )

        if abs(entry_loss_proxy) >= abs(exit_loss_proxy):
            dominant = "ENTRY"
            rationale = (
                "stop_hit trades with peak_mfe<0.3% dominate loss; "
                "positions rarely went favorable before hard stop."
            )
        else:
            dominant = "EXIT"
            rationale = (
                "stop_hit after meaningful MFE and trailing_mfe losses "
                "dominate; entry had upside but exit path failed to capture."
            )

        return {
            "total_pnl_yen_100": round(total_pnl, 2),
            "stop_hit_pnl_yen_100": round(stop_pnl, 2),
            "trailing_mfe_pnl_yen_100": round(trail_pnl, 2),
            "stop_hit_low_mfe_count": len(stop_low_mfe),
            "stop_hit_low_mfe_pnl_yen_100": round(stop_low_pnl, 2),
            "stop_hit_high_mfe_count": len(stop_high_mfe),
            "stop_hit_high_mfe_pnl_yen_100": round(stop_high_pnl, 2),
            "mfe_left_on_losers_pct_sum": round(mfe_left_total, 2),
            "pullback_excluded_pnl_yen_100": round(excluded_pnl, 2),
            "entry_loss_proxy_yen_100": round(entry_loss_proxy, 2),
            "exit_loss_proxy_yen_100": round(exit_loss_proxy, 2),
            "dominant_driver": dominant,
            "rationale": rationale,
        }

    def build_summary(self) -> dict[str, Any]:
        trades = self.kept_trades
        breakdown = self._reason_breakdown(trades)
        total_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades)

        by_reason: dict[str, Any] = {}
        for bucket in sorted(set(list(breakdown.keys()) + list(CANONICAL_EXIT_BUCKETS))):
            acc = breakdown.get(bucket, _ReasonAccum())
            met = acc.metrics()
            if total_pnl != 0 and acc.count:
                met["share_of_total_pnl"] = round(acc.total_pnl_yen_100 / total_pnl, 4)
            met["share_of_trade_count"] = round(acc.count / len(trades), 4) if trades else 0.0
            by_reason[bucket] = met

        # Board Dynamic = trailing_mfe_exit in production
        trailing_count = by_reason.get("trailing_mfe_exit", {}).get("count", 0)
        board_tier_counts = Counter(
            str(t.get("board_dynamic_trailing_tier") or "unknown")
            for t in trades
            if t.get("exit_reason_canonical") == "trailing_mfe_exit"
        )

        focus_am = [
            t
            for t in trades
            if str(t.get("day_key") or "") == FOCUS_DAY_AM
            and entry_session_bucket(str(t.get("entry_time") or "")) == "am"
        ]
        focus_breakdown = self._reason_breakdown(focus_am)

        non_overlap = [t for t in trades if t.get("exit_reason_canonical") != "overlap_replaced"]
        non_overlap_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in non_overlap)

        stop_sub = self._subgroup_metrics(
            [t for t in trades if t.get("exit_reason_canonical") == "stop_hit"]
        )
        trail_sub = self._subgroup_metrics(
            [t for t in trades if t.get("exit_reason_canonical") == "trailing_mfe_exit"]
        )

        return {
            "phase": 357,
            "title": "actual_exit_audit",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_post_pullback_guard_excluded",
            "date_range": {"min_day": self.min_day, "max_day": self.max_day},
            "sessions_loaded": self.sessions_loaded,
            "trade_count_kept": len(trades),
            "trade_count_excluded_pullback": len(self.excluded_trades),
            "actual_total_pnl_yen_100": round(total_pnl, 2),
            "actual_pf": _pf([float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]),
            "exit_reason_breakdown": by_reason,
            "board_dynamic_note": (
                "Production Board Dynamic Trailing exits are recorded as trailing_mfe_exit; "
                "no separate board_dynamic_exit reason in live events."
            ),
            "board_dynamic_trailing_exit_count": trailing_count,
            "board_dynamic_tier_on_trailing_exits": dict(board_tier_counts),
            "stop_hit_deep_dive": stop_sub,
            "trailing_mfe_deep_dive": trail_sub,
            "focus_20260612_am": {
                "trade_count": len(focus_am),
                "total_pnl_yen_100": round(
                    sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in focus_am), 2
                ),
                "exit_reason_counts": {
                    k: v.count for k, v in sorted(focus_breakdown.items(), key=lambda x: -x[1].count)
                },
            },
            "answers": {
                "q1_majority_exit_reason_by_count": max(
                    breakdown.items(), key=lambda x: x[1].count, default=("none", _ReasonAccum())
                )[0]
                if breakdown
                else "none",
                "q1_majority_loss_driver_by_pnl": min(
                    ((k, v.total_pnl_yen_100) for k, v in breakdown.items() if v.count),
                    key=lambda x: x[1],
                    default=("none", 0.0),
                )[0],
                "q2_board_dynamic_exit_count": trailing_count,
                "q3_stop_hit_count": by_reason.get("stop_hit", {}).get("count", 0),
                "q4_trailing_mfe_count": trailing_count,
                "q5_612_am_distribution": {
                    k: v.count for k, v in focus_breakdown.items() if v.count
                },
            },
            "metrics_excluding_overlap_replaced": {
                "trade_count": len(non_overlap),
                "total_pnl_yen_100": round(non_overlap_pnl, 2),
                "profit_factor": _pf(
                    [float(_float(t.get("pnl_yen_100")) or 0.0) for t in non_overlap]
                ),
                "note": "Phase356 replay baseline used 875 finalized positions (-765,330 yen); "
                "this audit counts all observer_exit rows (1306) including overlap closures.",
            },
            "loss_attribution": self._dominance_analysis(trades),
        }

    def _subgroup_metrics(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"count": 0}
        by_universe: dict[str, _ReasonAccum] = defaultdict(_ReasonAccum)
        by_session: dict[str, _ReasonAccum] = defaultdict(_ReasonAccum)
        by_tier: dict[str, _ReasonAccum] = defaultdict(_ReasonAccum)
        for row in rows:
            by_universe[str(row.get("universe_group") or "other")].ingest(row)
            by_session[str(row.get("session_kind") or "other")].ingest(row)
            tier = str(row.get("board_dynamic_trailing_tier") or "n/a")
            by_tier[tier].ingest(row)
        return {
            "count": len(rows),
            "total_pnl_yen_100": round(
                sum(float(_float(r.get("pnl_yen_100")) or 0.0) for r in rows), 2
            ),
            "avg_peak_mfe_pct": round(
                sum(_float(r.get("peak_mfe_pct")) or 0.0 for r in rows) / len(rows), 4
            ),
            "avg_hold_sec": round(
                sum(_float(r.get("hold_sec")) or 0.0 for r in rows) / len(rows), 1
            ),
            "by_universe_group": {k: v.metrics() for k, v in by_universe.items()},
            "by_session_kind": {k: v.metrics() for k, v in by_session.items()},
            "by_board_dynamic_tier": {k: v.metrics() for k, v in by_tier.items()},
        }

    def breakdown_rows(self) -> list[dict[str, Any]]:
        summary = self.build_summary()
        rows = []
        for reason, met in summary.get("exit_reason_breakdown", {}).items():
            if met.get("count", 0) <= 0:
                continue
            rows.append({"exit_reason_canonical": reason, **met})
        rows.sort(key=lambda r: -int(r.get("count") or 0))
        return rows

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._write_csv(paths["breakdown"], self.breakdown_rows())
        stop_rows = [t for t in self.kept_trades if t.get("exit_reason_canonical") == "stop_hit"]
        trail_rows = [
            t for t in self.kept_trades if t.get("exit_reason_canonical") == "trailing_mfe_exit"
        ]
        self._write_csv(paths["stop_hit"], stop_rows, TRADE_FIELDS)
        self._write_csv(paths["trailing_mfe"], trail_rows, TRADE_FIELDS)
        return {k: str(v) for k, v in paths.items()}

    def _write_csv(
        self,
        path: Path,
        rows: Sequence[Mapping[str, Any]],
        fieldnames: Optional[Sequence[str]] = None,
    ) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fields = list(fieldnames) if fieldnames else sorted({k for r in rows for k in r})
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)
