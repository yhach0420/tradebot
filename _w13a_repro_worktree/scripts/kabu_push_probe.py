#!/usr/bin/env python3
"""
kabuステーション® PUSH（WebSocket）PoC。

- token 発行 → PUT /register → ws 接続 → 受信 JSON を JSONL 保存
- 期待フィールドの有無・1 分足ビルダ・セッション VWAP フィールド・recent_5m_high 近似をログ

土日・取引時間外は接続失敗し得る。その場合は --spec-only で URL / 期待キーだけ確認可能。

例::
    python scripts/kabu_push_probe.py --symbol 9984 --seconds 120
    python scripts/kabu_push_probe.py --spec-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_env_loaded() -> None:
    root = _project_root()
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=root / ".env", override=False)
    except ImportError:
        pass


def _setup_logging() -> logging.Logger:
    log = logging.getLogger("kabu_push_probe")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(sh)
    log.propagate = False
    return log


def _print_spec_only(base_url: str, log: logging.Logger) -> int:
    sys.path.insert(0, str(_project_root()))
    from src.kabu_push_client import EXPECTED_PUSH_FIELDS_STOCK, rest_base_to_websocket_url

    ws = rest_base_to_websocket_url(base_url.rstrip("/"))
    log.info("REST base: %s", base_url.rstrip("/"))
    log.info("WebSocket: %s", ws)
    log.info("公式 PUSH は値更新時の board 相当。生の約定ティック列ではない。")
    log.info("検証対象キー (株式想定・抜粋): %s", ", ".join(EXPECTED_PUSH_FIELDS_STOCK))
    return 0


async def _run_async(
    *,
    base_url: str,
    token: str,
    symbol: str,
    exchange: int,
    seconds: float,
    max_messages: int | None,
    recv_poll_sec: float | None,
    jsonl_path: Path,
    unregister_on_exit: bool,
    log: logging.Logger,
) -> dict[str, Any]:
    sys.path.insert(0, str(_project_root()))
    from src.kabu_bar_builder import (
        MinuteBarBuilderFromPush,
        recent_n_minute_high_excluding_current,
        vwap_from_push_field,
        vwap_typical_from_bars,
    )
    from src.kabu_push_client import (
        EXPECTED_PUSH_FIELDS_STOCK,
        iter_push_board_messages,
        register_push_symbols,
        rest_base_to_websocket_url,
        unregister_all_push,
    )

    reg = register_push_symbols(token=token, symbols_spec=[(symbol, exchange)], rest_base_url=base_url)
    log.info("register response: %s", reg)

    ws_url = rest_base_to_websocket_url(base_url.rstrip("/"))
    log.info("connecting WebSocket: %s", ws_url)

    field_hits: Counter[str] = Counter()
    key_union: set[str] = set()
    message_count = 0
    last_msg: dict[str, Any] | None = None
    completed: list = []
    builder = MinuteBarBuilderFromPush()
    listen_ended_reason = "unknown"

    t0 = time.monotonic()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with jsonl_path.open("a", encoding="utf-8") as jf:
            async for payload in iter_push_board_messages(ws_url, recv_poll_sec=recv_poll_sec):
                line = json.dumps(payload, ensure_ascii=False)
                jf.write(line + "\n")
                message_count += 1
                last_msg = payload

                for fk in EXPECTED_PUSH_FIELDS_STOCK:
                    if fk in payload and payload[fk] is not None:
                        field_hits[fk] += 1
                key_union.update(payload.keys())

                bar = builder.update(payload)
                if bar is not None:
                    completed.append(bar)
                    log.info(
                        "1m bar closed utc=%s O=%s H=%s L=%s C=%s vol_d=%s",
                        bar.minute_start_utc.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume_delta,
                    )

                if message_count % 20 == 0:
                    log.info(
                        "messages=%s elapsed=%.1fs",
                        message_count,
                        time.monotonic() - t0,
                    )

                if max_messages is not None and message_count >= max_messages:
                    listen_ended_reason = "max_messages"
                    break

                elapsed = time.monotonic() - t0
                if elapsed >= seconds:
                    listen_ended_reason = "elapsed_limit"
                    break
    finally:
        if unregister_on_exit:
            try:
                unr = unregister_all_push(token=token, rest_base_url=base_url)
                log.info("unregister/all response: %s", unr)
            except Exception as e:  # noqa: BLE001
                log.warning("unregister/all failed: %s", e)

    flush_bar = builder.flush()
    if flush_bar is not None:
        completed.append(flush_bar)
        log.info("flushed incomplete bucket as bar utc=%s", flush_bar.minute_start_utc.isoformat())
    expected_template = set(EXPECTED_PUSH_FIELDS_STOCK)
    unexpected = sorted(key_union - expected_template)

    if listen_ended_reason == "unknown":
        listen_ended_reason = "stopped_before_elapsed_budget" if message_count else "no_payloads_received"
    push_vwap = vwap_from_push_field(last_msg) if last_msg else None
    typical_vwap = vwap_typical_from_bars(completed) if completed else None
    r5 = recent_n_minute_high_excluding_current(completed, n=5) if completed else None

    summary = {
        "websocket_url": ws_url,
        "register_response": reg,
        "message_count": message_count,
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "recv_poll_sec": recv_poll_sec,
        "listen_ended_reason": listen_ended_reason,
        "jsonl_path": str(jsonl_path),
        "field_hits_any_message": dict(field_hits),
        "field_missing_in_all_messages": sorted(
            fk for fk in EXPECTED_PUSH_FIELDS_STOCK if field_hits[fk] == 0
        ),
        "unexpected_keys_union_sample": unexpected[:80],
        "unexpected_keys_union_count": len(unexpected),
        "minute_bars_completed_count": len(completed),
        "last_message_keys": sorted(last_msg.keys()) if last_msg else [],
        "vwap_from_last_push_payload": push_vwap,
        "vwap_typical_price_from_completed_bars": typical_vwap,
        "recent_5m_high_excluding_last_open_bar": r5,
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return summary


def main() -> int:
    _ensure_env_loaded()
    root = _project_root()
    log = _setup_logging()

    parser = argparse.ArgumentParser(description="kabu PUSH WebSocket 接続 PoC（JSONL 保存）")
    parser.add_argument("--symbol", default="9984", help="銘柄コード（例: 9984）")
    parser.add_argument(
        "--exchange",
        type=int,
        default=int(os.environ.get("KABU_EXCHANGE", "1")),
        help="市場コード（デフォルト: 1 東証）",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("KABU_API_BASE", "http://localhost:18080/kabusapi"),
        help="REST API ベース URL",
    )
    parser.add_argument("--seconds", type=float, default=60.0, help="受信を続ける最大秒数")
    parser.add_argument("--max-messages", type=int, default=None, help="受信メッセージ数の上限（任意）")
    parser.add_argument(
        "--recv-poll",
        type=float,
        default=15.0,
        metavar="SEC",
        help="ws.recv のポーリング間隔秒。0 で完全ブロック（未約定では永久待ちになり得る）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="出力先（既定: results/kabu_push_probe/YYYYMMDD/）",
    )
    parser.add_argument(
        "--no-unregister",
        action="store_true",
        help="終了時に unregister/all を呼ばない",
    )
    parser.add_argument(
        "--spec-only",
        action="store_true",
        help="接続せず REST→WS URL と期待 PUSH キー一覧のみ表示",
    )
    args = parser.parse_args()

    if args.spec_only:
        return _print_spec_only(args.base_url, log)

    password = os.environ.get("KABU_API_PASSWORD", "").strip()
    if not password:
        log.error("KABU_API_PASSWORD が未設定です（.env を確認）")
        return 2

    sys.path.insert(0, str(root))
    from src.kabu_api_client import KabuApiClient, KabuApiError

    stamp_d = datetime.now().strftime("%Y%m%d")
    stamp_t = datetime.now().strftime("%H%M%S")
    out_dir = args.output_dir or (root / "results" / "kabu_push_probe" / stamp_d)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"push_probe_{args.symbol}_{args.exchange}_{stamp_t}.jsonl"
    summary_path = out_dir / f"push_probe_{args.symbol}_{args.exchange}_{stamp_t}_summary.json"

    try:
        client = KabuApiClient(base_url=args.base_url)
        token = client.issue_token(password)
        log.info("token 取得成功")
    except KabuApiError as e:
        log.error("%s", e)
        return 3

    recv_poll_sec: float | None = None if args.recv_poll == 0 else args.recv_poll
    hard_cap = max(args.seconds + 120.0, 180.0)

    try:
        summary = asyncio.run(
            asyncio.wait_for(
                _run_async(
                    base_url=args.base_url.rstrip("/"),
                    token=token,
                    symbol=args.symbol,
                    exchange=args.exchange,
                    seconds=args.seconds,
                    max_messages=args.max_messages,
                    recv_poll_sec=recv_poll_sec,
                    jsonl_path=jsonl_path,
                    unregister_on_exit=not args.no_unregister,
                    log=log,
                ),
                timeout=hard_cap,
            )
        )
    except asyncio.TimeoutError:
        log.error(
            "硬性タイムアウト %.0fs に達しました。"
            "PUSH が来ず recv が進まなかった可能性（土日終了・API 未設定・ポート違い等）があります。",
            hard_cap,
        )
        return 6
    except Exception as e:  # noqa: BLE001
        log.exception("PUSH run failed: %s", e)
        return 4

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("summary written: %s", summary_path.relative_to(root))
    log.info("jsonl written: %s", jsonl_path.relative_to(root))
    log.info(
        "messages=%s end=%s recv_poll=%r fields_never_seen=%s bars=%s push_vwap=%s typical_vwap=%s recent_5m_high=%s",
        summary["message_count"],
        summary["listen_ended_reason"],
        summary["recv_poll_sec"],
        summary["field_missing_in_all_messages"],
        summary["minute_bars_completed_count"],
        summary["vwap_from_last_push_payload"],
        summary["vwap_typical_price_from_completed_bars"],
        summary["recent_5m_high_excluding_last_open_bar"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
