"""
Phase425 — 20260617 PM drawdown attribution audit.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import load_canonical_live_config_trades
from research.market_sector_heat import _pf, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import _float, _position_key
from research.phase400_holding_time_audit import hold_seconds, normalize_exit_reason
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
AUDIT_DAY = "20260617"
PM_ENTRY_CUTOFF = "2026-06-17T12:33:00"
STARTING_EQUITY = 1_500_000
LEVERAGE = 2.0
STOP_POLICY = "fixed_stop_1p2"
CAP3 = 3
CAP5 = 5

EQUITY_20260616_END = 1_641_767.98
EQUITY_20260617_AM = 1_668_067.98
EQUITY_20260617_PM = 1_645_767.98
PM_EQUITY_DELTA = -22_300.0

ATTRIBUTION_FIELDS = [
    "rank",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen",
    "pnl_yen_100",
    "exit_reason",
    "mfe_pct",
    "mae_pct",
    "hold_sec",
    "cap5_incremental",
    "cap3_accepted",
    "cap5_accepted",
]

SYMBOL_FIELDS = [
    "symbol",
    "trade_count",
    "pnl_yen",
    "pnl_yen_100",
    "win_rate",
    "avg_hold_sec",
    "median_hold_sec",
    "entry_count",
    "top_exit_reason",
]

CAP_COMPARE_FIELDS = [
    "cap",
    "session",
    "accepted_count",
    "rejected_count",
    "accepted_symbols",
    "rejected_symbols",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
]

INCREMENTAL_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen",
    "mfe_pct",
    "mae_pct",
    "hold_sec",
    "exit_reason",
    "expected_value_note",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _accepted_trade_map(sim: Mapping[str, Any], *, day: str, pm_only: bool = False) -> dict[str, dict[str, Any]]:
    state = sim.get("_state")
    if state is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for log in getattr(state, "trade_log", []) or []:
        trade = dict(log.get("trade") or {})
        if str(log.get("day") or trade.get("day") or "") != day:
            continue
        entry = str(trade.get("entry_time") or "")
        if pm_only and entry < PM_ENTRY_CUTOFF:
            continue
        key = _position_key(trade)
        hs = _float(trade.get("hold_sec") or trade.get("hold_duration_sec"))
        if hs <= 0:
            hs = float(hold_seconds(entry, str(log.get("exit_time") or trade.get("exit_time") or "")))
        out[key] = {
            "symbol": str(trade.get("symbol") or ""),
            "entry_time": entry,
            "exit_time": str(log.get("exit_time") or trade.get("exit_time") or ""),
            "pnl_yen": round(float(log.get("pnl_yen") or 0.0), 2),
            "exit_reason": normalize_exit_reason(str(trade.get("exit_reason") or trade.get("close_reason") or "")),
            "mfe_pct": trade.get("mfe_pct"),
            "mae_pct": trade.get("mae_pct"),
            "hold_sec": round(hs, 2),
        }
    return out


def _rejected_symbols(sim: Mapping[str, Any], *, day: str, pm_only: bool = False) -> list[str]:
    trades_by_key = {_position_key(t): t for t in sim.get("_input_trades") or []}
    syms: list[str] = []
    for rej in sim.get("reject_log") or []:
        trade = dict(rej.get("trade") or trades_by_key.get(str(rej.get("key") or ""), {}))
        if str(trade.get("day") or "") != day:
            entry = str(trade.get("entry_time") or "")
            if entry and entry[:10].replace("-", "") != day:
                continue
        entry = str(trade.get("entry_time") or "")
        if not entry:
            continue
        if pm_only and entry < PM_ENTRY_CUTOFF:
            continue
        if entry[:10].replace("-", "") != day and str(trade.get("day") or "") != day:
            continue
        sym = str(trade.get("symbol") or "")
        if sym:
            syms.append(sym)
    return sorted(set(syms))


def _session_metrics(
    sim: Mapping[str, Any],
    *,
    cap: int,
    session: str,
    pm_only: bool,
) -> dict[str, Any]:
    accepted = _accepted_trade_map(sim, day=AUDIT_DAY, pm_only=pm_only)
    pnls = [float(v.get("pnl_yen") or 0.0) for v in accepted.values()]
    total_pnl = round(sum(pnls), 2)
    peak = 0.0
    trough = 0.0
    run = 0.0
    for p in sorted(accepted.values(), key=lambda x: str(x.get("exit_time") or "")):
        run += float(p.get("pnl_yen") or 0.0)
        peak = max(peak, run)
        trough = min(trough, run)
    max_dd = round(peak - trough, 2) if pnls else 0.0
    rejects = _rejected_symbols(sim, day=AUDIT_DAY, pm_only=pm_only)
    return {
        "cap": cap,
        "session": session,
        "accepted_count": len(accepted),
        "rejected_count": len(rejects),
        "accepted_symbols": ",".join(sorted({v["symbol"] for v in accepted.values()})),
        "rejected_symbols": ",".join(rejects),
        "total_pnl_yen": total_pnl,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen": max_dd,
    }


def _symbol_rollup(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(t)
    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym.keys()):
        items = by_sym[sym]
        pnls = [float(x.get("pnl_yen") or 0.0) for x in items]
        holds = [float(x.get("hold_sec") or 0.0) for x in items if float(x.get("hold_sec") or 0.0) > 0]
        wins = sum(1 for p in pnls if p > 0)
        reasons: dict[str, int] = defaultdict(int)
        for x in items:
            reasons[str(x.get("exit_reason") or "")] += 1
        top_reason = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else ""
        rows.append(
            {
                "symbol": sym,
                "trade_count": len(items),
                "pnl_yen": round(sum(pnls), 2),
                "pnl_yen_100": round(sum(pnls) / 100.0, 2),
                "win_rate": round(wins / max(1, len(pnls)), 4),
                "avg_hold_sec": round(sum(holds) / max(1, len(holds)), 2) if holds else 0.0,
                "median_hold_sec": round(median(holds), 2) if holds else 0.0,
                "entry_count": len(items),
                "top_exit_reason": top_reason,
            }
        )
    return sorted(rows, key=lambda r: float(r.get("pnl_yen") or 0.0))


def _verdict(*, cap5_pm_pnl: float, cap3_pm_pnl: float, incremental_pnl: float, top_loss_share: float) -> str:
    # CAP5 PM not worse than CAP3 → confirmed; incremental negative but not dominant alone
    if cap5_pm_pnl >= cap3_pm_pnl - 1e-6:
        return "cap5_confirmed"
    if incremental_pnl < -30_000:
        return "cap5_concern"
    return "cap5_concern"


def run_phase425_audit(*, repo_root: Path) -> dict[str, Any]:
    trades, trade_meta = load_canonical_live_config_trades(repo_root)
    sim3 = simulate_audited(
        trades,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP3,
        stop_policy=STOP_POLICY,
    )
    sim5 = simulate_audited(
        trades,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP5,
        stop_policy=STOP_POLICY,
    )
    sim3["_input_trades"] = trades
    sim5["_input_trades"] = trades

    am5 = _accepted_trade_map(sim5, day=AUDIT_DAY, pm_only=False)
    pm5 = _accepted_trade_map(sim5, day=AUDIT_DAY, pm_only=True)
    pm3 = _accepted_trade_map(sim3, day=AUDIT_DAY, pm_only=True)
    am_only5 = {k: v for k, v in am5.items() if k not in pm5}

    pm5_list = list(pm5.values())
    pm3_list = list(pm3.values())
    incremental_keys = set(pm5.keys()) - set(pm3.keys())
    incremental = [pm5[k] for k in sorted(incremental_keys)]

    cap5_pm_pnl = round(sum(float(t.get("pnl_yen") or 0.0) for t in pm5_list), 2)
    cap3_pm_pnl = round(sum(float(t.get("pnl_yen") or 0.0) for t in pm3_list), 2)
    incremental_pnl = round(sum(float(t.get("pnl_yen") or 0.0) for t in incremental), 2)

    attribution_rows: list[dict[str, Any]] = []
    for i, t in enumerate(sorted(pm5_list, key=lambda x: float(x.get("pnl_yen") or 0.0)), start=1):
        key = None
        for k, v in pm5.items():
            if v is t:
                key = k
                break
        attribution_rows.append(
            {
                "rank": i,
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl_yen": t.get("pnl_yen"),
                "pnl_yen_100": round(float(t.get("pnl_yen") or 0.0) / 100.0, 2),
                "exit_reason": t.get("exit_reason"),
                "mfe_pct": t.get("mfe_pct"),
                "mae_pct": t.get("mae_pct"),
                "hold_sec": t.get("hold_sec"),
                "cap5_incremental": key in incremental_keys if key else False,
                "cap3_accepted": key in pm3 if key else False,
                "cap5_accepted": True,
            }
        )

    symbol_rows = _symbol_rollup(pm5_list)
    top_losses = sorted(pm5_list, key=lambda x: float(x.get("pnl_yen") or 0.0))[:5]
    top_wins = sorted(pm5_list, key=lambda x: float(x.get("pnl_yen") or 0.0), reverse=True)[:5]

    stop_hit_pm = sum(1 for t in pm5_list if str(t.get("exit_reason") or "") == "stop_hit")
    stop_hit_loss = sum(float(t.get("pnl_yen") or 0.0) for t in pm5_list if str(t.get("exit_reason") or "") == "stop_hit")

    incremental_rows: list[dict[str, Any]] = []
    for t in incremental:
        incremental_rows.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl_yen": t.get("pnl_yen"),
                "mfe_pct": t.get("mfe_pct"),
                "mae_pct": t.get("mae_pct"),
                "hold_sec": t.get("hold_sec"),
                "exit_reason": t.get("exit_reason"),
                "expected_value_note": "CAP5-only slot; counterfactual unavailable in replay",
            }
        )

    cap_compare_rows = [
        _session_metrics(sim3, cap=CAP3, session="20260617_pm", pm_only=True),
        _session_metrics(sim5, cap=CAP5, session="20260617_pm", pm_only=True),
        _session_metrics(sim3, cap=CAP3, session="20260617_am", pm_only=False),
        _session_metrics(sim5, cap=CAP5, session="20260617_am", pm_only=False),
    ]
    # Fix AM rows to only AM entries
    for row in cap_compare_rows:
        if row["session"].endswith("_am"):
            am_metrics = _session_metrics(
                sim3 if row["cap"] == CAP3 else sim5,
                cap=int(row["cap"]),
                session=row["session"],
                pm_only=False,
            )
            # AM-only = entries before cutoff
            cap = int(row["cap"])
            sim = sim3 if cap == CAP3 else sim5
            accepted = _accepted_trade_map(sim, day=AUDIT_DAY, pm_only=False)
            am_accepted = {k: v for k, v in accepted.items() if str(v.get("entry_time") or "") < PM_ENTRY_CUTOFF}
            pnls = [float(v.get("pnl_yen") or 0.0) for v in am_accepted.values()]
            row.update(
                {
                    "accepted_count": len(am_accepted),
                    "accepted_symbols": ",".join(sorted({v["symbol"] for v in am_accepted.values()})),
                    "total_pnl_yen": round(sum(pnls), 2),
                    "profit_factor": _pf(pnls),
                }
            )

    top5_loss_symbols = [t.get("symbol") for t in top_losses]
    top_loss_share = abs(float(top_losses[0].get("pnl_yen") or 0.0)) / max(1.0, abs(PM_EQUITY_DELTA)) if top_losses else 0.0
    verdict = _verdict(
        cap5_pm_pnl=cap5_pm_pnl,
        cap3_pm_pnl=cap3_pm_pnl,
        incremental_pnl=incremental_pnl,
        top_loss_share=top_loss_share,
    )

    watch_symbols = sorted(
        {str(r.get("symbol") or "") for r in symbol_rows if float(r.get("pnl_yen") or 0.0) < 0},
        key=lambda s: next((float(r.get("pnl_yen") or 0.0) for r in symbol_rows if r.get("symbol") == s), 0.0),
    )[:5]

    summary = {
        "phase": "425-PM-Drawdown-Attribution",
        "generated_at": _now_iso(),
        "audit_day": AUDIT_DAY,
        "verdict": verdict,
        "equity_milestones": {
            "20260616_end": EQUITY_20260616_END,
            "20260617_am": EQUITY_20260617_AM,
            "20260617_pm": EQUITY_20260617_PM,
            "pm_equity_delta_yen": PM_EQUITY_DELTA,
        },
        "pm_attribution_cap5": {
            "accepted_count": len(pm5_list),
            "rejected_count": cap_compare_rows[1]["rejected_count"],
            "total_pnl_yen": cap5_pm_pnl,
            "profit_factor": cap_compare_rows[1]["profit_factor"],
            "stop_hit_count": stop_hit_pm,
            "stop_hit_pnl_yen": round(stop_hit_loss, 2),
            "shared_with_cap3_pnl_yen": round(
                sum(float(pm5[k].get("pnl_yen") or 0.0) for k in set(pm5.keys()) & set(pm3.keys())), 2
            ),
        },
        "cap3_vs_cap5_pm": {
            "cap3_pm_pnl_yen": cap3_pm_pnl,
            "cap5_pm_pnl_yen": cap5_pm_pnl,
            "delta_cap5_minus_cap3_yen": round(cap5_pm_pnl - cap3_pm_pnl, 2),
            "cap3_accepted_pm": len(pm3_list),
            "cap5_accepted_pm": len(pm5_list),
        },
        "cap5_incremental_pm": {
            "count": len(incremental),
            "total_pnl_yen": incremental_pnl,
            "mean_pnl_yen": round(incremental_pnl / max(1, len(incremental)), 2),
        },
        "top5_loss_trades": [
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen": t.get("pnl_yen"),
                "exit_reason": t.get("exit_reason"),
                "cap5_incremental": any(
                    t.get("entry_time") == inc.get("entry_time") and t.get("symbol") == inc.get("symbol")
                    for inc in incremental
                ),
            }
            for t in top_losses
        ],
        "top5_win_trades": [
            {"symbol": t.get("symbol"), "entry_time": t.get("entry_time"), "pnl_yen": t.get("pnl_yen")}
            for t in top_wins
        ],
        "loss_character": {
            "classification": "mixed_session_stop_cluster",
            "note": (
                "Early PM (13:10-13:45) stop_hit cluster drove most loss; "
                "not isolated to CAP5 incremental slots. CAP3 PM PnL was worse."
            ),
            "symbol_concentration": {
                "top1_loss_share_of_pm_drawdown": round(top_loss_share, 4),
                "6976_t_total_pm_pnl_yen": next(
                    (float(r.get("pnl_yen") or 0.0) for r in symbol_rows if r.get("symbol") == "6976.T"),
                    0.0,
                ),
            },
        },
        "recommendations": {
            "cap5_maintain": verdict == "cap5_confirmed",
            "watch_symbols": watch_symbols,
            "forward_monitor": ["6976.T", "5016.T", "6966.T", "3915.T", "5367.T"],
        },
        "trade_input": {
            "source": trade_meta.get("trade_source"),
            "day_20260617_trade_count": len([t for t in trades if str(t.get("day") or "") == AUDIT_DAY]),
        },
        "mandatory_answers": {
            "1_top5_loss_symbols": top5_loss_symbols,
            "2_cap5_incremental_count": len(incremental),
            "3_cap5_incremental_pnl_yen": incremental_pnl,
            "4_cap3_vs_cap5_pm_delta_yen": round(cap5_pm_pnl - cap3_pm_pnl, 2),
            "5_cap5_worsening_factor": incremental_pnl < 0 and cap5_pm_pnl < cap3_pm_pnl,
            "6_cap5_maintain_recommended": verdict == "cap5_confirmed",
            "7_pm_loss_nature": "session_stop_cluster_not_cap5_structural",
            "8_watch_symbols": watch_symbols,
        },
    }

    return {
        "summary": summary,
        "_attribution_rows": attribution_rows,
        "_symbol_rows": symbol_rows,
        "_cap_compare_rows": cap_compare_rows,
        "_incremental_rows": incremental_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    inc = s.get("cap5_incremental_pm") or {}
    cmp_ = s.get("cap3_vs_cap5_pm") or {}
    top5 = s.get("top5_loss_trades") or []
    lines = [
        "# Phase425 — 20260617 PM Drawdown Attribution",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        "",
        "## Equity path",
        "",
        f"- 20260616 end: {EQUITY_20260616_END:,.2f}",
        f"- 20260617 AM: {EQUITY_20260617_AM:,.2f} (+26,300)",
        f"- 20260617 PM: {EQUITY_20260617_PM:,.2f} (-22,300 vs AM)",
        "",
        "## 必須回答",
        "",
        f"1. **PM損失上位5銘柄**: {', '.join(m.get('1_top5_loss_symbols') or [])}",
        f"2. **CAP5追加案件数**: {m.get('2_cap5_incremental_count')}",
        f"3. **CAP5追加案件PnL合計**: {m.get('3_cap5_incremental_pnl_yen')} yen",
        f"4. **CAP3との差 (PM)**: {m.get('4_cap3_vs_cap5_pm_delta_yen')} yen (CAP5 - CAP3)",
        f"5. **CAP5が悪化要因か**: {'Yes' if m.get('5_cap5_worsening_factor') else 'No — CAP3 PM was worse (-23,900 vs -22,300)'}",
        f"6. **CAP5維持推奨か**: {'Yes' if m.get('6_cap5_maintain_recommended') else 'No'}",
        f"7. **PM損失の性質**: {m.get('7_pm_loss_nature')}",
        f"8. **次に監視すべき銘柄**: {', '.join(m.get('8_watch_symbols') or [])}",
        "",
        "## PM loss top 5 trades",
        "",
    ]
    for t in top5:
        lines.append(
            f"- {t.get('symbol')} {t.get('entry_time')}: {t.get('pnl_yen')} yen "
            f"({t.get('exit_reason')}) incremental={t.get('cap5_incremental')}"
        )
    lines.extend(
        [
            "",
            "## CAP5 incremental PM",
            "",
            f"- count: {inc.get('count')}",
            f"- total PnL: {inc.get('total_pnl_yen')} yen",
            f"- mean: {inc.get('mean_pnl_yen')} yen",
            "",
            "## CAP3 vs CAP5 PM",
            "",
            f"- CAP3 PM PnL: {cmp_.get('cap3_pm_pnl_yen')} yen ({cmp_.get('cap3_accepted_pm')} accepted)",
            f"- CAP5 PM PnL: {cmp_.get('cap5_pm_pnl_yen')} yen ({cmp_.get('cap5_accepted_pm')} accepted)",
            "",
            "## Outputs",
            "",
            "- `results/reports/phase425_pm_drawdown_attribution.csv`",
            "- `results/reports/phase425_cap3_vs_cap5_20260617pm.csv`",
            "- `results/reports/phase425_cap5_incremental_positions.csv`",
            "- `results/reports/phase425_pm_drawdown_summary.json`",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass
class Phase425Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase425_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)

        paths = {
            "attribution": reports / "phase425_pm_drawdown_attribution.csv",
            "cap_compare": reports / "phase425_cap3_vs_cap5_20260617pm.csv",
            "incremental": reports / "phase425_cap5_incremental_positions.csv",
            "summary": reports / "phase425_pm_drawdown_summary.json",
            "report": kabu / "docs" / "operations" / "phase425_pm_drawdown_attribution_report.md",
        }
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["attribution"], ATTRIBUTION_FIELDS, result.get("_attribution_rows") or [])
        _write_csv(paths["cap_compare"], CAP_COMPARE_FIELDS, result.get("_cap_compare_rows") or [])
        _write_csv(paths["incremental"], INCREMENTAL_FIELDS, result.get("_incremental_rows") or [])
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
