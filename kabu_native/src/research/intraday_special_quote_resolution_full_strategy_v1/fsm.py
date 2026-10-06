"""Intraday special-quote episode FSM. Ingress order. No Sign buy/sell."""
from __future__ import annotations

from typing import Any, Optional

from research.intraday_special_quote_resolution_full_strategy_v1 import (
    EXIT_D,
    EXIT_U,
    THESIS_D,
    THESIS_U,
)
from research.intraday_special_quote_resolution_full_strategy_v1.semantics import TRUSTED_CONTINUOUS, TRUSTED_SPECIAL
from research.intraday_special_quote_resolution_full_strategy_v1.trade import (
    add_trade_to_bar,
    classify_payload,
    finish_bar,
    minute_epoch,
    new_trade_bar,
    observed_trade_update,
    volume_regression,
)

ST_SPECIAL_ACTIVE = "SPECIAL_ACTIVE"
ST_WAIT_RELEASE = "WAIT_RELEASE"
ST_RELEASED = "RELEASED"
ST_CLOSED = "CLOSED"
ST_NOT_EVALUABLE = "NOT_EVALUABLE"

DIR_UP = "UP_RELEASE"
DIR_DOWN = "DOWN_RELEASE"
DIR_FLAT = "FLAT_RELEASE"


def _direction(release_px: float, pre_px: float) -> str:
    if float(release_px) > float(pre_px):
        return DIR_UP
    if float(release_px) < float(pre_px):
        return DIR_DOWN
    return DIR_FLAT


def first_anchor_loss(bars: list[dict[str, Any]], *, after_t: float, anchor: float) -> int | None:
    for i, b in enumerate(bars):
        ft = b.get("finalize_t")
        if ft is None:
            continue
        if float(ft) <= float(after_t) + 1e-12:
            continue
        cl = b.get("close")
        if cl is None:
            continue
        if float(cl) < float(anchor):
            return int(i)
    return None


