"""
Phase276–277: Operator-readable Discord message text (no trading logic).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from replay.pnl_yen import (
    enrich_trade_pnl_yen,
    format_exit_pnl_line,
    format_pnl_yen_100_display,
    format_summary_avg_pnl_yen_100,
    format_summary_profit_factor_yen,
    format_summary_total_pnl_line,
    resolve_pnl_yen_100,
    summarize_pnl_yen_100,
)
from small_paper.reject_reasons import REJECT_MAX_CONCURRENT
from small_paper.order_latency_dryrun_trace import format_order_latency_dryrun_lines
from small_paper.discord_symbol_names import format_symbol_display, format_symbol_label
from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _feature_token

JST = ZoneInfo("Asia/Tokyo")

ENTRY_DEFERRED_MIN_SCORE_V2 = 5

EXTENDED_REASON_JA: dict[str, str] = {
    "rise_5min": "短期上昇が続いている",
    "vwap_dev": "VWAP上で推移",
    "rolling_mfe": "有利方向への推移が続いている",
    "high_break_recent": "5分高値更新",
}

SCORE_TOKEN_JA: dict[str, str] = {
    "HBRecent:no": "直近高値ブレイクなし（スコア加点）",
    "Duration:high": "継続時間が長い",
    "Momentum:low": "Momentum条件成立",
    "Board:mid": "Board mid以上",
    "Board:high": "Board mid以上",
    "Price:high": "価格帯が高め",
    "TV:mid": "出来高増加",
}

EXIT_REASON_JA: dict[str, str] = {
    "stop_hit": "損切り",
    "trailing_mfe_exit": "トレーリング決済",
    "no_progress_exit": "停滞ポジション整理",
    "momentum_fade_exit": "上昇継続条件消失",
    "price_momentum_fade_exit": "上昇継続条件消失",
    "favorable_fade_exit": "上昇継続条件消失",
    "quality_decay_exit": "継続品質の低下",
    "vwap_break_exit": "VWAP下抜け",
    "mfe_giveback_exit": "含み益の戻りが許容を超えた",
    "take_exit": "利確条件到達",
    "session_end": "セッション終了",
    "morning_session_close": "前場終了前の決済",
    "afternoon_session_close": "後場終了前の決済",
    "overlap_replaced_review": "同一銘柄の重複エントリー観測",
    "fade_watch_exit": "上昇継続条件消失",
    "fade_watch_breakdown": "上昇継続条件消失",
    "fade_watch_giveback": "上昇継続条件消失",
    "fade_watch_momentum_fade": "上昇継続条件消失",
    "fade_watch_session_close": "セッション終了",
    "fade_hybrid_breakdown": "上昇継続条件消失",
    "fade_hybrid_second_fade": "上昇継続条件消失",
    "fade_hybrid_structural_exit": "構造EXITへ移行",
}

PAPER_ONLY_FOOTER = "PAPER ONLY / 実注文なし"
TEST_FOOTER = "表示確認用 / 実取引ではありません"
DEFAULT_POSITION_CAP = 5

# Embed colors (legacy palette)
COLOR_ENTRY = 0x2F855A
COLOR_EXIT = 0xC05621  # Phase687W25C-R2: all EXIT reasons share legacy orange
COLOR_STOP = 0xE53E3E  # retained for non-EXIT callers / tests
COLOR_TRAILING = 0x3182CE
COLOR_NO_PROGRESS = 0xDD6B20
COLOR_SESSION_CLOSE = 0x718096
COLOR_CAP_BLOCKED = 0xDD6B20
COLOR_SUMMARY = 0x805AD5
COLOR_SHADOW = 0x718096

# Freshness: legacy ENTRY/EXIT always show 鮮度 section; warn highlight when abnormal
FRESHNESS_AGE_WARN_SEC = 10.0


def exit_embed_color(exit_reason: str = "") -> int:
    """Phase687W25C-R2: EXIT color is fixed legacy orange (reason ignored)."""
    del exit_reason
    return COLOR_EXIT


def format_exit_pnl_embed(pnl_pct: float, pnl_yen_100: Optional[float]) -> str:
    """Legacy-compatible EXIT pnl line (pct first, same as build_exit_detail)."""
    return format_exit_pnl_line(pnl_pct, pnl_yen_100)


def _freshness_abnormal(
    *,
    stale_trade: bool = False,
    price_age_sec: Optional[float] = None,
    board_age_sec: Optional[float] = None,
    market_time_age_sec: Optional[float] = None,
    price_freshness_source: Optional[str] = None,
) -> bool:
    if stale_trade:
        return True
    src = str(price_freshness_source or "").lower()
    if "stale" in src:
        return True
    for age in (price_age_sec, board_age_sec, market_time_age_sec):
        if age is not None:
            try:
                if float(age) >= FRESHNESS_AGE_WARN_SEC:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def build_entry_embed_payload(
    *,
    symbol: str,
    entry_price: float,
    slot_usage: str,
    entry_score_v2: Optional[int],
    data: Mapping[str, Any],
    name_map: Optional[Mapping[str, str]] = None,
    entry_time: Optional[str] = None,
    stop_price: Optional[float] = None,
    score5_candidate_ordinal: Optional[int] = None,
    reentry_info: Optional[Mapping[str, Any]] = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Legacy ENTRY embed + times + optional same-day re-entry block (W25C-R3)."""
    display = format_symbol_display(symbol, name_map=name_map)
    route = resolve_entry_route(data)
    title = f"【ENTRY】{display}"
    if test_mode:
        title = f"【TEST】{title}"
    try:
        from small_paper.discord_current_system_summary import (
            render_entry_quantity_line,
            resolve_entry_quantity,
        )

        qty = resolve_entry_quantity(data, config=None)
        # Paper lot default when payload omits qty (official ENTRY must show a qty line)
        if qty is None:
            qty = resolve_entry_quantity(data, config={"paper_quantity": 100})
        qty_line = render_entry_quantity_line(qty)
    except Exception:
        qty_line = "qty: n/a"
    desc_lines = [
        f"エントリー時間: {format_time_hms_jst(entry_time)}",
        f"ENTRY価格: {_fmt_price_yen(entry_price)}",
        qty_line,
    ]
    if stop_price is not None:
        desc_lines.append(f"損切り価格: {_fmt_price_yen(stop_price)}")
    desc_lines.extend(
        [
            f"保有枠: {slot_usage}",
            f"ENTRY方式: {route}",
        ]
    )
    # Same-day re-entry visibility (2nd+ only)
    ri = dict(reentry_info or {})
    if not ri and data.get("reentry_info"):
        ri = dict(data.get("reentry_info") or {})  # type: ignore[arg-type]
    if ri.get("is_reentry") and int(ri.get("entry_count_today_after") or 0) >= 2:
        desc_lines.extend(
            [
                "",
                f"本日同銘柄ENTRY: {int(ri['entry_count_today_after'])}回目",
                f"前回EXIT: {ri.get('previous_exit_reason_ja') or humanize_exit_reason(str(ri.get('previous_exit_reason') or ''))}",
                f"前回EXIT時刻: {ri.get('previous_exit_time_hms') or format_time_hms_jst(ri.get('previous_exit_at'))}",
                f"前回EXITから: {ri.get('previous_exit_elapsed') or 'N/A'}",
                f"前回EXIT価格: {_fmt_price_yen(ri.get('previous_exit_price'))}",
            ]
        )
    desc_lines.extend(
        [
            "",
            f"entry_score_v2: {entry_score_v2 if entry_score_v2 is not None else 'N/A'}",
        ]
    )
    if score5_candidate_ordinal is not None and entry_score_v2 is not None and entry_score_v2 >= 5:
        desc_lines.append(f"本日score5候補: {score5_candidate_ordinal}件目")
    if route == "OR" and data.get("or_reason"):
        desc_lines.append(f"OR理由: {data.get('or_reason')}")

    reason_data = dict(data)
    if entry_score_v2 is not None:
        reason_data.setdefault("entry_expectancy_score_v2", entry_score_v2)
    fields: list[dict[str, Any]] = [
        {
            "name": "ENTRY理由",
            "value": format_entry_reason_block(reason_data)[:1020] or "・条件成立",
            "inline": False,
        }
    ]
    # Freshness only when abnormal (R3); avoid duplicating event_time / audit keys
    price_age = None
    board_age = None
    try:
        if data.get("price_age_sec") is not None:
            price_age = float(data.get("price_age_sec"))
    except (TypeError, ValueError):
        price_age = None
    try:
        if data.get("board_age_sec") is not None:
            board_age = float(data.get("board_age_sec"))
    except (TypeError, ValueError):
        board_age = None
    stale = data.get("stale_trade") in (True, "true", "True", 1)
    src = str(data.get("price_freshness_source") or data.get("price_source") or "")
    if _freshness_abnormal(
        stale_trade=stale,
        price_age_sec=price_age,
        board_age_sec=board_age,
        price_freshness_source=src,
    ):
        warn: list[str] = []
        if price_age is not None and price_age >= FRESHNESS_AGE_WARN_SEC:
            warn.append(f"⚠ 価格更新なし: {format_hold_duration(float(price_age) / 60.0)}")
        if board_age is not None and board_age >= FRESHNESS_AGE_WARN_SEC:
            warn.append(f"⚠ 板更新なし: {format_hold_duration(float(board_age) / 60.0)}")
        if stale or "stale" in src.lower():
            warn.append("stale tradeは警告でありrejectではない")
        if not warn:
            warn.append("⚠ 価格データが古い状態")
        fields.append({"name": "警告", "value": "\n".join(warn)[:1020], "inline": False})
    return {
        "title": title,
        "description": "\n".join(desc_lines)[:2048],
        "color": COLOR_ENTRY,
        "fields": fields,
        "footer": TEST_FOOTER if test_mode else PAPER_ONLY_FOOTER,
    }


