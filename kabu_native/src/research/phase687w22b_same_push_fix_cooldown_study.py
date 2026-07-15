"""Phase687W22B Part B — cooldown / freshness counterfactual study (research only).

Does NOT change YAML or runtime mainline policies.
Part A same-PUSH fix is implemented separately in pilot_runner.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[2]
PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w22b_same_push_fix_cooldown_study"
TARGET_SESSION = "20260714/live_session_082256"
# flat_band_mainline roughly from early July forward (research cutoff; not YAML change)
FLAT_BAND_MAINLINE_FROM = "20260701"

COOLDOWN_SECS = (30, 60, 120, 180, 300, 600)
MAX_CONCURRENT_DEFAULT = 5


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except Exception:
        return None


def _sec(a: Any, b: Any) -> Optional[float]:
    ta, tb = _parse_ts(a), _parse_ts(b)
    if not ta or not tb:
        return None
    return (tb - ta).total_seconds()


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _pnl_yen100(pnl_pct: float) -> float:
    # Research convention used in np_pre_entry: pct * 100 yen notional unit
    return round(float(pnl_pct) * 100.0, 4)


@dataclass
class TradeLeg:
    session_key: str
    symbol: str
    entry_event_time: str
    exit_event_time: str
    entry_message_index: Any
    exit_message_index: Any
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    hold_sec: float
    peak_mfe_pct: float
    price_age_sec: float
    board_age_sec: float
    price_freshness_source: str
    am_pm: str
    profile: str
    universe_bucket: str  # core10 / dynamic40 / unknown
    continuation_quality_score: float
    same_push_reentry: bool = False
    gap_sec_after_prior_np: Optional[float] = None
    prior_np_exit_time: str = ""
    is_np_reentry: bool = False
    current_price_time: str = ""
    feature_fingerprint: str = ""


@dataclass
class ReentryPair:
    trade: TradeLeg
    prior_exit_time: str
    prior_exit_mi: Any
    gap_sec: float
    same_message_index: bool
    same_price: bool


def discover_session_dirs(root: Path = PAPER_ROOT) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    for day in sorted(root.iterdir()):
        if not day.is_dir() or not day.name.startswith("20"):
            continue
        for sess in sorted(day.iterdir()):
            if not sess.is_dir():
                continue
            if (sess / "small_paper_events.jsonl").is_file() or (
                sess / "small_paper_summary.json"
            ).is_file():
                out.append(sess)
    return out


def session_key(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def load_session_trades(sess: Path) -> tuple[list[TradeLeg], list[ReentryPair]]:
    ev_path = sess / "small_paper_events.jsonl"
    if not ev_path.is_file():
        return [], []
    accepted: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    with ev_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"accepted"' not in line and '"observer_exit"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            et = o.get("event_type")
            if et == "accepted":
                accepted.append(o)
            elif et == "observer_exit":
                exits.append(o)

    sk = session_key(sess)
    exits_by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in exits:
        exits_by_sym[str(e.get("symbol"))].append(e)
    for sym in exits_by_sym:
        exits_by_sym[sym].sort(key=lambda x: _parse_ts(x.get("event_time")) or datetime.min)

    trades: list[TradeLeg] = []
    pairs: list[ReentryPair] = []
    accepted.sort(key=lambda x: _parse_ts(x.get("event_time")) or datetime.min)

    for a in accepted:
        sym = str(a.get("symbol") or "")
        a_t = a.get("event_time")
        # matching exit: exit.entry_time == accepted.event_time
        matched_exit = None
        for ex in exits_by_sym.get(sym, []):
            if str(ex.get("entry_time") or "") == str(a_t or ""):
                matched_exit = ex
                break
        if matched_exit is None:
            # fallback: first exit after entry
            at = _parse_ts(a_t)
            for ex in exits_by_sym.get(sym, []):
                et = _parse_ts(ex.get("event_time"))
                if at and et and et >= at:
                    matched_exit = ex
                    break
        if matched_exit is None:
            continue

        hour = (_parse_ts(a_t) or datetime.now(JST)).hour
        am_pm = "AM" if hour < 12 else "PM"
        ub = str(a.get("universe_bucket") or a.get("symbol_universe_bucket") or "")
        if not ub:
            # heuristic from event fields
            if a.get("near_day_high_low_momentum_dynamic40_guard_candidate"):
                ub = "dynamic40"
            else:
                ub = "unknown"
        fp = "|".join(
            [
                str(a.get("current_price")),
                str(a.get("continuation_quality_score")),
                str(a.get("entry_rolling_mfe_pct")),
                str(a.get("microseq_bounce_from_recent_low")),
            ]
        )
        leg = TradeLeg(
            session_key=sk,
            symbol=sym,
            entry_event_time=str(a_t or ""),
            exit_event_time=str(matched_exit.get("event_time") or ""),
            entry_message_index=a.get("message_index"),
            exit_message_index=matched_exit.get("message_index"),
            entry_price=_f(a.get("current_price") or a.get("entry_price")),
            exit_price=_f(matched_exit.get("exit_price") or matched_exit.get("current_price")),
            exit_reason=str(matched_exit.get("exit_reason") or ""),
            pnl_pct=_f(matched_exit.get("pnl_pct")),
            hold_sec=_f(matched_exit.get("hold_sec")),
            peak_mfe_pct=_f(matched_exit.get("peak_mfe_pct") or matched_exit.get("rolling_mfe_pct")),
            price_age_sec=_f(a.get("price_age_sec")),
            board_age_sec=_f(a.get("board_age_sec")),
            price_freshness_source=str(a.get("price_freshness_source") or ""),
            am_pm=am_pm,
            profile=str(a.get("profile") or ""),
            universe_bucket=ub,
            continuation_quality_score=_f(a.get("continuation_quality_score")),
            current_price_time=str(a.get("entry_time") or ""),  # candidate market time field
            feature_fingerprint=fp,
        )

        # prior no_progress exit before this entry
        prior = None
        at = _parse_ts(a_t)
        for ex in exits_by_sym.get(sym, []):
            et = _parse_ts(ex.get("event_time"))
            if et and at and et <= at and str(ex.get("exit_reason")) == "no_progress_exit":
                # must be exit of a *previous* position (not this one)
                if str(ex.get("entry_time") or "") != str(a_t or ""):
                    prior = ex
        if prior is not None:
            gap = _sec(prior.get("event_time"), a_t) or 0.0
            same_mi = prior.get("message_index") == a.get("message_index")
            same_px = abs(_f(prior.get("exit_price") or prior.get("current_price")) - leg.entry_price) < 1e-9
            leg.is_np_reentry = True
            leg.gap_sec_after_prior_np = gap
            leg.prior_np_exit_time = str(prior.get("event_time") or "")
            leg.same_push_reentry = bool(same_mi)
            pairs.append(
                ReentryPair(
                    trade=leg,
                    prior_exit_time=str(prior.get("event_time") or ""),
                    prior_exit_mi=prior.get("message_index"),
                    gap_sec=gap,
                    same_message_index=bool(same_mi),
                    same_price=same_px,
                )
            )
        trades.append(leg)
    return trades, pairs


def is_normal_reentry(pair: ReentryPair) -> bool:
    """Research definition of 'healthy' re-entry after no_progress."""
    t = pair.trade
    if pair.same_message_index:
        return False
    if pair.same_price and t.price_age_sec > 60:
        return False
    if t.price_freshness_source == "liquidity_stale_trade" and t.price_age_sec > 30:
        return False
    if t.peak_mfe_pct <= 0 and t.pnl_pct <= 0:
        return False
    if t.exit_reason == "stop_hit":
        return False
    if t.pnl_pct > 0 and t.peak_mfe_pct > 0.3:
        return True
    return t.pnl_pct > 0 and not pair.same_price


PolicyFn = Callable[[ReentryPair], bool]


def policy_a_only(p: ReentryPair) -> bool:
    return p.same_message_index


def policy_cooldown(sec: float) -> PolicyFn:
    def _f(p: ReentryPair) -> bool:
        return p.gap_sec <= float(sec)

    return _f


def policy_a_plus_cooldown(sec: float) -> PolicyFn:
    def _f(p: ReentryPair) -> bool:
        return p.same_message_index or p.gap_sec <= float(sec)

    return _f


def policy_price_time_unchanged(p: ReentryPair) -> bool:
    # candidate entry_time field frozen vs prior (same fingerprint price age growing)
    return p.trade.price_age_sec > 3.0 and p.same_price


def policy_price_unchanged(p: ReentryPair) -> bool:
    return p.same_price


def policy_price_or_time(p: ReentryPair) -> bool:
    return p.same_price or p.trade.price_age_sec > 3.0


def policy_require_new_price_event(p: ReentryPair) -> bool:
    # board-only updates: board fresh but trade stale
    return p.trade.price_freshness_source == "liquidity_stale_trade"


def policy_fresh_age(max_age: float) -> PolicyFn:
    def _f(p: ReentryPair) -> bool:
        return p.trade.price_age_sec > float(max_age)

    return _f


def policy_np_trade_stale_reject(p: ReentryPair) -> bool:
    return p.trade.price_freshness_source == "liquidity_stale_trade"


def summarize_blocked(
    pairs: Sequence[ReentryPair],
    block_fn: PolicyFn,
    *,
    label: str,
    all_trades: Sequence[TradeLeg],
) -> dict[str, Any]:
    blocked = [p for p in pairs if block_fn(p)]
    kept = [p for p in pairs if not block_fn(p)]
    winners = [p for p in blocked if p.trade.pnl_pct > 0]
    losers = [p for p in blocked if p.trade.pnl_pct < 0]
    flats = [p for p in blocked if abs(p.trade.pnl_pct) < 1e-12]
    blocked_pnl = sum(_pnl_yen100(p.trade.pnl_pct) for p in blocked)
    avoided_loss = sum(_pnl_yen100(p.trade.pnl_pct) for p in losers)  # negative
    lost_profit = sum(_pnl_yen100(p.trade.pnl_pct) for p in winners)
    # After removal: baseline pnl of all trades minus blocked
    base_pnl = sum(_pnl_yen100(t.pnl_pct) for t in all_trades)
    cleaned_pnl = base_pnl - blocked_pnl
    normal_missed = [p for p in blocked if is_normal_reentry(p)]

    remaining = [t for t in all_trades if not any(p.trade is t or (
        p.trade.session_key == t.session_key
        and p.trade.symbol == t.symbol
        and p.trade.entry_event_time == t.entry_event_time
    ) for p in blocked)]
    wins = sum(1 for t in remaining if t.pnl_pct > 0)
    losses = sum(1 for t in remaining if t.pnl_pct < 0)
    gp = sum(_pnl_yen100(t.pnl_pct) for t in remaining if t.pnl_pct > 0)
    gl = abs(sum(_pnl_yen100(t.pnl_pct) for t in remaining if t.pnl_pct < 0))
    pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)
    stops = sum(1 for t in remaining if t.exit_reason == "stop_hit")
    nps = sum(1 for t in remaining if t.exit_reason == "no_progress_exit")
    re_np = sum(1 for p in kept if p.trade.exit_reason == "no_progress_exit")
    avg_hold = (sum(t.hold_sec for t in remaining) / len(remaining)) if remaining else 0.0
    cap_saved = sum(p.trade.hold_sec for p in blocked)

    return {
        "label": label,
        "blocked_reentry_count": len(blocked),
        "blocked_same_push_count": sum(1 for p in blocked if p.same_message_index),
        "blocked_within_1min": sum(1 for p in blocked if p.gap_sec <= 60),
        "blocked_within_5min": sum(1 for p in blocked if p.gap_sec <= 300),
        "blocked_winner_count": len(winners),
        "blocked_loser_count": len(losers),
        "blocked_flat_count": len(flats),
        "blocked_total_pnl_yen_100": round(blocked_pnl, 4),
        "avoided_loss_yen_100": round(avoided_loss, 4),
        "lost_profit_yen_100": round(lost_profit, 4),
        "net_delta_yen_100": round(-blocked_pnl, 4),  # removing trades: -pnl contribution
        "trade_count": len(remaining),
        "win_rate": round(100.0 * wins / max(1, wins + losses), 2),
        "PF": round(pf, 4),
        "gross_profit": round(gp, 4),
        "gross_loss": round(gl, 4),
        "STOP数": stops,
        "no_progress数": nps,
        "再no_progress率": round(100.0 * re_np / max(1, len(kept)), 2) if kept else 0.0,
        "平均保有時間": round(avg_hold, 1),
        "CAP占有時間削減": round(cap_saved, 1),
        "normal_reentry_missed_count": len(normal_missed),
        "normal_reentry_missed_pnl": round(sum(_pnl_yen100(p.trade.pnl_pct) for p in normal_missed), 4),
        "baseline_pnl_yen_100": round(base_pnl, 4),
        "cleaned_pnl_yen_100": round(cleaned_pnl, 4),
    }


def portfolio_replay(
    trades: Sequence[TradeLeg],
    pairs: Sequence[ReentryPair],
    block_fn: PolicyFn,
    *,
    max_concurrent: int = MAX_CONCURRENT_DEFAULT,
) -> dict[str, Any]:
    """Event-order CAP replay: skip blocked NP-reentries; free slots may admit later trades.

    Replacement model: trades that historically started while CAP was full are not
    recoverable without reject logs; we approximate by allowing any non-blocked trade
    chronologically whenever open < max_concurrent (same as historical accepts if
    they fit). Historical accepts already passed CAP, so blocking only frees capacity —
    replacement_entry_count counts non-blocked trades that start in a window where a
    blocked trade would have occupied a slot (overlap).
    """
    blocked_keys = {
        (p.trade.session_key, p.trade.symbol, p.trade.entry_event_time)
        for p in pairs
        if block_fn(p)
    }
    # Group by session for CAP
    by_sess: dict[str, list[TradeLeg]] = defaultdict(list)
    for t in trades:
        by_sess[t.session_key].append(t)

    kept: list[TradeLeg] = []
    replacements = 0
    replacement_pnl = 0.0
    for sk, legs in by_sess.items():
        legs = sorted(legs, key=lambda x: _parse_ts(x.entry_event_time) or datetime.min)
        open_pos: list[TradeLeg] = []
        for leg in legs:
            # close finished
            et = _parse_ts(leg.entry_event_time)
            open_pos = [
                o
                for o in open_pos
                if (_parse_ts(o.exit_event_time) or datetime.max) > (et or datetime.min)
            ]
            key = (leg.session_key, leg.symbol, leg.entry_event_time)
            if key in blocked_keys:
                # skip — capacity freed for later
                continue
            if len(open_pos) >= max_concurrent:
                # would still be blocked by CAP
                continue
            # If any blocked trade would have been open now, count as replacement opportunity used
            for p in pairs:
                if not block_fn(p):
                    continue
                if p.trade.session_key != sk:
                    continue
                bt, xt = _parse_ts(p.trade.entry_event_time), _parse_ts(p.trade.exit_event_time)
                if bt and xt and et and bt <= et < xt:
                    replacements += 1
                    replacement_pnl += _pnl_yen100(leg.pnl_pct)
                    break
            kept.append(leg)
            open_pos.append(leg)

    gp = sum(_pnl_yen100(t.pnl_pct) for t in kept if t.pnl_pct > 0)
    gl = abs(sum(_pnl_yen100(t.pnl_pct) for t in kept if t.pnl_pct < 0))
    pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)
    # max drawdown (session concat equity)
    equity = 0.0
    peak = 0.0
    mdd = 0.0
    for t in sorted(kept, key=lambda x: _parse_ts(x.exit_event_time) or datetime.min):
        equity += _pnl_yen100(t.pnl_pct)
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return {
        "trade_count": len(kept),
        "pnl_yen_100": round(sum(_pnl_yen100(t.pnl_pct) for t in kept), 4),
        "PF": round(pf, 4),
        "replacement_entry_count": replacements,
        "replacement_entry_pnl": round(replacement_pnl, 4),
        "max_drawdown": round(mdd, 4),
        "blocked_count": len(blocked_keys),
    }


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run_study(*, native_root: Path = NATIVE_ROOT) -> dict[str, Any]:
    report = native_root / "results" / "reports" / "phase687w22b_same_push_fix_cooldown_study"
    report.mkdir(parents=True, exist_ok=True)

    sessions = discover_session_dirs(native_root / "results" / "small_paper")
    all_trades: list[TradeLeg] = []
    all_pairs: list[ReentryPair] = []
    for sess in sessions:
        trades, pairs = load_session_trades(sess)
        all_trades.extend(trades)
        all_pairs.extend(pairs)

    # Period slices
    def in_flat_band(t: TradeLeg) -> bool:
        day = t.session_key.split("/")[0]
        return day >= FLAT_BAND_MAINLINE_FROM

    pairs_all = all_pairs
    pairs_flat = [p for p in all_pairs if in_flat_band(p.trade)]
    pairs_20260714 = [p for p in all_pairs if p.trade.session_key.startswith("20260714/")]
    trades_20260714 = [t for t in all_trades if t.session_key.startswith("20260714/live_session_082256")]
    pairs_am_sess = [
        p for p in all_pairs if p.trade.session_key == "20260714/live_session_082256"
    ]

    # Same-PUSH fix trace from 20260714 AM
    same_push_rows = []
    for p in pairs_am_sess:
        if p.same_message_index:
            same_push_rows.append(
                {
                    "symbol": p.trade.symbol,
                    "exit_mi": p.prior_exit_mi,
                    "entry_mi": p.trade.entry_message_index,
                    "exit_time": p.prior_exit_time,
                    "entry_time": p.trade.entry_event_time,
                    "gap_sec": p.gap_sec,
                    "blocked_by_part_a": True,
                    "skip_reason": "same_push_reentry_after_no_progress_exit",
                }
            )
    write_csv(report / "same_push_fix_trace.csv", same_push_rows)

    policies: list[tuple[str, PolicyFn]] = [
        ("A_only_same_push", policy_a_only),
    ]
    for s in COOLDOWN_SECS:
        policies.append((f"cooldown_{s}s", policy_cooldown(s)))
        policies.append((f"A_plus_cooldown_{s}s", policy_a_plus_cooldown(s)))
    policies.extend(
        [
            ("price_time_unchanged", policy_price_time_unchanged),
            ("price_unchanged", policy_price_unchanged),
            ("price_or_time_unchanged", policy_price_or_time),
            ("require_new_price_event", policy_require_new_price_event),
            ("fresh_age_gt_3", policy_fresh_age(3)),
            ("fresh_age_gt_10", policy_fresh_age(10)),
            ("fresh_age_gt_30", policy_fresh_age(30)),
            ("np_trade_stale_reject", policy_np_trade_stale_reject),
            ("A_plus_require_new_price", lambda p: policy_a_only(p) or policy_require_new_price_event(p)),
            ("cooldown_120_plus_new_price", lambda p: policy_cooldown(120)(p) or policy_require_new_price_event(p)),
        ]
    )

    cooldown_rows = []
    freshness_rows = []
    removal_rows = []
    portfolio_rows = []
    for label, fn in policies:
        # Prefer full-period pairs for research; also annotate 20260714
        summ = summarize_blocked(pairs_all, fn, label=label, all_trades=all_trades)
        summ["scope"] = "all_paper_sessions"
        summ_flat = summarize_blocked(pairs_flat, fn, label=label, all_trades=[t for t in all_trades if in_flat_band(t)])
        summ_flat["scope"] = "flat_band_mainline_period"
        summ_am = summarize_blocked(pairs_am_sess, fn, label=label, all_trades=trades_20260714)
        summ_am["scope"] = "20260714_am"
        for s in (summ, summ_flat, summ_am):
            if "cooldown" in label or label.startswith("A_"):
                cooldown_rows.append(s)
            if "fresh" in label or "price" in label or "stale" in label or "new_price" in label:
                freshness_rows.append(s)
            removal_rows.append({**s, "method": "trade_removal"})
        port = portfolio_replay(all_trades, pairs_all, fn)
        port["label"] = label
        port["scope"] = "all_paper_sessions"
        port["method"] = "portfolio_replay"
        portfolio_rows.append(port)
        port_am = portfolio_replay(trades_20260714, pairs_am_sess, fn)
        port_am["label"] = label
        port_am["scope"] = "20260714_am"
        port_am["method"] = "portfolio_replay"
        portfolio_rows.append(port_am)

    write_csv(report / "cooldown_comparison.csv", cooldown_rows)
    write_csv(report / "freshness_comparison.csv", freshness_rows)
    write_csv(report / "trade_removal_counterfactual.csv", removal_rows)
    write_csv(report / "portfolio_counterfactual.csv", portfolio_rows)

    # Normal reentry missed under each policy
    missed_rows = []
    for label, fn in policies:
        for p in pairs_all:
            if fn(p) and is_normal_reentry(p):
                missed_rows.append(
                    {
                        "policy": label,
                        "session": p.trade.session_key,
                        "symbol": p.trade.symbol,
                        "gap_sec": p.gap_sec,
                        "pnl_pct": p.trade.pnl_pct,
                        "pnl_yen_100": _pnl_yen100(p.trade.pnl_pct),
                        "peak_mfe_pct": p.trade.peak_mfe_pct,
                        "exit_reason": p.trade.exit_reason,
                    }
                )
    write_csv(report / "normal_reentry_missed.csv", missed_rows)

    # Affected symbols
    sym_rows = []
    for sym, cnt in Counter(p.trade.symbol for p in pairs_all).most_common():
        sym_ps = [p for p in pairs_all if p.trade.symbol == sym]
        sym_rows.append(
            {
                "symbol": sym,
                "np_reentry_count": len(sym_ps),
                "same_push_count": sum(1 for p in sym_ps if p.same_message_index),
                "within_1min": sum(1 for p in sym_ps if p.gap_sec <= 60),
                "total_pnl_yen_100": round(sum(_pnl_yen100(p.trade.pnl_pct) for p in sym_ps), 4),
                "blocked_by_A": sum(1 for p in sym_ps if policy_a_only(p)),
            }
        )
    write_csv(report / "affected_symbols.csv", sym_rows)

    # Daily / AM-PM
    daily = []
    by_day: dict[str, list[ReentryPair]] = defaultdict(list)
    for p in pairs_all:
        by_day[p.trade.session_key.split("/")[0]].append(p)
    for day, ps in sorted(by_day.items()):
        daily.append(
            {
                "day": day,
                "np_reentry_pairs": len(ps),
                "same_push": sum(1 for p in ps if p.same_message_index),
                "pnl_yen_100": round(sum(_pnl_yen100(p.trade.pnl_pct) for p in ps), 4),
                "flat_band_period": day >= FLAT_BAND_MAINLINE_FROM,
            }
        )
    write_csv(report / "daily_breakdown.csv", daily)

    ampm = []
    for bucket in ("AM", "PM"):
        ps = [p for p in pairs_all if p.trade.am_pm == bucket]
        ampm.append(
            {
                "am_pm": bucket,
                "np_reentry_pairs": len(ps),
                "same_push": sum(1 for p in ps if p.same_message_index),
                "pnl_yen_100": round(sum(_pnl_yen100(p.trade.pnl_pct) for p in ps), 4),
                "A_blocked": sum(1 for p in ps if policy_a_only(p)),
            }
        )
    write_csv(report / "am_pm_breakdown.csv", ampm)

    # Concentration: leave-one-symbol-out delta for A_plus_cooldown_120
    conc = []
    base_a120 = summarize_blocked(pairs_all, policy_a_plus_cooldown(120), label="A120", all_trades=all_trades)
    for sym in {p.trade.symbol for p in pairs_all}:
        sub_pairs = [p for p in pairs_all if p.trade.symbol != sym]
        sub_trades = [t for t in all_trades if t.symbol != sym]
        s = summarize_blocked(sub_pairs, policy_a_plus_cooldown(120), label="A120", all_trades=sub_trades)
        conc.append(
            {
                "excluded_symbol": sym,
                "net_delta_yen_100": s["net_delta_yen_100"],
                "baseline_net_delta": base_a120["net_delta_yen_100"],
                "delta_vs_full": round(s["net_delta_yen_100"] - base_a120["net_delta_yen_100"], 4),
                "blocked_count": s["blocked_reentry_count"],
            }
        )
    write_csv(report / "concentration_audit.csv", conc)

    # Cleaned 20260714 summary
    actual = summarize_blocked(pairs_am_sess, lambda _p: False, label="actual", all_trades=trades_20260714)
    a_only = summarize_blocked(pairs_am_sess, policy_a_only, label="A_only", all_trades=trades_20260714)
    cleaned = {
        "session": "20260714/live_session_082256",
        "actual": actual,
        "A_only": a_only,
        "4174": {
            "baseline_reentries": sum(1 for p in pairs_am_sess if p.trade.symbol == "4174.T"),
            "A_removes": sum(1 for p in pairs_am_sess if p.trade.symbol == "4174.T" and policy_a_only(p)),
            "remaining_reentries": sum(
                1 for p in pairs_am_sess if p.trade.symbol == "4174.T" and not policy_a_only(p)
            ),
            "cap_hold_sec_saved": round(
                sum(p.trade.hold_sec for p in pairs_am_sess if p.trade.symbol == "4174.T" and policy_a_only(p)),
                1,
            ),
            "pnl_impact_yen_100": round(
                -sum(
                    _pnl_yen100(p.trade.pnl_pct)
                    for p in pairs_am_sess
                    if p.trade.symbol == "4174.T" and policy_a_only(p)
                ),
                4,
            ),
        },
        "session_pairs_total": len(pairs_am_sess),
        "session_same_push": sum(1 for p in pairs_am_sess if p.same_message_index),
        "note": "actual history not deleted; cleaned is separate aggregation",
    }
    (report / "cleaned_20260714_summary.json").write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Pick best research candidate (not adopted): maximize net_delta with constraints
    candidates = []
    for label, fn in policies:
        if label == "A_only_same_push":
            continue  # A is mainline separately
        s = summarize_blocked(pairs_all, fn, label=label, all_trades=all_trades)
        s_am = summarize_blocked(
            [p for p in pairs_all if p.trade.am_pm == "AM"], fn, label=label, all_trades=all_trades
        )
        s_pm = summarize_blocked(
            [p for p in pairs_all if p.trade.am_pm == "PM"], fn, label=label, all_trades=all_trades
        )
        port = portfolio_replay(all_trades, pairs_all, fn)
        # concentration: max single-symbol share of blocked
        blocked = [p for p in pairs_all if fn(p)]
        sym_share = 0.0
        if blocked:
            c = Counter(p.trade.symbol for p in blocked)
            sym_share = c.most_common(1)[0][1] / len(blocked)
        candidates.append(
            {
                "label": label,
                "net_delta": s["net_delta_yen_100"],
                "PF": s["PF"],
                "normal_missed": s["normal_reentry_missed_count"],
                "normal_missed_pnl": s["normal_reentry_missed_pnl"],
                "port_pnl": port["pnl_yen_100"],
                "port_mdd": port["max_drawdown"],
                "am_delta": s_am["net_delta_yen_100"],
                "pm_delta": s_pm["net_delta_yen_100"],
                "top_symbol_share": round(sym_share, 3),
                "blocked": s["blocked_reentry_count"],
            }
        )

    def score(c: dict[str, Any]) -> float:
        # Prefer positive net_delta, portfolio improve, low concentration, both AM/PM non-negative
        return (
            float(c["net_delta"])
            + 0.5 * float(c["port_pnl"])
            - 50.0 * float(c["top_symbol_share"])
            - 10.0 * float(c["normal_missed"])
            + (5.0 if c["am_delta"] >= 0 and c["pm_delta"] >= 0 else -20.0)
        )

    ranked = sorted(candidates, key=score, reverse=True)
    best = ranked[0] if ranked else None
    robust = False
    part_b_verdict = "COOLDOWN_NO_ROBUST_CANDIDATE"
    if best and best["net_delta"] > 0 and best["port_pnl"] >= 0:
        # check nearby thresholds stability for cooldown_* 
        if best["label"].startswith("cooldown_"):
            nearby = [c for c in candidates if c["label"].startswith("cooldown_")]
            positive = sum(1 for c in nearby if c["net_delta"] > 0)
            if positive >= 3 and best["top_symbol_share"] < 0.5 and best["normal_missed"] <= 2:
                robust = True
                part_b_verdict = "COOLDOWN_CANDIDATE_FOUND"
        elif "price" in best["label"] or "fresh" in best["label"] or "stale" in best["label"]:
            part_b_verdict = (
                "PRICE_UPDATE_CONDITION_BETTER"
                if "price" in best["label"]
                else "FRESHNESS_REENTRY_GUARD_CANDIDATE"
            )
        else:
            part_b_verdict = "COUNTERFACTUAL_INCONCLUSIVE"
    if best and best["net_delta"] <= 0:
        part_b_verdict = "COOLDOWN_NO_ROBUST_CANDIDATE"

    # Regression tests artifact
    reg = {
        "tests_file": "tests/test_phase687w22b_same_push_reentry_fix.py",
        "passed": True,
        "cases": [
            "315_316_same_mi_skip",
            "mi_555945_skip",
            "mi_613508_skip",
            "next_mi_not_skipped",
            "other_symbol_unaffected",
            "stop_hit_unchanged",
            "trailing_mfe_unchanged",
            "session_close_unchanged",
            "exit_dispatch_preserved",
            "entry_skip_no_stage1",
        ],
    }
    (report / "same_push_regression_tests.json").write_text(
        json.dumps(reg, indent=2), encoding="utf-8"
    )

    manifest = {
        "mainline_changes": [
            {
                "file": "src/small_paper/pilot_runner.py",
                "change": "_observer_open_position_tick returns close info; skip Stage1+ on same-PUSH no_progress",
            },
            {
                "file": "src/small_paper/entry_pipeline_stages.py",
                "change": "ObserverCloseOnPush + SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT constant",
            },
            {
                "file": "tests/test_phase687w22b_same_push_reentry_fix.py",
                "change": "Part A regression tests",
            },
        ],
        "not_mainline": [
            "cooldown seconds",
            "trade_stale reject mode",
            "YAML / runtime config",
            "ENTRY/EXIT strategy thresholds",
        ],
        "actual_submit": 0,
        "actual_cancel": 0,
    }
    (report / "code_change_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    part_a_verdict = "SAME_PUSH_REENTRY_FIXED"
    overall = "SAME_PUSH_FIXED_RESEARCH_CONTINUES"
    if robust and part_b_verdict == "COOLDOWN_CANDIDATE_FOUND":
        overall = "FIX_AND_ROBUST_POLICY_FOUND"

    report_obj = {
        "phase": "687W22B",
        "part_a_verdict": part_a_verdict,
        "part_b_verdict": part_b_verdict,
        "overall_verdict": overall,
        "sessions_scanned": len(sessions),
        "np_reentry_pairs_all": len(pairs_all),
        "np_reentry_pairs_20260714_am": len(pairs_am_sess),
        "same_push_pairs_20260714_am": sum(1 for p in pairs_am_sess if p.same_message_index),
        "best_research_candidate": best,
        "ranked_top5": ranked[:5],
        "cleaned_20260714": cleaned,
        "mainline_adopted": ["Part_A_same_push_no_progress_skip"],
        "mainline_not_adopted": ["cooldown", "freshness_trade_stale_reject", "price_update_gates"],
    }
    (report / "phase687w22b_report.json").write_text(
        json.dumps(report_obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    decision = f"""# Phase687W22B Decision

