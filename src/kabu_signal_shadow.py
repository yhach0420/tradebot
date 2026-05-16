"""
paper_trade 向け kabu_signal_v1 シャドウ評価（ログ/CSV/JSONL のみ）。

Yahoo ENTRY/EXIT/Discord には一切接続しない。
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.kabu_api_client import DEFAULT_BASE_URL, KabuApiClient, KabuApiError, build_symbol_key
from src.kabu_signal_engine import (
    PushHistoryRing,
    evaluate_kabu_signal_v1,
)
from src.signal_engine import BreakoutStateTracker

SHADOW_CSV_FIELDS: tuple[str, ...] = (
    "timestamp",
    "poll_ts_jst",
    "poll_number",
    "symbol",
    "current_price",
    "signal_score",
    "notify_entry_eligible",
    "timing_ok",
    "tier",
    "reject_reasons",
    "quote_age_sec",
    "spread_bps",
    "board_imbalance",
    "vwap_distance_pct",
    "high_proximity_ratio",
    "push_samples_1m",
    "rolling_high_5m",
    "trigger_level",
    "breakout_event",
    "data_mode",
    "evaluated_at_utc",
)


def kabu_signal_shadow_enabled(*, cli_flag: bool = False) -> bool:
    if cli_flag:
        return True
    raw = os.environ.get("KABU_SIGNAL_SHADOW", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def default_shadow_tier() -> str:
    t = os.environ.get("KABU_SIGNAL_SHADOW_TIER", "B").strip().upper()
    return t if t in ("A", "B", "C") else "B"


def _yahoo_code(symbol: str) -> Optional[str]:
    s = symbol.strip().upper()
    if s.endswith(".T"):
        code = s[:-2].strip()
        if code.isdigit():
            return code
    return None


@dataclass
class KabuSignalShadowRunner:
    """銘柄ごとに PUSH 履歴リング・breakout 状態を保持し、日次ファイルへ追記。"""

    script_dir: str
    tier: str = "B"
    _day_key: str = ""
    _csv_path: Optional[Path] = None
    _jsonl_path: Optional[Path] = None
    _csv_header_written: bool = False
    _histories: dict[str, PushHistoryRing] = field(default_factory=dict)
    _trackers: dict[str, BreakoutStateTracker] = field(default_factory=dict)
    _client: Optional[KabuApiClient] = None
    _token: Optional[str] = None

    def __post_init__(self) -> None:
        self.tier = self.tier.upper() if self.tier.upper() in ("A", "B", "C") else "B"

    def _client_instance(self) -> KabuApiClient:
        if self._client is None:
            base = os.environ.get("KABU_API_BASE", "").strip() or DEFAULT_BASE_URL
            self._client = KabuApiClient(base_url=base)
        return self._client

    def _fetch_board(self, symbol_yahoo: str) -> dict[str, Any]:
        code = _yahoo_code(symbol_yahoo)
        if code is None:
            raise ValueError(f"unsupported symbol format: {symbol_yahoo!r}")
        pwd = os.environ.get("KABU_API_PASSWORD", "").strip()
        if not pwd:
            raise KabuApiError("KABU_API_PASSWORD unset")
        ex = os.environ.get("KABU_EXCHANGE", "1").strip() or "1"
        key = build_symbol_key(code, ex)
        client = self._client_instance()

        def _once() -> dict[str, Any]:
            if not self._token:
                self._token = client.issue_token(pwd)
            return dict(client.get_board(key, token=self._token))

        try:
            return _once()
        except KabuApiError:
            self._token = None
            return _once()

    def ingest_push_message(self, symbol_yahoo: str, msg: dict[str, Any]) -> None:
        """将来の WebSocket フィード用。paper_trade 本体には影響しない。"""
        ring = self._histories.setdefault(symbol_yahoo, PushHistoryRing())
        ring.add_from_board(msg)

    def _ensure_output_paths(self, day_key: str) -> None:
        if self._day_key == day_key and self._csv_path is not None:
            return
        self._day_key = day_key
        root = Path(self.script_dir) / "results" / "kabu_signal_shadow" / day_key
        root.mkdir(parents=True, exist_ok=True)
        self._csv_path = root / f"shadow_eval_{day_key}.csv"
        self._jsonl_path = root / f"shadow_eval_{day_key}.jsonl"
        self._csv_header_written = self._csv_path.is_file() and self._csv_path.stat().st_size > 0

    def _row_from_result(
        self,
        result_dict: dict[str, Any],
        *,
        poll_ts_jst: str,
        poll_number: int,
    ) -> dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        rejects = result_dict.get("reject_reasons") or []
        if isinstance(rejects, list):
            rejects_str = ";".join(str(x) for x in rejects)
        else:
            rejects_str = str(rejects)
        return {
            "timestamp": ts,
            "poll_ts_jst": poll_ts_jst,
            "poll_number": poll_number,
            "symbol": result_dict.get("symbol", ""),
            "current_price": result_dict.get("current_price"),
            "signal_score": result_dict.get("signal_score"),
            "notify_entry_eligible": result_dict.get("notify_breakout_eligible", False),
            "timing_ok": result_dict.get("timing_ok"),
            "tier": result_dict.get("tier"),
            "reject_reasons": rejects_str,
            "quote_age_sec": result_dict.get("quote_age_sec"),
            "spread_bps": result_dict.get("spread_bps"),
            "board_imbalance": result_dict.get("board_imbalance"),
            "vwap_distance_pct": result_dict.get("vwap_distance_pct"),
            "high_proximity_ratio": result_dict.get("high_proximity_ratio"),
            "push_samples_1m": result_dict.get("push_samples_1m"),
            "rolling_high_5m": result_dict.get("rolling_high_5m"),
            "trigger_level": result_dict.get("trigger_level"),
            "breakout_event": result_dict.get("breakout_event"),
            "data_mode": result_dict.get("data_mode"),
            "evaluated_at_utc": result_dict.get("evaluated_at_utc"),
        }

    def _append_row(self, row: dict[str, Any]) -> None:
        assert self._csv_path is not None
        assert self._jsonl_path is not None
        with self._jsonl_path.open("a", encoding="utf-8") as jf:
            jf.write(json.dumps(row, ensure_ascii=False) + "\n")
        write_header = not self._csv_header_written
        with self._csv_path.open("a", encoding="utf-8", newline="") as cf:
            w = csv.DictWriter(cf, fieldnames=list(SHADOW_CSV_FIELDS), extrasaction="ignore")
            if write_header:
                w.writeheader()
                self._csv_header_written = True
            w.writerow(row)

    def evaluate_symbol(
        self,
        symbol_yahoo: str,
        *,
        day_key: str,
        poll_ts_jst: str,
        poll_number: int,
        log_fn: Optional[Any] = None,
    ) -> None:
        """
        kabu /board を取得して評価し、シャドウ CSV/JSONL に追記する。
        例外は呼び出し元で握る想定（ここでは再送出しない設計も可）。
        """
        self._ensure_output_paths(day_key)
        board = self._fetch_board(symbol_yahoo)
        ring = self._histories.setdefault(symbol_yahoo, PushHistoryRing())
        ring.add_from_board(board)
        tracker = self._trackers.setdefault(symbol_yahoo, BreakoutStateTracker())
        result, tracker = evaluate_kabu_signal_v1(
            board,
            push_history=ring,
            breakout_tracker=tracker,
            tier=self.tier,
            rest_fallback=False,
        )
        self._trackers[symbol_yahoo] = tracker
        row = self._row_from_result(result.to_dict(), poll_ts_jst=poll_ts_jst, poll_number=poll_number)
        self._append_row(row)
        if log_fn is not None:
            log_fn(
                f"[KABU_SHADOW] symbol={symbol_yahoo} score={row.get('signal_score')} "
                f"timing_ok={row.get('timing_ok')} mode={row.get('data_mode')}"
            )

    def evaluate_watch_list(
        self,
        symbols: list[str],
        *,
        day_key: str,
        poll_ts_jst: str,
        poll_number: int,
        now_str_fn: Any,
    ) -> None:
        for sym in symbols:
            try:
                self.evaluate_symbol(
                    sym,
                    day_key=day_key,
                    poll_ts_jst=poll_ts_jst,
                    poll_number=poll_number,
                )
            except Exception as e:
                print(f"[{now_str_fn()}] [KABU_SHADOW] error symbol={sym} err={e!r}")