def build_exit_embed_payload(
    *,
    symbol: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    mfe_pct: Optional[float],
    mae_pct: Optional[float],
    hold_minutes: float,
    exit_reason: str,
    pnl_yen_100: Optional[float] = None,
    side: str = "long",
    board_dynamic_trailing_tier: Optional[str] = None,
    board_dynamic_trailing_activate_pct: Optional[float] = None,
    board_dynamic_trailing_giveback_frac: Optional[float] = None,
    name_map: Optional[Mapping[str, str]] = None,
    entry_time: Optional[str] = None,
    exit_time: Optional[str] = None,
    market_time_age_sec: Optional[float] = None,
    price_age_sec: Optional[float] = None,
    board_age_sec: Optional[float] = None,
    stale_trade: bool = False,
    price_freshness_source: Optional[str] = None,
    session_close: bool = False,
    position_cap_mode: bool = False,
    symbol_pnl_yen_100_today: Optional[float] = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Legacy EXIT embed + times + same-day symbol cumulative (W25C-R3)."""
    yen = resolve_pnl_yen_100(
        entry_price=entry_price,
        exit_price=exit_price,
        side=side,
        pnl_yen_100=pnl_yen_100,
    )
    display = format_symbol_display(symbol, name_map=name_map)
    title = f"【EXIT】{display}"
    if test_mode:
        title = f"【TEST】{title}"
    desc_lines = [
        f"エントリー時間: {format_time_hms_jst(entry_time)}",
        f"EXIT時間: {format_time_hms_jst(exit_time)}",
        f"ENTRY価格: {_fmt_price_yen(entry_price)}",
        f"EXIT価格: {_fmt_price_yen(exit_price)}",
        format_exit_pnl_line(pnl_pct, yen).replace("円(100株)", "円（100株）"),
    ]
    if symbol_pnl_yen_100_today is not None:
        try:
            cum = float(symbol_pnl_yen_100_today)
            sign = "+" if cum >= 0 else ""
            desc_lines.append(f"本日同銘柄累計: {sign}{int(round(cum)):,}円（100株換算）")
        except (TypeError, ValueError):
            pass
    desc_lines.extend(
        [
            f"保有時間: {format_hold_duration(hold_minutes)}",
            f"EXIT理由: {humanize_exit_reason(exit_reason)}",
            "",
            f"最大含み益 MFE: {_fmt_na(mfe_pct, digits=2)}%",
            f"最大逆行 MAE: {_fmt_na(mae_pct, digits=2)}%",
        ]
    )
    if "trailing_mfe" in str(exit_reason or ""):
        desc_lines.extend(
            format_board_dynamic_trailing_lines(
                board_dynamic_trailing_tier=board_dynamic_trailing_tier,
                board_dynamic_trailing_activate_pct=board_dynamic_trailing_activate_pct,
                board_dynamic_trailing_giveback_frac=board_dynamic_trailing_giveback_frac,
            )
        )
    if position_cap_mode and session_close:
        desc_lines.append("Session close: CAP枠解放")
    fields: list[dict[str, Any]] = []
    # Abnormal freshness only
    if _freshness_abnormal(
        stale_trade=stale_trade,
        price_age_sec=price_age_sec,
        board_age_sec=board_age_sec,
        market_time_age_sec=market_time_age_sec,
        price_freshness_source=price_freshness_source,
    ):
        lag = market_time_age_sec if market_time_age_sec is not None else price_age_sec
        warn_lines: list[str] = []
        if lag is not None:
            warn_lines.append(f"⚠ 価格更新なし: {format_hold_duration(float(lag) / 60.0)}")
        if stale_trade or (price_freshness_source and "stale" in str(price_freshness_source).lower()):
            warn_lines.append("stale tradeは警告でありrejectではない")
        if is_stop_low_mfe_exit(exit_reason, mfe_pct):
            warn_lines.append(f"⚠ stop_low_mfe: MFE<{STOP_LOW_MFE_THRESHOLD_PCT:.1f}% at stop")
        if warn_lines:
            fields.append({"name": "警告", "value": "\n".join(warn_lines)[:1020], "inline": False})
    return {
        "title": title,
        "description": "\n".join(desc_lines)[:2048],
        "color": COLOR_EXIT,
        "fields": fields,
        "footer": TEST_FOOTER if test_mode else PAPER_ONLY_FOOTER,
    }


def build_cap_blocked_embed_payload(
    *,
    symbol: str,
    entry_score_v2: Optional[int],
    data: Mapping[str, Any],
    active_positions: int,
    position_cap: int,
    name_map: Optional[Mapping[str, str]] = None,
    block_reason: str = REJECT_MAX_CONCURRENT,
    test_mode: bool = False,
) -> dict[str, Any]:
    display = format_symbol_display(symbol, name_map=name_map)
    route = resolve_entry_route(data)
    title = f"【CAP BLOCKED】{display}"
    if test_mode:
        title = f"【TEST】{title}"
    bullets = build_entry_reason_bullets(data)
    reason_block = "\n".join(f"・{b}" for b in bullets) if bullets else "・（なし）"
    desc = "\n".join(
        [
            "ENTRY条件成立",
            f"方式: {route}",
            f"保有: {active_positions} / {int(position_cap) if position_cap else DEFAULT_POSITION_CAP}",
            "見送り理由: 保有上限到達",
            f"score: {entry_score_v2 if entry_score_v2 is not None else 'N/A'}",
        ]
    )
    return {
        "title": title,
        "description": desc[:2048],
        "color": COLOR_CAP_BLOCKED,
        "fields": [{"name": "ENTRY理由", "value": reason_block[:1020], "inline": False}],
        "footer": TEST_FOOTER if test_mode else PAPER_ONLY_FOOTER,
    }


def build_summary_embed_payload(
    metrics: Mapping[str, Any],
    *,
    am_pm: str = "",
    test_mode: bool = False,
    day_realized_pnl_yen_100: Optional[float] = None,
    reentry_audit: Optional[Mapping[str, Any]] = None,
    research_highlights: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    label = "AM PAPER SUMMARY" if str(am_pm).upper() == "AM" else (
        "PM PAPER SUMMARY" if str(am_pm).upper() == "PM" else "PAPER SUMMARY"
    )
    title = f"【{label}】"
    if test_mode:
        title = f"【TEST】{title}"
    validity = str(metrics.get("session_validity") or "")
    if validity.startswith("INVALID_") or metrics.get("include_in_strategy_metrics") is False:
        title = f"【INVALID PAPER SESSION】{title}"
    win_c = int(metrics.get("win_count") or 0)
    loss_c = int(metrics.get("loss_count") or 0)
    draw_c = int(metrics.get("draw_count") or metrics.get("flat_count") or 0)
    pf = metrics.get("profit_factor_yen_100", metrics.get("profit_factor"))
    pf_s = format_summary_profit_factor_yen(pf)
    if str(pf_s).lower() == "inf":
        pf_s = "∞"
    session_yen = _format_summary_yen_value(metrics.get("total_pnl_yen_100"))
    session_yen = (
        str(session_yen).replace("円(100株)", "円").replace("円（100株）", "円")
    )
    session_label = "AM損益" if str(am_pm).upper() == "AM" else (
        "PM損益" if str(am_pm).upper() == "PM" else "セッション損益"
    )
    desc_lines = []
    # Phase687W60: Daily only — TODAY'S RESEARCH before Actual metrics
    is_daily = str(am_pm or "").upper() not in ("AM", "PM")
    hl = list(research_highlights or [])
    if is_daily and hl:
        desc_lines.extend(hl)
        desc_lines.append("")
    if validity.startswith("INVALID_") or metrics.get("include_in_strategy_metrics") is False:
        from small_paper.session_validity import format_invalid_session_discord_lines, classify_session_validity

        v = metrics if metrics.get("discord_banner") else classify_session_validity(metrics)
        desc_lines.extend(format_invalid_session_discord_lines(v))
        desc_lines.append("")
    desc_lines.append(f"{session_label}: {session_yen}")
    day_yen = day_realized_pnl_yen_100
    if day_yen is None:
        day_yen = metrics.get("day_total_pnl_yen_100")
    if day_yen is not None:
        try:
            dv = float(day_yen)
            sign = "+" if dv >= 0 else ""
            desc_lines.append(f"本日累計: {sign}{int(round(dv)):,}円")
        except (TypeError, ValueError):
            pass
    desc_lines.extend(
        [
            "",
            f"取引数: {metrics.get('trade_count', 0)}",
            f"勝 / 負 / 引分: {win_c} / {loss_c} / {draw_c}",
            f"PF: {pf_s}",
        ]
    )
    fields: list[dict[str, Any]] = []
    audit = dict(reentry_audit or {})
    if not audit and isinstance(metrics.get("reentry_audit"), Mapping):
        audit = dict(metrics.get("reentry_audit") or {})  # type: ignore[arg-type]
    if audit:
        fields.append(
            {
                "name": "再ENTRY監査",
                "value": "\n".join(
                    [
                        f"同一銘柄再ENTRY: {int(audit.get('same_symbol_reentry_count') or 0)}件",
                        f"停滞EXIT後再ENTRY: {int(audit.get('reentry_after_no_progress_count') or 0)}件",
                        f"same-PUSH抑止: {int(audit.get('same_push_suppression_count') or 0)}件",
                    ]
                )[:1020],
                "inline": False,
            }
        )
    stop_n = metrics.get("stop_count", "N/A")
    np_n = metrics.get("no_progress_exit_count", metrics.get("stagnation_exit_count"))
    lower = [f"STOP: {stop_n}"]
    if np_n is not None:
        lower.append(f"停滞EXIT: {np_n}")
    lower.extend(
        [
            f"Best: {_canonical_trade_display(metrics, 'best_trade')}",
            f"Worst: {_canonical_trade_display(metrics, 'worst_trade')}",
            (
                f"ピーク保有 / CAP: "
                f"{metrics.get('max_concurrent', 0)} / "
                f"{metrics.get('max_concurrent_cap', DEFAULT_POSITION_CAP)}"
            ),
        ]
    )
    fields.append({"name": "内訳", "value": "\n".join(lower)[:1020], "inline": False})
    return {
        "title": title,
        "description": "\n".join(desc_lines)[:2048],
        "color": COLOR_SUMMARY,
        "fields": fields,
        "footer": TEST_FOOTER if test_mode else PAPER_ONLY_FOOTER,
    }


def build_shadow_observation_embed_payload(
    data: Mapping[str, Any],
    *,
    am_pm: str = "",
    test_mode: bool = False,
) -> dict[str, Any]:
    """Active Shadow observation card (no actual PnL). Heading: 【SHADOW OBSERVATION】."""
    title = "【SHADOW OBSERVATION】"
    if am_pm:
        title = f"【SHADOW OBSERVATION - {str(am_pm).upper()}】"
    if test_mode:
        title = f"【TEST】{title}"
    blocks = data.get("blocks")
    if blocks is None:
        blocks = data.get("block_count")
    if blocks is None:
        blocks = data.get("candidates")
    delta = data.get("delta_yen")
    if delta is None:
        delta = data.get("hypothetical_pnl")
    name = data.get("shadow_name") or "N/A"
    # Multi-shadow body: list of active rows if provided
    active_rows = data.get("active_shadows")
    if isinstance(active_rows, Sequence) and active_rows and not isinstance(active_rows, (str, bytes)):
        lines = []
        for row in active_rows:
            if not isinstance(row, Mapping):
                continue
            pf = row.get("pf_delta")
            pf_s = "N/A" if pf in (None, "") else str(pf)
            lines.extend(
                [
                    f"名称: {row.get('name')}",
                    f"対象件数: {row.get('count', 0)}",
                    f"block件数: {row.get('block_count', row.get('count', 0))}",
                    f"delta円: {row.get('delta', 'N/A')}",
                    f"PF差: {pf_s}",
                    "判定: observation only",
                    "",
                ]
            )
        desc = "\n".join(lines).rstrip()
    else:
        pf = data.get("pf_delta")
        desc = "\n".join(
            [
                f"名称: {name}",
                f"対象件数: {blocks if blocks is not None else 'N/A'}",
                f"block件数: {data.get('block_count', blocks if blocks is not None else 'N/A')}",
                f"delta円: {delta if delta is not None else 'N/A'}",
                f"PF差: {pf if pf is not None else 'N/A'}",
                "判定: observation only",
            ]
        )
    return {
        "title": title,
        "description": desc[:2048],
        "color": COLOR_SHADOW,
        "fields": [],
        "footer": TEST_FOOTER if test_mode else "observation only / RESEARCH",
    }


# Runtime Discord Shadow catalog (formatter inventory vs enabled+count filter)
DISCORD_SHADOW_INVENTORY: tuple[dict[str, str], ...] = (
    {
        "name": "Rise5",
        "enabled_key": "pbv2_rise5_shadow_enabled",
        "count_key": "pbv2_rise5_shadow_block_count",
        "delta_key": "pbv2_rise5_shadow_net_effect_yen",
    },
    {
        "name": "Flat-band",
        "enabled_key": "pbv2_flat_band_shadow_enabled",
        "count_key": "pbv2_flat_band_shadow_block_count",
        "delta_key": "pbv2_flat_band_shadow_net_effect_yen",
    },
    {
        "name": "PullbackMisread",
        "enabled_key": "pullback_misread_guard_shadow_enabled",
        "count_key": "pullback_misread_guard_shadow_blocked_count",
        "delta_key": "pullback_misread_guard_shadow_delta_yen",
    },
    {
        "name": "BoardDynamic",
        "enabled_key": "board_dynamic_shadow_enabled",
        "count_key": "board_dynamic_shadow_exit_count",
        "delta_key": "board_dynamic_shadow_total_delta_yen",
    },
    {
        "name": "EXIT monitor",
        "enabled_key": "exit_shadow_monitor_enabled",
        "count_key": "exit_shadow_monitor_event_count",
        "delta_key": "shadow_exit_t3_delta",
    },
)


def _shadow_target_count(summary: Mapping[str, Any], spec: Mapping[str, str]) -> int:
    """evaluable_count / target_count / trade_count / block count — any >= 1."""
    keys = [
        spec.get("count_key") or "",
        spec.get("enabled_key", "").replace("_enabled", "_evaluable_count"),
        spec.get("enabled_key", "").replace("_enabled", "_target_count"),
        spec.get("enabled_key", "").replace("_enabled", "_trade_count"),
        spec.get("enabled_key", "").replace("_enabled", "_block_count"),
    ]
    best = 0
    for k in keys:
        if not k:
            continue
        best = max(best, _as_int(summary.get(k)))
    # explicit alternate keys
    name = spec.get("name")
    if name == "BoardDynamic":
        best = max(
            best,
            _as_int(summary.get("board_dynamic_shadow_exit_count")),
            _as_int(summary.get("board_dynamic_shadow_improved_count")),
        )
    if name == "EXIT monitor":
        best = max(best, _as_int(summary.get("exit_shadow_monitor_event_count")))
        if summary.get("shadow_exit_t3_delta") is not None or summary.get("shadow_exit_t2_delta") is not None:
            best = max(best, 1)
    return best


def collect_active_shadow_observations(
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """enabled=true AND today's target/evaluable/trade/block count >= 1. No actual PnL."""
    active: list[dict[str, Any]] = []
    for spec in DISCORD_SHADOW_INVENTORY:
        if not summary.get(spec["enabled_key"]):
            continue
        count = _shadow_target_count(summary, spec)
        if count <= 0:
            continue
        delta = summary.get(spec["delta_key"])
        if spec["name"] == "EXIT monitor" and delta is None:
            delta = summary.get("shadow_exit_t2_delta")
        # Hide outcome-mapping-unavailable zero-delta masquerading as measured
        if delta is not None:
            try:
                if float(delta) == 0.0 and summary.get(
                    f"{spec['enabled_key'].replace('_enabled', '')}_outcome_mapping_unavailable"
                ):
                    continue
            except (TypeError, ValueError):
                pass
        pf_key = spec["enabled_key"].replace("_enabled", "_pf_delta")
        active.append(
            {
                "name": spec["name"],
                "count": count,
                "block_count": _as_int(summary.get(spec["count_key"])) or count,
                "delta": _yen_display(delta) if delta is not None else "N/A",
                "pf_delta": summary.get(pf_key),
                "enabled_key": spec["enabled_key"],
            }
        )
    return active


def audit_discord_shadow_inventory(
    summary: Mapping[str, Any],
    *,
    runtime_enabled: Optional[Mapping[str, bool]] = None,
) -> dict[str, Any]:
    """Compare formatter inventory vs runtime enabled flags and active filter."""
    active_names = {r["name"] for r in collect_active_shadow_observations(summary)}
    rows: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for spec in DISCORD_SHADOW_INVENTORY:
        enabled_summary = bool(summary.get(spec["enabled_key"]))
        enabled_runtime = None
        if runtime_enabled is not None:
            enabled_runtime = bool(runtime_enabled.get(spec["enabled_key"]))
        count = _shadow_target_count(summary, spec)
        visible = spec["name"] in active_names
        reason = ""
        if not enabled_summary:
            reason = "disabled"
        elif count <= 0:
            reason = "target_count_zero"
        elif not visible:
            reason = "filtered_outcome_or_inactive"
        rows.append(
            {
                "name": spec["name"],
                "enabled_key": spec["enabled_key"],
                "enabled_in_summary": enabled_summary,
                "enabled_in_runtime": enabled_runtime,
                "count": count,
                "discord_visible": visible,
                "hidden_reason": "" if visible else reason,
            }
        )
        if not visible:
            hidden.append({"name": spec["name"], "reason": reason or "not_displayed"})
    visible = [r for r in rows if r["discord_visible"]]
    mismatched = [
        r
        for r in rows
        if runtime_enabled is not None
        and r["enabled_in_runtime"] is not None
        and r["enabled_in_summary"] != r["enabled_in_runtime"]
    ]
    return {
        "inventory": rows,
        "visible_count": len(visible),
        "visible_names": [r["name"] for r in visible],
        "hidden": hidden,
        "mismatched_runtime": mismatched,
        "verdict": "SHADOW_INVENTORY_OUTDATED" if mismatched else "SHADOW_INVENTORY_OK",
    }


def write_shadow_inventory_csvs(
    summary: Mapping[str, Any],
    *,
    out_dir: Any,
    runtime_enabled: Optional[Mapping[str, bool]] = None,
) -> dict[str, Any]:
    """Write enabled / displayed / hidden shadow inventory CSVs for R3 audit."""
    from pathlib import Path
    import csv

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    audit = audit_discord_shadow_inventory(summary, runtime_enabled=runtime_enabled)
    active = collect_active_shadow_observations(summary)

    enabled_path = out / "enabled_shadow_inventory.csv"
    with enabled_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["name", "enabled_key", "enabled_in_summary", "enabled_in_runtime", "count"],
        )
        w.writeheader()
        for row in audit["inventory"]:
            if row["enabled_in_summary"] or row.get("enabled_in_runtime"):
                w.writerow(
                    {
                        "name": row["name"],
                        "enabled_key": row["enabled_key"],
                        "enabled_in_summary": row["enabled_in_summary"],
                        "enabled_in_runtime": row["enabled_in_runtime"],
                        "count": row["count"],
                    }
                )

    displayed_path = out / "displayed_shadow_inventory.csv"
    with displayed_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "count", "block_count", "delta", "pf_delta"])
        w.writeheader()
        for row in active:
            w.writerow(
                {
                    "name": row["name"],
                    "count": row["count"],
                    "block_count": row.get("block_count"),
                    "delta": row.get("delta"),
                    "pf_delta": row.get("pf_delta"),
                }
            )

    hidden_path = out / "hidden_shadow_reason.csv"
    with hidden_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "reason"])
        w.writeheader()
        for row in audit.get("hidden") or []:
            w.writerow(row)

    return {
        "audit": audit,
        "enabled_path": str(enabled_path),
        "displayed_path": str(displayed_path),
        "hidden_path": str(hidden_path),
    }


