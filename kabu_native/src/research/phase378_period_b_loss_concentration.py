"""
Phase378: Period-B loss concentration review for production stack C.

Period: 20260528-20260612 only (no full-period analysis).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase366_stophit_reclassification import production_kept_trades
from research.phase377_daily_regime_breakdown import (
    PERIOD_B_END,
    PERIOD_B_ID,
    PERIOD_B_START,
    PRIMARY_STACK,
)

JST = ZoneInfo("Asia/Tokyo")
LOW_MFE_THRESHOLD_PCT = 0.3
FOCUS_SYMBOLS = ("6976.T", "6981.T", "7220.T", "4062.T")
TOP_N_TIERS = (10, 20, 50, 100)

EXIT_BUCKETS = (
    "stop_hit",
    "trailing_mfe_exit",
    "overlap_replaced",
    "session_end",
    "other",
)

MFE_BANDS = (
    ("peak_mfe_lt_0.3", None, 0.3),
    ("peak_mfe_0.3_to_0.6", 0.3, 0.6),
    ("peak_mfe_0.6_to_1.0", 0.6, 1.0),
    ("peak_mfe_ge_1.0", 1.0, None),
)

HOLD_BANDS = (
    ("lt_60s", 0.0, 60.0),
    ("60_to_180s", 60.0, 180.0),
    ("180_to_600s", 180.0, 600.0),
    ("ge_600s", 600.0, None),
)

LOSS_RANK_FIELDS = [
    "rank",
    "day",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "exit_reason",
    "peak_mfe_pct",
    "peak_mae_pct",
    "hold_seconds",
    "universe",
    "entry_momentum_score",
    "entry_vwap_dev_pct",
    "entry_rise_5min_pct",
    "board_tier",
]

BY_SYMBOL_FIELDS = [
    "rank",
    "symbol",
    "loss_count",
    "total_loss_yen_100",
    "avg_loss_yen_100",
    "stop_hit_rate",
    "avg_mfe_pct",
    "avg_hold_seconds",
    "is_focus_symbol",
]

BY_EXIT_REASON_FIELDS = [
    "top_n",
    "exit_reason",
    "count",
    "loss_yen_100",
    "share_of_top_n_loss",
    "share_of_total_loss",
]

BY_MFE_BAND_FIELDS = [
    "top_n",
    "mfe_band",
    "count",
    "loss_yen_100",
    "share_of_top_n_loss",
    "share_of_total_loss",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int:
    try:
        if val is None or val == "":
            return 0
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def _session_day_key(session_result: Mapping[str, Any]) -> str:
    day = str(session_result.get("day_key") or "")
    if day:
        return day
    meta = session_result.get("session_meta") or {}
    day = str(meta.get("day_key") or meta.get("day") or "")
    if day:
        return day
    sid = str(session_result.get("session_id") or "")
    if "/" in sid:
        return sid.split("/")[0]
    for trade in session_result.get("trades") or []:
        trade_day = str(trade.get("day_key") or "")
        if trade_day:
            return trade_day
    return ""


def _in_period_b(day: str) -> bool:
    return PERIOD_B_START <= day <= PERIOD_B_END


def _exit_bucket(trade: Mapping[str, Any]) -> str:
    reason = str(
        trade.get("exit_reason_canonical")
        or trade.get("structural_exit_reason")
        or trade.get("exit_reason")
        or ""
    ).strip()
    if reason in EXIT_BUCKETS:
        return reason
    return "other"


def _mfe_band_label(peak_mfe: Optional[float]) -> str:
    peak = peak_mfe if peak_mfe is not None else 0.0
    for label, lo, hi in MFE_BANDS:
        if hi is None:
            if peak >= (lo if lo is not None else 0.0):
                return label
        elif lo is not None and lo <= peak < hi:
            return label
        elif lo is None and peak < hi:
            return label
    return "peak_mfe_lt_0.3"


def _hold_band_label(hold_sec: Optional[float]) -> Optional[str]:
    if hold_sec is None:
        return None
    h = float(hold_sec)
    for label, lo, hi in HOLD_BANDS:
        if hi is None:
            if h >= lo:
                return label
        elif lo <= h < hi:
            return label
    return "ge_600s"


def loss_rank_row(trade: Mapping[str, Any], *, rank: int) -> dict[str, Any]:
    hold = _float(trade.get("hold_sec")) or _float(trade.get("hold_duration_sec"))
    return {
        "rank": rank,
        "day": trade.get("day_key") or "",
        "symbol": trade.get("symbol") or "",
        "entry_time": trade.get("entry_time") or "",
        "exit_time": trade.get("exit_time") or "",
        "pnl_yen_100": _float(trade.get("pnl_yen_100")),
        "pnl_pct": _float(trade.get("pnl_pct")),
        "exit_reason": _exit_bucket(trade),
        "peak_mfe_pct": _float(trade.get("peak_mfe_pct")),
        "peak_mae_pct": _float(trade.get("peak_mae_pct")),
        "hold_seconds": hold,
        "universe": trade.get("universe_group") or "",
        "entry_momentum_score": _float(
            trade.get("entry_momentum_score")
            or trade.get("entry_momentum_continuation_score")
        ),
        "entry_vwap_dev_pct": _float(trade.get("entry_vwap_dev_pct")),
        "entry_rise_5min_pct": _float(trade.get("entry_rise_5min_pct")),
        "board_tier": trade.get("board_dynamic_trailing_tier") or "",
    }


def kept_trades_period_b(session_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    day = _session_day_key(session_result)
    if not _in_period_b(day):
        return []
    out: list[dict[str, Any]] = []
    for trade in production_kept_trades(session_result):
        trade_day = str(trade.get("day_key") or day)
        if not _in_period_b(trade_day):
            continue
        row = dict(trade)
        row["day_key"] = trade_day
        out.append(row)
    return out


def losing_trades_sorted(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    losses = [dict(t) for t in trades if (_float(t.get("pnl_yen_100")) or 0.0) < 0]
    losses.sort(key=lambda t: (_float(t.get("pnl_yen_100")) or 0.0, str(t.get("day") or t.get("day_key") or "")))
    return losses


def total_loss_amount(losses: Sequence[Mapping[str, Any]]) -> float:
    return round(abs(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in losses)), 2)


def exit_reason_breakdown(
    losses_sorted: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
    total_loss: float,
) -> list[dict[str, Any]]:
    subset = list(losses_sorted[:top_n])
    counts: dict[str, int] = defaultdict(int)
    loss_by: dict[str, float] = defaultdict(float)
    subset_loss = abs(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in subset))
    for t in subset:
        bucket = _exit_bucket(t)
        counts[bucket] += 1
        loss_by[bucket] += abs(float(_float(t.get("pnl_yen_100")) or 0.0))
    rows: list[dict[str, Any]] = []
    for bucket in EXIT_BUCKETS:
        loss_amt = round(loss_by.get(bucket, 0.0), 2)
        rows.append(
            {
                "top_n": top_n,
                "exit_reason": bucket,
                "count": counts.get(bucket, 0),
                "loss_yen_100": loss_amt,
                "share_of_top_n_loss": round(loss_amt / subset_loss, 4) if subset_loss > 0 else None,
                "share_of_total_loss": round(loss_amt / total_loss, 4) if total_loss > 0 else None,
            }
        )
    return rows


def universe_breakdown(
    losses_sorted: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
    total_loss: float,
) -> list[dict[str, Any]]:
    subset = list(losses_sorted[:top_n])
    counts: dict[str, int] = defaultdict(int)
    loss_by: dict[str, float] = defaultdict(float)
    subset_loss = abs(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in subset))
    for t in subset:
        ug = str(t.get("universe_group") or "other")
        if ug not in ("dynamic40", "core10"):
            ug = "other"
        counts[ug] += 1
        loss_by[ug] += abs(float(_float(t.get("pnl_yen_100")) or 0.0))
    rows: list[dict[str, Any]] = []
    for ug in ("dynamic40", "core10", "other"):
        loss_amt = round(loss_by.get(ug, 0.0), 2)
        rows.append(
            {
                "top_n": top_n,
                "universe": ug,
                "count": counts.get(ug, 0),
                "loss_yen_100": loss_amt,
                "share_of_top_n_loss": round(loss_amt / subset_loss, 4) if subset_loss > 0 else None,
                "share_of_total_loss": round(loss_amt / total_loss, 4) if total_loss > 0 else None,
            }
        )
    return rows


def mfe_band_breakdown(
    losses_sorted: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
    total_loss: float,
) -> list[dict[str, Any]]:
    subset = list(losses_sorted[:top_n])
    counts: dict[str, int] = defaultdict(int)
    loss_by: dict[str, float] = defaultdict(float)
    subset_loss = abs(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in subset))
    for t in subset:
        bucket = _mfe_band_label(_float(t.get("peak_mfe_pct")))
        counts[bucket] += 1
        loss_by[bucket] += abs(float(_float(t.get("pnl_yen_100")) or 0.0))
    rows: list[dict[str, Any]] = []
    for label, _, _ in MFE_BANDS:
        loss_amt = round(loss_by.get(label, 0.0), 2)
        rows.append(
            {
                "top_n": top_n,
                "mfe_band": label,
                "count": counts.get(label, 0),
                "loss_yen_100": loss_amt,
                "share_of_top_n_loss": round(loss_amt / subset_loss, 4) if subset_loss > 0 else None,
                "share_of_total_loss": round(loss_amt / total_loss, 4) if total_loss > 0 else None,
            }
        )
    return rows


def hold_band_breakdown(
    losses_sorted: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    subset = list(losses_sorted[:top_n])
    counts: dict[str, int] = defaultdict(int)
    loss_by: dict[str, float] = defaultdict(float)
    for t in subset:
        hold = _float(t.get("hold_sec")) or _float(t.get("hold_duration_sec"))
        bucket = _hold_band_label(hold)
        if bucket is None:
            bucket = "unknown"
        counts[bucket] += 1
        loss_by[bucket] += abs(float(_float(t.get("pnl_yen_100")) or 0.0))
    rows: list[dict[str, Any]] = []
    for label, _, _ in HOLD_BANDS:
        rows.append(
            {
                "top_n": top_n,
                "hold_band": label,
                "count": counts.get(label, 0),
                "loss_yen_100": round(loss_by.get(label, 0.0), 2),
            }
        )
    unknown_count = counts.get("unknown", 0)
    if unknown_count:
        rows.append(
            {
                "top_n": top_n,
                "hold_band": "unknown",
                "count": unknown_count,
                "loss_yen_100": round(loss_by.get("unknown", 0.0), 2),
            }
        )
    return rows


def loss_concentration_shares(
    losses_sorted: Sequence[Mapping[str, Any]], total_loss: float
) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for n in (20, 50, 100):
        subset = losses_sorted[: min(n, len(losses_sorted))]
        subset_loss = abs(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in subset))
        out[f"loss_top{n}_share"] = (
            round(subset_loss / total_loss, 4) if total_loss > 0 and subset else None
        )
    return out


def symbol_loss_ranking(
    losses: Sequence[Mapping[str, Any]], *, top_n: int = 20
) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in losses:
        sym = str(t.get("symbol") or "")
        if sym:
            by_sym[sym].append(dict(t))

    rows: list[dict[str, Any]] = []
    for sym, trades in by_sym.items():
        loss_vals = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]
        total_loss = round(sum(loss_vals), 2)
        stops = sum(1 for t in trades if _exit_bucket(t) == "stop_hit")
        mfes = [_float(t.get("peak_mfe_pct")) for t in trades]
        mfes_valid = [float(m) for m in mfes if m is not None]
        holds = [_float(t.get("hold_sec")) or _float(t.get("hold_duration_sec")) for t in trades]
        holds_valid = [float(h) for h in holds if h is not None]
        rows.append(
            {
                "symbol": sym,
                "loss_count": len(trades),
                "total_loss_yen_100": total_loss,
                "avg_loss_yen_100": round(total_loss / len(trades), 2) if trades else None,
                "stop_hit_rate": round(stops / len(trades), 4) if trades else None,
                "avg_mfe_pct": round(statistics.mean(mfes_valid), 4) if mfes_valid else None,
                "avg_hold_seconds": round(statistics.mean(holds_valid), 2) if holds_valid else None,
                "is_focus_symbol": sym in FOCUS_SYMBOLS,
            }
        )
    rows.sort(key=lambda r: float(r.get("total_loss_yen_100") or 0.0))
    for i, row in enumerate(rows[:top_n], start=1):
        row["rank"] = i
    return rows[:top_n]


def entry_exit_dominated_analysis(
    losses: Sequence[Mapping[str, Any]], total_loss: float
) -> dict[str, Any]:
    entry_loss = 0.0
    exit_loss = 0.0
    entry_count = 0
    exit_count = 0
    for t in losses:
        peak = _float(t.get("peak_mfe_pct"))
        amt = abs(float(_float(t.get("pnl_yen_100")) or 0.0))
        if peak is None:
            continue
        if peak < LOW_MFE_THRESHOLD_PCT:
            entry_loss += amt
            entry_count += 1
        else:
            exit_loss += amt
            exit_count += 1
    return {
        "entry_dominated": {
            "count": entry_count,
            "total_loss_yen_100": round(-entry_loss, 2),
            "share_of_total_loss": round(entry_loss / total_loss, 4) if total_loss > 0 else None,
        },
        "exit_dominated": {
            "count": exit_count,
            "total_loss_yen_100": round(-exit_loss, 2),
            "share_of_total_loss": round(exit_loss / total_loss, 4) if total_loss > 0 else None,
        },
        "entry_dominated_loss_share": round(entry_loss / total_loss, 4) if total_loss > 0 else None,
        "exit_dominated_loss_share": round(exit_loss / total_loss, 4) if total_loss > 0 else None,
        "unknown_mfe_count": sum(1 for t in losses if _float(t.get("peak_mfe_pct")) is None),
    }


def period_b_judgment(
    *,
    losses_sorted: Sequence[Mapping[str, Any]],
    total_loss: float,
    concentration: Mapping[str, Any],
    symbol_rows: Sequence[Mapping[str, Any]],
    entry_exit: Mapping[str, Any],
    top_n_breakdowns: Mapping[str, Any],
) -> dict[str, Any]:
    focus_rows = {str(r["symbol"]): r for r in symbol_rows if r.get("is_focus_symbol")}
    focus_still_major = any(
        focus_rows.get(sym) is not None
        and abs(float(focus_rows[sym].get("total_loss_yen_100") or 0.0)) > 0
        for sym in FOCUS_SYMBOLS
    )
    focus_in_top20 = any(
        str(r.get("symbol")) in FOCUS_SYMBOLS for r in symbol_rows[:20]
    )

    entry_share = _float(entry_exit.get("entry_dominated_loss_share")) or 0.0
    exit_share = _float(entry_exit.get("exit_dominated_loss_share")) or 0.0
    if entry_share > exit_share:
        dominant_cause = "ENTRY"
    elif exit_share > entry_share:
        dominant_cause = "EXIT"
    else:
        dominant_cause = "equal"

    uni_top100 = top_n_breakdowns.get("universe_top100") or []
    dyn_share = next(
        (_float(r.get("share_of_total_loss")) for r in uni_top100 if r.get("universe") == "dynamic40"),
        None,
    )
    if dominant_cause == "ENTRY" and (dyn_share is not None and dyn_share >= 0.5):
        priority = "ENTRY"
    elif dominant_cause == "EXIT":
        priority = "EXIT"
    elif dyn_share is not None and dyn_share >= 0.7:
        priority = "Universe"
    else:
        priority = dominant_cause

    top20_share = concentration.get("loss_top20_share")
    top_exit = top_n_breakdowns.get("exit_reason_top100") or []
    top_exit_sorted = sorted(top_exit, key=lambda r: _float(r.get("loss_yen_100")) or 0.0, reverse=True)
    main_exit = top_exit_sorted[0].get("exit_reason") if top_exit_sorted else None

    return {
        "q1_where_losses_concentrate": {
            "primary_exit_reason_top100": main_exit,
            "top20_loss_share": top20_share,
            "entry_dominated_share": entry_exit.get("entry_dominated_loss_share"),
            "exit_dominated_share": entry_exit.get("exit_dominated_loss_share"),
        },
        "q2_loss_top20_share": top20_share,
        "q3_focus_symbols_still_loss_sources": {
            "6976.T": focus_rows.get("6976.T"),
            "6981.T": focus_rows.get("6981.T"),
            "7220.T": focus_rows.get("7220.T"),
            "4062.T": focus_rows.get("4062.T"),
            "any_still_major": focus_still_major,
            "any_in_top20": focus_in_top20,
        },
        "q4_dominant_loss_cause": dominant_cause,
        "q5_next_improvement_priority": priority,
        "core_loss_summary": _core_loss_summary(
            losses_sorted=losses_sorted,
            symbol_rows=symbol_rows,
            entry_exit=entry_exit,
            main_exit=main_exit,
        ),
    }


def _core_loss_summary(
    *,
    losses_sorted: Sequence[Mapping[str, Any]],
    symbol_rows: Sequence[Mapping[str, Any]],
    entry_exit: Mapping[str, Any],
    main_exit: Optional[str],
) -> str:
    entry_share = _float(entry_exit.get("entry_dominated_loss_share"))
    exit_share = _float(entry_exit.get("exit_dominated_loss_share"))
    top_sym = symbol_rows[0].get("symbol") if symbol_rows else None
    parts = []
    if main_exit:
        parts.append(f"exit_reason={main_exit}")
    if entry_share is not None and exit_share is not None:
        if entry_share >= exit_share:
            parts.append(f"ENTRY支配({entry_share:.1%})")
        else:
            parts.append(f"EXIT支配({exit_share:.1%})")
    if top_sym:
        parts.append(f"最大銘柄={top_sym}")
    return " / ".join(parts) if parts else ""


def load_phase377_period_b_reference(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / "phase377_daily_regime_breakdown_summary.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("period_metrics") or {}).get(PERIOD_B_ID, {}).get(PRIMARY_STACK, {})


def consistency_checks(
    all_trades: Sequence[Mapping[str, Any]],
    phase377_ref: Mapping[str, Any],
) -> dict[str, Any]:
    yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in all_trades]
    total_pnl = round(sum(yens), 2)
    ref_pnl = _float(phase377_ref.get("total_pnl_yen_100"))
    ref_trades = _int(phase377_ref.get("trade_count"))
    return {
        "period_b_trade_count": len(all_trades),
        "phase377_period_b_trade_count": ref_trades,
        "trade_count_matches": len(all_trades) == ref_trades if ref_trades else None,
        "period_b_total_pnl_yen_100": total_pnl,
        "phase377_period_b_total_pnl_yen_100": ref_pnl,
        "total_pnl_matches": total_pnl == ref_pnl if ref_pnl is not None else None,
    }


def build_report_markdown(summary: Mapping[str, Any]) -> str:
    judgment = summary.get("period_b_judgment") or {}
    entry_exit = summary.get("entry_exit_dominated") or {}
    conc = summary.get("loss_concentration") or {}
    cons = summary.get("consistency_checks") or {}
    focus = judgment.get("q3_focus_symbols_still_loss_sources") or {}

    lines = [
        "# Phase378 Period-B Loss Concentration Report",
        "",
        f"**期間:** {PERIOD_B_START}–{PERIOD_B_END} | **Stack:** {PRIMARY_STACK}",
        "",
        "## 結論",
        "",
        f"**現行スタックで残る損失の本丸:** {judgment.get('core_loss_summary')}",
        "",
        f"- 損失トレード数: {summary.get('loss_trade_count')}",
        f"- 総損失額: {summary.get('total_loss_yen_100')}",
        f"- loss_top20_share: {conc.get('loss_top20_share')}",
        f"- loss_top50_share: {conc.get('loss_top50_share')}",
        f"- loss_top100_share: {conc.get('loss_top100_share')}",
        f"- ENTRY支配シェア: {entry_exit.get('entry_dominated_loss_share')}",
        f"- EXIT支配シェア: {entry_exit.get('exit_dominated_loss_share')}",
        f"- 支配的原因: {judgment.get('q4_dominant_loss_cause')}",
        f"- 次の改善優先: {judgment.get('q5_next_improvement_priority')}",
        "",
        "## 必須6問",
        "",
        f"1. 損失集中箇所: {judgment.get('q1_where_losses_concentrate')}",
        f"2. TOP20シェア: {judgment.get('q2_loss_top20_share')}",
        f"3. フォーカス銘柄: {focus}",
        f"4. ENTRY/EXIT支配: {judgment.get('q4_dominant_loss_cause')}",
        f"5. 改善優先順位: {judgment.get('q5_next_improvement_priority')}",
        "",
        "## Phase377整合",
        "",
        f"- trade_count_matches: {cons.get('trade_count_matches')}",
        f"- total_pnl_matches: {cons.get('total_pnl_matches')}",
        "",
        "## 損失上位銘柄 Top20",
        "",
    ]
    for row in summary.get("symbol_top20") or []:
        lines.append(
            f"- {row.get('rank')}. {row.get('symbol')}: "
            f"loss={row.get('total_loss_yen_100')} count={row.get('loss_count')} "
            f"stop_rate={row.get('stop_hit_rate')}"
        )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase378PeriodBLossConcentration:
    reports_dir: Path
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase378_period_b_loss_concentration_summary.json",
            "loss_top100": self.reports_dir / "phase378_period_b_loss_top100.csv",
            "by_symbol": self.reports_dir / "phase378_period_b_loss_by_symbol.csv",
            "by_exit_reason": self.reports_dir / "phase378_period_b_loss_by_exit_reason.csv",
            "by_mfe_band": self.reports_dir / "phase378_period_b_loss_by_mfe_band.csv",
            "report": self.reports_dir / "phase378_period_b_loss_report.md",
        }

    def ingest_trades(self, trades: Sequence[Mapping[str, Any]]) -> None:
        self.all_trades.extend(dict(t) for t in trades)

    def analyze(self) -> dict[str, Any]:
        losses = losing_trades_sorted(self.all_trades)
        total_loss = total_loss_amount(losses)
        loss_rows = [loss_rank_row(t, rank=i + 1) for i, t in enumerate(losses)]

        concentration = loss_concentration_shares(losses, total_loss)
        symbol_top20 = symbol_loss_ranking(losses, top_n=20)
        entry_exit = entry_exit_dominated_analysis(losses, total_loss)

        exit_rows: list[dict[str, Any]] = []
        mfe_rows: list[dict[str, Any]] = []
        hold_rows: list[dict[str, Any]] = []
        uni_rows: list[dict[str, Any]] = []
        top_n_breakdowns: dict[str, Any] = {}

        for n in TOP_N_TIERS:
            exit_rows.extend(exit_reason_breakdown(losses, top_n=n, total_loss=total_loss))
            mfe_rows.extend(mfe_band_breakdown(losses, top_n=n, total_loss=total_loss))
            hold_rows.extend(hold_band_breakdown(losses, top_n=n))
            uni = universe_breakdown(losses, top_n=n, total_loss=total_loss)
            uni_rows.extend(uni)
            top_n_breakdowns[f"exit_reason_top{n}"] = [r for r in exit_rows if r["top_n"] == n]
            top_n_breakdowns[f"universe_top{n}"] = uni
            top_n_breakdowns[f"hold_band_top{n}"] = [r for r in hold_rows if r["top_n"] == n]

        phase377_ref = load_phase377_period_b_reference(self.reports_dir)
        consistency = consistency_checks(self.all_trades, phase377_ref)
        judgment = period_b_judgment(
            losses_sorted=losses,
            total_loss=total_loss,
            concentration=concentration,
            symbol_rows=symbol_top20,
            entry_exit=entry_exit,
            top_n_breakdowns=top_n_breakdowns,
        )

        return {
            "phase": 378,
            "title": "Period-B loss concentration review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "period": {"start": PERIOD_B_START, "end": PERIOD_B_END},
            "stack_id": PRIMARY_STACK,
            "trade_count": len(self.all_trades),
            "loss_trade_count": len(losses),
            "win_trade_count": len(self.all_trades) - len(losses),
            "total_pnl_yen_100": consistency.get("period_b_total_pnl_yen_100"),
            "total_loss_yen_100": round(-total_loss, 2),
            "loss_concentration": concentration,
            "entry_exit_dominated": entry_exit,
            "symbol_top20": symbol_top20,
            "focus_symbols": {sym: next((r for r in symbol_top20 if r["symbol"] == sym), None) for sym in FOCUS_SYMBOLS},
            "period_b_judgment": judgment,
            "consistency_checks": consistency,
            "phase377_reference": phase377_ref,
            "top_n_analysis": {
                "tiers": list(TOP_N_TIERS),
                "exit_reason": top_n_breakdowns,
                "universe": {f"top{n}": top_n_breakdowns.get(f"universe_top{n}") for n in TOP_N_TIERS},
                "hold_band": {f"top{n}": top_n_breakdowns.get(f"hold_band_top{n}") for n in TOP_N_TIERS},
            },
            "_loss_rows": loss_rows,
            "_exit_rows": exit_rows,
            "_mfe_rows": mfe_rows,
            "_symbol_rows": symbol_top20,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        _write_csv(paths["loss_top100"], list(result["_loss_rows"]), LOSS_RANK_FIELDS)
        _write_csv(paths["by_symbol"], list(result["_symbol_rows"]), BY_SYMBOL_FIELDS)
        _write_csv(paths["by_exit_reason"], list(result["_exit_rows"]), BY_EXIT_REASON_FIELDS)
        _write_csv(paths["by_mfe_band"], list(result["_mfe_rows"]), BY_MFE_BAND_FIELDS)

        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
