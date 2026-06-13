"""
Phase362: Production candidate stack validation (Phase355 + Phase361).
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

from research.phase357_actual_exit_audit import MAX_DAY, MIN_DAY, _universe_group
from small_paper.near_day_high_low_mom_entry_guard_shadow import (
    _is_dynamic40,
    enrich_trade_for_near_day_high_low_mom_shadow,
    would_block_near_day_high_low_mom_guard,
)
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow

JST = ZoneInfo("Asia/Tokyo")
FOCUS_DAY_AM = "20260612"
CONCENTRATION_MAX_SHARE = 0.5

STACK_VARIANTS = (
    "A_phase355_only",
    "B_phase355_plus_c03_all",
    "C_phase355_plus_c03_dynamic40",
)

STACK_LABELS = {
    "A_phase355_only": "Phase355 pullback guard only (current production)",
    "B_phase355_plus_c03_all": "Phase355 + C03 near_day_high_low_mom (all symbols)",
    "C_phase355_plus_c03_dynamic40": "Phase355 + C03 near_day_high_low_mom (Dynamic40 only)",
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


def stack_blocked(stack: str, trade: Mapping[str, Any], *, session_kind: str) -> bool:
    if would_block_pullback_dynamic40_shadow(trade):
        return True
    if stack == "A_phase355_only":
        return False
    if stack == "B_phase355_plus_c03_all":
        return would_block_near_day_high_low_mom_guard(trade)
    if stack == "C_phase355_plus_c03_dynamic40":
        return would_block_near_day_high_low_mom_guard(trade) and _is_dynamic40(trade)
    return False


def c03_blocked_only(trade: Mapping[str, Any], *, scope: str) -> bool:
    if not would_block_near_day_high_low_mom_guard(trade):
        return False
    if scope == "all":
        return True
    if scope == "dynamic40":
        return _is_dynamic40(trade)
    return False


def enrich_stack_trade(trade: Mapping[str, Any], acc: Mapping[str, str]) -> dict[str, Any]:
    row = enrich_trade_for_near_day_high_low_mom_shadow(trade, acc)
    rise5 = _float(acc.get("entry_rise_5min_pct") or trade.get("entry_rise_5min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or trade.get("entry_vwap_dev_pct"))
    pb_fields = {
        "entry_rise_5min_pct": rise5,
        "entry_vwap_dev_pct": vwap_dev,
        "universe_slot": trade.get("universe_slot"),
        "source_bucket": trade.get("source_bucket"),
        "universe_bucket": trade.get("universe_bucket"),
    }
    row["phase355_would_block"] = would_block_pullback_dynamic40_shadow(pb_fields)
    row["c03_would_block"] = would_block_near_day_high_low_mom_guard(row)
    row["universe_group"] = trade.get("universe_group") or _universe_group(trade)
    row["is_stop_hit"] = (
        trade.get("exit_reason_canonical") == "stop_hit"
        or str(trade.get("structural_exit_reason") or trade.get("exit_reason") or "")
        == "stop_hit"
    )
    return row


def load_session_stack_trades(session_meta: Mapping[str, Any], *, reports_dir: Path) -> dict[str, Any]:
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
        t = enrich_stack_trade(trade, acc)
        t["session_id"] = session_meta.get("session_id") or ""
        t["day_key"] = session_meta.get("day_key") or session_meta.get("day") or ""
        t["session_kind"] = session_kind
        trades.append(t)

    return {
        **base,
        "trades": trades,
        "trade_count_actual": len(trades),
        "session_kind": session_kind,
        "session_source": str(session_meta.get("session_source") or _session_source_label(sess_dir)),
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


@dataclass
class Phase362StackValidation:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase362_stack_validation_summary.json",
            "by_variant": self.reports_dir / "phase362_stack_validation_by_variant.csv",
            "by_day": self.reports_dir / "phase362_stack_validation_by_day.csv",
            "by_symbol": self.reports_dir / "phase362_stack_validation_by_symbol.csv",
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
                "session_count": 0,
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

        day_pnl_a: dict[str, float] = defaultdict(float)
        for sr in self.session_results:
            session_kind = str(sr.get("session_kind") or "")
            day = str(sr["session_meta"].get("day_key") or sr["session_meta"].get("day") or "")
            for t in sr.get("trades") or []:
                yen = _float(t.get("pnl_yen_100"))
                if yen is None:
                    continue
                if not stack_blocked("A_phase355_only", t, session_kind=session_kind):
                    day_pnl_a[day] += float(yen)

        raw_stack: dict[str, dict[str, Any]] = {}

        for stack in STACK_VARIANTS:
            total_pnl = skipped_pnl = dyn_pnl = core_pnl = 0.0
            kept = skipped = stops = 0
            yens_kept: list[float] = []
            day_deltas: dict[str, float] = defaultdict(float)
            day_pnl_stack: dict[str, float] = defaultdict(float)
            c03_only_skipped = c03_only_skipped_pnl = 0

            for sr in self.session_results:
                sm = sr["session_meta"]
                session_kind = str(sr.get("session_kind") or "")
                day = str(sm.get("day_key") or sm.get("day") or "")
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
                        if stack != "A_phase355_only" and c03_blocked_only(
                            t, scope="all" if stack == "B_phase355_plus_c03_all" else "dynamic40"
                        ) and not t.get("phase355_would_block"):
                            c03_only_skipped += 1
                            c03_only_skipped_pnl += float(yen)
                        sym = str(t.get("symbol") or "")
                        symbol_skipped[(stack, sym)] += float(yen)
                    else:
                        kept += 1
                        total_pnl += float(yen)
                        sess_pnl += float(yen)
                        sess_kept += 1
                        yens_kept.append(float(yen))
                        if t.get("is_stop_hit"):
                            stops += 1
                        ug = str(t.get("universe_group") or "")
                        if ug == "dynamic40":
                            dyn_pnl += float(yen)
                            by_am_pm_stack[(session_kind, stack)]["dynamic40_pnl"] += float(yen)
                        elif ug == "core10":
                            core_pnl += float(yen)
                            by_am_pm_stack[(session_kind, stack)]["core10_pnl"] += float(yen)

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

                kind_key = (session_kind, stack)
                by_am_pm_stack[kind_key]["total_pnl_yen_100"] += sess_pnl
                by_am_pm_stack[kind_key]["session_count"] += 1

                if stack != "A_phase355_only":
                    day_deltas[day] = round(day_pnl_stack[day] - day_pnl_a.get(day, 0.0), 2)

            raw_stack[stack] = {
                "total_pnl": round(total_pnl, 2),
                "skipped_pnl": round(skipped_pnl, 2),
                "kept": kept,
                "skipped": skipped,
                "stops": stops,
                "yens_kept": yens_kept,
                "dyn_pnl": round(dyn_pnl, 2),
                "core_pnl": round(core_pnl, 2),
                "c03_only_skipped": c03_only_skipped,
                "c03_only_skipped_pnl": round(c03_only_skipped_pnl, 2),
                "day_deltas": dict(day_deltas),
                "day_pnl_stack": dict(day_pnl_stack),
                "sym_map": {sym: pnl for (s, sym), pnl in symbol_skipped.items() if s == stack},
            }

        by_stack: dict[str, dict[str, Any]] = {}
        baseline_pnl = raw_stack["A_phase355_only"]["total_pnl"]

        for stack in STACK_VARIANTS:
            raw = raw_stack[stack]
            delta_vs_a = (
                round(raw["total_pnl"] - baseline_pnl, 2) if stack != "A_phase355_only" else 0.0
            )
            conc_input_delta = delta_vs_a if stack != "A_phase355_only" else raw["total_pnl"]
            conc_day = raw["day_deltas"] if stack != "A_phase355_only" else raw["day_pnl_stack"]
            conc = _concentration(
                conc_day,
                raw["sym_map"],
                total_delta=conc_input_delta,
                total_skipped_pnl=raw["skipped_pnl"],
            )

            by_stack[stack] = {
                "stack": stack,
                "label": STACK_LABELS.get(stack, stack),
                "total_pnl_yen_100": raw["total_pnl"],
                "profit_factor": _pf(raw["yens_kept"]),
                "trade_count": raw["kept"],
                "skipped_trade_count": raw["skipped"],
                "skipped_trade_pnl_actual": raw["skipped_pnl"],
                "c03_incremental_skipped_count": raw["c03_only_skipped"]
                if stack != "A_phase355_only"
                else 0,
                "c03_incremental_skipped_pnl": raw["c03_only_skipped_pnl"]
                if stack != "A_phase355_only"
                else 0.0,
                "stop_hit_count": raw["stops"],
                "dynamic40_pnl_yen_100": raw["dyn_pnl"],
                "core10_pnl_yen_100": raw["core_pnl"],
                **conc,
            }

        baseline = by_stack["A_phase355_only"]
        for stack in ("B_phase355_plus_c03_all", "C_phase355_plus_c03_dynamic40"):
            row = by_stack[stack]
            row["delta_vs_a_yen"] = round(row["total_pnl_yen_100"] - baseline["total_pnl_yen_100"], 2)
            row["delta_pf_vs_a"] = (
                round((row["profit_factor"] or 0) - (baseline["profit_factor"] or 0), 4)
                if row["profit_factor"] is not None and baseline["profit_factor"] is not None
                else None
            )
            row["stop_hit_reduction_vs_a"] = baseline["stop_hit_count"] - row["stop_hit_count"]

        improved: dict[str, int] = defaultdict(int)
        worsened: dict[str, int] = defaultdict(int)
        for sr in self.session_results:
            sid = str(sr["session_meta"].get("session_id") or "")
            base_sess = 0.0
            for t in sr.get("trades") or []:
                yen = _float(t.get("pnl_yen_100"))
                if yen is None:
                    continue
                if not stack_blocked("A_phase355_only", t, session_kind=str(sr.get("session_kind") or "")):
                    base_sess += float(yen)
            for stack in ("B_phase355_plus_c03_all", "C_phase355_plus_c03_dynamic40"):
                stack_sess = 0.0
                for t in sr.get("trades") or []:
                    yen = _float(t.get("pnl_yen_100"))
                    if yen is None:
                        continue
                    if not stack_blocked(stack, t, session_kind=str(sr.get("session_kind") or "")):
                        stack_sess += float(yen)
                delta = round(stack_sess - base_sess, 2)
                if delta > 0:
                    improved[stack] += 1
                elif delta < 0:
                    worsened[stack] += 1

        for stack in ("B_phase355_plus_c03_all", "C_phase355_plus_c03_dynamic40"):
            by_stack[stack]["improved_session_count"] = improved[stack]
            by_stack[stack]["worsened_session_count"] = worsened[stack]

        am_612: dict[str, float] = {}
        for stack in STACK_VARIANTS:
            for sr in self.session_results:
                sm = sr["session_meta"]
                if (sm.get("day_key") == FOCUS_DAY_AM or sm.get("day") == FOCUS_DAY_AM) and sm.get(
                    "session_kind"
                ) == "am":
                    pnl = 0.0
                    for t in sr.get("trades") or []:
                        yen = _float(t.get("pnl_yen_100"))
                        if yen is None:
                            continue
                        if not stack_blocked(
                            stack, t, session_kind=str(sr.get("session_kind") or "")
                        ):
                            pnl += float(yen)
                    am_612[stack] = round(pnl, 2)

        am_612_delta_b = round(
            am_612.get("B_phase355_plus_c03_all", 0) - am_612.get("A_phase355_only", 0), 2
        )
        am_612_delta_c = round(
            am_612.get("C_phase355_plus_c03_dynamic40", 0) - am_612.get("A_phase355_only", 0), 2
        )

        def _stability_score(stack: str) -> tuple[float, float, float]:
            row = by_stack[stack]
            return (
                float(row.get("delta_vs_a_yen") or 0),
                1.0 if row.get("not_single_day_dependent") else 0.0,
                1.0 if row.get("not_single_symbol_dependent") else 0.0,
            )

        candidates = ["B_phase355_plus_c03_all", "C_phase355_plus_c03_dynamic40"]
        most_stable = max(
            candidates,
            key=lambda s: (
                _stability_score(s)[1] + _stability_score(s)[2],
                _stability_score(s)[0],
                by_stack[s].get("improved_session_count", 0)
                - by_stack[s].get("worsened_session_count", 0),
            ),
        )

        return {
            "by_stack": by_stack,
            "by_day_stack": by_day_stack,
            "by_am_pm_stack": by_am_pm_stack,
            "symbol_skipped": symbol_skipped,
            "am_20260612_pnl_by_stack": am_612,
            "am_20260612_delta_b_vs_a": am_612_delta_b,
            "am_20260612_delta_c_vs_a": am_612_delta_c,
            "most_stable_stack": most_stable,
        }

    def finalize_outputs(self, *, wall_runtime_sec: float, sessions_discovered: int) -> dict[str, Path]:
        agg = self._aggregate()
        paths = self.paths()
        by_stack = agg["by_stack"]

        variant_rows = [dict(by_stack[s]) for s in STACK_VARIANTS]
        by_day_rows = [
            {
                "day": day,
                "stack": stack,
                "total_pnl_yen_100": round(vals["total_pnl_yen_100"], 2),
                "trade_count": int(vals["trade_count"]),
                "skipped_trade_count": int(vals["skipped_trade_count"]),
                "skipped_trade_pnl_actual": round(vals["skipped_trade_pnl_actual"], 2),
                "stop_hit_count": int(vals["stop_hit_count"]),
                "session_count": int(vals["session_count"]),
            }
            for (day, stack), vals in sorted(agg["by_day_stack"].items())
        ]
        by_symbol_rows = [
            {"stack": stack, "symbol": sym, "skipped_trade_pnl_actual": round(pnl, 2)}
            for (stack, sym), pnl in sorted(agg["symbol_skipped"].items(), key=lambda x: (x[0][0], x[1]))
        ]

        most_stable = agg["most_stable_stack"]
        stable_row = by_stack[most_stable]
        b_row = by_stack["B_phase355_plus_c03_all"]
        c_row = by_stack["C_phase355_plus_c03_dynamic40"]
        a_row = by_stack["A_phase355_only"]

        add_value = (b_row.get("delta_vs_a_yen") or 0) > 0 or (c_row.get("delta_vs_a_yen") or 0) > 0

        summary = {
            "phase": 362,
            "title": "production_candidate_stack_validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "all_observer_exit_trades",
            "date_range": {"min_day": MIN_DAY, "max_day": MAX_DAY},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "stacks": STACK_LABELS,
            "by_stack": by_stack,
            "by_am_pm": {
                f"{kind}/{stack}": vals
                for (kind, stack), vals in sorted(agg["by_am_pm_stack"].items())
            },
            "am_20260612_pnl_by_stack": agg["am_20260612_pnl_by_stack"],
            "am_20260612_delta_b_vs_a": agg["am_20260612_delta_b_vs_a"],
            "am_20260612_delta_c_vs_a": agg["am_20260612_delta_c_vs_a"],
            "most_stable_stack": most_stable,
            "conclusion": {
                "phase355_baseline_pnl": a_row["total_pnl_yen_100"],
                "phase355_baseline_pf": a_row["profit_factor"],
                "add_phase361_value": add_value,
                "stack_b_delta_vs_a": b_row.get("delta_vs_a_yen"),
                "stack_c_delta_vs_a": c_row.get("delta_vs_a_yen"),
                "stack_b_stop_hit_reduction": b_row.get("stop_hit_reduction_vs_a"),
                "stack_c_stop_hit_reduction": c_row.get("stop_hit_reduction_vs_a"),
                "recommended_stack": most_stable,
                "recommended_label": STACK_LABELS.get(most_stable, ""),
                "recommended_total_pnl": stable_row["total_pnl_yen_100"],
                "recommended_pf": stable_row["profit_factor"],
                "recommendation": (
                    f"Add Phase361 C03 guard as {most_stable}; "
                    f"delta_vs_phase355={stable_row.get('delta_vs_a_yen')} yen."
                    if add_value
                    else "Do not add Phase361; no stable improvement over Phase355 alone."
                ),
            },
        }

        paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["by_variant"], variant_rows, sorted({k for r in variant_rows for k in r}))
        if by_day_rows:
            _write_csv(paths["by_day"], by_day_rows, sorted({k for r in by_day_rows for k in r}))
        if by_symbol_rows:
            _write_csv(paths["by_symbol"], by_symbol_rows, ["stack", "symbol", "skipped_trade_pnl_actual"])
        return paths
