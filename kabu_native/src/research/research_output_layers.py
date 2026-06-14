"""
Shared dual-layer research output standard.

Every research phase must emit both:
  - Research Layer: PF, PnL, WinRate (trade-level / static analysis)
  - Live Simulation Layer: 1.5M / leverage 2.0 / 100 shares / CAP=N capital path

Adoption decisions use final_equity from Live Simulation Layer, not Research Layer PF.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from research.phase382_capital_constrained_backtest import _pf

COMMON_RESEARCH_CONSTRAINTS: dict[str, bool] = {
    "review_only": True,
    "runtime_reflected": False,
    "runtime_change_forbidden": True,
    "universe_change_forbidden": True,
    "entry_change_forbidden": True,
    "exit_change_forbidden": True,
    "yaml_changes_forbidden": True,
}

LIVE_SIM_DEFAULT_STARTING_EQUITY = 1_500_000.0
LIVE_SIM_DEFAULT_LEVERAGE = 2.0
LIVE_SIM_DEFAULT_SHARES = 100

RESEARCH_LAYER_FIELDS = ("profit_factor", "total_pnl_yen", "win_rate")
LIVE_SIM_REQUIRED_FIELDS = (
    "final_equity",
    "total_return_pct",
    "max_drawdown_pct",
    "days_below_50pct",
    "accepted_count",
    "rejected_count",
)


def compute_days_below_50pct(
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    starting_equity: float,
) -> int:
    floor = starting_equity * 0.5
    count = 0
    for row in daily_rows:
        end_eq = float(row.get("end_equity") or 0.0)
        if end_eq < floor:
            count += 1
    return count


def build_research_layer(
    pnls: Sequence[float],
    *,
    trade_count: int | None = None,
    label: str = "",
) -> dict[str, Any]:
    total = round(sum(pnls), 2)
    count = trade_count if trade_count is not None else len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "label": label,
        "trade_count": count,
        "total_pnl_yen": total,
        "profit_factor": _pf(list(pnls)),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
    }


def build_live_simulation_layer(
    *,
    cap: int,
    final_equity: float,
    total_return_pct: float,
    max_drawdown_pct: float,
    days_below_50pct: int,
    accepted_count: int,
    rejected_count: int,
    starting_equity: float = LIVE_SIM_DEFAULT_STARTING_EQUITY,
    leverage: float = LIVE_SIM_DEFAULT_LEVERAGE,
    shares: int = LIVE_SIM_DEFAULT_SHARES,
    profit_factor: float | None = None,
    win_rate: float | None = None,
    total_pnl_yen: float | None = None,
) -> dict[str, Any]:
    layer: dict[str, Any] = {
        "starting_equity": starting_equity,
        "leverage": leverage,
        "shares": shares,
        "cap": cap,
        "final_equity": round(float(final_equity), 2),
        "total_return_pct": round(float(total_return_pct), 4),
        "max_drawdown_pct": round(float(max_drawdown_pct), 4),
        "days_below_50pct": int(days_below_50pct),
        "accepted_count": int(accepted_count),
        "rejected_count": int(rejected_count),
    }
    if total_pnl_yen is not None:
        layer["total_pnl_yen"] = round(float(total_pnl_yen), 2)
    if profit_factor is not None:
        layer["profit_factor"] = profit_factor
    if win_rate is not None:
        layer["win_rate"] = win_rate
    return layer


def build_live_simulation_layer_from_cap_result(
    result: Mapping[str, Any],
    *,
    cap: int,
    daily_rows: Sequence[Mapping[str, Any]] | None = None,
    starting_equity: float = LIVE_SIM_DEFAULT_STARTING_EQUITY,
    leverage: float = LIVE_SIM_DEFAULT_LEVERAGE,
    shares: int = LIVE_SIM_DEFAULT_SHARES,
) -> dict[str, Any]:
    rows = daily_rows if daily_rows is not None else list(result.get("_daily_rows") or [])
    if not rows and result.get("_daily_accepted"):
        rows = [{"end_equity": result.get("final_equity")}]
    days_below = compute_days_below_50pct(rows, starting_equity=starting_equity) if rows else 0
    initial = float(result.get("initial_equity") or starting_equity)
    final_eq = float(result.get("final_equity") or 0.0)
    total_pnl = float(result.get("total_pnl_yen") or round(final_eq - initial, 2))
    return build_live_simulation_layer(
        cap=cap,
        starting_equity=initial,
        leverage=leverage,
        shares=shares,
        final_equity=final_eq,
        total_return_pct=float(result.get("return_pct") or 0.0),
        max_drawdown_pct=float(result.get("max_drawdown_pct") or 0.0),
        days_below_50pct=days_below,
        accepted_count=int(result.get("accepted_trade_count") or 0),
        rejected_count=int(result.get("rejected_trade_count") or 0),
        total_pnl_yen=total_pnl,
        profit_factor=result.get("profit_factor"),
        win_rate=result.get("win_rate"),
    )


def build_live_simulation_layer_from_equity_metrics(
    metrics: Mapping[str, Any],
    *,
    cap: int,
    daily_rows: Sequence[Mapping[str, Any]] | None = None,
    starting_equity: float = LIVE_SIM_DEFAULT_STARTING_EQUITY,
    leverage: float = LIVE_SIM_DEFAULT_LEVERAGE,
    shares: int = LIVE_SIM_DEFAULT_SHARES,
) -> dict[str, Any]:
    initial = float(metrics.get("initial_equity") or starting_equity)
    rows = daily_rows if daily_rows is not None else []
    days_below = int(metrics.get("days_below_50pct") or 0)
    if rows and metrics.get("days_below_50pct") is None:
        days_below = compute_days_below_50pct(rows, starting_equity=initial)
    return build_live_simulation_layer(
        cap=cap,
        starting_equity=initial,
        leverage=leverage,
        shares=shares,
        final_equity=float(metrics.get("final_equity") or 0.0),
        total_return_pct=float(metrics.get("total_return_pct") or 0.0),
        max_drawdown_pct=float(metrics.get("max_drawdown_pct") or 0.0),
        days_below_50pct=days_below,
        accepted_count=int(metrics.get("accepted_trade_count") or 0),
        rejected_count=int(metrics.get("rejected_trade_count") or 0),
        total_pnl_yen=round(float(metrics.get("final_equity") or 0.0) - initial, 2),
        profit_factor=metrics.get("profit_factor"),
        win_rate=metrics.get("win_rate"),
    )


def build_adoption_verdict(
    *,
    live_simulation_layer: Mapping[str, Any],
    research_layer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    starting = float(live_simulation_layer.get("starting_equity") or LIVE_SIM_DEFAULT_STARTING_EQUITY)
    final_eq = float(live_simulation_layer.get("final_equity") or 0.0)
    delta = round(final_eq - starting, 2)
    research_pf = (research_layer or {}).get("profit_factor")
    return {
        "primary_metric": "final_equity",
        "adoptable": final_eq > starting,
        "final_equity_yen": round(final_eq, 2),
        "starting_equity_yen": starting,
        "delta_yen": delta,
        "research_profit_factor": research_pf,
        "research_pf_not_adoption_basis": True,
        "note": (
            "Adoption requires Live Simulation Layer final_equity > starting_equity. "
            "Research Layer PF/PnL/WinRate alone must not drive adoption."
        ),
    }


def build_dual_layer_bundle(
    *,
    research_layer: Mapping[str, Any],
    live_simulation_layer: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "research_layer": dict(research_layer),
        "live_simulation_layer": dict(live_simulation_layer),
        "adoption_verdict": build_adoption_verdict(
            live_simulation_layer=live_simulation_layer,
            research_layer=research_layer,
        ),
    }


def format_dual_layer_markdown(
    bundle: Mapping[str, Any],
    *,
    title: str = "",
) -> list[str]:
    research = bundle.get("research_layer") or {}
    live = bundle.get("live_simulation_layer") or {}
    verdict = bundle.get("adoption_verdict") or {}
    lines: list[str] = []
    if title:
        lines.extend(["", f"### {title}", ""])
    lines.extend(
        [
            "#### Research Layer",
            "",
            f"- PF: {research.get('profit_factor')}",
            f"- PnL: {research.get('total_pnl_yen')}円",
            f"- WinRate: {research.get('win_rate')}",
            f"- trades: {research.get('trade_count')}",
            "",
            "#### Live Simulation Layer",
            "",
            f"- starting_equity: {live.get('starting_equity')} / leverage: {live.get('leverage')} / shares: {live.get('shares')} / CAP: {live.get('cap')}",
            f"- final_equity: {live.get('final_equity')}",
            f"- total_return_pct: {live.get('total_return_pct')}",
            f"- max_drawdown_pct: {live.get('max_drawdown_pct')}",
            f"- days_below_50pct: {live.get('days_below_50pct')}",
            f"- accepted_count: {live.get('accepted_count')}",
            f"- rejected_count: {live.get('rejected_count')}",
            "",
            "#### 採用判定（final_equity 主指標）",
            "",
            f"- adoptable: {'はい' if verdict.get('adoptable') else 'いいえ'} (final={verdict.get('final_equity_yen')} vs start={verdict.get('starting_equity_yen')})",
            f"- Research PF={verdict.get('research_profit_factor')} は採用根拠にしない",
            "",
        ]
    )
    return lines
