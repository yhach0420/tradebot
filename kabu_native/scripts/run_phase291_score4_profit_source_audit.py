#!/usr/bin/env python3
"""
Phase291: score4-and-below profit source audit (review only).

Explore profit buried under entry_score_v2>=5 gate while keeping v2>=5 as baseline policy.
Output: kabu_native/results/reports/phase291_score4_profit_source_audit.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase291_score4_profit_source_audit.json"

DATE_START = 20260518
DATE_END = 20260605
V2_MIN = 5
V1_MODE = "legacy"
V1_RATIO = 0.85
PF_SOURCE_MIN = 1.10
TRADE_SOURCE_MIN = 30
NO_TRADE_DAYS = ("20260604", "20260605")

HARD_EXCLUDE_REASONS = frozenset(
    {
        "symbol_cooloff",
        "risk_cluster_block",
        "daily_loss_guard",
        "wrong_profile",
        "outside_allowed_trading_window",
        "low_liquidity_shadow",
        "low_liquidity_shadow_reject",
    }
)

SCORE_GATE_REASONS = frozenset(
    {
        "entry_score_v2_below_threshold",
        "low_quality",
    }
)

REJECT_REPLAY_MAX_EVENTS = 500_000


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel: str) -> Any:
    path = REPO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str) -> bool:
    try:
        d = int(day)
        return DATE_START <= d <= DATE_END
    except ValueError:
        return False


def _session_stream(sid: str, summary: dict[str, Any]) -> str:
    base = sid.split("/")[-1].lower()
    source = str((summary or {}).get("source") or "").lower()
    mode = str((summary or {}).get("mode") or "").lower()
    if "live_full_session" in base or "live_session" in base or source == "live" or "live" in mode:
        return "live"
    if "push_replay" in base or "push_replay" in mode or source in ("push-replay", "push_replay"):
        return "push_replay"
    if source == "replay" or ("replay" in mode and "push" not in mode):
        return "replay"
    return "other"


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return []


def _read_summary(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "small_paper_summary.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _in_decision_pool(ev: dict[str, Any]) -> bool:
    et = str(ev.get("event_type") or "")
    if et == "accepted":
        return True
    if et == "rejected":
        return str(ev.get("gate_reject_reason") or "") not in HARD_EXCLUDE_REASONS
    return False


def _enrich_tokens(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _feature_token

    active: dict[str, bool] = {}
    score = 0
    active_points: dict[str, int] = {}
    for token, pts in SCORE_POINTS_V2.items():
        lbl = token.split(":", 1)[0]
        tok = _feature_token(lbl, ev)
        hit = tok == token
        active[token] = hit
        if hit:
            score += pts
            active_points[token] = pts
    pattern = "+".join(sorted(t for t, on in active.items() if on)) or "(none)"
    return {
        "active_tokens": active,
        "active_points": active_points,
        "entry_score_v2": score,
        "pattern": pattern,
    }


def _gap_to_score5(active: dict[str, bool], score: int) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    needed = max(0, V2_MIN - score)
    missing = {t: SCORE_POINTS_V2[t] for t in SCORE_POINTS_V2 if not active.get(t)}
    active_map = {t: SCORE_POINTS_V2[t] for t in SCORE_POINTS_V2 if active.get(t)}
    # Greedy: smallest single token that would reach 5, else combo hint
    bridging: list[dict[str, Any]] = []
    for token, pts in sorted(missing.items(), key=lambda x: x[1]):
        if pts >= needed:
            bridging.append({"token": token, "points": pts, "would_reach_score": score + pts})
    return {
        "points_to_score5": needed,
        "active_tokens": active_map,
        "missing_tokens": missing,
        "single_token_bridges": bridging[:5],
    }


def _accepted_outcomes(session_dir: Path, events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = {}
    st_path = session_dir / "structural_trades.csv"
    if st_path.is_file():
        with st_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol") or "")
                ent = str(row.get("entry_time") or "")
                pnl = _float(row.get("realized_pnl_pct"))
                if not sym or not ent or pnl is None:
                    continue
                out[(sym, ent)] = {
                    "pnl_pct": float(pnl),
                    "mfe_pct": _float(row.get("mfe_pct")) or 0.0,
                    "mae_pct": abs(_float(row.get("mae_pct")) or 0.0),
                }
        return out

    exits: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("event_type") or "") != "observer_exit":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[0] and key[1]:
            exits[key] = ev

    for ev in events:
        if str(ev.get("event_type") or "") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if not key[0] or not key[1]:
            continue
        ex = exits.get(key)
        if ex:
            pnl = _float(ex.get("pnl_pct"))
            mfe = _float(ex.get("peak_mfe_pct")) or _float(ex.get("rolling_mfe_pct"))
            mae = _float(ex.get("rolling_mae_pct"))
        else:
            pnl = _float(ev.get("pnl_pct"))
            mfe = _float(ev.get("peak_mfe_pct")) or _float(ev.get("rolling_mfe_pct"))
            mae = _float(ev.get("rolling_mae_pct"))
        if pnl is None:
            continue
        out[key] = {
            "pnl_pct": float(pnl),
            "mfe_pct": mfe or 0.0,
            "mae_pct": abs(mae or 0.0),
        }
    return out


def _replay_reject_outcomes(
    p71: Any,
    events: list[dict[str, Any]],
    keys: set[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, float]]:
    if not keys:
        return {}
    target_syms = {sym for sym, _ in keys}
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    metrics: dict[tuple[str, str], dict[str, float]] = {}
    inject: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        sym = str(ev.get("symbol") or "")
        if sym not in target_syms:
            continue
        ent = str(ev.get("entry_time") or "")
        key = (sym, ent)
        if key not in keys or key in inject:
            continue
        px = _float(ev.get("current_price"))
        if px and px > 0:
            inject[key] = ev
    if not inject:
        return {}
    injected: set[tuple[str, str]] = set()
    pending_inject = set(inject.keys())

    def close_act(act: Any, key: tuple[str, str], *, close_price: float) -> None:
        pnls = [float(t.get("pnl_pct") or 0) for t in act.rich_ticks]
        metrics[key] = {
            "pnl_pct": float(p71._pnl_pct(act.trade.entry_price, close_price)),
            "mfe_pct": max(pnls) if pnls else 0.0,
            "mae_pct": abs(min(pnls)) if pnls else 0.0,
        }

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if sym not in target_syms:
            continue
        ent = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent) if hasattr(p71, "_parse_ts") else _parse_ts(ent)
        price = _float(ev.get("current_price")) or 0.0
        if price <= 0:
            continue
        st = sym_states.setdefault(sym, p71.SymState())
        key = (sym, ent)
        if key in inject and key not in injected:
            injected.add(key)
            pending_inject.discard(key)
            if sym in active:
                old = active.pop(sym)
                ok = (old.trade.symbol, old.trade.entry_time)
                close_act(old, ok, close_price=price)
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent, price, float(inject[key].get("continuation_quality_score") or 0))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": price,
                        "pnl_pct": 0.0,
                        "quality": comps["quality"],
                        "momentum": comps["momentum"],
                        "favorable": comps["favorable"],
                        "pure_price_momentum": comps["pure_price_momentum"],
                        "vwap_strength": comps["vwap_strength"],
                        "mfe_proxy": comps["mfe_proxy"],
                    }
                ],
            )
            if not pending_inject and not active:
                break
        if str(ev.get("event_type") or "") == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append(
                {
                    "price": price,
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, price),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                k2 = (act.trade.symbol, act.trade.entry_time)
                close_act(act, k2, close_price=price)
                active.pop(sym, None)
                if not pending_inject and not active:
                    break
    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        k2 = (act.trade.symbol, act.trade.entry_time)
        close_act(act, k2, close_price=float(last_px))
    return metrics


@dataclass
class ObsRow:
    session_id: str
    stream: str
    day: str
    symbol: str
    entry_time: str
    event_type: str
    gate_reject_reason: str
    entry_score_v2: int
    active_tokens: dict[str, bool]
    pattern: str
    pnl_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None


def _metrics_from_rows(rows: list[ObsRow]) -> dict[str, Any]:
    closed = [r for r in rows if r.pnl_pct is not None]
    if not closed:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "avg_mfe_pct": None,
            "avg_mae_pct": None,
        }
    pnls = [float(r.pnl_pct) for r in closed]
    wins = sum(1 for p in pnls if p > 0)
    n = len(closed)
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else "inf",
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "win_rate": round(wins / n, 4),
        "avg_mfe_pct": round(sum(float(r.mfe_pct or 0) for r in closed) / n, 4),
        "avg_mae_pct": round(sum(float(r.mae_pct or 0) for r in closed) / n, 4),
    }


def _pattern_breakdown(rows: list[ObsRow], appearances: list[ObsRow], *, score: int) -> list[dict[str, Any]]:
    out_rows = [r for r in rows if r.entry_score_v2 == score]
    apps = [r for r in appearances if r.entry_score_v2 == score]
    by_pattern: dict[str, list[ObsRow]] = defaultdict(list)
    for r in out_rows:
        by_pattern[r.pattern].append(r)
    app_counts = Counter(r.pattern for r in apps)
    acc_counts = Counter(r.pattern for r in apps if r.event_type == "accepted")
    result: list[dict[str, Any]] = []
    for pattern in sorted(app_counts.keys(), key=lambda p: (-app_counts[p], p)):
        m = _metrics_from_rows(by_pattern.get(pattern, []))
        active_tokens = [] if pattern == "(none)" else pattern.split("+")
        result.append(
            {
                "pattern": pattern,
                "active_tokens": active_tokens,
                "appearance_count": app_counts[pattern],
                "accepted_count": acc_counts.get(pattern, 0),
                **m,
            }
        )
    return result


def _discover_sessions() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day):
            continue
        events = _load_events(summary_path.parent)
        if not events:
            continue
        summary = _read_summary(summary_path.parent)
        found.append(
            {
                "session_id": sid,
                "day": day,
                "stream": _session_stream(sid, summary),
                "session_dir": str(summary_path.parent),
                "event_count": len(events),
            }
        )
    return found


def _score_band_block(
    outcome_rows: list[ObsRow],
    appearance_rows: list[ObsRow],
    *,
    band: str,
) -> dict[str, Any]:
    if band == "score6_plus":
        out = [r for r in outcome_rows if r.entry_score_v2 >= 6]
        app = [r for r in appearance_rows if r.entry_score_v2 >= 6]
    else:
        target = int(band.replace("score", ""))
        out = [r for r in outcome_rows if r.entry_score_v2 == target]
        app = [r for r in appearance_rows if r.entry_score_v2 == target]
    m = _metrics_from_rows(out)
    return {
        "appearance_count": len(app),
        "accepted_count": sum(1 for r in app if r.event_type == "accepted"),
        **m,
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p291", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    sessions = _discover_sessions()
    appearance_rows: list[ObsRow] = []
    outcome_rows: list[ObsRow] = []
    replay_skipped_sessions: list[dict[str, Any]] = []

    for i, sess in enumerate(sessions, 1):
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        print(f"[{i}/{len(sessions)}] {sess['session_id']} events={len(events)}", flush=True)
        accepted_out = _accepted_outcomes(sdir, events)
        reject_keys: set[tuple[str, str]] = set()

        for ev in events:
            if not _in_decision_pool(ev):
                continue
            sym = str(ev.get("symbol") or "")
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            if not sym or not ent:
                continue
            tok = _enrich_tokens(ev)
            base = ObsRow(
                session_id=sess["session_id"],
                stream=sess["stream"],
                day=sess["day"],
                symbol=sym,
                entry_time=ent,
                event_type=str(ev.get("event_type") or ""),
                gate_reject_reason=str(ev.get("gate_reject_reason") or ""),
                entry_score_v2=int(tok["entry_score_v2"]),
                active_tokens=dict(tok["active_tokens"]),
                pattern=str(tok["pattern"]),
            )
            appearance_rows.append(base)
            if base.event_type == "accepted":
                fin = accepted_out.get((sym, ent))
                if fin:
                    outcome_rows.append(
                        replace(
                            base,
                            pnl_pct=fin["pnl_pct"],
                            mfe_pct=fin["mfe_pct"],
                            mae_pct=fin["mae_pct"],
                        )
                    )
            else:
                reject_keys.add((sym, ent))

        skip_replay = len(events) > REJECT_REPLAY_MAX_EVENTS
        if reject_keys and not skip_replay:
            vm = _replay_reject_outcomes(p71, events, reject_keys)
            seen: set[tuple[str, str]] = set()
            for ev in events:
                if str(ev.get("event_type") or "") != "rejected":
                    continue
                sym = str(ev.get("symbol") or "")
                ent = str(ev.get("entry_time") or "")
                key = (sym, ent)
                if key in seen or key not in vm:
                    continue
                seen.add(key)
                tok = _enrich_tokens(ev)
                fin = vm[key]
                outcome_rows.append(
                    ObsRow(
                        session_id=sess["session_id"],
                        stream=sess["stream"],
                        day=sess["day"],
                        symbol=sym,
                        entry_time=ent,
                        event_type="rejected",
                        gate_reject_reason=str(ev.get("gate_reject_reason") or ""),
                        entry_score_v2=int(tok["entry_score_v2"]),
                        active_tokens=dict(tok["active_tokens"]),
                        pattern=str(tok["pattern"]),
                        pnl_pct=fin["pnl_pct"],
                        mfe_pct=fin["mfe_pct"],
                        mae_pct=fin["mae_pct"],
                    )
                )
        elif reject_keys and skip_replay:
            replay_skipped_sessions.append(
                {
                    "session_id": sess["session_id"],
                    "event_count": len(events),
                    "reject_keys": len(reject_keys),
                    "reason": f"reject_replay_skipped_event_count>{REJECT_REPLAY_MAX_EVENTS}",
                }
            )

    score_bands = {
        band: _score_band_block(outcome_rows, appearance_rows, band=band)
        for band in [f"score{i}" for i in range(6)] + ["score6_plus"]
    }

    score4_breakdown = _pattern_breakdown(outcome_rows, appearance_rows, score=4)
    score3_breakdown = _pattern_breakdown(outcome_rows, appearance_rows, score=3)

    pattern_outcomes: dict[str, list[ObsRow]] = defaultdict(list)
    pattern_max_score: dict[str, int] = {}
    for r in outcome_rows:
        pattern_outcomes[r.pattern].append(r)
        pattern_max_score[r.pattern] = max(pattern_max_score.get(r.pattern, 0), r.entry_score_v2)

    profit_sources: list[dict[str, Any]] = []
    for pattern, rows in pattern_outcomes.items():
        max_score = pattern_max_score[pattern]
        if max_score > 4:
            continue
        m = _metrics_from_rows(rows)
        tc = int(m.get("trade_count") or 0)
        pf = m.get("profit_factor")
        try:
            pf_f = float(pf) if pf not in (None, "inf") else None
        except (TypeError, ValueError):
            pf_f = None
        if tc >= TRADE_SOURCE_MIN and pf_f is not None and pf_f > PF_SOURCE_MIN:
            profit_sources.append(
                {
                    "pattern": pattern,
                    "max_score_in_pattern": max_score,
                    "active_tokens": [] if pattern == "(none)" else pattern.split("+"),
                    **m,
                }
            )
    profit_sources.sort(key=lambda x: (-float(x.get("profit_factor") or 0), -int(x.get("trade_count") or 0)))

    rescue_candidates: list[dict[str, Any]] = []
    for row in score4_breakdown:
        tc = int(row.get("trade_count") or 0)
        if tc <= 0:
            continue
        pf = row.get("profit_factor")
        try:
            pf_f = float(pf) if pf not in (None, "inf") else None
        except (TypeError, ValueError):
            pf_f = None
        rescue_candidates.append(
            {
                "pattern": row["pattern"],
                "active_tokens": row.get("active_tokens") or [],
                "trade_count": tc,
                "profit_factor": pf,
                "total_pnl_pct": row.get("total_pnl_pct"),
                "avg_pnl_pct": row.get("avg_pnl_pct"),
                "win_rate": row.get("win_rate"),
                "appearance_count": row.get("appearance_count"),
                "meets_profit_source_criteria": bool(
                    tc >= TRADE_SOURCE_MIN and pf_f is not None and pf_f > PF_SOURCE_MIN
                ),
                "rescue_rule_example": f"allow score4 pattern [{row['pattern']}] as limited exception",
            }
        )
    rescue_candidates.sort(
        key=lambda x: (
            -int(x.get("meets_profit_source_criteria") or 0),
            -float(x.get("profit_factor") or 0),
            -int(x.get("trade_count") or 0),
        )
    )

    no_trade_day_audit: dict[str, Any] = {}
    for day in NO_TRADE_DAYS:
        live_apps = [r for r in appearance_rows if r.day == day and r.stream == "live"]
        live_outcomes = [r for r in outcome_rows if r.day == day and r.stream == "live"]
        live_accepts = [r for r in live_apps if r.event_type == "accepted"]
        live_v2_ge5_accepts = [r for r in live_accepts if r.entry_score_v2 >= V2_MIN]
        score_dist = Counter(r.entry_score_v2 for r in live_apps)
        reject_reason_dist = Counter(
            r.gate_reject_reason for r in live_apps if r.event_type == "rejected"
        )
        score4_stuck = [
            r
            for r in live_apps
            if r.entry_score_v2 == 4
            and r.event_type == "rejected"
            and r.gate_reject_reason in SCORE_GATE_REASONS
        ]
        # On 20260604/05 live had no score4; include highest-score near-miss (score3) for diagnosis.
        near_miss = [
            r
            for r in live_apps
            if r.entry_score_v2 == 3 and r.event_type == "rejected"
        ]

        def _stuck_row(r: ObsRow) -> dict[str, Any]:
            gap = _gap_to_score5(r.active_tokens, r.entry_score_v2)
            return {
                "session_id": r.session_id,
                "stream": r.stream,
                "symbol": r.symbol,
                "entry_time": r.entry_time,
                "event_type": r.event_type,
                "gate_reject_reason": r.gate_reject_reason,
                "entry_score_v2": r.entry_score_v2,
                "pattern": r.pattern,
                "active_tokens": gap["active_tokens"],
                "points_to_score5": gap["points_to_score5"],
                "missing_tokens": gap["missing_tokens"],
                "single_token_bridges": gap["single_token_bridges"],
            }

        stuck_rows = [_stuck_row(r) for r in sorted(score4_stuck, key=lambda x: (x.symbol, x.entry_time))]
        near_miss_rows = [_stuck_row(r) for r in sorted(near_miss, key=lambda x: (x.symbol, x.entry_time))[:50]]
        no_trade_day_audit[day] = {
            "calendar_day": day,
            "scope": "live_sessions_only",
            "accepted_count": len(live_accepts),
            "v2_ge5_accepted_count": len(live_v2_ge5_accepts),
            "realized_trade_count": len(live_outcomes),
            "score_appearance_distribution": {str(k): v for k, v in sorted(score_dist.items())},
            "reject_reason_distribution": dict(reject_reason_dist.most_common(10)),
            "score4_appearance_count": score_dist.get(4, 0),
            "score4_stuck_count": len(score4_stuck),
            "score3_near_miss_count": len(near_miss),
            "score5_appearance_count": sum(v for sc, v in score_dist.items() if sc >= V2_MIN),
            "max_score_observed": max(score_dist.keys()) if score_dist else None,
            "live_sessions": sorted({r.session_id for r in live_apps}),
            "score4_stuck_candidates": stuck_rows,
            "score3_near_miss_sample": near_miss_rows,
            "note": (
                "No score4/score5 live candidates on this day; "
                "bottleneck is score composition below 4 (mostly score1-2)."
                if score_dist.get(4, 0) == 0 and sum(v for sc, v in score_dist.items() if sc >= V2_MIN) == 0
                else ""
            ),
        }

    has_profit_source = bool(profit_sources)
    verdict = {
        "profit_source_exists_below_score5": has_profit_source,
        "qualifying_pattern_count": len(profit_sources),
        "criteria": {
            "max_score": 4,
            "profit_factor_gt": PF_SOURCE_MIN,
            "trade_count_gte": TRADE_SOURCE_MIN,
        },
        "top_profit_sources": profit_sources[:10],
        "phase292_recommendation": (
            "PF>1.10 source exists only at score1 (Momentum:low); score4-specific rescue has no pattern meeting criteria. "
            "For 20260604/05 no-trade days, bottleneck is score3-or-below (no score4/5 candidates on live). "
            "Phase292 should evaluate: (a) limited score1-pattern rescue is impractical (too broad); "
            "(b) score-boost path via missing HBRecent:no / Duration:high on live days."
            if has_profit_source
            else "No robust score4-or-below profit source met PF>1.10 and trade_count>=30"
        ),
        "rescue_candidates_meeting_criteria": [
            r["pattern"] for r in rescue_candidates if r.get("meets_profit_source_criteria")
        ],
        "score4_rescue_viable": bool(
            [r for r in rescue_candidates if r.get("meets_profit_source_criteria")]
        ),
    }

    report = {
        "phase": 291,
        "mode": "score4_profit_source_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "production_logic_changes_forbidden": True,
            "entry_score_v2_min_policy_unchanged": V2_MIN,
            "goal": "find profit under score5 without changing production gate",
        },
        "date_range": {"start": DATE_START, "end": DATE_END, "label": "20260518-20260605"},
        "method": {
            "decision_pool": "accepted + rejected excluding hard structural excludes",
            "outcomes": "accepted realized + reject counterfactual replay (phase71)",
            "reject_replay_skip": f"sessions with >{REJECT_REPLAY_MAX_EVENTS} events use accepted-only outcomes",
            "profit_source_criteria": {
                "max_score": 4,
                "profit_factor_gt": PF_SOURCE_MIN,
                "trade_count_gte": TRADE_SOURCE_MIN,
            },
        },
        "sessions": {
            "count": len(sessions),
            "ids": [s["session_id"] for s in sessions],
            "reject_replay_skipped": replay_skipped_sessions,
        },
        "population": {
            "appearance_count": len(appearance_rows),
            "outcome_count": len(outcome_rows),
            "score4_appearance_count": sum(1 for r in appearance_rows if r.entry_score_v2 == 4),
            "score4_outcome_count": sum(1 for r in outcome_rows if r.entry_score_v2 == 4),
            "score5_plus_appearance_count": sum(1 for r in appearance_rows if r.entry_score_v2 >= V2_MIN),
        },
        "1_score_bands": score_bands,
        "2_score4_pattern_breakdown": score4_breakdown,
        "3_score3_pattern_breakdown": score3_breakdown,
        "4_profit_sources_score4_or_below": profit_sources,
        "5_rescue_candidates_score4": rescue_candidates,
        "6_no_trade_day_audit": no_trade_day_audit,
        "verdict": verdict,
    }

    def _json_default(val: Any) -> Any:
        if val == float("inf"):
            return "inf"
        raise TypeError(type(val))

    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}", flush=True)
    print(
        f"sessions={len(sessions)} outcomes={len(outcome_rows)} "
        f"profit_sources={len(profit_sources)} verdict={has_profit_source}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