class IsqSymbolFsm:
    """One symbol, one AM session. Availability order only."""

    def __init__(self, *, symbol: str, date: str, flatten_t: float) -> None:
        self.symbol = str(symbol)
        self.date = str(date)
        self.flatten_t = float(flatten_t)
        self.last_vol: float = 0.0
        self.last_vol_set = False
        self.last_trade_px: Optional[float] = None
        self.last_trade_ingress: Optional[float] = None
        self.last_trade_vol: Optional[float] = None
        self.prev_trusted: Optional[str] = None
        self.had_trade_after_open = False
        self.episode: Optional[dict[str, Any]] = None
        self.episode_seq = 0
        self.episodes: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.bars: dict[float, dict[str, Any]] = {}
        self.completed_bars: list[dict[str, Any]] = []
        self.integrity_n = 0
        self.unrelated_regression_n = 0
        self.current_price_time_as_trade_id_n = 0
        self.current_price_time_as_availability_n = 0

    def _lifecycle_active(self) -> bool:
        ep = self.episode
        if ep is None:
            return False
        return str(ep.get("state") or "") in (ST_SPECIAL_ACTIVE, ST_WAIT_RELEASE, ST_RELEASED)

    def _advance_minutes(self, *, now_m: float, now_t: float) -> None:
        pending_ms = [m for m in list(self.bars.keys()) if m + 1e-12 < float(now_m) and self.bars[m].get("finalize_t") is None]
        pending_ms.sort()
        for m in pending_ms:
            bar = finish_bar(self.bars[m], finalize_t=float(now_t))
            self.bars[m] = bar
            self.completed_bars.append(bar)
            self._on_bar_finalized(bar)

    def _on_bar_finalized(self, bar: dict[str, Any]) -> None:
        ep = self.episode
        if ep is None:
            return
        if str(ep.get("state") or "") != ST_RELEASED:
            return
        if ep.get("signaled") or ep.get("no_entry_reason"):
            return
        accept_m = ep.get("accept_minute")
        if accept_m is None:
            return
        bar_m = float(bar["minute_epoch"])
        if bar_m + 1e-12 < float(accept_m):
            return
        if abs(bar_m - float(accept_m)) > 1e-9:
            if float(bar_m) > float(accept_m) + 1e-12 and ep.get("accept_resolved") is not True:
                acc = self.bars.get(float(accept_m))
                if acc is None or acc.get("finalize_t") is None or int(acc.get("TRADE_UPDATE_N") or 0) < 1:
                    ep["no_entry_reason"] = "NO_ACCEPTANCE_EVIDENCE"
                    ep["accept_resolved"] = True
                    ep["state"] = ST_CLOSED
                    self._close_episode()
            return
        if ep.get("reinterrupted"):
            ep["no_entry_reason"] = "RELEASE_REINTERRUPTED"
            ep["accept_resolved"] = True
            ep["state"] = ST_CLOSED
            self._close_episode()
            return
        if int(bar.get("TRADE_UPDATE_N") or 0) < 1:
            ep["no_entry_reason"] = "NO_ACCEPTANCE_EVIDENCE"
            ep["accept_resolved"] = True
            ep["state"] = ST_CLOSED
            self._close_episode()
            return
        self._maybe_signal(bar)

    def _maybe_signal(self, accept_bar: dict[str, Any]) -> None:
        ep = self.episode
        if ep is None:
            return
        ep["accept_resolved"] = True
        ep["accept_bar"] = {
            "minute_epoch": accept_bar["minute_epoch"],
            "open": accept_bar["open"],
            "high": accept_bar["high"],
            "low": accept_bar["low"],
            "close": accept_bar["close"],
            "TRADE_UPDATE_N": accept_bar["TRADE_UPDATE_N"],
            "finalize_t": accept_bar["finalize_t"],
        }
        close = float(accept_bar["close"])
        direction = str(ep.get("direction") or "")
        t0 = float(accept_bar["finalize_t"])
        if t0 + 1e-12 >= self.flatten_t:
            ep["no_entry_reason"] = "FLATTEN"
            ep["state"] = ST_CLOSED
            self._close_episode()
            return
        if direction == DIR_FLAT:
            ep["no_entry_reason"] = "FLAT_RELEASE"
            ep["state"] = ST_CLOSED
            self._close_episode()
            return
        if direction == DIR_UP:
            if close > float(ep["release_price"]):
                self._emit(THESIS_U, EXIT_U, float(ep["release_price"]), t0, accept_bar)
            else:
                ep["no_entry_reason"] = "UP_ACCEPT_FAIL"
            ep["state"] = ST_CLOSED
            self._close_episode()
            return
        if direction == DIR_DOWN:
            if close > float(ep["pre_special_price"]):
                self._emit(THESIS_D, EXIT_D, float(ep["pre_special_price"]), t0, accept_bar)
            else:
                ep["no_entry_reason"] = "DOWN_ACCEPT_FAIL"
            ep["state"] = ST_CLOSED
            self._close_episode()
            return
        ep["state"] = ST_CLOSED
        self._close_episode()

    def _emit(self, thesis: str, exit_id: str, anchor: float, t0: float, accept_bar: dict[str, Any]) -> None:
        ep = self.episode
        if ep is None or ep.get("signaled"):
            return
        ep["signaled"] = True
        rec = {
            "date": self.date,
            "session": "AM",
            "symbol": self.symbol,
            "t0": float(t0),
            "signal_t0": float(t0),
            "episode_id": ep["episode_id"],
            "episode_seq": ep["episode_seq"],
            "thesis": thesis,
            "exit_id": exit_id,
            "anchor": float(anchor),
            "release_price": ep.get("release_price"),
            "pre_special_price": ep.get("pre_special_price"),
            "direction": ep.get("direction"),
            "accept_close": accept_bar.get("close"),
            "accept_minute": ep.get("accept_minute"),
            "release_ingress": ep.get("release_ingress"),
            "special_start_ingress": ep.get("special_start_ingress"),
            "special_start_coobserved": ep.get("special_start_coobserved"),
        }
        self.signals.append(rec)

    def _close_episode(self) -> None:
        ep = self.episode
        if ep is None:
            return
        if ep not in self.episodes:
            self.episodes.append(ep)
        self.episode = None

    def _start_episode(self, *, ingress_t: float, coobserved: bool) -> None:
        if self.last_trade_px is None or self.last_trade_ingress is None:
            return
        if float(self.last_trade_ingress) >= float(ingress_t) - 1e-15:
            return
        self.episode_seq += 1
        ep = {
            "date": self.date,
            "symbol": self.symbol,
            "episode_seq": int(self.episode_seq),
            "episode_id": f"{self.date}:{self.symbol}:{self.episode_seq}",
            "special_start_ingress": float(ingress_t),
            "pre_special_price": float(self.last_trade_px),
            "pre_special_trade_ingress": float(self.last_trade_ingress),
            "pre_special_trading_volume": self.last_trade_vol,
            "PRE_SPECIAL_REFERENCE_VALID": True,
            "special_start_coobserved": bool(coobserved),
            "state": ST_SPECIAL_ACTIVE,
            "special_active_trade_update_n": 0,
            "special_active_trade_conflict": False,
            "direction": None,
            "release_price": None,
            "release_ingress": None,
            "release_trading_volume": None,
            "release_minute": None,
            "accept_minute": None,
            "reinterrupted": False,
            "signaled": False,
            "accept_resolved": False,
            "no_entry_reason": None,
            "created_at": float(ingress_t),
        }
        self.episode = ep

    def process(self, *, ingress_t: float, payload: dict[str, Any]) -> None:
        t = float(ingress_t)
        if t + 1e-12 >= self.flatten_t:
            self._advance_minutes(now_m=minute_epoch(self.flatten_t + 60.0), now_t=self.flatten_t)
            if self.episode is not None and str(self.episode.get("state") or "") == ST_RELEASED:
                acc_m = self.episode.get("accept_minute")
                if acc_m is not None and not self.episode.get("accept_resolved"):
                    acc = self.bars.get(float(acc_m))
                    if acc is None or acc.get("finalize_t") is None or int(acc.get("TRADE_UPDATE_N") or 0) < 1:
                        self.episode["no_entry_reason"] = "NO_ACCEPTANCE_EVIDENCE"
                        self.episode["accept_resolved"] = True
                        self.episode["state"] = ST_CLOSED
                        self._close_episode()
            return
        facts = classify_payload(payload, event_t=t)
        trusted = str(facts["trusted"])
        vol = facts["vol"]
        px = facts["px"]
        opened = bool(facts["opened"])
        last_for_cmp = self.last_vol if self.last_vol_set else 0.0
        regression = volume_regression(last_vol=last_for_cmp if self.last_vol_set else None, vol=vol)
        trade_update = observed_trade_update(last_vol=last_for_cmp, vol=vol, px=px)
        if not self.last_vol_set:
            trade_update = observed_trade_update(last_vol=0.0, vol=vol, px=px)

        if regression and self._lifecycle_active():
            self.integrity_n += 1
            if self.episode is not None:
                self.episode["integrity_error"] = True
        elif regression:
            self.unrelated_regression_n += 1

        now_m = minute_epoch(t)
        ep0 = self.episode
        if (
            ep0 is not None
            and str(ep0.get("state") or "") == ST_RELEASED
            and trusted == TRUSTED_SPECIAL
            and not ep0.get("accept_resolved")
        ):
            ep0["reinterrupted"] = True
            ep0["no_entry_reason"] = "RELEASE_REINTERRUPTED"
            ep0["accept_resolved"] = True
            ep0["state"] = ST_CLOSED
            self._close_episode()
        self._advance_minutes(now_m=now_m, now_t=t)

        just_started = False
        if (
            opened
            and self.had_trade_after_open
            and self.prev_trusted == TRUSTED_CONTINUOUS
            and trusted == TRUSTED_SPECIAL
            and t < self.flatten_t
            and self.episode is None
            and self.last_trade_px is not None
            and self.last_trade_ingress is not None
            and float(self.last_trade_ingress) < t - 1e-15
        ):
            self._start_episode(ingress_t=t, coobserved=bool(trade_update))
            just_started = self.episode is not None
            if just_started and trade_update:
                self.episode["special_start_coobserved"] = True

        ep = self.episode
        if ep is not None and str(ep.get("state") or "") == ST_SPECIAL_ACTIVE:
            if trusted == TRUSTED_CONTINUOUS:
                ep["state"] = ST_WAIT_RELEASE
                ep["return_continuous_ingress"] = t
            elif trusted == TRUSTED_SPECIAL and trade_update and not just_started:
                ep["special_active_trade_update_n"] = int(ep.get("special_active_trade_update_n") or 0) + 1
                ep["special_active_trade_conflict"] = True
                ep["state"] = ST_NOT_EVALUABLE
                ep["no_entry_reason"] = "SPECIAL_ACTIVE_TRADE_SEMANTIC_CONFLICT"
                self._close_episode()
                ep = None

        ep = self.episode
        if ep is not None and str(ep.get("state") or "") == ST_WAIT_RELEASE:
            if trusted == TRUSTED_SPECIAL:
                ep["state"] = ST_SPECIAL_ACTIVE
            elif trusted == TRUSTED_CONTINUOUS and trade_update:
                ep["release_price"] = float(px)
                ep["release_ingress"] = t
                ep["release_trading_volume"] = vol
                ep["release_minute"] = now_m
                ep["accept_minute"] = float(now_m) + 60.0
                ep["direction"] = _direction(float(px), float(ep["pre_special_price"]))
                ep["state"] = ST_RELEASED
                if ep["direction"] == DIR_FLAT:
                    ep["no_entry_reason"] = "FLAT_RELEASE"
                    ep["accept_resolved"] = True
                    ep["state"] = ST_CLOSED
                    self._close_episode()
                    ep = None

        if trade_update and not regression:
            add_m = now_m
            bar = self.bars.get(add_m)
            if bar is None or bar.get("finalize_t") is not None:
                self.bars[add_m] = new_trade_bar(minute=add_m, px=float(px), ingress_t=t)
            else:
                add_trade_to_bar(bar, px=float(px), ingress_t=t)
            if opened:
                self.had_trade_after_open = True
            skip_pre_special_replace = just_started
            if not skip_pre_special_replace:
                self.last_trade_px = float(px)
                self.last_trade_ingress = t
                self.last_trade_vol = vol
            else:
                self.last_trade_vol = vol

        if vol is not None and not regression:
            self.last_vol = float(vol)
            self.last_vol_set = True

        if trusted in (TRUSTED_CONTINUOUS, TRUSTED_SPECIAL):
            self.prev_trusted = trusted
        else:
            self.prev_trusted = trusted

    def close_session(self) -> None:
        self._advance_minutes(now_m=minute_epoch(self.flatten_t + 60.0), now_t=self.flatten_t)
        if self.episode is not None:
            if str(self.episode.get("state") or "") == ST_RELEASED and not self.episode.get("accept_resolved"):
                acc_m = self.episode.get("accept_minute")
                acc = self.bars.get(float(acc_m)) if acc_m is not None else None
                if acc is None or acc.get("finalize_t") is None or int(acc.get("TRADE_UPDATE_N") or 0) < 1:
                    self.episode["no_entry_reason"] = "NO_ACCEPTANCE_EVIDENCE"
                self.episode["accept_resolved"] = True
            if str(self.episode.get("state") or "") not in (ST_CLOSED,):
                self.episode["state"] = ST_CLOSED
            self._close_episode()
