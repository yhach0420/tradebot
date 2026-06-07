"""
kabu_native shadow runner — REST board (+ optional PUSH), no orders.

Optional [KABU_PAPER] Discord virtual ENTRY/EXIT notify (kabu_native/notify only).
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from shadow.config import ShadowConfig
from shadow.events import SHADOW_EVENT_CSV_FIELDS
from shadow.watchlist import WatchSymbol

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

log = logging.getLogger("kabu_native.shadow.runner")


@dataclass
class ShadowVirtualPosition:
    symbol: str
    entry_price: float
    entry_time: datetime
    trigger_level: float
    entry_vwap_dist_pct: Optional[float]
    session_high_at_entry: float
    peak_price: float
    tier: str
    imbalance_low_streak: int = 0
    bf_streak: int = 0


@dataclass
class ShadowRunner:
    repo_root: Path
    native_root: Path
    config: ShadowConfig
    watchlist: list[WatchSymbol]
    _token: Optional[str] = None
    _histories: dict = field(default_factory=dict)
    _trackers: dict = field(default_factory=dict)
    _positions: dict[str, ShadowVirtualPosition] = field(default_factory=dict)
    _csv_path: Optional[Path] = None
    _jsonl_path: Optional[Path] = None
    _csv_header_written: bool = False
    _poll_count: int = 0
    _push_stop: threading.Event = field(default_factory=threading.Event)
    _discord_notifier: Any = None

    def _ensure_repo_imports(self) -> None:
        root = str(self.repo_root)
        if root not in sys.path:
            sys.path.insert(0, root)

    def _exit_config(self):
        from src.kabu_exit_engine import KabuExitV1Config

        r = self.config.rules
        hard = abs(float(r.hard_stop_pct))
        return KabuExitV1Config(
            hard_stop_pct_a=hard,
            hard_stop_pct_b=hard,
            fail_buffer_pct_a=float(r.fail_buffer_pct),
            fail_buffer_pct_b=float(r.fail_buffer_pct),
            fail_window_sec=float(r.fail_window_min) * 60.0,
        )

    def _get_rest_client(self):
        from api.rest_client import KabuNativeRestClient, default_base_url

        if not hasattr(self, "_rest_client_instance"):
            self._rest_client_instance = KabuNativeRestClient(base_url=default_base_url())
        return self._rest_client_instance

    def _fetch_board(self, ws: WatchSymbol) -> dict[str, Any]:
        from api.rest_client import require_kabu_password

        client = self._get_rest_client()
        pwd = require_kabu_password()

        def _once() -> dict[str, Any]:
            if not self._token:
                self._token = client.issue_token(pwd)
            return dict(client.get_board(ws.symbol_key, token=self._token))

        try:
            return _once()
        except Exception:
            self._token = None
            return _once()

    def _output_paths(self, day_key: str) -> tuple[Path, Path]:
        root = self.native_root / "results" / "shadow" / day_key
        root.mkdir(parents=True, exist_ok=True)
        return root / "shadow_events.csv", root / "shadow_events.jsonl"

    def _append_event(self, row: dict[str, Any]) -> None:
        assert self._csv_path and self._jsonl_path
        with self._jsonl_path.open("a", encoding="utf-8") as jf:
            jf.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        write_header = not self._csv_header_written
        with self._csv_path.open("a", encoding="utf-8", newline="") as cf:
            w = csv.DictWriter(cf, fieldnames=list(SHADOW_EVENT_CSV_FIELDS), extrasaction="ignore")
            if write_header:
                w.writeheader()
                self._csv_header_written = True
            w.writerow(row)

    def _init_discord_notifier(self) -> None:
        from notify.discord import ShadowDiscordNotifier

        self._discord_notifier = ShadowDiscordNotifier(self.config.discord)
        if self._discord_notifier.active:
            log.info(
                "shadow [KABU_PAPER] discord ON env=%s",
                self.config.discord.webhook_env,
            )
        else:
            log.info("shadow discord OFF (default)")

    def _symbol_display_name(self, ws: WatchSymbol, board_flat: dict[str, Any]) -> str:
        return (
            str(board_flat.get("SymbolName") or board_flat.get("symbol_name") or "").strip()
            or ws.symbol.replace(".T", "")
        )

    def _maybe_discord_paper_entry(
        self,
        ws: WatchSymbol,
        rd: dict[str, Any],
        board_flat: dict[str, Any],
    ) -> bool:
        notifier = self._discord_notifier
        pos = self._positions.get(ws.symbol)
        if notifier is None or pos is None or not getattr(notifier, "active", False):
            return False
        try:
            return notifier.notify_paper_entry(
                symbol=ws.symbol,
                symbol_name=self._symbol_display_name(ws, board_flat),
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
                trigger_level=pos.trigger_level,
                rd=rd,
            )
        except Exception as e:
            log.warning("[KABU_NOTIFY] error %s", e, exc_info=False)
            return False

    def _maybe_discord_paper_exit(self, exit_snapshot: Optional[dict[str, Any]]) -> bool:
        notifier = self._discord_notifier
        if notifier is None or not exit_snapshot or not getattr(notifier, "active", False):
            return False
        try:
            return notifier.notify_paper_exit(
                symbol=str(exit_snapshot["symbol"]),
                entry_price=float(exit_snapshot["entry_price"]),
                exit_price=float(exit_snapshot["exit_price"]),
                entry_time=exit_snapshot["entry_time"],
                exit_reason=str(exit_snapshot.get("exit_reason") or ""),
                pnl_pct=exit_snapshot.get("pnl_pct"),
                mfe_pct=exit_snapshot.get("mfe_pct"),
                elapsed_min=exit_snapshot.get("elapsed_min"),
                pnl_yen_100=exit_snapshot.get("pnl_yen_100"),
                side=str(exit_snapshot.get("side") or "long"),
            )
        except Exception as e:
            log.warning("[KABU_NOTIFY] error %s", e, exc_info=False)
            return False

    def _now_jst_str(self) -> str:
        if JST is None:
            return datetime.now().isoformat(timespec="seconds")
        return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    def _session_entry_allowed(self, ts: datetime) -> bool:
        from replay.session_control import entry_allowed

        return entry_allowed(ts, market_session_control=self.config.rules.market_session_control)

    def _maybe_entry(
        self,
        ws: WatchSymbol,
        rd: dict[str, Any],
        board_flat: dict[str, Any],
        now: datetime,
    ) -> bool:
        if ws.symbol in self._positions:
            return False
        if not self._session_entry_allowed(now):
            return False
        r = self.config.rules
        if str(rd.get("tier", r.tier)).upper() == "C":
            return False
        if not rd.get("breakout_event"):
            return False
        if int(rd.get("signal_score") or 0) < r.entry_score_min:
            return False
        if r.require_timing_ok and not rd.get("timing_ok"):
            return False
        price = rd.get("current_price")
        trigger = rd.get("trigger_level")
        if price is None or trigger is None:
            return False
        px = float(price)
        sh = board_flat.get("HighPrice")
        session_high = float(sh) if sh is not None else px
        from src.kabu_signal_engine import board_time_utc

        entry_time = board_time_utc(board_flat) or now
        self._positions[ws.symbol] = ShadowVirtualPosition(
            symbol=ws.symbol,
            entry_price=px,
            entry_time=entry_time,
            trigger_level=float(trigger),
            entry_vwap_dist_pct=rd.get("vwap_distance_pct"),
            session_high_at_entry=session_high,
            peak_price=px,
            tier=str(rd.get("tier") or r.tier).upper(),
        )
        return True

    def _evaluate_exit(
        self,
        ws: WatchSymbol,
        rd: dict[str, Any],
        board_flat: dict[str, Any],
        ring: Any,
        now: datetime,
    ) -> tuple[dict[str, Any], bool, Optional[dict[str, Any]]]:
        from src.kabu_exit_engine import KabuExitEvalInput, evaluate_kabu_exit_v1
        from src.kabu_signal_engine import board_current_price

        pos = self._positions.get(ws.symbol)
        if pos is None:
            placeholder = float(rd.get("current_price") or 1.0)
            exit_res = evaluate_kabu_exit_v1(
                KabuExitEvalInput(
                    entry_price=placeholder,
                    current_price=placeholder,
                    entry_time=now,
                    now_time=now,
                    high_since_entry=placeholder,
                    tier=self.config.rules.tier,
                    breakout_trigger_level=placeholder,
                ),
                has_position=False,
                cfg=self._exit_config(),
            )
            return (
                {
                    "shadow_virtual_position": False,
                    "would_exit": exit_res.would_exit,
                    "exit_reason": exit_res.exit_reason,
                    "exit_priority": exit_res.exit_priority,
                    "unrealized_pct": exit_res.unrealized_pct,
                    "mfe_pct": exit_res.mfe_pct,
                    "elapsed_min": exit_res.elapsed_min,
                    "bf_confirm_streak": 0,
                },
                False,
                None,
            )

        price = board_current_price(board_flat) or rd.get("current_price")
        px = float(price) if price is not None else pos.entry_price
        if px > pos.peak_price:
            pos.peak_price = px

        imb_thr = 0.48 if pos.tier == "B" else 0.46
        imbalance = rd.get("board_imbalance")
        if imbalance is not None and float(imbalance) <= imb_thr:
            pos.imbalance_low_streak += 1
        else:
            pos.imbalance_low_streak = 0

        push_3m = ring.push_samples_avg_per_minute(as_of=now)
        exit_res = evaluate_kabu_exit_v1(
            KabuExitEvalInput(
                entry_price=pos.entry_price,
                current_price=px,
                entry_time=pos.entry_time,
                now_time=now,
                high_since_entry=pos.peak_price,
                current_vwap=board_flat.get("VWAP"),
                entry_vwap_dist_pct=pos.entry_vwap_dist_pct,
                spread_bps=rd.get("spread_bps"),
                board_imbalance=imbalance,
                push_density_1m=int(rd.get("push_samples_1m") or 0),
                push_density_3m_avg=push_3m,
                tier=pos.tier,
                breakout_trigger_level=pos.trigger_level,
                session_high_at_entry=pos.session_high_at_entry,
                session_high_now=board_flat.get("HighPrice"),
                imbalance_low_streak=pos.imbalance_low_streak,
                max_price_since_entry=pos.peak_price,
            ),
            has_position=True,
            cfg=self._exit_config(),
        )

        virtual_exit = False
        exit_snapshot: Optional[dict[str, Any]] = None
        would_exit = exit_res.would_exit
        exit_reason = exit_res.exit_reason
        confirm_n = max(1, int(self.config.rules.bf_confirm_count))

        def _capture_exit() -> None:
            nonlocal virtual_exit, exit_snapshot
            virtual_exit = True
            exit_snapshot = {
                "symbol": ws.symbol,
                "entry_price": pos.entry_price,
                "exit_price": px,
                "entry_time": pos.entry_time,
                "exit_reason": exit_reason,
                "pnl_pct": exit_res.unrealized_pct,
                "mfe_pct": exit_res.mfe_pct,
                "elapsed_min": exit_res.elapsed_min,
            }
            del self._positions[ws.symbol]

        if exit_res.would_exit and exit_res.exit_reason == "breakout_failure":
            pos.bf_streak += 1
            if pos.bf_streak >= confirm_n:
                _capture_exit()
            else:
                would_exit = False
                exit_reason = "HOLD_SHADOW"
        else:
            pos.bf_streak = 0
            if exit_res.would_exit:
                _capture_exit()

        return (
            {
                "shadow_virtual_position": ws.symbol in self._positions,
                "would_exit": would_exit,
                "exit_reason": exit_reason,
                "exit_priority": exit_res.exit_priority,
                "unrealized_pct": exit_res.unrealized_pct,
                "mfe_pct": exit_res.mfe_pct,
                "elapsed_min": exit_res.elapsed_min,
                "bf_confirm_streak": pos.bf_streak if ws.symbol in self._positions else 0,
            },
            virtual_exit,
            exit_snapshot,
        )

    def evaluate_symbol(self, ws: WatchSymbol, *, poll_ts_jst: str) -> None:
        self._ensure_repo_imports()
        from src.kabu_signal_engine import PushHistoryRing, evaluate_kabu_signal_v1, flatten_board_dict
        from src.signal_engine import BreakoutStateTracker

        board = self._fetch_board(ws)
        board_flat = flatten_board_dict(board) if "current_quote" in board else dict(board)
        ring = self._histories.setdefault(ws.symbol, PushHistoryRing())
        ring.add_from_board(board_flat)
        tracker = self._trackers.setdefault(ws.symbol, BreakoutStateTracker())
        now = datetime.now(timezone.utc)

        result, tracker = evaluate_kabu_signal_v1(
            board,
            push_history=ring,
            breakout_tracker=tracker,
            tier=self.config.rules.tier,
            evaluated_at=now,
            rest_fallback=False,
        )
        self._trackers[ws.symbol] = tracker
        rd = result.to_dict()

        opened = self._maybe_entry(ws, rd, board_flat, now)
        exit_snapshot: Optional[dict[str, Any]] = None
        try:
            exit_fields, closed, exit_snapshot = self._evaluate_exit(ws, rd, board_flat, ring, now)
        except Exception as e:
            log.exception("exit eval failed symbol=%s", ws.symbol)
            exit_fields = {
                "shadow_virtual_position": ws.symbol in self._positions,
                "would_exit": False,
                "exit_reason": "EXIT_EVAL_ERROR",
                "exit_priority": 0,
                "unrealized_pct": None,
                "mfe_pct": None,
                "elapsed_min": None,
                "bf_confirm_streak": 0,
            }
            closed = False
            exit_snapshot = None

        rejects = rd.get("reject_reasons") or []
        if isinstance(rejects, list):
            rejects_str = ";".join(str(x) for x in rejects)
        else:
            rejects_str = str(rejects)

        session_ok = self._session_entry_allowed(now)
        discord_entry_sent = False
        discord_exit_sent = False
        if opened:
            discord_entry_sent = self._maybe_discord_paper_entry(ws, rd, board_flat)
        if closed and exit_snapshot:
            discord_exit_sent = self._maybe_discord_paper_exit(exit_snapshot)

        row = {
            "timestamp_utc": now.isoformat(),
            "poll_ts_jst": poll_ts_jst,
            "poll_number": self._poll_count,
            "symbol": ws.symbol,
            "symbol_key": ws.symbol_key,
            "current_price": rd.get("current_price"),
            "signal_score": rd.get("signal_score"),
            "breakout_event": rd.get("breakout_event"),
            "timing_ok": rd.get("timing_ok"),
            "tier": rd.get("tier"),
            "reject_reasons": rejects_str,
            "spread_bps": rd.get("spread_bps"),
            "board_imbalance": rd.get("board_imbalance"),
            "vwap_distance_pct": rd.get("vwap_distance_pct"),
            "push_samples_1m": rd.get("push_samples_1m"),
            "trigger_level": rd.get("trigger_level"),
            "quote_age_sec": rd.get("quote_age_sec"),
            "notify_entry_eligible": rd.get("notify_breakout_eligible"),
            "entry_allowed_session": session_ok,
            "market_session_control": self.config.rules.market_session_control,
            "shadow_virtual_position": ws.symbol in self._positions,
            "shadow_virtual_entry": opened,
            "shadow_virtual_exit": closed,
            "shadow_discord_entry_notified": discord_entry_sent,
            "shadow_discord_exit_notified": discord_exit_sent,
            "data_mode": "rest_board",
            **exit_fields,
        }
        self._append_event(row)

    def ingest_push_board(self, symbol: str, board: dict[str, Any]) -> None:
        from src.kabu_signal_engine import PushHistoryRing

        ring = self._histories.setdefault(symbol, PushHistoryRing())
        ring.add_from_board(board)

    def _start_push_thread(self) -> None:
        if not self.config.runtime.use_push:
            return

        def _run() -> None:
            try:
                import asyncio

                from api.push_client import KabuNativePushClient
                from api.rest_client import KabuNativeRestClient, default_base_url, require_kabu_password

                async def _loop() -> None:
                    client = KabuNativeRestClient(base_url=default_base_url())
                    token = client.issue_token(require_kabu_password())
                    push = KabuNativePushClient(client, token)
                    spec = [(ws.code, ws.exchange) for ws in self.watchlist]
                    push.register(spec)
                    sym_by_code = {ws.code: ws.symbol for ws in self.watchlist}
                    async for msg in push.iter_messages():
                        if self._push_stop.is_set():
                            break
                        code = str(msg.get("Symbol") or "")
                        sym = sym_by_code.get(code)
                        if sym:
                            self.ingest_push_board(sym, msg)

                asyncio.run(_loop())
            except Exception:
                log.exception("PUSH thread stopped")

        t = threading.Thread(target=_run, name="kabu_native_push", daemon=True)
        t.start()

    def run_loop(self) -> None:
        day_key = datetime.now().strftime("%Y%m%d")
        self._csv_path, self._jsonl_path = self._output_paths(day_key)
        self._csv_header_written = self._csv_path.is_file() and self._csv_path.stat().st_size > 0

        log.info(
            "shadow start symbols=%d session_control=%s bf_confirm=%d push=%s",
            len(self.watchlist),
            self.config.rules.market_session_control,
            self.config.rules.bf_confirm_count,
            self.config.runtime.use_push,
        )
        self._init_discord_notifier()
        self._start_push_thread()

        max_polls = self.config.runtime.max_polls
        interval = max(1.0, float(self.config.runtime.poll_interval_sec))

        try:
            while True:
                self._poll_count += 1
                poll_ts = self._now_jst_str()
                for ws in self.watchlist:
                    try:
                        self.evaluate_symbol(ws, poll_ts_jst=poll_ts)
                    except Exception:
                        log.exception("symbol eval failed %s", ws.symbol)
                        if not self.config.runtime.continue_on_error:
                            raise
                if max_polls is not None and self._poll_count >= max_polls:
                    log.info("max_polls reached (%s)", max_polls)
                    break
                time.sleep(interval)
        finally:
            self._push_stop.set()
