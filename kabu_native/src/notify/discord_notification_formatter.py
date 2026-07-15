"""Phase687W10 / Phase687W25 — Human-facing Discord formatters (JP)."""

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
    "hard_stop": "損切り",
    "HARD_STOP": "損切り",
    "stop_hit": "損切り",
    "trailing": "トレーリング決済",
    "TRAILING_STOP": "トレーリング決済",
    "trailing_mfe_exit": "トレーリング決済",
    "profit_protect": "利益保護",
    "PROFIT_PROTECT": "利益保護",
    "no_progress": "停滞ポジション整理",
    "NO_PROGRESS": "停滞ポジション整理",
    "no_progress_exit": "停滞ポジション整理",
    "session_end": "セッション終了",
    "SESSION_END": "セッション終了",
    "force_close": "セッション終了",
    "TIME_EXIT": "時間切れEXIT",
    "morning_session_close": "前場終了前の決済",
    "afternoon_session_close": "後場終了前の決済",
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
    """Research/demo formatter (not paper runtime path)."""
    lines = [
        "[ENTRY]",
        f"銘柄: {symbol}",
        f"価格: {price}",
        f"方式: {entry_method}",
        f"score_v2: {score}",
        f"ENTRY理由: {entry_reason_jp(reason)}",
        f"時刻: {at}",
        f"保有: {open_count}",
        "PAPER ONLY / 実注文なし",
    ]
    if capture_status and capture_status != "CAPTURE_ONLINE":
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
    """Research/demo formatter (not paper runtime path)."""
    lines = [
        "[EXIT]",
        f"銘柄: {symbol}",
        f"{entry_price} → {exit_price}",
        f"損益: {pnl_100}",
        f"保有時間: {hold_time}",
        f"理由: {exit_reason_jp(reason)}",
        f"MFE: {mfe}",
        f"MAE: {mae}",
        "PAPER ONLY / 実注文なし",
    ]
    if capture_status and capture_status != "CAPTURE_ONLINE":
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
    cap_st = str(capture_status or "N/A")
    if cap_st == "CAPTURE_ONLINE":
        cap_st = "READY_FOR_FANOUT"
    return "\n".join(
        drop_none_lines(
            [
                title,
                f"failed step: {failed_step}",
                f"reason: {reason}",
                f"next action: {next_action}",
                f"Capture status: {cap_st}",
                f"Capture PID: {capture_pid}",
                f"Capture output: {capture_output}",
                f"Capture continues: {'yes' if capture_continues else 'no'}",
                "Real orders: DISABLED",
                "Capture topology: SINGLE_INGRESS_LOCAL_FANOUT",
            ]
        )
    )


def format_capture_status_body(data: Mapping[str, Any]) -> str:
    """Phase687W25 Capture operator text. Never emits CAPTURE_ONLINE."""
    status = str(data.get("status") or data.get("capture_status") or "").upper()
    # Normalize legacy
    if status in ("CAPTURE_ONLINE", "ONLINE", "CONNECTED"):
        # Prefer explicit fan-out / write state when available
        if int(data.get("written") or data.get("events") or 0) > 0:
            status = "CAPTURE_WRITING"
        elif str(data.get("ingress") or "") == "paper_fanout":
            status = "CAPTURE_READY_FOR_FANOUT"
        else:
            status = "CAPTURE_READY_FOR_FANOUT"

    short = status.replace("CAPTURE_", "") if status.startswith("CAPTURE_") else status
    topology = str(data.get("topology") or "SINGLE_INGRESS_LOCAL_FANOUT")
    if topology == "PASSIVE_DUAL_WEBSOCKET":
        topology = "SINGLE_INGRESS_LOCAL_FANOUT"

    received = data.get("received", data.get("on_message_count", data.get("events", 0)))
    written = data.get("written", data.get("events", 0))
    bytes_n = data.get("bytes", data.get("bytes_written", 0))
    drops = data.get("drops", data.get("dropped", 0))
    malformed = data.get("malformed", 0)

    if short in ("READY_FOR_FANOUT", "STARTING"):
        return "\n".join(
            drop_none_lines(
                [
                    "[CAPTURE]",
                    f"状態: {short}",
                    "Paper PUSH待機中",
                    f"保存件数: {written or 0}",
                    f"topology: {topology}",
                ]
            )
        )
    if short == "RECEIVING":
        return "\n".join(
            drop_none_lines(
                [
                    "[CAPTURE]",
                    "状態: RECEIVING",
                    f"受信: {received}",
                    f"保存: {written}",
                    f"topology: {topology}",
                ]
            )
        )
    if short == "WRITING":
        return "\n".join(
            drop_none_lines(
                [
                    "[CAPTURE]",
                    "状態: WRITING",
                    f"受信: {received}",
                    f"保存: {written}",
                    f"bytes: {bytes_n}",
                    f"drop: {drops}",
                    f"malformed: {malformed}",
                    f"topology: {topology}",
                ]
            )
        )
    if short == "STALE":
        return "\n".join(
            drop_none_lines(
                [
                    "[CAPTURE WARNING]",
                    "状態: STALE",
                    f"最終PUSHから: {data.get('stale_age_sec', 'N/A')}秒",
                    f"Paper本体: {data.get('paper_status') or 'RUNNING'}",
                    "保存停止の可能性あり",
                ]
            )
        )
    if short in ("FAILED", "WRITE_FAILED", "DEGRADED"):
        kind = "[CAPTURE ERROR]" if short != "DEGRADED" else "[CAPTURE WARNING]"
        return "\n".join(
            drop_none_lines(
                [
                    kind,
                    f"状態: {short}",
                    f"last_error: {data.get('reason') or data.get('last_error') or 'N/A'}",
                    f"Paper本体への影響: {data.get('paper_impact') or data.get('paper_status') or 'NONE'}",
                    "fan-out: fail-open",
                ]
            )
        )
    # finished / complete
    return "\n".join(
        drop_none_lines(
            [
                "[CAPTURE]",
                f"状態: {short or 'FINISHED'}",
                f"保存: {written}",
                f"symbols: {data.get('symbols')}",
                f"drop: {drops}",
                f"topology: {topology}",
                f"seal: {data.get('seal')}",
            ]
        )
    )


