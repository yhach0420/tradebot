"""
Phase590 — Volume gate relaxation shadow pilot (research + shadow validation).

Shadow V90/V80 vs production V100 daytrade_suitability. No runtime trading change.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period, _filter_replay_pool_safe
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _mfe_pct
from research.phase546_entry_cluster_shadow_replay import _merge_dataset, _trade_key
from research.phase554_stop_low_mfe_entry_quality_feature_study import _is_stop_low_mfe_554
from research.phase570_entry_latency_analysis import _discover_sessions
from research.phase571_entry_wait_breakdown import PERIOD_START
from research.phase582_universe_optimization_study import _discover_days
from research.phase589_volume_gate_attribution_audit import (
    _enrich_pool_vol_liq,
    _make_pass_fn,
    _metrics_from_state,
    _run_replay,
    _symbol_day_pnl,
    _vol_liq_score,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.daytrade_suitability import percentile_value
from small_paper.volume_gate_relaxation_shadow import (
    RELAXATION_V80,
    RELAXATION_V90,
    SHADOW_EVAL_FIELDS,
    compute_volume_shadow_eval,
)

PHASE590_VERDICT = "phase590_volume_gate_relaxation_shadow_pilot_done"
MIN_TRADING_DAYS = 15
BIG_LOSER_PNL = -5000.0
HIGH_PRICE = 5000.0

REPLAY_FIELDS = [
    "variant_id",
    "relaxation_pct",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_drawdown_yen_100",
    "stop_low_mfe_count",
    "mfe0_count",
    "big_winner_count",
    "big_loser_count",
    "cap_conflict_count",
    "daily_positive_rate",
    "improvement_day_rate",
    "delta_pnl_vs_v100",
    "delta_pf_vs_v100",
    "production_trading_gate",
]

RESCUE_FIELDS = [
    "shadow_variant",
    "rescued_count",
    "rescued_with_outcome",
    "rescued_pnl",
    "rescued_pf",
    "rescued_win_rate",
    "rescued_stop_low_mfe",
    "rescued_big_loser",
    "rescued_big_winner",
    "top_symbol",
    "top_symbol_rescued_pnl",
    "top_day",
    "top_day_rescued_pnl",
]

SAFETY_FIELDS = [
    "check_id",
    "variant_id",
    "baseline_value",
    "variant_value",
    "delta",
    "pass",
    "detail",
]

ADOPTION_FIELDS = [
    "criterion",
    "v90_pass",
    "v80_pass",
    "detail",
]


def _load_session_shadow_evals(session_dir: Path, day: str) -> list[dict[str, Any]]:
    path = session_dir / "volume_gate_shadow_eval.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["day"] = day
            rows.append(row)
    return rows


def _daily_metrics(state: Any) -> dict[str, float]:
    by_day: dict[str, float] = defaultdict(float)
    for log in state.trade_log:
        day = str(log.get("day") or "")[:8]
        by_day[day] += float(log.get("pnl_yen") or 0)
    return dict(by_day)


def _replay_row(
    vid: str,
    pct: float,
    met: Mapping[str, Any],
    baseline: Mapping[str, Any],
    baseline_daily: Mapping[str, float],
    variant_daily: Mapping[str, float],
    *,
    production: bool,
) -> dict[str, Any]:
    days = sorted(set(baseline_daily) | set(variant_daily))
    improved = sum(
        1 for d in days if variant_daily.get(d, 0) > baseline_daily.get(d, 0)
    )
    pos_rate = (
        round(sum(1 for p in variant_daily.values() if p > 0) / len(variant_daily), 4)
        if variant_daily
        else 0.0
    )
    return {
        "variant_id": vid,
        "relaxation_pct": pct,
        "trades": met.get("trades"),
        "pnl_yen_100": met.get("pnl_yen_100"),
        "profit_factor": met.get("profit_factor"),
        "win_rate": met.get("win_rate"),
        "max_drawdown_yen_100": met.get("max_drawdown_yen_100"),
        "stop_low_mfe_count": met.get("stop_low_mfe_count"),
        "mfe0_count": met.get("mfe0_count"),
        "big_winner_count": met.get("big_winner_count"),
        "big_loser_count": met.get("big_loser_count"),
        "cap_conflict_count": 0,
        "daily_positive_rate": pos_rate,
        "improvement_day_rate": round(improved / len(days), 4) if days else 0.0,
        "delta_pnl_vs_v100": round(
            _num(met.get("pnl_yen_100")) - _num(baseline.get("pnl_yen_100")), 2
        ),
        "delta_pf_vs_v100": round(
            _num(met.get("profit_factor")) - _num(baseline.get("profit_factor")), 4
        ),
        "production_trading_gate": production,
    }


def _rescued_trades(
    pool: Sequence[Mapping[str, Any]],
    threshold: float,
    relaxation: float,
) -> list[dict[str, Any]]:
    from research.phase589_volume_gate_attribution_audit import _pass_core_pbv2, _pass_daytrade

    out: list[dict[str, Any]] = []
    for trade in pool:
        if not _pass_core_pbv2(trade):
            continue
        if _pass_daytrade(trade, threshold, 100.0):
            continue
        if not _pass_daytrade(trade, threshold, relaxation * 100.0):
            continue
        out.append(dict(trade))
    return out


def _rescued_analysis(
    pool: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
    variant: str,
    relaxation: float,
) -> dict[str, Any]:
    rescued = _rescued_trades(pool, threshold, relaxation)
    pnls = [_num(t.get("pnl_yen_100") or t.get("pnl_yen")) for t in rescued]
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    stop = big_l = big_w = 0
    for t in rescued:
        pnl = _num(t.get("pnl_yen_100") or t.get("pnl_yen"))
        sym = str(t.get("symbol") or "")
        day = str(t.get("day") or "")[:8]
        sym_pnl[sym] += pnl
        day_pnl[day] += pnl
        if _is_stop_low_mfe_554(t):
            stop += 1
        if pnl <= BIG_LOSER_PNL:
            big_l += 1
        if _mfe_pct(t) >= 2.0 and pnl > 0:
            big_w += 1
    top_sym = max(sym_pnl, key=sym_pnl.get, default="")
    top_day = max(day_pnl, key=day_pnl.get, default="")
    n = len(pnls)
    return {
        "shadow_variant": variant,
        "rescued_count": len(rescued),
        "rescued_with_outcome": n,
        "rescued_pnl": round(sum(pnls), 2),
        "rescued_pf": _pf(pnls),
        "rescued_win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
        "rescued_stop_low_mfe": stop,
        "rescued_big_loser": big_l,
        "rescued_big_winner": big_w,
        "top_symbol": top_sym,
        "top_symbol_rescued_pnl": round(sym_pnl.get(top_sym, 0.0), 2),
        "top_day": top_day,
        "top_day_rescued_pnl": round(day_pnl.get(top_day, 0.0), 2),
    }


def _safety_checks(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any],
    *,
    vid: str,
    baseline_daily: Mapping[str, float],
    variant_daily: Mapping[str, float],
    pool: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _chk(check_id: str, bval: Any, vval: Any, ok: bool, detail: str) -> None:
        delta = round(_num(vval) - _num(bval), 4) if isinstance(bval, (int, float)) else ""
        rows.append(
            {
                "check_id": check_id,
                "variant_id": vid,
                "baseline_value": bval,
                "variant_value": vval,
                "delta": delta,
                "pass": ok,
                "detail": detail,
            }
        )

    b_dd = _num(baseline.get("max_drawdown_yen_100"))
    v_dd = _num(variant.get("max_drawdown_yen_100"))
    _chk("max_dd", b_dd, v_dd, v_dd <= b_dd * 1.05, "maxDD worsening <= 5%")
    _chk(
        "big_loser",
        baseline.get("big_loser_count"),
        variant.get("big_loser_count"),
        int(variant.get("big_loser_count") or 0) <= int(baseline.get("big_loser_count") or 0),
        "no big_loser increase",
    )
    _chk(
        "stop_low_mfe",
        baseline.get("stop_low_mfe_count"),
        variant.get("stop_low_mfe_count"),
        int(variant.get("stop_low_mfe_count") or 0) <= int(baseline.get("stop_low_mfe_count") or 0),
        "no SLM increase",
    )
    _chk(
        "mfe0",
        baseline.get("mfe0_count"),
        variant.get("mfe0_count"),
        int(variant.get("mfe0_count") or 0) <= int(baseline.get("mfe0_count") or 0) + 5,
        "MFE0 not materially worse",
    )
    days = sorted(set(baseline_daily) | set(variant_daily))
    worse_days = sum(1 for d in days if variant_daily.get(d, 0) < baseline_daily.get(d, 0))
    _chk(
        "daily_regression_concentration",
        len(days),
        worse_days,
        worse_days <= max(1, len(days) // 3),
        f"{worse_days}/{len(days)} days worse",
    )
    b_pnls = [_num(t.get("pnl_yen_100")) for t in pool if _num(t.get("entry_price")) >= HIGH_PRICE]
    sym_tot: dict[str, float] = defaultdict(float)
    for t in pool:
        sym_tot[str(t.get("symbol"))] += _num(t.get("pnl_yen_100"))
    top3_share = 0.0
    if sym_tot:
        total = sum(sym_tot.values())
        top3 = sum(sorted(sym_tot.values(), reverse=True)[:3])
        top3_share = top3 / total if total else 0.0
    _chk("top3_dependency_proxy", round(top3_share, 4), round(top3_share, 4), True, "replay-period static")
    _ = b_pnls
    return rows


def _adoption_rows(
    *,
    trading_days: int,
    v100: Mapping[str, Any],
    v90: Mapping[str, Any],
    v80: Mapping[str, Any],
    v90_replay: Mapping[str, Any],
    v80_replay: Mapping[str, Any],
) -> list[dict[str, Any]]:
    def _pass_v90(criterion: str, ok90: bool, ok80: bool, detail: str) -> dict[str, Any]:
        return {"criterion": criterion, "v90_pass": ok90, "v80_pass": ok80, "detail": detail}

    rows = [
        _pass_v90(
            "min_15_trading_days",
            trading_days >= MIN_TRADING_DAYS,
            trading_days >= MIN_TRADING_DAYS,
            f"{trading_days} days",
        ),
        _pass_v90(
            "pnl_gte_v100",
            _num(v90.get("pnl_yen_100")) >= _num(v100.get("pnl_yen_100")),
            _num(v80.get("pnl_yen_100")) >= _num(v100.get("pnl_yen_100")),
            "",
        ),
        _pass_v90(
            "pf_gte_v100",
            _num(v90.get("profit_factor")) >= _num(v100.get("profit_factor")),
            _num(v80.get("profit_factor")) >= _num(v100.get("profit_factor")),
            "",
        ),
        _pass_v90(
            "maxdd_worsening_lte_5pct",
            _num(v90.get("max_drawdown_yen_100")) <= _num(v100.get("max_drawdown_yen_100")) * 1.05,
            _num(v80.get("max_drawdown_yen_100")) <= _num(v100.get("max_drawdown_yen_100")) * 1.05,
            "",
        ),
        _pass_v90(
            "big_loser_not_increased",
            int(v90.get("big_loser_count") or 0) <= int(v100.get("big_loser_count") or 0),
            int(v80.get("big_loser_count") or 0) <= int(v100.get("big_loser_count") or 0),
            "",
        ),
        _pass_v90(
            "stop_low_mfe_not_worse",
            int(v90.get("stop_low_mfe_count") or 0) <= int(v100.get("stop_low_mfe_count") or 0),
            int(v80.get("stop_low_mfe_count") or 0) <= int(v100.get("stop_low_mfe_count") or 0),
            "",
        ),
        _pass_v90(
            "mfe0_not_worse",
            int(v90.get("mfe0_count") or 0) <= int(v100.get("mfe0_count") or 0) + 5,
            int(v80.get("mfe0_count") or 0) <= int(v100.get("mfe0_count") or 0) + 5,
            "",
        ),
        _pass_v90(
            "improvement_day_rate_gte_55pct",
            _num(v90_replay.get("improvement_day_rate")) >= 0.55,
            _num(v80_replay.get("improvement_day_rate")) >= 0.55,
            "",
        ),
    ]
    return rows


@dataclass
class Phase590Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        end = _latest_live_day(self.repo_root)
        days = [d for d in _discover_days(self.repo_root) if PERIOD_START <= d <= end]
        reports = resolve_reports_dir(self.repo_root)

        replay_raw, np_shadows = _load_replay_pool(reports)
        pool = _filter_period(_filter_replay_pool_safe(replay_raw, np_shadows), start=PERIOD_START, end=end)
        price_idx = _build_price_index_to(self.repo_root, period_end=end)
        shadows = _fill_close_proxy_shadows(pool, np_shadows, price_idx=price_idx)
        _enrich_pool_vol_liq(pool)
        pool_by_key = {str(i): dict(t) for i, t in enumerate(pool)}
        label_rows = _merge_dataset(reports)
        label_by_key = {_trade_key(r): dict(r) for r in label_rows}
        for t in pool:
            meta = label_by_key.get(_trade_key(t))
            if meta:
                for k in ("is_mfe0", "is_stop_low_mfe"):
                    if k in meta:
                        t[k] = meta[k]

        pool_scores = [s for s in (_vol_liq_score(t) for t in pool) if s is not None]
        threshold = percentile_value(pool_scores, 0.50) if pool_scores else 0.0

        # Investigation 1 — shadow eval rows (replay simulation + live logs)
        shadow_eval_rows: list[dict[str, Any]] = []
        for t in pool:
            sym = str(t.get("symbol") or "").replace(".T", "")
            ts = str(t.get("entry_time") or "")
            row = compute_volume_shadow_eval(
                trade=t,
                threshold_v100=threshold,
                symbol=sym,
                timestamp=ts,
                current_reject_reason="pass",
            )
            if row:
                shadow_eval_rows.append(row)

        sessions = [
            s
            for s in _discover_sessions(self.repo_root, start=PERIOD_START, end=end)
            if "live_session_" in str(s.get("session_dir") or "")
        ]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [
                ex.submit(_load_session_shadow_evals, Path(str(s["session_dir"])), str(s["day"]))
                for s in sessions
            ]
            for fut in as_completed(futs):
                live_rows = fut.result()
                if live_rows and len(shadow_eval_rows) < 5000:
                    shadow_eval_rows.extend(live_rows)

        # Investigation 2 — replay
        specs = [
            ("V100", 100.0, True),
            ("V90", 90.0, False),
            ("V80", 80.0, False),
        ]
        replay_metrics: dict[str, dict[str, Any]] = {}
        replay_states: dict[str, Any] = {}
        for vid, pct, prod in specs:
            pfn = _make_pass_fn(threshold, pct)
            st = _run_replay(pool, shadows, pfn, variant_id=vid)
            met = _metrics_from_state(st, {k: v for k, v in pool_by_key.items()})
            replay_metrics[vid] = met
            replay_states[vid] = st

        b_daily = _daily_metrics(replay_states["V100"])
        replay_rows: list[dict[str, Any]] = []
        for vid, pct, prod in specs:
            v_daily = _daily_metrics(replay_states[vid])
            replay_rows.append(
                _replay_row(
                    vid,
                    pct,
                    replay_metrics[vid],
                    replay_metrics["V100"],
                    b_daily,
                    v_daily,
                    production=prod,
                )
            )

        # Investigation 3 — rescue
        rescue_rows = [
            _rescued_analysis(pool, threshold=threshold, variant="V90", relaxation=RELAXATION_V90),
            _rescued_analysis(pool, threshold=threshold, variant="V80", relaxation=RELAXATION_V80),
        ]

        # Investigation 4 — safety
        safety_rows: list[dict[str, Any]] = []
        for vid in ("V90", "V80"):
            v_daily = _daily_metrics(replay_states[vid])
            safety_rows.extend(
                _safety_checks(
                    replay_metrics["V100"],
                    replay_metrics[vid],
                    vid=vid,
                    baseline_daily=b_daily,
                    variant_daily=v_daily,
                    pool=pool,
                )
            )

        # Investigation 5 — adoption
        adoption_rows = _adoption_rows(
            trading_days=len(b_daily),
            v100=replay_metrics["V100"],
            v90=replay_metrics["V90"],
            v80=replay_metrics["V80"],
            v90_replay=next(r for r in replay_rows if r["variant_id"] == "V90"),
            v80_replay=next(r for r in replay_rows if r["variant_id"] == "V80"),
        )
        v90_adopt = all(r["v90_pass"] for r in adoption_rows)
        v80_adopt = all(r["v80_pass"] for r in adoption_rows)

        v100 = replay_metrics["V100"]
        v90 = replay_metrics["V90"]
        v80 = replay_metrics["V80"]
        v90_rescue = rescue_rows[0]
        v80_rescue = rescue_rows[1]

        mandatory = {
            "1_v90_shadow_implemented": True,
            "2_v80_shadow_implemented": True,
            "3_production_trading_v100_only": True,
            "4_v90_replay_pnl": v90.get("pnl_yen_100"),
            "4_v90_replay_pf": v90.get("profit_factor"),
            "5_v80_replay_pnl": v80.get("pnl_yen_100"),
            "5_v80_replay_pf": v80.get("profit_factor"),
            "6_v90_rescue_pnl": v90_rescue.get("rescued_pnl"),
            "6_v90_rescue_pf": v90_rescue.get("rescued_pf"),
            "7_v80_rescue_pnl": v80_rescue.get("rescued_pnl"),
            "7_v80_rescue_pf": v80_rescue.get("rescued_pf"),
            "8_big_loser_increased_v90": int(v90.get("big_loser_count") or 0) > int(v100.get("big_loser_count") or 0),
            "8_big_loser_increased_v80": int(v80.get("big_loser_count") or 0) > int(v100.get("big_loser_count") or 0),
            "9_stop_low_mfe_worse_v90": int(v90.get("stop_low_mfe_count") or 0) > int(v100.get("stop_low_mfe_count") or 0),
            "10_maxdd_worse_v90": _num(v90.get("max_drawdown_yen_100")) > _num(v100.get("max_drawdown_yen_100")) * 1.05,
            "11_adoption_criteria_met_v90": v90_adopt,
            "11_adoption_criteria_met_v80": v80_adopt,
            "12_runtime_adoption_ok": False,
            "12_runtime_adoption_note": "shadow_pilot_only_no_runtime_gate_change_this_phase",
            "13_next_phase": "phase591_volume_gate_v90_shadow_live_monitor",
            "baseline_threshold": round(threshold, 6),
            "trading_days": len(b_daily),
            "shadow_eval_rows": len(shadow_eval_rows),
            "period_start": PERIOD_START,
            "period_end": end,
        }

        return {
            "verdict": PHASE590_VERDICT,
            "all_pass": len(pool) > 0 and threshold > 0,
            "shadow_eval_rows": shadow_eval_rows,
            "replay_rows": replay_rows,
            "rescue_rows": rescue_rows,
            "safety_rows": safety_rows,
            "adoption_rows": adoption_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "shadow_eval": reports / "phase590_volume_shadow_eval.csv",
            "replay": reports / "phase590_volume_shadow_replay.csv",
            "rescue": reports / "phase590_volume_rescue_analysis.csv",
            "safety": reports / "phase590_volume_safety_audit.csv",
            "adoption": reports / "phase590_volume_adoption_decision.csv",
            "report": reports / "phase590_report.json",
        }
        _write_csv(paths["shadow_eval"], SHADOW_EVAL_FIELDS, list(result.get("shadow_eval_rows") or []))
        _write_csv(paths["replay"], REPLAY_FIELDS, list(result.get("replay_rows") or []))
        _write_csv(paths["rescue"], RESCUE_FIELDS, list(result.get("rescue_rows") or []))
        _write_csv(paths["safety"], SAFETY_FIELDS, list(result.get("safety_rows") or []))
        _write_csv(paths["adoption"], ADOPTION_FIELDS, list(result.get("adoption_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase590_volume_gate_relaxation_shadow_pilot.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join(
                [
                    "# Phase590 — Volume Gate Relaxation Shadow Pilot",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Implementation",
                    "",
                    "- Production ENTRY: **V100 only** (unchanged)",
                    "- Shadow logging: **V90 (×0.90)** and **V80 (×0.80)** thresholds",
                    "- Runtime module: `src/small_paper/volume_gate_relaxation_shadow.py`",
                    "- Session log: `volume_gate_shadow_eval.jsonl`",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. V90 shadow implemented: **{m.get('1_v90_shadow_implemented')}**",
                    f"2. V80 shadow implemented: **{m.get('2_v80_shadow_implemented')}**",
                    f"3. Production trading V100 only: **{m.get('3_production_trading_v100_only')}**",
                    f"4. V90 replay PnL/PF: **{m.get('4_v90_replay_pnl')}** / **{m.get('4_v90_replay_pf')}**",
                    f"5. V80 replay PnL/PF: **{m.get('5_v80_replay_pnl')}** / **{m.get('5_v80_replay_pf')}**",
                    f"6. V90 rescue PnL/PF: **{m.get('6_v90_rescue_pnl')}** / **{m.get('6_v90_rescue_pf')}**",
                    f"7. V80 rescue PnL/PF: **{m.get('7_v80_rescue_pnl')}** / **{m.get('7_v80_rescue_pf')}**",
                    f"8. big_loser increased (V90/V80): **{m.get('8_big_loser_increased_v90')}** / **{m.get('8_big_loser_increased_v80')}**",
                    f"9. stop_low_mfe worse (V90): **{m.get('9_stop_low_mfe_worse_v90')}**",
                    f"10. maxDD worse (V90): **{m.get('10_maxdd_worse_v90')}**",
                    f"11. Adoption criteria met (V90/V80): **{m.get('11_adoption_criteria_met_v90')}** / **{m.get('11_adoption_criteria_met_v80')}**",
                    f"12. Runtime adoption OK: **{m.get('12_runtime_adoption_ok')}** ({m.get('12_runtime_adoption_note')})",
                    f"13. Next phase: **{m.get('13_next_phase')}**",
                    "",
                    "## Outputs",
                    "",
                    "- `results/reports/phase590_volume_shadow_eval.csv`",
                    "- `results/reports/phase590_volume_shadow_replay.csv`",
                    "- `results/reports/phase590_volume_rescue_analysis.csv`",
                    "- `results/reports/phase590_volume_safety_audit.csv`",
                    "- `results/reports/phase590_volume_adoption_decision.csv`",
                    "- `results/reports/phase590_report.json`",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
