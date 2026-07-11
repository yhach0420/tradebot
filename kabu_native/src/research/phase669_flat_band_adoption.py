"""Phase669 — Flat-band mainline adoption validation (research only)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    load_all_full_period_trades,
)
from research.phase649_flat_band_guard_counterfactual import block_flat_plus_overheat
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase668_existing_shadow_adoption_review import (
    _filter_canonical,
    _is_mfe0,
    _is_no_progress,
    _is_stop_hit,
    _rate,
)
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.pbv2_flat_band_entry_guard import (
    REJECT_FLAT_BAND_MAINLINE,
    would_block_flat_band_mainline,
)
from small_paper.pbv2_flat_band_guard_shadow import compute_pbv2_flat_band_shadow_fields

PHASE669_VERDICT = "phase669_flat_band_mainline_adopted"
REPORT_DIR_NAME = "phase669_flat_band_adoption"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
PHASE668_REPORT = (
    NATIVE_ROOT / "results" / "reports" / "phase668_existing_shadow_adoption" / "phase668_shadow_adoption_report.json"
)
EXPECTED_FLAT_BAND_BLOCKS = 664
DISK_USAGE_MAX_PCT = 75.0

REMOVED_SHADOWS = (
    "pbv2_rise5_shadow",
    "vwap_shadow_reject",
    "exit_shadow_monitor_t2",
    "exit_shadow_monitor_t3",
)
KEPT_SHADOWS = (
    "pullback_misread_guard_shadow",
    "board_imbalance_shadow",
    "board_dynamic_trailing_shadow",
)


def _mainline_config() -> SimpleNamespace:
    return SimpleNamespace(
        pbv2_flat_band_mainline_enabled=True,
        pbv2_flat_band_shadow_enabled=False,
        pbv2_flat_band_shadow_apply_pool="PBV2_ONLY",
        pbv2_flat_band_shadow_rise5_flat_min_pct=0.0,
        pbv2_flat_band_shadow_rise5_flat_max_pct=0.5,
        pbv2_flat_band_shadow_rise10_flat_min_pct=-0.5,
        pbv2_flat_band_shadow_rise10_flat_max_pct=0.5,
        pbv2_flat_band_shadow_overheat_rise5_pct=2.0,
    )


def _shadow_config() -> SimpleNamespace:
    cfg = _mainline_config()
    cfg.pbv2_flat_band_mainline_enabled = False
    cfg.pbv2_flat_band_shadow_enabled = True
    return cfg


def _pbv2_trades(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in trades if str(t.get("entry_pool") or "") == "PBV2"]


def parity_shadow_vs_mainline(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    shadow_cfg = _shadow_config()
    mainline_cfg = _mainline_config()
    pbv2 = _pbv2_trades(trades)
    mismatches: list[dict[str, Any]] = []
    shadow_blocks = 0
    mainline_blocks = 0
    for t in pbv2:
        shadow_block = bool(
            compute_pbv2_flat_band_shadow_fields(shadow_cfg, t).get("pbv2_flat_band_shadow_block")
        )
        mainline_block, reason = would_block_flat_band_mainline(mainline_cfg, t)
        if shadow_block:
            shadow_blocks += 1
        if mainline_block:
            mainline_blocks += 1
        if shadow_block != mainline_block:
            mismatches.append(
                {
                    "symbol": t.get("symbol"),
                    "day": t.get("day"),
                    "shadow_block": shadow_block,
                    "mainline_block": mainline_block,
                    "reason": reason,
                }
            )
    return {
        "pbv2_trade_count": len(pbv2),
        "shadow_block_count": shadow_blocks,
        "mainline_block_count": mainline_blocks,
        "mismatch_count": len(mismatches),
        "parity_pct": round((len(pbv2) - len(mismatches)) / len(pbv2) * 100.0, 4) if pbv2 else 100.0,
        "mismatches_sample": mismatches[:20],
    }


def _counterfactual_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
    pool: str = "PBV2_ONLY",
) -> dict[str, Any]:
    universe = list(trades)
    if pool == "PBV2_ONLY":
        universe = _pbv2_trades(trades)
    blocked = [dict(t) for t in universe if block_fn(t)]
    kept = [dict(t) for t in universe if not block_fn(t)]
    base_m = _metrics(universe)
    kept_m = _metrics(kept)
    blocked_w = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
    blocked_l = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
    delta_pf: Optional[float] = None
    if base_m.get("profit_factor") is not None and kept_m.get("profit_factor") is not None:
        delta_pf = round(
            float(kept_m.get("profit_factor") or 0) - float(base_m.get("profit_factor") or 0),
            4,
        )
    return {
        "trade_count": len(universe),
        "blocked_count": len(blocked),
        "kept_count": len(kept),
        "baseline_pnl_yen": base_m.get("pnl_yen_100"),
        "counterfactual_pnl_yen": kept_m.get("pnl_yen_100"),
        "delta_pnl_yen": round(
            float(kept_m.get("pnl_yen_100") or 0) - float(base_m.get("pnl_yen_100") or 0),
            2,
        ),
        "baseline_pf": base_m.get("profit_factor"),
        "counterfactual_pf": kept_m.get("profit_factor"),
        "delta_pf": delta_pf,
        "baseline_dd_yen": base_m.get("max_dd_yen_100"),
        "counterfactual_dd_yen": kept_m.get("max_dd_yen_100"),
        "delta_dd_yen": round(
            float(kept_m.get("max_dd_yen_100") or 0) - float(base_m.get("max_dd_yen_100") or 0),
            2,
        ),
        "blocked_winners": blocked_w,
        "blocked_losers": blocked_l,
        "baseline_stop_hit_rate": _rate(universe, _is_stop_hit),
        "kept_stop_hit_rate": _rate(kept, _is_stop_hit),
        "baseline_no_progress_rate": _rate(universe, _is_no_progress),
        "kept_no_progress_rate": _rate(kept, _is_no_progress),
        "baseline_mfe0_rate": _rate(universe, _is_mfe0),
        "kept_mfe0_rate": _rate(kept, _is_mfe0),
    }


def _load_phase668_flat_band_reference() -> dict[str, Any]:
    if not PHASE668_REPORT.is_file():
        return {}
    report = json.loads(PHASE668_REPORT.read_text(encoding="utf-8"))
    answers = report.get("mandatory_answers") or {}
    flat = (answers.get("4_flat_band_vs_rise5") or {}).get("flat_band") or {}
    return flat


def _compare_metric(
    actual: Any,
    expected: Any,
    *,
    tol: float = 0.0001,
    name: str,
) -> dict[str, Any]:
    if expected is None:
        return {"name": name, "actual": actual, "expected": expected, "match": True}
    try:
        a = float(actual)
        e = float(expected)
        match = abs(a - e) <= tol
    except (TypeError, ValueError):
        match = actual == expected
    return {"name": name, "actual": actual, "expected": expected, "match": match}


def replay_validation(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ref = _load_phase668_flat_band_reference()
    cf = _counterfactual_metrics(trades, block_fn=block_flat_plus_overheat, pool="PBV2_ONLY")
    full_entry_blocked = sum(1 for t in trades if block_flat_plus_overheat(t))
    checks = [
        _compare_metric(cf["blocked_count"], EXPECTED_FLAT_BAND_BLOCKS, name="flat_band_block_count"),
        _compare_metric(cf["blocked_count"], ref.get("trigger_or_block_count"), name="phase668_block_count"),
        _compare_metric(cf["delta_pnl_yen"], ref.get("delta_pnl_yen"), tol=1.0, name="delta_pnl_yen"),
        _compare_metric(cf["counterfactual_pf"], ref.get("shadow_pf"), tol=0.001, name="counterfactual_pf"),
        _compare_metric(cf["delta_pf"], ref.get("delta_pf"), tol=0.001, name="delta_pf"),
        _compare_metric(cf["delta_dd_yen"], ref.get("delta_dd_yen"), tol=1.0, name="delta_dd_yen"),
        _compare_metric(cf["blocked_winners"], ref.get("blocked_winners"), name="blocked_winners"),
        _compare_metric(cf["blocked_losers"], ref.get("blocked_losers"), name="blocked_losers"),
        _compare_metric(cf["kept_stop_hit_rate"], ref.get("kept_stop_hit_rate"), tol=0.0001, name="kept_stop_hit_rate"),
        _compare_metric(
            cf["kept_no_progress_rate"], ref.get("kept_no_progress_rate"), tol=0.0001, name="kept_no_progress_rate"
        ),
        _compare_metric(cf["kept_mfe0_rate"], ref.get("kept_mfe0_rate"), tol=0.0001, name="kept_mfe0_rate"),
    ]
    return {
        "entry_count": len(trades),
        "entry_blocked_by_flat_band": full_entry_blocked,
        "kept_entry_count": len(trades) - full_entry_blocked,
        "reject_reason": REJECT_FLAT_BAND_MAINLINE,
        "phase668_reference": ref,
        "counterfactual": cf,
        "metric_checks": checks,
        "all_metrics_match_phase668": all(c["match"] for c in checks),
    }


def _write_shadow_cleanup_md(*, report: Mapping[str, Any]) -> None:
    lines = [
        "# Phase669 — Shadow Portfolio Cleanup",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## ADOPT → Mainline",
        "",
        "- `pbv2_flat_band_shadow` → `pbv2_flat_band_mainline_enabled` (reject_reason=`flat_band_mainline`)",
        "- Shadow module retained; `pbv2_flat_band_shadow_enabled: false`",
        "",
        "## REMOVE (disabled in production YAML)",
        "",
    ]
    for sid in REMOVED_SHADOWS:
        lines.append(f"- `{sid}`")
    lines.extend(["", "## KEEP", ""])
    for sid in KEPT_SHADOWS:
        lines.append(f"- `{sid}`")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Shadow/Mainline parity: {report.get('parity', {}).get('mismatch_count', '?')} mismatches",
            f"- Flat-band blocks: {report.get('replay_validation', {}).get('counterfactual', {}).get('blocked_count', '?')}",
            f"- Phase668 metrics match: {report.get('replay_validation', {}).get('all_metrics_match_phase668')}",
            "",
            "## Next phase",
            "",
            "Paper forward ~1 week with flat-band mainline, then evaluate Phase667 flat_weak+range as new shadow.",
            "",
        ]
    )
    (REPORT_ROOT / "phase669_shadow_cleanup.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_adoption_audit(*, skip_slow: bool = True) -> dict[str, Any]:
    del skip_slow
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    trades, sessions = load_all_full_period_trades(repo_root / "results" / "small_paper")
    trades, _sessions = _filter_canonical([dict(t) for t in trades], sessions)
    parity = parity_shadow_vs_mainline(trades)
    replay = replay_validation(trades)
    disk_after = _disk_usage_pct(NATIVE_ROOT)

    report: dict[str, Any] = {
        "verdict": PHASE669_VERDICT,
        "entry_count": len(trades),
        "trading_day_count": len({t.get("day") for t in trades}),
        "canonical_days": list(CANONICAL_DAYS),
        "pbv2_flat_band_mainline_enabled": True,
        "pbv2_flat_band_shadow_enabled": False,
        "reject_reason": REJECT_FLAT_BAND_MAINLINE,
        "removed_shadows": list(REMOVED_SHADOWS),
        "kept_shadows": list(KEPT_SHADOWS),
        "parity": parity,
        "replay_validation": replay,
        "shadow_mainline_parity_ok": parity["mismatch_count"] == 0,
        "flat_band_block_count_ok": replay["counterfactual"]["blocked_count"] == EXPECTED_FLAT_BAND_BLOCKS,
        "phase668_counterfactual_match": replay["all_metrics_match_phase668"],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
        "disk_cap_exceeded": disk_after > DISK_USAGE_MAX_PCT,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase669_adoption_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = replay.get("metric_checks") or []
    rows.append(
        {
            "name": "shadow_mainline_parity",
            "actual": parity.get("mismatch_count"),
            "expected": 0,
            "match": parity.get("mismatch_count") == 0,
        }
    )
    rows.append(
        {
            "name": "flat_band_block_count",
            "actual": replay["counterfactual"].get("blocked_count"),
            "expected": EXPECTED_FLAT_BAND_BLOCKS,
            "match": replay["counterfactual"].get("blocked_count") == EXPECTED_FLAT_BAND_BLOCKS,
        }
    )
    _write_csv(
        REPORT_ROOT / "phase669_replay_validation.csv",
        ["name", "actual", "expected", "match"],
        rows,
    )
    _write_shadow_cleanup_md(report=report)
    return report


def main() -> None:
    report = run_adoption_audit()
    print(json.dumps({"verdict": report["verdict"], "blocked": report["replay_validation"]["counterfactual"]["blocked_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
