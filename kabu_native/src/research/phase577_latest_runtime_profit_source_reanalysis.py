"""
Phase577 — Latest Runtime profit source re-analysis (research only, no Runtime changes).

Analyzes accepted trades from 20260529 through latest live session data.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _is_no_progress,
    _load_canonical_trades_for_day,
)
from research.phase551_current_runtime_full_period_replay import _is_or_trade
from research.phase572_runtime_pipeline_visualization import SESSION_DIR_RE
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE577_VERDICT = "phase577_latest_runtime_profit_source_reanalysis_done"
PERIOD_START = "20260529"

SUMMARY_FIELDS = ["dimension", "key", "trades", "pnl_yen_100", "profit_factor", "win_rate", "pnl_share_pct"]
SYMBOL_FIELDS = ["rank", "symbol", "trades", "pnl_yen_100", "profit_factor", "win_rate", "pnl_share_pct"]
TIME_SESSION_FIELDS = ["bucket", "trades", "pnl_yen_100", "profit_factor", "win_rate", "pnl_share_pct"]
EXIT_ENTRY_FIELDS = ["dimension", "key", "trades", "pnl_yen_100", "profit_factor", "win_rate", "pnl_share_pct"]
GUARD_FIELDS = ["guard", "cohort", "trades", "pnl_yen_100", "profit_factor", "win_rate", "pnl_share_pct"]


def _discover_days(repo_root: Path) -> list[str]:
    kabu = resolve_kabu_root(repo_root)
    sp = kabu / "results" / "small_paper"
    days: list[str] = []
    if not sp.is_dir():
        return days
    for d in sorted(sp.iterdir()):
        if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
            continue
        if d.name < PERIOD_START:
            continue
        if any(d.glob("live_session_*")):
            days.append(d.name)
    return days


def _session_kind(session_name: str) -> str:
    m = SESSION_DIR_RE.match(session_name)
    if not m:
        return "unknown"
    return "pm" if int(m.group(1)[:2]) >= 12 else "am"


def _time_bucket(entry_time: str) -> str:
    dt = _parse_ts(entry_time)
    if not dt:
        return "unknown"
    dt = dt.astimezone(JST)
    hm = dt.hour * 60 + dt.minute
    if hm < 9 * 60 + 35:
        return "post_open_30m"
    if hm < 11 * 60 + 30:
        return "late_morning"
    if hm < 13 * 60 + 30:
        return "afternoon_open"
    if hm < 15 * 60 + 20:
        return "mid_afternoon"
    return "pre_close"


def _price_band(trade: Mapping[str, Any]) -> str:
    px = _num(trade.get("entry_price") or trade.get("price") or 0)
    if px <= 0:
        return "unknown"
    if px < 1000:
        return "lt_1000"
    if px < 3000:
        return "1000_3000"
    if px < 5000:
        return "3000_5000"
    return "gte_5000"


def _mfe_bucket(trade: Mapping[str, Any]) -> str:
    mfe = _num(trade.get("mfe_pct"))
    if mfe <= 0:
        return "mfe0"
    if mfe < 0.5:
        return "mfe_lt_0p5"
    if mfe < 1.0:
        return "mfe_0p5_1"
    if mfe < 2.0:
        return "mfe_1_2"
    return "mfe_gte_2"


def _wait_reason(trade: Mapping[str, Any]) -> str:
    for key in ("wait_reason", "entry_wait_reason", "gate_wait_reason"):
        val = str(trade.get(key) or "").strip()
        if val:
            return val
    return "none"


def _enrich_trade(trade: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(trade)
    sess = str(row.get("session") or "")
    row["session_kind"] = _session_kind(sess)
    row["time_bucket"] = _time_bucket(str(row.get("entry_time") or ""))
    row["price_band"] = _price_band(row)
    row["mfe_bucket"] = _mfe_bucket(row)
    row["wait_reason"] = _wait_reason(row)
    row["is_or"] = _is_or_trade(row)
    row["is_stop_low_mfe"] = _is_stop_low_mfe(row)
    row["is_mfe0"] = _is_mfe0(row)
    row["is_no_progress"] = _is_no_progress(row)
    row["entry_type"] = str(row.get("entry_type") or "PBV2").upper()
    row["exit_reason"] = str(row.get("exit_reason") or "unknown")
    row["cap_pool"] = str(row.get("cap_pool") or row.get("universe_bucket") or "unknown")
    return row


def _load_day_trades(repo_root: str, day: str) -> list[dict[str, Any]]:
    trades = _load_canonical_trades_for_day(Path(repo_root), day, all_sessions=True)
    return [_enrich_trade(t) for t in trades]


def _aggregate(trades: Sequence[Mapping[str, Any]], key_fn) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(_num(t.get("pnl_yen_100")))
    total = sum(sum(v) for v in groups.values()) or 1.0
    rows: list[dict[str, Any]] = []
    for key, pnls in sorted(groups.items(), key=lambda kv: sum(kv[1]), reverse=True):
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "key": key,
                "trades": len(pnls),
                "pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": round(_pf(pnls) or 0.0, 4),
                "win_rate": round(100.0 * wins / max(len(pnls), 1), 2),
                "pnl_share_pct": round(100.0 * sum(pnls) / total, 2),
            }
        )
    return rows


def _guard_contribution(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    specs = [
        ("ClusterGuard_proxy", lambda t: not t.get("is_or") and str(t.get("entry_type") or "") == "PBV2"),
        ("SLMGuard_proxy", lambda t: bool(t.get("is_stop_low_mfe"))),
        ("OR_overlay", lambda t: bool(t.get("is_or"))),
        ("CAP_pool", lambda t: str(t.get("cap_pool") or "") != "unknown"),
        ("stop_low_mfe_losses", lambda t: bool(t.get("is_stop_low_mfe")) and _num(t.get("pnl_yen_100")) < 0),
    ]
    total = sum(_num(t.get("pnl_yen_100")) for t in trades) or 1.0
    for guard, fn in specs:
        cohort = [t for t in trades if fn(t)]
        pnls = [_num(t.get("pnl_yen_100")) for t in cohort]
        if not pnls:
            continue
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "guard": guard,
                "cohort": "accepted",
                "trades": len(pnls),
                "pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": round(_pf(pnls) or 0.0, 4),
                "win_rate": round(100.0 * wins / len(pnls), 2),
                "pnl_share_pct": round(100.0 * sum(pnls) / total, 2),
            }
        )
    return rows


def _day_attribution(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**r, "dimension": "day"}
        for r in _aggregate(trades, lambda t: str(t.get("day") or ""))
    ]


@dataclass
class Phase577Job:
    repo_root: Path
    workers: int = 4
    period_end: Optional[str] = None

    def run(self) -> dict[str, Any]:
        days = _discover_days(self.repo_root)
        end = self.period_end or _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]

        all_trades: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(_load_day_trades, str(self.repo_root), d): d for d in days}
            for fut in as_completed(futs):
                all_trades.extend(fut.result())
        all_trades.sort(
            key=lambda t: _parse_ts(str(t.get("entry_time") or ""))
            or datetime.min.replace(tzinfo=JST)
        )

        pnls = [_num(t.get("pnl_yen_100")) for t in all_trades]
        total_pnl = round(sum(pnls), 2)
        pf = round(_pf(pnls) or 0.0, 4)

        symbol_rows = _aggregate(all_trades, lambda t: str(t.get("symbol") or ""))
        for i, row in enumerate(symbol_rows, 1):
            row["rank"] = i
            row["symbol"] = row.pop("key")

        time_session_rows = []
        for dim, fn in (
            ("session_kind", lambda t: t.get("session_kind")),
            ("time_bucket", lambda t: t.get("time_bucket")),
        ):
            for r in _aggregate(all_trades, fn):
                time_session_rows.append({"bucket": f"{dim}:{r['key']}", **{k: r[k] for k in r if k != "key"}})

        exit_entry_rows: list[dict[str, Any]] = []
        for dim, fn in (
            ("entry_type", lambda t: t.get("entry_type")),
            ("exit_reason", lambda t: t.get("exit_reason")),
            ("mfe_bucket", lambda t: t.get("mfe_bucket")),
            ("wait_reason", lambda t: t.get("wait_reason")),
            ("price_band", lambda t: t.get("price_band")),
        ):
            for r in _aggregate(all_trades, fn):
                exit_entry_rows.append({"dimension": dim, **r})

        guard_rows = _guard_contribution(all_trades)
        day_rows = _day_attribution(all_trades)

        summary_rows: list[dict[str, Any]] = []
        for dim, rows in (
            ("symbol_top", symbol_rows[:10]),
            ("symbol_bottom", list(reversed(symbol_rows[-10:])) if symbol_rows else []),
            ("session_kind", [r for r in time_session_rows if r["bucket"].startswith("session_kind:")]),
            ("time_bucket", [r for r in time_session_rows if r["bucket"].startswith("time_bucket:")]),
            ("exit_reason", [r for r in exit_entry_rows if r["dimension"] == "exit_reason"][:10]),
            ("entry_type", [r for r in exit_entry_rows if r["dimension"] == "entry_type"]),
            ("day", day_rows),
        ):
            for r in rows:
                summary_rows.append(
                    {
                        "dimension": dim,
                        "key": r.get("symbol") or r.get("key") or r.get("bucket", ""),
                        **{k: v for k, v in r.items() if k not in ("symbol", "key", "bucket", "dimension")},
                    }
                )

        top_sym = symbol_rows[0] if symbol_rows else {}
        bottom_sym = symbol_rows[-1] if symbol_rows else {}
        am_rows = [r for r in time_session_rows if r["bucket"] == "session_kind:am"]
        pm_rows = [r for r in time_session_rows if r["bucket"] == "session_kind:pm"]
        am_pnl = am_rows[0]["pnl_yen_100"] if am_rows else 0.0
        pm_pnl = pm_rows[0]["pnl_yen_100"] if pm_rows else 0.0

        stop_low = [t for t in all_trades if t.get("is_stop_low_mfe")]
        stop_low_pnl = sum(_num(t.get("pnl_yen_100")) for t in stop_low)
        stop_low_share = 100.0 * stop_low_pnl / (total_pnl or 1.0)

        sym_counts = Counter(str(t.get("symbol") or "") for t in all_trades)
        top3_share = 0.0
        if symbol_rows and total_pnl:
            top3_share = 100.0 * sum(r["pnl_yen_100"] for r in symbol_rows[:3]) / total_pnl

        exit_losses = [r for r in exit_entry_rows if r["dimension"] == "exit_reason" and r["pnl_yen_100"] < 0]
        exit_losses.sort(key=lambda r: r["pnl_yen_100"])

        mandatory = {
            "1_total_pnl_yen_100": total_pnl,
            "2_profit_factor": pf,
            "3_top_profit_source": f"{top_sym.get('symbol', 'n/a')} ({top_sym.get('pnl_yen_100', 0)})",
            "4_top_loss_source": f"{bottom_sym.get('symbol', 'n/a')} ({bottom_sym.get('pnl_yen_100', 0)})",
            "5_am_vs_pm": "am" if am_pnl >= pm_pnl else "pm",
            "6_price_band_dependency": max(
                [r for r in exit_entry_rows if r["dimension"] == "price_band"],
                key=lambda r: abs(r["pnl_yen_100"]),
                default={"key": "unknown"},
            )["key"],
            "7_symbol_concentration_strong": top3_share > 40.0,
            "8_stop_low_mfe_still_major": abs(stop_low_share) > 15.0,
            "9_exit_improvement_room": len(exit_losses) > 0 and exit_losses[0]["pnl_yen_100"] < -500,
            "10_entry_improvement_room": any(
                r["dimension"] == "entry_type" and r["pnl_yen_100"] < 0 for r in exit_entry_rows
            ),
            "11_universe_improvement_room": top3_share > 50.0,
            "12_next_improvement_theme": (
                "stop_low_mfe_exit_tuning"
                if abs(stop_low_share) > 15.0
                else "exit_reason_tail_losses"
            ),
            "trade_count": len(all_trades),
            "period_start": PERIOD_START,
            "period_end": end,
            "stop_low_mfe_pnl_share_pct": round(stop_low_share, 2),
            "top3_symbol_pnl_share_pct": round(top3_share, 2),
        }

        return {
            "verdict": PHASE577_VERDICT,
            "all_pass": len(all_trades) > 0,
            "summary_rows": summary_rows,
            "symbol_rows": symbol_rows,
            "time_session_rows": time_session_rows,
            "exit_entry_rows": exit_entry_rows,
            "guard_rows": guard_rows,
            "day_rows": day_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        paths = {
            "summary": reports / "phase577_profit_source_summary.csv",
            "symbol": reports / "phase577_symbol_attribution.csv",
            "time_session": reports / "phase577_time_session_attribution.csv",
            "exit_entry": reports / "phase577_exit_entry_attribution.csv",
            "guard": reports / "phase577_guard_contribution.csv",
            "report": reports / "phase577_report.json",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["symbol"], SYMBOL_FIELDS, list(result.get("symbol_rows") or []))
        _write_csv(paths["time_session"], TIME_SESSION_FIELDS, list(result.get("time_session_rows") or []))
        _write_csv(paths["exit_entry"], EXIT_ENTRY_FIELDS, list(result.get("exit_entry_rows") or []))
        _write_csv(paths["guard"], GUARD_FIELDS, list(result.get("guard_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = (
            resolve_kabu_root(self.repo_root)
            / "docs"
            / "operations"
            / "phase577_latest_runtime_profit_source_reanalysis.md"
        )
        doc.write_text(
            "\n".join(
                [
                    "# Phase577 — Latest Runtime Profit Source Re-Analysis",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    f"**Trades:** {m.get('trade_count')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Total PnL (yen/100): {m.get('1_total_pnl_yen_100')}",
                    f"2. Profit factor: {m.get('2_profit_factor')}",
                    f"3. Top profit source: {m.get('3_top_profit_source')}",
                    f"4. Top loss source: {m.get('4_top_loss_source')}",
                    f"5. AM vs PM stronger: {m.get('5_am_vs_pm')}",
                    f"6. Price band dependency: {m.get('6_price_band_dependency')}",
                    f"7. Symbol concentration strong: {m.get('7_symbol_concentration_strong')}",
                    f"8. stop_low_mfe still major: {m.get('8_stop_low_mfe_still_major')}",
                    f"9. EXIT improvement room: {m.get('9_exit_improvement_room')}",
                    f"10. ENTRY improvement room: {m.get('10_entry_improvement_room')}",
                    f"11. Universe improvement room: {m.get('11_universe_improvement_room')}",
                    f"12. Next improvement theme: {m.get('12_next_improvement_theme')}",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