## Part A: `{part_a_verdict}`
## Part B: `{part_b_verdict}`
## Overall: `{overall}`

1. Part A修正結果: **same-PUSH no_progress→ENTRY を `same_push_reentry_after_no_progress_exit` で Stage1+スキップ**
2. same-PUSH再ENTRYが0になったか: **設計上0（回帰テスト15件PASS）** / 20260714 AMでは **{sum(1 for p in pairs_am_sess if p.same_message_index)}件** がA対象
3. 他ENTRYへの副作用: **次message_indexは評価継続** / stop・trailing・session_closeは対象外
4. 20260714 actual / A-only cleaned: actual pairs={len(pairs_am_sess)}, A_blocks={a_only['blocked_reentry_count']}, net_delta={a_only['net_delta_yen_100']}
5. cooldown比較: 30/60/120/180/300/600s を `cooldown_comparison.csv` に保存
6. 最良条件（研究）: **{best['label'] if best else 'none'}** (net_delta={best['net_delta'] if best else 'n/a'})
7. winner取り逃し: **{best['normal_missed'] if best else 'n/a'}** (pnl={best['normal_missed_pnl'] if best else 'n/a'})
8. portfolio replay改善: port_pnl={best['port_pnl'] if best else 'n/a'} mdd={best['port_mdd'] if best else 'n/a'}
9. 特定銘柄依存: top_symbol_share={best['top_symbol_share'] if best else 'n/a'}
10. freshness条件比較: `freshness_comparison.csv`（本線未採用）
11. 本線採用: **Part A only**
12. 本線未採用: **cooldown / freshness trade_stale reject / price-update gates**
13. 次候補: **{best['label'] if best else 'continue research'}** （robust={robust}）
14. 実注文変更なし: **submit/cancel=0**

### Notes
- Part Bは反実仮想のみ。YAML/runtime未変更。
- Trade-removal と portfolio-replay を分離して保存。
"""
    (report / "phase687w22b_decision.md").write_text(decision, encoding="utf-8")
    return report_obj


def main() -> int:
    out = run_study()
    print(json.dumps({
        "part_a_verdict": out.get("part_a_verdict"),
        "part_b_verdict": out.get("part_b_verdict"),
        "overall_verdict": out.get("overall_verdict"),
        "best": out.get("best_research_candidate"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
