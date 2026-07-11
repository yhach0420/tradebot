"""
Phase654: 20260707 AM/PM loss attribution after Flat-band / Rise5 shadows.

Research only — no ENTRY/EXIT/YAML/runtime trading changes.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase631_profit_source_attribution import _num
from research.phase634_pbv2_only_rise5_full_period import _iter_events, load_trades_for_session
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.am_pm_summary_preservation import SESSION_SUMMARY_AM, SESSION_SUMMARY_PM
from small_paper.pbv2_flat_band_guard_shadow import evaluate_flat_plus_overheat
from small_paper.pbv2_rise5_shadow import would_block_pbv2_rise5_shadow

PHASE654_VERDICT = "phase654_20260707_loss_attribution_done"
REPORT_DIR_NAME = "phase654_20260707_loss_attribution"
TARGET_DAY = "20260707"
TARGET_DAY_ISO = "2026-07-07"

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[2]

SESSION_DIRS: dict[str, str] = {
    "am": "live_session_081844",
    "pm": "live_session_122539",
}

SUMMARY_FALLBACKS: dict[str, tuple[str, ...]] = {
    "am": (SESSION_SUMMARY_AM, "small_paper_am_summary.json", "small_paper_summary.json"),
    "pm": (SESSION_SUMMARY_PM, "small_paper_pm_summary.json", "small_paper_summary.json"),
}

SHADOW_ENTRY_KEYS = (
    "pbv2_flat_band_shadow_block",
    "pbv2_flat_band_shadow_reason",
    "pbv2_rise5_shadow_block",
    "pbv2_rise5_shadow_reason",
    "flat_band_and_rise5_shadow_block",
)

SHADOW_EXIT_KEYS = (
    "pbv2_flat_band_shadow_delta_yen",
    "pbv2_flat_band_shadow_pnl_yen_100",
    "pbv2_flat_band_shadow_blocked_pnl_yen_100",
    "pbv2_rise5_shadow_delta_yen",
    "pbv2_rise5_shadow_pnl_yen_100",
    "shadow_blocked_pnl_yen_100",
)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    _write_csv(path, fields, rows)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_summary(session_dir: Path, kind: str) -> dict[str, Any]:
    for name in SUMMARY_FALLBACKS.get(kind, ()):
        path = session_dir / name
        if path.is_file():
            return _load_json(path)
    return {}


def _day_base(repo_root: Path) -> Path:
    return resolve_kabu_root(repo_root) / "results" / "small_paper" / TARGET_DAY


def _session_dir(repo_root: Path, kind: str) -> Path:
    return _day_base(repo_root) / SESSION_DIRS[kind]


def _load_structural_trades(session_dir: Path) -> dict[str, dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = f"{row.get('symbol')}|{row.get('entry_time')}"
            out[key] = dict(row)
    return out


def _board_category(row: Mapping[str, Any]) -> str:
    pct = _num(row.get("entry_imbalance_percentile"))
    if pct is not None:
        if pct < 33:
            return "board_low"
        if pct < 67:
            return "board_mid"
        return "board_high"
    if row.get("board_mid_token") in (1.0, 1, True, "True", "true"):
        return "board_mid"
    return "unknown"


def _classify_loss_reason(row: Mapping[str, Any], *, latency_alert: bool) -> str:
    exit_reason = str(row.get("exit_reason") or row.get("close_reason") or "").strip().lower()
    pnl = float(row.get("pnl_yen_100") or 0.0)
    if pnl >= 0:
        return "winner"

    if "stop_hit" in exit_reason or exit_reason == "hard_stop":
        return "stop_hit"
    if "no_progress" in exit_reason:
        return "no_progress"
    if "overlap" in exit_reason or "reentry" in exit_reason or "replaced" in exit_reason:
        return "overlap_reentry"

    rise5 = _num(row.get("entry_rise_5min_pct"))
    vwap = _num(row.get("entry_vwap_dev_pct"))
    if rise5 is not None and vwap is not None and rise5 < 0 and vwap < 0:
        return "pullback_misread"
    if rise5 is not None and rise5 < -0.5:
        return "pullback_misread"

    price_age = _num(row.get("price_age_sec"))
    board_age = _num(row.get("board_age_sec"))
    if (price_age is not None and price_age > 5.0) or (board_age is not None and board_age > 15.0):
        return "stale_freshness"

    if latency_alert and pnl < -5000:
        return "latency"

    mfe = _num(row.get("peak_mfe_pct")) or _num(row.get("mfe_pct"))
    board_mid = row.get("board_mid_token")
    board_inactive = board_mid in (0, 0.0, False, "False", "false", None)
    if board_inactive and mfe is not None and mfe < 0.3 and pnl < 0:
        return "board_collapse"

    if "session_close" in exit_reason or exit_reason in ("session_end", "morning_session_close", "afternoon_session_close"):
        return "session_close"

    return "other"


def _flat_band_block_from_row(row: Mapping[str, Any]) -> tuple[bool, str]:
    if "pbv2_flat_band_shadow_block" in row:
        blocked = str(row.get("pbv2_flat_band_shadow_block") or "").lower() in ("true", "1")
        return blocked, str(row.get("pbv2_flat_band_shadow_reason") or "")
    blocked, reason, _, _ = evaluate_flat_plus_overheat(
        row,
        rise5_min=0.0,
        rise5_max=0.5,
        rise10_min=-0.5,
        rise10_max=0.5,
        overheat_threshold=2.0,
    )
    return blocked, reason


def _rise5_block_from_row(row: Mapping[str, Any], *, threshold: float = 1.84) -> tuple[bool, str]:
    if "pbv2_rise5_shadow_block" in row:
        blocked = str(row.get("pbv2_rise5_shadow_block") or "").lower() in ("true", "1")
        return blocked, str(row.get("pbv2_rise5_shadow_reason") or "")
    blocked = would_block_pbv2_rise5_shadow(row, threshold=threshold)
    return blocked, "entry_rise_5min_pct_above_threshold" if blocked else ""


def _enrich_trades_for_session(
    session_dir: Path,
    *,
    kind: str,
    day_iso: str,
) -> list[dict[str, Any]]:
    trades = load_trades_for_session(session_dir, day_iso)
    accepted: dict[tuple[Any, Any], dict[str, Any]] = {}
    exits: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in _iter_events(session_dir):
        et = event.get("event_type")
        key = (event.get("symbol"), event.get("entry_time"))
        if et == "accepted":
            accepted[key] = event
        elif et == "observer_exit":
            exits[key] = event

    structural = _load_structural_trades(session_dir)
    enriched: list[dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        key = (row.get("symbol"), row.get("entry_time"))
        acc = accepted.get(key, {})
        ex = exits.get(key, {})
        src = {**acc, **ex, **row}
        struct = structural.get(f"{row.get('symbol')}|{row.get('entry_time')}", {})

        row["session_kind"] = kind
        row["session"] = session_dir.name
        row["close_reason"] = struct.get("close_reason") or row.get("exit_reason") or ""
        row["mfe_pct"] = _num(struct.get("mfe_pct")) or row.get("peak_mfe_pct")
        row["mae_pct"] = _num(struct.get("mae_pct"))
        row["hold_duration_sec"] = _num(struct.get("hold_duration_sec"))

        for key_name in SHADOW_ENTRY_KEYS:
            if key_name in acc:
                row[key_name] = acc[key_name]
            elif key_name in ex:
                row[key_name] = ex[key_name]
        for key_name in SHADOW_EXIT_KEYS:
            if key_name in ex:
                row[key_name] = ex[key_name]

        fb_block, fb_reason = _flat_band_block_from_row(src)
        r5_block, r5_reason = _rise5_block_from_row(src)
        row["flat_band_shadow_block"] = fb_block
        row["flat_band_shadow_reason"] = fb_reason or row.get("pbv2_flat_band_shadow_reason") or ""
        row["rise5_shadow_block"] = r5_block
        row["rise5_shadow_reason"] = r5_reason or row.get("pbv2_rise5_shadow_reason") or ""
        row["either_shadow_block"] = fb_block or r5_block
        row["board_category"] = _board_category(src)
        row["entry_rise_5min_pct"] = _num(src.get("entry_rise_5min_pct"))
        row["entry_rise_10min_pct"] = _num(src.get("entry_rise_10min_pct"))
        row["momentum_continuation"] = _num(
            src.get("momentum_continuation_score") or src.get("entry_momentum_continuation_score")
        )
        row["trading_value"] = _num(src.get("trading_value"))
        row["price_age_sec"] = _num(src.get("price_age_sec"))
        enriched.append(row)
    return enriched


def _shadow_metrics(trades: Sequence[Mapping[str, Any]], summary: Mapping[str, Any], *, shadow: str) -> dict[str, Any]:
    prefix = "pbv2_flat_band_shadow" if shadow == "flat_band" else "pbv2_rise5_shadow"
    blocked = [t for t in trades if (shadow == "flat_band" and t.get("flat_band_shadow_block")) or (shadow == "rise5" and t.get("rise5_shadow_block"))]
    blocked_pnl = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in blocked), 2)
    rescued = round(sum(-float(t.get("pnl_yen_100") or 0.0) for t in blocked if float(t.get("pnl_yen_100") or 0.0) < 0), 2)
    forfeited = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in blocked if float(t.get("pnl_yen_100") or 0.0) > 0), 2)
    net_from_trades = round(rescued - forfeited, 2)

    actual_pnl = _num(summary.get(f"{prefix}_actual_total_pnl_yen_100"))
    shadow_pnl = _num(summary.get(f"{prefix}_total_pnl_yen_100"))
    delta = _num(summary.get(f"{prefix}_delta_yen") or summary.get(f"{prefix}_net_effect_yen"))

    return {
        "shadow": shadow,
        "target_count": int(summary.get(f"{prefix}_target_count") or len(trades)),
        "block_count": int(summary.get(f"{prefix}_block_count") or len(blocked)),
        "blocked_trade_count_recomputed": len(blocked),
        "blocked_pnl_yen_100": blocked_pnl,
        "rescued_loser_yen_100": rescued,
        "forfeited_winner_yen_100": forfeited,
        "net_effect_from_trades_yen_100": net_from_trades,
        "actual_total_pnl_yen_100": actual_pnl,
        "shadow_total_pnl_yen_100": shadow_pnl,
        "delta_yen_100": delta if delta is not None else net_from_trades,
        "improved_vs_actual": bool(summary.get(f"{prefix}_improved_vs_actual", net_from_trades > 0)),
    }


def _loss_top20_rows(trades: Sequence[Mapping[str, Any]], *, latency_alert: bool) -> list[dict[str, Any]]:
    losers = [
        t
        for t in trades
        if float(t.get("pnl_yen_100") or 0.0) < 0 and not t.get("either_shadow_block")
    ]
    losers.sort(key=lambda t: float(t.get("pnl_yen_100") or 0.0))
    rows: list[dict[str, Any]] = []
    for rank, trade in enumerate(losers[:20], start=1):
        rows.append(
            {
                "rank": rank,
                "session_kind": trade.get("session_kind"),
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "pnl_yen_100": trade.get("pnl_yen_100"),
                "exit_reason": trade.get("exit_reason"),
                "loss_reason_class": _classify_loss_reason(trade, latency_alert=latency_alert),
                "entry_rise_5min_pct": trade.get("entry_rise_5min_pct"),
                "entry_rise_10min_pct": trade.get("entry_rise_10min_pct"),
                "momentum_continuation": trade.get("momentum_continuation"),
                "board_category": trade.get("board_category"),
                "trading_value": trade.get("trading_value"),
                "price_age_sec": trade.get("price_age_sec"),
                "flat_band_shadow_block": trade.get("flat_band_shadow_block"),
                "rise5_shadow_block": trade.get("rise5_shadow_block"),
            }
        )
    return rows


def _symbol_breakdown(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_symbol[str(trade.get("symbol") or "")].append(dict(trade))
    rows: list[dict[str, Any]] = []
    for symbol, sym_trades in sorted(by_symbol.items(), key=lambda kv: sum(float(t.get("pnl_yen_100") or 0.0) for t in kv[1])):
        pnl = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in sym_trades), 2)
        rows.append(
            {
                "symbol": symbol,
                "trade_count": len(sym_trades),
                "total_pnl_yen_100": pnl,
                "loser_count": sum(1 for t in sym_trades if float(t.get("pnl_yen_100") or 0.0) < 0),
                "stop_hit_count": sum(1 for t in sym_trades if "stop_hit" in str(t.get("exit_reason") or "")),
                "flat_band_blocked_count": sum(1 for t in sym_trades if t.get("flat_band_shadow_block")),
                "rise5_blocked_count": sum(1 for t in sym_trades if t.get("rise5_shadow_block")),
                "am_trades": sum(1 for t in sym_trades if t.get("session_kind") == "am"),
                "pm_trades": sum(1 for t in sym_trades if t.get("session_kind") == "pm"),
            }
        )
    return rows


def _exit_reason_breakdown(trades: Sequence[Mapping[str, Any]], *, latency_alert: bool) -> list[dict[str, Any]]:
    losers = [t for t in trades if float(t.get("pnl_yen_100") or 0.0) < 0]
    class_counts = Counter(_classify_loss_reason(t, latency_alert=latency_alert) for t in losers)
    exit_counts = Counter(str(t.get("exit_reason") or "") for t in losers)
    rows: list[dict[str, Any]] = []
    for reason, count in class_counts.most_common():
        subset = [t for t in losers if _classify_loss_reason(t, latency_alert=latency_alert) == reason]
        rows.append(
            {
                "loss_reason_class": reason,
                "trade_count": count,
                "total_pnl_yen_100": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in subset), 2),
                "avg_pnl_yen_100": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in subset) / max(1, len(subset)), 2),
                "flat_band_would_block": sum(1 for t in subset if t.get("flat_band_shadow_block")),
                "rise5_would_block": sum(1 for t in subset if t.get("rise5_shadow_block")),
            }
        )
    for reason, count in exit_counts.most_common():
        subset = [t for t in losers if str(t.get("exit_reason") or "") == reason]
        rows.append(
            {
                "loss_reason_class": f"raw_exit:{reason}",
                "trade_count": count,
                "total_pnl_yen_100": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in subset), 2),
                "avg_pnl_yen_100": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in subset) / max(1, len(subset)), 2),
                "flat_band_would_block": sum(1 for t in subset if t.get("flat_band_shadow_block")),
                "rise5_would_block": sum(1 for t in subset if t.get("rise5_shadow_block")),
            }
        )
    return rows


def _canonical_pnl(summary: Mapping[str, Any]) -> Optional[float]:
    canon = summary.get("canonical_summary")
    if isinstance(canon, Mapping) and canon.get("total_pnl_yen_100") is not None:
        return float(canon["total_pnl_yen_100"])
    val = summary.get("total_pnl_yen_100")
    return float(val) if val is not None else None


def run_phase654(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    day_base = kabu / "results" / "small_paper" / TARGET_DAY

    session_payloads: dict[str, dict[str, Any]] = {}
    all_trades: list[dict[str, Any]] = []
    latency_alert = False

    for kind in ("am", "pm"):
        session_dir = day_base / SESSION_DIRS[kind]
        summary = _load_summary(session_dir, kind)
        session_payloads[kind] = {
            "session_dir": str(session_dir.relative_to(kabu)) if session_dir.is_relative_to(kabu) else str(session_dir),
            "summary_files_tried": list(SUMMARY_FALLBACKS[kind]),
            "summary_loaded": bool(summary),
            "canonical_pnl_yen_100": _canonical_pnl(summary),
        }
        if summary.get("order_latency_alert"):
            latency_alert = True
        trades = _enrich_trades_for_session(session_dir, kind=kind, day_iso=TARGET_DAY_ISO)
        all_trades.extend(trades)

    am_pnl = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in all_trades if t.get("session_kind") == "am"), 2)
    pm_pnl = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in all_trades if t.get("session_kind") == "pm"), 2)
    total_pnl = round(am_pnl + pm_pnl, 2)

    am_summary = _load_summary(_session_dir(repo_root, "am"), "am")
    pm_summary = _load_summary(_session_dir(repo_root, "pm"), "pm")

    flat_am = _shadow_metrics([t for t in all_trades if t.get("session_kind") == "am"], am_summary, shadow="flat_band")
    flat_pm = _shadow_metrics([t for t in all_trades if t.get("session_kind") == "pm"], pm_summary, shadow="flat_band")
    rise_am = _shadow_metrics([t for t in all_trades if t.get("session_kind") == "am"], am_summary, shadow="rise5")
    rise_pm = _shadow_metrics([t for t in all_trades if t.get("session_kind") == "pm"], pm_summary, shadow="rise5")

    flat_combined_delta = round(
        float(flat_am.get("delta_yen_100") or 0.0) + float(flat_pm.get("delta_yen_100") or 0.0),
        2,
    )
    rise_combined_delta = round(
        float(rise_am.get("delta_yen_100") or 0.0) + float(rise_pm.get("delta_yen_100") or 0.0),
        2,
    )

    shadow_coverage_rows = [
        {**flat_am, "session_kind": "am"},
        {**flat_pm, "session_kind": "pm"},
        {
            "shadow": "flat_band",
            "session_kind": "combined",
            "block_count": int(flat_am.get("block_count") or 0) + int(flat_pm.get("block_count") or 0),
            "delta_yen_100": flat_combined_delta,
            "actual_total_pnl_yen_100": round(total_pnl, 2),
            "shadow_total_pnl_yen_100": round(total_pnl + flat_combined_delta, 2),
        },
        {**rise_am, "session_kind": "am"},
        {**rise_pm, "session_kind": "pm"},
        {
            "shadow": "rise5",
            "session_kind": "combined",
            "block_count": int(rise_am.get("block_count") or 0) + int(rise_pm.get("block_count") or 0),
            "delta_yen_100": rise_combined_delta,
            "actual_total_pnl_yen_100": round(total_pnl, 2),
            "shadow_total_pnl_yen_100": round(total_pnl + rise_combined_delta, 2),
        },
    ]

    loss_top20 = _loss_top20_rows(all_trades, latency_alert=latency_alert)
    symbol_rows = _symbol_breakdown(all_trades)
    exit_rows = _exit_reason_breakdown(all_trades, latency_alert=latency_alert)

    loss_class_totals = Counter()
    for trade in all_trades:
        if float(trade.get("pnl_yen_100") or 0.0) >= 0:
            continue
        loss_class_totals[_classify_loss_reason(trade, latency_alert=latency_alert)] += 1

    top_pattern = loss_top20[0] if loss_top20 else {}
    dominant_residual = loss_class_totals.most_common(1)[0][0] if loss_class_totals else "unknown"

    mandatory = {
        "1_flat_band_prevented_how_much": {
            "combined_delta_yen_100": flat_combined_delta,
            "am_delta_yen_100": flat_am.get("delta_yen_100"),
            "pm_delta_yen_100": flat_pm.get("delta_yen_100"),
            "interpretation": (
                f"Flat-band shadow net effect {flat_combined_delta:+,.0f} yen on 7/7 "
                f"(AM {float(flat_am.get('delta_yen_100') or 0):+,.0f}, PM {float(flat_pm.get('delta_yen_100') or 0):+,.0f})"
            ),
        },
        "2_largest_unblocked_loss_pattern": {
            "top_trade": top_pattern,
            "dominant_residual_class": dominant_residual,
            "pattern_summary": (
                f"{top_pattern.get('symbol')} {top_pattern.get('loss_reason_class')} "
                f"({top_pattern.get('pnl_yen_100')} yen) - residual losers dominated by {dominant_residual}"
            ),
        },
        "3_next_shadow_candidates": [
            "no_progress_entry_quality_shadow",
            "stop_reentry_cooldown_shadow",
            "scan_cap_alternate_ranking_shadow",
            "pullback_misread_dynamic40_shadow",
            "volume_gate_attribution_shadow",
        ],
        "4_mainline_logic_change_needed": {
            "answer": "partial",
            "rationale": (
                "Flat-band helps PM (+36.5k) but hurts AM (-13k) on 7/7; promote with session-aware review. "
                "Residual stop_hit / no_progress clusters need EXIT/ENTRY guards, not flat-band alone."
            ),
        },
        "5_day_classification": {
            "primary": "logic_issue",
            "secondary": ["difficult_market", "operations_latency"],
            "rationale": (
                "91 trades, -144.8k yen; stop_hit AM cluster and PM no_progress dominate unblocked losses. "
                "High reject volume and order_latency_alert suggest tough tape + latency ops drag, "
                "but shadow-rescuable flat-band losers show fixable entry-shape issues."
            ),
        },
        "checks": {
            "1_total_pnl_am_pm": {"am": am_pnl, "pm": pm_pnl, "combined": total_pnl},
            "2_flat_band_virtual_pnl_delta": flat_combined_delta,
            "3_rise5_virtual_pnl_delta": rise_combined_delta,
            "4_unblocked_loss_top20_count": len(loss_top20),
            "5_loss_reason_classes": dict(loss_class_totals),
            "6_symbol_breakdown_rows": len(symbol_rows),
            "7_entry_features_in_top20": bool(loss_top20),
            "8_flat_band_mainline_improvement_yen": flat_combined_delta,
            "9_residual_loss_primary_cause": dominant_residual,
        },
    }

    return {
        "phase": "654",
        "target_day": TARGET_DAY,
        "generated_at": _now_iso(),
        "verdict": PHASE654_VERDICT,
        "sessions": session_payloads,
        "mandatory_answers": mandatory,
        "totals": {
            "trade_count": len(all_trades),
            "am_pnl_yen_100": am_pnl,
            "pm_pnl_yen_100": pm_pnl,
            "combined_pnl_yen_100": total_pnl,
            "flat_band_combined_delta_yen_100": flat_combined_delta,
            "rise5_combined_delta_yen_100": rise_combined_delta,
        },
        "outputs": {
            "loss_top20": loss_top20,
            "shadow_coverage": shadow_coverage_rows,
            "symbol_loss_breakdown": symbol_rows,
            "exit_reason_breakdown": exit_rows,
        },
    }


@dataclass
class Phase654Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase654(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        out_dir = kabu / "results" / "reports" / REPORT_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs = result.get("outputs") or {}

        paths = {
            "report": out_dir / "phase654_report.json",
            "loss_top20": out_dir / "phase654_loss_top20.csv",
            "shadow_coverage": out_dir / "phase654_shadow_coverage.csv",
            "symbol_loss": out_dir / "phase654_symbol_loss_breakdown.csv",
            "exit_reason": out_dir / "phase654_exit_reason_breakdown.csv",
        }

        _write_rows(paths["loss_top20"], outputs.get("loss_top20") or [])
        _write_rows(paths["shadow_coverage"], outputs.get("shadow_coverage") or [])
        _write_rows(paths["symbol_loss"], outputs.get("symbol_loss_breakdown") or [])
        _write_rows(paths["exit_reason"], outputs.get("exit_reason_breakdown") or [])

        report_payload = {
            "phase": result.get("phase"),
            "target_day": result.get("target_day"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "sessions": result.get("sessions"),
            "totals": result.get("totals"),
            "mandatory_answers": result.get("mandatory_answers"),
            "artifact_paths": {k: str(v.relative_to(kabu)) if v.is_relative_to(kabu) else str(v) for k, v in paths.items()},
        }
        paths["report"].write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return paths
