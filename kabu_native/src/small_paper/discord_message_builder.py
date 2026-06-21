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
    "Momentum:low": "モメンタムが相対的に低い（スコア加点）",
    "Board:mid": "板のバランスが中位帯",
    "Price:high": "価格帯が高め",
    "TV:mid": "出来高増加",
}

EXIT_REASON_JA: dict[str, str] = {
    "stop_hit": "損切りライン到達",
    "trailing_mfe_exit": "利益確定条件到達",
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
        return "利益確定条件到達"
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

    if not lines:
        q = data.get("continuation_quality_score")
        if q is not None:
            lines.append("継続品質がエントリー基準を満たす")
        else:
            lines.append("エントリー監視条件を満たす")

    return lines


def format_entry_reason_block(data: Mapping[str, Any]) -> str:
    bullets = build_entry_reason_bullets(data)
    body = "\n".join(f"・{b}" for b in bullets)
    return f"{body}\nENTRY条件を満たしたためエントリー"


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
) -> str:
    display = format_symbol_display(symbol, name_map=name_map)
    lines = [
        f"銘柄: {display}",
    ]
    if entry_time:
        lines.append(f"時刻: {format_time_hms_jst(entry_time)}")
    lines.extend(
        [
        f"ENTRY価格: {_fmt_num(entry_price)}",
        f"損切り価格: {_fmt_num(stop_price)}",
        f"保有枠: {slot_usage}",
        f"entry_score_v2: {entry_score_v2 if entry_score_v2 is not None else '—'}",
        ]
    )
    if score5_candidate_ordinal is not None and entry_score_v2 is not None and entry_score_v2 >= 5:
        lines.append(f"本日score5候補: {score5_candidate_ordinal}件目")
    scan_id = data.get("scan_id")
    if scan_id:
        lines.append(f"scan_id: {scan_id}")
    data_source = data.get("data_source") or data.get("entry_data_source")
    if data_source:
        lines.append(f"data_source: {data_source}")
    if data.get("price_age_sec") is not None:
        lines.append(f"price_age_sec: {_fmt_num(data.get('price_age_sec'), digits=1)}")
    if data.get("board_age_sec") is not None:
        lines.append(f"board_age_sec: {_fmt_num(data.get('board_age_sec'), digits=1)}")
    if data.get("signal_to_notify_latency_ms") is not None:
        lines.append(f"latency_ms: {int(float(data.get('signal_to_notify_latency_ms')))}")
    if data.get("same_scan_rank"):
        lines.append(f"same_scan_rank: {data.get('same_scan_rank')}")
    if data.get("same_scan_candidates") is not None:
        lines.append(f"same_scan_candidates: {data.get('same_scan_candidates')}")
    lines.extend(
        [
            "ENTRY理由:",
            format_entry_reason_block(data),
        ]
    )
    if data.get("position_cap_mode"):
        lines.extend(
            [
                "Gate model: position_cap_until_exit",
                "Position model: observer_structural",
                f"CAP note: max {data.get('max_concurrent_positions', '—')} open positions until structural EXIT",
            ]
        )
    return "\n".join(lines)


