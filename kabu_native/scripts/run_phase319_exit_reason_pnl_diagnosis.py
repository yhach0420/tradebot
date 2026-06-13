#!/usr/bin/env python3
"""
Phase319: EXIT reason PnL diagnosis for 20260608 live paper (169 trades).

Read-only analysis of observer_exit rows in small_paper_events.csv.
Output: phase319_exit_reason_pnl_diagnosis.json
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase319_exit_reason_pnl_diagnosis.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}

TARGET_EXIT_REASONS = (
    "trailing_mfe_exit",
    "stop_hit",
    "overlap_replaced_review",
    "morning_session_close",
    "afternoon_session_close",
)


@dataclass
class TradeExit:
    session: str
    symbol: str
    exit_reason: str
    pnl_pct: float
    pnl_yen_100: float
    entry_price: float
    exit_price: float


def _bootstrap() -> None:
    src = REPO / "kabu_native" / "src"
    for p in (src, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(pnls: list[float]) -> Any:
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    if loss <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss, 4)


def _load_exits(session_label: str, session_dir: Path) -> list[TradeExit]:
    from replay.pnl_yen import compute_pnl_yen_100

    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    out: list[TradeExit] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "observer_exit":
                continue
            entry = _float(row.get("entry_price"))
            exit_p = _float(row.get("exit_price")) or _float(row.get("current_price"))
            pnl = _float(row.get("pnl_pct"))
            if pnl is None and entry and exit_p:
                pnl = ((exit_p - entry) / entry * 100.0) if entry > 0 else 0.0
            yen = compute_pnl_yen_100(entry or 0.0, exit_p or 0.0) if entry and exit_p else 0.0
            out.append(
                TradeExit(
                    session=session_label,
                    symbol=str(row.get("symbol") or ""),
                    exit_reason=str(row.get("exit_reason") or "unknown"),
                    pnl_pct=float(pnl or 0.0),
                    pnl_yen_100=float(yen),
                    entry_price=float(entry or 0.0),
                    exit_price=float(exit_p or 0.0),
                )
            )
    return out


def _summarize(trades: list[TradeExit]) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "total_pnl_yen_100": 0.0,
            "avg_pnl_yen_100": None,
        }
    pnls = [t.pnl_pct for t in trades]
    yens = [t.pnl_yen_100 for t in trades]
    n = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trade_count": n,
        "win_rate": round(wins / n, 4),
        "profit_factor": _pf(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "total_pnl_yen_100": round(sum(yens), 2),
        "avg_pnl_yen_100": round(sum(yens) / n, 2),
    }


def _by_exit_reason(trades: list[TradeExit]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[TradeExit]] = defaultdict(list)
    for t in trades:
        buckets[t.exit_reason].append(t)
    out: dict[str, dict[str, Any]] = {}
    for reason in TARGET_EXIT_REASONS:
        out[reason] = _summarize(buckets.get(reason, []))
    other = [t for t in trades if t.exit_reason not in TARGET_EXIT_REASONS]
    if other:
        out["_other"] = _summarize(other)
    return out


def _pnl_contribution_ranking(by_reason: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for reason, m in by_reason.items():
        if reason.startswith("_"):
            continue
        rows.append(
            {
                "exit_reason": reason,
                "total_pnl_pct": m.get("total_pnl_pct"),
                "total_pnl_yen_100": m.get("total_pnl_yen_100"),
                "trade_count": m.get("trade_count"),
                "profit_factor": m.get("profit_factor"),
            }
        )
    rows.sort(key=lambda r: float(r.get("total_pnl_yen_100") or 0), reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank_by_yen_contribution"] = i
    return rows


def _verdict(
    by_reason: dict[str, dict[str, Any]],
    ranking: list[dict[str, Any]],
    combined: dict[str, Any],
) -> dict[str, Any]:
    ranked = [r for r in ranking if int(r.get("trade_count") or 0) > 0]
    best = ranked[0]["exit_reason"] if ranked else None
    worst = ranked[-1]["exit_reason"] if ranked else None

    overlap = by_reason.get("overlap_replaced_review", {})
    trailing = by_reason.get("trailing_mfe_exit", {})
    stop = by_reason.get("stop_hit", {})

    overlap_harmful = (
        float(overlap.get("total_pnl_yen_100") or 0) < 0
        or (
            overlap.get("profit_factor") not in (None, "inf")
            and float(overlap.get("profit_factor") or 0) < 1.0
        )
    )

    trailing_effective = (
        int(trailing.get("trade_count") or 0) > 0
        and float(trailing.get("total_pnl_yen_100") or 0) > 0
        and trailing.get("profit_factor") not in (None,)
        and (
            trailing.get("profit_factor") == "inf"
            or float(trailing.get("profit_factor") or 0) >= 1.0
        )
    )

    stop_loss_yen = abs(float(stop.get("total_pnl_yen_100") or 0))
    total_abs_yen = sum(abs(float(r.get("total_pnl_yen_100") or 0)) for r in ranked)
    stop_share = (stop_loss_yen / total_abs_yen) if total_abs_yen > 0 else 0.0
    stop_excessive = (
        int(stop.get("trade_count") or 0) >= 5
        and float(stop.get("total_pnl_yen_100") or 0) < 0
        and (stop.get("win_rate") or 0) == 0
        and stop_share >= 0.5
    )

    return {
        "best_exit_reason": best,
        "worst_exit_reason": worst,
        "overlap_replaced_review_harmful": overlap_harmful,
        "trailing_mfe_effective": trailing_effective,
        "stop_hit_excessive": stop_excessive,
        "combined_net_pnl_yen_100": combined.get("total_pnl_yen_100"),
        "combined_net_pnl_pct": combined.get("total_pnl_pct"),
        "stop_hit_loss_share_of_abs_yen": round(stop_share, 4),
        "rationale": [
            f"best={best} by total_pnl_yen_100; worst={worst}",
            f"overlap_replaced_review total_yen={overlap.get('total_pnl_yen_100')} PF={overlap.get('profit_factor')}",
            f"trailing_mfe_exit total_yen={trailing.get('total_pnl_yen_100')} PF={trailing.get('profit_factor')} wr={trailing.get('win_rate')}",
            f"stop_hit total_yen={stop.get('total_pnl_yen_100')} wr={stop.get('win_rate')} loss_share={stop_share:.1%}",
        ],
    }


def _load_summary(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "small_paper_summary.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    all_trades: list[TradeExit] = []
    session_blocks: dict[str, Any] = {}
    for label, session_dir in SESSIONS.items():
        exits = _load_exits(label, session_dir)
        all_trades.extend(exits)
        positions_path = session_dir / "small_paper_positions.csv"
        pos_rows = 0
        if positions_path.is_file():
            with positions_path.open(encoding="utf-8", newline="") as f:
                pos_rows = sum(1 for _ in csv.DictReader(f))
        session_blocks[label] = {
            "session_dir": str(session_dir.relative_to(REPO)).replace("\\", "/"),
            "events_csv": str((session_dir / "small_paper_events.csv").relative_to(REPO)).replace("\\", "/"),
            "positions_csv_rows": pos_rows,
            "positions_csv_note": "empty at session end (positions flushed); trades from observer_exit events",
            "summary_accepted_count": _load_summary(session_dir).get("accepted_count"),
            "observer_exit_count": len(exits),
            "exit_reason_counts": dict(Counter(t.exit_reason for t in exits)),
            "by_exit_reason": _by_exit_reason(exits),
            "session_totals": _summarize(exits),
        }

    combined_by_reason = _by_exit_reason(all_trades)
    combined_totals = _summarize(all_trades)
    ranking = _pnl_contribution_ranking(combined_by_reason)
    verdict = _verdict(combined_by_reason, ranking, combined_totals)

    report = {
        "phase": 319,
        "title": "exit_reason_pnl_diagnosis",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; no logic changes",
        "target_date": DAY,
        "trade_source": "observer_exit rows in small_paper_events.csv",
        "trade_count": len(all_trades),
        "target_exit_reasons": list(TARGET_EXIT_REASONS),
        "sessions": session_blocks,
        "combined": {
            "by_exit_reason": combined_by_reason,
            "session_totals": combined_totals,
            "pnl_contribution_ranking": ranking,
        },
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"trades={len(all_trades)} best={verdict['best_exit_reason']} worst={verdict['worst_exit_reason']} "
        f"stop_excessive={verdict['stop_hit_excessive']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
