"""
Phase480 — PBv2 Loss Cluster Audit + Trend Shadow (research only).

Part A: bottom-20% PBv2 loser cluster audit
Part B: 4062 / 6920 win vs lose feature separation
Part C: Trend shadow (PB5 + Session Hold) record-only
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase404_no_progress_exit_shadow import build_tick_states
from research.phase443_full_runtime_combined_capital_sim import (
    simulate_capacity_replay,
    _stop_rate_from_log,
)
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log as _chron_pnls,
    _now_iso,
    _optional_float,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token
from research.phase463_trend_pullback_population_tournament import (
    _board_bucket,
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _momentum_score,
)
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio, _vwap_dev
from research.phase465b_trend_gate_redesign import _day_high_distance
from research.phase467_trend_exit_audit import _prepare_forward_context_price_idx
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import (
    _ensure_enriched,
    _fill_counterfactual_gaps,
    _gate_pb5,
    _load_replay_pool,
    _make_pb_entry,
    _precompute_exit_shadows_subset,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PB_PASS = _make_pb_entry(_gate_pb5)
EXIT_ID = "C"
FOCUS_SYMBOLS = ("4062", "6920")
TREND_SYMBOLS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")

CLUSTER_FIELDS = [
    "rank",
    "cluster_id",
    "cluster_label",
    "trade_count",
    "share_of_bottom20",
    "total_pnl_yen",
    "avg_pnl_yen",
    "avg_hold_sec",
    "avg_mfe_pct",
    "avg_mae_pct",
    "dominant_exit_reason",
    "top_symbols",
    "avg_momentum_score",
    "avg_r10",
    "avg_day_high_distance",
    "avg_vwap_dev",
]

SYMBOL_FIELDS = [
    "symbol",
    "side",
    "trade_count",
    "total_pnl_yen",
    "avg_pnl_yen",
    "avg_hold_sec",
    "avg_mfe_pct",
    "avg_mae_pct",
    "avg_momentum_score",
    "avg_r10",
    "avg_day_high_distance",
    "avg_vwap_dev",
    "avg_vwap_above_ratio",
    "board_bucket_mode",
    "exit_reason_mode",
    "separation_feature",
    "separation_delta",
    "improvement_hint",
]

TREND_SHADOW_FIELDS = [
    "symbol",
    "candidate_count",
    "accepted_count",
    "total_pnl_yen",
    "avg_pnl_yen",
    "profit_factor",
    "share_of_total_pnl",
]

COMPARE_FEATURES = (
    "momentum_score",
    "r10",
    "day_high_distance",
    "vwap_dev",
    "vwap_above_ratio",
    "hold_sec",
    "mfe_pct",
    "mae_pct",
    "board_bucket",
    "exit_reason",
)


def _r10(trade: Mapping[str, Any]) -> Optional[float]:
    return _optional_float(trade.get("return_10min_pct")) or _optional_float(trade.get("entry_rise_10min_pct"))


def _mfe_mae_to_exit(
    trade: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list],
    exit_ts_iso: str,
) -> tuple[Optional[float], Optional[float]]:
    ctx = _prepare_forward_context_price_idx(trade, price_idx=price_idx)
    if ctx is None:
        return None, None
    exit_ts = _parse_ts(exit_ts_iso)
    if exit_ts is None:
        return None, None
    cut = exit_ts.timestamp()
    states = ctx.get("tick_states") or []
    pnls = [float(s.get("pnl") or 0) for s in states if float(s.get("ts") or 0) <= cut + 1.0]
    if not pnls:
        return None, None
    mfe = max(pnls)
    mae = min(pnls)
    return round(mfe, 4), round(mae, 4)


def _trade_row(
    log_row: Mapping[str, Any],
    trade: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list],
) -> dict[str, Any]:
    exit_ts = str(log_row.get("exit_time") or "")
    mfe, mae = _mfe_mae_to_exit(trade, price_idx=price_idx, exit_ts_iso=exit_ts)
    sym = str(trade.get("symbol") or "").replace(".T", "")
    return {
        "position_key": _position_key(trade),
        "symbol": sym,
        "day": str(trade.get("day") or "")[:8],
        "entry_time": trade.get("entry_time"),
        "exit_time": exit_ts,
        "pnl_yen": float(log_row.get("pnl_yen") or 0),
        "exit_reason": normalize_exit_reason(str(log_row.get("exit_reason") or "")),
        "hold_sec": float(log_row.get("hold_sec") or 0),
        "mfe_pct": mfe,
        "mae_pct": mae,
        "board_bucket": _board_bucket(trade),
        "momentum_score": _momentum_score(trade),
        "r10": _r10(trade),
        "day_high_distance": _day_high_distance(trade),
        "vwap_dev": _vwap_dev(trade),
        "vwap_above_ratio": _vwap_above_ratio(trade),
    }


def _assign_cluster(row: Mapping[str, Any]) -> tuple[str, str]:
    er = str(row.get("exit_reason") or "")
    mfe = float(row.get("mfe_pct") or 0)
    hold = float(row.get("hold_sec") or 0)
    bb = str(row.get("board_bucket") or "unknown")
    mom = float(row.get("momentum_score") or 0)
    dhd = float(row.get("day_high_distance") or 0)

    if er == "stop_hit" and mfe < 0.5:
        return "A", "stop_low_mfe"
    if er == "stop_hit" and mfe >= 0.5:
        return "B", "stop_gave_back"
    if hold >= 7200:
        return "C", "long_hold_bleed"
    if bb == "low":
        return "D", "board_low"
    if dhd >= 5.0:
        return "E", "far_from_day_high"
    if mom < 0.35:
        return "F", "low_momentum_entry"
    if er in ("no_progress_exit", "other"):
        return "G", "no_progress_or_other"
    return "H", "other_loss"


def _cluster_ranking(bottom20: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in bottom20:
        cid, _ = _assign_cluster(row)
        buckets[cid].append(row)

    label_map = {_assign_cluster(r)[0]: _assign_cluster(r)[1] for r in bottom20}
    rows: list[dict[str, Any]] = []
    n = len(bottom20)
    for cid, items in buckets.items():
        pnls = [float(r.get("pnl_yen") or 0) for r in items]
        syms = Counter(str(r.get("symbol") or "") for r in items)
        exits = Counter(str(r.get("exit_reason") or "") for r in items)
        mfes = [float(r.get("mfe_pct") or 0) for r in items if r.get("mfe_pct") is not None]
        maes = [float(r.get("mae_pct") or 0) for r in items if r.get("mae_pct") is not None]
        moms = [float(r.get("momentum_score") or 0) for r in items if r.get("momentum_score") is not None]
        r10s = [float(r.get("r10") or 0) for r in items if r.get("r10") is not None]
        dhds = [float(r.get("day_high_distance") or 0) for r in items]
        vdevs = [float(r.get("vwap_dev") or 0) for r in items if r.get("vwap_dev") is not None]
        rows.append(
            {
                "cluster_id": cid,
                "cluster_label": label_map.get(cid, cid),
                "trade_count": len(items),
                "share_of_bottom20": round(len(items) / n, 4) if n else 0.0,
                "total_pnl_yen": round(sum(pnls), 2),
                "avg_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
                "avg_hold_sec": round(statistics.mean([float(r.get("hold_sec") or 0) for r in items]), 2),
                "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
                "avg_mae_pct": round(statistics.mean(maes), 4) if maes else None,
                "dominant_exit_reason": exits.most_common(1)[0][0] if exits else "",
                "top_symbols": ",".join(s for s, _ in syms.most_common(3)),
                "avg_momentum_score": round(statistics.mean(moms), 4) if moms else None,
                "avg_r10": round(statistics.mean(r10s), 4) if r10s else None,
                "avg_day_high_distance": round(statistics.mean(dhds), 4) if dhds else None,
                "avg_vwap_dev": round(statistics.mean(vdevs), 4) if vdevs else None,
            }
        )
    rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _numeric_mean(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    vals = [float(r.get(key)) for r in rows if r.get(key) is not None]
    return statistics.mean(vals) if vals else None


def _symbol_win_lose_analysis(
    all_rows: Sequence[Mapping[str, Any]],
    symbol: str,
) -> list[dict[str, Any]]:
    sym_rows = [r for r in all_rows if str(r.get("symbol") or "") == symbol]
    wins = [r for r in sym_rows if float(r.get("pnl_yen") or 0) > 0]
    loses = [r for r in sym_rows if float(r.get("pnl_yen") or 0) < 0]
    out: list[dict[str, Any]] = []

    best_feat = ""
    best_delta = 0.0
    best_hint = ""

    for side, bucket in (("win", wins), ("lose", loses)):
        bb = Counter(str(r.get("board_bucket") or "") for r in bucket)
        er = Counter(str(r.get("exit_reason") or "") for r in bucket)
        out.append(
            {
                "symbol": symbol,
                "side": side,
                "trade_count": len(bucket),
                "total_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in bucket), 2),
                "avg_pnl_yen": round(statistics.mean([float(r.get("pnl_yen") or 0) for r in bucket]), 2)
                if bucket
                else 0.0,
                "avg_hold_sec": round(statistics.mean([float(r.get("hold_sec") or 0) for r in bucket]), 2)
                if bucket
                else 0.0,
                "avg_mfe_pct": _numeric_mean(bucket, "mfe_pct"),
                "avg_mae_pct": _numeric_mean(bucket, "mae_pct"),
                "avg_momentum_score": _numeric_mean(bucket, "momentum_score"),
                "avg_r10": _numeric_mean(bucket, "r10"),
                "avg_day_high_distance": _numeric_mean(bucket, "day_high_distance"),
                "avg_vwap_dev": _numeric_mean(bucket, "vwap_dev"),
                "avg_vwap_above_ratio": _numeric_mean(bucket, "vwap_above_ratio"),
                "board_bucket_mode": bb.most_common(1)[0][0] if bb else "",
                "exit_reason_mode": er.most_common(1)[0][0] if er else "",
                "separation_feature": "",
                "separation_delta": None,
                "improvement_hint": "",
            }
        )

    numeric_feats = ("momentum_score", "r10", "day_high_distance", "vwap_dev", "vwap_above_ratio", "hold_sec", "mfe_pct")
    for feat in numeric_feats:
        wm = _numeric_mean(wins, feat)
        lm = _numeric_mean(loses, feat)
        if wm is None or lm is None:
            continue
        delta = abs(wm - lm)
        if delta > best_delta:
            best_delta = delta
            best_feat = feat
            if feat == "momentum_score":
                best_hint = f"raise momentum floor above {min(wm, lm):.3f}" if lm < wm else f"review high-momentum {symbol} entries"
            elif feat == "day_high_distance":
                best_hint = f"tighten day_high_distance (< {min(wm, lm):.2f})" if lm > wm else f"near-high entries underperform"
            elif feat == "hold_sec":
                best_hint = "time-based exit for long losers" if lm > wm else "short-hold stops dominate"
            elif feat == "mfe_pct":
                best_hint = "low-MFE stop cluster — earlier exit or entry filter"
            else:
                best_hint = f"separate on {feat}: win={wm:.3f} lose={lm:.3f}"

    for row in out:
        row["separation_feature"] = best_feat
        row["separation_delta"] = round(best_delta, 4) if best_feat else None
        row["improvement_hint"] = best_hint

    return out


def _trend_shadow_by_symbol(
    st: Any,
    *,
    replay_pool: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates = [t for t in replay_pool if PB_PASS(t) and not pass_pbv2(t)]
    cand_by_sym = Counter(str(t.get("symbol") or "").replace(".T", "") for t in candidates)
    total_pnl = sum(_chron_pnls(st.trade_log))
    rows: list[dict[str, Any]] = []
    for sym in TREND_SYMBOLS:
        sym_logs = [
            r
            for r in st.trade_log
            if str(r.get("symbol") or "").replace(".T", "") == sym
        ]
        pnls = [float(r.get("pnl_yen") or 0) for r in sym_logs]
        sym_pnl = sum(pnls)
        rows.append(
            {
                "symbol": sym,
                "candidate_count": cand_by_sym.get(sym, 0),
                "accepted_count": len(sym_logs),
                "total_pnl_yen": round(sym_pnl, 2),
                "avg_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
                "profit_factor": _pf(pnls) if pnls else None,
                "share_of_total_pnl": round(sym_pnl / total_pnl, 4) if abs(total_pnl) > 1e-9 else 0.0,
            }
        )
    return rows


def _verdict(
    *,
    clusters: Sequence[Mapping[str, Any]],
    bottom20: Sequence[Mapping[str, Any]],
    sym4062: Sequence[Mapping[str, Any]],
    sym6920: Sequence[Mapping[str, Any]],
) -> str:
    if not clusters or not bottom20:
        return "no_actionable_loss_pattern"
    top = clusters[0]
    share = float(top.get("share_of_bottom20") or 0)
    label = str(top.get("cluster_label") or "")
    if share >= 0.25 and label in ("stop_low_mfe", "board_low", "low_momentum_entry", "long_hold_bleed"):
        return "pbv2_loss_reduction_candidate"
    for sym_rows in (sym4062, sym6920):
        lose = next((r for r in sym_rows if r.get("side") == "lose"), None)
        if lose and lose.get("separation_feature") and float(lose.get("separation_delta") or 0) > 0.15:
            return "pbv2_loss_reduction_candidate"
    return "no_actionable_loss_pattern"


def run_phase480(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    st_pbv2 = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase480_pbv2",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )

    trade_by_key = {_position_key(t): t for t in replay_pool}
    all_rows: list[dict[str, Any]] = []
    for log_row in st_pbv2.trade_log:
        tr = log_row.get("trade") or log_row
        key = _position_key(tr)
        src = trade_by_key.get(key) or tr
        all_rows.append(_trade_row(log_row, src, price_idx=price_idx))

    sorted_rows = sorted(all_rows, key=lambda r: float(r.get("pnl_yen") or 0))
    n_bottom = max(1, int(math.ceil(len(sorted_rows) * 0.20)))
    bottom20 = sorted_rows[:n_bottom]
    cluster_rows = _cluster_ranking(bottom20)

    sym4062 = _symbol_win_lose_analysis(all_rows, "4062")
    sym6920 = _symbol_win_lose_analysis(all_rows, "6920")
    symbol_analysis = sym4062 + sym6920

    pb_union = [t for t in replay_pool if PB_PASS(t)]
    exit_c = _precompute_exit_shadows_subset(pb_union, exit_id=EXIT_ID, price_idx=price_idx)
    exit_c = _fill_counterfactual_gaps(replay_pool, exit_c, price_idx=price_idx, entry_fn=PB_PASS)
    st_trend = simulate_capacity_replay(
        replay_pool,
        exit_c,
        mode="phase480_trend_shadow",
        entry_block_fn=_entry_block(PB_PASS),
        baseline_accepted_keys=set(),
    )
    trend_rows = _trend_shadow_by_symbol(st_trend, replay_pool=replay_pool)
    trend_chron = _chron_pnls(st_trend.trade_log)
    trend_pnl = round(sum(trend_chron), 2)
    trend_pf = _pf(trend_chron)
    sym6976_pnl = sum(
        float(r.get("pnl_yen") or 0)
        for r in st_trend.trade_log
        if str(r.get("symbol") or "").replace(".T", "") == "6976"
    )
    gross_pnl = sum(abs(float(r.get("pnl_yen") or 0)) for r in st_trend.trade_log)
    dep6976 = abs(sym6976_pnl) / gross_pnl if gross_pnl > 0 else 0.0

    baseline_pnl = round(sum(_chron_pnls(st_pbv2.trade_log)), 2)
    top_cluster = cluster_rows[0] if cluster_rows else {}
    verdict = _verdict(
        clusters=cluster_rows,
        bottom20=bottom20,
        sym4062=sym4062,
        sym6920=sym6920,
    )

    hint4062 = next((r.get("improvement_hint") for r in sym4062 if r.get("side") == "lose"), "")
    hint6920 = next((r.get("improvement_hint") for r in sym6920 if r.get("side") == "lose"), "")

    runtime_candidate = False
    shadow_candidate = "shadow_trend_candidate" if trend_pnl > 0 else None

    mandatory = {
        "1_largest_loss_cluster": f"{top_cluster.get('cluster_id')} {top_cluster.get('cluster_label')}",
        "2_best_improvement_feature": top_cluster.get("dominant_exit_reason")
        if top_cluster.get("cluster_label", "").startswith("stop")
        else top_cluster.get("cluster_label"),
        "3_4062_improvement_candidate": hint4062 or sym4062[1].get("separation_feature") if len(sym4062) > 1 else "",
        "4_6920_improvement_candidate": hint6920 or (
            "no PBv2 trades in period" if not any(r.get("trade_count") for r in sym6920) else ""
        ),
        "5_trend_shadow_pnl": trend_pnl,
        "6_trend_shadow_pf": trend_pf,
        "7_6976_dependency_rate": round(dep6976, 4),
        "8_runtime_candidate": runtime_candidate,
        "9_shadow_continue_candidate": shadow_candidate,
        "10_next_actions": _next_actions(verdict, top_cluster, hint4062, hint6920),
        "verdict": verdict,
        "pbv2_baseline_pnl": baseline_pnl,
        "pbv2_accepted": st_pbv2.accepted_trade_count,
        "bottom20_count": len(bottom20),
        "trend_shadow_accepted": st_trend.accepted_trade_count,
        "trend_shadow_candidates": len(pb_union),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_cluster_rows": cluster_rows,
        "_symbol_rows": symbol_analysis,
        "_trend_rows": trend_rows,
        "_bottom20_detail": bottom20,
    }


def _next_actions(
    verdict: str,
    top_cluster: Mapping[str, Any],
    hint4062: str,
    hint6920: str,
) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "pbv2_loss_reduction_candidate":
        actions.append(f"Target cluster {top_cluster.get('cluster_label')} ({top_cluster.get('share_of_bottom20')} of bottom20)")
        if hint4062:
            actions.append(f"4062: {hint4062}")
        if hint6920:
            actions.append(f"6920: {hint6920}")
    else:
        actions.append("No single dominant actionable loss cluster; keep PBv2-only runtime")
    actions.append("Trend shadow: record-only (PB5 Session Hold); no runtime adoption")
    return actions


@dataclass
class Phase480Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase480(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "cluster": reports / "phase480_pbv2_loss_cluster_audit.csv",
            "symbol": reports / "phase480_symbol_loss_analysis.csv",
            "trend": reports / "phase480_trend_shadow_summary.csv",
            "summary": reports / "phase480_summary.json",
        }
        _write_csv(paths["cluster"], CLUSTER_FIELDS, list(result.get("_cluster_rows") or []))
        _write_csv(paths["symbol"], SYMBOL_FIELDS, list(result.get("_symbol_rows") or []))
        _write_csv(paths["trend"], TREND_SHADOW_FIELDS, list(result.get("_trend_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase480_pbv2_loss_cluster_audit.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        clusters = list(result.get("_cluster_rows") or [])
        lines = [
            "# Phase480 — PBv2 Loss Cluster Audit + Trend Shadow",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最大損失クラスター | **{m.get('1_largest_loss_cluster')}** |",
            f"| 2 | 改善余地特徴量 | **{m.get('2_best_improvement_feature')}** |",
            f"| 3 | 4062改善候補 | **{m.get('3_4062_improvement_candidate')}** |",
            f"| 4 | 6920改善候補 | **{m.get('4_6920_improvement_candidate')}** |",
            f"| 5 | Trend shadow PnL | **{m.get('5_trend_shadow_pnl')}** |",
            f"| 6 | Trend shadow PF | **{m.get('6_trend_shadow_pf')}** |",
            f"| 7 | 6976依存率 | **{m.get('7_6976_dependency_rate')}** |",
            f"| 8 | Runtime候補 | **{m.get('8_runtime_candidate')}** |",
            f"| 9 | Shadow継続 | **{m.get('9_shadow_continue_candidate')}** |",
            f"| 10 | 次アクション | {'; '.join(m.get('10_next_actions') or [])} |",
            "",
            "## Loss cluster ranking",
            "",
        ]
        for c in clusters:
            lines.append(
                f"- **{c.get('rank')}. {c.get('cluster_label')}**: {c.get('trade_count')} trades, "
                f"PnL {c.get('total_pnl_yen')}, share {c.get('share_of_bottom20')}"
            )
        lines.extend(["", f"**判定:** `{result.get('verdict')}`"])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
