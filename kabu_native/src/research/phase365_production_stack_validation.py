"""
Phase365: Production stack historical validation.

Compare:
  A — baseline (no ENTRY guards)
  B — Phase355 pullback_misread_dynamic40_guard only
  C — Phase355 + Phase364 near_day_high_low_momentum_dynamic40_guard (current production)
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase357_actual_exit_audit import _universe_group
from small_paper.near_day_high_low_mom_entry_guard_shadow import (
    _is_dynamic40,
    would_block_near_day_high_low_mom_guard,
)
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow

JST = ZoneInfo("Asia/Tokyo")
MIN_DAY = "20260518"
EXCLUDE_DAY = "20260612"
CONCENTRATION_MAX_SHARE = 0.5

STACK_VARIANTS = (
    "A_baseline_no_guard",
    "B_phase355_only",
    "C_phase355_plus_phase364",
)

STACK_LABELS = {
    "A_baseline_no_guard": "Baseline (no ENTRY guards)",
    "B_phase355_only": "Phase355 pullback_misread_dynamic40_guard only",
    "C_phase355_plus_phase364": "Phase355 + Phase364 (current production)",
}


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def phase364_blocked_only(trade: Mapping[str, Any]) -> bool:
    return would_block_near_day_high_low_mom_guard(trade) and _is_dynamic40(trade)


def stack_blocked(stack: str, trade: Mapping[str, Any], *, session_kind: str) -> bool:
    del session_kind
    if stack == "A_baseline_no_guard":
        return False
    if stack == "B_phase355_only":
        return would_block_pullback_dynamic40_shadow(trade)
    if stack == "C_phase355_plus_phase364":
        return would_block_pullback_dynamic40_shadow(trade) or phase364_blocked_only(trade)
    return False


def enrich_production_stack_trade(
    trade: Mapping[str, Any], acc: Mapping[str, str]
) -> dict[str, Any]:
    from research.phase362_stack_validation import enrich_stack_trade

    row = enrich_stack_trade(trade, acc)
    row["phase364_would_block"] = phase364_blocked_only(row)
    row["is_trailing_mfe_exit"] = row.get("exit_reason_canonical") == "trailing_mfe_exit"
    return row


def load_session_production_stack_trades(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    from research.phase357_actual_exit_audit import _load_session_trades
    from small_paper.limit_up_proximity_entry_guard_shadow import _session_source_label
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = _load_session_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "trades": [], "error": base.get("error")}

    sess_dir = Path(str(session_meta["session_dir"]))
    session_kind = str(base.get("session_kind") or "")

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for trade in base.get("all") or []:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        t = enrich_production_stack_trade(trade, acc)
        t["session_id"] = session_meta.get("session_id") or ""
        t["day_key"] = session_meta.get("day_key") or session_meta.get("day") or ""
        t["session_kind"] = session_kind
        t["universe_group"] = t.get("universe_group") or _universe_group(t)
        trades.append(t)

    return {
        **base,
        "trades": trades,
        "trade_count_actual": len(trades),
        "session_kind": session_kind,
        "session_source": str(
            session_meta.get("session_source") or _session_source_label(sess_dir)
        ),
        "error": "",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _concentration(
    day_deltas: dict[str, float],
    symbol_skipped: dict[str, float],
    *,
    total_delta: float,
    total_skipped_pnl: float,
) -> dict[str, Any]:
    day_share = sym_share = None
    top_day = top_symbol = ""
    if abs(total_delta) > 1e-6 and day_deltas:
        top_day = max(day_deltas, key=lambda d: abs(day_deltas[d]))
        day_share = round(abs(day_deltas[top_day]) / abs(total_delta), 4)
    if abs(total_skipped_pnl) > 1e-6 and symbol_skipped:
        top_symbol = min(symbol_skipped, key=symbol_skipped.get)
        sym_share = round(abs(symbol_skipped[top_symbol]) / abs(total_skipped_pnl), 4)
    return {
        "top_day": top_day,
        "top_day_delta_share": day_share,
        "top_symbol": top_symbol,
        "top_symbol_skipped_pnl_share": sym_share,
        "not_single_day_dependent": day_share is None or day_share < CONCENTRATION_MAX_SHARE,
        "not_single_symbol_dependent": sym_share is None or sym_share < CONCENTRATION_MAX_SHARE,
    }


def _stack_metrics_from_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    stack: str,
    session_kind: str,
) -> dict[str, Any]:
    yens: list[float] = []
    stops = trails = wins = skipped = 0
    skipped_pnl = 0.0
    dyn_pnl = core_pnl = 0.0
    for t in trades:
        yen = _float(t.get("pnl_yen_100"))
        if yen is None:
            continue
        if stack_blocked(stack, t, session_kind=session_kind):
            skipped += 1
            skipped_pnl += float(yen)
            continue
        y = float(yen)
        yens.append(y)
        if y > 0:
            wins += 1
        if t.get("is_stop_hit"):
            stops += 1
        if t.get("is_trailing_mfe_exit"):
            trails += 1
        ug = str(t.get("universe_group") or "")
        if ug == "dynamic40":
            dyn_pnl += y
        elif ug == "core10":
            core_pnl += y
    total = round(sum(yens), 2) if yens else 0.0
    n = len(yens)
    return {
        "total_pnl_yen_100": total,
        "profit_factor": _pf(yens),
        "win_rate": round(wins / n, 4) if n else None,
        "trade_count": n,
        "stop_hit_count": stops,
        "trailing_mfe_exit_count": trails,
        "skipped_trade_count": skipped,
        "skipped_trade_pnl_actual": round(skipped_pnl, 2),
        "dynamic40_pnl_yen_100": round(dyn_pnl, 2),
        "core10_pnl_yen_100": round(core_pnl, 2),
        "yens": yens,
    }


@dataclass
class Phase365ProductionStackValidation:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase365_production_stack_validation_summary.json",
            "by_day": self.reports_dir / "phase365_production_stack_validation_by_day.csv",
            "by_symbol": self.reports_dir
            / "phase365_production_stack_validation_by_symbol.csv",
            "by_universe": self.reports_dir
            / "phase365_production_stack_validation_by_universe.csv",
            "trades": self.reports_dir / "phase365_production_stack_validation_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error") or int(result.get("trade_count_actual") or 0) <= 0:
            return
        self.session_results.append(dict(result))

    def _aggregate(self) -> dict[str, Any]:
        by_day_stack: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "total_pnl_yen_100": 0.0,
                "trade_count": 0,
                "skipped_trade_count": 0,
                "skipped_trade_pnl_actual": 0.0,
                "stop_hit_count": 0,
                "trailing_mfe_exit_count": 0,
                "session_count": 0,
            }
        )
        by_universe_stack: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "total_pnl_yen_100": 0.0,
                "trade_count": 0,
                "stop_hit_count": 0,
                "skipped_trade_count": 0,
            }
        )
        by_am_pm_stack: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "total_pnl_yen_100": 0.0,
                "dynamic40_pnl": 0.0,
                "core10_pnl": 0.0,
                "session_count": 0,
            }
        )
        symbol_skipped: dict[tuple[str, str], float] = defaultdict(float)
        all_trades: list[dict[str, Any]] = []
        for sr in self.session_results:
            session_kind = str(sr.get("session_kind") or "")
            for t in sr.get("trades") or []:
                all_trades.append(
                    {
                        **dict(t),
                        "blocked_A": stack_blocked(
                            "A_baseline_no_guard", t, session_kind=session_kind
                        ),
                        "blocked_B": stack_blocked(
                            "B_phase355_only", t, session_kind=session_kind
                        ),
                        "blocked_C": stack_blocked(
                            "C_phase355_plus_phase364", t, session_kind=session_kind
                        ),
                    }
                )

        raw_stack: dict[str, dict[str, Any]] = {}

        for stack in STACK_VARIANTS:
            total_pnl = skipped_pnl = dyn_pnl = core_pnl = 0.0
            kept = skipped = stops = trails = wins = 0
            yens_kept: list[float] = []
            day_pnl_stack: dict[str, float] = defaultdict(float)
            phase364_only_skipped = phase364_only_skipped_pnl = 0
            sym_map: dict[str, float] = defaultdict(float)

            for sr in self.session_results:
                session_kind = str(sr.get("session_kind") or "")
                day = str(sr["session_meta"].get("day_key") or sr["session_meta"].get("day") or "")
                sess_pnl = 0.0
                sess_kept = 0

                for t in sr.get("trades") or []:
                    yen = _float(t.get("pnl_yen_100"))
                    if yen is None:
                        continue
                    blocked = stack_blocked(stack, t, session_kind=session_kind)
                    if blocked:
                        skipped += 1
                        skipped_pnl += float(yen)
                        if (
                            stack == "C_phase355_plus_phase364"
                            and phase364_blocked_only(t)
                            and not t.get("phase355_would_block")
                        ):
                            phase364_only_skipped += 1
                            phase364_only_skipped_pnl += float(yen)
                        sym = str(t.get("symbol") or "")
                        symbol_skipped[(stack, sym)] += float(yen)
                        if stack == "C_phase355_plus_phase364" and phase364_blocked_only(t):
                            sym_map[sym] += float(yen)
                    else:
                        kept += 1
                        total_pnl += float(yen)
                        sess_pnl += float(yen)
                        sess_kept += 1
                        yens_kept.append(float(yen))
                        if float(yen) > 0:
                            wins += 1
                        if t.get("is_stop_hit"):
                            stops += 1
                        if t.get("is_trailing_mfe_exit"):
                            trails += 1
                        ug = str(t.get("universe_group") or "")
                        if ug == "dynamic40":
                            dyn_pnl += float(yen)
                            by_universe_stack[("dynamic40", stack)]["total_pnl_yen_100"] += float(
                                yen
                            )
                            by_universe_stack[("dynamic40", stack)]["trade_count"] += 1
                            if t.get("is_stop_hit"):
                                by_universe_stack[("dynamic40", stack)]["stop_hit_count"] += 1
                        elif ug == "core10":
                            core_pnl += float(yen)
                            by_universe_stack[("core10", stack)]["total_pnl_yen_100"] += float(yen)
                            by_universe_stack[("core10", stack)]["trade_count"] += 1
                            if t.get("is_stop_hit"):
                                by_universe_stack[("core10", stack)]["stop_hit_count"] += 1

                day_pnl_stack[day] += sess_pnl
                day_key = (day, stack)
                by_day_stack[day_key]["total_pnl_yen_100"] += sess_pnl
                by_day_stack[day_key]["trade_count"] += sess_kept
                by_day_stack[day_key]["session_count"] += 1
                by_day_stack[day_key]["skipped_trade_count"] += sum(
                    1
                    for t in sr.get("trades") or []
                    if _float(t.get("pnl_yen_100")) is not None
                    and stack_blocked(stack, t, session_kind=session_kind)
                )
                by_day_stack[day_key]["skipped_trade_pnl_actual"] += sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in sr.get("trades") or []
                    if _float(t.get("pnl_yen_100")) is not None
                    and stack_blocked(stack, t, session_kind=session_kind)
                )
                by_day_stack[day_key]["stop_hit_count"] += sum(
                    1
                    for t in sr.get("trades") or []
                    if t.get("is_stop_hit")
                    and not stack_blocked(stack, t, session_kind=session_kind)
                )
                by_day_stack[day_key]["trailing_mfe_exit_count"] += sum(
                    1
                    for t in sr.get("trades") or []
                    if t.get("is_trailing_mfe_exit")
                    and not stack_blocked(stack, t, session_kind=session_kind)
                )

                kind_key = (session_kind, stack)
                by_am_pm_stack[kind_key]["total_pnl_yen_100"] += sess_pnl
                by_am_pm_stack[kind_key]["session_count"] += 1
                by_am_pm_stack[kind_key]["dynamic40_pnl"] += sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in sr.get("trades") or []
                    if _float(t.get("pnl_yen_100")) is not None
                    and str(t.get("universe_group") or "") == "dynamic40"
                    and not stack_blocked(stack, t, session_kind=session_kind)
                )
                by_am_pm_stack[kind_key]["core10_pnl"] += sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in sr.get("trades") or []
                    if _float(t.get("pnl_yen_100")) is not None
                    and str(t.get("universe_group") or "") == "core10"
                    and not stack_blocked(stack, t, session_kind=session_kind)
                )

            day_deltas: dict[str, float] = {}
            if stack == "B_phase355_only":
                ref_day = {
                    day: vals["total_pnl_yen_100"]
                    for (day, stk), vals in by_day_stack.items()
                    if stk == "A_baseline_no_guard"
                }
                for day, pnl in day_pnl_stack.items():
                    day_deltas[day] = round(pnl - ref_day.get(day, 0.0), 2)
            elif stack == "C_phase355_plus_phase364":
                ref_day = {
                    day: vals["total_pnl_yen_100"]
                    for (day, stk), vals in by_day_stack.items()
                    if stk == "B_phase355_only"
                }
                for day, pnl in day_pnl_stack.items():
                    day_deltas[day] = round(pnl - ref_day.get(day, 0.0), 2)

            raw_stack[stack] = {
                "total_pnl": round(total_pnl, 2),
                "skipped_pnl": round(skipped_pnl, 2),
                "kept": kept,
                "skipped": skipped,
                "stops": stops,
                "trails": trails,
                "wins": wins,
                "yens_kept": yens_kept,
                "dyn_pnl": round(dyn_pnl, 2),
                "core_pnl": round(core_pnl, 2),
                "phase364_only_skipped": phase364_only_skipped,
                "phase364_only_skipped_pnl": round(phase364_only_skipped_pnl, 2),
                "day_deltas": dict(day_deltas),
                "sym_map": dict(sym_map),
            }

        by_stack: dict[str, dict[str, Any]] = {}
        for stack in STACK_VARIANTS:
            raw = raw_stack[stack]
            n = raw["kept"]
            conc = _concentration(
                raw["day_deltas"] if stack != "A_baseline_no_guard" else {},
                raw["sym_map"] if stack == "C_phase355_plus_phase364" else {},
                total_delta=(
                    raw["total_pnl"] - raw_stack["A_baseline_no_guard"]["total_pnl"]
                    if stack == "B_phase355_only"
                    else raw["total_pnl"] - raw_stack["B_phase355_only"]["total_pnl"]
                    if stack == "C_phase355_plus_phase364"
                    else raw["total_pnl"]
                ),
                total_skipped_pnl=raw["skipped_pnl"],
            )
            by_stack[stack] = {
                "stack": stack,
                "label": STACK_LABELS.get(stack, stack),
                "total_pnl_yen_100": raw["total_pnl"],
                "profit_factor": _pf(raw["yens_kept"]),
                "win_rate": round(raw["wins"] / n, 4) if n else None,
                "trade_count": raw["kept"],
                "stop_hit_count": raw["stops"],
                "trailing_mfe_exit_count": raw["trails"],
                "skipped_trade_count": raw["skipped"],
                "skipped_trade_pnl_actual": raw["skipped_pnl"],
                "phase364_incremental_skipped_count": raw["phase364_only_skipped"]
                if stack == "C_phase355_plus_phase364"
                else 0,
                "phase364_incremental_skipped_pnl": raw["phase364_only_skipped_pnl"]
                if stack == "C_phase355_plus_phase364"
                else 0.0,
                "dynamic40_pnl_yen_100": raw["dyn_pnl"],
                "core10_pnl_yen_100": raw["core_pnl"],
                **conc,
            }

        a_row = by_stack["A_baseline_no_guard"]
        b_row = by_stack["B_phase355_only"]
        c_row = by_stack["C_phase355_plus_phase364"]

        b_row["delta_vs_a_yen"] = round(
            b_row["total_pnl_yen_100"] - a_row["total_pnl_yen_100"], 2
        )
        b_row["delta_pf_vs_a"] = (
            round((b_row["profit_factor"] or 0) - (a_row["profit_factor"] or 0), 4)
            if b_row["profit_factor"] is not None and a_row["profit_factor"] is not None
            else None
        )
        b_row["stop_hit_reduction_vs_a"] = a_row["stop_hit_count"] - b_row["stop_hit_count"]

        c_row["delta_vs_a_yen"] = round(
            c_row["total_pnl_yen_100"] - a_row["total_pnl_yen_100"], 2
        )
        c_row["delta_vs_b_yen"] = round(
            c_row["total_pnl_yen_100"] - b_row["total_pnl_yen_100"], 2
        )
        c_row["delta_pf_vs_a"] = (
            round((c_row["profit_factor"] or 0) - (a_row["profit_factor"] or 0), 4)
            if c_row["profit_factor"] is not None and a_row["profit_factor"] is not None
            else None
        )
        c_row["delta_pf_vs_b"] = (
            round((c_row["profit_factor"] or 0) - (b_row["profit_factor"] or 0), 4)
            if c_row["profit_factor"] is not None and b_row["profit_factor"] is not None
            else None
        )
        c_row["stop_hit_reduction_vs_a"] = a_row["stop_hit_count"] - c_row["stop_hit_count"]
        c_row["stop_hit_reduction_vs_b"] = b_row["stop_hit_count"] - c_row["stop_hit_count"]
        c_row["dynamic40_delta_vs_b"] = round(
            c_row["dynamic40_pnl_yen_100"] - b_row["dynamic40_pnl_yen_100"], 2
        )
        c_row["core10_delta_vs_b"] = round(
            c_row["core10_pnl_yen_100"] - b_row["core10_pnl_yen_100"], 2
        )

        improved_vs_a: dict[str, int] = defaultdict(int)
        worsened_vs_a: dict[str, int] = defaultdict(int)
        improved_vs_b: dict[str, int] = defaultdict(int)
        worsened_vs_b: dict[str, int] = defaultdict(int)

        for sr in self.session_results:
            session_kind = str(sr.get("session_kind") or "")
            trades = sr.get("trades") or []
            a_sess = _stack_metrics_from_trades(
                trades, stack="A_baseline_no_guard", session_kind=session_kind
            )["total_pnl_yen_100"]
            b_sess = _stack_metrics_from_trades(
                trades, stack="B_phase355_only", session_kind=session_kind
            )["total_pnl_yen_100"]
            c_sess = _stack_metrics_from_trades(
                trades, stack="C_phase355_plus_phase364", session_kind=session_kind
            )["total_pnl_yen_100"]
            delta_b = round(b_sess - a_sess, 2)
            delta_c_a = round(c_sess - a_sess, 2)
            delta_c_b = round(c_sess - b_sess, 2)
            if delta_b > 0:
                improved_vs_a["B_phase355_only"] += 1
            elif delta_b < 0:
                worsened_vs_a["B_phase355_only"] += 1
            if delta_c_a > 0:
                improved_vs_a["C_phase355_plus_phase364"] += 1
            elif delta_c_a < 0:
                worsened_vs_a["C_phase355_plus_phase364"] += 1
            if delta_c_b > 0:
                improved_vs_b["C_phase355_plus_phase364"] += 1
            elif delta_c_b < 0:
                worsened_vs_b["C_phase355_plus_phase364"] += 1

        for stack in ("B_phase355_only", "C_phase355_plus_phase364"):
            by_stack[stack]["improved_session_count_vs_a"] = improved_vs_a[stack]
            by_stack[stack]["worsened_session_count_vs_a"] = worsened_vs_a[stack]
        by_stack["C_phase355_plus_phase364"]["improved_session_count_vs_b"] = improved_vs_b[
            "C_phase355_plus_phase364"
        ]
        by_stack["C_phase355_plus_phase364"]["worsened_session_count_vs_b"] = worsened_vs_b[
            "C_phase355_plus_phase364"
        ]

        def _subset_metrics(
            sessions: Sequence[Mapping[str, Any]], *, exclude_days: set[str]
        ) -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for stack in STACK_VARIANTS:
                trades = [
                    t
                    for sr in sessions
                    if str(sr["session_meta"].get("day_key") or sr["session_meta"].get("day") or "")
                    not in exclude_days
                    for t in sr.get("trades") or []
                ]
                m = _stack_metrics_from_trades(
                    trades,
                    stack=stack,
                    session_kind=str(sessions[0].get("session_kind") or "") if sessions else "",
                )
                out[stack] = {
                    "total_pnl_yen_100": m["total_pnl_yen_100"],
                    "profit_factor": m["profit_factor"],
                    "trade_count": m["trade_count"],
                    "stop_hit_count": m["stop_hit_count"],
                }
            return out

        ex_612 = _subset_metrics(self.session_results, exclude_days={EXCLUDE_DAY})
        ex_612["C_phase355_plus_phase364"]["delta_vs_b_yen"] = round(
            ex_612["C_phase355_plus_phase364"]["total_pnl_yen_100"]
            - ex_612["B_phase355_only"]["total_pnl_yen_100"],
            2,
        )
        ex_612["C_phase355_plus_phase364"]["delta_pf_vs_b"] = (
            round(
                (ex_612["C_phase355_plus_phase364"]["profit_factor"] or 0)
                - (ex_612["B_phase355_only"]["profit_factor"] or 0),
                4,
            )
            if ex_612["C_phase355_plus_phase364"]["profit_factor"] is not None
            and ex_612["B_phase355_only"]["profit_factor"] is not None
            else None
        )

        return {
            "by_stack": by_stack,
            "by_day_stack": by_day_stack,
            "by_am_pm_stack": by_am_pm_stack,
            "by_universe_stack": by_universe_stack,
            "symbol_skipped": symbol_skipped,
            "all_trades": all_trades,
            "ex_612": ex_612,
        }

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int
    ) -> dict[str, Path]:
        agg = self._aggregate()
        paths = self.paths()
        by_stack = agg["by_stack"]
        a_row = by_stack["A_baseline_no_guard"]
        b_row = by_stack["B_phase355_only"]
        c_row = by_stack["C_phase355_plus_phase364"]
        ex_612 = agg["ex_612"]

        by_day_rows = [
            {
                "day": day,
                "stack": stack,
                "total_pnl_yen_100": round(vals["total_pnl_yen_100"], 2),
                "trade_count": int(vals["trade_count"]),
                "skipped_trade_count": int(vals["skipped_trade_count"]),
                "skipped_trade_pnl_actual": round(vals["skipped_trade_pnl_actual"], 2),
                "stop_hit_count": int(vals["stop_hit_count"]),
                "trailing_mfe_exit_count": int(vals["trailing_mfe_exit_count"]),
                "session_count": int(vals["session_count"]),
            }
            for (day, stack), vals in sorted(agg["by_day_stack"].items())
        ]
        by_symbol_rows = [
            {
                "stack": stack,
                "symbol": sym,
                "skipped_trade_pnl_actual": round(pnl, 2),
            }
            for (stack, sym), pnl in sorted(
                agg["symbol_skipped"].items(), key=lambda x: (x[0][0], x[1])
            )
        ]
        by_universe_rows = [
            {
                "universe_group": ug,
                "stack": stack,
                "total_pnl_yen_100": round(vals["total_pnl_yen_100"], 2),
                "trade_count": int(vals["trade_count"]),
                "stop_hit_count": int(vals["stop_hit_count"]),
            }
            for (ug, stack), vals in sorted(agg["by_universe_stack"].items())
        ]

        c_delta_b = c_row.get("delta_vs_b_yen") or 0
        c_pf_delta_b = c_row.get("delta_pf_vs_b") or 0
        ex_delta = ex_612["C_phase355_plus_phase364"].get("delta_vs_b_yen") or 0
        dyn_delta = c_row.get("dynamic40_delta_vs_b") or 0
        core_delta = c_row.get("core10_delta_vs_b") or 0
        improved_b = c_row.get("improved_session_count_vs_b") or 0
        worsened_b = c_row.get("worsened_session_count_vs_b") or 0

        maintain = (
            c_delta_b > 0
            and (c_pf_delta_b or 0) > 0
            and improved_b >= worsened_b
            and ex_delta > 0
            and bool(c_row.get("not_single_symbol_dependent"))
            and dyn_delta >= 0
            and core_delta >= 0
        )

        summary = {
            "phase": 365,
            "title": "production_stack_historical_validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "all_observer_exit_trades",
            "date_range": {"min_day": MIN_DAY, "exclude_day_analysis": EXCLUDE_DAY},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "stacks": STACK_LABELS,
            "by_stack": by_stack,
            "by_am_pm": {
                f"{kind}/{stack}": vals
                for (kind, stack), vals in sorted(agg["by_am_pm_stack"].items())
            },
            "ex_612_by_stack": ex_612,
            "conclusion": {
                "baseline_total_pnl": a_row["total_pnl_yen_100"],
                "baseline_pf": a_row["profit_factor"],
                "phase355_only_total_pnl": b_row["total_pnl_yen_100"],
                "phase355_only_pf": b_row["profit_factor"],
                "phase355_delta_vs_baseline_yen": b_row.get("delta_vs_a_yen"),
                "phase355_delta_pf_vs_baseline": b_row.get("delta_pf_vs_a"),
                "current_production_total_pnl": c_row["total_pnl_yen_100"],
                "current_production_pf": c_row["profit_factor"],
                "phase364_incremental_delta_vs_phase355_yen": c_delta_b,
                "phase364_incremental_delta_pf_vs_phase355": c_pf_delta_b,
                "full_period_delta_vs_baseline_yen": c_row.get("delta_vs_a_yen"),
                "full_period_delta_pf_vs_baseline": c_row.get("delta_pf_vs_a"),
                "ex_612_delta_c_vs_b_yen": ex_delta,
                "ex_612_delta_pf_c_vs_b": ex_612["C_phase355_plus_phase364"].get(
                    "delta_pf_vs_b"
                ),
                "dynamic40_delta_c_vs_b": dyn_delta,
                "core10_delta_c_vs_b": core_delta,
                "stop_hit_reduction_c_vs_b": c_row.get("stop_hit_reduction_vs_b"),
                "improved_session_count_c_vs_b": improved_b,
                "worsened_session_count_c_vs_b": worsened_b,
                "top_day_delta_share_c_vs_b": c_row.get("top_day_delta_share"),
                "top_symbol_skipped_pnl_share_c": c_row.get("top_symbol_skipped_pnl_share"),
                "maintain_production_stack": maintain,
                "recommendation": (
                    "Maintain Phase355 + Phase364 production stack."
                    if maintain
                    else "Review Phase364 rollout; one or more acceptance criteria not met."
                ),
            },
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if by_day_rows:
            _write_csv(paths["by_day"], by_day_rows, sorted({k for r in by_day_rows for k in r}))
        if by_symbol_rows:
            _write_csv(
                paths["by_symbol"],
                by_symbol_rows,
                ["stack", "symbol", "skipped_trade_pnl_actual"],
            )
        if by_universe_rows:
            _write_csv(
                paths["by_universe"],
                by_universe_rows,
                ["universe_group", "stack", "total_pnl_yen_100", "trade_count", "stop_hit_count"],
            )
        trade_rows = agg["all_trades"]
        if trade_rows:
            trade_fields = sorted({k for r in trade_rows for k in r})
            _write_csv(paths["trades"], trade_rows, trade_fields)
        return paths
