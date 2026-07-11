"""Phase687W10 — Human-facing Discord formatters (JP)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

# ENTRY reason code → Japanese
ENTRY_REASON_JP: dict[str, str] = {
    "momentum": "モメンタム条件成立",
    "board_bid_dominant": "板の買い優勢",
    "opening_range": "Opening Range条件成立",
    "pullback_reaccel": "押し目後の再加速を確認",
    "flat_band": "Flat-band条件成立",
    "pbv2": "Pullback v2条件成立",
    "quality_gate": "品質ゲート通過",
}

EXIT_REASON_JP: dict[str, str] = {
    "hard_stop": "ハードストップ",
    "HARD_STOP": "ハードストップ",
    "trailing": "板崩れによるトレーリングEXIT",
    "TRAILING_STOP": "板崩れによるトレーリングEXIT",
    "profit_protect": "利益保護",
    "PROFIT_PROTECT": "利益保護",
    "no_progress": "値動き停滞",
    "NO_PROGRESS": "値動き停滞",
    "session_end": "セッション終了",
    "SESSION_END": "セッション終了",
    "force_close": "セッション終了",
    "TIME_EXIT": "時間切れEXIT",
}


def entry_reason_jp(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "条件成立"
    if s in ENTRY_REASON_JP:
        return ENTRY_REASON_JP[s]
    low = s.lower()
    for k, v in ENTRY_REASON_JP.items():
        if k in low:
            return v
    # already Japanese-ish
    if any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in s):
        return s
    return s


def exit_reason_jp(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "EXIT"
    if s in EXIT_REASON_JP:
        return EXIT_REASON_JP[s]
    for k, v in EXIT_REASON_JP.items():
        if k.lower() in s.lower():
            return v
    if any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in s):
        return s
    return s


def drop_none_lines(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        if line is None:
            continue
        if ": None" in line or line.strip().endswith(": "):
            continue
        if "None" == line.strip():
            continue
        out.append(line)
    return out


def format_entry_actual(
    *,
    symbol: str,
    price: Any,
    qty: Any,
    notional: Any,
    entry_method: str,
    score: Any,
    reason: Any,
    at: str,
    session: str,
    open_count: Any,
    capture_status: str = "",
) -> str:
    lines = [
        "[ENTRY - ACTUAL]",
        f"銘柄: {symbol}",
        f"価格: {price}",
        f"株数: {qty}",
        f"想定建玉金額: {notional}",
        f"ENTRY方式: {entry_method}",
        f"ENTRY score: {score}",
        f"ENTRY理由: {entry_reason_jp(reason)}",
        f"時刻: {at}",
        f"session: {session}",
        f"現在保有数: {open_count}",
    ]
    if capture_status:
        lines.append(f"Capture状態: {capture_status}")
    return "\n".join(drop_none_lines(lines))


def format_exit_actual(
    *,
    symbol: str,
    entry_price: Any,
    exit_price: Any,
    qty: Any,
    pnl: Any,
    pnl_100: Any,
    hold_time: Any,
    reason: Any,
    mfe: Any,
    mae: Any,
    session: str,
    capture_status: str = "",
) -> str:
    lines = [
        "[EXIT - ACTUAL]",
        f"銘柄: {symbol}",
        f"ENTRY価格: {entry_price}",
        f"EXIT価格: {exit_price}",
        f"株数: {qty}",
        f"損益: {pnl}",
        f"100株換算損益: {pnl_100}",
        f"保有時間: {hold_time}",
        f"EXIT理由: {exit_reason_jp(reason)}",
        f"最大含み益: {mfe}",
        f"最大含み損: {mae}",
        f"session: {session}",
    ]
    if capture_status:
        lines.append(f"Capture状態: {capture_status}")
    return "\n".join(drop_none_lines(lines))


def format_paper_blocked(
    *,
    failed_step: str,
    reason: str,
    next_action: str,
    capture_status: str,
    capture_pid: Any,
    capture_output: str,
    capture_continues: bool,
) -> str:
    title = "[PAPER BLOCKED - CAPTURE CONTINUES]" if capture_continues else "[PAPER BLOCKED]"
    return "\n".join(
        drop_none_lines(
            [
                title,
                f"failed step: {failed_step}",
                f"reason: {reason}",
                f"next action: {next_action}",
                f"Capture status: {capture_status}",
                f"Capture PID: {capture_pid}",
                f"Capture output: {capture_output}",
                f"Capture continues: {'yes' if capture_continues else 'no'}",
                "real orders disabled: yes",
            ]
        )
    )


def format_capture_started(data: Mapping[str, Any]) -> str:
    return "\n".join(
        drop_none_lines(
            [
                "[MARKET CAPTURE STARTED]",
                f"date: {data.get('date')}",
                f"PID: {data.get('pid')}",
                f"registered symbols: {data.get('symbols')}",
                f"topology: {data.get('topology')}",
                f"output path: {data.get('output')}",
                "Paper dependency: NONE",
            ]
        )
    )


def format_capture_degraded(data: Mapping[str, Any]) -> str:
    return "\n".join(
        drop_none_lines(
            [
                "[MARKET CAPTURE DEGRADED]",
                f"reason: {data.get('reason')}",
                f"disconnect duration: {data.get('disconnect_duration')}",
                f"dropped events: {data.get('drops')}",
                f"registration mismatch: {data.get('registration_mismatch')}",
                f"queue high-water: {data.get('queue_high_water')}",
                f"last event: {data.get('last_event')}",
                f"Paper status: {data.get('paper_status')}",
                f"operator action: {data.get('operator_action') or 'Investigate capture gaps / reconnect'}",
            ]
        )
    )


def format_capture_finished(data: Mapping[str, Any]) -> str:
    return "\n".join(
        drop_none_lines(
            [
                "[MARKET CAPTURE FINISHED]",
                f"total events: {data.get('events')}",
                f"symbols seen: {data.get('symbols')}",
                f"first/last event: {data.get('first_event')} / {data.get('last_event')}",
                f"disconnects: {data.get('disconnects')}",
                f"drops: {data.get('drops')}",
                f"gaps: {data.get('gaps')}",
                f"Capture status: {data.get('status')}",
                f"Capture Seal: {data.get('seal')}",
                f"total size: {data.get('total_size')}",
            ]
        )
    )


def format_critical_safety(data: Mapping[str, Any]) -> str:
    return "\n".join(
        drop_none_lines(
            [
                "[CRITICAL SAFETY]",
                f"incident id: {data.get('incident_id')}",
                f"severity: {data.get('severity')}",
                f"detected at: {data.get('detected_at')}",
                f"affected session: {data.get('session')}",
                f"failure type: {data.get('failure_type')}",
                f"recovery mode: {data.get('recovery_mode')}",
                f"ENTRY allowed: {data.get('entry_allowed')}",
                f"EXIT allowed: {data.get('exit_allowed')}",
                f"actual submit/cancel: {data.get('submit_cancel')}",
                f"operator action: {data.get('operator_action')}",
                f"audit bundle path: {data.get('artifact_path')}",
            ]
        )
    )


def format_shadow_summary(data: Mapping[str, Any]) -> str:
    days = int(data.get("forward_sessions") or 0)
    if days < 5:
        adopt_extra = "DATA COLLECTION ONLY"
    elif days < 10:
        adopt_extra = "RULE DISCOVERY NOT ALLOWED"
    else:
        adopt_extra = "RULE DISCOVERY REVIEW ALLOWED"
    lines = [
        "[SHADOW SUMMARY]",
        f"shadow name: {data.get('shadow_name')}",
        f"candidates: {data.get('candidates')}",
        f"hypothetical fills: {data.get('hypothetical_fills')}",
        f"hypothetical PnL yen_100: {data.get('hypothetical_pnl')}",
        f"actual overlap: {data.get('actual_overlap')}",
        f"data completeness: {data.get('data_completeness')}",
        f"forward sessions: {days}",
        "ADOPTION STATUS: NOT ADOPTED",
        adopt_extra,
    ]
    return "\n".join(drop_none_lines(lines))


def truncate_for_discord(text: str, *, max_len: int = 1900) -> list[str]:
    """Split into at most 3 Discord messages."""
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    rest = text
    while rest and len(parts) < 3:
        parts.append(rest[:max_len])
        rest = rest[max_len:]
    if rest and len(parts) == 3:
        parts[2] = parts[2][: max_len - 40] + "\n…(詳細は artifact path を参照)"
    return parts
