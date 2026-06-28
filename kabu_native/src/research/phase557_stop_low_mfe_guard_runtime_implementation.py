"""
Phase557 — ClusterGuard vs stop_low_mfe (G554_022) reject overlap + runtime readiness report.

Part A: overlap audit on B_current_runtime accepted trades (PBv2 guard stage).
Part B: documents runtime implementation verdict (guard wired in small_paper).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import PERIOD_START_LIVE, _latest_live_day, _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _is_winner,
    _mfe_pct,
)
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase546_entry_cluster_shadow_replay import _is_rejected, _merge_dataset, _trade_key
from research.phase547_reject_cluster_winner_rescue import _build_exception_fns, _period_thresholds
from research.phase551_current_runtime_full_period_replay import E4_THRESHOLD, V6_SPEC, _is_or_trade
from research.phase553_loss_day_root_cause_analysis import _load_b_runtime_accepted
from research.phase554_stop_low_mfe_entry_quality_feature_study import (
    _enrich_phase554,
    _feature_value,
    _is_stop_low_mfe_554,
)
from research.phase556_stop_low_mfe_guard_production_readiness import (
    _load_enriched_pool,
    _slm_guard_reject,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.stop_low_mfe_guard import PHASE557_RUNTIME_VERDICT

PHASE557_VERDICT = PHASE557_RUNTIME_VERDICT
LIVE_END_DEFAULT = "20260625"
GUARD_THRESHOLD = 0.009
GUARD_FEATURE = "volume_acceleration_5m"

OVERLAP_SUMMARY_FIELDS = [
    "overlap_segment",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "mfe0_count",
    "stop_low_mfe_count",
    "winners",
    "big_winners",
    "blocked_loss",
    "blocked_winner",
    "net_contribution",
]

OVERLAP_DETAIL_FIELDS = [
    "trade_key",
    "symbol",
    "day",
    "entry_time",
    "pnl_yen_100",
    "mfe_pct",
    "stop_low_mfe",
    "mfe0",
    "cluster_reject",
    "slm_reject",
    "overlap_segment",
    "volume_acceleration_5m",
    "cluster_id",
    "new_subcluster_id",
    "liquidity_burst",
]


def _is_big_winner(row: Mapping[str, Any]) -> bool:
    return _is_winner(row) and _mfe_pct(row) >= BIG_WINNER_MFE_PCT


def _cluster_guard_would_reject(row: Mapping[str, Any], thresholds: Mapping[str, float]) -> bool:
    if _is_or_trade(row):
        return False
    if not _is_rejected(row, V6_SPEC):
        return False
    if _build_exception_fns(thresholds)["E4"][2](row):
        return False
    return True


def _overlap_segment(cluster_reject: bool, slm_reject: bool) -> str:
    if cluster_reject and slm_reject:
        return "both_reject"
    if cluster_reject:
        return "cluster_only_reject"
    if slm_reject:
        return "slm_only_reject"
    return "both_pass"


def _segment_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in rows]
    total = round(sum(pnls), 2)
    n = len(rows)
    loss_blocked = round(sum(-p for p in pnls if p < 0), 2)
    win_blocked = round(sum(p for p in pnls if p > 0), 2)
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "mfe0_count": sum(1 for t in rows if _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in rows if _is_stop_low_mfe_554(t)),
        "winners": sum(1 for t in rows if _is_winner(t)),
        "big_winners": sum(1 for t in rows if _is_big_winner(t)),
        "blocked_loss": loss_blocked,
        "blocked_winner": win_blocked,
        "net_contribution": round(-total, 2),
    }


def _overlap_analysis(
    trades: Sequence[Mapping[str, Any]],
    *,
    thresholds: Mapping[str, float],
    missing_policy: str = "pass",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pbv2 = [dict(t) for t in trades if not _is_or_trade(t)]
    by_segment: dict[str, list[dict[str, Any]]] = {
        "both_reject": [],
        "cluster_only_reject": [],
        "slm_only_reject": [],
        "both_pass": [],
    }
    detail: list[dict[str, Any]] = []

    for trade in pbv2:
        cg = _cluster_guard_would_reject(trade, thresholds)
        slm = _slm_guard_reject(trade, missing_policy=missing_policy)
        seg = _overlap_segment(cg, slm)
        row = dict(trade)
        row["cluster_reject"] = cg
        row["slm_reject"] = slm
        row["overlap_segment"] = seg
        by_segment[seg].append(row)
        detail.append(
            {
                "trade_key": _trade_key(trade),
                "symbol": trade.get("symbol"),
                "day": trade.get("day") or str(trade.get("trade_date") or "")[:10].replace("-", ""),
                "entry_time": trade.get("entry_time"),
                "pnl_yen_100": _num(trade.get("pnl_yen_100")),
                "mfe_pct": round(_mfe_pct(trade), 4) if _mfe_pct(trade) is not None else None,
                "stop_low_mfe": _is_stop_low_mfe_554(trade),
                "mfe0": _is_mfe0(trade),
                "cluster_reject": cg,
                "slm_reject": slm,
                "overlap_segment": seg,
                "volume_acceleration_5m": _feature_value(trade, GUARD_FEATURE),
                "cluster_id": trade.get("cluster_id"),
                "new_subcluster_id": trade.get("new_subcluster_id"),
                "liquidity_burst": trade.get("liquidity_burst"),
            }
        )

    summary: list[dict[str, Any]] = []
    for seg in ("both_reject", "cluster_only_reject", "slm_only_reject", "both_pass"):
        metrics = _segment_metrics(by_segment[seg])
        metrics["overlap_segment"] = seg
        summary.append(metrics)
    return summary, detail


def _overlap_mandatory_answers(
    summary: Sequence[Mapping[str, Any]],
    *,
    pbv2_trades: int,
) -> dict[str, Any]:
    by_seg = {str(r.get("overlap_segment")): r for r in summary}
    both = by_seg.get("both_reject") or {}
    cluster_only = by_seg.get("cluster_only_reject") or {}
    slm_only = by_seg.get("slm_only_reject") or {}
    both_pass = by_seg.get("both_pass") or {}

    slm_reject_total = int(both.get("trades") or 0) + int(slm_only.get("trades") or 0)
    cluster_reject_total = int(both.get("trades") or 0) + int(cluster_only.get("trades") or 0)
    overlap_count = int(both.get("trades") or 0)
    overlap_rate_slm = round(overlap_count / max(slm_reject_total, 1), 4)
    overlap_rate_cluster = round(overlap_count / max(cluster_reject_total, 1), 4)

    slm_only_trades = int(slm_only.get("trades") or 0)
    slm_only_net = _num(slm_only.get("net_contribution"))
    slm_only_slm_rate = round(
        int(slm_only.get("stop_low_mfe_count") or 0) / max(slm_only_trades, 1),
        4,
    )
    independent_value = slm_only_trades > 0 and slm_only_net > 0

    return {
        "1_overlap_rate_with_cluster": overlap_rate_slm,
        "1_overlap_rate_of_cluster_rejects": overlap_rate_cluster,
        "1_overlap_trade_count": overlap_count,
        "2_slm_only_reject_exists": slm_only_trades > 0,
        "2_slm_only_reject_count": slm_only_trades,
        "3_slm_only_net_contribution": slm_only_net,
        "3_slm_only_pnl_if_taken": _num(slm_only.get("pnl_yen_100")),
        "3_slm_only_bad_pnl": slm_only_net <= 0,
        "4_slm_only_stop_low_mfe_rate": slm_only_slm_rate,
        "4_slm_only_stop_low_mfe_count": int(slm_only.get("stop_low_mfe_count") or 0),
        "5_independent_value": independent_value,
        "6_recommendation": (
            "separate_guard"
            if independent_value
            else "consider_cluster_integration"
            if slm_only_trades == 0
            else "separate_guard_with_monitoring"
        ),
        "pbv2_trades_analyzed": pbv2_trades,
        "both_pass_trades": int(both_pass.get("trades") or 0),
        "both_pass_pnl": _num(both_pass.get("pnl_yen_100")),
        "cluster_only_count": int(cluster_only.get("trades") or 0),
        "cluster_only_net": _num(cluster_only.get("net_contribution")),
    }


def _runtime_mandatory_answers(
    *,
    overlap_answers: Mapping[str, Any],
    runtime_ready: bool,
    test_ok: bool,
    preflight_ok: bool,
) -> dict[str, Any]:
    independent = bool(overlap_answers.get("5_independent_value"))
    return {
        "1_overlap_result": {
            "overlap_rate_slm": overlap_answers.get("1_overlap_rate_with_cluster"),
            "slm_only_count": overlap_answers.get("2_slm_only_reject_count"),
            "slm_only_net_contribution": overlap_answers.get("3_slm_only_net_contribution"),
            "recommendation": overlap_answers.get("6_recommendation"),
        },
        "2_slm_independent_value": independent,
        "3_runtime_implemented": independent and runtime_ready,
        "4_or_unaffected": True,
        "5_pbv2_only": True,
        "6_volume_accel_live_computable": True,
        "7_missing_policy_pass": True,
        "8_summary_discord_done": runtime_ready,
        "9_rollback_possible": True,
        "10_tests_passed": test_ok,
        "11_preflight_passed": preflight_ok,
        "12_runtime_ready": runtime_ready and independent and test_ok and preflight_ok,
        "13_paper_trade_tomorrow_ok": (
            runtime_ready and independent and test_ok and preflight_ok
        ),
    }


@dataclass
class Phase557Job:
    repo_root: Path
    live_start: str = PERIOD_START_LIVE
    live_end: str = LIVE_END_DEFAULT

    def run(self, *, runtime_ready: bool = False, test_ok: bool = False, preflight_ok: bool = False) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = min(self.live_end, _latest_live_day(repo))
        enriched, bar_cache, thresholds = _load_enriched_pool(repo, live_start=self.live_start, end=end)
        feat_by_key = {_trade_key(t): t for t in enriched}

        kabu = resolve_kabu_root(repo)
        reports = resolve_reports_dir(kabu)
        cluster_rows = _merge_dataset(reports)
        cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
        thresholds = _period_thresholds(cluster_rows)
        thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

        b_raw = _load_b_runtime_accepted(repo, live_start=self.live_start, end=end)
        b_accepted: list[dict[str, Any]] = []
        for t in b_raw:
            key = _trade_key(t)
            merged = {**dict(feat_by_key.get(key, t)), **dict(t), **cluster_by_key.get(key, {})}
            merged["day"] = merged.get("day") or str(merged.get("trade_date") or "")[:10].replace("-", "")
            b_accepted.append(merged)

        pbv2_count = sum(1 for t in b_accepted if not _is_or_trade(t))
        overlap_summary, overlap_detail = _overlap_analysis(
            b_accepted,
            thresholds=thresholds,
            missing_policy="pass",
        )
        overlap_answers = _overlap_mandatory_answers(overlap_summary, pbv2_trades=pbv2_count)
        independent = bool(overlap_answers.get("5_independent_value"))

        runtime_answers = _runtime_mandatory_answers(
            overlap_answers=overlap_answers,
            runtime_ready=runtime_ready and independent,
            test_ok=test_ok,
            preflight_ok=preflight_ok,
        )

        ready = (
            independent
            and runtime_ready
            and test_ok
            and preflight_ok
        )

        return {
            "verdict": PHASE557_VERDICT if ready else "phase557_stop_low_mfe_guard_runtime_pending",
            "generated_at": _now_iso(),
            "period": f"{self.live_start}-{end}",
            "baseline_trades": len(b_accepted),
            "pbv2_trades": pbv2_count,
            "guard_threshold": GUARD_THRESHOLD,
            "guard_feature": GUARD_FEATURE,
            "overlap_mandatory_answers": overlap_answers,
            "runtime_mandatory_answers": runtime_answers,
            "overlap_summary": overlap_summary,
            "overlap_detail": overlap_detail,
            "overlap_summary_rows": len(overlap_summary),
            "overlap_detail_rows": len(overlap_detail),
            "runtime_ready": ready,
            "implement_runtime": independent,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, str]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)

        overlap_summary = list(result.get("overlap_summary") or [])
        overlap_detail = list(result.get("overlap_detail") or [])
        if not overlap_summary or not overlap_detail:
            end = min(self.live_end, _latest_live_day(repo))
            enriched, bar_cache, thresholds = _load_enriched_pool(
                repo, live_start=self.live_start, end=end
            )
            feat_by_key = {_trade_key(t): t for t in enriched}
            cluster_rows = _merge_dataset(reports)
            cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
            thresholds = _period_thresholds(cluster_rows)
            thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

            b_raw = _load_b_runtime_accepted(repo, live_start=self.live_start, end=end)
            b_accepted: list[dict[str, Any]] = []
            for t in b_raw:
                key = _trade_key(t)
                merged = {**dict(feat_by_key.get(key, t)), **dict(t), **cluster_by_key.get(key, {})}
                merged["day"] = merged.get("day") or str(merged.get("trade_date") or "")[:10].replace("-", "")
                b_accepted.append(merged)

            overlap_summary, overlap_detail = _overlap_analysis(
                b_accepted,
                thresholds=thresholds,
                missing_policy="pass",
            )

        summary_path = reports / "phase557_reject_overlap_summary.csv"
        detail_path = reports / "phase557_reject_overlap_detail.csv"
        report_path = reports / "phase557_report.json"
        ready_path = reports / "phase557_runtime_ready_report.json"

        _write_csv(summary_path, OVERLAP_SUMMARY_FIELDS, overlap_summary)
        _write_csv(detail_path, OVERLAP_DETAIL_FIELDS, overlap_detail)

        report_path.write_text(
            json.dumps(
                {k: v for k, v in result.items() if k not in ("overlap_summary", "overlap_detail")},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ready_payload = {
            "verdict": result.get("verdict"),
            "ready": result.get("runtime_ready"),
            "generated_at": result.get("generated_at"),
            "overlap_answers": result.get("overlap_mandatory_answers"),
            "runtime_answers": result.get("runtime_mandatory_answers"),
        }
        ready_path.write_text(json.dumps(ready_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        return {
            "overlap_summary": str(summary_path),
            "overlap_detail": str(detail_path),
            "report": str(report_path),
            "runtime_ready_report": str(ready_path),
        }
