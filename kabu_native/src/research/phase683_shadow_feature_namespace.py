"""Phase683 — Shadow feature namespace fix validation (research only)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from research.market_sector_heat import _write_csv
from research.phase631_profit_source_attribution import _num
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN
from research.phase675_recent_early_stop_focus import _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import _high_bounce, _live_feature_incomplete, _low_expectancy
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase681_microsequence_c_runtime_shadow import _decomp, _enrich_live_c, _pred_c_live, _pred_h, _pred_i
from small_paper.readiness_forward_shadow import EARLY_STOP_SEC, evaluate_readiness_economics

VERDICT_OK = "SHADOW_NAMESPACE_FIXED_AND_PAPER_STARTED"
VERDICT_ABORT = "ABORT_AND_REPORT"

REPORT_DIR_NAME = "phase683_shadow_feature_namespace"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

SHADOW_CFG = SimpleNamespace(
    readiness_precision_shadow_enabled=True,
    readiness_precision_shadow_expectancy_max=2.5,
    readiness_precision_shadow_require_live_incomplete=True,
    readiness_economics_shadow_enabled=True,
    readiness_economics_shadow_bounce_min=0.45,
    readiness_economics_shadow_require_live_incomplete=True,
    microsequence_recovery_fail_shadow_enabled=True,
    microsequence_recovery_fail_bounce_min=0.2182,
    microsequence_recovery_fail_fall_from_high_max=-0.1735,
    microsequence_recovery_fail_slope_5min_max=0.1152,
)

PHASE679_H = {"blocked_count": 115, "net_delta_yen": 190_900.0, "blocked_big_winners": 13}


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _is_early_stop_trade(t: Mapping[str, Any]) -> bool:
    if _is_early_stop(t):
        return True
    hs = _num(t.get("hold_sec"))
    return bool(_is_stop_hit(t) and hs is not None and hs <= EARLY_STOP_SEC)


def _pred_h_phase679(t: Mapping[str, Any]) -> bool:
    return _live_feature_incomplete(t) and _high_bounce(t, 0.45)


def _lane(pool: Sequence[Mapping[str, Any]], pred) -> dict[str, Any]:
    blocked = [t for t in pool if pred(t)]
    losers = [t for t in blocked if _pnl(t) < 0]
    winners = [t for t in blocked if _pnl(t) > 0]
    avoided = round(-sum(_pnl(t) for t in losers), 2)
    lost = round(sum(_pnl(t) for t in winners), 2)
    return {
        "entry_count": len(pool),
        "blocked_count": len(blocked),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop_trade(t)),
        "blocked_winners": len(winners),
        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
        "avoided_loss_yen": avoided,
        "lost_profit_yen": lost,
        "net_delta_yen": round(avoided - lost, 2),
    }


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())
    post_raw = [t for t in trades if t.get("post_flat_band_entry")]
    enriched = _enrich_live_c([dict(t) for t in trades])
    post_enriched = [t for t in enriched if t.get("post_flat_band_entry")]

    h_phase679 = _lane(post_raw, _pred_h_phase679)
    h_runtime = _lane(post_raw, lambda t: evaluate_readiness_economics(SHADOW_CFG, t))
    h_enriched = _lane(post_enriched, _pred_h)
    i_enriched = _lane(post_enriched, _pred_i)
    c_enriched = _lane(post_enriched, _pred_c_live)
    ihc_enriched = _lane(post_enriched, lambda t: _pred_i(t) or _pred_h(t) or _pred_c_live(t))

    bounce_preserved = all(
        _num(raw.get("bounce_from_recent_low")) == _num(enr.get("bounce_from_recent_low"))
        for raw, enr in zip(post_raw, post_enriched, strict=False)
        if _num(raw.get("bounce_from_recent_low")) is not None
    )
    accept_ns_present = all(enr.get("readiness_bounce_from_recent_low_accept") is not None or _num(enr.get("bounce_from_recent_low")) is None for enr in post_enriched)
    microseq_ns_present = sum(1 for t in post_enriched if t.get("microseq_bounce_from_recent_low") is not None)

    h_ok = (
        h_phase679["blocked_count"] == PHASE679_H["blocked_count"]
        and h_phase679["net_delta_yen"] == PHASE679_H["net_delta_yen"]
        and h_enriched["blocked_count"] == PHASE679_H["blocked_count"]
        and h_enriched["net_delta_yen"] == PHASE679_H["net_delta_yen"]
    )

    checks = {
        "h_phase679_reproduced": h_phase679["blocked_count"] == PHASE679_H["blocked_count"],
        "h_enriched_matches_phase679": h_enriched["blocked_count"] == PHASE679_H["blocked_count"],
        "h_net_delta_phase679": h_phase679["net_delta_yen"] == PHASE679_H["net_delta_yen"],
        "h_net_delta_enriched": h_enriched["net_delta_yen"] == PHASE679_H["net_delta_yen"],
        "accept_bounce_not_overwritten": bounce_preserved,
        "readiness_accept_namespace_present": accept_ns_present,
        "microseq_namespace_populated": microseq_ns_present > 0,
        "c_shadow_computed": c_enriched["blocked_count"] > 0,
        "mainline_reject": False,
        "entry_suppression": False,
    }
    verdict = VERDICT_OK if h_ok and checks["accept_bounce_not_overwritten"] else VERDICT_ABORT

    reconcile_rows = [
        {"lane": "H_baseline", "method": "phase679_pred_h", **h_phase679},
        {"lane": "H_baseline", "method": "runtime_evaluate_readiness_economics", **h_runtime},
        {"lane": "H_baseline", "method": "phase683_enriched_pred_h", **h_enriched},
        {"lane": "I_precision", "method": "phase683_enriched", **i_enriched},
        {"lane": "microsequence_C", "method": "phase683_enriched", **c_enriched},
    ]
    union_rows = [
        {
            "pool": "post_flat_band",
            "entry_count": ihc_enriched["entry_count"],
            "ihc_union_blocked_count": ihc_enriched["blocked_count"],
            "ihc_union_net_delta_yen": ihc_enriched["net_delta_yen"],
            "ihc_i_feature_source": "entry_expectancy_score_v2+live_feature_incomplete",
            "ihc_h_feature_source": "readiness_bounce_from_recent_low_accept",
            "ihc_c_feature_source": "microseq_ring",
            "ihc_union_feature_sources": "I:expectancy|H:accept_bounce|C:microseq_ring",
        }
    ]

    report: dict[str, Any] = {
        "verdict": verdict,
        "checks": checks,
        "phase679_h_reference": PHASE679_H,
        "h_phase679": h_phase679,
        "h_enriched_after_namespace_fix": h_enriched,
        "c_enriched": c_enriched,
        "ihc_union": ihc_enriched,
        "runtime_shadow": {"mainline_reject": False, "entry_suppression": False},
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase683_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(REPORT_ROOT / "phase683_h_reconciliation.csv", list(reconcile_rows[0].keys()), reconcile_rows)
    _write_csv(REPORT_ROOT / "phase683_ihc_union_recomputed.csv", list(union_rows[0].keys()), union_rows)
    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    checks = report.get("checks") or {}
    h = report.get("h_enriched_after_namespace_fix") or {}
    lines = [
        "# Phase683 — Shadow Feature Namespace Fix",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## Namespace rules",
        "",
        "- H: `readiness_bounce_from_recent_low_accept` (accept-field, Phase679/680 compatible)",
        "- C: `microseq_bounce_from_recent_low`, `microseq_fall_from_recent_high`, `microseq_slope_5min` (ring)",
        "- Generic `bounce_from_recent_low` / `fall_from_recent_high` / `slope_5min` are not overwritten by C enrich",
        "",
        "## H reconciliation (post_flat_band)",
        "",
        f"- Phase679 reference: blocked={PHASE679_H['blocked_count']} net={PHASE679_H['net_delta_yen']}",
        f"- After namespace fix (enriched): blocked={h.get('blocked_count')} net={h.get('net_delta_yen')}",
        f"- accept bounce preserved: {checks.get('accept_bounce_not_overwritten')}",
        "",
        "## I∨H∨C union sources",
        "",
        "`I:expectancy|H:accept_bounce|C:microseq_ring`",
        "",
    ]
    (REPORT_ROOT / "phase683_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "checks": report.get("checks")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
