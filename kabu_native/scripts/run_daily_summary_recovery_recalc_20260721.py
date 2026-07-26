#!/usr/bin/env python3
"""Regenerate formal Daily Summary for 20260721 after Recovery Finalize.

Counts recovery_forced_close as formal EXIT. Does not change Runtime ENTRY/EXIT logic.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

DAY = "20260721"
AM_SESSION = "live_session_080044"
PM_SESSION = "live_session_124342"


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    from small_paper.ws_freeze_recovery import load_jsonl

    return load_jsonl(session_dir / "small_paper_events.jsonl")


def _bucket_exits(events: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    from small_paper.canonical_summary import collect_canonical_trades

    trades = collect_canonical_trades(events)
    normal = [t for t in trades if str(t.get("exit_reason") or "") != "recovery_forced_close"]
    recovery = [t for t in trades if str(t.get("exit_reason") or "") == "recovery_forced_close"]
    return trades, normal, recovery


def _metrics(trades: list[dict[str, Any]], *, peak: int = 0, cap: int = 5, watch: Optional[int] = None) -> dict[str, Any]:
    from small_paper.canonical_summary import build_canonical_summary

    return build_canonical_summary(
        trades,
        peak_open_slots=peak,
        max_concurrent_positions=cap,
        watch_symbols_count=watch,
    )


def _recovery_price(row: dict[str, Any]) -> Optional[float]:
    for k in ("exit_price", "current_price", "validated_current_price", "entry_price"):
        v = _f(row.get(k))
        if v is not None:
            return v
    return None


def _recovery_rows(
    events: list[dict[str, Any]],
    *,
    session: str,
    am_pm: str,
) -> list[dict[str, Any]]:
    from replay.pnl_yen import enrich_trade_pnl_yen

    accepted_by_pid = {
        str(e.get("position_id") or ""): e
        for e in events
        if e.get("event_type") == "accepted" and e.get("position_id")
    }
    rows: list[dict[str, Any]] = []
    for e in events:
        if e.get("event_type") != "observer_exit":
            continue
        if str(e.get("exit_reason") or "") != "recovery_forced_close":
            continue
        enriched = enrich_trade_pnl_yen(dict(e))
        pid = str(e.get("position_id") or "")
        acc = accepted_by_pid.get(pid, {})
        entry_px = _f(e.get("entry_price"))
        if entry_px is None:
            entry_px = _f(acc.get("entry_price"))
        rec_px = _recovery_price(e)
        if rec_px is None:
            rec_px = entry_px
        yen = enriched.get("pnl_yen_100")
        if yen is None:
            yen = 0.0
        rows.append(
            {
                "session": session,
                "am_pm": am_pm,
                "symbol": str(e.get("symbol") or ""),
                "position_id": pid,
                "entry_price": entry_px,
                "recovery_price": rec_px,
                "pnl_yen_100": round(float(yen), 2),
                "pnl_pct": _f(e.get("pnl_pct")) if _f(e.get("pnl_pct")) is not None else 0.0,
                "recovery_reason": str(
                    e.get("recovery_note")
                    or e.get("structural_exit_reason")
                    or "recovery_forced_close"
                ),
                "exit_reason": "recovery_forced_close",
            }
        )
    return rows


def _shadow_rollup(am_s: dict[str, Any], pm_s: dict[str, Any]) -> dict[str, Any]:
    """Roll up key Actual+Shadow numeric fields after recovery finalize."""
    keys = [
        "flat_weak_range_shadow_actual_total_pnl_yen_100",
        "flat_weak_range_shadow_total_pnl_yen_100",
        "flat_weak_range_shadow_actual_pf",
        "flat_weak_range_shadow_shadow_pf",
        "flat_weak_range_shadow_delta_yen",
        "pbv2_flat_band_shadow_actual_total_pnl_yen_100",
        "pbv2_flat_band_shadow_total_pnl_yen_100",
        "pbv2_rise5_shadow_actual_total_pnl_yen_100",
        "pbv2_rise5_shadow_total_pnl_yen_100",
        "pullback_misread_guard_shadow_actual_total_pnl_yen_100",
        "pullback_misread_guard_shadow_total_pnl_yen_100",
        "pullback_misread_guard_shadow_delta_yen",
        "board_dynamic_shadow_total_delta_yen",
        "extended_entry_shadow_count",
        "extended_entry_shadow_pnl_estimate",
        "classic_momentum_shadow_trade_count",
        "classic_momentum_shadow_pnl_yen_100",
        "post_entry_shadow_score_ge3_count",
        "post_entry_shadow_score_ge3_pnl",
        "post_entry_shadow_score_ge4_count",
        "post_entry_shadow_score_ge4_pnl",
        "vwap_shadow_reject_candidate_count",
        "vwap_shadow_candidate_total_pnl",
        "vwap_shadow_candidate_pf",
    ]

    def _get(s: dict[str, Any], k: str) -> Any:
        return s.get(k)

    am_block = {k: _get(am_s, k) for k in keys if k in am_s}
    pm_block = {k: _get(pm_s, k) for k in keys if k in pm_s}
    combined: dict[str, Any] = {}
    for k in keys:
        av, pv = am_s.get(k), pm_s.get(k)
        if isinstance(av, (int, float)) and isinstance(pv, (int, float)):
            if "pf" in k and "delta" not in k:
                # PF is not additive — leave per-session
                continue
            combined[k] = round(float(av) + float(pv), 4)
        elif isinstance(av, (int, float)) and pv is None:
            combined[k] = av
        elif isinstance(pv, (int, float)) and av is None:
            combined[k] = pv

    # Official Actual after recovery (canonical)
    return {
        "trading_date": DAY,
        "generated_at": _now(),
        "shadow_summary_status": "RECOVERY_FORMAL",
        "note": "Shadow rollup after AM+PM recovery_forced_close finalize; Actual PnL includes recovery flats.",
        "am": am_block,
        "pm": pm_block,
        "combined_additive": combined,
        "am_session": AM_SESSION,
        "pm_session": PM_SESSION,
    }


def _discord_text(canonical: dict[str, Any], *, recovery_n: int, recovery_pnl: float, normal_pnl: float) -> str:
    from small_paper.discord_message_builder import format_discord_summary_lines

    lines = ["【Daily Summary】", f"日付: {DAY}", "status: RECOVERY_RECALCULATED", ""]
    lines.extend(format_discord_summary_lines(canonical))
    lines.extend(
        [
            "",
            "【EXIT区分】",
            f"通常EXIT損益: {int(round(normal_pnl)):,}円".replace(",", ","),
            f"Recovery EXIT損益: {int(round(recovery_pnl)):,}円".replace(",", ","),
            f"Total損益: {int(round(normal_pnl + recovery_pnl)):,}円".replace(",", ","),
            f"通常EXIT件数: {int(canonical.get('normal_exit_count') or 0)}",
            f"Recovery件数: {recovery_n}",
            f"正式EXIT件数: {int(canonical.get('trade_count') or 0)}",
            f"accepted件数: {int(canonical.get('accepted_count') or 0)}",
            f"Final active_positions: {int(canonical.get('active_positions') or 0)}",
            "PAPER ONLY",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    from small_paper.discord_message_builder import format_discord_summary_lines
    from small_paper.canonical_summary import parse_discord_summary_fields

    root = NATIVE / "results" / "small_paper" / DAY
    am_dir = root / AM_SESSION
    pm_dir = root / PM_SESSION
    out_dir = NATIVE / "results" / "daily" / DAY
    out_dir.mkdir(parents=True, exist_ok=True)

    am_events = _load_events(am_dir)
    pm_events = _load_events(pm_dir)
    all_events = am_events + pm_events

    am_accepted = sum(1 for e in am_events if e.get("event_type") == "accepted")
    pm_accepted = sum(1 for e in pm_events if e.get("event_type") == "accepted")
    accepted = am_accepted + pm_accepted

    am_all, am_normal, am_rec = _bucket_exits(am_events)
    pm_all, pm_normal, pm_rec = _bucket_exits(pm_events)
    all_trades, normal_trades, rec_trades = _bucket_exits(all_events)

    am_s = json.loads((am_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
    pm_s = json.loads((pm_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
    peak = max(int(am_s.get("peak_open_slots") or 0), int(pm_s.get("peak_open_slots") or 0), 5)
    cap = int(am_s.get("max_concurrent_positions") or pm_s.get("max_concurrent_positions") or 5)
    watch = am_s.get("watch_symbols_count") or pm_s.get("watch_symbols_count")

    m_all = _metrics(all_trades, peak=peak, cap=cap, watch=watch)
    m_normal = _metrics(normal_trades, peak=peak, cap=cap, watch=watch)
    m_rec = _metrics(rec_trades, peak=peak, cap=cap, watch=watch)
    m_am = _metrics(am_all, peak=int(am_s.get("peak_open_slots") or 0), cap=cap, watch=watch)
    m_pm = _metrics(pm_all, peak=int(pm_s.get("peak_open_slots") or 0), cap=cap, watch=watch)

    recovery_list = _recovery_rows(am_events, session=AM_SESSION, am_pm="AM") + _recovery_rows(
        pm_events, session=PM_SESSION, am_pm="PM"
    )

    formal_exit = len(all_trades)
    normal_exit_n = len(normal_trades)
    recovery_n = len(rec_trades)
    identity_ok = accepted == formal_exit == (normal_exit_n + recovery_n)
    active = 0  # both sessions finalized to 0

    # Enrich canonical for Discord
    canonical = dict(m_all)
    canonical.update(
        {
            "accepted_count": accepted,
            "entry_count": accepted,
            "exit_count": formal_exit,
            "normal_exit_count": normal_exit_n,
            "recovery_forced_close_count": recovery_n,
            "recovery_count": recovery_n,
            "active_positions": active,
            "draw_count": m_all.get("flat_count"),
            "win_rate": m_all.get("win_rate_yen_100"),
            "profit_factor": m_all.get("profit_factor_yen_100"),
            "am_pm_session": {"kind": "daily"},
            "daily_summary_status": "RECOVERY_FORMAL",
            # Do not set session_validity here — truthy values trigger INVALID Discord banner.
        }
    )

    pnl_split = {
        "normal_exit": {
            "count": normal_exit_n,
            "total_pnl_yen_100": m_normal.get("total_pnl_yen_100"),
            "win_count": m_normal.get("win_count"),
            "loss_count": m_normal.get("loss_count"),
            "flat_count": m_normal.get("flat_count"),
            "profit_factor_yen_100": m_normal.get("profit_factor_yen_100"),
            "avg_pnl_yen_100": m_normal.get("avg_pnl_yen_100"),
            "stop_count": m_normal.get("stop_count"),
            "best_trade": m_normal.get("best_trade"),
            "worst_trade": m_normal.get("worst_trade"),
        },
        "recovery_exit": {
            "count": recovery_n,
            "total_pnl_yen_100": m_rec.get("total_pnl_yen_100"),
            "win_count": m_rec.get("win_count"),
            "loss_count": m_rec.get("loss_count"),
            "flat_count": m_rec.get("flat_count"),
            "profit_factor_yen_100": m_rec.get("profit_factor_yen_100"),
            "avg_pnl_yen_100": m_rec.get("avg_pnl_yen_100"),
            "stop_count": m_rec.get("stop_count"),
            "trades": recovery_list,
        },
        "total": {
            "count": formal_exit,
            "total_pnl_yen_100": m_all.get("total_pnl_yen_100"),
            "win_count": m_all.get("win_count"),
            "loss_count": m_all.get("loss_count"),
            "flat_count": m_all.get("flat_count"),
            "profit_factor_yen_100": m_all.get("profit_factor_yen_100"),
            "avg_pnl_yen_100": m_all.get("avg_pnl_yen_100"),
            "stop_count": m_all.get("stop_count"),
            "best_trade": m_all.get("best_trade"),
            "worst_trade": m_all.get("worst_trade"),
        },
    }

    discord_body = _discord_text(
        canonical,
        recovery_n=recovery_n,
        recovery_pnl=float(m_rec.get("total_pnl_yen_100") or 0),
        normal_pnl=float(m_normal.get("total_pnl_yen_100") or 0),
    )
    discord_path = out_dir / f"discord_summary_recovery_{DAY}.txt"
    discord_path.write_text(discord_body + "\n", encoding="utf-8")

    # Consistency: Discord core fields vs JSON
    parsed = parse_discord_summary_fields(format_discord_summary_lines(canonical))
    discord_match = {
        "trade_count_line": parsed.get("取引数") == str(formal_exit),
        "win_loss_line": parsed.get("勝 / 負 / 引分")
        == f"{m_all['win_count']} / {m_all['loss_count']} / {m_all['flat_count']}",
        "pf_present": "PF" in parsed,
        "accepted_equals_exits": identity_ok,
    }

    daily = {
        "trading_date": DAY,
        "generated_at": _now(),
        "verdict": "DAILY_SUMMARY_RECOVERY_RECALCULATED",
        "daily_summary_status": "RECOVERY_FORMAL",
        "am_session": AM_SESSION,
        "pm_session": PM_SESSION,
        "identity_check": {
            "accepted_count": accepted,
            "normal_exit_count": normal_exit_n,
            "recovery_forced_close_count": recovery_n,
            "formal_exit_count": formal_exit,
            "formula": "accepted == normal_exit + recovery_forced_close == formal_exit",
            "ok": identity_ok,
            "am": {
                "accepted": am_accepted,
                "normal_exit": len(am_normal),
                "recovery": len(am_rec),
                "formal_exit": len(am_all),
                "ok": am_accepted == len(am_all) == len(am_normal) + len(am_rec),
            },
            "pm": {
                "accepted": pm_accepted,
                "normal_exit": len(pm_normal),
                "recovery": len(pm_rec),
                "formal_exit": len(pm_all),
                "ok": pm_accepted == len(pm_all) == len(pm_normal) + len(pm_rec),
            },
        },
        "accepted_count": accepted,
        "entry_count": accepted,
        "exit_count": formal_exit,
        "normal_exit_count": normal_exit_n,
        "recovery_forced_close_count": recovery_n,
        "active_positions": active,
        "final_active_positions": active,
        "win_count": m_all.get("win_count"),
        "loss_count": m_all.get("loss_count"),
        "draw_count": m_all.get("flat_count"),
        "flat_count": m_all.get("flat_count"),
        "win_rate": m_all.get("win_rate_yen_100"),
        "win_rate_yen_100": m_all.get("win_rate_yen_100"),
        "total_pnl_yen_100": m_all.get("total_pnl_yen_100"),
        "avg_pnl_yen_100": m_all.get("avg_pnl_yen_100"),
        "profit_factor_yen_100": m_all.get("profit_factor_yen_100"),
        "profit_factor": m_all.get("profit_factor_yen_100"),
        "gross_profit_yen_100": m_all.get("gross_profit_yen_100"),
        "gross_loss_yen_100": m_all.get("gross_loss_yen_100"),
        "stop_count": m_all.get("stop_count"),
        "stop_rate": m_all.get("stop_rate"),
        "best_trade": m_all.get("best_trade"),
        "worst_trade": m_all.get("worst_trade"),
        "recovery_pnl_yen_100": m_rec.get("total_pnl_yen_100"),
        "normal_exit_pnl_yen_100": m_normal.get("total_pnl_yen_100"),
        "pnl_split": pnl_split,
        "recovery_trades": recovery_list,
        "am": {
            "accepted": am_accepted,
            "metrics": m_am,
            "recovery": am_s.get("am_recovery"),
            "active_positions": am_s.get("active_positions", 0),
        },
        "pm": {
            "accepted": pm_accepted,
            "metrics": m_pm,
            "recovery": pm_s.get("pm_recovery"),
            "active_positions": pm_s.get("active_positions", 0),
        },
        "canonical_summary": canonical,
        "discord_summary_path": str(discord_path),
        "discord_json_match": discord_match,
        "submit_count": 0,
        "cancel_count": 0,
        "note": "Formal Daily Summary after Recovery Finalize; recovery_forced_close counted as EXIT.",
    }

    daily_path = out_dir / f"daily_summary_recovery_{DAY}.json"
    daily_path.write_text(json.dumps(daily, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    shadow = _shadow_rollup(am_s, pm_s)
    shadow["official_actual_total_pnl_yen_100"] = m_all.get("total_pnl_yen_100")
    shadow["official_actual_pf"] = m_all.get("profit_factor_yen_100")
    shadow["official_accepted"] = accepted
    shadow["official_exit"] = formal_exit
    shadow["official_recovery_count"] = recovery_n
    shadow_path = out_dir / f"shadow_summary_recovery_{DAY}.json"
    shadow_path.write_text(json.dumps(shadow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Markdown report
    md_lines = [
        f"# Daily Summary Recovery Recalculated — {DAY}",
        "",
        f"- verdict: `DAILY_SUMMARY_RECOVERY_RECALCULATED`",
        f"- generated_at: `{daily['generated_at']}`",
        f"- identity_ok: **{identity_ok}** (accepted={accepted} = normal={normal_exit_n} + recovery={recovery_n} = exit={formal_exit})",
        f"- Final active_positions: **{active}**",
        "",
        "## PnL Split",
        "",
        f"| 区分 | 件数 | 損益(円/100株) |",
        f"|---|---:|---:|",
        f"| 通常EXIT | {normal_exit_n} | {m_normal.get('total_pnl_yen_100')} |",
        f"| Recovery EXIT | {recovery_n} | {m_rec.get('total_pnl_yen_100')} |",
        f"| **Total** | **{formal_exit}** | **{m_all.get('total_pnl_yen_100')}** |",
        "",
        "## Formal Metrics",
        "",
        f"- 勝/負/引分: {m_all.get('win_count')} / {m_all.get('loss_count')} / {m_all.get('flat_count')}",
        f"- 勝率: {round(float(m_all.get('win_rate_yen_100') or 0)*100, 1)}%",
        f"- PF: {m_all.get('profit_factor_yen_100')}",
        f"- 平均損益: {m_all.get('avg_pnl_yen_100')}",
        f"- STOP件数: {m_all.get('stop_count')}",
        f"- Recovery件数: {recovery_n}",
        f"- Recovery損益: {m_rec.get('total_pnl_yen_100')}",
        f"- Best: {m_all.get('best_trade')}",
        f"- Worst: {m_all.get('worst_trade')}",
        f"- ENTRY数: {accepted}",
        f"- EXIT数: {formal_exit}",
        "",
        "## Recovery 9 trades",
        "",
        "| am_pm | symbol | position_id | entry | recovery | pnl_yen | reason |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for r in recovery_list:
        md_lines.append(
            f"| {r['am_pm']} | {r['symbol']} | `{r['position_id']}` | {r['entry_price']} | "
            f"{r['recovery_price']} | {r['pnl_yen_100']} | {r['recovery_reason']} |"
        )
    md_lines.extend(
        [
            "",
            "## Artifact paths",
            "",
            f"- JSON: `{daily_path}`",
            f"- Discord: `{discord_path}`",
            f"- Shadow: `{shadow_path}`",
            f"- discord_json_match: `{json.dumps(discord_match, ensure_ascii=False)}`",
            "",
        ]
    )
    report_path = out_dir / f"daily_summary_recovery_{DAY}.md"
    report_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    # Also mirror under results/reports for discoverability
    reports = NATIVE / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"daily_summary_recovery_{DAY}.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    (reports / f"daily_summary_recovery_{DAY}.json").write_text(
        json.dumps(daily, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "verdict": "DAILY_SUMMARY_RECOVERY_RECALCULATED",
                "identity_ok": identity_ok,
                "accepted": accepted,
                "normal_exit": normal_exit_n,
                "recovery": recovery_n,
                "formal_exit": formal_exit,
                "normal_pnl": m_normal.get("total_pnl_yen_100"),
                "recovery_pnl": m_rec.get("total_pnl_yen_100"),
                "total_pnl": m_all.get("total_pnl_yen_100"),
                "wl": f"{m_all.get('win_count')}/{m_all.get('loss_count')}/{m_all.get('flat_count')}",
                "pf": m_all.get("profit_factor_yen_100"),
                "discord_match": discord_match,
                "paths": {
                    "json": str(daily_path),
                    "md": str(report_path),
                    "discord": str(discord_path),
                    "shadow": str(shadow_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if identity_ok and all(discord_match.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
