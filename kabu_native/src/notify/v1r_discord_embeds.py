"""V1R Discord Embed builders — preserve existing trade-notify information density.

Readable Embed + fields. No PBv2-only concepts. Color locked by event type.
"""
from __future__ import annotations

from typing import Any, Optional

from small_paper.discord_symbol_names import format_symbol_display

# Colors locked by event (never by PnL)
COLOR_ENTRY = 0x57F287
COLOR_FILL = 0x3498DB
COLOR_EXIT = 0xED4245
COLOR_EXPIRED = 0xE67E22
COLOR_SUMMARY = 0x95A5A6
COLOR_SHADOW = 0x9B59B6

PAPER_FOOTER = {"text": "PAPER ONLY / 実注文なし"}
TEST_FOOTER = {"text": "TEST ONLY / NO TRADE · PAPER ONLY / 実注文なし"}


def _runtime_identity_field(p: dict[str, Any]) -> dict[str, Any]:
    """Minimum V1R-native identity block required on Primary Discord messages."""
    lim = p.get("limit")
    if lim is None:
        lim = p.get("entry_price")
    return _field(
        "Identity",
        "\n".join(
            [
                f"symbol: {_disp(p.get('symbol'))}",
                f"source: {_disp(p.get('source') or 'v1r_native')}",
                f"anchor: {_disp(p.get('anchor') or p.get('signal_time'))}",
                f"limit: {_yen(lim)}",
                f"status: {_disp(p.get('status'))}",
                f"role: {_disp(p.get('role') or 'PAPER_PRIMARY')}",
            ]
        ),
        inline=False,
    )


EXIT_REASON_HUMAN = {
    "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET": "600秒経過後の最初の有効Buy1",
    "FIXED600": "固定600秒",
    "FIXED_HOLD": "600秒決済: 上昇継続条件なし",
    "SESSION_CLOSE": "セッション終了",
    # V1R EXIT V2 Arch E (human reasons; ledger keeps raw ids)
    "IMBALANCE": "早期撤退: 売り優勢状態が5秒継続",
    "CONT_EXIT_600": "600秒決済: 上昇継続条件なし",
    "CONT_EXTEND_750": "750秒延長決済: 600秒時点で上昇継続条件成立",
    "TIME750": "750秒延長決済: 600秒時点で上昇継続条件成立",
}