def embed_to_discord_payload(embed: Mapping[str, Any], *, content: str = "") -> dict[str, Any]:
    """Build Discord webhook JSON (content empty for production cards)."""
    body = {
        "embeds": [
            {
                "title": str(embed.get("title") or "")[:256],
                "description": str(embed.get("description") or "")[:2048],
                "color": int(embed.get("color") or COLOR_ENTRY),
                "fields": list(embed.get("fields") or [])[:25],
                "footer": {"text": str(embed.get("footer") or PAPER_ONLY_FOOTER)[:2048]},
            }
        ]
    }
    if content:
        body["content"] = str(content)[:1800]
    return body


def _fmt_na(v: Any, *, digits: Optional[int] = None) -> str:
    if v is None or v == "":
        return "N/A"
    if digits is None:
        return str(v)
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_price_yen(v: Any) -> str:
    if v is None or v == "":
        return "N/A"
    try:
        f = float(v)
        # Do not display fabricated 0円 for missing official ENTRY price
        if f <= 0:
            return "N/A"
        if abs(f - round(f)) < 1e-9:
            return f"{int(round(f))}円"
        return f"{f:.1f}円"
    except (TypeError, ValueError):
        return f"{v}円"


def format_hold_duration(hold_minutes: float) -> str:
    total_sec = max(0, int(round(float(hold_minutes) * 60.0)))
    mins, secs = divmod(total_sec, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}時間{mins:02d}分{secs:02d}秒"
    return f"{mins}分{secs:02d}秒"


def resolve_entry_route(data: Mapping[str, Any]) -> str:
    raw = str(data.get("entry_type") or data.get("entry_route") or data.get("entry_pool") or "").strip()
    up = raw.upper()
    if "OR" in up:
        return "OR"
    if up in ("PBV2", "PB_V2", "PULLBACK_V2", "PBV2"):
        return "PBv2"
    if not raw:
        return "PBv2"
    return raw


def resolve_momentum_label(data: Mapping[str, Any]) -> str:
    tok = _feature_token("Momentum", data)
    if tok and ":" in tok:
        return tok.split(":", 1)[1]
    for key in ("momentum_band", "momentum_label", "Momentum"):
        if data.get(key) not in (None, ""):
            return str(data.get(key))
    return "N/A"


def resolve_board_label(data: Mapping[str, Any]) -> str:
    tok = _feature_token("Board", data)
    if tok and ":" in tok:
        return tok.split(":", 1)[1]
    for key in ("board_band", "board_label", "Board"):
        if data.get(key) not in (None, ""):
            return str(data.get(key))
    return "N/A"


