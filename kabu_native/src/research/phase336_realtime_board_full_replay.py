"""
Phase336: Aggregate Phase335 realtime board shadow across push_jsonl replays.
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

JST = ZoneInfo("Asia/Tokyo")

AM_START = (9, 5)
AM_END = (11, 23)
PM_START = (12, 33)
PM_END = (15, 20)

CONCENTRATION_THRESHOLD = 0.60
NO_SHADOW_EXIT_RATIO_REJECT = 0.50


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


def discover_push_jsonl_sessions(push_root: Path) -> list[dict[str, Any]]:
    """One session per push_jsonl date directory with at least one jsonl file."""
    out: list[dict[str, Any]] = []
    if not push_root.is_dir():
        return out
    for d in sorted(push_root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        files = list(d.glob("*.jsonl"))
        if not files:
            continue
        day_key = d.name.replace("-", "")
        out.append(
            {
                "session_id": f"push_jsonl/{d.name}",
                "push_dir": str(d),
                "day_key": day_key,
                "source": "push_jsonl",
                "symbol_files": len(files),
            }
        )
    return out


def discover_small_paper_push_replay_sessions(small_paper_root: Path) -> list[dict[str, Any]]:
    """Existing push-replay runs under results/small_paper (metadata only)."""
    out: list[dict[str, Any]] = []
    if not small_paper_root.is_dir():
        return out
    for summary_path in sorted(small_paper_root.glob("*/*/small_paper_summary.json")):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(summary.get("source") or "") != "push-replay":
            continue
        push_dir = str(summary.get("push_dir") or "")
        if not push_dir:
            continue
        session_dir = summary_path.parent
        day_part = session_dir.parent.name
        day_key = day_part.replace("-", "") if "-" in day_part else day_part
        sid = f"{day_part}/{session_dir.name}"
        out.append(
            {
                "session_id": sid,
                "push_dir": push_dir,
                "day_key": day_key,
                "source": "small_paper_push_replay",
                "symbol_files": None,
                "existing_session_dir": str(session_dir),
            }
        )
    return out


def entry_session_bucket(entry_time: str) -> str:
    """AM / PM / other from entry timestamp and production windows."""
    try:
        dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        else:
            dt = dt.astimezone(JST)
    except (TypeError, ValueError):
        return "other"
    t = (dt.hour, dt.minute)
    if AM_START <= t <= AM_END:
        return "am"
    if PM_START <= t <= PM_END:
        return "pm"
    return "other"


def _trade_delta_yen(row: Mapping[str, Any]) -> float:
    return float(_float(row.get("realtime_board_vs_actual_delta_yen")) or 0.0)


def _classify_trade(row: Mapping[str, Any]) -> str:
    d = _trade_delta_yen(row)
    if d > 0:
        return "improved"
    if d < 0:
        return "worsened"
    return "unchanged"


@dataclass
class Phase336Aggregator:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)

    def add_session_result(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        error: str = "",
    ) -> None:
        if error:
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return
        day_key = str(session_meta.get("day_key") or "")
        sid = str(session_meta.get("session_id") or "")
        actual_yen = 0.0
        shadow_yen = 0.0
        for row in trade_rows:
            enriched = {
                **dict(row),
                "session_id": sid,
                "day_key": day_key,
                "session_bucket": entry_session_bucket(str(row.get("entry_time") or "")),
            }
            self.trades.append(enriched)
            actual_yen += float(_float(row.get("actual_pnl_yen_100")) or 0.0)
            shadow_yen += float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
        self.sessions.append(
            {
                **dict(session_meta),
                "trades": len(trade_rows),
                "push_rows": push_rows,
                "runtime_sec": round(runtime_sec, 1),
                "actual_total_pnl_yen_100": round(actual_yen, 2),
                "shadow_total_pnl_yen_100": round(shadow_yen, 2),
                "session_delta_yen": round(shadow_yen - actual_yen, 2),
            }
        )

    def build_summary(self) -> dict[str, Any]:
        trades = self.trades
        actual_yens = [float(_float(t.get("actual_pnl_yen_100")) or 0) for t in trades]
        shadow_yens = [float(_float(t.get("shadow_pnl_yen_100")) or 0) for t in trades]
        deltas = [_trade_delta_yen(t) for t in trades]

        actual_total = round(sum(actual_yens), 2)
        shadow_total = round(sum(shadow_yens), 2)
        total_delta = round(shadow_total - actual_total, 2)

        improved = sum(1 for t in trades if _classify_trade(t) == "improved")
        worsened = sum(1 for t in trades if _classify_trade(t) == "worsened")
        unchanged = sum(1 for t in trades if _classify_trade(t) == "unchanged")

        shadow_exit_count = sum(1 for t in trades if not t.get("no_shadow_exit"))
        no_shadow_count = sum(1 for t in trades if t.get("no_shadow_exit"))

        shadow_reason_counts: Counter[str] = Counter()
        for t in trades:
            r = str(t.get("shadow_exit_reason") or "").strip()
            if r:
                shadow_reason_counts[r] += 1

        actual_stop = sum(1 for t in trades if t.get("actual_exit_reason") == "stop_hit")
        shadow_loss_accel = shadow_reason_counts.get("loss_acceleration_exit", 0)

        loss_accel_help = sum(
            1
            for t in trades
            if t.get("shadow_exit_reason") == "loss_acceleration_exit"
            and t.get("actual_exit_reason") == "stop_hit"
            and _trade_delta_yen(t) > 0
        )

        session_deltas = [float(s.get("session_delta_yen") or 0) for s in self.sessions]
        improved_sessions = sum(1 for d in session_deltas if d > 0)
        worsened_sessions = sum(1 for d in session_deltas if d < 0)

        daily_actual: dict[str, float] = defaultdict(float)
        daily_shadow: dict[str, float] = defaultdict(float)
        daily_delta: dict[str, float] = defaultdict(float)
        for t in trades:
            day = str(t.get("day_key") or "")
            daily_actual[day] += float(_float(t.get("actual_pnl_yen_100")) or 0)
            daily_shadow[day] += float(_float(t.get("shadow_pnl_yen_100")) or 0)
            daily_delta[day] += _trade_delta_yen(t)

        days_with_trades = [d for d in daily_actual if daily_actual[d] != 0 or daily_shadow[d] != 0]
        daily_win_actual = (
            round(
                sum(1 for d in days_with_trades if daily_actual[d] > 0) / max(1, len(days_with_trades)),
                4,
            )
            if days_with_trades
            else None
        )
        daily_win_shadow = (
            round(
                sum(1 for d in days_with_trades if daily_shadow[d] > 0) / max(1, len(days_with_trades)),
                4,
            )
            if days_with_trades
            else None
        )

        session_win_actual = (
            round(
                sum(1 for s in self.sessions if float(s.get("actual_total_pnl_yen_100") or 0) > 0)
                / max(1, len(self.sessions)),
                4,
            )
            if self.sessions
            else None
        )
        session_win_shadow = (
            round(
                sum(1 for s in self.sessions if float(s.get("shadow_total_pnl_yen_100") or 0) > 0)
                / max(1, len(self.sessions)),
                4,
            )
            if self.sessions
            else None
        )

        symbol_delta: dict[str, float] = defaultdict(float)
        for t in trades:
            symbol_delta[str(t.get("symbol") or "")] += _trade_delta_yen(t)

        day_delta_abs = {d: abs(v) for d, v in daily_delta.items()}
        sym_delta_abs = {s: abs(v) for s, v in symbol_delta.items() if s}

        day_concentrated = _is_concentrated(day_delta_abs, total_delta)
        sym_concentrated = _is_concentrated(sym_delta_abs, total_delta)

        actual_pf = _profit_factor(actual_yens)
        shadow_pf = _profit_factor(shadow_yens)

        no_shadow_ratio = no_shadow_count / max(1, len(trades))

        adopt, reject, verdict_notes = _verdict(
            actual_total=actual_total,
            shadow_total=shadow_total,
            actual_pf=actual_pf,
            shadow_pf=shadow_pf,
            shadow_loss_accel=shadow_loss_accel,
            loss_accel_help=loss_accel_help,
            actual_stop=actual_stop,
            improved_sessions=improved_sessions,
            worsened_sessions=worsened_sessions,
            daily_win_actual=daily_win_actual,
            daily_win_shadow=daily_win_shadow,
            day_concentrated=day_concentrated,
            sym_concentrated=sym_concentrated,
            no_shadow_ratio=no_shadow_ratio,
            improved_trades=improved,
            worsened_trades=worsened,
        )

        return {
            "phase": 336,
            "title": "realtime_board_adaptive_exit_full_push_replay",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "evaluation_note": (
                "push-replay per push_jsonl day; optional max_push_rows caps rows per session"
            ),
            "sessions_evaluated": len(self.sessions),
            "sessions_failed": len(self.failed_sessions),
            "trades_evaluated": len(trades),
            "actual_total_pnl_yen_100": actual_total,
            "shadow_total_pnl_yen_100": shadow_total,
            "realtime_board_vs_actual_total_delta_yen": total_delta,
            "actual_pf": actual_pf,
            "shadow_pf": shadow_pf,
            "actual_stop_hit_count": actual_stop,
            "shadow_loss_acceleration_exit_count": shadow_loss_accel,
            "loss_acceleration_avoided_stop_hit_count": loss_accel_help,
            "shadow_exit_count": shadow_exit_count,
            "no_shadow_exit_count": no_shadow_count,
            "no_shadow_exit_ratio": round(no_shadow_ratio, 4),
            "shadow_exit_reason_counts": dict(shadow_reason_counts),
            "improved_trade_count": improved,
            "worsened_trade_count": worsened,
            "unchanged_trade_count": unchanged,
            "improved_session_count": improved_sessions,
            "worsened_session_count": worsened_sessions,
            "daily_win_rate_actual": daily_win_actual,
            "daily_win_rate_shadow": daily_win_shadow,
            "session_win_rate_actual": session_win_actual,
            "session_win_rate_shadow": session_win_shadow,
            "improvement_concentration": {
                "single_day_dominant": day_concentrated,
                "single_symbol_dominant": sym_concentrated,
                "top_day_delta_share": _top_share(day_delta_abs, total_delta),
                "top_symbol_delta_share": _top_share(sym_delta_abs, total_delta),
            },
            "verdict": {
                "adopt_candidate": adopt,
                "reject_candidate": reject,
                "notes": verdict_notes,
            },
            "failed_sessions": self.failed_sessions,
            "timing_note": (
                "seconds_before_actual_exit and shadow/actual exit ordering are unreliable "
                "in push-replay; verdict uses PnL/PF/stop_hit/session stability only"
            ),
        }

    def session_rows(self) -> list[dict[str, Any]]:
        return list(self.sessions)

    def symbol_rows(self) -> list[dict[str, Any]]:
        by_sym: dict[str, dict[str, Any]] = {}
        for t in self.trades:
            sym = str(t.get("symbol") or "")
            if sym not in by_sym:
                by_sym[sym] = {
                    "symbol": sym,
                    "trades": 0,
                    "actual_total_pnl_yen_100": 0.0,
                    "shadow_total_pnl_yen_100": 0.0,
                    "improved_trade_count": 0,
                    "worsened_trade_count": 0,
                    "shadow_exit_count": 0,
                    "actual_stop_hit_count": 0,
                }
            row = by_sym[sym]
            row["trades"] += 1
            row["actual_total_pnl_yen_100"] += float(_float(t.get("actual_pnl_yen_100")) or 0)
            row["shadow_total_pnl_yen_100"] += float(_float(t.get("shadow_pnl_yen_100")) or 0)
            cls = _classify_trade(t)
            if cls == "improved":
                row["improved_trade_count"] += 1
            elif cls == "worsened":
                row["worsened_trade_count"] += 1
            if not t.get("no_shadow_exit"):
                row["shadow_exit_count"] += 1
            if t.get("actual_exit_reason") == "stop_hit":
                row["actual_stop_hit_count"] += 1
        out = []
        for sym in sorted(by_sym):
            r = by_sym[sym]
            act = round(r["actual_total_pnl_yen_100"], 2)
            sh = round(r["shadow_total_pnl_yen_100"], 2)
            out.append(
                {
                    **r,
                    "actual_total_pnl_yen_100": act,
                    "shadow_total_pnl_yen_100": sh,
                    "realtime_board_vs_actual_delta_yen": round(sh - act, 2),
                    "actual_pf": _profit_factor(
                        [
                            float(_float(t.get("actual_pnl_yen_100")) or 0)
                            for t in self.trades
                            if t.get("symbol") == sym
                        ]
                    ),
                    "shadow_pf": _profit_factor(
                        [
                            float(_float(t.get("shadow_pnl_yen_100")) or 0)
                            for t in self.trades
                            if t.get("symbol") == sym
                        ]
                    ),
                }
            )
        return out

    def exit_reason_rows(self) -> list[dict[str, Any]]:
        keys: dict[tuple[str, str], dict[str, Any]] = {}
        for t in self.trades:
            key = (
                str(t.get("actual_exit_reason") or ""),
                str(t.get("shadow_exit_reason") or ""),
            )
            if key not in keys:
                keys[key] = {
                    "actual_exit_reason": key[0],
                    "shadow_exit_reason": key[1],
                    "trades": 0,
                    "actual_total_pnl_yen_100": 0.0,
                    "shadow_total_pnl_yen_100": 0.0,
                }
            row = keys[key]
            row["trades"] += 1
            row["actual_total_pnl_yen_100"] += float(_float(t.get("actual_pnl_yen_100")) or 0)
            row["shadow_total_pnl_yen_100"] += float(_float(t.get("shadow_pnl_yen_100")) or 0)
        out = []
        for key in sorted(keys):
            r = keys[key]
            act = round(r["actual_total_pnl_yen_100"], 2)
            sh = round(r["shadow_total_pnl_yen_100"], 2)
            out.append(
                {
                    **r,
                    "actual_total_pnl_yen_100": act,
                    "shadow_total_pnl_yen_100": sh,
                    "realtime_board_vs_actual_delta_yen": round(sh - act, 2),
                }
            )
        return out

    def daily_rows(self) -> list[dict[str, Any]]:
        by_day: dict[str, dict[str, Any]] = {}
        for t in self.trades:
            day = str(t.get("day_key") or "")
            bucket = str(t.get("session_bucket") or "other")
            key = f"{day}_{bucket}"
            if key not in by_day:
                by_day[key] = {
                    "day_key": day,
                    "session_bucket": bucket,
                    "trades": 0,
                    "actual_total_pnl_yen_100": 0.0,
                    "shadow_total_pnl_yen_100": 0.0,
                }
            row = by_day[key]
            row["trades"] += 1
            row["actual_total_pnl_yen_100"] += float(_float(t.get("actual_pnl_yen_100")) or 0)
            row["shadow_total_pnl_yen_100"] += float(_float(t.get("shadow_pnl_yen_100")) or 0)
        out = []
        for key in sorted(by_day):
            r = by_day[key]
            act = round(r["actual_total_pnl_yen_100"], 2)
            sh = round(r["shadow_total_pnl_yen_100"], 2)
            out.append(
                {
                    **r,
                    "actual_total_pnl_yen_100": act,
                    "shadow_total_pnl_yen_100": sh,
                    "realtime_board_vs_actual_delta_yen": round(sh - act, 2),
                }
            )
        return out

    def delta_rows(self) -> list[dict[str, Any]]:
        from small_paper.realtime_board_exit_shadow import DELTA_FIELD_KEYS

        rows = []
        for t in self.trades:
            rows.append({k: t.get(k, "") for k in DELTA_FIELD_KEYS})
            rows[-1]["session_id"] = t.get("session_id", "")
            rows[-1]["day_key"] = t.get("day_key", "")
            rows[-1]["session_bucket"] = t.get("session_bucket", "")
        return rows


def _top_share(parts: dict[str, float], total_delta: float) -> Optional[float]:
    if not parts or abs(total_delta) < 1e-6:
        return None
    return round(max(abs(v) for v in parts.values()) / abs(total_delta), 4)


def _is_concentrated(parts: dict[str, float], total_delta: float) -> bool:
    share = _top_share(parts, total_delta)
    return share is not None and share >= CONCENTRATION_THRESHOLD


def _verdict(
    *,
    actual_total: float,
    shadow_total: float,
    actual_pf: Optional[float],
    shadow_pf: Optional[float],
    shadow_loss_accel: int,
    loss_accel_help: int,
    actual_stop: int,
    improved_sessions: int,
    worsened_sessions: int,
    daily_win_actual: Optional[float],
    daily_win_shadow: Optional[float],
    day_concentrated: bool,
    sym_concentrated: bool,
    no_shadow_ratio: float,
    improved_trades: int,
    worsened_trades: int,
) -> tuple[bool, bool, list[str]]:
    notes: list[str] = []

    adopt_checks = {
        "shadow_pnl_gt_actual": shadow_total > actual_total,
        "shadow_pf_gt_actual": (
            shadow_pf is not None
            and actual_pf is not None
            and shadow_pf > actual_pf
        ),
        "loss_accel_effective": loss_accel_help > 0 or (
            shadow_loss_accel > 0 and actual_stop > 0 and shadow_total > actual_total
        ),
        "sessions_improved_ge_worsened": improved_sessions >= worsened_sessions,
        "daily_win_rate_not_worse": (
            daily_win_shadow is not None
            and daily_win_actual is not None
            and daily_win_shadow >= daily_win_actual
        ),
        "not_concentrated": not day_concentrated and not sym_concentrated,
    }
    reject_checks = {
        "total_pnl_worse": shadow_total < actual_total,
        "pf_worse": (
            shadow_pf is not None
            and actual_pf is not None
            and shadow_pf < actual_pf
        ),
        "concentrated_day_or_symbol": day_concentrated or sym_concentrated,
        "profit_giveback_vs_stop": worsened_trades > improved_trades and actual_stop > 0,
        "too_many_no_shadow_exit": no_shadow_ratio >= NO_SHADOW_EXIT_RATIO_REJECT,
    }

    adopt = all(adopt_checks.values())
    reject = any(reject_checks.values())

    if adopt and reject:
        adopt = False
        notes.append("conflicting_signals_adopt_suppressed")

    for k, v in adopt_checks.items():
        notes.append(f"adopt_{k}={v}")
    for k, v in reject_checks.items():
        notes.append(f"reject_{k}={v}")

    if not adopt and not reject:
        notes.append("inconclusive_neither_adopt_nor_reject")

    return adopt, reject, notes


def write_phase336_outputs(agg: Phase336Aggregator, reports_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": reports_dir / "phase336_realtime_board_full_replay_summary.json",
        "sessions": reports_dir / "phase336_realtime_board_full_replay_sessions.csv",
        "symbols": reports_dir / "phase336_realtime_board_full_replay_symbols.csv",
        "exit_reasons": reports_dir / "phase336_realtime_board_full_replay_exit_reasons.csv",
        "trades": reports_dir / "phase336_realtime_board_full_replay_trades.csv",
        "delta": reports_dir / "phase336_realtime_board_full_replay_delta.csv",
        "daily_am_pm": reports_dir / "phase336_realtime_board_full_replay_daily_am_pm.csv",
    }
    summary = agg.build_summary()
    paths["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(paths["sessions"], _session_csv_fields(), agg.session_rows())
    _write_csv(paths["symbols"], _symbol_csv_fields(), agg.symbol_rows())
    _write_csv(paths["exit_reasons"], _exit_reason_csv_fields(), agg.exit_reason_rows())
    _write_csv(paths["trades"], _trade_csv_fields(), agg.trades)
    _write_csv(paths["delta"], _delta_csv_fields(), agg.delta_rows())
    _write_csv(paths["daily_am_pm"], _daily_csv_fields(), agg.daily_rows())
    return {k: str(v) for k, v in paths.items()}


def _session_csv_fields() -> list[str]:
    return [
        "session_id",
        "day_key",
        "source",
        "push_dir",
        "symbol_files",
        "push_rows",
        "runtime_sec",
        "trades",
        "actual_total_pnl_yen_100",
        "shadow_total_pnl_yen_100",
        "session_delta_yen",
    ]


def _symbol_csv_fields() -> list[str]:
    return [
        "symbol",
        "trades",
        "actual_total_pnl_yen_100",
        "shadow_total_pnl_yen_100",
        "realtime_board_vs_actual_delta_yen",
        "actual_pf",
        "shadow_pf",
        "improved_trade_count",
        "worsened_trade_count",
        "shadow_exit_count",
        "actual_stop_hit_count",
    ]


def _exit_reason_csv_fields() -> list[str]:
    return [
        "actual_exit_reason",
        "shadow_exit_reason",
        "trades",
        "actual_total_pnl_yen_100",
        "shadow_total_pnl_yen_100",
        "realtime_board_vs_actual_delta_yen",
    ]


def _trade_csv_fields() -> list[str]:
    return [
        "session_id",
        "day_key",
        "session_bucket",
        "symbol",
        "position_id",
        "entry_time",
        "entry_price",
        "shadow_exit_reason",
        "shadow_exit_time",
        "shadow_exit_price",
        "shadow_pnl_pct",
        "shadow_pnl_yen_100",
        "actual_exit_reason",
        "actual_exit_time",
        "actual_exit_price",
        "actual_pnl_pct",
        "actual_pnl_yen_100",
        "realtime_board_vs_actual_delta_yen",
        "no_shadow_exit",
        "board_strength_hold_extend",
    ]


def _delta_csv_fields() -> list[str]:
    return [
        "session_id",
        "day_key",
        "session_bucket",
        "symbol",
        "position_id",
        "shadow_exit_reason",
        "actual_exit_reason",
        "shadow_pnl_yen_100",
        "actual_pnl_yen_100",
        "realtime_board_vs_actual_delta_yen",
    ]


def _daily_csv_fields() -> list[str]:
    return [
        "day_key",
        "session_bucket",
        "trades",
        "actual_total_pnl_yen_100",
        "shadow_total_pnl_yen_100",
        "realtime_board_vs_actual_delta_yen",
    ]


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})
