"""
Phase276–277: Operator-readable Discord message text (no trading logic).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Optional, Sequence

from research.exposure_gate import REJECT_MAX_CONCURRENT
from small_paper.discord_symbol_names import format_symbol_label
from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _feature_token

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
) -> str:
    lines = [
        f"銘柄: {symbol}",
        f"ENTRY価格: {_fmt_num(entry_price)}",
        f"損切り価格: {_fmt_num(stop_price)}",
        f"保有枠: {slot_usage}",
        f"entry_score_v2: {entry_score_v2 if entry_score_v2 is not None else '—'}",
    ]
    if score5_candidate_ordinal is not None and entry_score_v2 is not None and entry_score_v2 >= 5:
        lines.append(f"本日score5候補: {score5_candidate_ordinal}件目")
    lines.extend(
        [
            "ENTRY理由:",
            format_entry_reason_block(data),
        ]
    )
    return "\n".join(lines)


def build_entry_deferred_detail(
    *,
    symbol: str,
    current_price: float,
    entry_score_v2: int,
    slot_usage: str,
    data: Mapping[str, Any],
    open_positions: Sequence[Mapping[str, Any]],
    score5_candidate_ordinal: Optional[int] = None,
) -> str:
    lines = [
        f"銘柄: {symbol}",
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
) -> str:
    sign = "+" if pnl_pct >= 0 else ""
    return "\n".join(
        [
            f"銘柄: {symbol}",
            f"ENTRY価格: {_fmt_num(entry_price)}",
            f"EXIT価格: {_fmt_num(exit_price)}",
            f"損益: {sign}{_fmt_num(pnl_pct)}%",
            f"最大含み益 MFE: {_fmt_num(mfe_pct)}%",
            f"最大逆行 MAE: {_fmt_num(mae_pct)}%",
            f"保有時間: {int(round(hold_minutes))}分",
            f"EXIT理由: {humanize_exit_reason(exit_reason)}",
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


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return wins / gl


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
    name_map: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    exits = [
        e
        for e in events
        if e.get("event_type") == "observer_exit"
        and e.get("pnl_pct") is not None
    ]
    pnls = [float(e["pnl_pct"]) for e in exits]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    stop_n = sum(1 for e in exits if e.get("stop_hit") or e.get("exit_reason") == "stop_hit")

    best_trade = worst_trade = "—"
    best_sym = worst_sym = "—"
    if exits:
        best_e = max(exits, key=lambda e: float(e["pnl_pct"]))
        worst_e = min(exits, key=lambda e: float(e["pnl_pct"]))
        b_code = _symbol_short(str(best_e.get("symbol", "")))
        w_code = _symbol_short(str(worst_e.get("symbol", "")))
        best_sym = f"{b_code} {_fmt_num(best_e.get('pnl_pct'))}%"
        worst_sym = f"{w_code} {_fmt_num(worst_e.get('pnl_pct'))}%"
        best_trade = (
            f"{b_code} {_fmt_num(best_e.get('pnl_pct'))}% "
            f"({humanize_exit_reason(str(best_e.get('exit_reason', '')))})"
        )
        worst_trade = (
            f"{w_code} {_fmt_num(worst_e.get('pnl_pct'))}% "
            f"({humanize_exit_reason(str(worst_e.get('exit_reason', '')))})"
        )

    traded = {str(e.get("symbol")) for e in exits if e.get("symbol")}
    pf = _profit_factor(pnls)
    pf_out: Any
    if pf is None:
        pf_out = "—"
    elif pf == float("inf"):
        pf_out = "∞"
    else:
        pf_out = round(pf, 2)

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
        "trade_count": len(exits),
        "profit_factor": pf_out,
        "total_pnl_pct": round(sum(pnls), 2) if pnls else 0.0,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "win_rate": round(len(wins) / len(pnls), 2) if pnls else 0.0,
        "stop_rate": round(stop_n / len(exits), 2) if exits else 0.0,
        "max_concurrent": int(summary.get("peak_open_slots") or 0),
        "max_concurrent_cap": max_concurrent_positions,
        "entry_count": int(
            summary.get("observer_entry_count") or summary.get("accepted_count") or 0
        ),
        "exit_count": int(summary.get("observer_exit_count") or len(exits)),
        "win_count": len(wins),
        "loss_count": len(losses),
        "best_symbol": best_sym,
        "worst_symbol": worst_sym,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "score5_candidate_count": score5_candidates,
        "score5_entry_count": score5_entries,
        "score5_deferred_count": score5_deferred,
        "entry_deferred_notify_count": int((ux_stats or {}).get("entry_deferred_notify_count") or 0),
        "deferred_ranking": deferred_ranking,
        "deferred_count_ranking": deferred_count_ranking,
        "top_deferred_opportunity": top_deferred,
        "monitored_symbol_count": monitored_symbol_count,
        "traded_symbol_count": len(traded),
    }


def build_daily_summary_detail(
    metrics: Mapping[str, Any],
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    mon = metrics.get("monitored_symbol_count")
    mon_s = str(mon) if mon is not None else "—"
    top_def = metrics.get("top_deferred_opportunity") or {}
    top_line = "—"
    if top_def:
        label = format_symbol_label(str(top_def.get("symbol", "")), name_map)
        top_line = f"{label} score{top_def.get('entry_score_v2', '—')}"
    count_rank_lines = format_deferred_ranking_lines(
        metrics.get("deferred_count_ranking") or [],
        name_map=name_map,
        with_count=True,
    )
    return "\n".join(
        [
            "見送り最高score:",
            top_line,
            "理由: 保有枠上限",
            "",
            "ENTRY見送り上位:",
            count_rank_lines,
            "",
            f"score5以上候補: {metrics.get('score5_candidate_count', 0)}",
            f"ENTRY: {metrics.get('score5_entry_count', 0)}",
            f"枠不足見送り: {metrics.get('score5_deferred_count', 0)}",
            "",
            f"trade_count: {metrics.get('trade_count', 0)}",
            f"PF: {metrics.get('profit_factor', '—')}",
            f"total_pnl_pct: {_fmt_num(metrics.get('total_pnl_pct'))}%",
            f"avg_pnl_pct: {_fmt_num(metrics.get('avg_pnl_pct'))}%",
            f"win_rate: {_fmt_num(float(metrics.get('win_rate', 0)) * 100, digits=0)}%",
            f"stop_rate: {_fmt_num(float(metrics.get('stop_rate', 0)) * 100, digits=0)}%",
            f"max_concurrent: {metrics.get('max_concurrent', 0)}/{metrics.get('max_concurrent_cap', 3)}",
            f"ENTRY見送り通知数: {metrics.get('entry_deferred_notify_count', 0)}",
            f"entry_count: {metrics.get('entry_count', 0)}",
            f"exit_count: {metrics.get('exit_count', 0)}",
            f"best_trade: {metrics.get('best_trade', '—')}",
            f"worst_trade: {metrics.get('worst_trade', '—')}",
            f"best_symbol: {metrics.get('best_symbol', '—')}",
            f"worst_symbol: {metrics.get('worst_symbol', '—')}",
            f"監視銘柄数: {mon_s}",
            f"取引銘柄数: {metrics.get('traded_symbol_count', 0)}",
        ]
    )


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