def build_entry_cap_blocked_detail(
    *,
    symbol: str,
    entry_score_v2: Optional[int],
    data: Mapping[str, Any],
    active_positions: int,
    position_cap: int,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    bullets = build_entry_reason_bullets(data)
    reason_block = "\n".join(f"・{b}" for b in bullets) if bullets else "・（なし）"
    display = format_symbol_display(symbol, name_map=name_map)
    return "\n".join(
        [
            display,
            "",
            "ENTRY条件成立",
            f"active_positions: {active_positions}",
            f"position_cap: {position_cap}",
            "",
            "見送り理由:",
            "保有上限到達",
            "",
            f"entry_score_v2: {entry_score_v2 if entry_score_v2 is not None else '—'}",
            "",
            "ENTRY理由:",
            reason_block,
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
    exit_time: Optional[str] = None,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    yen = resolve_pnl_yen_100(
        entry_price=entry_price,
        exit_price=exit_price,
        side=side,
        pnl_yen_100=pnl_yen_100,
    )
    display = format_symbol_display(symbol, name_map=name_map)
    lines = [
        f"銘柄: {display}",
    ]
    if exit_time:
        lines.append(f"EXIT時刻: {format_time_hms_jst(exit_time)}")
    lines.extend(
        [
        f"ENTRY価格: {_fmt_num(entry_price)}",
        f"EXIT価格: {_fmt_num(exit_price)}",
        format_exit_pnl_line(pnl_pct, yen),
        f"最大含み益 MFE: {_fmt_num(mfe_pct)}%",
        f"最大逆行 MAE: {_fmt_num(mae_pct)}%",
        f"保有時間: {int(round(hold_minutes))}分",
        f"EXIT理由: {humanize_exit_reason(exit_reason)}",
        ]
    )
    if is_stop_low_mfe_exit(exit_reason, mfe_pct):
        lines.append(f"⚠ stop_low_mfe: MFE<{STOP_LOW_MFE_THRESHOLD_PCT:.1f}% at stop")
    if (
        "trailing_mfe" in str(exit_reason or "")
        and board_dynamic_trailing_tier
    ):
        gb_pct = (
            int(round(float(board_dynamic_trailing_giveback_frac) * 100))
            if board_dynamic_trailing_giveback_frac is not None
            else None
        )
        lines.append(
            "Trailing: "
            f"{board_dynamic_trailing_tier} "
            f"(activate {_fmt_num(board_dynamic_trailing_activate_pct)}% / "
            f"giveback {gb_pct if gb_pct is not None else '—'}%)"
        )
    return "\n".join(lines)


def build_universe_screening_overview(
    *,
    session_label: str,
    watch_symbol_count: int,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """Initial universe after AM/PM screening (no add/remove vs prior refresh)."""
    _ = name_map
    return "\n".join(
        [
            f"セッション: {session_label}",
            f"現在監視: {watch_symbol_count}銘柄",
            "",
            "初期監視銘柄:",
            "（下の監視銘柄一覧を参照）",
            "",
            "削除銘柄:",
            "（なし）",
        ]
    )


def build_universe_refresh_overview(
    *,
    session_label: str,
    refresh_time: str,
    added: Sequence[str],
    removed: Sequence[str],
    watch_symbol_count: int,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """Session + add/remove blocks (watch list sent as separate embed fields)."""
    add_block = format_added_symbols_block(added, name_map=name_map)
    rem_block = format_removed_symbols_block(removed, name_map=name_map)
    return "\n".join(
        [
            f"セッション: {session_label} {refresh_time}",
            f"現在監視: {watch_symbol_count}銘柄",
            "",
            "追加銘柄:",
            add_block,
            "",
            "削除銘柄:",
            rem_block,
        ]
    )


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
        return str(trade.get("display") or "—")
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


def format_runtime_health_lines(summary: Mapping[str, Any]) -> list[str]:
    peak = int(summary.get("peak_open_slots") or summary.get("max_concurrent") or 0)
    cap = int(summary.get("max_concurrent_positions") or summary.get("max_concurrent_cap") or 3)
    feat = summary.get("live_feature_complete_rate_pct")
    feat_s = f"{_fmt_num(feat, digits=1)}%" if feat is not None else "—"
    return [
        f"api_errors: {int(summary.get('api_error_count') or 0)}",
        f"stale_ticks: {int(summary.get('stale_tick_count') or 0)}",
        f"data_gaps: {int(summary.get('data_gap_count') or 0)}",
        f"feature_complete: {feat_s}",
        f"config: {_config_sha_tail(summary)}",
        f"peak_slots: {peak}/{cap}",
    ]


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
        ("Reject Funnel", format_reject_funnel_lines(summary)),
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
    cap = int(summary.get("max_concurrent_positions") or 3)
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


def format_research_shadow_daily_summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """Research-only shadow blocks appended to Daily / AM / PM Summary."""
    lines: list[str] = []
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
    return lines


def format_discord_summary_lines(metrics: Mapping[str, Any]) -> list[str]:
    """Production Discord summary from canonical_summary only (100-share yen primary)."""
    watch_n = metrics.get("watch_symbols_count", metrics.get("monitored_symbol_count"))
    watch_s = str(watch_n) if watch_n is not None else "—"
    traded_n = metrics.get("traded_symbols_count", metrics.get("traded_symbol_count", 0))
    avg_yen = format_summary_avg_pnl_yen_100(metrics.get("avg_pnl_yen_100"))
    win_rate = metrics.get("win_rate_yen_100", metrics.get("win_rate", 0))
    pf = metrics.get("profit_factor_yen_100", metrics.get("profit_factor"))
    return [
        f"trade_count: {metrics.get('trade_count', 0)}",
        f"win_rate_yen_100: {_fmt_num(float(win_rate) * 100, digits=0)}%",
        f"profit_factor_yen_100: {format_summary_profit_factor_yen(pf)}",
        f"total_pnl_yen_100: {_format_summary_yen_value(metrics.get('total_pnl_yen_100'))}",
        f"avg_pnl_yen_100: {avg_yen or '—'}",
        f"stop_rate: {_fmt_num(float(metrics.get('stop_rate', 0)) * 100, digits=0)}%",
        f"best_trade: {_canonical_trade_display(metrics, 'best_trade')}",
        f"worst_trade: {_canonical_trade_display(metrics, 'worst_trade')}",
        f"max_concurrent: {metrics.get('max_concurrent', 0)}/{metrics.get('max_concurrent_cap', 3)}",
        f"監視銘柄数: {watch_s}",
        f"取引銘柄数: {traded_n}",
    ]
    if metrics.get("position_cap_mode"):
        lines.extend(
            [
                "position_cap_mode: true",
                f"position_cap_max_open: {metrics.get('position_cap_max_open', 0)}",
                f"observer_open_max_positions: {metrics.get('observer_open_max_positions', 0)}",
                f"gate_virtual_hold_max_slots: {metrics.get('gate_virtual_hold_max_slots', 0)}",
                f"session_close_exit_burst_count: {metrics.get('session_close_exit_burst_count', 0)}",
            ]
        )
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
        "header": "\n".join(["[SMALL PAPER DRY RUN]", f"[{event_tag}]", "[NO ORDER]", title_line]),
        "detail": detail,
        "extra_fields": list(extra_fields or []),
        "color": color,
    }