def human_exit_reason(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "600秒経過後の最初の有効Buy1"
    return EXIT_REASON_HUMAN.get(s, s if "FIRST_VALID" not in s else "600秒経過後の最初の有効Buy1")


def human_exit_mode(raw: Any = None, *, extended: bool = False, guard: bool = False) -> str:
    """Discord EXIT方式 line for V1R Primary (Arch E or FIXED600)."""
    s = str(raw or "").strip()
    if guard or s == "IMBALANCE":
        return "EXIT方式: 非対称EXIT V2（早期撤退）"
    if extended or s in ("CONT_EXTEND_750", "TIME750"):
        return "EXIT方式: 非対称EXIT V2（750秒延長）"
    if s in ("CONT_EXIT_600", "FIXED_HOLD", "FIXED600", "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"):
        return "EXIT方式: 非対称EXIT V2（600秒）"
    return "EXIT方式: 非対称EXIT V2"


def _disp(v: Any, *, default: str = "—") -> str:
    if v is None:
        return default
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return default
    return s


def _yen(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(round(float(v))):,}円"
    except Exception:
        return "—"


def _pct(v: Any, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        p = float(v)
        return f"+{p:.{digits}f}%" if p >= 0 else f"{p:.{digits}f}%"
    except Exception:
        return "—"


def _pnl_yen(v: Any) -> str:
    if v is None:
        return "—"
    try:
        y = float(v)
        sign = "+" if y >= 0 else ""
        return f"{sign}{int(round(y)):,}円"
    except Exception:
        return "—"


def _field(name: str, value: str, *, inline: bool = False) -> dict[str, Any]:
    return {"name": name[:256], "value": (value or "—")[:1020], "inline": inline}


def _header(kind_emoji: str, symbol: str, name: Optional[str] = None) -> str:
    label = format_symbol_display(symbol, name)
    return f"{kind_emoji} | {label}"


def _na_none(text: str) -> str:
    # Never show Python None/null in Discord
    return (
        text.replace("None", "—")
        .replace("null", "—")
        .replace("NULL", "—")
    )


def build_entry_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    sym = str(p.get("symbol") or "")
    title = _header("🟢 ENTRY", sym, p.get("symbol_name"))
    anchor = _disp(p.get("anchor") or p.get("signal_time"))
    rank = p.get("rank")
    candidates = p.get("candidates") or p.get("cohort_n") or 50
    score = p.get("score")
    open_n = p.get("open", p.get("OPEN", 0))
    pending_n = p.get("pending", p.get("PENDING", 0))
    cap = p.get("cap", 5)
    qty = p.get("qty", 100)
    limit = p.get("limit")
    wait = p.get("wait_sec", 1.0)
    fresh = p.get("freshness_sec")
    entry_n = p.get("entry_count_today")
    prev = p.get("previous_trade")  # dict or None

    desc = "\n".join([
        f"エントリー判定: {anchor}",
        f"銘柄: {format_symbol_display(sym, p.get('symbol_name'))}",
        f"株数: {qty}株",
        f"指値: {_yen(limit)}",
    ])

    fields = [
        _field("V1R判定", "\n".join([
            f"順位: {rank} / {candidates}",
            f"Score: {_disp(score)}",
            f"保有枠: OPEN {open_n} / PENDING {pending_n} / CAP {cap}",
        ]), inline=False),
        _field("ENTRY方式", "\n".join([
            "V1R / PASSIVE BID",
            f"約定待ち: {wait}秒",
        ]), inline=True),
    ]

    # previous trade
    if isinstance(prev, dict) and (prev.get("exit_time") or prev.get("exit_price") is not None):
        prev_lines = [
            f"本日同銘柄ENTRY: {entry_n}回目" if entry_n is not None else "本日同銘柄ENTRY: —",
            f"前回EXIT理由: {_disp(prev.get('exit_reason_ja') or human_exit_reason(prev.get('exit_reason')))}",
            f"前回EXIT時刻: {_disp(prev.get('exit_time'))}",
            f"前回EXIT価格: {_yen(prev.get('exit_price'))}",
            f"前回EXIT損益: {_pnl_yen(prev.get('exit_pnl_yen'))}",
            f"前回EXITから: {_disp(prev.get('elapsed'))}",
        ]
    else:
        prev_lines = [
            f"本日同銘柄ENTRY: {entry_n}回目" if entry_n is not None else "本日同銘柄ENTRY: —",
            "前回取引: なし",
        ]
    fields.append(_field("前回取引", "\n".join(prev_lines), inline=False))

    reason_lines = [
        f"・{_disp(anchor)}固定Anchor",
        f"・Model Score {_disp(score)}",
        f"・{candidates}銘柄中{rank}位" if rank is not None else "・Rank —",
        "・Cap admission PASS",
        f"・Freshness {_disp(fresh)}秒" if fresh is not None else "・Freshness PASS",
        "・Special quoteなし" if not p.get("special_quote") else "・Special quote検出",
    ]
    fields.append(_field("ENTRY判断", "\n".join(reason_lines), inline=False))
    fields.append(_runtime_identity_field(p))

    return {
        "title": title[:256],
        "description": _na_none(desc)[:2048],
        "color": COLOR_ENTRY,
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_fill_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    sym = str(p.get("symbol") or "")
    title = _header("🔵 FILL", sym, p.get("symbol_name"))
    qty = p.get("qty", 100)
    limit = p.get("limit")
    fill = p.get("fill") if p.get("fill") is not None else limit
    rank = p.get("rank")
    candidates = p.get("candidates") or p.get("cohort_n") or 50
    score = p.get("score")
    delay = p.get("fill_delay_sec")
    open_n = p.get("open", 0)
    pending_n = p.get("pending", 0)
    cap = p.get("cap", 5)
    buy1 = p.get("buy1")
    sell1 = p.get("sell1")
    fresh = p.get("freshness_sec")
    fill_n = p.get("fill_count_today")

    desc = "\n".join([
        f"ENTRY判定: {_disp(p.get('anchor') or p.get('signal_time'))}",
        f"FILL時刻: {_disp(p.get('fill_time'))}",
        "",
        f"指値: {_yen(limit)}",
        f"約定価格: {_yen(fill)}",
        f"株数: {qty}株",
    ])
    board = f"板: Buy1 {_yen(buy1)} / Sell1 {_yen(sell1)} / freshness {_disp(fresh)}s"
    fields = [
        _field("V1R判定", "\n".join([
            f"順位: {rank} / {candidates}",
            f"Score: {_disp(score)}",
            f"約定時間: {_disp(delay)}秒",
            f"保有枠: OPEN {open_n} / PENDING {pending_n} / CAP {cap}",
        ])),
        _field("EXIT", "\n".join([
            "EXIT方式: 非対称EXIT V2（Early Guard + 600/750）",
            f"EXIT予定: {_disp(p.get('exit_target') or '状態依存')}",
        ]), inline=True),
        _field("板", board, inline=False),
        _field("本日", f"本日同銘柄FILL: {fill_n}回目" if fill_n is not None else "本日同銘柄FILL: —"),
        _runtime_identity_field(p),
    ]
    return {
        "title": title[:256],
        "description": _na_none(desc)[:2048],
        "color": COLOR_FILL,
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_expired_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    sym = str(p.get("symbol") or "")
    title = _header("🟠 EXPIRED", sym, p.get("symbol_name"))
    qty = p.get("qty", 100)
    rank = p.get("rank")
    candidates = p.get("candidates") or 50
    score = p.get("score")
    entry_n = p.get("entry_count_today")
    desc = "\n".join([
        f"ENTRY判定: {_disp(p.get('anchor') or p.get('signal_time'))}",
        f"失効時刻: {_disp(p.get('expire_time'))}",
        "",
        f"指値: {_yen(p.get('limit'))}",
        f"株数: {qty}株",
    ])
    fields = [
        _field("V1R判定", "\n".join([
            f"順位: {rank} / {candidates}",
            f"Score: {_disp(score)}",
        ])),
        _field("結果", "\n".join([
            f"{p.get('wait_sec', 1.0)}秒以内に約定せず",
            "取引: なし",
        ])),
        _field("本日", f"本日同銘柄ENTRY候補: {entry_n}回目" if entry_n is not None else "本日同銘柄ENTRY候補: —"),
        _field("失効時板", "\n".join([
            f"Buy1: {_yen(p.get('buy1'))}",
            f"Sell1: {_yen(p.get('sell1'))}",
            f"freshness: {_disp(p.get('freshness_sec'))}秒",
        ])),
        _runtime_identity_field(p),
    ]
    return {
        "title": title[:256],
        "description": _na_none(desc)[:2048],
        "color": COLOR_EXPIRED,
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_exit_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    sym = str(p.get("symbol") or "")
    title = _header("🔴 EXIT", sym, p.get("symbol_name"))
    qty = p.get("qty", 100)
    desc = "\n".join([
        f"ENTRY時刻: {_disp(p.get('entry_time') or p.get('fill_time'))}",
        f"EXIT時刻: {_disp(p.get('exit_time'))}",
        "",
        f"ENTRY価格: {_yen(p.get('entry_price'))}",
        f"EXIT価格: {_yen(p.get('exit_price'))}",
        f"株数: {qty}株",
    ])
    fields = [
        _field("損益", "\n".join([
            f"損益: {_pnl_yen(p.get('pnl_yen'))}",
            f"騰落率: {_pct(p.get('pnl_pct'))}",
            f"本日同銘柄累計: {_pnl_yen(p.get('daily_symbol_pnl_yen'))}",
            f"本日V1R累計: {_pnl_yen(p.get('daily_v1r_pnl_yen') if p.get('daily_v1r_pnl_yen') is not None else p.get('today_pnl_yen'))}",
        ])),
        _field("保有・EXIT", "\n".join([
            f"保有時間: {_disp(p.get('hold_sec'))}秒",
            human_exit_mode(
                p.get("reason"),
                extended=bool(p.get("exit_v2_extended") or p.get("extended")),
                guard=bool(p.get("exit_v2_triggered_guard") or p.get("triggered_guard")),
            ),
            f"EXIT理由: {p.get('exit_reason_ja') or human_exit_reason(p.get('reason'))}",
        ])),
        _field("MFE / MAE", "\n".join([
            f"最大含み益 MFE: {_pct(p.get('mfe_pct'))}",
            f"最大逆行 MAE: {_pct(p.get('mae_pct'))}",
        ]), inline=True),
        _field("EXIT時板", "\n".join([
            f"Buy1: {_yen(p.get('buy1'))}",
            f"Buy1 Qty: {_disp(p.get('buy1_qty'))}",
            f"freshness: {_disp(p.get('freshness_sec'))}秒",
        ])),
        _runtime_identity_field(p),
    ]
    return {
        "title": title[:256],
        "description": _na_none(desc)[:2048],
        "color": COLOR_EXIT,  # always red
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_primary_summary_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    title = f"📊 V1R 日次結果 | {_disp(p.get('date'))}"
    desc = "\n".join([
        f"損益: {_disp(p.get('total_pnl'))}",
        f"PF: {_disp(p.get('overall_pf'))}",
        f"勝敗: {_disp(p.get('wins'))}勝 / {_disp(p.get('losses'))}敗",
    ])
    fields = [
        _field("件数", "\n".join([
            f"ENTRY候補数: {_disp(p.get('signals') or p.get('admitted_candidates'))}",
            f"FILL数: {_disp(p.get('fills'))}",
            f"EXPIRED数: {_disp(p.get('expired'))}",
            f"FILL率: {_disp(p.get('fill_rate'))}",
            f"CAP BLOCK数: {_disp(p.get('capacity_blocked'))}",
        ])),
        _field("Best / Worst", "\n".join([
            f"Best: {_disp(p.get('best'))}",
            f"Worst: {_disp(p.get('worst'))}",
        ]), inline=True),
        _field("枠・寄与", "\n".join([
            f"最大OPEN+PENDING: {_disp(p.get('max_open_pending'))}",
            f"本日最大寄与銘柄: {_disp(p.get('top_symbol'))}",
            f"最大寄与率: {_disp(p.get('top_symbol_contribution'))}",
        ])),
        _field("MFE / MAE", "\n".join([
            f"MFE平均: {_pct(p.get('mfe_mean_pct'))}",
            f"MAE平均: {_pct(p.get('mae_mean_pct'))}",
        ]), inline=True),
        _field("安全", "\n".join([
            f"本日V1R累計: {_disp(p.get('daily_v1r_pnl') or p.get('total_pnl'))}",
            f"submit/cancel/live: {_disp(p.get('submit_cancel_live'), default='0/0/0')}",
        ])),
    ]
    return {
        "title": title[:256],
        "description": _na_none(desc)[:2048],
        "color": COLOR_SUMMARY,
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_pbv2_shadow_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    # Aggregated window digest (preferred) — one message per 5m / fixed anchor
    if p.get("digest") or str(p.get("status") or "") == "DIGEST":
        syms = p.get("symbols") or []
        if isinstance(syms, (list, tuple)):
            sym_txt = ", ".join(str(s) for s in list(syms)[:20]) or "—"
            if len(syms) > 20:
                sym_txt += f" …(+{len(syms) - 20})"
        else:
            sym_txt = _disp(syms)
        title = f"[PBV2 SHADOW] digest | {_disp(p.get('anchor') or p.get('window_id'))}"
        fields = [
            _field(
                "Window",
                "\n".join(
                    [
                        f"date: {_disp(p.get('date'))}",
                        f"window: {_disp(p.get('window_id'))}",
                        f"anchor: {_disp(p.get('anchor'))}",
                        f"role: {_disp(p.get('role') or 'SHADOW_ONLY')}",
                    ]
                ),
            ),
            _field(
                "Counts",
                "\n".join(
                    [
                        f"evaluated: {_disp(p.get('evaluated'))}",
                        f"accepted: {_disp(p.get('accepted'))}",
                        f"already_open: {_disp(p.get('already_open'))}",
                        f"cap_blocked: {_disp(p.get('cap_blocked'))}",
                        f"hypothetical fills: {_disp(p.get('hypothetical_fills') if p.get('hypothetical_fills') is not None else p.get('accepted'))}",
                        f"exits: {_disp(p.get('exits'))}",
                        f"pnl: {_disp(p.get('pnl'))}",
                        f"open/cap: {_disp(p.get('open_n'))}/{_disp(p.get('cap'))}",
                    ]
                ),
            ),
            _field("symbols (accepted)", sym_txt),
            _field(
                "Note",
                _disp(
                    p.get("note"),
                    default="SHADOW_ONLY — Primary occupancy unchanged",
                ),
            ),
        ]
        return {
            "title": title[:256],
            "description": "SHADOW_ONLY digest (aggregated)",
            "color": COLOR_SHADOW,
            "fields": fields,
            "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
        }

    title = f"[PBV2 SHADOW] 日次結果 | {_disp(p.get('date'))}"
    fields = [
        _field("成績", "\n".join([
            f"損益: {_disp(p.get('pnl'))}",
            f"取引数: {_disp(p.get('trades') or p.get('positions'))}",
            f"勝/負: {_disp(p.get('wins'))}/{_disp(p.get('losses'))}",
            f"PF: {_disp(p.get('pf'))}",
        ])),
        _field("Best / Worst", "\n".join([
            f"Best: {_disp(p.get('best'))}",
            f"Worst: {_disp(p.get('worst'))}",
        ]), inline=True),
        _field("EXIT理由内訳", _disp(p.get("exit_reason_breakdown"), default="—")),
    ]
    return {
        "title": title[:256],
        "description": "SHADOW_ONLY",
        "color": COLOR_SHADOW,
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_1m_shadow_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    title = f"[V1R 1M SHADOW] 日次結果 | {_disp(p.get('date'))}"
    fields = [
        _field("Cash", "\n".join([
            f"開始cash: {_disp(p.get('start_cash'))}",
            f"終了cash: {_disp(p.get('end_cash') or p.get('cash'))}",
            f"損益: {_disp(p.get('pnl'))}",
            f"Return: {_disp(p.get('return_pct'))}",
        ])),
        _field("制約", "\n".join([
            f"FILL: {_disp(p.get('fills'))}",
            f"Capital Blocked: {_disp(p.get('capital_blocked'))}",
            f"最大使用資金: {_disp(p.get('max_invested'))}",
            f"最大DD: {_disp(p.get('max_dd'))}",
            f"勝/負: {_disp(p.get('wins'))}/{_disp(p.get('losses'))}",
        ])),
    ]
    return {
        "title": title[:256],
        "description": "SHADOW_ONLY_DIAGNOSTIC",
        "color": COLOR_SHADOW,
        "fields": fields,
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def build_cap_blocked_embed(p: dict[str, Any], *, test_only: bool = False) -> dict[str, Any]:
    title = _header("⛔ CAP BLOCKED", str(p.get("symbol") or ""), p.get("symbol_name"))
    return {
        "title": title[:256],
        "description": f"rank: {_disp(p.get('rank'))}\nreason: {_disp(p.get('reason'), default='CAPACITY_BLOCKED')}",
        "color": 0xF1C40F,
        "fields": [],
        "footer": TEST_FOOTER if test_only else PAPER_FOOTER,
    }


def _field_text(embed: dict[str, Any]) -> str:
    parts = [str(embed.get("title") or ""), str(embed.get("description") or "")]
    for f in embed.get("fields") or []:
        parts.append(str(f.get("name") or ""))
        parts.append(str(f.get("value") or ""))
    return "\n".join(parts)


def assert_entry_fields(embed: dict[str, Any]) -> dict[str, bool]:
    t = _field_text(embed)
    return {
        "symbol_name": ".T" in t or "ENTRY" in t,
        "time": "エントリー判定" in t or ":" in t,
        "price": "指値" in t,
        "qty": "株" in t,
        "rank_score": "順位" in t and "Score" in t,
        "cap": "CAP" in t,
        "previous_trade": ("前回取引" in t) or ("前回EXIT" in t),
        "v1r_entry_reason": "ENTRY判断" in t or "固定Anchor" in t,
    }


def assert_fill_fields(embed: dict[str, Any]) -> dict[str, bool]:
    t = _field_text(embed)
    return {
        "entry_fill_time": "ENTRY判定" in t and "FILL時刻" in t,
        "fill_price": "約定価格" in t,
        "qty": "株" in t,
        "rank_score": "順位" in t and "Score" in t,
        "fill_delay": "約定時間" in t,
        "cap": "CAP" in t,
        "exit_target": "EXIT予定" in t,
    }


def assert_exit_fields(embed: dict[str, Any]) -> dict[str, bool]:
    t = _field_text(embed)
    return {
        "entry_exit_time": "ENTRY時刻" in t and "EXIT時刻" in t,
        "entry_exit_price": "ENTRY価格" in t and "EXIT価格" in t,
        "qty": "株" in t,
        "pnl_yen": "損益" in t and "円" in t,
        "pnl_pct": "%" in t,
        "daily_symbol_pnl": "本日同銘柄累計" in t,
        "daily_v1r_pnl": "本日V1R累計" in t,
        "hold": "保有時間" in t,
        "reason": "EXIT理由" in t and "FIRST_VALID" not in t,
        "mfe": "MFE" in t,
        "mae": "MAE" in t,
        "color_red": int(embed.get("color") or 0) == COLOR_EXIT,
    }


def assert_expired_fields(embed: dict[str, Any]) -> dict[str, bool]:
    t = _field_text(embed)
    return {
        "time": "ENTRY判定" in t or "失効時刻" in t,
        "price": "指値" in t,
        "qty": "株" in t,
        "rank_score": "順位" in t and "Score" in t,
        "expiry_reason": "約定せず" in t or "取引" in t,
    }
