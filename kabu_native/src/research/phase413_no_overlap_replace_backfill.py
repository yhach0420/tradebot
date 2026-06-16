"""
Phase413: No overlap replace — Runtime adoption review (research backfill).

Goal:
- Prevent overlap_replaced_review chain by keeping existing position open
  and ignoring same-symbol ENTRY while open.

Backfill approximation:
- For same (day, session, symbol), collapse consecutive trades where the prior trade
  exited via overlap_replaced_review (overlap replaced) and the next trade entry is
  effectively immediate.
- The collapsed shadow position keeps the first entry_time and the last exit_time,
  with pnl = sum(pnl segments). This approximates "do not close/reopen at overlap".

Research / report only. Does not modify Runtime / YAML / Entry / Exit / Orders.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase400_holding_time_audit import hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase406_portfolio_adoption import load_phase405_boundary_policy
from research.phase409_boundary_forward_shadow import FORWARD_PERIOD_START, load_structural_trades_for_day
from research.phase410_duplicate_reentry_audit import _phase409_skip_reason

JST = ZoneInfo("Asia/Tokyo")

POLICY = "no_overlap_replace"
REJECT_SAME_SYMBOL_OPEN_OVERLAP = "REJECT_SAME_SYMBOL_OPEN_OVERLAP"
PERIOD_START = "20260529"
PERIOD_END = "20260616"
INITIAL_EQUITY_YEN = 1_500_000.0

PHASE399_TRADES_CSV = "phase399_historical_position_cap_backfill_trades.csv"

TRADES_FIELDS = [
    "logged_at",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "exit_reason",
    "baseline_included",
    "shadow_included",
    "reject_reason",
    "pnl_yen_100",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "shadow_exit_time",
    "shadow_hold_sec",
    "shadow_exit_reason",
]

DAILY_FIELDS = [
    "day",
    "session_count",
    "trade_count",
    "shadow_trade_count",
    "overlap_replaced_review_count",
    "shadow_overlap_replaced_review_count",
    "rejected_same_symbol_overlap_count",
    "total_pnl_yen_100",
    "shadow_total_pnl_yen_100",
    "delta_pnl_yen_100",
    "pf",
    "shadow_pf",
    "maxdd",
    "shadow_maxdd",
    "final_equity",
    "shadow_final_equity",
    "win_rate",
    "shadow_win_rate",
    "avg_hold_sec",
    "shadow_avg_hold_sec",
    "median_hold_sec",
    "shadow_median_hold_sec",
    "stop_hit_count",
    "shadow_stop_hit_count",
    "trailing_mfe_count",
    "shadow_trailing_mfe_count",
    "session_close_count",
    "shadow_session_close_count",
    "affected_symbols",
    "boundary_eligible_count",
    "shadow_boundary_eligible_count",
    "phase409_would_hit_count",
    "shadow_phase409_would_hit_count",
    "status",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _win_rate(pnls: Sequence[float]) -> float:
    if not pnls:
        return 0.0
    wins = sum(1 for p in pnls if p > 0)
    return round(100.0 * wins / len(pnls), 2)


def _counts_by_bucket(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {"overlap_replaced_review": 0, "stop_hit": 0, "trailing_mfe": 0, "session_close": 0}
    for t in trades:
        bucket = normalize_exit_reason(str(t.get("exit_reason") or t.get("close_reason") or ""))
        if bucket == "overlap_replaced":
            out["overlap_replaced_review"] += 1
        elif bucket == "stop_hit":
            out["stop_hit"] += 1
        elif bucket == "trailing_mfe":
            out["trailing_mfe"] += 1
        elif bucket == "session_close":
            out["session_close"] += 1
    return out


def _ensure_hold_sec(trade: Mapping[str, Any]) -> float:
    hs = trade.get("hold_sec")
    if hs not in (None, ""):
        return _float(hs)
    return hold_seconds(str(trade.get("entry_time") or ""), str(trade.get("exit_time") or ""))


def _chronological_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trades)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    return [_float(trades[i].get("pnl_yen_100_float") or trades[i].get("pnl_yen_100") or 0) for i in order]


def _load_phase399_position_cap_baseline(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / "results" / "reports" / PHASE399_TRADES_CSV
    rows = _read_csv_rows(path)
    out: list[dict[str, Any]] = []
    for r in rows:
        if not (PERIOD_START <= str(r.get("day") or "") <= "20260615"):
            continue
        if not _bool(r.get("position_cap_accepted")):
            continue
        t = dict(r)
        t["pnl_yen_100_float"] = _float(t.get("pnl_yen_100"))
        t["exit_reason"] = t.get("exit_reason") or t.get("close_reason") or ""
        t["hold_sec"] = _ensure_hold_sec(t)
        out.append(t)
    return out


def _load_baseline_trades_for_period(repo_root: Path) -> list[dict[str, Any]]:
    baseline = _load_phase399_position_cap_baseline(repo_root)
    day_616 = "20260616"
    if PERIOD_START <= day_616 <= PERIOD_END:
        baseline.extend(load_structural_trades_for_day(repo_root, day_616))
    for t in baseline:
        t["pnl_yen_100_float"] = _float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100"))
        t["exit_reason"] = t.get("exit_reason") or t.get("close_reason") or ""
        t["hold_sec"] = _ensure_hold_sec(t)
    baseline.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            str(r.get("session") or ""),
            str(r.get("symbol") or ""),
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
        )
    )
    return baseline


def _immediate_gap_sec(prev_exit_time: str, next_entry_time: str) -> Optional[float]:
    ex = _parse_ts(str(prev_exit_time or ""))
    ent = _parse_ts(str(next_entry_time or ""))
    if not ex or not ent:
        return None
    return max(0.0, ent.timestamp() - ex.timestamp())


def _is_overlap_exit(reason: str) -> bool:
    r = str(reason or "")
    return r == "overlap_replaced_review" or normalize_exit_reason(r) == "overlap_replaced"


def collapse_overlap_replace_chains(
    baseline_trades: Sequence[Mapping[str, Any]],
    *,
    max_gap_sec: float = 2.0,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str, str], dict[str, Any]]]:
    """
    Returns:
      - shadow_positions: collapsed positions (trade-like dicts)
      - mapping: (day, session, symbol, entry_time of baseline row) -> shadow info
        For chain-start row: includes shadow_exit_time/hold/pnl/exit_reason + shadow_included=True
        For rejected rows: shadow_included=False + reject_reason
    """
    # group by (day, session, symbol)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for t in baseline_trades:
        key = (str(t.get("day") or ""), str(t.get("session") or ""), str(t.get("symbol") or ""))
        groups.setdefault(key, []).append(dict(t))
    for key in groups:
        groups[key].sort(
            key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))
        )

    mapping: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    shadow_positions: list[dict[str, Any]] = []

    for (day, session, sym), trades in groups.items():
        i = 0
        while i < len(trades):
            start = trades[i]
            chain = [start]
            j = i
            while j + 1 < len(trades):
                cur = trades[j]
                nxt = trades[j + 1]
                if str(nxt.get("symbol") or "") != sym:
                    break
                if not _is_overlap_exit(str(cur.get("exit_reason") or "")):
                    break
                gap = _immediate_gap_sec(str(cur.get("exit_time") or ""), str(nxt.get("entry_time") or ""))
                if gap is None or gap > max_gap_sec:
                    break
                chain.append(nxt)
                j += 1

            if len(chain) == 1:
                # unchanged position
                shadow_positions.append(dict(start))
                base_key = (day, session, sym, str(start.get("entry_time") or ""))
                mapping[base_key] = {
                    "shadow_included": True,
                    "reject_reason": "",
                    "shadow_exit_time": start.get("exit_time"),
                    "shadow_exit_reason": start.get("exit_reason"),
                    "shadow_pnl_yen_100": _float(start.get("pnl_yen_100_float") or start.get("pnl_yen_100") or 0),
                    "shadow_hold_sec": _ensure_hold_sec(start),
                }
                i += 1
                continue

            # collapse chain into one shadow position
            first = chain[0]
            last = chain[-1]
            pnl_sum = round(
                sum(_float(x.get("pnl_yen_100_float") or x.get("pnl_yen_100") or 0) for x in chain), 2
            )
            shadow_pos = dict(first)
            shadow_pos["exit_time"] = last.get("exit_time")
            shadow_pos["exit_reason"] = last.get("exit_reason") or last.get("close_reason") or ""
            shadow_pos["pnl_yen_100_float"] = pnl_sum
            shadow_pos["pnl_yen_100"] = pnl_sum
            shadow_pos["hold_sec"] = hold_seconds(str(first.get("entry_time") or ""), str(last.get("exit_time") or ""))
            shadow_positions.append(shadow_pos)

            # map chain-start as kept with collapsed info
            start_key = (day, session, sym, str(first.get("entry_time") or ""))
            mapping[start_key] = {
                "shadow_included": True,
                "reject_reason": "",
                "shadow_exit_time": shadow_pos.get("exit_time"),
                "shadow_exit_reason": shadow_pos.get("exit_reason"),
                "shadow_pnl_yen_100": pnl_sum,
                "shadow_hold_sec": shadow_pos.get("hold_sec"),
                "collapsed_chain_len": len(chain),
            }
            # map remaining chain items as rejected
            for k in range(1, len(chain)):
                row = chain[k]
                rk = (day, session, sym, str(row.get("entry_time") or ""))
                mapping[rk] = {
                    "shadow_included": False,
                    "reject_reason": REJECT_SAME_SYMBOL_OPEN_OVERLAP,
                    "shadow_exit_time": "",
                    "shadow_exit_reason": "",
                    "shadow_pnl_yen_100": 0.0,
                    "shadow_hold_sec": 0.0,
                    "collapsed_into_entry_time": str(first.get("entry_time") or ""),
                }

            i = j + 1

    shadow_positions.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return shadow_positions, mapping


def build_trade_rows(
    baseline_trades: Sequence[Mapping[str, Any]],
    *,
    mapping: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    logged_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in baseline_trades:
        day = str(t.get("day") or "")
        session = str(t.get("session") or "")
        sym = str(t.get("symbol") or "")
        ent = str(t.get("entry_time") or "")
        key = (day, session, sym, ent)
        shadow = dict(mapping.get(key) or {})
        pnl = _float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0)
        rows.append(
            {
                "logged_at": logged_at,
                "day": day,
                "session": session,
                "symbol": sym,
                "entry_time": ent,
                "exit_time": t.get("exit_time"),
                "hold_sec": _ensure_hold_sec(t),
                "exit_reason": t.get("exit_reason"),
                "baseline_included": True,
                "shadow_included": bool(shadow.get("shadow_included")),
                "reject_reason": shadow.get("reject_reason") or "",
                "pnl_yen_100": round(pnl, 2),
                "baseline_pnl_yen_100": round(pnl, 2),
                "shadow_pnl_yen_100": round(_float(shadow.get("shadow_pnl_yen_100")), 2)
                if shadow
                else round(pnl, 2),
                "shadow_exit_time": shadow.get("shadow_exit_time") or (t.get("exit_time") if shadow else t.get("exit_time")),
                "shadow_hold_sec": _float(shadow.get("shadow_hold_sec")) if shadow else _ensure_hold_sec(t),
                "shadow_exit_reason": shadow.get("shadow_exit_reason") or (t.get("exit_reason") if shadow else t.get("exit_reason")),
            }
        )
    return rows


def _boundary_counts(
    trades: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path,
    boundary_rules: Mapping[int, Any],
) -> tuple[int, int]:
    eligible = 0
    would_hit = 0
    session_cache: dict[str, Any] = {}
    for t in trades:
        skip, ok, hit = _phase409_skip_reason(
            t, repo_root=repo_root, session_cache=session_cache, boundary_rules=boundary_rules
        )
        if ok:
            eligible += 1
        if hit:
            would_hit += 1
    return eligible, would_hit


def aggregate_daily(
    baseline: Sequence[Mapping[str, Any]],
    shadow: Sequence[Mapping[str, Any]],
    *,
    day: str,
    repo_root: Path,
    boundary_rules: Mapping[int, Any],
    rejected_count: int,
) -> dict[str, Any]:
    sessions = {str(t.get("session") or "") for t in baseline if t.get("session")}
    base_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in baseline]
    sh_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in shadow]
    base_holds = [float(_ensure_hold_sec(t)) for t in baseline]
    sh_holds = [float(_ensure_hold_sec(t)) for t in shadow]
    base_counts = _counts_by_bucket(baseline)
    sh_counts = _counts_by_bucket(shadow)

    base_total = round(sum(base_pnls), 2)
    sh_total = round(sum(sh_pnls), 2)
    base_chron = _chronological_pnls(baseline)
    sh_chron = _chronological_pnls(shadow)

    base_eligible, base_hit = _boundary_counts(baseline, repo_root=repo_root, boundary_rules=boundary_rules)
    sh_eligible, sh_hit = _boundary_counts(shadow, repo_root=repo_root, boundary_rules=boundary_rules)

    affected_syms = sorted({str(t.get("symbol") or "") for t in baseline if _is_overlap_exit(str(t.get("exit_reason") or ""))})

    return {
        "day": day,
        "session_count": len(sessions),
        "trade_count": len(baseline),
        "shadow_trade_count": len(shadow),
        "overlap_replaced_review_count": int(base_counts["overlap_replaced_review"]),
        "shadow_overlap_replaced_review_count": int(sh_counts["overlap_replaced_review"]),
        "rejected_same_symbol_overlap_count": int(rejected_count),
        "total_pnl_yen_100": base_total,
        "shadow_total_pnl_yen_100": sh_total,
        "delta_pnl_yen_100": round(sh_total - base_total, 2),
        "pf": _pf(base_chron),
        "shadow_pf": _pf(sh_chron),
        "maxdd": _max_drawdown_yen(base_chron),
        "shadow_maxdd": _max_drawdown_yen(sh_chron),
        "final_equity": round(INITIAL_EQUITY_YEN + base_total, 2),
        "shadow_final_equity": round(INITIAL_EQUITY_YEN + sh_total, 2),
        "win_rate": _win_rate(base_pnls),
        "shadow_win_rate": _win_rate(sh_pnls),
        "avg_hold_sec": round(sum(base_holds) / len(base_holds), 2) if base_holds else 0.0,
        "shadow_avg_hold_sec": round(sum(sh_holds) / len(sh_holds), 2) if sh_holds else 0.0,
        "median_hold_sec": round(median(base_holds), 2) if base_holds else 0.0,
        "shadow_median_hold_sec": round(median(sh_holds), 2) if sh_holds else 0.0,
        "stop_hit_count": int(base_counts["stop_hit"]),
        "shadow_stop_hit_count": int(sh_counts["stop_hit"]),
        "trailing_mfe_count": int(base_counts["trailing_mfe"]),
        "shadow_trailing_mfe_count": int(sh_counts["trailing_mfe"]),
        "session_close_count": int(base_counts["session_close"]),
        "shadow_session_close_count": int(sh_counts["session_close"]),
        "affected_symbols": ",".join([s for s in affected_syms if s]),
        "boundary_eligible_count": int(base_eligible),
        "shadow_boundary_eligible_count": int(sh_eligible),
        "phase409_would_hit_count": int(base_hit),
        "shadow_phase409_would_hit_count": int(sh_hit),
        "status": "ok",
    }


def aggregate_cumulative(
    baseline_all: Sequence[Mapping[str, Any]],
    shadow_all: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in baseline_all]
    sh_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in shadow_all]
    base_total = round(sum(base_pnls), 2)
    sh_total = round(sum(sh_pnls), 2)

    base_chron = _chronological_pnls(baseline_all)
    sh_chron = _chronological_pnls(shadow_all)
    base_pf = _pf(base_chron)
    sh_pf = _pf(sh_chron)
    base_dd = _max_drawdown_yen(base_chron)
    sh_dd = _max_drawdown_yen(sh_chron)

    base_counts = _counts_by_bucket(baseline_all)
    sh_counts = _counts_by_bucket(shadow_all)

    affected_days = sorted(
        [str(r.get("day") or "") for r in daily_rows if int(_float(r.get("rejected_same_symbol_overlap_count"))) > 0]
    )
    affected_symbols = sorted(
        {s for r in daily_rows for s in str(r.get("affected_symbols") or "").split(",") if s.strip()}
    )
    base_boundary_eligible = sum(int(_float(r.get("boundary_eligible_count"))) for r in daily_rows)
    sh_boundary_eligible = sum(int(_float(r.get("shadow_boundary_eligible_count"))) for r in daily_rows)
    base_would_hit = sum(int(_float(r.get("phase409_would_hit_count"))) for r in daily_rows)
    sh_would_hit = sum(int(_float(r.get("shadow_phase409_would_hit_count"))) for r in daily_rows)

    base_boundary_rate = round(100.0 * base_boundary_eligible / max(1, len(baseline_all)), 2)
    sh_boundary_rate = round(100.0 * sh_boundary_eligible / max(1, len(shadow_all)), 2)
    base_hit_rate = round(100.0 * base_would_hit / max(1, len(baseline_all)), 2)
    sh_hit_rate = round(100.0 * sh_would_hit / max(1, len(shadow_all)), 2)

    verdict = "reject_runtime_adoption"
    adopt_allowed = (
        sh_total >= base_total
        and (sh_pf or 0) >= (base_pf or 0)
        and sh_dd <= base_dd + 1e-6
        and (base_counts["overlap_replaced_review"] - sh_counts["overlap_replaced_review"]) > 0
        and (len(baseline_all) - len(shadow_all)) > 0
    )
    if adopt_allowed:
        verdict = "runtime_adoption_candidate"

    return {
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "policy": POLICY,
        "baseline_trade_count": len(baseline_all),
        "shadow_trade_count": len(shadow_all),
        "trade_reduction_count": len(baseline_all) - len(shadow_all),
        "baseline_total_pnl_yen_100": base_total,
        "shadow_total_pnl_yen_100": sh_total,
        "delta_pnl_yen_100": round(sh_total - base_total, 2),
        "baseline_pf": base_pf,
        "shadow_pf": sh_pf,
        "baseline_maxdd": base_dd,
        "shadow_maxdd": sh_dd,
        "baseline_final_equity": round(INITIAL_EQUITY_YEN + base_total, 2),
        "shadow_final_equity": round(INITIAL_EQUITY_YEN + sh_total, 2),
        "baseline_win_rate": _win_rate(base_pnls),
        "shadow_win_rate": _win_rate(sh_pnls),
        "baseline_avg_hold_sec": round(
            sum(float(_ensure_hold_sec(t)) for t in baseline_all) / max(1, len(baseline_all)), 2
        ),
        "shadow_avg_hold_sec": round(
            sum(float(_ensure_hold_sec(t)) for t in shadow_all) / max(1, len(shadow_all)), 2
        ),
        "baseline_median_hold_sec": round(median([float(_ensure_hold_sec(t)) for t in baseline_all]), 2)
        if baseline_all
        else 0.0,
        "shadow_median_hold_sec": round(median([float(_ensure_hold_sec(t)) for t in shadow_all]), 2)
        if shadow_all
        else 0.0,
        "baseline_overlap_replaced_review_count": int(base_counts["overlap_replaced_review"]),
        "shadow_overlap_replaced_review_count": int(sh_counts["overlap_replaced_review"]),
        "overlap_replaced_review_reduction_count": int(
            base_counts["overlap_replaced_review"] - sh_counts["overlap_replaced_review"]
        ),
        "baseline_stop_hit_count": int(base_counts["stop_hit"]),
        "shadow_stop_hit_count": int(sh_counts["stop_hit"]),
        "baseline_trailing_mfe_count": int(base_counts["trailing_mfe"]),
        "shadow_trailing_mfe_count": int(sh_counts["trailing_mfe"]),
        "baseline_session_close_count": int(base_counts["session_close"]),
        "shadow_session_close_count": int(sh_counts["session_close"]),
        "affected_days": affected_days,
        "affected_symbols": affected_symbols,
        "boundary_eligible_count": base_boundary_eligible,
        "shadow_boundary_eligible_count": sh_boundary_eligible,
        "boundary_eligible_rate_pct": base_boundary_rate,
        "shadow_boundary_eligible_rate_pct": sh_boundary_rate,
        "phase409_would_hit_count": base_would_hit,
        "shadow_phase409_would_hit_count": sh_would_hit,
        "phase409_would_hit_rate_pct": base_hit_rate,
        "shadow_phase409_would_hit_rate_pct": sh_hit_rate,
        "adoption_gate": {
            "pnl_ge_baseline": sh_total >= base_total,
            "pf_ge_baseline": (sh_pf or 0) >= (base_pf or 0),
            "maxdd_le_baseline": sh_dd <= base_dd + 1e-6,
            "overlap_reduced": (base_counts["overlap_replaced_review"] - sh_counts["overlap_replaced_review"]) > 0,
            "trade_count_reduced": (len(baseline_all) - len(shadow_all)) > 0,
            "hold_naturally_extended": (
                (median([float(_ensure_hold_sec(t)) for t in shadow_all]) if shadow_all else 0.0)
                >= (median([float(_ensure_hold_sec(t)) for t in baseline_all]) if baseline_all else 0.0)
            ),
            "boundary_eval_rate_improved": sh_hit_rate >= base_hit_rate,
        },
        "verdict": verdict,
        "runtime_change_forbidden_until_adoption": True,
    }


def render_report_md(*, summary: Mapping[str, Any], day_616: Mapping[str, Any]) -> str:
    verdict = str(summary.get("verdict") or "")
    allow = verdict == "runtime_adoption_candidate"
    lines: list[str] = []
    lines.append("# Phase413 — No Overlap Replace Runtime Adoption Review")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    lines.append(f"- **Runtime反映してよいか**: {'YES (candidate)' if allow else 'NO (do not adopt)'}")
    lines.append(f"- **反映理由**: verdict=`{verdict}` (PnL/PF/maxDD gate + churn reduction)")
    lines.append("- **rollback方法**: `same_symbol_open_policy: replace`")
    lines.append("")
    lines.append("## 必須回答")
    lines.append("")
    lines.append("- **1. Phase412と何が違うか**: Phase412は同一銘柄open中の新ENTRYを単純にreject（その結果、既存ポジションがbaselineでは早期に閉じていた分まで失われ得る）。Phase413は overlap_replaced_review 連鎖を“継続ポジション”に連結し、既存ポジション維持（hold延長）を近似する。")
    lines.append(f"- **2. overlap_replaced_review はどれだけ減るか**: {summary.get('baseline_overlap_replaced_review_count')} → {summary.get('shadow_overlap_replaced_review_count')} (Δ={summary.get('overlap_replaced_review_reduction_count')})")
    lines.append(f"- **3. trade_count はどれだけ減るか**: {summary.get('baseline_trade_count')} → {summary.get('shadow_trade_count')} (Δ={summary.get('trade_reduction_count')})")
    lines.append(f"- **4. PnL/PF/maxDD は改善するか**: PnL Δ={summary.get('delta_pnl_yen_100')}, PF {summary.get('baseline_pf')}→{summary.get('shadow_pf')}, maxDD {summary.get('baseline_maxdd')}→{summary.get('shadow_maxdd')}")
    lines.append(f"- **5. 保有時間は自然に伸びるか**: median_hold {summary.get('baseline_median_hold_sec')}→{summary.get('shadow_median_hold_sec')} / avg_hold {summary.get('baseline_avg_hold_sec')}→{summary.get('shadow_avg_hold_sec')}")
    lines.append(
        f"- **6. Boundary/Phase409評価可能性は上がるか**: eligible {summary.get('boundary_eligible_count')}→{summary.get('shadow_boundary_eligible_count')} "
        f"(rate {summary.get('boundary_eligible_rate_pct')}%→{summary.get('shadow_boundary_eligible_rate_pct')}%), "
        f"would_hit {summary.get('phase409_would_hit_count')}→{summary.get('shadow_phase409_would_hit_count')} "
        f"(rate {summary.get('phase409_would_hit_rate_pct')}%→{summary.get('shadow_phase409_would_hit_rate_pct')}%)"
    )
    lines.append(f"- **7. Runtime反映してよいか**: {'YES' if allow else 'NO'}")
    lines.append("- **8. 反映するなら rollback 方法**: `same_symbol_open_policy: replace`")
    lines.append("")
    lines.append("## 20260616 (churn day) check")
    lines.append("")
    lines.append(f"- baseline trades: {day_616.get('trade_count')} / shadow trades: {day_616.get('shadow_trade_count')}")
    lines.append(f"- overlap_replaced_review: {day_616.get('overlap_replaced_review_count')} → {day_616.get('shadow_overlap_replaced_review_count')}")
    lines.append(f"- boundary_eligible: {day_616.get('boundary_eligible_count')} → {day_616.get('shadow_boundary_eligible_count')}")
    lines.append("")
    return "\n".join(lines)


def run_phase413_backfill(*, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    logged_at = _now_iso()
    baseline_all = _load_baseline_trades_for_period(repo_root)

    phase405_policy_path = reports_dir / "phase405_time_boundary_policy.csv"
    boundary_rules = load_phase405_boundary_policy(phase405_policy_path)

    # Per-day baseline -> shadow collapse
    by_day: dict[str, list[dict[str, Any]]] = {}
    for t in baseline_all:
        day = str(t.get("day") or "")
        if not (PERIOD_START <= day <= PERIOD_END):
            continue
        by_day.setdefault(day, []).append(dict(t))
    for d in by_day:
        by_day[d].sort(
            key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))
        )

    shadow_all: list[dict[str, Any]] = []
    mapping_all: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    daily_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    for day in sorted(by_day.keys()):
        base_day = by_day[day]
        shadow_day, mapping = collapse_overlap_replace_chains(base_day)
        shadow_all.extend(dict(t) for t in shadow_day)
        mapping_all.update(mapping)

        rejected = sum(1 for v in mapping.values() if not bool(v.get("shadow_included")))
        daily_rows.append(
            aggregate_daily(
                base_day,
                shadow_day,
                day=day,
                repo_root=repo_root,
                boundary_rules=boundary_rules,
                rejected_count=rejected,
            )
        )

    trade_rows = build_trade_rows(baseline_all, mapping=mapping_all, logged_at=logged_at)
    summary = aggregate_cumulative(baseline_all, shadow_all, daily_rows)
    day_616 = next((r for r in daily_rows if str(r.get("day") or "") == "20260616"), {})

    paths = Phase413BackfillJob(repo_root=repo_root, reports_dir=reports_dir).paths()
    paths["trades"].parent.mkdir(parents=True, exist_ok=True)
    _write_csv(paths["trades"], trade_rows, TRADES_FIELDS)
    _write_csv(paths["daily"], daily_rows, DAILY_FIELDS)

    payload = {
        "phase": 413,
        "generated_at": logged_at,
        "policy": POLICY,
        "reject_reason": REJECT_SAME_SYMBOL_OPEN_OVERLAP,
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "forward_period_start": FORWARD_PERIOD_START,
        "inputs": {
            "phase399_trades_csv": str(repo_root / "results" / "reports" / PHASE399_TRADES_CSV),
            "day_616_source": "results/small_paper/20260616/*/structural_trades.csv",
            "phase405_policy_path": str(phase405_policy_path),
        },
        "summary": summary,
        "day_616_check": day_616,
        "output_paths": {k: str(v) for k, v in paths.items()},
        "constraints": {
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "exit_change_forbidden": True,
            "order_change_forbidden": True,
            "yaml_change_forbidden": True,
            "discord_change_forbidden": True,
        },
    }
    paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase413_no_overlap_replace_adoption_review.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report_md(summary=summary, day_616=day_616), encoding="utf-8")

    return payload


@dataclass
class Phase413BackfillJob:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "trades": self.reports_dir / "phase413_no_overlap_replace_backfill_trades.csv",
            "daily": self.reports_dir / "phase413_no_overlap_replace_backfill_daily.csv",
            "summary": self.reports_dir / "phase413_no_overlap_replace_backfill_summary.json",
        }

    def run(self) -> dict[str, Any]:
        return run_phase413_backfill(repo_root=self.repo_root, reports_dir=self.reports_dir)

