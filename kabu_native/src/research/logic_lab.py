"""
Phase 17: logic validation lab — multi-profile, multi-day replay diagnostics.

Separates candidate (signal quality) vs entry (virtual position) vs exit (closed trade).
No per-symbol / per-day / per-time optimization (profiles are fixed rule sets).
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

log = logging.getLogger("kabu_native.research.logic_lab")

PROFILE_NAMES: tuple[str, ...] = (
    "baseline",
    "relaxed_entry",
    "continuation_v1",
    "breakout_v1",
    "vwap_trend_v1",
    "volume_confirm_v1",
)

from research.entry_v2 import (
    ENTRY_V2_COMPARISON_PROFILES,
    ENTRY_V2_PHASE24_PROFILES,
    ENTRY_V2_PHASE25_PROFILES,
    ENTRY_V2_PHASE26_PROFILES,
    ENTRY_V2_PHASE27_PROFILES,
    ENTRY_V2_PHASE28_PROFILES,
    ENTRY_V2_PHASE29_PROFILES,
    ENTRY_V2_PHASE30_PROFILES,
    ENTRY_V2_PHASE31_PROFILES,
    ENTRY_V2_PHASE32_PROFILES,
    ENTRY_V2_PHASE33_PROFILES,
    ENTRY_V2_PHASE34_PROFILES,
    ENTRY_V2_PHASE35_PROFILES,
    ENTRY_V2_PROFILE_NAMES,
    is_entry_v2_profile,
    is_momentum_enriched_profile,
    uses_momentum_v6_cooldown,
)

ALL_PROFILE_NAMES: tuple[str, ...] = PROFILE_NAMES + ENTRY_V2_PROFILE_NAMES


@dataclass(frozen=True)
class LogicProfile:
    name: str
    entry_score_min: int = 60
    require_timing_ok: bool = True
    relaxed_signal: bool = False
    bf_confirm_count: int = 1
    vwap_distance_pct_min: float = 0.35
    entry_requires_session: bool = True

    def signal_cfg(self):
        from dataclasses import replace

        from src.kabu_signal_replay import replay_signal_config

        cfg = replay_signal_config(relaxed=self.relaxed_signal)
        if self.name == "vwap_trend_v1":
            return replace(cfg, vwap_distance_pct_min=max(self.vwap_distance_pct_min, 0.5))
        return cfg

    def is_candidate(self, rd: dict[str, Any]) -> bool:
        score = int(rd.get("signal_score") or 0)
        if self.name == "baseline" or self.name == "continuation_v1":
            return bool(rd.get("notify_breakout_eligible"))
        if self.name == "relaxed_entry":
            return bool(rd.get("breakout_event")) and score >= 50 and bool(rd.get("timing_ok"))
        if self.name == "breakout_v1":
            return bool(rd.get("breakout_event")) and score >= max(50, self.entry_score_min - 5)
        if self.name == "vwap_trend_v1":
            vd = rd.get("vwap_distance_pct")
            return (
                bool(rd.get("timing_ok"))
                and vd is not None
                and float(vd) >= self.vwap_distance_pct_min
                and score >= self.entry_score_min
            )
        if self.name == "volume_confirm_v1":
            return self._volume_ok(rd) and score >= self.entry_score_min
        return bool(rd.get("notify_breakout_eligible"))

    def is_entry_eligible(self, rd: dict[str, Any], *, tier: str) -> bool:
        if tier.upper() == "C":
            return False
        score = int(rd.get("signal_score") or 0)
        if self.name == "relaxed_entry":
            return (
                bool(rd.get("breakout_event"))
                and score >= max(50, self.entry_score_min - 5)
                and bool(rd.get("timing_ok"))
            )
        eligible = bool(rd.get("breakout_event")) and score >= self.entry_score_min
        if self.require_timing_ok:
            eligible = eligible and bool(rd.get("timing_ok"))
        if self.name == "volume_confirm_v1":
            eligible = eligible and self._volume_ok(rd)
        if self.name == "vwap_trend_v1":
            vd = rd.get("vwap_distance_pct")
            eligible = eligible and vd is not None and float(vd) >= self.vwap_distance_pct_min
        return eligible

    @staticmethod
    def _volume_ok(rd: dict[str, Any]) -> bool:
        rejects = rd.get("reject_reasons") or []
        if isinstance(rejects, str):
            parts = [p.strip() for p in rejects.split(";") if p.strip()]
        else:
            parts = [str(x) for x in rejects]
        for code in parts:
            if code.startswith("G6_") or code.startswith("G7"):
                return False
        return bool(rd.get("timing_ok"))


def build_profiles(names: Sequence[str] | None = None) -> dict[str, LogicProfile]:
    specs: dict[str, LogicProfile] = {
        "baseline": LogicProfile(name="baseline"),
        "relaxed_entry": LogicProfile(
            name="relaxed_entry",
            entry_score_min=55,
            relaxed_signal=True,
        ),
        "continuation_v1": LogicProfile(
            name="continuation_v1",
            bf_confirm_count=2,
        ),
        "breakout_v1": LogicProfile(
            name="breakout_v1",
            entry_score_min=55,
        ),
        "vwap_trend_v1": LogicProfile(
            name="vwap_trend_v1",
            vwap_distance_pct_min=0.5,
        ),
        "volume_confirm_v1": LogicProfile(name="volume_confirm_v1"),
    }
    for v2_name in ENTRY_V2_PROFILE_NAMES:
        specs[v2_name] = LogicProfile(
            name=v2_name,
            require_timing_ok=False,
            entry_requires_session=True,
        )
    if names is None:
        return specs
    return {k: specs[k] for k in names if k in specs}


@dataclass
class LogicLabConfig:
    start_date: str
    end_date: str
    symbols: list[str]
    data_roots: list[Path]
    output_dir: Path
    profiles: list[str] = field(default_factory=lambda: list(PROFILE_NAMES))
    tier: str = "B"
    repo_root: Path | None = None
    synthetic_push_keep: float = 1.0
    synthetic_spread_bps: float = 8.0
    synthetic_events_per_minute: int = 10
    eod_exit_reason: str = "eod_close"
    market_session_control: bool = True
    research_exit_phase36: bool = False
    validation_phase37: bool = False
    validation_phase38: bool = False


@dataclass
class SymbolDayResult:
    profile: str
    trade_date: str
    symbol: str
    eval_count: int = 0
    breakout_count: int = 0
    candidate_count: int = 0
    entry_signal_count: int = 0
    trades: list[Any] = field(default_factory=list)
    reject_counts: Counter[str] = field(default_factory=Counter)
    score_buckets: Counter[str] = field(default_factory=Counter)
    candidate_rows: list[dict[str, Any]] = field(default_factory=list)
    skip_reason: str = ""
    g7: Any = None  # G7DiagnosticAccumulator
    g5: Any = None  # G5DiagnosticAccumulator
    g6: Any = None  # G6DiagnosticAccumulator
    g3: Any = None  # G3DiagnosticAccumulator
    enriched_trade_rows: list[dict[str, Any]] = field(default_factory=list)


def _ensure_repo(repo_root: Path) -> None:
    s = str(repo_root)
    if s not in sys.path:
        sys.path.insert(0, s)


def _score_bucket(score: int) -> str:
    if score < 40:
        return "0-39"
    if score < 60:
        return "40-59"
    if score < 80:
        return "60-79"
    return "80-100"


def _as_float_g5(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _session_ok(ts: datetime, *, enabled: bool) -> bool:
    if not enabled:
        return True
    from replay.session_control import entry_allowed

    return entry_allowed(ts, market_session_control=True)


def replay_profile_symbol_day(
    profile: LogicProfile,
    *,
    symbol: str,
    trade_date: str,
    events: list[Any],
    tier: str,
    eod_exit_reason: str,
    market_session_control: bool,
    csv_minute_lookup: dict | None = None,
    events_per_minute: int = 10,
    forward_cache: Any = None,
) -> SymbolDayResult:
    from src.kabu_exit_engine import KabuExitEvalInput, KabuExitV1Config, evaluate_kabu_exit_v1
    from src.kabu_signal_engine import PushHistoryRing, evaluate_kabu_signal_v1
    from research.g3_diagnostic import G3DiagnosticAccumulator, g3_classify
    from research.g5_diagnostic import G5DiagnosticAccumulator, compute_forward_path, EventForwardCache, g5_classify
    from research.g6_diagnostic import G6DiagnosticAccumulator, g6_classify
    from research.g7_diagnostic import G7DiagnosticAccumulator, G7_SOURCE_SESSION, _minute_key
    from src.kabu_signal_replay import OpenPosition, ClosedTrade, _pct_change
    from src.signal_engine import BreakoutStateTracker

    res = SymbolDayResult(profile=profile.name, trade_date=trade_date, symbol=symbol)
    signal_cfg = profile.signal_cfg()
    res.g7 = G7DiagnosticAccumulator(
        threshold_tv=float(signal_cfg.min_trading_value),
        g7_source=G7_SOURCE_SESSION,
    )
    res.g5 = G5DiagnosticAccumulator(profile=profile.name)
    res.g6 = G6DiagnosticAccumulator(profile=profile.name, tier=tier)
    res.g3 = G3DiagnosticAccumulator(
        profile=profile.name,
        threshold_pct=float(signal_cfg.vwap_distance_pct_min),
    )
    v2_ctx = None
    if is_entry_v2_profile(profile.name):
        from research.entry_v2 import EntryV2Context

        v2_ctx = EntryV2Context.create(profile.name)
    if not events:
        res.skip_reason = "no_events"
        return res

    if forward_cache is None:
        forward_cache = EventForwardCache.build(events)

    ring = PushHistoryRing()
    tracker = BreakoutStateTracker()
    position: Optional[OpenPosition] = None
    imb_thr = 0.48 if tier.upper() == "B" else 0.46
    hard = 1.20
    exit_cfg = KabuExitV1Config(
        hard_stop_pct_a=hard,
        hard_stop_pct_b=hard,
        fail_buffer_pct_a=0.12,
        fail_buffer_pct_b=0.12,
        fail_window_sec=120.0,
    )
    bf_confirm = max(1, profile.bf_confirm_count)
    bf_streak = 0
    from research.microstructure_runtime import StructureCooldownState

    structure_cd = StructureCooldownState()

    def _close(pos: OpenPosition, exit_time: datetime, exit_price: float, reason: str) -> None:
        pnl = _pct_change(exit_price, pos.entry_price)
        mfe = _pct_change(pos.peak_price, pos.entry_price)
        mae = _pct_change(pos.trough_price, pos.entry_price)
        elapsed = (exit_time - pos.entry_time).total_seconds() / 60.0
        closed = ClosedTrade(
            symbol=symbol,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            pnl_pct=pnl,
            exit_reason=reason,
            max_favorable_excursion_pct=mfe,
            max_adverse_excursion_pct=mae,
            elapsed_min=elapsed,
            signal_score_at_entry=pos.signal_score_at_entry,
            data_source="logic_lab",
        )
        res.trades.append(closed)
        if uses_momentum_v6_cooldown(profile.name):
            structure_cd.record_structure_exit(reason)
        early_rt = getattr(pos, "_early_runtime", None)
        if early_rt is not None:
            pos._early_snap = early_rt.finalize()  # type: ignore[attr-defined]
        if is_momentum_enriched_profile(profile.name):
            from research.entry_v2_deep_dive import build_enriched_trade_row

            snap = getattr(pos, "_entry_snap", {})
            idx = int(getattr(pos, "_entry_eval_idx", 0))
            exit_snap = getattr(pos, "_exit_snap", {})
            early_snap = getattr(pos, "_early_snap", None)
            row = build_enriched_trade_row(
                profile=profile.name,
                trade_date=trade_date,
                symbol=symbol,
                trade=closed,
                entry_snap=snap,
                entry_idx=idx,
                forward_cache=forward_cache,
                exit_snap=exit_snap,
                early_snap=early_snap,
            )
            if uses_momentum_v6_cooldown(profile.name):
                row["reentry_blocked"] = int(getattr(pos, "_reentry_blocked", 0))
            res.enriched_trade_rows.append(row)
        if res.g5 is not None:
            res.g5.record_closed_trade(
                symbol=symbol,
                pnl_pct=pnl,
                mfe_pct=mfe,
                mae_pct=mae,
                hold_min=elapsed,
                exit_reason=reason,
            )
        if res.g6 is not None:
            res.g6.record_closed_trade(
                pnl_pct=pnl,
                mfe_pct=mfe,
                mae_pct=mae,
                hold_min=elapsed,
                exit_reason=reason,
            )
        if res.g3 is not None:
            res.g3.record_closed_trade(
                pnl_pct=pnl,
                mfe_pct=mfe,
                mae_pct=mae,
                hold_min=elapsed,
                exit_reason=reason,
                all_three_pass=getattr(pos, "_g3_g5_g6_pass", False),
            )

    for ev in events:
        res.eval_count += 1
        board = ev.board
        ring.add_from_board(board)
        result, tracker = evaluate_kabu_signal_v1(
            board,
            push_history=ring,
            breakout_tracker=tracker,
            tier=tier,
            evaluated_at=ev.ts,
            cfg=signal_cfg,
        )
        rd = result.to_dict()
        score = int(rd.get("signal_score") or 0)
        res.score_buckets[_score_bucket(score)] += 1

        rejects = rd.get("reject_reasons") or []
        if isinstance(rejects, list):
            reject_list = [str(r) for r in rejects]
            for r in reject_list:
                res.reject_counts[r] += 1
        elif rejects:
            reject_list = [str(rejects)]
            res.reject_counts[str(rejects)] += 1
        else:
            reject_list = []

        csv_meta = None
        if csv_minute_lookup is not None:
            csv_meta = csv_minute_lookup.get(_minute_key(ev.ts))
        if not is_entry_v2_profile(profile.name):
            res.g7.record_eval(rd, reject_list, board=board, csv_meta=csv_meta)

        if v2_ctx is not None:
            v2_ctx.update(
                ts=ev.ts,
                rd=rd,
                board=board,
                rejects=reject_list,
                csv_meta=csv_meta,
            )

        is_cand = (
            v2_ctx.is_candidate(rd, reject_list)
            if v2_ctx is not None
            else profile.is_candidate(rd)
        )

        px0 = rd.get("current_price")
        if px0 is not None and not is_entry_v2_profile(profile.name):
            fwd_short, fwd_ext = compute_forward_path(
                events,
                res.eval_count - 1,
                px0=float(px0),
                rolling_high_5m=_as_float_g5(rd.get("rolling_high_5m")),
                trigger_level=_as_float_g5(rd.get("trigger_level")),
                session_high0=_as_float_g5(rd.get("high_price")),
                cache=forward_cache,
            )
            res.g5.record_eval(
                trade_date=trade_date,
                symbol=symbol,
                event_time=ev.ts,
                rejects=reject_list,
                rd=rd,
                forward_short=fwd_short,
                forward_ext=fwd_ext,
                is_candidate=is_cand,
            )
            vol_p75 = ring.volume_delta_p75_30m(as_of=ev.ts) if ring.has_push_history else None
            csv_vol = None
            if csv_meta is not None:
                csv_vol = csv_meta.get("bar_volume")
            res.g6.record_eval(
                trade_date=trade_date,
                symbol=symbol,
                event_time=ev.ts,
                rejects=reject_list,
                rd=rd,
                board=board,
                forward_short=fwd_short,
                forward_ext=fwd_ext,
                is_candidate=is_cand,
                vol_p75_30m=vol_p75,
                csv_bar_volume=float(csv_vol) if csv_vol is not None else None,
                signal_cfg=signal_cfg,
            )
            res.g3.record_eval(
                trade_date=trade_date,
                symbol=symbol,
                event_time=ev.ts,
                rejects=reject_list,
                rd=rd,
                forward_short=fwd_short,
                forward_ext=fwd_ext,
                is_candidate=is_cand,
            )

        if rd.get("breakout_event"):
            res.breakout_count += 1

        if is_cand:
            res.candidate_count += 1
            row_extra = {
                "entry_v2_score": v2_ctx.last_entry_v2_score if v2_ctx else None,
            }
            res.candidate_rows.append(
                {
                    "profile": profile.name,
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "event_time": ev.ts.isoformat(),
                    "signal_score": score,
                    "breakout_event": rd.get("breakout_event"),
                    "timing_ok": rd.get("timing_ok"),
                    "notify_breakout_eligible": rd.get("notify_breakout_eligible"),
                    "vwap_distance_pct": rd.get("vwap_distance_pct"),
                    "reject_reasons": ";".join(rejects) if isinstance(rejects, list) else str(rejects),
                    "session_ok": _session_ok(ev.ts, enabled=market_session_control),
                    **row_extra,
                }
            )

        price = rd.get("current_price")
        if price is None:
            continue
        px = float(price)

        if position is not None:
            if px > position.peak_price:
                position.peak_price = px
            if px < position.trough_price:
                position.trough_price = px
            imbalance = rd.get("board_imbalance")
            if imbalance is not None and float(imbalance) <= imb_thr:
                position.imbalance_low_streak += 1
            else:
                position.imbalance_low_streak = 0
            if imbalance is not None:
                imb_f = float(imbalance)
                prev_min = getattr(position, "_min_imbalance_since_entry", imb_f)
                position._min_imbalance_since_entry = min(prev_min, imb_f)  # type: ignore[attr-defined]
                position._exit_snap = {  # type: ignore[attr-defined]
                    "board_imbalance_exit": imb_f,
                    "board_imbalance_min_since_entry": position._min_imbalance_since_entry,
                    "imbalance_low_streak": position.imbalance_low_streak,
                }

            early_rt = getattr(position, "_early_runtime", None)
            if early_rt is not None:
                ts_sec = ev.ts.timestamp()
                if ev.ts.tzinfo:
                    from datetime import timezone as _tz

                    ts_sec = ev.ts.astimezone(_tz.utc).timestamp()
                vwap = _as_float_g5(rd.get("vwap"))
                mtv = None
                if csv_minute_lookup is not None:
                    cm = csv_minute_lookup.get(_minute_key(ev.ts))
                    if cm:
                        mtv = _as_float_g5(cm.get("bar_trading_value"))
                early_rt.update(
                    ts_sec=ts_sec,
                    price=px,
                    board_imbalance=float(imbalance) if imbalance is not None else None,
                    vwap=vwap,
                    volume_delta_30s=_as_float_g5(rd.get("volume_delta_30s")),
                    minute_trading_value=mtv,
                    spread_bps=_as_float_g5(rd.get("spread_bps")),
                )

            push_3m = ring.push_samples_avg_per_minute(as_of=ev.ts)
            exit_inp = KabuExitEvalInput(
                entry_price=position.entry_price,
                current_price=px,
                entry_time=position.entry_time,
                now_time=ev.ts,
                high_since_entry=position.peak_price,
                current_vwap=rd.get("vwap"),
                entry_vwap_dist_pct=position.entry_vwap_dist_pct,
                spread_bps=rd.get("spread_bps"),
                board_imbalance=imbalance,
                push_density_1m=int(rd.get("push_samples_1m") or 0),
                push_density_3m_avg=push_3m,
                tier=position.tier,
                breakout_trigger_level=position.trigger_level,
                session_high_at_entry=position.session_high_at_entry,
                session_high_now=rd.get("high_price"),
                imbalance_low_streak=position.imbalance_low_streak,
                max_price_since_entry=position.peak_price,
            )
            from research.momentum_exit_v3 import evaluate_momentum_v3_exit, uses_momentum_v3_exit
            from research.momentum_exit_v4 import evaluate_momentum_v4_exit, uses_momentum_v4_exit
            from research.momentum_exit_v5 import evaluate_momentum_v5_exit, uses_momentum_v5_exit
            from research.momentum_exit_v6 import evaluate_momentum_v6_exit, uses_momentum_v6_exit
            from research.momentum_exit_v7 import evaluate_momentum_v7_exit, uses_momentum_v7_exit
            from research.momentum_exit_v8 import evaluate_momentum_v8_exit, uses_momentum_v8_exit
            from research.momentum_exit_v9 import evaluate_momentum_v9_exit, uses_momentum_v9_exit
            from research.momentum_exit_v10 import evaluate_momentum_v10_exit, uses_momentum_v10_exit
            from research.momentum_exit_v11 import evaluate_momentum_v11_exit, uses_momentum_v11_exit
            from research.momentum_exit_v12 import evaluate_momentum_v12_exit, uses_momentum_v12_exit
            from research.momentum_exit_v13 import evaluate_momentum_v13_exit, uses_momentum_v13_exit

            if uses_momentum_v13_exit(profile.name):
                exit_res = evaluate_momentum_v13_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v12_exit(profile.name):
                exit_res = evaluate_momentum_v12_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v11_exit(profile.name):
                exit_res = evaluate_momentum_v11_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v10_exit(profile.name):
                exit_res = evaluate_momentum_v10_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v9_exit(profile.name):
                exit_res = evaluate_momentum_v9_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v8_exit(profile.name):
                exit_res = evaluate_momentum_v8_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v7_exit(profile.name):
                exit_res = evaluate_momentum_v7_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v6_exit(profile.name):
                exit_res = evaluate_momentum_v6_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v5_exit(profile.name):
                exit_res = evaluate_momentum_v5_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v4_exit(profile.name):
                exit_res = evaluate_momentum_v4_exit(
                    profile.name,
                    exit_inp,
                    cfg=exit_cfg,
                    runtime=early_rt,
                )
            elif uses_momentum_v3_exit(profile.name):
                exit_res = evaluate_momentum_v3_exit(profile.name, exit_inp, cfg=exit_cfg)
            else:
                exit_res = evaluate_kabu_exit_v1(
                    exit_inp,
                    has_position=True,
                    cfg=exit_cfg,
                )
            if exit_res.would_exit:
                reason = exit_res.exit_reason or "exit"
                if reason == "breakout_failure" and bf_confirm > 1:
                    bf_streak += 1
                    if bf_streak >= bf_confirm:
                        _close(position, ev.ts, px, reason)
                        position = None
                        bf_streak = 0
                else:
                    bf_streak = 0
                    _close(position, ev.ts, px, reason)
                    position = None
            continue

        if not _session_ok(ev.ts, enabled=market_session_control):
            continue
        entry_ok = (
            v2_ctx.is_entry(rd, reject_list, tier=tier)
            if v2_ctx is not None
            else profile.is_entry_eligible(rd, tier=tier)
        )
        if not entry_ok:
            continue
        if uses_momentum_v6_cooldown(profile.name):
            vd_cd = _as_float_g5(rd.get("vwap_distance_pct"))
            imb_cd = _as_float_g5(rd.get("board_imbalance"))
            structure_cd.try_release(
                vwap_dist=vd_cd,
                imbalance=float(imb_cd) if imb_cd is not None else None,
            )
            if structure_cd.block_entry():
                continue
        trigger = rd.get("trigger_level")
        if trigger is None and v2_ctx is None:
            continue
        if trigger is None:
            trigger = px

        res.entry_signal_count += 1
        if not is_entry_v2_profile(profile.name):
            if res.g5 is not None:
                res.g5.record_trade_entry()
            if res.g6 is not None:
                res.g6.record_trade_entry()
            g3p = g3_classify(reject_list) == "pass"
            g5p = g5_classify(reject_list) == "pass"
            g6p = g6_classify(reject_list) == "pass"
            if res.g3 is not None:
                res.g3.record_trade_entry(g3_pass=g3p, g5_pass=g5p, g6_pass=g6p)
        else:
            g3p = g5p = g6p = False
        session_high = rd.get("high_price")
        position = OpenPosition(
            symbol=symbol,
            entry_time=ev.ts,
            entry_price=px,
            trigger_level=float(trigger),
            entry_vwap_dist_pct=rd.get("vwap_distance_pct"),
            session_high_at_entry=float(session_high) if session_high is not None else px,
            peak_price=px,
            trough_price=px,
            tier=tier.upper(),
            signal_score_at_entry=score,
        )
        position._g3_g5_g6_pass = g3p and g5p and g6p  # type: ignore[attr-defined]
        if v2_ctx is not None:
            position._entry_eval_idx = res.eval_count - 1  # type: ignore[attr-defined]
            position._entry_snap = v2_ctx.entry_snapshot(rd)  # type: ignore[attr-defined]
        if is_momentum_enriched_profile(profile.name):
            from research.microstructure_runtime import MicrostructureRuntime

            ets = ev.ts.timestamp()
            if ev.ts.tzinfo:
                from datetime import timezone as _tz

                ets = ev.ts.astimezone(_tz.utc).timestamp()
            snap = getattr(position, "_entry_snap", v2_ctx.entry_snapshot(rd) if v2_ctx else {})
            position._early_runtime = MicrostructureRuntime.from_entry_snap(  # type: ignore[attr-defined]
                entry_price=px,
                entry_ts_sec=ets,
                entry_snap=snap,
            )
            position._early_snap = {}  # type: ignore[attr-defined]
            if uses_momentum_v6_cooldown(profile.name):
                position._reentry_blocked = structure_cd.reentry_blocked_count  # type: ignore[attr-defined]
        entry_imb = rd.get("board_imbalance")
        if entry_imb is not None:
            position._min_imbalance_since_entry = float(entry_imb)  # type: ignore[attr-defined]
            position._exit_snap = {  # type: ignore[attr-defined]
                "board_imbalance_exit": float(entry_imb),
                "board_imbalance_min_since_entry": float(entry_imb),
                "imbalance_low_streak": 0,
            }
        bf_streak = 0

    if position is not None:
        last = events[-1]
        last_px = float(position.peak_price)
        if last.board.get("CurrentPrice") is not None:
            last_px = float(last.board["CurrentPrice"])
        _close(position, last.ts, last_px, eod_exit_reason)

    return res


def _mfe_reach_rate(trades: Sequence[Any], threshold_pct: float) -> Optional[float]:
    if not trades:
        return None
    hit = 0
    for t in trades:
        mfe = getattr(t, "max_favorable_excursion_pct", None)
        if mfe is not None and float(mfe) >= threshold_pct:
            hit += 1
    return hit / len(trades)


def _trade_metrics(
    trades: Sequence[Any],
    *,
    days: int,
    symbols: set[str],
    symbol_trade_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    from replay.metrics import _summary_block

    block = _summary_block(trades)
    pnls = []
    mfes = []
    maes = []
    holds = []
    losses = []
    for t in trades:
        pnls.append(float(getattr(t, "pnl_pct", 0)))
        mfes.append(float(getattr(t, "max_favorable_excursion_pct", 0)))
        maes.append(float(getattr(t, "max_adverse_excursion_pct", 0)))
        holds.append(float(getattr(t, "elapsed_min", 0)))
        if float(getattr(t, "pnl_pct", 0)) < 0:
            losses.append(float(getattr(t, "pnl_pct", 0)))

    n = len(trades)
    bf_n = sum(1 for t in trades if getattr(t, "exit_reason", "") == "breakout_failure")
    imb_exit_n = sum(
        1 for t in trades if getattr(t, "exit_reason", "") == "board_imbalance_deterioration"
    )
    hs_n = sum(1 for t in trades if getattr(t, "exit_reason", "") == "hard_stop")
    mae_sorted = sorted(maes)
    mae_p90 = None
    if mae_sorted:
        mae_p90 = mae_sorted[min(len(mae_sorted) - 1, int(0.9 * (len(mae_sorted) - 1)))]
    conc_sym = ""
    conc_pct = None
    if symbol_trade_counts and n > 0:
        top_sym, top_n = max(symbol_trade_counts.items(), key=lambda x: x[1])
        conc_sym = top_sym
        conc_pct = top_n / n

    return {
        **block,
        "entry_count": block.get("trades", 0),
        "mfe_ge_0_3_pct_rate": _mfe_reach_rate(trades, 0.3),
        "mfe_ge_0_5_pct_rate": _mfe_reach_rate(trades, 0.5),
        "avg_mfe_pct": statistics.mean(mfes) if mfes else None,
        "avg_mae_pct": statistics.mean(maes) if maes else None,
        "median_hold_min": statistics.median(holds) if holds else None,
        "breakout_failure_rate": (bf_n / n) if n else None,
        "board_imbalance_exit_rate": (imb_exit_n / n) if n else None,
        "hard_stop_rate": (hs_n / n) if n else None,
        "mae_p50": statistics.median(maes) if maes else None,
        "mae_p90": mae_p90,
        "avg_loss_pct": statistics.mean(losses) if losses else None,
        "symbols_with_trades": len(symbols),
        "trades_per_day": (block.get("trades", 0) / days) if days > 0 else None,
        "concentration_top_symbol": conc_sym,
        "concentration_top_symbol_pct": conc_pct,
    }


def _aggregate_profile(
    results: list[SymbolDayResult],
    *,
    num_days: int,
    num_symbols: int,
    tier: str = "B",
) -> tuple[dict[str, Any], Any, Any, Any, Any]:
    all_trades: list[Any] = []
    symbols_with_trades: set[str] = set()
    total_eval = 0
    total_breakout = 0
    total_candidates = 0
    total_entry_signals = 0
    reject_agg: Counter[str] = Counter()
    from research.g5_diagnostic import G5DiagnosticAccumulator, summarize_g5
    from research.g3_diagnostic import G3DiagnosticAccumulator, summarize_g3
    from research.g6_diagnostic import G6DiagnosticAccumulator, summarize_g6
    from research.g7_diagnostic import G7DiagnosticAccumulator, G7_SOURCE_SESSION, summarize_g7

    g7_merged = G7DiagnosticAccumulator()
    g5_merged = G5DiagnosticAccumulator(profile=results[0].profile if results else "")
    g6_merged = G6DiagnosticAccumulator(
        profile=results[0].profile if results else "",
        tier=tier,
    )
    g3_thr = (
        float(results[0].g3.threshold_pct)
        if results and results[0].g3 is not None
        else 0.35
    )
    g3_merged = G3DiagnosticAccumulator(
        profile=results[0].profile if results else "",
        threshold_pct=g3_thr,
    )
    sym_trade_counts: Counter[str] = Counter()
    day_pnl: dict[str, float] = defaultdict(float)
    for r in results:
        if r.g7 is not None:
            g7_merged.merge(r.g7)
        if r.g5 is not None:
            g5_merged.merge(r.g5)
        if r.g6 is not None:
            g6_merged.merge(r.g6)
        if r.g3 is not None:
            g3_merged.merge(r.g3)
        total_eval += r.eval_count
        total_breakout += r.breakout_count
        total_candidates += r.candidate_count
        total_entry_signals += r.entry_signal_count
        reject_agg.update(r.reject_counts)
        for t in r.trades:
            all_trades.append(t)
            symbols_with_trades.add(r.symbol)
            sym_trade_counts[r.symbol] += 1
            day_pnl[r.trade_date] += float(getattr(t, "pnl_pct", 0))

    metrics = _trade_metrics(
        all_trades,
        days=num_days,
        symbols=symbols_with_trades,
        symbol_trade_counts=dict(sym_trade_counts),
    )
    g7_summary = summarize_g7(g7_merged) if g7_merged.eval_count else {}
    g5_summary = summarize_g5(g5_merged) if g5_merged.eval_count else {}
    g6_summary = summarize_g6(g6_merged) if g6_merged.eval_count else {}
    g3_summary = summarize_g3(g3_merged) if g3_merged.eval_count else {}
    top_reject = reject_agg.most_common(1)[0][0] if reject_agg else ""
    losing_day_count = sum(1 for v in day_pnl.values() if v < 0)
    worst_day_pnl = min(day_pnl.values()) if day_pnl else None

    row = {
        "profile": results[0].profile if results else "",
        "eval_count": total_eval,
        "breakout_count": total_breakout,
        "candidate_count": total_candidates,
        "entry_count": metrics.get("entry_count", 0),
        "entry_signal_count": total_entry_signals,
        "candidates_per_day": total_candidates / num_days if num_days else None,
        "entries_per_day": total_entry_signals / num_days if num_days else None,
        "reject_top": dict(reject_agg.most_common(15)),
        "top_reject_reason": top_reject,
        "top_reject_is_data_quality_issue": bool(g7_summary.get("top_reject_is_data_quality_issue"))
        if str(top_reject).startswith("G7")
        else False,
        "possible_threshold_too_strict": bool(g7_summary.get("possible_threshold_too_strict"))
        if str(top_reject).startswith("G7")
        else False,
        "g7_source": g7_summary.get("g7_source", G7_SOURCE_SESSION),
        "g7_threshold": g7_summary.get("g7_threshold"),
        "g7_pass_rate": g7_summary.get("g7_pass_rate"),
        "session_cumulative_trading_value_p90": g7_summary.get("session_cumulative_trading_value_p90"),
        "minute_trading_value_p90": g7_summary.get("minute_trading_value_p90"),
        "g7_pass_rate_old": g7_summary.get("pass_rate_old"),
        "g7_board_trading_value_p90": g7_summary.get("board_trading_value_p90"),
        "g7_below_threshold_count": g7_summary.get("below_threshold_count"),
        "g7_diagnosis_notes": g7_summary.get("diagnosis_notes"),
        "g7_summary": g7_summary,
        "g5_pass_count": g5_summary.get("g5_pass_count"),
        "g5_reject_count": g5_summary.get("g5_reject_count"),
        "g5_pass_rate": g5_summary.get("g5_pass_rate"),
        "trades_after_g5": g5_summary.get("trades_after_g5"),
        "candidates_after_g5": g5_summary.get("candidates_after_g5"),
        "g5_is_alpha_positive": g5_summary.get("g5_is_alpha_positive"),
        "g5_possible_overfilter": g5_summary.get("g5_possible_overfilter"),
        "g5_rejected_mfe_rate": g5_summary.get("g5_rejected_mfe_rate"),
        "g5_pass_pf": g5_summary.get("g5_pass_pf"),
        "g5_summary": g5_summary,
        "g6_pass_count": g6_summary.get("g6_pass_count"),
        "g6_reject_count": g6_summary.get("g6_reject_count"),
        "g6_pass_rate": g6_summary.get("g6_pass_rate"),
        "trades_after_g6": g6_summary.get("trades_after_g6"),
        "candidates_after_g6": g6_summary.get("candidates_after_g6"),
        "g6_is_alpha_positive": g6_summary.get("g6_is_alpha_positive"),
        "g6_possible_overfilter": g6_summary.get("g6_possible_overfilter"),
        "g6_rejected_mfe_rate": g6_summary.get("g6_rejected_mfe_rate"),
        "g6_pass_pf": g6_summary.get("g6_pass_pf"),
        "g5_g6_both_pass_count": g6_summary.get("g5_g6_both_pass_count"),
        "g6_summary": g6_summary,
        "g3_pass_count": g3_summary.get("g3_pass_count"),
        "g3_reject_count": g3_summary.get("g3_reject_count"),
        "g3_pass_rate": g3_summary.get("g3_pass_rate"),
        "trades_after_g3": g3_summary.get("trades_after_g3"),
        "candidates_after_g3": g3_summary.get("candidates_after_g3"),
        "g3_is_alpha_positive": g3_summary.get("g3_is_alpha_positive"),
        "g3_possible_overfilter": g3_summary.get("g3_possible_overfilter"),
        "g3_rejected_mfe_rate": g3_summary.get("g3_rejected_mfe_rate"),
        "g3_pass_pf": g3_summary.get("g3_pass_pf"),
        "g3_g5_g6_all_pass_count": g3_summary.get("g3_g5_g6_all_pass_count"),
        "three_gate_profit_factor": g3_summary.get("three_gate_profit_factor"),
        "g3_summary": g3_summary,
        "losing_day_count": losing_day_count,
        "worst_day_pnl": worst_day_pnl,
        **metrics,
    }
    return row, g7_merged, g5_merged, g6_merged, g3_merged


def _entry_v2_comparison_row(
    profile_row: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flatten profile summary for entry_v2_comparison export."""

    def _f(key: str) -> Any:
        return profile_row.get(key)

    row = {
        "profile": profile_row.get("profile"),
        "eval_count": _f("eval_count"),
        "candidate_count": _f("candidate_count"),
        "entry_count": _f("entry_count"),
        "entry_signal_count": _f("entry_signal_count"),
        "candidates_per_day": _f("candidates_per_day"),
        "entries_per_day": _f("entries_per_day"),
        "trades_per_day": _f("trades_per_day"),
        "symbols_with_trades": _f("symbols_with_trades"),
        "win_rate": _f("win_rate"),
        "total_pnl_pct": _f("total_pnl_pct"),
        "avg_pnl_pct": _f("avg_pnl_pct"),
        "median_pnl_pct": _f("median_pnl_pct"),
        "profit_factor": _f("profit_factor"),
        "max_loss_pct": _f("max_loss_pct"),
        "avg_loss_pct": _f("avg_loss_pct"),
        "mfe_ge_0_3_pct_rate": _f("mfe_ge_0_3_pct_rate"),
        "mfe_ge_0_5_pct_rate": _f("mfe_ge_0_5_pct_rate"),
        "avg_mfe_pct": _f("avg_mfe_pct"),
        "avg_mae_pct": _f("avg_mae_pct"),
        "breakout_failure_rate": _f("breakout_failure_rate"),
        "median_hold_min": _f("median_hold_min"),
        "top_reject_reason": _f("top_reject_reason"),
        "concentration_top_symbol": _f("concentration_top_symbol"),
        "concentration_top_symbol_pct": _f("concentration_top_symbol_pct"),
    }
    if baseline is None or profile_row.get("profile") == "baseline":
        row["vs_baseline"] = {}
        return row

    def _delta(key: str) -> Any:
        a, b = profile_row.get(key), baseline.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) - float(b)
        return None

    pf = _f("profit_factor")
    bpf = baseline.get("profit_factor")
    flags: list[str] = []
    if pf is not None and bpf is not None and float(pf) <= float(bpf):
        flags.append("pf_not_improved_vs_baseline")
    if (_f("entry_count") or 0) > (baseline.get("entry_count") or 0) and (
        pf is not None and bpf is not None and float(pf) < float(bpf)
    ):
        flags.append("trade_count_up_pf_down")
    if (_f("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    row["vs_baseline"] = {
        "candidate_count_delta": _delta("candidate_count"),
        "entry_count_delta": _delta("entry_count"),
        "profit_factor_delta": _delta("profit_factor"),
        "mfe_ge_0_3_delta": _delta("mfe_ge_0_3_pct_rate"),
        "breakout_failure_rate_delta": _delta("breakout_failure_rate"),
        "max_loss_pct_delta": _delta("max_loss_pct"),
        "adoption_flags": flags,
        "recommended": len(flags) == 0
        and pf is not None
        and bpf is not None
        and float(pf) >= float(bpf),
    }
    return row


def _write_entry_v2_comparison(
    out: Path,
    profile_summaries: list[dict[str, Any]],
) -> None:
    baseline = next((r for r in profile_summaries if r.get("profile") == "baseline"), None)
    v2_names = set(ENTRY_V2_PROFILE_NAMES)
    rows = []
    for row in profile_summaries:
        pname = str(row.get("profile", ""))
        if pname == "baseline" or pname in v2_names:
            rows.append(_entry_v2_comparison_row(row, baseline))

    if not rows:
        return

    flat_rows = []
    for r in rows:
        flat = {k: v for k, v in r.items() if k != "vs_baseline"}
        vs = r.get("vs_baseline") or {}
        if isinstance(vs, dict):
            for vk, vv in vs.items():
                flat[f"vs_{vk}"] = vv
        flat_rows.append(flat)

    fields = list(flat_rows[0].keys())
    with (out / "entry_v2_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat_rows)

    (out / "entry_v2_comparison.json").write_text(
        json.dumps(
            {
                "phase": 23,
                "description": "ENTRY v2 prototype comparison vs baseline",
                "baseline_profile": "baseline",
                "entry_v2_profiles": list(ENTRY_V2_PROFILE_NAMES),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _adoption_vs_baseline(profile_row: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Heuristic flags — not auto-adopt; human review required."""

    def _f(key: str) -> Optional[float]:
        v = profile_row.get(key)
        if v is None:
            return None
        return float(v)

    flags: list[str] = []
    notes: list[str] = []

    cand_day = profile_row.get("candidates_per_day") or 0
    base_cand = baseline.get("candidates_per_day") or 0
    if cand_day < 1.0:
        flags.append("candidates_per_day_too_low")
    if (profile_row.get("entries_per_day") or 0) < 0.3:
        flags.append("entries_per_day_too_low")

    pf = _f("profit_factor")
    bpf = _f("profit_factor")
    if pf is not None and bpf is not None and pf < bpf:
        flags.append("pf_worse_than_baseline")

    mfe_p = _f("avg_mfe_pct")
    mfe_b = _f("avg_mfe_pct")
    if mfe_p is not None and mfe_b is not None and mfe_p < mfe_b:
        flags.append("avg_mfe_worse_than_baseline")

    max_l = _f("max_loss_pct")
    max_lb = _f("max_loss_pct")
    if max_l is not None and max_lb is not None and max_l < max_lb - 0.05:
        flags.append("max_loss_worse_than_baseline")

    sym_tr = profile_row.get("symbols_with_trades", 0) or 0
    if sym_tr <= 1 and profile_row.get("trades", 0) > 0:
        flags.append("possible_single_symbol_dependency")

    if (
        profile_row.get("trades", 0) < (baseline.get("trades", 0) or 0) * 0.5
        and (pf or 0) <= (bpf or 0)
    ):
        flags.append("trade_count_drop_without_pf_gain")

    if not flags and profile_row.get("profile") != "baseline":
        notes.append("review_manually_for_overfitting")

    return {
        "adoption_blockers": flags,
        "adoption_notes": notes,
        "paper_trade_ready": len(flags) == 0 and profile_row.get("profile") != "baseline",
    }


def iter_trade_dates(start: str, end: str) -> list[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def run_logic_lab(config: LogicLabConfig) -> Path:
    repo_root = config.repo_root or Path(__file__).resolve().parents[3]
    native_src = repo_root / "kabu_native" / "src"
    for p in (native_src, repo_root):
        s = str(p.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)
    _ensure_repo(repo_root)

    from replay.intraday import load_intraday_csv, resolve_intraday_csv
    from src.kabu_signal_replay import (
        DATA_SOURCE_YAHOO_SYNTHETIC,
        events_from_push_messages,
        push_messages_from_yahoo_df,
        yahoo_symbol_code,
    )

    profiles = build_profiles(config.profiles)
    dates = iter_trade_dates(config.start_date, config.end_date)
    num_days = len(dates)
    num_symbols = len(config.symbols)

    by_profile: dict[str, list[SymbolDayResult]] = {n: [] for n in profiles}

    for trade_date in dates:
        for symbol in config.symbols:
            sym = symbol if symbol.endswith(".T") else f"{yahoo_symbol_code(symbol)}.T"
            csv_path = resolve_intraday_csv(config.data_roots, trade_date, sym)
            if csv_path is None:
                for pname in profiles:
                    by_profile[pname].append(
                        SymbolDayResult(
                            profile=pname,
                            trade_date=trade_date,
                            symbol=sym,
                            skip_reason="missing_intraday_csv",
                        )
                    )
                continue

            loaded = load_intraday_csv(csv_path)
            if not loaded.ok:
                for pname in profiles:
                    by_profile[pname].append(
                        SymbolDayResult(
                            profile=pname,
                            trade_date=trade_date,
                            symbol=sym,
                            skip_reason=loaded.skip_reason or "load_failed",
                        )
                    )
                continue

            msgs = push_messages_from_yahoo_df(
                loaded.df,
                symbol=sym,
                keep_fraction=config.synthetic_push_keep,
                seed=hash((trade_date, sym)) % (2**31),
                spread_bps=config.synthetic_spread_bps,
                events_per_minute=config.synthetic_events_per_minute,
            )
            events = events_from_push_messages(msgs, source=DATA_SOURCE_YAHOO_SYNTHETIC)
            csv_lookup = None
            if loaded.df is not None:
                from research.g7_diagnostic import build_csv_minute_lookup

                csv_lookup = build_csv_minute_lookup(
                    loaded.df,
                    events_per_minute=config.synthetic_events_per_minute,
                )

            from research.g5_diagnostic import EventForwardCache

            fwd_cache = EventForwardCache.build(events) if events else None
            for pname, prof in profiles.items():
                by_profile[pname].append(
                    replay_profile_symbol_day(
                        prof,
                        symbol=sym,
                        trade_date=trade_date,
                        events=events,
                        tier=config.tier,
                        eod_exit_reason=config.eod_exit_reason,
                        market_session_control=config.market_session_control,
                        csv_minute_lookup=csv_lookup,
                        events_per_minute=config.synthetic_events_per_minute,
                        forward_cache=fwd_cache,
                    )
                )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(config, by_profile, num_days=num_days, num_symbols=num_symbols)
    return config.output_dir


def _write_outputs(
    config: LogicLabConfig,
    by_profile: dict[str, list[SymbolDayResult]],
    *,
    num_days: int,
    num_symbols: int,
) -> None:
    out = config.output_dir
    profile_summaries: list[dict[str, Any]] = []
    g7_by_profile: dict[str, Any] = {}
    g5_by_profile: dict[str, Any] = {}
    g6_by_profile: dict[str, Any] = {}
    g3_by_profile: dict[str, Any] = {}
    baseline_row: dict[str, Any] | None = None

    profile_order = [p for p in config.profiles if p in by_profile]
    for pname in profile_order:
        row, g7_acc, g5_acc, g6_acc, g3_acc = _aggregate_profile(
            by_profile[pname],
            num_days=num_days,
            num_symbols=num_symbols,
            tier=config.tier,
        )
        g7_by_profile[pname] = g7_acc
        g5_by_profile[pname] = g5_acc
        g6_by_profile[pname] = g6_acc
        g3_by_profile[pname] = g3_acc
        profile_summaries.append(row)
        if pname == "baseline":
            baseline_row = row

    if baseline_row:
        for row in profile_summaries:
            row["adoption_review"] = _adoption_vs_baseline(row, baseline_row)

    # profile_summary
    if profile_summaries:
        fields = list(profile_summaries[0].keys())
        for row in profile_summaries:
            for k in row:
                if k not in fields:
                    fields.append(k)
        with (out / "profile_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in profile_summaries:
                flat = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
                w.writerow(flat)

    (out / "profile_summary.json").write_text(
        json.dumps(
            {
                "component": "kabu_native.logic_lab",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "start_date": config.start_date,
                "end_date": config.end_date,
                "symbols": config.symbols,
                "profiles": list(by_profile.keys()),
                "paper_trade_status": "stopped_pending_logic_validation",
                "profiles_summary": profile_summaries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # trades_by_profile
    trade_rows: list[dict[str, Any]] = []
    for pname, results in by_profile.items():
        for r in results:
            for t in r.trades:
                row = t.to_row() if hasattr(t, "to_row") else dict(t)
                row["profile"] = pname
                row["trade_date"] = r.trade_date
                trade_rows.append(row)
    if trade_rows:
        tf = list(trade_rows[0].keys())
        with (out / "trades_by_profile.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=tf, extrasaction="ignore")
            w.writeheader()
            w.writerows(trade_rows)

    # candidates_by_profile
    cand_rows: list[dict[str, Any]] = []
    for results in by_profile.values():
        for r in results:
            cand_rows.extend(r.candidate_rows)
    if cand_rows:
        cf = list(cand_rows[0].keys())
        with (out / "candidates_by_profile.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cf, extrasaction="ignore")
            w.writeheader()
            w.writerows(cand_rows)

    # rejects_by_profile (G7 extended)
    from research.g7_diagnostic import (
        build_g7_definition_fix_report,
        reject_detail_row,
        summarize_g7,
    )

    reject_fields = [
        "profile",
        "reject_reason",
        "count",
        "missing_count",
        "zero_count",
        "below_threshold_count",
        "p50",
        "p75",
        "p90",
        "threshold",
        "g7_source",
    ]
    reject_rows: list[dict[str, Any]] = []
    for pname, results in by_profile.items():
        agg: Counter[str] = Counter()
        for r in results:
            agg.update(r.reject_counts)
        g7_acc = g7_by_profile.get(pname)
        for reason, cnt in agg.most_common():
            if g7_acc is not None and reason.startswith("G7"):
                reject_rows.append(reject_detail_row(pname, reason, g7_acc, cnt))
            else:
                reject_rows.append(
                    {
                        "profile": pname,
                        "reject_reason": reason,
                        "count": cnt,
                        "missing_count": "",
                        "zero_count": "",
                        "below_threshold_count": "",
                        "p50": "",
                        "p75": "",
                        "p90": "",
                        "threshold": "",
                    }
                )
    if reject_rows:
        with (out / "rejects_by_profile.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=reject_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(reject_rows)

    # g7_trading_value_diagnostic.json (per profile)
    g7_export = {p: summarize_g7(g7_by_profile[p]) for p in g7_by_profile if g7_by_profile[p].eval_count}
    if g7_export:
        (out / "g7_trading_value_diagnostic.json").write_text(
            json.dumps(g7_export, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    native_root = out.parents[4] if len(out.parents) > 4 else out
    fix_report = build_g7_definition_fix_report(
        profile_summaries=profile_summaries,
        g7_by_profile=g7_by_profile,
        native_root=native_root,
        num_days=num_days,
    )
    (out / "g7_definition_fix_report.json").write_text(
        json.dumps(fix_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    from research.g5_diagnostic import build_g5_diagnostic_report, write_g5_csv_outputs

    if any(acc.eval_count for acc in g5_by_profile.values()):
        g5_report = build_g5_diagnostic_report(g5_by_profile)
        (out / "g5_diagnostic_report.json").write_text(
            json.dumps(g5_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_g5_csv_outputs(out, g5_by_profile)

    from research.g6_diagnostic import build_g6_diagnostic_report, write_g6_csv_outputs

    if any(acc.eval_count for acc in g6_by_profile.values()):
        g6_report = build_g6_diagnostic_report(g6_by_profile)
        (out / "g6_diagnostic_report.json").write_text(
            json.dumps(g6_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_g6_csv_outputs(out, g6_by_profile)

    from research.g3_diagnostic import build_g3_diagnostic_report, write_g3_csv_outputs

    if any(acc.eval_count for acc in g3_by_profile.values()):
        g3_report = build_g3_diagnostic_report(g3_by_profile)
        (out / "g3_diagnostic_report.json").write_text(
            json.dumps(g3_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_g3_csv_outputs(out, g3_by_profile)

    phase35_run = any(str(r.get("profile")) in ENTRY_V2_PHASE35_PROFILES for r in profile_summaries)
    if phase35_run:
        from research.momentum_v13_analysis import write_momentum_phase35_outputs

        write_momentum_phase35_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase34_run = any(str(r.get("profile")) in ENTRY_V2_PHASE34_PROFILES for r in profile_summaries)
    if phase34_run and not phase35_run:
        from research.momentum_v12_analysis import write_momentum_phase34_outputs

        write_momentum_phase34_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase33_run = any(str(r.get("profile")) in ENTRY_V2_PHASE33_PROFILES for r in profile_summaries)
    if phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v11_analysis import write_momentum_phase33_outputs

        write_momentum_phase33_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase32_run = any(str(r.get("profile")) in ENTRY_V2_PHASE32_PROFILES for r in profile_summaries)
    if phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v10_analysis import write_momentum_phase32_outputs

        write_momentum_phase32_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase31_run = any(str(r.get("profile")) in ENTRY_V2_PHASE31_PROFILES for r in profile_summaries)
    if phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v9_analysis import write_momentum_phase31_outputs

        write_momentum_phase31_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase30_run = any(str(r.get("profile")) in ENTRY_V2_PHASE30_PROFILES for r in profile_summaries)
    if phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v8_analysis import write_momentum_phase30_outputs

        write_momentum_phase30_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase29_run = any(str(r.get("profile")) in ENTRY_V2_PHASE29_PROFILES for r in profile_summaries)
    if phase29_run and not phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v7_analysis import write_momentum_phase29_outputs

        write_momentum_phase29_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase28_run = any(str(r.get("profile")) in ENTRY_V2_PHASE28_PROFILES for r in profile_summaries)
    if phase28_run and not phase29_run and not phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v6_analysis import write_momentum_phase28_outputs

        write_momentum_phase28_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase27_run = any(str(r.get("profile")) in ENTRY_V2_PHASE27_PROFILES for r in profile_summaries)
    if phase27_run and not phase28_run and not phase29_run and not phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v5_analysis import write_momentum_phase27_outputs

        write_momentum_phase27_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase26_run = any(str(r.get("profile")) in ENTRY_V2_PHASE26_PROFILES for r in profile_summaries)
    if phase26_run and not phase27_run and not phase28_run and not phase29_run and not phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v4_analysis import write_momentum_phase26_outputs

        write_momentum_phase26_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase25_run = any(str(r.get("profile")) in ENTRY_V2_PHASE25_PROFILES for r in profile_summaries)
    if phase25_run and not phase26_run and not phase27_run and not phase28_run and not phase29_run and not phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.momentum_v2_loss_analysis import write_momentum_phase25_outputs

        write_momentum_phase25_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )

    phase24_run = any(str(r.get("profile")) in ENTRY_V2_PHASE24_PROFILES for r in profile_summaries)
    if phase24_run and not phase25_run and not phase26_run and not phase27_run and not phase28_run and not phase29_run and not phase30_run and not phase31_run and not phase32_run and not phase33_run and not phase34_run and not phase35_run:
        from research.entry_v2_deep_dive import write_entry_v2_phase24_outputs

        write_entry_v2_phase24_outputs(
            out,
            by_profile=by_profile,
            profile_summaries=profile_summaries,
        )
    elif any(str(r.get("profile")) in ENTRY_V2_PROFILE_NAMES for r in profile_summaries):
        _write_entry_v2_comparison(out, profile_summaries)

    # symbol_summary / day_summary per profile
    sym_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    for pname, results in by_profile.items():
        by_sym: dict[str, list[Any]] = defaultdict(list)
        by_day: dict[str, list[Any]] = defaultdict(list)
        for r in results:
            for t in r.trades:
                by_sym[r.symbol].append(t)
                by_day[r.trade_date].append(t)
        for sym, ts in sorted(by_sym.items()):
            m = _trade_metrics(ts, days=1, symbols={sym})
            sym_rows.append({"profile": pname, "symbol": sym, **m})
        for day, ts in sorted(by_day.items()):
            m = _trade_metrics(ts, days=1, symbols=set(by_sym.keys()))
            day_rows.append({"profile": pname, "trade_date": day, **m})

    if sym_rows:
        sf = list(sym_rows[0].keys())
        with (out / "symbol_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=sf, extrasaction="ignore")
            w.writeheader()
            w.writerows(sym_rows)
    if day_rows:
        df = list(day_rows[0].keys())
        with (out / "day_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=df, extrasaction="ignore")
            w.writeheader()
            w.writerows(day_rows)

    momentum_research = any(
        str(r.get("profile"))
        in (
            *ENTRY_V2_PHASE25_PROFILES,
            *ENTRY_V2_PHASE26_PROFILES,
            *ENTRY_V2_PHASE27_PROFILES,
            *ENTRY_V2_PHASE28_PROFILES,
            *ENTRY_V2_PHASE29_PROFILES,
            *ENTRY_V2_PHASE30_PROFILES,
            *ENTRY_V2_PHASE31_PROFILES,
            *ENTRY_V2_PHASE32_PROFILES,
            *ENTRY_V2_PHASE33_PROFILES,
            *ENTRY_V2_PHASE34_PROFILES,
            *ENTRY_V2_PHASE35_PROFILES,
        )
        for r in profile_summaries
    )
    if config.research_exit_phase36 or momentum_research:
        from research.research_exit_criteria import run_research_exit_analysis

        logic_lab_root = out.parent.parent if out.parent.name.startswith("run_") else out.parent
        run_research_exit_analysis(
            out,
            phase_run_roots=[logic_lab_root, out.parent],
        )
        log.info("logic_lab wrote research exit criteria (phase 36)")

    if config.validation_phase37:
        from research.phase37_validation import (
            DEFAULT_OOS_WINDOWS,
            Phase37Input,
            VALIDATION_PROFILES,
            _latest_trading_date,
            _trading_days_between,
            run_logic_lab_for_window,
            run_phase37_validation,
        )

        oos_runs: list[dict[str, Any]] = []
        for spec in DEFAULT_OOS_WINDOWS:
            start = str(spec["start"])
            end = str(spec["end"] or _latest_trading_date(config.data_roots) or config.end_date)
            days = _trading_days_between(start, end, config.data_roots)
            if not days:
                log.warning("phase37 OOS %s: no data for %s..%s", spec["id"], start, end)
                continue
            oos_out = (
                out.parent.parent
                / "phase37_oos"
                / out.name
                / str(spec["id"])
            )
            log.info("phase37 OOS replay %s %s..%s", spec["id"], start, end)
            oos_path = run_logic_lab_for_window(
                start=start,
                end=end,
                symbols=config.symbols,
                data_roots=config.data_roots,
                output_dir=oos_out,
                repo_root=config.repo_root or out,
                tier=config.tier,
            )
            oos_runs.append(
                {"id": spec["id"], "start": start, "end": end, "run_dir": str(oos_path)}
            )
        if oos_runs:
            run_phase37_validation(
                Phase37Input(
                    is_run_dir=out,
                    oos_runs=oos_runs,
                    data_roots=config.data_roots,
                    universe_symbol_count=len(config.symbols),
                    output_dir=out,
                )
            )
            log.info("logic_lab wrote phase37 validation outputs")

    if config.validation_phase38:
        from research.extended_oos_validation import resolve_extended_windows
        from research.phase38_validation import (
            Phase38Input,
            run_extended_oos_replays,
            run_phase38_validation,
        )

        oos38 = run_extended_oos_replays(
            symbols=config.symbols,
            data_roots=config.data_roots,
            repo_root=config.repo_root or out,
            reference_run_dir=out,
            tier=config.tier,
        )
        if not oos38:
            specs = resolve_extended_windows(config.data_roots)
            log.warning("phase38: no OOS windows with data — partial reports only")
        run_phase38_validation(
            Phase38Input(
                reference_run_dir=out,
                window_runs=oos38,
                data_roots=config.data_roots,
                universe_symbol_count=len(config.symbols),
                output_dir=out,
                repo_root=config.repo_root,
                tier=config.tier,
            )
        )
        log.info("logic_lab wrote phase38 validation outputs")

    log.info("logic_lab wrote %s profiles=%s", out, list(by_profile.keys()))