def _fmt_num(v: Any, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _int_score(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def format_slot_usage(open_slots: int, max_slots: int) -> str:
    cap = max(1, int(max_slots))
    used = max(0, min(int(open_slots), cap))
    return f"{used}/{cap}"


def format_position_slot_pair(
    pre_count: Optional[int],
    post_count: int,
    max_slots: int,
) -> str:
    """Discord ENTRY slot line: pre→post/max (post_count is after register)."""
    cap = max(1, int(max_slots))
    post = max(0, min(int(post_count), cap))
    if pre_count is None:
        return format_slot_usage(post, cap)
    pre = max(0, min(int(pre_count), cap))
    if pre == post:
        return f"{pre}/{cap}"
    return f"{pre}→{post}/{cap}"


def append_discord_delivery_audit_lines(
    lines: list[str],
    *,
    event_time: Optional[str] = None,
    sent_time: Optional[str] = None,
    generated_at: Optional[str] = None,
    session_id: Optional[str] = None,
    position_id: Optional[str] = None,
    sequence_id: Optional[int] = None,
) -> None:
    if generated_at:
        lines.append(f"generated_at: {format_time_hms_jst(generated_at)}")
    if event_time:
        lines.append(f"event_time: {format_time_hms_jst(event_time)}")
    if sent_time:
        lines.append(f"sent_time: {format_time_hms_jst(sent_time)}")
    if session_id:
        lines.append(f"session_id: {session_id}")
    if position_id:
        lines.append(f"position_id: {position_id}")
    if sequence_id is not None:
        lines.append(f"sequence_id: {sequence_id}")


def _symbol_short(sym: str) -> str:
    s = str(sym or "").strip().upper()
    return s.replace(".T", "") if s else "—"


def format_time_hms_jst(ts: Any) -> str:
    """Format ISO timestamp to JST HH:MM:SS for Discord notifications."""
    s = str(ts or "").strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        else:
            dt = dt.astimezone(JST)
        return dt.strftime("%H:%M:%S")
    except ValueError:
        if "T" in s:
            tail = s.split("T", 1)[1]
            for sep in ("+", "-"):
                if sep in tail:
                    tail = tail.split(sep, 1)[0]
            return tail[:8] if len(tail) >= 8 else tail
        return s[:8] if len(s) >= 8 else s


def humanize_exit_reason(reason: str) -> str:
    r = str(reason or "").strip()
    if not r:
        return "決済（理由不明）"
    if r in EXIT_REASON_JA:
        return EXIT_REASON_JA[r]
    if "fade" in r.lower():
        return "上昇継続条件消失"
    if "trailing_mfe" in r:
        return "トレーリング決済"
    if r in ("stop_hit", "hard_stop"):
        return "損切り"
    return "決済条件を満たした"


def build_entry_reason_bullets(data: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    raw = str(data.get("extended_entry_shadow_reasons") or "")
    for part in raw.split(";"):
        key = part.strip()
        if key and key in EXTENDED_REASON_JA:
            lines.append(EXTENDED_REASON_JA[key])

    vwap_dev = data.get("entry_vwap_dev_pct")
    if vwap_dev is not None:
        try:
            if float(vwap_dev) > 0 and "VWAP上で推移" not in lines:
                lines.append("VWAP上で推移")
        except (TypeError, ValueError):
            pass

    if data.get("entry_high_break_recent") in (True, "true", "True", 1):
        if "5分高値更新" not in lines:
            lines.append("5分高値更新")

    tv = data.get("trading_value")
    if tv is not None:
        try:
            if float(tv) > 0 and "出来高増加" not in lines:
                lines.append("出来高増加")
        except (TypeError, ValueError):
            pass

    for token in SCORE_POINTS_V2:
        lbl = token.split(":", 1)[0]
        if _feature_token(lbl, data) == token:
            ja = SCORE_TOKEN_JA.get(token)
            if ja and ja not in lines:
                lines.append(ja)

    # Explicit reason tokens from notify payload / preview
    raw_tokens = data.get("entry_reason_tokens") or data.get("reason_tokens") or []
    if isinstance(raw_tokens, str):
        raw_tokens = [t.strip() for t in raw_tokens.split(";") if t.strip()]
    for token in raw_tokens:
        ja = SCORE_TOKEN_JA.get(str(token))
        if not ja:
            # allow already-Japanese or Board mid以上 style labels
            t = str(token)
            if t in ("Board mid以上", "Board:mid", "Board mid"):
                ja = "Board条件成立"
            elif t.startswith("Momentum"):
                ja = "Momentum条件成立"
            else:
                ja = t if any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in t) else ""
        if ja and ja not in lines:
            lines.append(ja)

    if not lines:
        q = data.get("continuation_quality_score")
        if q is not None:
            lines.append("継続品質がエントリー基準を満たす")
        else:
            lines.append("エントリー監視条件を満たす")

    return lines


def format_entry_reason_block(data: Mapping[str, Any]) -> str:
    bullets = build_entry_reason_bullets(data)
    v2 = _int_score(data.get("entry_expectancy_score_v2") or data.get("entry_score_v2"))
    if v2 is not None and "score閾値到達" not in bullets and "score_v2閾値到達" not in bullets:
        bullets.append("score閾値到達")
    # normalize legacy phrasing
    bullets = [b.replace("score_v2閾値到達", "score閾値到達").replace("Board mid以上", "Board条件成立") for b in bullets]
    body = "\n".join(f"・{b}" for b in bullets)
    return body


def format_entry_deferred_reason_block() -> str:
    return "保有枠上限のため見送り"


def _score_label(v2: Optional[int]) -> str:
    if v2 is None:
        return "score—"
    return f"score{v2}"


def format_open_positions_lines(holdings: Sequence[Mapping[str, Any]]) -> str:
    if not holdings:
        return "（なし）"
    parts: list[str] = []
    for h in holdings:
        code = str(h.get("symbol_short") or _symbol_short(str(h.get("symbol", ""))))
        pnl = h.get("unrealized_pnl_pct")
        v2 = _int_score(h.get("entry_score_v2"))
        hold_min = h.get("hold_minutes")
        if pnl is None:
            parts.append(code)
            continue
        sign = "+" if float(pnl) >= 0 else ""
        line = f"{code} {sign}{float(pnl):.1f}% {_score_label(v2)}"
        if hold_min is not None:
            line += f" {int(round(float(hold_min)))}分"
        parts.append(line)
    return "\n".join(parts)


DISCORD_EMBED_FIELD_MAX = 1020
WATCH_SYMBOLS_PER_EMBED_CHUNK = 10


def format_watch_symbols_block(
    symbols: Sequence[str],
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """One symbol per line: ``01. 7203 トヨタ自動車``."""
    if not symbols:
        return "（なし）"
    lines = [
        f"{i:02d}. {format_symbol_label(sym, name_map)}"
        for i, sym in enumerate(symbols, 1)
    ]
    return "\n".join(lines)


def format_added_symbols_block(
    symbols: Sequence[str],
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    if not symbols:
        return "（なし）"
    return "\n".join(f"+ {format_symbol_label(s, name_map)}" for s in symbols)


def format_removed_symbols_block(
    symbols: Sequence[str],
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    if not symbols:
        return "（なし）"
    return "\n".join(f"- {format_symbol_label(s, name_map)}" for s in symbols)


def split_watch_symbols_discord_fields(
    symbols: Sequence[str],
    *,
    name_map: Optional[Mapping[str, str]] = None,
    per_chunk: int = WATCH_SYMBOLS_PER_EMBED_CHUNK,
    max_value_len: int = DISCORD_EMBED_FIELD_MAX,
) -> list[dict[str, str]]:
    """Split numbered watch list into Discord embed fields (e.g. 監視銘柄一覧 1/4)."""
    syms = list(symbols)
    if not syms:
        return [{"name": "監視銘柄一覧", "value": "（なし）"}]

    per = max(1, int(per_chunk))

    def _build(per_n: int) -> Optional[list[dict[str, str]]]:
        ranges: list[tuple[int, int]] = []
        for lo in range(0, len(syms), per_n):
            ranges.append((lo, min(lo + per_n, len(syms))))
        n_chunks = len(ranges)
        out: list[dict[str, str]] = []
        for ci, (lo, hi) in enumerate(ranges):
            lines = [
                f"{i:02d}. {format_symbol_label(syms[i - 1], name_map)}"
                for i in range(lo + 1, hi + 1)
            ]
            if n_chunks > 1:
                header = f"{lo + 1:02d}〜{hi:02d}"
                body = "\n".join([header, *lines])
                fname = f"監視銘柄一覧 {ci + 1}/{n_chunks}"
            else:
                body = "\n".join(lines)
                fname = "監視銘柄一覧"
            if len(body) > max_value_len:
                return None
            out.append({"name": fname, "value": body})
        return out

    while per >= 1:
        built = _build(per)
        if built is not None:
            return built
        per = max(1, per // 2)

    # Fallback: split by character length on full numbered block
    full = format_watch_symbols_block(syms, name_map=name_map)
    fields: list[dict[str, str]] = []
    chunk = full
    idx = 1
    while chunk:
        fields.append(
            {
                "name": f"監視銘柄一覧({idx})",
                "value": chunk[:max_value_len],
            }
        )
        chunk = chunk[max_value_len:]
        idx += 1
    return fields or [{"name": "監視銘柄一覧", "value": "（なし）"}]


def build_entry_detail(
    *,
    symbol: str,
    entry_price: float,
    stop_price: float,
    slot_usage: str,
    entry_score_v2: Optional[int],
    data: Mapping[str, Any],
    score5_candidate_ordinal: Optional[int] = None,
    name_map: Optional[Mapping[str, str]] = None,
    entry_time: Optional[str] = None,
    sent_time: Optional[str] = None,
    sequence_id: Optional[int] = None,
) -> str:
    del stop_price, sent_time, sequence_id  # kept for call-site compat; not shown in operator body
    display = format_symbol_display(symbol, name_map=name_map)
    route = resolve_entry_route(data)
    momentum = resolve_momentum_label(data)
    board = resolve_board_label(data)
    try:
        from small_paper.discord_current_system_summary import (
            render_entry_quantity_line,
            resolve_entry_quantity,
        )

        qty = resolve_entry_quantity(data, config=None)
        if qty is None:
            qty = resolve_entry_quantity(data, config={"paper_quantity": 100})
        qty_line = render_entry_quantity_line(qty)
    except Exception:
        qty_line = "qty: n/a"
    lines = [
        display,
        f"エントリー時間: {format_time_hms_jst(entry_time)}",
        f"価格: {_fmt_price_yen(entry_price)}",
        qty_line,
        f"方式: {route}",
        f"score_v2: {entry_score_v2 if entry_score_v2 is not None else 'N/A'}",
        f"Momentum: {momentum}",
        f"Board: {board}",
        f"保有: {slot_usage}",
    ]
    if score5_candidate_ordinal is not None and entry_score_v2 is not None and entry_score_v2 >= 5:
        lines.append(f"本日score5候補: {score5_candidate_ordinal}件目")
    if route == "OR":
        or_reason = data.get("or_reason")
        if or_reason:
            lines.append(f"OR理由: {or_reason}")
    lines.extend(
        [
            "",
            "ENTRY理由:",
            format_entry_reason_block(data),
            "",
            "鮮度:",
            f"price_age_sec: {_fmt_na(data.get('price_age_sec'), digits=1)}",
            f"board_age_sec: {_fmt_na(data.get('board_age_sec'), digits=1)}",
            f"price_source: {_fmt_na(data.get('price_freshness_source') or data.get('price_source'))}",
        ]
    )
    if data.get("stale_trade") in (True, "true", "True", 1):
        lines.append("stale_trade: true（tag_only / 警告）")
    lines.extend(["", PAPER_ONLY_FOOTER])
    return "\n".join(lines)


def build_entry_cap_blocked_detail(
    *,
    symbol: str,
    entry_score_v2: Optional[int],
    data: Mapping[str, Any],
    active_positions: int,
    position_cap: int,
    name_map: Optional[Mapping[str, str]] = None,
    block_reason: str = REJECT_MAX_CONCURRENT,
) -> str:
    from small_paper.reject_reasons import entry_blocked_discord_label

    bullets = build_entry_reason_bullets(data)
    reason_block = "\n".join(f"・{b}" for b in bullets) if bullets else "・（なし）"
    display = format_symbol_display(symbol, name_map=name_map)
    block_label = entry_blocked_discord_label(block_reason)
    route = resolve_entry_route(data)
    event_time = str(data.get("event_time") or "")
    return "\n".join(
        [
            display,
            format_time_hms_jst(event_time) if event_time else "N/A",
            "",
            "ENTRY条件成立",
            f"方式: {route}",
            f"active_positions: {active_positions}",
            f"position_cap: {int(position_cap) if position_cap else DEFAULT_POSITION_CAP}",
            "",
            "見送り理由: 保有上限到達" if "上限" in block_label or "cap" in block_reason.lower() or "max_concurrent" in block_reason.lower() else f"見送り理由: {block_label}",
            f"entry_score_v2: {entry_score_v2 if entry_score_v2 is not None else 'N/A'}",
            "",
            "ENTRY理由:",
            reason_block,
            "",
            f"price_age_sec: {_fmt_na(data.get('price_age_sec'), digits=1)}",
            f"board_age_sec: {_fmt_na(data.get('board_age_sec'), digits=1)}",
            "",
            PAPER_ONLY_FOOTER,
        ]
    )


def build_entry_deferred_detail(
    *,
    symbol: str,
    current_price: float,
    entry_score_v2: int,
    slot_usage: str,
    data: Mapping[str, Any],
    open_positions: Sequence[Mapping[str, Any]],
    score5_candidate_ordinal: Optional[int] = None,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    display = format_symbol_display(symbol, name_map=name_map)
    lines = [
        f"銘柄: {display}",
        f"現在価格: {_fmt_num(current_price)}",
        f"entry_score_v2: {entry_score_v2}",
        f"保有枠: {slot_usage}",
    ]
    if score5_candidate_ordinal is not None:
        lines.append(f"本日score5候補: {score5_candidate_ordinal}件目")
    lines.extend(
        [
            "ENTRY理由:",
            "\n".join(f"・{b}" for b in build_entry_reason_bullets(data)),
            "保有中:",
            format_open_positions_lines(open_positions),
            "見送り理由:",
            format_entry_deferred_reason_block(),
        ]
    )
    return "\n".join(lines)


def format_board_dynamic_trailing_lines(
    *,
    board_dynamic_trailing_tier: Optional[str] = None,
    board_dynamic_trailing_activate_pct: Optional[float] = None,
    board_dynamic_trailing_giveback_frac: Optional[float] = None,
) -> list[str]:
    """Format runtime trailing thresholds only — no hardcoded tier/activate/giveback."""
    tier = str(board_dynamic_trailing_tier or "").strip()
    if not tier:
        return []
    lines = [f"board tier: {tier}"]
    if board_dynamic_trailing_activate_pct is not None:
        lines.append(
            f"activation threshold: {_fmt_na(board_dynamic_trailing_activate_pct, digits=2)}%"
        )
    else:
        lines.append("activation threshold: N/A")
    if board_dynamic_trailing_giveback_frac is not None:
        gb_pct = int(round(float(board_dynamic_trailing_giveback_frac) * 100))
        lines.append(f"giveback threshold: {gb_pct}%")
    else:
        lines.append("giveback threshold: N/A")
    return lines


def build_exit_detail(
    *,
    symbol: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    mfe_pct: Optional[float],
    mae_pct: Optional[float],
    hold_minutes: float,
    exit_reason: str,
    pnl_yen_100: Optional[float] = None,
    side: str = "long",
    board_dynamic_trailing_tier: Optional[str] = None,
    board_dynamic_trailing_activate_pct: Optional[float] = None,
    board_dynamic_trailing_giveback_frac: Optional[float] = None,
    entry_time: Optional[str] = None,
    exit_time: Optional[str] = None,
    name_map: Optional[Mapping[str, str]] = None,
    market_time_age_sec: Optional[float] = None,
    price_age_sec: Optional[float] = None,
    stale_trade: bool = False,
    sent_time: Optional[str] = None,
    session_id: Optional[str] = None,
    position_id: Optional[str] = None,
    sequence_id: Optional[int] = None,
    price_freshness_source: Optional[str] = None,
) -> str:
    del sent_time, session_id, position_id, sequence_id
    yen = resolve_pnl_yen_100(
        entry_price=entry_price,
        exit_price=exit_price,
        side=side,
        pnl_yen_100=pnl_yen_100,
    )
    display = format_symbol_display(symbol, name_map=name_map)
    lines = [
        display,
        f"エントリー時間: {format_time_hms_jst(entry_time)}",
        f"EXIT時間: {format_time_hms_jst(exit_time)}",
        f"{_fmt_price_yen(entry_price)} → {_fmt_price_yen(exit_price)}",
        format_exit_pnl_line(pnl_pct, yen),
        f"理由: {humanize_exit_reason(exit_reason)}",
        f"保有時間: {format_hold_duration(hold_minutes)}",
        f"MFE: {_fmt_na(mfe_pct, digits=2)}%",
        f"MAE: {_fmt_na(mae_pct, digits=2)}%",
    ]
    if "trailing_mfe" in str(exit_reason or ""):
        lines.extend(
            format_board_dynamic_trailing_lines(
                board_dynamic_trailing_tier=board_dynamic_trailing_tier,
                board_dynamic_trailing_activate_pct=board_dynamic_trailing_activate_pct,
                board_dynamic_trailing_giveback_frac=board_dynamic_trailing_giveback_frac,
            )
        )
    lines.extend(["", "鮮度:"])
    lag = market_time_age_sec if market_time_age_sec is not None else price_age_sec
    if lag is not None:
        lines.append(f"market_time_age_sec: {_fmt_na(lag, digits=1)}")
    if stale_trade:
        lines.append("stale_trade: true（警告 / tag_only・rejectではない）")
    src = price_freshness_source
    if src:
        lines.append(f"price_source: {src}")
    if is_stop_low_mfe_exit(exit_reason, mfe_pct):
        lines.append(f"⚠ stop_low_mfe: MFE<{STOP_LOW_MFE_THRESHOLD_PCT:.1f}% at stop")
    lines.extend(["", PAPER_ONLY_FOOTER])
    return "\n".join(lines)


def build_universe_screening_overview(
    *,
    session_label: str,
    watch_symbol_count: int,
    name_map: Optional[Mapping[str, str]] = None,
    generated_at: Optional[str] = None,
    sent_at: Optional[str] = None,
    sequence_id: Optional[int] = None,
) -> str:
    """Initial universe after AM/PM screening (no add/remove vs prior refresh)."""
    _ = name_map
    lines = [
            f"セッション: {session_label}",
            f"現在監視: {watch_symbol_count}銘柄",
    ]
    append_discord_delivery_audit_lines(
        lines,
        generated_at=generated_at,
        sent_time=sent_at,
        sequence_id=sequence_id,
    )
    lines.extend(
        [
            "",
            "初期監視銘柄:",
            "（下の監視銘柄一覧を参照）",
            "",
            "削除銘柄:",
            "（なし）",
        ]
    )
    return "\n".join(lines)


def build_universe_refresh_overview(
    *,
    session_label: str,
    refresh_time: str,
    added: Sequence[str],
    removed: Sequence[str],
    watch_symbol_count: int,
    name_map: Optional[Mapping[str, str]] = None,
    status: str = "SUCCESS",
    core10_count: Optional[int] = None,
    dynamic40_count: Optional[int] = None,
    registered_count: Optional[int] = None,
    capture_topology: str = "SINGLE_INGRESS_LOCAL_FANOUT",
) -> str:
    """Session + add/remove blocks (watch list sent as separate embed fields)."""
    _ = name_map
    st = str(status or "SUCCESS").strip().upper()
    if st in ("COMPLETED", "OK", "PASS"):
        st = "SUCCESS"
    elif st not in ("SUCCESS", "FAILED"):
        st = st or "SUCCESS"
    reg = registered_count if registered_count is not None else watch_symbol_count
    lines = [
        f"時刻: {refresh_time}",
        f"結果: {st}",
        f"登録: {reg} / 50",
    ]
    if core10_count is not None:
        lines.append(f"Core10: {core10_count}")
    if dynamic40_count is not None:
        lines.append(f"Dynamic40: {dynamic40_count}")
    lines.extend(
        [
            f"Capture topology: {capture_topology}",
            "Paper継続: YES",
            "実注文: DISABLED",
            f"セッション: {session_label}",
            f"現在監視: {watch_symbol_count}銘柄",
        ]
    )
    # Only show add/remove when lists were provided (may be empty = no change)
    lines.extend(
        [
            "",
            "追加銘柄:",
            format_added_symbols_block(added, name_map=name_map),
            "",
            "削除銘柄:",
            format_removed_symbols_block(removed, name_map=name_map),
        ]
    )
    return "\n".join(lines)


def build_universe_refresh_detail(
    *,
    session_label: str,
    refresh_time: str,
    added: Sequence[str],
    removed: Sequence[str],
    watch_symbols: Sequence[str],
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """Full text preview (overview + numbered watch list)."""
    overview = build_universe_refresh_overview(
        session_label=session_label,
        refresh_time=refresh_time,
        added=added,
        removed=removed,
        watch_symbol_count=len(watch_symbols),
        name_map=name_map,
    )
    watch_block = format_watch_symbols_block(watch_symbols, name_map=name_map)
    return "\n".join([overview, "", "監視銘柄一覧:", watch_block])


def observer_exit_rows(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        enrich_trade_pnl_yen(dict(e))
        for e in events
        if e.get("event_type") == "observer_exit" and e.get("pnl_pct") is not None
    ]


def summarize_observer_exit_metrics(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """100-share yen summary from observer_exit events."""
    exits = observer_exit_rows(events)
    pnls = [float(e["pnl_pct"]) for e in exits if e.get("pnl_pct") is not None]
    yen = summarize_pnl_yen_100(exits)
    return {
        "total_pnl_pct": round(sum(pnls), 2) if pnls else 0.0,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "total_pnl_yen_100": yen.get("total_pnl_yen_100"),
        "avg_pnl_yen_100": yen.get("avg_pnl_yen_100"),
        "profit_factor_yen_100": yen.get("profit_factor_yen_100"),
        "gross_profit_yen_100": yen.get("gross_profit_yen_100"),
        "gross_loss_yen_100": yen.get("gross_loss_yen_100"),
        "max_win_yen_100": yen.get("max_win_yen_100"),
        "max_loss_yen_100": yen.get("max_loss_yen_100"),
        "observer_exit_count_with_pnl": len(exits),
    }


def summary_notification_labels(summary: Mapping[str, Any]) -> tuple[str, str]:
    """Return (event_tag, title_line) for Daily / AM / PM summary Discord."""
    am_pm = summary.get("am_pm_session") or {}
    kind = str(am_pm.get("kind") or "").lower()
    if kind == "am":
        return "AM Summary", "【AM Summary】"
    if kind == "pm":
        return "PM Summary", "【PM Summary】"
    return "Daily Summary", "【Daily Summary】"


def format_summary_yen_display_lines(metrics: Mapping[str, Any]) -> list[str]:
    """Legacy compact yen block (phase reports). Prefer format_discord_summary_lines for Discord."""
    lines = [
        format_summary_total_pnl_line(
            float(metrics.get("total_pnl_pct") or 0.0),
            metrics.get("total_pnl_yen_100"),
        )
    ]
    avg_line = format_summary_avg_pnl_yen_100(metrics.get("avg_pnl_yen_100"))
    if avg_line is not None:
        lines.append(f"平均損益: {avg_line}")
    pf = metrics.get("profit_factor")
    if pf is not None:
        lines.append(f"PF: {format_summary_profit_factor_yen(pf)}")
    return lines


def _format_summary_yen_value(yen: Any) -> str:
    if yen is None:
        return "—"
    try:
        return format_pnl_yen_100_display(float(yen))
    except (TypeError, ValueError):
        return "—"


def _canonical_trade_display(metrics: Mapping[str, Any], key: str) -> str:
    trade = metrics.get(key)
    if isinstance(trade, Mapping):
        if trade.get("display"):
            return str(trade.get("display"))
        sym = str(trade.get("symbol") or "").strip()
        yen = trade.get("pnl_yen_100")
        if sym and yen is not None:
            try:
                y = float(yen)
                sign = "+" if y >= 0 else ""
                return f"{sym.replace('.T', '')} {sign}{int(round(y)):,}円".replace(",", ",")
            except (TypeError, ValueError):
                return sym
        return sym or "—"
    return str(trade or "—")


STOP_LOW_MFE_THRESHOLD_PCT = 0.5
OBSERVABILITY_SYMBOL_TOP_N = 5
OBSERVABILITY_REJECT_FUNNEL_TOP_N = 5
FOCUS_SYMBOL_SHORTS = frozenset({"6976", "4062"})
FOCUS_SYMBOL_SHARE_WARN_PCT = 30.0

EXIT_BUCKET_LABELS: dict[str, str] = {
    "stop_hit": "stop_hit",
    "no_progress": "no_progress",
    "trailing_mfe": "trailing_mfe",
    "session_close": "session_close",
    "other": "other",
}


def _trade_mfe_pct(row: Mapping[str, Any]) -> Optional[float]:
    for key in ("mfe_pct", "peak_mfe_pct", "max_favorable", "peak_pnl_pct"):
        v = row.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def is_stop_low_mfe_trade(row: Mapping[str, Any]) -> bool:
    from small_paper.canonical_summary import is_stop_exit

    if not is_stop_exit(row):
        return False
    mfe = _trade_mfe_pct(row)
    return mfe is not None and mfe < STOP_LOW_MFE_THRESHOLD_PCT


def is_stop_low_mfe_exit(exit_reason: str, mfe_pct: Optional[float]) -> bool:
    return is_stop_low_mfe_trade({"exit_reason": exit_reason, "mfe_pct": mfe_pct})


def count_stop_low_mfe(events: Sequence[Mapping[str, Any]]) -> int:
    from small_paper.canonical_summary import collect_canonical_trades

    return sum(1 for t in collect_canonical_trades(events) if is_stop_low_mfe_trade(t))


def _resolved_exit_reason_key(row: Mapping[str, Any]) -> str:
    reason = str(row.get("exit_reason") or "").strip()
    structural = str(row.get("structural_exit_reason") or "").strip()
    if reason == "overlap_replaced_review":
        return structural.lower()
    return (structural or reason).lower()


def classify_exit_bucket(row: Mapping[str, Any]) -> str:
    from small_paper.canonical_summary import is_stop_exit

    if is_stop_exit(row):
        return "stop_hit"
    reason = _resolved_exit_reason_key(row)
    if "no_progress" in reason:
        return "no_progress"
    if "trailing_mfe" in reason or "mfe_giveback" in reason:
        return "trailing_mfe"
    if any(
        token in reason
        for token in (
            "session_close",
            "session_end",
            "morning_session",
            "afternoon_session",
        )
    ):
        return "session_close"
    return "other"


def _compute_symbol_pnl_by_short(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    from small_paper.canonical_summary import collect_canonical_trades

    out: dict[str, dict[str, Any]] = {}
    for trade in collect_canonical_trades(events):
        sym_short = _symbol_short(str(trade.get("symbol") or ""))
        if not sym_short or sym_short == "—":
            continue
        bucket = out.setdefault(sym_short, {"pnl_yen_100": 0.0, "trade_count": 0})
        bucket["pnl_yen_100"] = round(bucket["pnl_yen_100"] + float(trade["pnl_yen_100"]), 2)
        bucket["trade_count"] += 1
    return out


def _top_symbol_share_pct(rows: Sequence[Mapping[str, Any]], total_pnl: float) -> Optional[float]:
    if not rows or total_pnl == 0:
        return None
    top3 = sum(float(r["pnl_yen_100"]) for r in rows[:3])
    return round(100.0 * top3 / total_pnl, 1)


def format_symbol_attribution_lines(
    events: Sequence[Mapping[str, Any]],
    *,
    name_map: Optional[Mapping[str, str]] = None,
    top_n: int = OBSERVABILITY_SYMBOL_TOP_N,
) -> list[str]:
    by_sym = _compute_symbol_pnl_by_short(events)
    if not by_sym:
        return ["（本日取引なし）"]
    total_pnl = round(sum(float(v["pnl_yen_100"]) for v in by_sym.values()), 2)
    ranked = sorted(
        (
            {
                "symbol_short": sym,
                "pnl_yen_100": float(data["pnl_yen_100"]),
                "trade_count": int(data["trade_count"]),
            }
            for sym, data in by_sym.items()
        ),
        key=lambda r: abs(float(r["pnl_yen_100"])),
        reverse=True,
    )[: max(1, int(top_n))]
    lines: list[str] = []
    for row in ranked:
        sym_short = str(row["symbol_short"])
        sym_full = f"{sym_short}.T"
        label = format_symbol_label(sym_full, name_map) if name_map else sym_short
        yen = format_pnl_yen_100_display(float(row["pnl_yen_100"]))
        share = (
            round(100.0 * float(row["pnl_yen_100"]) / total_pnl, 1)
            if total_pnl != 0
            else None
        )
        share_s = f", {share:.0f}% of day" if share is not None else ""
        warn = ""
        if sym_short in FOCUS_SYMBOL_SHORTS and share is not None and abs(share) >= FOCUS_SYMBOL_SHARE_WARN_PCT:
            warn = " ⚠"
        lines.append(f"{label}: {yen} ({int(row['trade_count'])}T{share_s}){warn}")
    top3_share = _top_symbol_share_pct(ranked, total_pnl)
    if top3_share is not None:
        lines.append(f"top3_share: {top3_share:.0f}%")
    return lines


def compute_exit_breakdown(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    from small_paper.canonical_summary import collect_canonical_trades

    buckets: dict[str, dict[str, Any]] = {
        key: {"count": 0, "pnl_yen_100": 0.0} for key in EXIT_BUCKET_LABELS
    }
    for trade in collect_canonical_trades(events):
        bucket = classify_exit_bucket(trade)
        if bucket not in buckets:
            bucket = "other"
        buckets[bucket]["count"] += 1
        buckets[bucket]["pnl_yen_100"] = round(
            buckets[bucket]["pnl_yen_100"] + float(trade["pnl_yen_100"]),
            2,
        )
    return buckets


def format_exit_breakdown_lines(events: Sequence[Mapping[str, Any]]) -> list[str]:
    from small_paper.canonical_summary import collect_canonical_trades

    trades = collect_canonical_trades(events)
    if not trades:
        return ["（本日取引なし）"]
    buckets = compute_exit_breakdown(events)
    order = ("stop_hit", "no_progress", "trailing_mfe", "session_close", "other")
    lines: list[str] = []
    for key in order:
        stat = buckets.get(key) or {}
        count = int(stat.get("count") or 0)
        if count <= 0:
            continue
        pnl = float(stat.get("pnl_yen_100") or 0.0)
        lines.append(f"{EXIT_BUCKET_LABELS[key]}: {count} ({format_pnl_yen_100_display(pnl)})")
    stop_low = count_stop_low_mfe(events)
    if stop_low > 0:
        low_pnl = round(
            sum(
                float(t["pnl_yen_100"])
                for t in trades
                if is_stop_low_mfe_trade(t)
            ),
            2,
        )
        lines.append(f"stop_low_mfe: {stop_low} ({format_pnl_yen_100_display(low_pnl)})")
    return lines


def _config_sha_tail(summary: Mapping[str, Any]) -> str:
    sha = str(summary.get("config_sha256") or "").strip()
    if len(sha) >= 8:
        return f"…{sha[-4:]}"
    return sha or "—"


def format_freshness_semantics_v2_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("freshness_semantics_v2_enabled"):
        return []
    return [
        f"event_stale rejects: {int(summary.get('event_stale_reject_count') or 0)}",
        f"board_stale rejects: {int(summary.get('board_stale_reject_count') or 0)}",
        f"trade_stale tags: {int(summary.get('trade_stale_tag_count') or 0)}",
        (
            f"thresholds: event={summary.get('event_stale_threshold_sec')}s "
            f"board={summary.get('board_stale_threshold_sec')}s "
            f"trade={summary.get('trade_stale_threshold_sec')}s"
        ),
    ]


def format_runtime_health_lines(summary: Mapping[str, Any]) -> list[str]:
    # Prefer observer peak / canonical peak over gate peak_open_slots (often 0 in CAP mode)
    canon = summary.get("canonical_summary") if isinstance(summary.get("canonical_summary"), dict) else {}
    peak = int(
        summary.get("observer_open_max_positions")
        or canon.get("max_concurrent")
        or summary.get("max_concurrent")
        or summary.get("peak_open_slots")
        or 0
    )
    cap = int(summary.get("max_concurrent_positions") or summary.get("max_concurrent_cap") or DEFAULT_POSITION_CAP)
    feat = summary.get("live_feature_complete_rate_pct")
    feat_s = f"{_fmt_num(feat, digits=1)}%" if feat is not None else "—"
    lines = [
        f"api_errors: {int(summary.get('api_error_count') or 0)}",
        f"stale_ticks: {int(summary.get('stale_tick_count') or 0)}",
        f"data_gaps: {int(summary.get('data_gap_count') or 0)}",
        f"feature_complete: {feat_s}",
        f"config: {_config_sha_tail(summary)}",
        f"peak_slots: {peak}/{cap}",
    ]
    if summary.get("discord_error_count") is not None:
        lines.append(f"discord_errors: {int(summary.get('discord_error_count') or 0)}")
    if summary.get("cap_blocked_notify_sent_count") is not None:
        lines.append(
            f"cap_blocked_sent: {int(summary.get('cap_blocked_notify_sent_count') or 0)}"
            f"/{int(summary.get('cap_blocked_notify_attempt_count') or 0)}"
        )
    exit_display = summary.get("pilot_exit_display")
    if not exit_display:
        from runner.pilot_subprocess_logging import format_pilot_exit_display

        exit_code = summary.get("pilot_exit_code")
        if exit_code is None:
            exit_code = summary.get("exit_code")
        exit_display = format_pilot_exit_display(
            exit_code=exit_code if isinstance(exit_code, int) else None,
            pilot_verdict=summary.get("pilot_subprocess_verdict")
            or summary.get("pilot_verdict"),
        )
    if exit_display:
        lines.append(f"Pilot Exit: {exit_display}")
    from small_paper.pre_session_warmup import format_warmup_health_lines

    lines.extend(format_warmup_health_lines(summary))
    return lines


def format_reject_funnel_lines(
    summary: Mapping[str, Any],
    *,
    top_n: int = OBSERVABILITY_REJECT_FUNNEL_TOP_N,
) -> list[str]:
    counts = summary.get("reject_reason_counts")
    if not isinstance(counts, Mapping) or not counts:
        return ["（拒否なし）"]
    ranked = sorted(
        ((str(reason), int(count)) for reason, count in counts.items() if int(count or 0) > 0),
        key=lambda item: item[1],
        reverse=True,
    )[: max(1, int(top_n))]
    if not ranked:
        return ["（拒否なし）"]
    return [f"{reason}: {count}" for reason, count in ranked]


def format_pbv2_internal_breakdown_lines(
    summary: Mapping[str, Any],
    *,
    top_n: int = 8,
) -> list[str]:
    """Phase627: decompose or_overlay_not_candidate into true PBv2 internal reasons."""
    counts = summary.get("pbv2_internal_reason_counts")
    if not isinstance(counts, Mapping) or not counts:
        return []
    reject_counts = summary.get("reject_reason_counts")
    masked = 0
    if isinstance(reject_counts, Mapping):
        masked = int(reject_counts.get("or_overlay_not_candidate") or 0)
    ranked = sorted(
        ((str(r), int(c)) for r, c in counts.items() if int(c or 0) > 0),
        key=lambda item: item[1],
        reverse=True,
    )[: max(1, int(top_n))]
    lines = [f"or_overlay_not_candidate: {masked} (PBv2内部理由の内訳)"] if masked else []
    lines.extend(f"{reason}: {count}" for reason, count in ranked)
    return lines


def format_gate_dominance_alert_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase627: single-blocker dominance alert (warning>=80%, critical>=95%)."""
    level = str(summary.get("gate_dominance_alert_level") or "none")
    if level == "none":
        return []
    reason = str(summary.get("gate_dominance_top_reason") or "")
    share = summary.get("gate_dominance_top_share_pct")
    total = int(summary.get("gate_dominance_total_rejects") or 0)
    marker = "🚨" if level == "critical" else "⚠"
    return [
        f"{marker} {level.upper()}: {reason} が reject の {_fmt_num(share, digits=1)}% を占有 "
        f"(n={total})",
        "paper trade は継続中 / 設定・特徴量の確認を推奨",
    ]


def build_observability_embed_fields(
    *,
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    name_map: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Phase490: Discord embed fields for observability blocks (no gate/runtime changes)."""
    sections = [
        ("Symbol Attribution", format_symbol_attribution_lines(events, name_map=name_map)),
        ("Exit Breakdown", format_exit_breakdown_lines(events)),
        ("Runtime Health", format_runtime_health_lines(summary)),
        ("Order Latency DryRun", format_order_latency_dryrun_lines(summary)),
        ("Reject Funnel", format_reject_funnel_lines(summary)),
    ]
    pbv2_internal_lines = format_pbv2_internal_breakdown_lines(summary)
    if pbv2_internal_lines:
        sections.append(("PBv2 Internal Breakdown", pbv2_internal_lines))
    dominance_lines = format_gate_dominance_alert_lines(summary)
    if dominance_lines:
        sections.append(("Gate Dominance Alert", dominance_lines))
    freshness_lines = format_freshness_semantics_v2_lines(summary)
    if freshness_lines:
        sections.insert(3, ("Freshness Semantics v2", freshness_lines))
    fields: list[dict[str, Any]] = []
    for name, lines in sections:
        if not lines:
            continue
        fields.append({"name": name, "value": "\n".join(lines)[:1020], "inline": False})
    return fields


def _as_int(val: Any, default: int = 0) -> int:
    try:
        if val is None or val == "":
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _as_float_or_none(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _yen_display(val: Any) -> str:
    num = _as_float_or_none(val)
    if num is None:
        return "—"
    return format_pnl_yen_100_display(num)


def format_pbv2_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: compact PBv2 / OR accepted counts (measured only)."""
    pbv2 = summary.get("pbv2_count")
    or_n = summary.get("or_count", summary.get("or_entry_count"))
    accepted = summary.get("accepted_count")
    if pbv2 is None and or_n is None and accepted is None:
        return []
    lines = [
        f"PBv2 accepted: {_as_int(pbv2)}",
        f"OR accepted: {_as_int(or_n)}",
        f"accepted total: {_as_int(accepted)}",
    ]
    exits = summary.get("observer_exit_count_with_pnl", summary.get("observer_exit_count"))
    if exits is not None:
        lines.append(f"exits(with pnl): {_as_int(exits)}")
    return lines


def format_flat_band_shadow_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase650: PBv2 flat-band shadow effect (measured only)."""
    if not summary.get("pbv2_flat_band_shadow_enabled"):
        return []
    lines = [
        f"variant: {summary.get('pbv2_flat_band_variant', 'flat_plus_overheat')}",
        (
            f"target={_as_int(summary.get('pbv2_flat_band_shadow_target_count'))} "
            f"block={_as_int(summary.get('pbv2_flat_band_shadow_block_count'))}"
        ),
        (
            f"blocked W/L: {_as_int(summary.get('pbv2_flat_band_shadow_blocked_winners'))}/"
            f"{_as_int(summary.get('pbv2_flat_band_shadow_blocked_losers'))} "
            f"blocked_pnl={_yen_display(summary.get('pbv2_flat_band_shadow_blocked_pnl_yen_100'))}"
        ),
        f"net_effect: {_yen_display(summary.get('pbv2_flat_band_shadow_net_effect_yen'))}",
        (
            f"flat={_as_int(summary.get('pbv2_flat_band_shadow_flat_blocks'))} "
            f"overheat={_as_int(summary.get('pbv2_flat_band_shadow_overheat_blocks'))} "
            f"rise5_overlap={_as_int(summary.get('pbv2_flat_band_shadow_overlap_with_rise5_shadow'))}"
        ),
    ]
    return lines


def format_rise5_shadow_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: PBv2 rise5 shadow effect (measured only)."""
    if not summary.get("pbv2_rise5_shadow_enabled"):
        return []
    thr = summary.get("pbv2_rise5_shadow_threshold_pct")
    lines = [
        f"threshold: {_fmt_num(thr, digits=2)}%" if thr is not None else "threshold: —",
        (
            f"block={_as_int(summary.get('pbv2_rise5_shadow_block_count'))} "
            f"kept={_as_int(summary.get('pbv2_rise5_shadow_kept_count'))} "
            f"target={_as_int(summary.get('pbv2_rise5_shadow_target_count'))}"
        ),
        (
            f"blocked W/L: {_as_int(summary.get('pbv2_rise5_shadow_blocked_winners'))}/"
            f"{_as_int(summary.get('pbv2_rise5_shadow_blocked_losers'))} "
            f"blocked_pnl={_yen_display(summary.get('pbv2_rise5_shadow_blocked_pnl_yen_100'))}"
        ),
        f"net_effect: {_yen_display(summary.get('pbv2_rise5_shadow_net_effect_yen'))}",
    ]
    return lines


def format_freshness_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: freshness rejects/tags (measured only)."""
    if not summary.get("freshness_semantics_v2_enabled"):
        # Still show counts when present without the v2 flag.
        has_any = any(
            summary.get(k) is not None
            for k in (
                "event_stale_reject_count",
                "board_stale_reject_count",
                "trade_stale_tag_count",
            )
        )
        if not has_any:
            return []
    return [
        f"event_stale rejects: {_as_int(summary.get('event_stale_reject_count'))}",
        f"board_stale rejects: {_as_int(summary.get('board_stale_reject_count'))}",
        f"trade_stale tags: {_as_int(summary.get('trade_stale_tag_count'))}",
    ]


def format_cluster_guard_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: cluster guard measured counters."""
    if not summary.get("entry_cluster_guard_enabled"):
        return []
    return [
        f"reject: {_as_int(summary.get('cluster_guard_reject_count'))}",
        f"exception: {_as_int(summary.get('cluster_guard_exception_count'))}",
        (
            f"exc_pnl={_yen_display(summary.get('cluster_guard_exception_pnl'))} "
            f"exc_pf={summary.get('cluster_guard_exception_pf', '—')}"
        ),
    ]


def format_gate_dominance_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: always-on gate dominance snapshot (measured only)."""
    total = summary.get("gate_dominance_total_rejects")
    reason = summary.get("gate_dominance_top_reason")
    share = summary.get("gate_dominance_top_share_pct")
    level = str(summary.get("gate_dominance_alert_level") or "none")
    if total is None and not reason:
        return []
    lines = [
        f"level: {level}",
        f"top: {reason or '—'} ({_fmt_num(share, digits=1)}%)",
        f"rejects(n): {_as_int(total)}",
    ]
    return lines


def format_entry_quality_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: entry quality guard measured rejects."""
    if not summary.get("entry_quality_guard_enabled"):
        return []
    return [
        f"reject total: {_as_int(summary.get('entry_quality_guard_reject_count'))}",
        (
            f"spread={_as_int(summary.get('entry_quality_guard_spread_reject_count'))} "
            f"update={_as_int(summary.get('entry_quality_guard_update_reject_count'))}"
        ),
    ]


def format_exit_summary_lines(events: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> list[str]:
    """Phase637: compact EXIT breakdown + monitor metrics when present."""
    lines = format_exit_breakdown_lines(events)
    if summary.get("exit_shadow_monitor_enabled"):
        capture = summary.get("exit_mfe_capture_ratio")
        opp = summary.get("exit_opportunity_loss_avg")
        early = summary.get("exit_early_profit_take_count")
        extra = []
        if capture is not None:
            extra.append(f"capture={capture}")
        if opp is not None:
            extra.append(f"opp_loss={opp}%")
        if early is not None:
            extra.append(f"early={early}")
        if extra:
            lines.append(" ".join(extra))
    # Keep one-screen: at most 5 lines.
    return lines[:5]


def format_shadow_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637/W25C-R2: only enabled shadows with today's count > 0."""
    lines: list[str] = []
    for row in collect_active_shadow_observations(summary):
        lines.append(f"{row['name']}: 対象={row['count']} 差分={row['delta']}")
    return lines[:4]


def format_todays_insight_lines(
    summary: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Phase637: brief comments strictly from measured counters."""
    insights: list[str] = []

    if summary.get("pbv2_rise5_shadow_enabled"):
        blocks = _as_int(summary.get("pbv2_rise5_shadow_block_count"))
        net = _as_float_or_none(summary.get("pbv2_rise5_shadow_net_effect_yen"))
        if blocks > 0 and net is not None:
            insights.append(
                f"Rise5 shadow block={blocks} → net_effect={_yen_display(net)}"
            )

    level = str(summary.get("gate_dominance_alert_level") or "none")
    if level in ("warning", "critical"):
        reason = summary.get("gate_dominance_top_reason") or "—"
        share = summary.get("gate_dominance_top_share_pct")
        insights.append(
            f"Gate dominance {level}: {reason} {_fmt_num(share, digits=1)}%"
        )

    eq = _as_int(summary.get("entry_quality_guard_reject_count"))
    if summary.get("entry_quality_guard_enabled") and eq > 0:
        insights.append(f"EntryQuality reject={eq}")

    cg = _as_int(summary.get("cluster_guard_reject_count"))
    if summary.get("entry_cluster_guard_enabled") and cg > 0:
        insights.append(f"ClusterGuard reject={cg}")

    if summary.get("pullback_misread_guard_shadow_enabled"):
        delta = _as_float_or_none(summary.get("pullback_misread_guard_shadow_delta_yen"))
        blocked = _as_int(summary.get("pullback_misread_guard_shadow_blocked_count"))
        if blocked > 0 and delta is not None and delta != 0:
            insights.append(
                f"PullbackMisread shadow block={blocked} → Δ={_yen_display(delta)}"
            )

    accepted = _as_int(summary.get("accepted_count"))
    exits = _as_int(
        summary.get("observer_exit_count_with_pnl", summary.get("observer_exit_count"))
    )
    if accepted == 0 and exits == 0 and not events:
        insights.append("本日取引なし")

    if not insights:
        insights.append("主要アラートなし")
    return insights[:4]


def format_system_health_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Phase637: system health snapshot (measured only)."""
    return format_runtime_health_lines(summary)


def build_operator_status_embed_fields(
    *,
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Phase637: one-screen operator status sections for Daily/AM/PM Summary."""
    sections: list[tuple[str, list[str]]] = [
        ("PBv2 Summary", format_pbv2_summary_lines(summary)),
        ("Rise5 Shadow Summary", format_rise5_shadow_summary_lines(summary)),
        ("Flat-band Shadow Summary", format_flat_band_shadow_summary_lines(summary)),
        ("Freshness Summary", format_freshness_summary_lines(summary)),
        ("Cluster Guard Summary", format_cluster_guard_summary_lines(summary)),
        ("Gate Dominance Summary", format_gate_dominance_summary_lines(summary)),
        ("ENTRY Quality Summary", format_entry_quality_summary_lines(summary)),
        ("EXIT Summary", format_exit_summary_lines(events, summary)),
        ("Shadow Summary", format_shadow_summary_lines(summary)),
        ("Today's Insight", format_todays_insight_lines(summary, events)),
        ("System Health", format_system_health_summary_lines(summary)),
    ]
    fields: list[dict[str, Any]] = []
    for name, lines in sections:
        if not lines:
            continue
        fields.append({"name": name, "value": "\n".join(lines)[:1020], "inline": False})
    return fields


def format_heartbeat_runtime_health_fields(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extra HEARTBEAT inline fields (Phase490 C03)."""
    peak = int(summary.get("peak_open_slots") or 0)
    cap = int(summary.get("max_concurrent_positions") or DEFAULT_POSITION_CAP)
    feat = summary.get("live_feature_complete_rate_pct")
    return [
        {"name": "data_gaps", "value": str(int(summary.get("data_gap_count") or 0)), "inline": True},
        {
            "name": "feature_complete",
            "value": f"{_fmt_num(feat, digits=1)}%" if feat is not None else "—",
            "inline": True,
        },
        {"name": "config", "value": _config_sha_tail(summary), "inline": True},
        {"name": "peak_slots", "value": f"{peak}/{cap}", "inline": True},
    ]


def format_research_shadow_daily_summary_lines(
    summary: Mapping[str, Any],
    *,
    omit_operator_covered: bool = False,
) -> list[str]:
    """Research-only shadow blocks appended to Daily / AM / PM Summary.

    When omit_operator_covered=True (Phase637), skip lines already shown in
    operator status sections to keep the Discord message one-screen readable.
    """
    lines: list[str] = []
    try:
        from small_paper.cost_aware_entry_shadow_hook import format_cost_aware_entry_shadow_lines

        lines.extend(format_cost_aware_entry_shadow_lines(summary))
    except Exception:
        pass
    try:
        from small_paper.pullback_volume_forward_logger import format_discord_lines

        lines.extend(format_discord_lines(summary))
    except Exception:
        pass
    sector = summary.get("sector_heat_forward_shadow")
    if isinstance(sector, Mapping):
        lines.append("SectorHeat Forward Shadow:")
        if sector.get("trade_overlap_days") is not None:
            lines.append(f"trade_overlap_days={sector.get('trade_overlap_days')}")
        if sector.get("adopt_not_allowed") is not None:
            lines.append(f"adopt_not_allowed={sector.get('adopt_not_allowed')}")
        status = sector.get("status")
        if status:
            lines.append(f"status={status}")

    risk = summary.get("risk_sizing_forward_shadow")
    if isinstance(risk, Mapping):
        lines.append("RiskAware Sizing Shadow:")
        if risk.get("trade_overlap_days") is not None:
            lines.append(f"days={risk.get('trade_overlap_days')}")
        if risk.get("best_policy") is not None:
            lines.append(f"best_policy={risk.get('best_policy')}")
        if risk.get("adopt_not_allowed") is not None:
            lines.append(f"adopt_not_allowed={risk.get('adopt_not_allowed')}")
        status = risk.get("status")
        if status:
            lines.append(f"status={status}")

    equity_stop = summary.get("equity_dynamic_stop_shadow")
    if isinstance(equity_stop, Mapping):
        lines.append("Equity Dynamic Stop Shadow:")
        if equity_stop.get("days") is not None:
            lines.append(f"days={equity_stop.get('days')}")
        if equity_stop.get("best_policy_1p5m") is not None:
            lines.append(f"best_policy_1p5m={equity_stop.get('best_policy_1p5m')}")
        if equity_stop.get("best_policy_5m") is not None:
            lines.append(f"best_policy_5m={equity_stop.get('best_policy_5m')}")
        if equity_stop.get("adopt_not_allowed") is not None:
            lines.append(f"adopt_not_allowed={equity_stop.get('adopt_not_allowed')}")
        status = equity_stop.get("status")
        if status:
            lines.append(f"status={status}")

    live_cfg = summary.get("live_config_forward_shadow")
    if isinstance(live_cfg, Mapping):
        lines.append("LiveConfig Shadow:")
        if live_cfg.get("day_count") is not None:
            lines.append(f"days={live_cfg.get('day_count')}")
        c1500 = live_cfg.get("candidate_1500k")
        if isinstance(c1500, Mapping):
            lines.append(
                "1500k: "
                f"final={c1500.get('final_equity')}, "
                f"DD={c1500.get('max_drawdown_pct')}, "
                f"verdict={c1500.get('verdict')}"
            )
        c2000 = live_cfg.get("candidate_2000k")
        if isinstance(c2000, Mapping):
            lines.append(
                "2000k: "
                f"final={c2000.get('final_equity')}, "
                f"DD={c2000.get('max_drawdown_pct')}, "
                f"verdict={c2000.get('verdict')}"
            )
        if live_cfg.get("current_recommendation") is not None:
            lines.append(f"current={live_cfg.get('current_recommendation')}")
        status = live_cfg.get("status")
        if status:
            lines.append(f"status={status}")

    live_trans = summary.get("live_config_transition_shadow")
    if summary.get("high_drift_pullback_guard_enabled"):
        lines.append(
            "HighDriftPullback Guard: "
            f"reject={summary.get('high_drift_pullback_reject_count', 0)}"
        )
    if summary.get("no_progress_exit_enabled"):
        lines.append(
            "NoProgress Exit: "
            f"count={summary.get('no_progress_exit_count', 0)}"
        )
    if summary.get("weak_shape_reject_enabled"):
        lines.append(
            "WeakShape Reject: "
            f"count={summary.get('weak_shape_reject_count', 0)}"
        )
    if summary.get("late_chase_guard_enabled"):
        lines.append(
            "LateChase Guard: "
            f"reject={summary.get('late_chase_reject_count', 0)}"
        )
    if summary.get("classic_late_chase_rsi_guard_enabled"):
        lines.append(
            "ClassicLateChaseRSI Guard: "
            f"classic_late_chase_rsi_over80={summary.get('classic_late_chase_rsi_over80', 0)}"
        )
    if summary.get("entry_quality_guard_enabled") and not omit_operator_covered:
        lines.append(
            "EntryQuality Guard: "
            f"reject={summary.get('entry_quality_guard_reject_count', 0)} "
            f"(spread={summary.get('entry_quality_guard_spread_reject_count', 0)}, "
            f"update={summary.get('entry_quality_guard_update_reject_count', 0)})"
        )
    if summary.get("entry_cluster_guard_enabled") and not omit_operator_covered:
        lines.append(
            "ClusterGuard: "
            f"reject={summary.get('cluster_guard_reject_count', 0)} "
            f"exception={summary.get('cluster_guard_exception_count', 0)} "
            f"exc_pnl={summary.get('cluster_guard_exception_pnl', 0)} "
            f"exc_pf={summary.get('cluster_guard_exception_pf', 0)}"
        )
    if summary.get("stop_low_mfe_guard_enabled"):
        lines.append(
            "StopLowMFEGuard: "
            f"reject={summary.get('stop_low_mfe_guard_reject_count', 0)} "
            f"missing={summary.get('stop_low_mfe_guard_missing_count', 0)} "
            f"net_shadow={summary.get('stop_low_mfe_guard_net_shadow', 0)}"
        )
    if summary.get("board_high_entry_count") is not None:
        lines.append(
            "BoardHigh ENTRY: "
            f"count={summary.get('board_high_entry_count', 0)}"
        )
    if summary.get("pullback_misread_dynamic40_guard_enabled"):
        lines.append(
            "VWAPPullback Guard: "
            f"reject={summary.get('pullback_misread_dynamic40_reject_count', 0)}"
        )

    if not omit_operator_covered:
        from small_paper.exit_shadow_monitor import format_exit_shadow_monitor_discord_lines

        lines.extend(format_exit_shadow_monitor_discord_lines(summary))
        from small_paper.pbv2_rise5_shadow import format_pbv2_rise5_shadow_discord_lines

        lines.extend(format_pbv2_rise5_shadow_discord_lines(summary))
        from small_paper.pbv2_flat_band_guard_shadow import format_pbv2_flat_band_shadow_discord_lines

        lines.extend(format_pbv2_flat_band_shadow_discord_lines(summary))
        from small_paper.flat_weak_range_forward_shadow import format_flat_weak_range_shadow_discord_lines

        lines.extend(format_flat_weak_range_shadow_discord_lines(summary))
        from small_paper.readiness_forward_shadow import format_readiness_shadow_discord_lines

        lines.extend(format_readiness_shadow_discord_lines(summary))
        if isinstance(summary.get("readiness_precision_shadow"), Mapping):
            from small_paper.ihc_shadow_counterfactual import format_entry_shadow_discord_lines

            lines.extend(format_entry_shadow_discord_lines(summary))
        else:
            from small_paper.shadow_ihc_portfolio import format_ihc_shadow_discord_lines

            lines.extend(format_ihc_shadow_discord_lines(summary))

    if isinstance(live_trans, Mapping):
        lines.append("LiveConfig Transition Shadow:")
        if live_trans.get("current_equity") is not None:
            lines.append(f"equity={live_trans.get('current_equity')}")
        if live_trans.get("active_policy_band") is not None:
            lines.append(f"band={live_trans.get('active_policy_band')}")
        if live_trans.get("cap_used") is not None:
            lines.append(f"cap={live_trans.get('cap_used')}")
        if live_trans.get("stop_policy_used") is not None:
            lines.append(f"stop={live_trans.get('stop_policy_used')}")
        if live_trans.get("transition_to_2000k") is not None:
            lines.append(f"transition_to_2000k={live_trans.get('transition_to_2000k')}")
        status = live_trans.get("status")
        if status:
            lines.append(f"status={status}")

    boundary = summary.get("boundary_forward_shadow")
    if isinstance(boundary, Mapping):
        lines.append("Boundary Shadow:")
        if boundary.get("day_count") is not None:
            lines.append(f"days={boundary.get('day_count')}")
        if boundary.get("baseline_total_pnl_yen_100") is not None:
            lines.append(f"baseline={boundary.get('baseline_total_pnl_yen_100')}")
        if boundary.get("shadow_total_pnl_yen_100") is not None:
            lines.append(f"shadow={boundary.get('shadow_total_pnl_yen_100')}")
        if boundary.get("delta_pnl_yen_100") is not None:
            lines.append(f"delta={boundary.get('delta_pnl_yen_100')}")
        if boundary.get("shadow_pf") is not None:
            lines.append(f"pf={boundary.get('shadow_pf')}")
        if boundary.get("shadow_maxdd_yen_100") is not None:
            lines.append(f"dd={boundary.get('shadow_maxdd_yen_100')}")
        if boundary.get("verdict") is not None:
            lines.append(f"verdict={boundary.get('verdict')}")
        status = boundary.get("status")
        if status:
            lines.append(f"status={status}")

    post_entry = summary.get("post_entry_forward_shadow")
    if isinstance(post_entry, Mapping):
        lines.append("PostEntry Shadow:")
        ge3 = post_entry.get("score_ge3_count")
        ge3_pnl = post_entry.get("score_ge3_pnl")
        ge4 = post_entry.get("score_ge4_count")
        ge4_pnl = post_entry.get("score_ge4_pnl")
        if ge3 is not None:
            lines.append(f"score>=3: count={ge3} pnl={ge3_pnl}")
        if ge4 is not None:
            lines.append(f"score>=4: count={ge4} pnl={ge4_pnl}")
        if post_entry.get("forward_days_collected") is not None:
            lines.append(f"days={post_entry.get('forward_days_collected')}")
        status = post_entry.get("status")
        if status:
            lines.append(f"status={status}")
    elif summary.get("post_entry_shadow_score_ge3_count") is not None:
        lines.append("PostEntry Shadow:")
        lines.append(
            f"score>=3: count={summary.get('post_entry_shadow_score_ge3_count')} "
            f"pnl={summary.get('post_entry_shadow_score_ge3_pnl')}"
        )
        lines.append(
            f"score>=4: count={summary.get('post_entry_shadow_score_ge4_count')} "
            f"pnl={summary.get('post_entry_shadow_score_ge4_pnl')}"
        )
    return lines


def format_discord_summary_lines(metrics: Mapping[str, Any]) -> list[str]:
    """Production Discord summary from canonical_summary only (100-share yen primary)."""
    from small_paper.session_validity import format_invalid_session_discord_lines, classify_session_validity

    lines: list[str] = []
    if metrics.get("session_validity") or metrics.get("stop_reason") == "register_failed":
        v = metrics if metrics.get("discord_banner") else classify_session_validity(metrics)
        lines.extend(format_invalid_session_discord_lines(v))
    watch_n = metrics.get("watch_symbols_count", metrics.get("monitored_symbol_count"))
    watch_s = str(watch_n) if watch_n is not None else "N/A"
    traded_n = metrics.get("traded_symbols_count", metrics.get("traded_symbol_count", 0))
    avg_yen = format_summary_avg_pnl_yen_100(metrics.get("avg_pnl_yen_100"))
    win_rate = metrics.get("win_rate_yen_100", metrics.get("win_rate", 0))
    pf = metrics.get("profit_factor_yen_100", metrics.get("profit_factor"))
    pf_s = format_summary_profit_factor_yen(pf)
    if str(pf_s).lower() == "inf":
        pf_s = "∞"
    win_c = metrics.get("win_count")
    loss_c = metrics.get("loss_count")
    draw_c = metrics.get("draw_count", metrics.get("flat_count"))
    if win_c is None and loss_c is None:
        wl = "N/A"
    else:
        wl = f"{int(win_c or 0)} / {int(loss_c or 0)} / {int(draw_c or 0)}"
    cap = int(metrics.get("max_concurrent_cap") or metrics.get("max_concurrent_positions") or DEFAULT_POSITION_CAP)
    stop_n = metrics.get("stop_count")
    stop_rate = metrics.get("stop_rate", 0)
    lines.extend(
        [
            f"取引数: {metrics.get('trade_count', 0)}",
            f"勝 / 負 / 引分: {wl}",
            f"勝率: {_fmt_num(float(win_rate) * 100, digits=1)}%",
            f"最終損益: {_format_summary_yen_value(metrics.get('total_pnl_yen_100'))}",
            f"平均損益: {avg_yen or 'N/A'}",
            f"PF: {pf_s}",
            f"Gross Profit: {_format_summary_yen_value(metrics.get('gross_profit_yen_100'))}",
            f"Gross Loss: {_format_summary_yen_value(metrics.get('gross_loss_yen_100'))}",
            f"STOP数 / STOP率: {stop_n if stop_n is not None else 'N/A'} / {_fmt_num(float(stop_rate) * 100, digits=1)}%",
            f"Best: {_canonical_trade_display(metrics, 'best_trade')}",
            f"Worst: {_canonical_trade_display(metrics, 'worst_trade')}",
            f"ピーク保有 / CAP: {metrics.get('max_concurrent', 0)} / {cap}",
            f"監視銘柄数: {watch_s}",
            f"取引銘柄数: {traded_n}",
            PAPER_ONLY_FOOTER,
        ]
    )
    # Phase687W43F: compact evaluation reachability (no per-symbol spam)
    eval_ready = metrics.get("evaluation_ready_symbol_count")
    eval_skip = metrics.get("evaluation_skipped_not_ready_count")
    recovery_n = metrics.get("evaluation_recovery_triggered_count")
    pipe_err = metrics.get("pipeline_integrity_error_count")
    if any(x is not None for x in (eval_ready, eval_skip, recovery_n, pipe_err)):
        lines.extend(
            [
                f"評価可能銘柄数: {eval_ready if eval_ready is not None else 'N/A'}",
                f"評価未到達件数: {eval_skip if eval_skip is not None else 'N/A'}",
                f"stale recovery評価件数: {recovery_n if recovery_n is not None else 'N/A'}",
                f"pipeline integrity error件数: {pipe_err if pipe_err is not None else 'N/A'}",
            ]
        )
    # Phase687W59: ENTRY integrity + delivery (Actual only; no Shadow PnL mix-in)
    try:
        from small_paper.discord_current_system_summary import render_canonical_integrity_lines

        if any(
            metrics.get(k) is not None
            for k in (
                "entry_integrity",
                "discord_delivery",
                "official_entry_count",
                "ghost_accept_count",
                "entry_aborted_count",
            )
        ):
            lines.extend(render_canonical_integrity_lines(metrics))
    except Exception:
        pass
    # Explicitly never show avg_pnl_pct / total_pnl_pct aggregate
    return lines


def _iter_score5_max_concurrent_rejects(
    reject_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in reject_rows:
        if str(r.get("gate_reject_reason") or "") != REJECT_MAX_CONCURRENT:
            continue
        v2 = _int_score(r.get("entry_expectancy_score_v2"))
        if v2 is None or v2 < ENTRY_DEFERRED_MIN_SCORE_V2:
            continue
        sym = str(r.get("symbol") or "")
        rows.append(
            {
                "symbol": sym,
                "symbol_short": _symbol_short(sym),
                "entry_score_v2": v2,
                "continuation_quality_score": r.get("continuation_quality_score"),
            }
        )
    return rows


def _deferred_ranking_from_rejects(
    reject_rows: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 15,
) -> list[dict[str, Any]]:
    rows = _iter_score5_max_concurrent_rejects(reject_rows)
    rows.sort(key=lambda x: (-int(x["entry_score_v2"]), -float(x.get("continuation_quality_score") or 0)))
    return rows[:top_n]


def _deferred_count_ranking(
    reject_rows: Sequence[Mapping[str, Any]],
    ux_stats: Optional[Mapping[str, Any]] = None,
    *,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    max_score: dict[str, int] = {}
    for r in _iter_score5_max_concurrent_rejects(reject_rows):
        sym = str(r["symbol"])
        counts[sym] += 1
        max_score[sym] = max(max_score.get(sym, 0), int(r["entry_score_v2"]))

    by_sym = (ux_stats or {}).get("deferred_reject_by_symbol") or {}
    if isinstance(by_sym, dict):
        for sym, bucket in by_sym.items():
            if not isinstance(bucket, dict):
                continue
            c = int(bucket.get("count") or 0)
            if c <= 0:
                continue
            counts[str(sym)] = max(counts[str(sym)], c)
            max_score[str(sym)] = max(max_score.get(str(sym), 0), int(bucket.get("max_score") or 0))

    ranked: list[dict[str, Any]] = []
    for sym, cnt in counts.most_common(top_n):
        ranked.append(
            {
                "symbol": sym,
                "symbol_short": _symbol_short(sym),
                "count": cnt,
                "entry_score_v2": max_score.get(sym, 0),
            }
        )
    return ranked


def _top_deferred_opportunity(
    reject_rows: Sequence[Mapping[str, Any]],
    ux_stats: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    rows = _iter_score5_max_concurrent_rejects(reject_rows)
    if rows:
        best = max(
            rows,
            key=lambda x: (
                int(x["entry_score_v2"]),
                float(x.get("continuation_quality_score") or 0),
            ),
        )
        return dict(best)
    by_sym = (ux_stats or {}).get("deferred_reject_by_symbol") or {}
    if not isinstance(by_sym, dict) or not by_sym:
        return None
    best_sym = ""
    best_score = -1
    for sym, bucket in by_sym.items():
        if not isinstance(bucket, dict):
            continue
        sc = int(bucket.get("max_score") or 0)
        if sc > best_score:
            best_score = sc
            best_sym = str(sym)
    if not best_sym:
        return None
    return {
        "symbol": best_sym,
        "symbol_short": _symbol_short(best_sym),
        "entry_score_v2": best_score,
    }


def format_deferred_ranking_lines(
    ranking: Sequence[Mapping[str, Any]],
    *,
    name_map: Optional[Mapping[str, str]] = None,
    with_count: bool = False,
) -> str:
    if not ranking:
        return "（なし）"
    parts = []
    for i, row in enumerate(ranking, 1):
        sym = str(row.get("symbol") or "")
        label = format_symbol_label(sym, name_map) if sym else str(row.get("symbol_short", "—"))
        v2 = row.get("entry_score_v2", "—")
        if with_count:
            cnt = int(row.get("count") or 0)
            parts.append(f"{i}. {label} score{v2} {cnt}回")
        else:
            parts.append(f"{i}. {label} score{v2}")
    return "\n".join(parts)


def aggregate_daily_metrics(
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    *,
    max_concurrent_positions: int,
    monitored_symbol_count: Optional[int] = None,
    reject_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    ux_stats: Optional[Mapping[str, Any]] = None,
    name_map: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades

    trades = collect_canonical_trades(events)
    canonical = build_canonical_summary(
        trades,
        peak_open_slots=int(summary.get("peak_open_slots") or 0),
        max_concurrent_positions=max_concurrent_positions,
        watch_symbols_count=monitored_symbol_count,
    )
    yen_block = summarize_observer_exit_metrics(events)

    score5_candidates = int(
        (ux_stats or {}).get("score5_candidate_count")
        or sum(
            1
            for e in events
            if e.get("event_type") == "candidate"
            and (_int_score(e.get("entry_expectancy_score_v2")) or 0) >= ENTRY_DEFERRED_MIN_SCORE_V2
        )
    )
    score5_entries = int(
        (ux_stats or {}).get("score5_entry_count")
        or sum(
            1
            for e in events
            if e.get("event_type") == "accepted"
            and (_int_score(e.get("entry_expectancy_score_v2")) or 0) >= ENTRY_DEFERRED_MIN_SCORE_V2
        )
    )
    rejects = list(reject_rows or [])
    score5_deferred = int(
        (ux_stats or {}).get("score5_deferred_total_count")
        or sum(
            1
            for r in rejects
            if str(r.get("gate_reject_reason") or "") == REJECT_MAX_CONCURRENT
            and (_int_score(r.get("entry_expectancy_score_v2")) or 0) >= ENTRY_DEFERRED_MIN_SCORE_V2
        )
    )
    deferred_ranking = _deferred_ranking_from_rejects(rejects)
    deferred_count_ranking = _deferred_count_ranking(rejects, ux_stats)
    top_deferred = _top_deferred_opportunity(rejects, ux_stats)

    return {
        **canonical,
        **yen_block,
        "profit_factor": canonical.get("profit_factor_yen_100"),
        "total_pnl_pct": yen_block.get("total_pnl_pct"),
        "total_pnl_pct_raw": canonical.get("total_pnl_pct_raw"),
        "win_rate": canonical.get("win_rate_yen_100"),
        "entry_count": int(
            summary.get("observer_entry_count") or summary.get("accepted_count") or 0
        ),
        "exit_count": int(summary.get("observer_exit_count") or canonical.get("trade_count") or 0),
        "score5_candidate_count": score5_candidates,
        "score5_entry_count": score5_entries,
        "score5_deferred_count": score5_deferred,
        "entry_deferred_notify_count": int((ux_stats or {}).get("entry_deferred_notify_count") or 0),
        "deferred_ranking": deferred_ranking,
        "deferred_count_ranking": deferred_count_ranking,
        "top_deferred_opportunity": top_deferred,
        "monitored_symbol_count": monitored_symbol_count,
        "traded_symbol_count": canonical.get("traded_symbols_count"),
    }


def build_daily_summary_detail(
    metrics: Mapping[str, Any],
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """Operator-facing Discord summary (actual paper trades only)."""
    del name_map
    return "\n".join(format_discord_summary_lines(metrics))


def preview_payload(
    *,
    event_tag: str,
    title_line: str,
    detail: str,
    color: int,
    extra_fields: Optional[Sequence[Mapping[str, str]]] = None,
) -> dict[str, Any]:
    """Offline sample for phase reports (no webhook)."""
    return {
        "event_tag": event_tag,
        "title": title_line,
        "header": "\n".join(
            ["[PAPER TRADE]", f"[{event_tag}]", "Real orders: DISABLED", title_line]
        ),
        "detail": detail,
        "extra_fields": list(extra_fields or []),
        "color": color,
    }