def format_capture_started(data: Mapping[str, Any]) -> str:
    payload = dict(data)
    payload.setdefault("status", "CAPTURE_READY_FOR_FANOUT")
    payload.setdefault("topology", data.get("topology") or "SINGLE_INGRESS_LOCAL_FANOUT")
    payload.setdefault("written", 0)
    return format_capture_status_body(payload)


def format_capture_degraded(data: Mapping[str, Any]) -> str:
    payload = dict(data)
    payload.setdefault("status", "CAPTURE_FAILED")
    payload.setdefault("reason", data.get("reason"))
    payload.setdefault("paper_status", data.get("paper_status") or "NONE")
    return format_capture_status_body(payload)


def format_capture_finished(data: Mapping[str, Any]) -> str:
    payload = dict(data)
    payload.setdefault("status", data.get("status") or "FINISHED")
    payload.setdefault("written", data.get("events"))
    payload.setdefault("topology", "SINGLE_INGRESS_LOCAL_FANOUT")
    return format_capture_status_body(payload)


def _communication_impact_defaults(target: str) -> tuple[str, str]:
    """Return (entry_eval, paper_impact) defaults by failure target.

    Discord / Capture fan-out failures must not look like Kabu PUSH outages.
    """
    t = str(target or "").strip().lower()
    if "discord" in t or "webhook" in t:
        return "継続", "NONE"
    if "fan-out" in t or "fanout" in t or "capture" in t:
        return "継続", "NONE"
    if any(k in t for k in ("kabu", "push", "rest", "token", "registration", "登録")):
        return "一時停止", "DEGRADED"
    # Unknown target: do not assume PUSH outage
    return "継続", "NONE"


def format_communication_degraded(data: Mapping[str, Any]) -> str:
    """Formatter only — not wired to a new Discord send path in W25."""
    target = str(data.get("target") or "Kabu PUSH")
    default_eval, default_impact = _communication_impact_defaults(target)
    entry_eval = data.get("entry_eval")
    if entry_eval is None or str(entry_eval).strip() == "":
        entry_eval = default_eval
    paper_impact = data.get("paper_impact")
    if paper_impact is None or str(paper_impact).strip() == "":
        paper_impact = default_impact
    return "\n".join(
        drop_none_lines(
            [
                "[COMMUNICATION DEGRADED]",
                f"対象: {target}",
                f"状態: {data.get('status') or 'DEGRADED'}",
                f"最終PUSHから: {data.get('last_push_age_sec', 'N/A')}秒",
                f"Paper process: {data.get('paper_process') or 'alive'}",
                f"ENTRY評価: {entry_eval}",
                f"Paper本体への影響: {paper_impact}",
                f"再接続: {data.get('reconnect') or 'N/A'}",
                "実注文: DISABLED",
            ]
        )
    )


def format_communication_recovered(data: Mapping[str, Any]) -> str:
    """Formatter only — not wired to a new Discord send path in W25."""
    target = str(data.get("target") or "Kabu PUSH")
    return "\n".join(
        drop_none_lines(
            [
                "[COMMUNICATION RECOVERED]",
                f"対象: {target}",
                f"停止時間: {data.get('down_sec', 'N/A')}秒",
                f"PUSH再開: {data.get('push_resumed') or 'YES'}",
                f"candidate評価再開: {data.get('candidate_resumed') or 'YES'}",
                f"ExposureGate再開: {data.get('gate_resumed') or 'YES'}",
                f"登録銘柄: {data.get('registered') or '50 / 50'}",
                "実注文: DISABLED",
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
    pnl = data.get("hypothetical_pnl")
    if pnl is None and data.get("outcome_mapping_unavailable"):
        pnl_line = "hypothetical PnL yen_100: N/A / outcome mapping unavailable"
    else:
        pnl_line = f"hypothetical PnL yen_100: {pnl}"
    lines = [
        "[SHADOW OBSERVATION]",
        f"名称: {data.get('shadow_name')}",
        f"block数: {data.get('blocks') or data.get('block_count') or 'N/A'}",
        f"差分: {data.get('delta_yen') or data.get('hypothetical_pnl') or 'N/A'}",
        "判定: observation only",
        # Extended research lines (text/artifact callers; Discord uses embed card)
        f"対象件数: {data.get('candidates')}",
        f"hypothetical fills: {data.get('hypothetical_fills')}",
        pnl_line,
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
