"""
Canonical session summary from actual executed observer_exit trades only.

Discord Summary reads SUMMARY.json.canonical_summary exclusively (no re-aggregation).
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Mapping, Optional, Sequence

from replay.pnl_yen import enrich_trade_pnl_yen, format_pnl_yen_100_display
from small_paper.discord_message_builder import humanize_exit_reason


def _is_virtual_hold_exit_reason(reason: str) -> bool:
    r = str(reason or "").strip().lower()
    return "virtual_hold" in r or r == "live_virtual_hold"

log = logging.getLogger(__name__)

CANONICAL_EXCLUDED_EVENT_TYPES = frozenset(
    {
        "rejected",
        "candidate",
        "accepted",
        "shadow_exit",
        "notification",
        "debug",
    }
)

STOP_EXIT_REASONS = frozenset(
    {
        "hard_stop",
        "stop_loss",
        "loss_cut",
        "stop_hit",
    }
)

PF_EPSILON = 1.0


def _symbol_short(sym: str) -> str:
    s = str(sym or "").strip().upper()
    return s.replace(".T", "") if s else "—"


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_canonical_trade(row: Mapping[str, Any]) -> bool:
    if str(row.get("event_type") or "") != "observer_exit":
        return False
    if str(row.get("event_type") or "") in CANONICAL_EXCLUDED_EVENT_TYPES:
        return False
    if row.get("notification_only") or row.get("debug_row") or row.get("skipped"):
        return False
    if row.get("shadow_only") or row.get("is_shadow_trade"):
        return False
    if str(row.get("exit_kind") or "") in ("shadow_exit", "capacity_rejected"):
        return False
    reason = str(row.get("exit_reason") or "")
    if _is_virtual_hold_exit_reason(reason):
        return False
    enriched = enrich_trade_pnl_yen(dict(row))
    return enriched.get("pnl_yen_100") is not None


def collect_canonical_trades(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_trade_pnl_yen(dict(e)) for e in events if is_canonical_trade(e)]


def is_stop_exit(row: Mapping[str, Any]) -> bool:
    if row.get("stop_hit"):
        return True
    for key in ("exit_reason", "structural_exit_reason"):
        reason = str(row.get(key) or "").strip()
        if reason in STOP_EXIT_REASONS:
            return True
        # Display labels (legacy + Phase687W25)
        if reason and humanize_exit_reason(reason) in ("損切りライン到達", "損切り"):
            return True
    return False


def _resolved_exit_reason(row: Mapping[str, Any]) -> str:
    reason = str(row.get("exit_reason") or "").strip()
    structural = str(row.get("structural_exit_reason") or "").strip()
    if reason == "overlap_replaced_review":
        return structural
    return structural or reason


def _display_exit_reason(row: Mapping[str, Any]) -> str:
    reason = _resolved_exit_reason(row)
    if not reason or reason == "overlap_replaced_review":
        return ""
    return humanize_exit_reason(reason)


def _profit_factor_value(gross_profit: float, gross_loss: float) -> float | str | None:
    if gross_loss > 0:
        return round(gross_profit / gross_loss, 4)
    if gross_profit > 0:
        return "inf"
    return None


def _format_trade_display(row: Mapping[str, Any]) -> str:
    sym = _symbol_short(str(row.get("symbol", "")))
    yen = float(row["pnl_yen_100"])
    yen_str = format_pnl_yen_100_display(yen)
    pct = _as_float(row.get("pnl_pct"))
    pct_part = ""
    if pct is not None:
        sign = "+" if pct >= 0 else ""
        pct_part = f"{sign}{pct:.2f}%"
    reason_disp = _display_exit_reason(row)
    base = " ".join(part for part in (sym, yen_str, pct_part) if part)
    if reason_disp:
        return f"{base} ({reason_disp})"
    return base


def _trade_summary_object(row: Mapping[str, Any]) -> dict[str, Any]:
    overlap = bool(row.get("overlap_replaced_review")) or str(
        row.get("exit_reason") or ""
    ).strip() == "overlap_replaced_review"
    resolved_reason = _resolved_exit_reason(row)
    return {
        "symbol": str(row.get("symbol") or ""),
        "symbol_short": _symbol_short(str(row.get("symbol", ""))),
        "pnl_yen_100": round(float(row["pnl_yen_100"]), 2),
        "pnl_pct": round(float(row["pnl_pct"]), 4) if _as_float(row.get("pnl_pct")) is not None else None,
        "exit_reason": resolved_reason,
        "exit_reason_display": _display_exit_reason(row),
        "duplicate_entry_observed": overlap,
        "display": _format_trade_display(row),
    }


def build_canonical_summary(
    trades: Sequence[Mapping[str, Any]],
    *,
    peak_open_slots: int = 0,
    max_concurrent_positions: int = 3,
    watch_symbols_count: Optional[int] = None,
) -> dict[str, Any]:
    yens = [float(t["pnl_yen_100"]) for t in trades]
    pnls_pct = [_as_float(t.get("pnl_pct")) for t in trades]
    pnls_pct = [p for p in pnls_pct if p is not None]

    trade_count = len(yens)
    win_count = sum(1 for y in yens if y > 0)
    loss_count = sum(1 for y in yens if y < 0)
    flat_count = sum(1 for y in yens if y == 0)

    gross_profit = round(sum(max(y, 0.0) for y in yens), 2)
    gross_loss = round(sum(abs(min(y, 0.0)) for y in yens), 2)
    total_pnl_yen_100 = round(sum(yens), 2) if yens else 0.0
    avg_pnl_yen_100 = round(total_pnl_yen_100 / trade_count, 2) if trade_count else None
    profit_factor_yen_100 = _profit_factor_value(gross_profit, gross_loss)

    stop_count = sum(1 for t in trades if is_stop_exit(t))
    traded_symbols = {str(t.get("symbol")) for t in trades if t.get("symbol")}

    best_trade: dict[str, Any] | str = "—"
    worst_trade: dict[str, Any] | str = "—"
    if trades:
        best_row = max(trades, key=lambda t: float(t["pnl_yen_100"]))
        worst_row = min(trades, key=lambda t: float(t["pnl_yen_100"]))
        best_trade = _trade_summary_object(best_row)
        worst_trade = _trade_summary_object(worst_row)

    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "win_rate_yen_100": round(win_count / trade_count, 4) if trade_count else 0.0,
        "total_pnl_yen_100": total_pnl_yen_100,
        "avg_pnl_yen_100": avg_pnl_yen_100,
        "gross_profit_yen_100": gross_profit,
        "gross_loss_yen_100": gross_loss,
        "profit_factor_yen_100": profit_factor_yen_100,
        "avg_pnl_pct": round(statistics.mean(pnls_pct), 2) if pnls_pct else 0.0,
        "avg_pnl_pct_raw": round(statistics.mean(pnls_pct), 2) if pnls_pct else 0.0,
        "total_pnl_pct_raw": round(sum(pnls_pct), 2) if pnls_pct else 0.0,
        "stop_count": stop_count,
        "stop_rate": round(stop_count / trade_count, 4) if trade_count else 0.0,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "max_concurrent": int(peak_open_slots or 0),
        "max_concurrent_cap": int(max_concurrent_positions),
        "watch_symbols_count": watch_symbols_count,
        "traded_symbols_count": len(traded_symbols),
    }


def parse_discord_summary_fields(lines: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        out[key.strip()] = value.strip()
    return out


def expected_discord_field_map(canonical: Mapping[str, Any]) -> dict[str, str]:
    from small_paper.discord_message_builder import format_discord_summary_lines

    return parse_discord_summary_fields(format_discord_summary_lines(canonical))


def validate_discord_display_matches_canonical(canonical: Mapping[str, Any]) -> list[str]:
    from small_paper.discord_message_builder import format_discord_summary_lines

    lines = format_discord_summary_lines(canonical)
    parsed = parse_discord_summary_fields(lines)
    expected = expected_discord_field_map(canonical)
    errors: list[str] = []
    for key, value in expected.items():
        if parsed.get(key) != value:
            errors.append(f"discord.{key}: expected {value!r}, got {parsed.get(key)!r}")
    for forbidden in ("total_pnl_pct", "avg_pnl_pct"):
        if forbidden in parsed:
            errors.append(f"discord.{forbidden}: must not be displayed")
    return errors


def validate_canonical_summary_integrity(
    canonical: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    yens = [float(t["pnl_yen_100"]) for t in trades]
    trade_count = len(yens)

    if int(canonical.get("trade_count") or 0) != trade_count:
        errors.append(
            f"trade_count: expected {trade_count}, got {canonical.get('trade_count')}"
        )

    expected_total = round(sum(yens), 2) if yens else 0.0
    if _as_float(canonical.get("total_pnl_yen_100")) != expected_total:
        errors.append(
            f"total_pnl_yen_100: expected {expected_total}, got {canonical.get('total_pnl_yen_100')}"
        )

    if trade_count:
        expected_avg = round(expected_total / trade_count, 2)
        if _as_float(canonical.get("avg_pnl_yen_100")) != expected_avg:
            errors.append(
                f"avg_pnl_yen_100: expected {expected_avg}, got {canonical.get('avg_pnl_yen_100')}"
            )

    expected_gross_profit = round(sum(max(y, 0.0) for y in yens), 2)
    expected_gross_loss = round(sum(abs(min(y, 0.0)) for y in yens), 2)
    if _as_float(canonical.get("gross_profit_yen_100")) != expected_gross_profit:
        errors.append(
            f"gross_profit_yen_100: expected {expected_gross_profit}, got {canonical.get('gross_profit_yen_100')}"
        )
    if _as_float(canonical.get("gross_loss_yen_100")) != expected_gross_loss:
        errors.append(
            f"gross_loss_yen_100: expected {expected_gross_loss}, got {canonical.get('gross_loss_yen_100')}"
        )

    pf = canonical.get("profit_factor_yen_100")
    if expected_gross_loss > 0:
        expected_pf = round(expected_gross_profit / expected_gross_loss, 4)
        if _as_float(pf) != expected_pf:
            errors.append(
                f"profit_factor_yen_100: expected {expected_pf}, got {pf}"
            )
        pf_f = float(expected_pf)
        if pf_f > 1.0 + 1e-9 and expected_total <= 0:
            errors.append("pf_consistency: profit_factor_yen_100 > 1 requires total_pnl_yen_100 > 0")
        if pf_f < 1.0 - 1e-9 and expected_total >= 0:
            errors.append("pf_consistency: profit_factor_yen_100 < 1 requires total_pnl_yen_100 < 0")
        if abs(pf_f - 1.0) <= 1e-9 and abs(expected_total) > PF_EPSILON:
            errors.append("pf_consistency: profit_factor_yen_100 == 1 requires total_pnl_yen_100 near 0")
    elif expected_gross_profit > 0:
        if pf != "inf":
            errors.append(f"profit_factor_yen_100: expected 'inf', got {pf!r}")
    elif expected_gross_profit == 0 and pf is not None:
        errors.append(f"profit_factor_yen_100: expected null, got {pf!r}")

    errors.extend(validate_discord_display_matches_canonical(canonical))
    return errors


def _is_session_close_trade(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("exit_reason") or "").strip().lower()
    if "session_close" in reason or reason in ("session_end", "morning_session_close", "afternoon_session_close"):
        return True
    return str(row.get("session_close") or "").lower() in ("true", "1", "yes")


def session_close_pnl_breakdown(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    close = [t for t in trades if _is_session_close_trade(t)]
    non = [t for t in trades if not _is_session_close_trade(t)]
    close_yen = round(sum(float(t["pnl_yen_100"]) for t in close), 2)
    non_yen = round(sum(float(t["pnl_yen_100"]) for t in non), 2)
    return {
        "session_close_trade_count": len(close),
        "session_close_pnl_yen_100": close_yen,
        "non_session_close_trade_count": len(non),
        "non_session_close_pnl_yen_100": non_yen,
        "non_session_close_total_pnl_yen_100": non_yen,  # explicit alias
    }


def peak_concurrent_from_position_events(events: Sequence[Mapping[str, Any]]) -> int:
    """Peak open count from immutable position_id open/close timeline.

    OPEN: accepted with position_id / accept_stage=position_registered
    CLOSE: observer_exit with same position_id (fallback: symbol FIFO if id missing)
    """
    open_ids: set[str] = set()
    open_syms: dict[str, list[str]] = {}
    peak = 0
    # stable chronological order by event_time / entry/exit
    def _ts(e: Mapping[str, Any]) -> str:
        return str(
            e.get("event_time")
            or e.get("exit_time")
            or e.get("entry_time")
            or ""
        )

    for e in sorted(events, key=_ts):
        et = str(e.get("event_type") or "")
        if et == "accepted":
            stage = str(e.get("accept_stage") or "")
            pid = str(e.get("observer_position_id") or e.get("position_id") or "")
            sym = str(e.get("symbol") or "")
            if stage and stage != "position_registered" and not pid:
                continue
            if not pid:
                # legacy rows without id: only count if we later see matching exit
                pid = f"legacy:{sym}:{e.get('entry_time')}"
            if pid in open_ids:
                continue
            open_ids.add(pid)
            open_syms.setdefault(sym, []).append(pid)
            peak = max(peak, len(open_ids))
        elif et == "observer_exit":
            pid = str(e.get("observer_position_id") or e.get("position_id") or "")
            sym = str(e.get("symbol") or "")
            if pid and pid in open_ids:
                open_ids.discard(pid)
                if sym in open_syms and pid in open_syms[sym]:
                    open_syms[sym].remove(pid)
            elif sym in open_syms and open_syms[sym]:
                old = open_syms[sym].pop(0)
                open_ids.discard(old)
    return int(peak)


def enrich_summary_with_canonical(
    summary: dict[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    peak_open_slots: Optional[int] = None,
    max_concurrent_positions: int = 3,
    watch_symbols_count: Optional[int] = None,
) -> dict[str, Any]:
    trades = collect_canonical_trades(events)
    # position_cap_mode: official max_concurrent = observer open peak, not gate peak_open_slots.
    obs_max = summary.get("observer_open_max_positions")
    timeline_peak = peak_concurrent_from_position_events(events)
    if summary.get("position_cap_mode"):
        resolved_peak = int(obs_max or timeline_peak or peak_open_slots or 0)
    else:
        resolved_peak = int(
            peak_open_slots
            if peak_open_slots is not None
            else summary.get("peak_open_slots")
            or obs_max
            or timeline_peak
            or 0
        )
    canonical = build_canonical_summary(
        trades,
        peak_open_slots=resolved_peak,
        max_concurrent_positions=max_concurrent_positions,
        watch_symbols_count=watch_symbols_count,
    )
    breakdown = session_close_pnl_breakdown(trades)
    canonical.update(breakdown)
    canonical["max_concurrent_source"] = (
        "observer_open_max_positions"
        if summary.get("position_cap_mode") and obs_max
        else "position_timeline"
    )
    errors = validate_canonical_summary_integrity(canonical, trades)
    summary["canonical_summary"] = canonical
    summary["total_pnl_pct_raw"] = canonical.get("total_pnl_pct_raw")

    # Official PnL SoT = canonical (includes session_close). Avoid dual total_pnl meaning.
    prior_top = summary.get("total_pnl_yen_100")
    summary["canonical_trade_count"] = canonical.get("trade_count")
    summary["canonical_total_pnl_yen_100"] = canonical.get("total_pnl_yen_100")
    summary["canonical_profit_factor_yen_100"] = canonical.get("profit_factor_yen_100")
    summary.update(breakdown)
    summary["total_pnl_yen_100"] = canonical.get("total_pnl_yen_100")
    summary["total_pnl_yen_100_source"] = "canonical_summary"
    summary["profit_factor_yen_100"] = canonical.get("profit_factor_yen_100")
    summary["observer_exit_count_with_pnl"] = canonical.get("trade_count")
    if prior_top is not None and _as_float(prior_top) != _as_float(canonical.get("total_pnl_yen_100")):
        summary["deprecated_non_canonical_total_pnl_yen_100"] = prior_top
        summary["total_pnl_yen_100_deprecated_note"] = (
            "Pre-fix top-level total_pnl excluded session_close or used a stale event snapshot. "
            "Official value is canonical_total_pnl_yen_100 / total_pnl_yen_100 (source=canonical_summary)."
        )
    summary["peak_concurrent_from_position_timeline"] = timeline_peak

    if errors:
        summary["summary_integrity_error"] = {"errors": errors}
        log.error(
            "canonical_summary integrity check failed: %s",
            "; ".join(errors),
        )
    else:
        summary.pop("summary_integrity_error", None)
    return summary
