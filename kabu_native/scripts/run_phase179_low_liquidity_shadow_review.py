#!/usr/bin/env python3
"""
Phase179: Shadow-only introduction review for low-liquidity filter (logging only).

Outputs:
- kabu_native/results/reports/phase179_low_liquidity_shadow_review.json

This is replay/review-first: it reads existing session artifacts and estimates:
- how many accepted would be shadow-rejected
- whether thin names like 2693.T / 6969.T are flagged
- whether liquid but low-priced 6659.T is preserved
- PF impact estimate (using Phase178 baseline sessions structural_trades.csv)
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional


OUT = Path("kabu_native/results/reports/phase179_low_liquidity_shadow_review.json")

TV_MIN = 1e8
TO_MIN = 0.002

TODAY_SESSIONS = [
    Path("kabu_native/results/small_paper/20260528/live_session_082247"),
    Path("kabu_native/results/small_paper/20260528/live_session_122515"),
]

PF_ESTIMATE_SESSIONS = [
    Path("kabu_native/results/small_paper/20260519/live_full_session_081047"),
    Path("kabu_native/results/small_paper/20260520/live_full_session_080745"),
    Path("kabu_native/results/small_paper/20260520/push_replay_001932"),
    Path("kabu_native/results/small_paper/20260520/push_replay_231314"),
    Path("kabu_native/results/small_paper/20260521/live_full_session_081418"),
    Path("kabu_native/results/small_paper/20260522/live_full_session_081229"),
    Path("kabu_native/results/small_paper/20260525/live_session_075733"),
]

FOCUS = {"2693.T", "6969.T", "6659.T"}


def _f(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _pf(win: float, loss: float) -> Optional[float]:
    gl = abs(loss)
    if gl <= 0:
        return None if win <= 0 else float("inf")
    return win / gl


def _accept_key(symbol: str, entry_time: str) -> str:
    return f"{symbol}|{entry_time}"


def _load_accept_snapshots(events_csv: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not events_csv.is_file():
        return out
    with events_csv.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("event_type") or "") != "accepted":
                continue
            sym = str(r.get("symbol") or "").strip()
            ent = str(r.get("entry_time") or "").strip()
            if not sym or not ent:
                continue
            out[_accept_key(sym, ent)] = {
                "symbol": sym,
                "entry_time": ent,
                "current_price": _f(r.get("current_price")),
                "trading_value": _f(r.get("trading_value")),
                "turnover_proxy": _f(r.get("turnover_proxy")),
            }
    return out


def _shadow_reject(liq: dict[str, Any]) -> tuple[bool, str]:
    tv = _f(liq.get("trading_value"))
    to = _f(liq.get("turnover_proxy"))
    if tv is not None and tv < TV_MIN:
        return True, "trading_value_below_min"
    if to is not None and to < TO_MIN:
        return True, "turnover_proxy_below_min"
    return False, ""


def _load_trades(trades_csv: Path) -> list[dict[str, Any]]:
    if not trades_csv.is_file():
        return []
    with trades_csv.open("r", encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _summarize_pf(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [_f(t.get("realized_pnl_pct")) or 0.0 for t in trades]
    wins = sum(x for x in pnls if x > 0)
    losses = sum(x for x in pnls if x < 0)
    pf = _pf(wins, losses)
    return {
        "trade_count": len(trades),
        "total_pnl": round(sum(pnls), 4),
        "total_win_pnl": round(wins, 4),
        "total_loss_pnl": round(losses, 4),
        "pf": round(pf, 4) if pf is not None and pf not in (float("inf"),) else pf,
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # Today: accepted vs shadow rejects
    today_rows: list[dict[str, Any]] = []
    focus_checks: dict[str, dict[str, Any]] = {s: {"accepted": 0, "shadow_rejected": 0, "examples": []} for s in FOCUS}
    total_accepted = 0
    total_shadow_reject = 0
    reason_counts = Counter()

    for sdir in TODAY_SESSIONS:
        ev = sdir / "small_paper_events.csv"
        summ = sdir / "small_paper_summary.json"
        summ_j = json.loads(summ.read_text(encoding="utf-8")) if summ.is_file() else {}
        acc_syms = set(summ_j.get("accepted_symbols") or [])

        snaps = _load_accept_snapshots(ev)
        for k, liq in snaps.items():
            total_accepted += 1
            sym = str(liq.get("symbol") or "")
            rejected, why = _shadow_reject(liq)
            if rejected:
                total_shadow_reject += 1
                reason_counts[why] += 1
            today_rows.append(
                {
                    "session_dir": str(sdir).replace("\\", "/"),
                    "symbol": sym,
                    "entry_time": liq.get("entry_time"),
                    "current_price": liq.get("current_price"),
                    "trading_value": liq.get("trading_value"),
                    "turnover_proxy": liq.get("turnover_proxy"),
                    "shadow_rejected": rejected,
                    "shadow_reason": why,
                    "in_accepted_symbols_list": sym in acc_syms,
                }
            )
            if sym in focus_checks:
                focus_checks[sym]["accepted"] += 1
                if rejected:
                    focus_checks[sym]["shadow_rejected"] += 1
                if len(focus_checks[sym]["examples"]) < 5:
                    focus_checks[sym]["examples"].append(
                        {
                            "entry_time": liq.get("entry_time"),
                            "current_price": liq.get("current_price"),
                            "trading_value": liq.get("trading_value"),
                            "turnover_proxy": liq.get("turnover_proxy"),
                            "shadow_rejected": rejected,
                            "shadow_reason": why,
                        }
                    )

    # PF impact estimate (same as Phase178 post-hoc exclusion, but presented as "shadow reject would have removed these trades")
    all_A: list[dict[str, Any]] = []
    all_B: list[dict[str, Any]] = []
    excluded = 0
    for sdir in PF_ESTIMATE_SESSIONS:
        trades_csv = sdir / "structural_trades.csv"
        events_csv = sdir / "small_paper_events.csv"
        trades = _load_trades(trades_csv)
        snaps = _load_accept_snapshots(events_csv)
        all_A.extend(trades)
        for t in trades:
            k = _accept_key(str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            liq = snaps.get(k)
            if liq is None:
                all_B.append(t)
                continue
            rejected, _why = _shadow_reject(liq)
            if rejected:
                excluded += 1
                continue
            all_B.append(t)

    report = {
        "phase": 179,
        "verdict": "review_only_shadow_logging",
        "rule": {"trading_value_min": TV_MIN, "turnover_proxy_min": TO_MIN},
        "intent": [
            "shadow logging only (no hard reject yet)",
            "aim: suppress extreme low-liquidity accepted (e.g., 2693.T/6969.T) while keeping low-priced but liquid names (e.g., 6659.T)",
        ],
        "today_shadow_effect": {
            "sessions": [str(p).replace("\\", "/") for p in TODAY_SESSIONS],
            "accepted_count": total_accepted,
            "low_liquidity_shadow_reject_count": total_shadow_reject,
            "reason_counts": dict(reason_counts),
            "focus": focus_checks,
        },
        "pf_impact_estimate": {
            "sessions": [str(p).replace("\\", "/") for p in PF_ESTIMATE_SESSIONS],
            "A_current": _summarize_pf(all_A),
            "B_excluding_shadow_reject_trades": _summarize_pf(all_B),
            "excluded_trade_count": excluded,
            "note": "Estimate uses structural_trades.csv + accepted-time liquidity from small_paper_events.csv; same logic as Phase178 but framed as shadow-only filter effect.",
        },
        "liquidity_distribution_today": {
            "rows_sample": today_rows[:50],
        },
        "notes": [
            "Next step for live shadow: enable low_liquidity_shadow_enabled in a non-prod shadow YAML to log counts without blocking.",
            "This report is replay/review-first and does not modify production YAML.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

