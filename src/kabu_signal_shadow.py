"""
paper_trade 向け kabu_signal_v1 / kabu_exit_v1 シャドウ評価（ログ/CSV/JSONL のみ）。

Yahoo ENTRY/EXIT/Discord には一切接続しない。
仮想ポジションはシャドウ専用（Yahoo paper_trade の position とは混在しない）。
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
from src.kabu_exit_engine import KabuExitEvalInput, evaluate_kabu_exit_v1
from src.kabu_signal_engine import (
    PushHistoryRing,
    board_current_price,
    board_time_utc,
    evaluate_kabu_signal_v1,
    flatten_board_dict,
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
    "shadow_has_virtual_position",
    "would_exit",
    "exit_reason",
    "exit_priority",
    "unrealized_pct",
    "mfe_pct",
    "elapsed_min",
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


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


@dataclass
class ShadowVirtualPosition:
    """シャドウ専用の仮想エントリー状態（Yahoo paper_trade とは無関係）。"""

    symbol: str
    entry_price: float
    entry_time: datetime
    trigger_level_at_entry: float
    entry_vwap_dist_pct: Optional[float]
    session_high_at_entry: float
    peak_price_since_entry: float
    tier: str
    imbalance_low_streak: int = 0


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
    _virtual_positions: dict[str, ShadowVirtualPosition] = field(default_factory=dict)
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

    def _maybe_open_virtual_position(
        self,
        symbol_yahoo: str,
        result_dict: dict[str, Any],
        *,
        board_flat: dict[str, Any],
        now: datetime,
    ) -> None:
        if symbol_yahoo in self._virtual_positions:
            return
        tier = str(result_dict.get("tier") or self.tier).upper()
        if tier == "C":
            return
        if not result_dict.get("breakout_event"):
            return
        price = _as_float(result_dict.get("current_price"))
        trigger = _as_float(result_dict.get("trigger_level"))
        if price is None or trigger is None or price <= 0:
            return
        entry_time = board_time_utc(board_flat) or now
        session_high = _as_float(board_flat.get("HighPrice"))
        if session_high is None:
            session_high = price
        vwap_dist = _as_float(result_dict.get("vwap_distance_pct"))
        self._virtual_positions[symbol_yahoo] = ShadowVirtualPosition(
            symbol=symbol_yahoo,
            entry_price=price,
            entry_time=entry_time,
            trigger_level_at_entry=trigger,
            entry_vwap_dist_pct=vwap_dist,
            session_high_at_entry=session_high,
            peak_price_since_entry=price,
            tier=tier,
            imbalance_low_streak=0,
        )

    def _evaluate_exit_shadow(
        self,
        symbol_yahoo: str,
        result_dict: dict[str, Any],
        *,
        board_flat: dict[str, Any],
        ring: PushHistoryRing,
        now: datetime,
    ) -> dict[str, Any]:
        price = _as_float(result_dict.get("current_price")) or board_current_price(board_flat)
        vwap = _as_float(board_flat.get("VWAP"))
        session_high_now = _as_float(board_flat.get("HighPrice"))
        spread_bps = _as_float(result_dict.get("spread_bps"))
        imbalance = _as_float(result_dict.get("board_imbalance"))
        push_1m = int(result_dict.get("push_samples_1m") or 0)
        push_3m_avg = ring.push_samples_avg_per_minute(as_of=now)

        pos = self._virtual_positions.get(symbol_yahoo)
        if pos is None:
            placeholder = float(price) if price is not None and price > 0 else 1.0
            exit_res = evaluate_kabu_exit_v1(
                KabuExitEvalInput(
                    entry_price=placeholder,
                    current_price=placeholder,
                    entry_time=now,
                    now_time=now,
                    high_since_entry=placeholder,
                    tier=str(result_dict.get("tier") or self.tier),
                    breakout_trigger_level=placeholder,
                ),
                has_position=False,
            )
            return {
                "shadow_has_virtual_position": False,
                "would_exit": exit_res.would_exit,
                "exit_reason": exit_res.exit_reason,
                "exit_priority": exit_res.exit_priority,
                "unrealized_pct": exit_res.unrealized_pct,
                "mfe_pct": exit_res.mfe_pct,
                "elapsed_min": exit_res.elapsed_min,
                "exit_thresholds_used": exit_res.exit_thresholds_used,
                "exit_debug": exit_res.exit_debug,
            }

        if price is not None and price > pos.peak_price_since_entry:
            pos.peak_price_since_entry = price

        imb_thr = 0.48 if pos.tier == "B" else 0.46
        if imbalance is not None and imbalance <= imb_thr:
            pos.imbalance_low_streak += 1
        else:
            pos.imbalance_low_streak = 0

        current_px = float(price) if price is not None else pos.entry_price
        exit_res = evaluate_kabu_exit_v1(
            KabuExitEvalInput(
                entry_price=pos.entry_price,
                current_price=current_px,
                entry_time=pos.entry_time,
                now_time=now,
                high_since_entry=pos.peak_price_since_entry,
                current_vwap=vwap,
                entry_vwap_dist_pct=pos.entry_vwap_dist_pct,
                spread_bps=spread_bps,
                board_imbalance=imbalance,
                push_density_1m=push_1m,
                push_density_3m_avg=push_3m_avg,
                tier=pos.tier,
                breakout_trigger_level=pos.trigger_level_at_entry,
                session_high_at_entry=pos.session_high_at_entry,
                session_high_now=session_high_now,
                imbalance_low_streak=pos.imbalance_low_streak,
                max_price_since_entry=pos.peak_price_since_entry,
            ),
            has_position=True,
        )

        if exit_res.would_exit:
            del self._virtual_positions[symbol_yahoo]

        return {
            "shadow_has_virtual_position": True,
            "would_exit": exit_res.would_exit,
            "exit_reason": exit_res.exit_reason,
            "exit_priority": exit_res.exit_priority,
            "unrealized_pct": exit_res.unrealized_pct,
            "mfe_pct": exit_res.mfe_pct,
            "elapsed_min": exit_res.elapsed_min,
            "exit_thresholds_used": exit_res.exit_thresholds_used,
            "exit_debug": exit_res.exit_debug,
        }

    def _row_from_result(
        self,
        result_dict: dict[str, Any],
        *,
        poll_ts_jst: str,
        poll_number: int,
        exit_fields: dict[str, Any],
    ) -> dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        rejects = result_dict.get("reject_reasons") or []
        if isinstance(rejects, list):
            rejects_str = ";".join(str(x) for x in rejects)
        else:
            rejects_str = str(rejects)
        row: dict[str, Any] = {
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
            "shadow_has_virtual_position": exit_fields.get("shadow_has_virtual_position"),
            "would_exit": exit_fields.get("would_exit"),
            "exit_reason": exit_fields.get("exit_reason"),
            "exit_priority": exit_fields.get("exit_priority"),
            "unrealized_pct": exit_fields.get("unrealized_pct"),
            "mfe_pct": exit_fields.get("mfe_pct"),
            "elapsed_min": exit_fields.get("elapsed_min"),
        }
        return row

    def _append_row(self, row: dict[str, Any]) -> None:
        assert self._csv_path is not None
        assert self._jsonl_path is not None
        jsonl_row = dict(row)
        if "exit_thresholds_used" in row:
            jsonl_row["exit_thresholds_used"] = row["exit_thresholds_used"]
        if "exit_debug" in row:
            jsonl_row["exit_debug"] = row["exit_debug"]
        with self._jsonl_path.open("a", encoding="utf-8") as jf:
            jf.write(json.dumps(jsonl_row, ensure_ascii=False) + "\n")
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
        board_flat = flatten_board_dict(board) if "current_quote" in board else dict(board)
        ring = self._histories.setdefault(symbol_yahoo, PushHistoryRing())
        ring.add_from_board(board_flat)
        tracker = self._trackers.setdefault(symbol_yahoo, BreakoutStateTracker())
        now = datetime.now(timezone.utc)
        result, tracker = evaluate_kabu_signal_v1(
            board,
            push_history=ring,
            breakout_tracker=tracker,
            tier=self.tier,
            rest_fallback=False,
        )
        self._trackers[symbol_yahoo] = tracker
        result_dict = result.to_dict()

        self._maybe_open_virtual_position(
            symbol_yahoo,
            result_dict,
            board_flat=board_flat,
            now=now,
        )

        try:
            exit_fields = self._evaluate_exit_shadow(
                symbol_yahoo,
                result_dict,
                board_flat=board_flat,
                ring=ring,
                now=now,
            )
        except Exception as e:
            print(f"[KABU_EXIT_SHADOW] error symbol={symbol_yahoo} err={e!r}")
            exit_fields = {
                "shadow_has_virtual_position": symbol_yahoo in self._virtual_positions,
                "would_exit": False,
                "exit_reason": "EXIT_EVAL_ERROR",
                "exit_priority": 0,
                "unrealized_pct": None,
                "mfe_pct": None,
                "elapsed_min": None,
                "exit_thresholds_used": {},
                "exit_debug": {"error": repr(e)},
            }

        row = self._row_from_result(
            result_dict,
            poll_ts_jst=poll_ts_jst,
            poll_number=poll_number,
            exit_fields=exit_fields,
        )
        jsonl_extra = {
            "exit_thresholds_used": exit_fields.get("exit_thresholds_used"),
            "exit_debug": exit_fields.get("exit_debug"),
        }
        self._append_row({**row, **jsonl_extra})
        if log_fn is not None:
            log_fn(
                f"[KABU_SHADOW] symbol={symbol_yahoo} score={row.get('signal_score')} "
                f"timing_ok={row.get('timing_ok')} mode={row.get('data_mode')} "
                f"would_exit={row.get('would_exit')} exit_reason={row.get('exit_reason')}"
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
