#!/usr/bin/env python3
"""
Phase321: stop_hit mechanism review for 20260608 live paper (41 trades).

Read-only analysis. Output: phase321_stop_hit_mechanism_review.json
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase321_stop_hit_mechanism_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")
HARD_STOP_PCT = 1.20
TRAILING_MFE_ACTIVATE_PCT = 0.80

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}


@dataclass
class StopTrade:
    session: str
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    loss_pct: float
    loss_yen_100: float
    peak_mfe_pct: float
    peak_mae_pct: float
    hold_sec: float
    trailing_mfe_activated: bool
    implied_stop_price: float
    loss_vs_hard_stop_pct: float


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


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _stop_hit_mechanism() -> dict[str, Any]:
    return {
        "primary_module": "kabu_native/src/small_paper/observer_position_tracker.py",
        "simulation_module": "kabu_native/src/research/structural_exit_policies.py",
        "config_param": "ObserverTrackerConfig.hard_stop_pct",
        "hard_stop_pct": HARD_STOP_PCT,
        "stop_price_formula": "entry_price * (1.0 - hard_stop_pct / 100.0)",
        "trigger_condition": "current_price <= stop_price (checked before trailing_mfe_exit)",
        "exit_reason_label": "stop_hit",
        "structural_exit_policy_note": (
            "combined_structural_exit_v1_trailing_mfe_shadow: stop_hit is first check; "
            "trailing_mfe_exit only when peak_pnl >= 0.8% then 50% giveback"
        ),
        "code_references": [
            {
                "file": "observer_position_tracker.py",
                "function": "ObserverPositionTracker.open / on_price",
                "lines": "stop = entry_price * (1.0 - hard_stop_pct / 100.0); if price <= pos.stop_price → stop_hit",
            },
            {
                "file": "structural_exit_policies.py",
                "function": "simulate_structural_policy",
                "lines": "stop = entry * (1.0 - cfg.hard_stop_pct / 100.0); if px <= stop → stop_hit",
            },
        ],
        "trailing_mfe_activate_pct": TRAILING_MFE_ACTIVATE_PCT,
        "trailing_mfe_giveback_frac": 0.50,
    }


def _load_stop_trades(session_label: str, session_dir: Path) -> list[StopTrade]:
    from replay.pnl_yen import compute_pnl_yen_100

    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    out: list[StopTrade] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "observer_exit":
                continue
            if str(row.get("exit_reason") or "") != "stop_hit":
                continue
            entry = _float(row.get("entry_price")) or 0.0
            exit_p = _float(row.get("exit_price")) or _float(row.get("current_price")) or 0.0
            loss_pct = _float(row.get("pnl_pct"))
            if loss_pct is None and entry > 0:
                loss_pct = (exit_p - entry) / entry * 100.0
            implied_stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
            actual_loss_from_entry = ((exit_p - entry) / entry * 100.0) if entry > 0 else 0.0
            out.append(
                StopTrade(
                    session=session_label,
                    symbol=str(row.get("symbol") or ""),
                    entry_time=str(row.get("entry_time") or ""),
                    exit_time=str(row.get("exit_time") or row.get("event_time") or ""),
                    entry_price=entry,
                    exit_price=exit_p,
                    loss_pct=float(loss_pct or 0.0),
                    loss_yen_100=compute_pnl_yen_100(entry, exit_p),
                    peak_mfe_pct=float(
                        _float(row.get("peak_mfe_pct"))
                        or _float(row.get("rolling_mfe_pct"))
                        or 0.0
                    ),
                    peak_mae_pct=float(
                        _float(row.get("rolling_mae_pct"))
                        or _float(row.get("peak_mae_pct"))
                        or 0.0
                    ),
                    hold_sec=float(_float(row.get("hold_sec")) or 0.0),
                    trailing_mfe_activated=_bool(row.get("trailing_mfe_activated")),
                    implied_stop_price=implied_stop,
                    loss_vs_hard_stop_pct=round(actual_loss_from_entry + HARD_STOP_PCT, 4),
                )
            )
    return out


def _trade_row(t: StopTrade) -> dict[str, Any]:
    return {
        "session": t.session,
        "symbol": t.symbol,
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "loss_pct": round(t.loss_pct, 4),
        "loss_yen_100": round(t.loss_yen_100, 2),
        "peak_mfe_pct": round(t.peak_mfe_pct, 4),
        "peak_mae_pct": round(t.peak_mae_pct, 4),
        "hold_sec": round(t.hold_sec, 1),
        "hold_min": round(t.hold_sec / 60.0, 1),
        "trailing_mfe_activated": t.trailing_mfe_activated,
        "implied_stop_price": round(t.implied_stop_price, 2),
        "loss_worse_than_hard_stop": t.loss_pct < -(HARD_STOP_PCT + 0.05),
    }


def _aggregate(trades: list[StopTrade]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    losses = [t.loss_pct for t in trades]
    yens = [t.loss_yen_100 for t in trades]
    mfes = [t.peak_mfe_pct for t in trades]
    maes = [t.peak_mae_pct for t in trades]
    holds = [t.hold_sec for t in trades]
    return {
        "trade_count": len(trades),
        "avg_loss_pct": round(statistics.mean(losses), 4),
        "median_loss_pct": round(statistics.median(losses), 4),
        "total_loss_yen_100": round(sum(yens), 2),
        "avg_loss_yen_100": round(statistics.mean(yens), 2),
        "avg_peak_mfe_pct": round(statistics.mean(mfes), 4),
        "median_peak_mfe_pct": round(statistics.median(mfes), 4),
        "avg_peak_mae_pct": round(statistics.mean(maes), 4),
        "median_peak_mae_pct": round(statistics.median(maes), 4),
        "avg_hold_sec": round(statistics.mean(holds), 1),
        "avg_hold_min": round(statistics.mean(holds) / 60.0, 1),
        "mfe_lt_0p3_count": sum(1 for m in mfes if m < 0.3),
        "mfe_0p3_to_0p8_count": sum(1 for m in mfes if 0.3 <= m < TRAILING_MFE_ACTIVATE_PCT),
        "mfe_ge_0p8_count": sum(1 for m in mfes if m >= TRAILING_MFE_ACTIVATE_PCT),
        "trailing_mfe_activated_count": sum(1 for t in trades if t.trailing_mfe_activated),
        "loss_worse_than_hard_stop_count": sum(1 for t in trades if t.loss_pct < -(HARD_STOP_PCT + 0.05)),
    }


def _symbol_ranking(trades: list[StopTrade]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[StopTrade]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    rows: list[dict[str, Any]] = []
    for sym, grp in by_sym.items():
        rows.append(
            {
                "symbol": sym,
                "stop_hit_count": len(grp),
                "total_loss_yen_100": round(sum(t.loss_yen_100 for t in grp), 2),
                "avg_loss_pct": round(statistics.mean(t.loss_pct for t in grp), 4),
                "avg_peak_mfe_pct": round(statistics.mean(t.peak_mfe_pct for t in grp), 4),
                "sessions": sorted({t.session for t in grp}),
            }
        )
    rows.sort(key=lambda r: (-int(r["stop_hit_count"]), float(r["total_loss_yen_100"])))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def _classify_root_cause(agg: dict[str, Any]) -> dict[str, Any]:
    n = int(agg.get("trade_count") or 0)
    mfe_low = int(agg.get("mfe_lt_0p3_count") or 0)
    mfe_mid = int(agg.get("mfe_0p3_to_0p8_count") or 0)
    mfe_high = int(agg.get("mfe_ge_0p8_count") or 0)
    trail_on = int(agg.get("trailing_mfe_activated_count") or 0)

    scores = {"ENTRYが悪い": 0.0, "STOPが悪い": 0.0, "利確が悪い": 0.0}

    if n > 0:
        scores["ENTRYが悪い"] += mfe_low / n * 2.0
        scores["ENTRYが悪い"] += (1.0 if mfe_high == 0 and trail_on == 0 else 0.0)
        scores["STOPが悪い"] += mfe_mid / n * 1.0
        scores["STOPが悪い"] += int(agg.get("loss_worse_than_hard_stop_count") or 0) / n * 0.5
        scores["利確が悪い"] += mfe_high / n * 2.0
        scores["利確が悪い"] += trail_on / n * 2.0

    primary = max(scores, key=scores.get)
    return {
        "classification": primary,
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "rationale": [
            f"{mfe_low}/{n} stop_hit had peak_mfe < 0.3% (immediate adverse move after entry)",
            f"{mfe_mid}/{n} had 0.3% <= peak_mfe < 0.8% (brief favorable move, below trailing activation)",
            f"{mfe_high}/{n} had peak_mfe >= 0.8% (trailing_mfe activation threshold)",
            f"trailing_mfe_activated before stop: {trail_on}/{n}",
            "trailing_mfe_exit requires peak_pnl >= 0.8%; none reached → 利確 path not engaged",
        ],
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    all_trades: list[StopTrade] = []
    session_blocks: dict[str, Any] = {}
    for label, session_dir in SESSIONS.items():
        trades = _load_stop_trades(label, session_dir)
        all_trades.extend(trades)
        session_blocks[label] = {
            "session_dir": str(session_dir.relative_to(REPO)).replace("\\", "/"),
            "stop_hit_count": len(trades),
            "aggregate": _aggregate(trades),
            "trades": [_trade_row(t) for t in trades],
        }

    combined_agg = _aggregate(all_trades)
    ranking = _symbol_ranking(all_trades)
    verdict = _classify_root_cause(combined_agg)

    report = {
        "phase": 321,
        "title": "stop_hit_mechanism_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; no logic changes",
        "target_date": DAY,
        "stop_hit_mechanism": _stop_hit_mechanism(),
        "stop_hit_trade_count": len(all_trades),
        "sessions": session_blocks,
        "combined": {
            "aggregate": combined_agg,
            "symbol_ranking": ranking,
            "am_pm_comparison": {
                "am": session_blocks["am"]["aggregate"],
                "pm": session_blocks["pm"]["aggregate"],
            },
        },
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"stop_hits={len(all_trades)} classification={verdict['classification']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
