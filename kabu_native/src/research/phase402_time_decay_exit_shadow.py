"""
Phase402: Time-decayed MFE / stop shadow exit replay.

Counterfactual exit policies on Phase399 position_cap_accepted trades.
Research / shadow only — no Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key, _write_csv
from research.phase400_holding_time_audit import (
    enrich_trade,
    load_phase399_trades,
    normalize_exit_reason,
)
from research.phase401_long_hold_loser_forensic import (
    _accepted_lookup,
    _load_structural_lookup,
    _session_dir,
)
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import _build_price_index
from research.small_paper_performance_review import _load_events
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"
HARD_STOP_PCT = 1.2

TIME_THRESHOLDS_SEC = (900, 1200, 1800)
MFE_ACTIVATION_AFTER = (0.2, 0.3, 0.4, 0.5)
STOP_AFTER_PCT = (-0.4, -0.6, -0.8, -1.0)

GOOD_LONG_HOLD_SYMBOLS = frozenset({"4062.T", "3905.T", "4047.T", "9984.T"})
BAD_LONG_HOLD_SYMBOLS = frozenset({"7220.T", "4078.T", "3915.T", "6055.T"})

POLICY_COMBINED_20M = "combined_20m"
POLICY_COMBINED_30M = "combined_30m"
POLICY_MFE = "time_decay_mfe"
POLICY_STOP = "time_decay_stop"
POLICY_BASELINE = "baseline_phase399"

GRID_FIELDS = [
    "policy_id",
    "time_threshold_sec",
    "mfe_activation_after_time",
    "stop_after_time",
    "total_pnl_yen_100",
    "profit_factor",
    "trade_count",
    "win_rate",
    "max_drawdown_yen_100",
    "long_hold_loser_count",
    "stop_hit_count",
    "trailing_mfe_count",
    "session_close_count",
    "saved_loss_yen",
    "lost_upside_yen",
    "net_delta_yen",
    "affected_trade_count",
    "long_hold_loser_delta",
    "good_long_hold_damage_yen",
    "bad_long_hold_rescue_yen",
    "adopt_candidate",
]

TRADE_FIELDS = [
    "policy_id",
    "time_threshold_sec",
    "mfe_activation_after_time",
    "stop_after_time",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "baseline_exit_time",
    "hold_sec",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen",
    "baseline_exit_reason",
    "shadow_exit_reason",
    "is_long_hold_loser",
    "is_good_long_hold",
    "is_bad_long_hold",
    "focus_symbol",
]


@dataclass(frozen=True)
class PolicySpec:
    policy_id: str
    time_threshold_sec: Optional[float]
    mfe_activation_after: Optional[float]
    stop_after_pct: Optional[float]
    apply_mfe_decay: bool
    apply_stop_decay: bool

    @property
    def grid_key(self) -> str:
        return (
            f"{self.policy_id}|{self.time_threshold_sec}|"
            f"{self.mfe_activation_after}|{self.stop_after_pct}"
        )


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_pct(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return round((px - entry) / entry * 100.0, 4)


def iter_policy_grid() -> list[PolicySpec]:
    specs: list[PolicySpec] = [PolicySpec(POLICY_BASELINE, None, None, None, False, False)]
    for t in TIME_THRESHOLDS_SEC:
        for mfe in MFE_ACTIVATION_AFTER:
            specs.append(
                PolicySpec(POLICY_MFE, float(t), mfe, None, True, False),
            )
    for t in TIME_THRESHOLDS_SEC:
        for stop in STOP_AFTER_PCT:
            specs.append(
                PolicySpec(POLICY_STOP, float(t), None, stop, False, True),
            )
    for mfe in MFE_ACTIVATION_AFTER:
        for stop in STOP_AFTER_PCT:
            specs.append(
                PolicySpec(POLICY_COMBINED_20M, 1200.0, mfe, stop, True, True),
            )
            specs.append(
                PolicySpec(POLICY_COMBINED_30M, 1800.0, mfe, stop, True, True),
            )
    return specs


def _normalize_shadow_exit(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if "stop" in r:
        return "stop_hit"
    if "trailing" in r:
        return "trailing_mfe"
    if "session" in r:
        return "session_close"
    return "other"


def simulate_time_decay_exit(
    series: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_price: float,
    session_end_ts: float,
    imb_pct: Optional[float],
    policy: PolicySpec,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    activate_base, giveback_frac, _tier = trailing_params_for_board_tier(imb_pct)
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    last_ts = entry_ts
    last_px = entry_price

    usable = [(ts, px) for ts, px in series if ts >= entry_ts and px > 0]
    if not usable:
        return {
            "shadow_exit_reason": "no_ticks",
            "shadow_exit_ts": entry_ts,
            "shadow_pnl_pct": 0.0,
            "shadow_pnl_yen_100": 0.0,
            "shadow_exit_price": entry_price,
        }

    for ts, px in usable:
        if ts > session_end_ts:
            break
        elapsed = ts - entry_ts
        pnl = _pnl_pct(entry_price, px)
        peak_pnl = max(peak_pnl, pnl)
        last_ts = ts
        last_px = px

        after_threshold = (
            policy.time_threshold_sec is not None
            and elapsed >= float(policy.time_threshold_sec)
        )

        activate = activate_base
        if after_threshold and policy.apply_mfe_decay and policy.mfe_activation_after is not None:
            activate = float(policy.mfe_activation_after)

        if after_threshold and policy.apply_stop_decay and policy.stop_after_pct is not None:
            if pnl <= float(policy.stop_after_pct):
                return _exit_result(
                    entry_price,
                    px,
                    ts,
                    pnl,
                    "stop_hit",
                )
        elif px <= hard_stop_px:
            return _exit_result(
                entry_price,
                px,
                ts,
                pnl,
                "stop_hit",
            )

        if peak_pnl >= activate and pnl <= peak_pnl * giveback_frac:
            return _exit_result(
                entry_price,
                px,
                ts,
                pnl,
                "trailing_mfe_exit",
            )

    final_pnl = _pnl_pct(entry_price, last_px)
    return {
        "shadow_exit_reason": "session_close",
        "shadow_exit_ts": last_ts,
        "shadow_pnl_pct": final_pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, last_px), 2),
        "shadow_exit_price": round(last_px, 4),
    }


def _exit_result(
    entry_price: float,
    px: float,
    ts: float,
    pnl: float,
    reason: str,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    return {
        "shadow_exit_reason": reason,
        "shadow_exit_ts": ts,
        "shadow_pnl_pct": pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, px), 2),
        "shadow_exit_price": round(px, 4),
    }


def _max_drawdown_yen(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _saved_lost_yen(
    baseline_pnls: Sequence[float],
    shadow_pnls: Sequence[float],
) -> tuple[float, float]:
    saved = 0.0
    lost = 0.0
    for b, s in zip(baseline_pnls, shadow_pnls):
        if b < 0 and s > b:
            saved += s - b
        if b > 0 and s < b:
            lost += b - s
    return round(saved, 2), round(lost, 2)


def _session_end_ts(series: Sequence[tuple[float, float]], fallback_ts: float) -> float:
    if not series:
        return fallback_ts
    return max(ts for ts, _ in series)


def _prepare_trade_context(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, dict[str, Any]],
) -> Optional[dict[str, Any]]:
    day = str(trade.get("day") or "")
    session = str(trade.get("session") or "")
    sym = str(trade.get("symbol") or "")
    entry_time = str(trade.get("entry_time") or "")
    cache_key = f"{day}/{session}"

    if cache_key not in session_cache:
        sdir = _session_dir(repo_root, day, session)
        events = _load_events(sdir) if sdir.is_dir() else []
        session_cache[cache_key] = {
            "structural": _load_structural_lookup(sdir),
            "accepted": _accepted_lookup(sdir),
            "price_index": _build_price_index(events),
        }

    cache = session_cache[cache_key]
    pos_key = _position_key({"symbol": sym, "entry_time": entry_time})
    struct = cache["structural"].get(pos_key, {})
    acc = cache["accepted"].get((sym, entry_time), {})

    entry_px = _float(struct.get("entry_price")) or _float(acc.get("current_price")) or _float(acc.get("entry_price"))
    ent_dt = _parse_ts(entry_time)
    if entry_px is None or entry_px <= 0 or ent_dt is None:
        return None

    ent_ts = ent_dt.timestamp()
    series = cache["price_index"].get(sym, [])
    ex_dt = _parse_ts(str(trade.get("exit_time") or ""))
    fallback_end = ex_dt.timestamp() if ex_dt else ent_ts + float(trade.get("hold_sec") or 0)
    session_end = _session_end_ts(series, fallback_end)
    imb = _float(acc.get("entry_imbalance_percentile"))

    baseline_pnl = float(trade.get("pnl_yen_100_float") or 0.0)
    baseline_reason = normalize_exit_reason(str(trade.get("exit_reason") or ""))

    return {
        "day": day,
        "session": session,
        "symbol": sym,
        "entry_time": entry_time,
        "exit_time": trade.get("exit_time"),
        "hold_sec": float(trade.get("hold_sec") or 0.0),
        "baseline_pnl_yen_100": baseline_pnl,
        "baseline_exit_reason": baseline_reason,
        "entry_price": entry_px,
        "entry_ts": ent_ts,
        "session_end_ts": session_end,
        "price_series": series,
        "imb_pct": imb,
        "is_long_hold_loser": bool(
            trade.get("is_loser")
            and float(trade.get("hold_sec") or 0) >= float(trade.get("_p90_hold") or 0)
        ),
        "is_good_long_hold": bool(
            sym in GOOD_LONG_HOLD_SYMBOLS
            and trade.get("is_winner")
            and float(trade.get("hold_sec") or 0) >= float(trade.get("_p90_hold") or 0)
        ),
        "is_bad_long_hold": bool(
            sym in BAD_LONG_HOLD_SYMBOLS
            and trade.get("is_loser")
            and float(trade.get("hold_sec") or 0) >= float(trade.get("_p90_hold") or 0)
        ),
        "focus_symbol": sym.rstrip(".T") in {s.rstrip(".T") for s in GOOD_LONG_HOLD_SYMBOLS | BAD_LONG_HOLD_SYMBOLS},
    }


def aggregate_policy_results(
    trade_results: Sequence[Mapping[str, Any]],
    *,
    policy: PolicySpec,
    p90_hold: float,
    baseline_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trade_results]
    if policy.policy_id == POLICY_BASELINE:
        shadow_pnls = baseline_pnls
        shadow_reasons = [str(t["baseline_exit_reason"]) for t in trade_results]
    else:
        key = policy.grid_key
        shadow_pnls = [float(t["shadow_by_policy"][key]["shadow_pnl_yen_100"]) for t in trade_results]
        shadow_reasons = [
            _normalize_shadow_exit(t["shadow_by_policy"][key]["shadow_exit_reason"])
            for t in trade_results
        ]

    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    deltas = [s - b for b, s in zip(baseline_pnls, shadow_pnls)]
    affected = sum(1 for d in deltas if abs(d) > 0.01)

    long_hold_losers = sum(
        1
        for t, s in zip(trade_results, shadow_pnls)
        if float(t.get("hold_sec") or 0) >= p90_hold and s < 0
    )
    baseline_long_hold_losers = int(baseline_metrics.get("long_hold_loser_count") or 0)

    good_damage = round(
        sum(
            min(0.0, float(t["shadow_by_policy"][policy.grid_key]["shadow_pnl_yen_100"]) - float(t["baseline_pnl_yen_100"]))
            for t in trade_results
            if t.get("is_good_long_hold") and policy.policy_id != POLICY_BASELINE
        ),
        2,
    ) if policy.policy_id != POLICY_BASELINE else 0.0

    bad_rescue = round(
        sum(
            max(0.0, float(t["shadow_by_policy"][policy.grid_key]["shadow_pnl_yen_100"]) - float(t["baseline_pnl_yen_100"]))
            for t in trade_results
            if t.get("is_bad_long_hold") and policy.policy_id != POLICY_BASELINE
        ),
        2,
    ) if policy.policy_id != POLICY_BASELINE else 0.0

    exit_counts = {
        "stop_hit": sum(1 for r in shadow_reasons if r == "stop_hit"),
        "trailing_mfe": sum(1 for r in shadow_reasons if r == "trailing_mfe"),
        "session_close": sum(1 for r in shadow_reasons if r == "session_close"),
    }

    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trade_results)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    chron_shadow = [shadow_pnls[i] for i in order]

    total_pnl = round(sum(shadow_pnls), 2)
    baseline_total = float(baseline_metrics.get("total_pnl_yen_100") or 0.0)
    baseline_dd = float(baseline_metrics.get("max_drawdown_yen_100") or 0.0)
    max_dd = _max_drawdown_yen(chron_shadow)

    adopt = False
    if policy.policy_id != POLICY_BASELINE:
        adopt = (
            total_pnl > baseline_total
            and max_dd <= baseline_dd + 0.01
            and lost < saved
            and long_hold_losers < baseline_long_hold_losers
            and abs(good_damage) < saved
        )

    return {
        "policy_id": policy.policy_id,
        "time_threshold_sec": policy.time_threshold_sec,
        "mfe_activation_after_time": policy.mfe_activation_after,
        "stop_after_time": policy.stop_after_pct,
        "total_pnl_yen_100": total_pnl,
        "profit_factor": _pf(shadow_pnls),
        "trade_count": len(trade_results),
        "win_rate": _win_rate(shadow_pnls),
        "max_drawdown_yen_100": max_dd,
        "long_hold_loser_count": long_hold_losers,
        "stop_hit_count": exit_counts["stop_hit"],
        "trailing_mfe_count": exit_counts["trailing_mfe"],
        "session_close_count": exit_counts["session_close"],
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": round(total_pnl - baseline_total, 2),
        "affected_trade_count": affected,
        "long_hold_loser_delta": long_hold_losers - baseline_long_hold_losers,
        "good_long_hold_damage_yen": good_damage,
        "bad_long_hold_rescue_yen": bad_rescue,
        "adopt_candidate": adopt,
    }


def build_trade_detail_rows(
    trade_results: Sequence[Mapping[str, Any]],
    *,
    policies: Sequence[PolicySpec],
    include_all_policies: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected = [p for p in policies if p.policy_id == POLICY_BASELINE or include_all_policies]
    if not include_all_policies:
        adopt_policies = [p for p in policies if p.policy_id != POLICY_BASELINE]
        selected.extend(adopt_policies[:0])  # filled below from summary

    for policy in policies:
        if policy.policy_id == POLICY_BASELINE:
            for t in trade_results:
                if not t.get("focus_symbol") and not t.get("is_long_hold_loser"):
                    continue
                rows.append(_trade_row(t, policy, t["baseline_pnl_yen_100"], t["baseline_exit_reason"]))
            continue
        if not include_all_policies:
            continue
        key = policy.grid_key
        for t in trade_results:
            sh = t["shadow_by_policy"][key]
            rows.append(
                _trade_row(
                    t,
                    policy,
                    sh["shadow_pnl_yen_100"],
                    _normalize_shadow_exit(sh["shadow_exit_reason"]),
                    shadow_exit_ts=sh.get("shadow_exit_ts"),
                )
            )
    return rows


def _trade_row(
    t: Mapping[str, Any],
    policy: PolicySpec,
    shadow_pnl: float,
    shadow_reason: str,
    *,
    shadow_exit_ts: Optional[float] = None,
) -> dict[str, Any]:
    baseline = float(t["baseline_pnl_yen_100"])
    exit_time = t.get("exit_time")
    if shadow_exit_ts and shadow_exit_ts > 0:
        exit_time = datetime.fromtimestamp(shadow_exit_ts, tz=JST).isoformat(timespec="seconds")
    return {
        "policy_id": policy.policy_id,
        "time_threshold_sec": policy.time_threshold_sec,
        "mfe_activation_after_time": policy.mfe_activation_after,
        "stop_after_time": policy.stop_after_pct,
        "day": t.get("day"),
        "session": t.get("session"),
        "symbol": t.get("symbol"),
        "entry_time": t.get("entry_time"),
        "exit_time": exit_time,
        "baseline_exit_time": t.get("exit_time"),
        "hold_sec": t.get("hold_sec"),
        "baseline_pnl_yen_100": baseline,
        "shadow_pnl_yen_100": round(shadow_pnl, 2),
        "delta_yen": round(shadow_pnl - baseline, 2),
        "baseline_exit_reason": t.get("baseline_exit_reason"),
        "shadow_exit_reason": shadow_reason,
        "is_long_hold_loser": t.get("is_long_hold_loser"),
        "is_good_long_hold": t.get("is_good_long_hold"),
        "is_bad_long_hold": t.get("is_bad_long_hold"),
        "focus_symbol": t.get("focus_symbol"),
    }


def run_phase402_shadow(
    *,
    repo_root: Path,
    trades_path: Optional[Path] = None,
    phase400_summary_path: Optional[Path] = None,
    output_dir: Path,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    trades_path = trades_path or (repo_root / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv")
    phase400_summary_path = phase400_summary_path or (
        repo_root / "results" / "reports" / "phase400_holding_time_summary.json"
    )

    p90_hold = 1290.6
    if phase400_summary_path.is_file():
        p400 = json.loads(phase400_summary_path.read_text(encoding="utf-8"))
        p90_hold = float(p400.get("hold_duration_sec", {}).get("p90_hold_sec") or p90_hold)

    raw = load_phase399_trades(trades_path)
    accepted = [
        enrich_trade(r)
        for r in raw
        if str(r.get("day") or "") >= period_start
        and str(r.get("day") or "") <= period_end
        and str(r.get("position_cap_accepted") or "").lower() in ("true", "1", "yes")
    ]
    for t in accepted:
        t["_p90_hold"] = p90_hold

    policies = iter_policy_grid()
    session_cache: dict[str, dict[str, Any]] = {}
    trade_results: list[dict[str, Any]] = []

    shadow_policies = [p for p in policies if p.policy_id != POLICY_BASELINE]

    for trade in accepted:
        ctx = _prepare_trade_context(trade, repo_root=repo_root, session_cache=session_cache)
        if ctx is None:
            continue
        shadow_by_policy: dict[str, dict[str, Any]] = {}
        for policy in shadow_policies:
            sim = simulate_time_decay_exit(
                ctx["price_series"],
                entry_ts=ctx["entry_ts"],
                entry_price=ctx["entry_price"],
                session_end_ts=ctx["session_end_ts"],
                imb_pct=ctx["imb_pct"],
                policy=policy,
            )
            shadow_by_policy[policy.grid_key] = sim
        trade_results.append({**ctx, "shadow_by_policy": shadow_by_policy})

    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trade_results]
    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trade_results)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    baseline_metrics = {
        "total_pnl_yen_100": round(sum(baseline_pnls), 2),
        "profit_factor": _pf(baseline_pnls),
        "trade_count": len(trade_results),
        "win_rate": _win_rate(baseline_pnls),
        "max_drawdown_yen_100": _max_drawdown_yen([baseline_pnls[i] for i in order]),
        "long_hold_loser_count": sum(1 for t in trade_results if t.get("is_long_hold_loser")),
        "stop_hit_count": sum(1 for t in trade_results if t.get("baseline_exit_reason") == "stop_hit"),
        "trailing_mfe_count": sum(1 for t in trade_results if t.get("baseline_exit_reason") == "trailing_mfe"),
        "session_close_count": sum(1 for t in trade_results if t.get("baseline_exit_reason") == "session_close"),
    }

    grid_rows: list[dict[str, Any]] = []
    for policy in policies:
        row = aggregate_policy_results(
            trade_results,
            policy=policy,
            p90_hold=p90_hold,
            baseline_metrics=baseline_metrics,
        )
        grid_rows.append(row)

    adopt_rows = [r for r in grid_rows if r.get("adopt_candidate")]
    adopt_rows.sort(
        key=lambda r: (
            -float(r.get("net_delta_yen") or 0),
            float(r.get("lost_upside_yen") or 0),
            -float(r.get("bad_long_hold_rescue_yen") or 0),
        )
    )
    best_policy_key: Optional[str] = None
    if adopt_rows:
        best = adopt_rows[0]
        best_policy_key = (
            f"{best['policy_id']}|{best['time_threshold_sec']}|"
            f"{best['mfe_activation_after_time']}|{best['stop_after_time']}"
        )

    # trade detail: baseline + focus/long-hold + best adopt policy
    trade_rows: list[dict[str, Any]] = []
    baseline_policy = policies[0]
    for t in trade_results:
        if t.get("focus_symbol") or t.get("is_long_hold_loser"):
            trade_rows.append(
                _trade_row(t, baseline_policy, t["baseline_pnl_yen_100"], t["baseline_exit_reason"])
            )
    if best_policy_key:
        best_policy = next(p for p in shadow_policies if p.grid_key == best_policy_key)
        for t in trade_results:
            if not (t.get("focus_symbol") or t.get("is_long_hold_loser")):
                continue
            sh = t["shadow_by_policy"][best_policy_key]
            trade_rows.append(
                _trade_row(
                    t,
                    best_policy,
                    sh["shadow_pnl_yen_100"],
                    _normalize_shadow_exit(sh["shadow_exit_reason"]),
                    shadow_exit_ts=sh.get("shadow_exit_ts"),
                )
            )

    # Also include top 3 adopt candidates trade-level for all long-hold losers
    for adopt_row in adopt_rows[:3]:
        pk = (
            f"{adopt_row['policy_id']}|{adopt_row['time_threshold_sec']}|"
            f"{adopt_row['mfe_activation_after_time']}|{adopt_row['stop_after_time']}"
        )
        pol = next(p for p in shadow_policies if p.grid_key == pk)
        for t in trade_results:
            if not t.get("is_long_hold_loser"):
                continue
            sh = t["shadow_by_policy"][pk]
            trade_rows.append(
                _trade_row(
                    t,
                    pol,
                    sh["shadow_pnl_yen_100"],
                    _normalize_shadow_exit(sh["shadow_exit_reason"]),
                    shadow_exit_ts=sh.get("shadow_exit_ts"),
                )
            )

    grid_path = output_dir / "phase402_time_decay_exit_grid.csv"
    trades_path_out = output_dir / "phase402_time_decay_exit_trades.csv"
    _write_csv(grid_path, grid_rows, GRID_FIELDS)
    _write_csv(trades_path_out, trade_rows, TRADE_FIELDS)

    long_hold_loser_baseline = [t for t in trade_results if t.get("is_long_hold_loser")]
    cohort_stats = _long_hold_loser_cohort_stats(long_hold_loser_baseline, best_policy_key)
    focus_analysis = _focus_symbol_analysis(trade_results, shadow_policies, best_policy_key)
    policy_type_summary = _policy_type_summary(grid_rows)

    summary = {
        "phase": 402,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "source_trades": str(trades_path),
        "position_cap_accepted_trade_count": len(trade_results),
        "p90_hold_sec": p90_hold,
        "baseline": baseline_metrics,
        "grid_row_count": len(grid_rows),
        "adopt_candidate_count": len(adopt_rows),
        "adopt_candidates": adopt_rows[:10],
        "best_adopt_policy": adopt_rows[0] if adopt_rows else None,
        "long_hold_loser_cohort": {
            "count": len(long_hold_loser_baseline),
            "baseline_total_pnl_yen_100": round(
                sum(float(t["baseline_pnl_yen_100"]) for t in long_hold_loser_baseline),
                2,
            ),
            **cohort_stats,
        },
        "policy_type_summary": policy_type_summary,
        "focus_symbol_analysis": focus_analysis,
        "verdict": "adopt_candidate_found" if adopt_rows else "no_adopt_candidate",
        "headline": _headline(baseline_metrics, adopt_rows, focus_analysis),
    }

    summary_path = output_dir / "phase402_time_decay_exit_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase402_time_decay_exit_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render_report(summary, grid_rows, focus_analysis, policy_type_summary),
        encoding="utf-8",
    )

    return {
        "summary": summary,
        "grid_path": str(grid_path),
        "trades_path": str(trades_path_out),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }


def _long_hold_loser_cohort_stats(
    losers: Sequence[Mapping[str, Any]],
    best_policy_key: Optional[str],
) -> dict[str, Any]:
    baseline_total = round(sum(float(t["baseline_pnl_yen_100"]) for t in losers), 2)
    if not best_policy_key:
        return {"shadow_total_pnl_yen_100": baseline_total, "cohort_delta_yen": 0.0, "improved_count": 0}
    shadow_pnls = [
        float(t["shadow_by_policy"][best_policy_key]["shadow_pnl_yen_100"]) for t in losers
    ]
    improved = sum(
        1
        for t, s in zip(losers, shadow_pnls)
        if s > float(t["baseline_pnl_yen_100"]) + 0.01
    )
    worsened = sum(
        1
        for t, s in zip(losers, shadow_pnls)
        if s < float(t["baseline_pnl_yen_100"]) - 0.01
    )
    unchanged = len(losers) - improved - worsened
    return {
        "shadow_total_pnl_yen_100": round(sum(shadow_pnls), 2),
        "cohort_delta_yen": round(sum(shadow_pnls) - baseline_total, 2),
        "improved_count": improved,
        "worsened_count": worsened,
        "unchanged_count": unchanged,
    }


def _policy_type_summary(grid_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for row in grid_rows:
        pid = str(row.get("policy_id") or "")
        if pid == POLICY_BASELINE:
            continue
        by_type.setdefault(pid, []).append(row)
    out: dict[str, Any] = {}
    for pid, rows in sorted(by_type.items()):
        best = max(rows, key=lambda r: float(r.get("net_delta_yen") or 0))
        adopt_n = sum(1 for r in rows if r.get("adopt_candidate"))
        out[pid] = {
            "grid_variants": len(rows),
            "adopt_candidate_count": adopt_n,
            "best_net_delta_yen": best.get("net_delta_yen"),
            "best_long_hold_loser_delta": best.get("long_hold_loser_delta"),
            "best_params": {
                "time_threshold_sec": best.get("time_threshold_sec"),
                "mfe_activation_after_time": best.get("mfe_activation_after_time"),
                "stop_after_time": best.get("stop_after_time"),
            },
        }
    return out


def _focus_symbol_analysis(
    trade_results: Sequence[Mapping[str, Any]],
    shadow_policies: Sequence[PolicySpec],
    best_policy_key: Optional[str],
) -> dict[str, Any]:
    good = [t for t in trade_results if t.get("is_good_long_hold")]
    bad = [t for t in trade_results if t.get("is_bad_long_hold")]

    def _symbol_summary(group: Sequence[Mapping[str, Any]], policy_key: Optional[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        by_sym: dict[str, list[Mapping[str, Any]]] = {}
        for t in group:
            by_sym.setdefault(str(t["symbol"]), []).append(t)
        for sym, rows in sorted(by_sym.items()):
            baseline = round(sum(float(r["baseline_pnl_yen_100"]) for r in rows), 2)
            shadow = baseline
            if policy_key:
                shadow = round(
                    sum(float(r["shadow_by_policy"][policy_key]["shadow_pnl_yen_100"]) for r in rows),
                    2,
                )
            out.append(
                {
                    "symbol": sym,
                    "trade_count": len(rows),
                    "baseline_pnl_yen_100": baseline,
                    "shadow_pnl_yen_100": shadow,
                    "delta_yen": round(shadow - baseline, 2),
                }
            )
        return out

    return {
        "good_long_hold_symbols": _symbol_summary(good, best_policy_key),
        "bad_long_hold_symbols": _symbol_summary(bad, best_policy_key),
        "good_long_hold_damage_yen_best": round(
            sum(
                min(
                    0.0,
                    float(r["shadow_by_policy"][best_policy_key]["shadow_pnl_yen_100"])
                    - float(r["baseline_pnl_yen_100"]),
                )
                for r in good
            ),
            2,
        )
        if best_policy_key
        else None,
        "bad_long_hold_rescue_yen_best": round(
            sum(
                max(
                    0.0,
                    float(r["shadow_by_policy"][best_policy_key]["shadow_pnl_yen_100"])
                    - float(r["baseline_pnl_yen_100"]),
                )
                for r in bad
            ),
            2,
        )
        if best_policy_key
        else None,
    }


def _headline(
    baseline: Mapping[str, Any],
    adopt_rows: Sequence[Mapping[str, Any]],
    focus: Mapping[str, Any],
) -> str:
    if not adopt_rows:
        return (
            f"Phase402: 時間減衰exit shadow — adopt候補なし "
            f"(baseline PnL ¥{baseline.get('total_pnl_yen_100')}, long_hold_loser={baseline.get('long_hold_loser_count')})"
        )
    best = adopt_rows[0]
    return (
        f"Phase402: {best.get('policy_id')} threshold={best.get('time_threshold_sec')}s "
        f"MFE={best.get('mfe_activation_after_time')}% stop={best.get('stop_after_time')}% "
        f"ΔPnL ¥{best.get('net_delta_yen')} long_hold_loser Δ{best.get('long_hold_loser_delta')} "
        f"bad_rescue ¥{focus.get('bad_long_hold_rescue_yen_best')}"
    )


def _render_report(
    summary: Mapping[str, Any],
    grid_rows: Sequence[Mapping[str, Any]],
    focus: Mapping[str, Any],
    policy_type_summary: Mapping[str, Any],
) -> str:
    baseline = summary.get("baseline") or {}
    best = summary.get("best_adopt_policy")
    lines = [
        "# Phase402 — Time-Decayed MFE / Stop Shadow",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Period: {summary.get('period_start')} – {summary.get('period_end')}",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        summary.get("headline") or "",
        "",
        "## Baseline (Phase399 position_cap_accepted)",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| total_pnl_yen_100 | ¥{baseline.get('total_pnl_yen_100')} |",
        f"| profit_factor | {baseline.get('profit_factor')} |",
        f"| trade_count | {baseline.get('trade_count')} |",
        f"| win_rate | {baseline.get('win_rate')} |",
        f"| max_drawdown_yen_100 | ¥{baseline.get('max_drawdown_yen_100')} |",
        f"| long_hold_loser_count | {baseline.get('long_hold_loser_count')} |",
        f"| stop_hit_count | {baseline.get('stop_hit_count')} |",
        f"| trailing_mfe_count | {baseline.get('trailing_mfe_count')} |",
        f"| session_close_count | {baseline.get('session_close_count')} |",
        "",
        "## Long-hold loser cohort (27)",
        "",
        f"- baseline total: ¥{summary.get('long_hold_loser_cohort', {}).get('baseline_total_pnl_yen_100')}",
        f"- best-policy shadow total: ¥{summary.get('long_hold_loser_cohort', {}).get('shadow_total_pnl_yen_100', 'n/a')}",
        f"- cohort delta: ¥{summary.get('long_hold_loser_cohort', {}).get('cohort_delta_yen', 'n/a')}",
        f"- improved / worsened / unchanged: "
        f"{summary.get('long_hold_loser_cohort', {}).get('improved_count', 'n/a')} / "
        f"{summary.get('long_hold_loser_cohort', {}).get('worsened_count', 'n/a')} / "
        f"{summary.get('long_hold_loser_cohort', {}).get('unchanged_count', 'n/a')}",
        "",
        "## Policy type comparison",
        "",
        "| policy | variants | adopt | best net_delta | best long_hold_loser_Δ |",
        "|--------|----------|-------|----------------|------------------------|",
    ]
    for pid, info in sorted((policy_type_summary or {}).items()):
        lines.append(
            f"| {pid} | {info.get('grid_variants')} | {info.get('adopt_candidate_count')} | "
            f"¥{info.get('best_net_delta_yen')} | {info.get('best_long_hold_loser_delta')} |"
        )
    lines.extend(
        [
            "",
            "## Focus symbols",
            "",
            "### Good long holds (should not damage)",
            "",
            "| symbol | trades | baseline | shadow (best) | delta |",
            "|--------|--------|----------|---------------|-------|",
        ]
    )
    for row in focus.get("good_long_hold_symbols") or []:
        lines.append(
            f"| {row.get('symbol')} | {row.get('trade_count')} | "
            f"¥{row.get('baseline_pnl_yen_100')} | ¥{row.get('shadow_pnl_yen_100')} | "
            f"¥{row.get('delta_yen')} |"
        )
    lines.extend(
        [
            "",
            "### Bad long holds (should rescue)",
            "",
            "| symbol | trades | baseline | shadow (best) | delta |",
            "|--------|--------|----------|---------------|-------|",
        ]
    )
    for row in focus.get("bad_long_hold_symbols") or []:
        lines.append(
            f"| {row.get('symbol')} | {row.get('trade_count')} | "
            f"¥{row.get('baseline_pnl_yen_100')} | ¥{row.get('shadow_pnl_yen_100')} | "
            f"¥{row.get('delta_yen')} |"
        )

    lines.extend(["", "## Top adopt candidates", ""])
    if best:
        lines.append(
            f"Best: `{best.get('policy_id')}` threshold={best.get('time_threshold_sec')}s "
            f"mfe_after={best.get('mfe_activation_after_time')}% stop_after={best.get('stop_after_time')}%"
        )
        lines.append("")
        lines.append(
            f"- net_delta_yen: ¥{best.get('net_delta_yen')}"
        )
        lines.append(f"- saved_loss_yen: ¥{best.get('saved_loss_yen')}")
        lines.append(f"- lost_upside_yen: ¥{best.get('lost_upside_yen')}")
        lines.append(f"- long_hold_loser_delta: {best.get('long_hold_loser_delta')}")
        lines.append(f"- good_long_hold_damage_yen: ¥{best.get('good_long_hold_damage_yen')}")
        lines.append(f"- bad_long_hold_rescue_yen: ¥{best.get('bad_long_hold_rescue_yen')}")
    else:
        lines.append("No policy passed adopt_candidate criteria.")

    lines.extend(["", "## Grid top 10 by net_delta_yen", ""])
    ranked = sorted(
        [r for r in grid_rows if r.get("policy_id") != POLICY_BASELINE],
        key=lambda r: -float(r.get("net_delta_yen") or 0),
    )[:10]
    lines.append(
        "| policy | threshold | mfe_after | stop_after | net_delta | long_hold_loser_Δ | adopt |"
    )
    lines.append("|--------|-----------|-----------|------------|-----------|---------------------|-------|")
    for r in ranked:
        lines.append(
            f"| {r.get('policy_id')} | {r.get('time_threshold_sec')} | "
            f"{r.get('mfe_activation_after_time')} | {r.get('stop_after_time')} | "
            f"¥{r.get('net_delta_yen')} | {r.get('long_hold_loser_delta')} | "
            f"{r.get('adopt_candidate')} |"
        )

    lines.extend(
        [
            "",
            "## Constraints",
            "",
            "- Runtime反映なし",
            "- YAML変更なし",
            "- Exit本番変更なし",
            "- shadow / research のみ",
            "",
        ]
    )
    return "\n".join(lines)
