#!/usr/bin/env python3
"""
Phase289: entry_score_v2 factor attribution audit (review only).

Enumerate SCORE_POINTS_V2 components, per-factor and combination metrics,
score-band breakdown, and counterfactual exclusion estimates.

Output: kabu_native/results/reports/phase289_entry_score_v2_factor_attribution.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase289_entry_score_v2_factor_attribution.json"

DATE_START = 20260518
DATE_END = 20260605
V2_GE5 = 5
V1_MODE = "legacy"
V1_RATIO = 0.85

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

PF_EFFECTIVE = 1.05
PF_SUSPICIOUS = 0.95


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


def _classify_pf(pf: Optional[float]) -> str:
    if pf is None:
        return "insufficient_data"
    if pf > PF_EFFECTIVE:
        return "effective"
    if pf < PF_SUSPICIOUS:
        return "suspicious"
    return "neutral"


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


def _trigger_condition(token: str) -> str:
    from small_paper.entry_expectancy_score_shadow import TERTILE_CUTOFFS

    if token == "HBRecent:no":
        return "entry_high_break_recent is false"
    if token == "HBRecent:yes":
        return "entry_high_break_recent is true"
    field_map = {
        "Board": "entry_order_book_imbalance",
        "TV": "trading_value",
        "Momentum": "momentum_continuation_score",
        "Duration": "max_continuation_duration",
        "RollingMAE": "rolling_mae_pct",
        "Price": "current_price",
    }
    if ":" not in token:
        return "unknown"
    label, level = token.split(":", 1)
    fld = field_map.get(label, label)
    cuts = TERTILE_CUTOFFS.get(label)
    if not cuts:
        return f"{fld} level={level}"
    p33, p66 = cuts["p33"], cuts["p66"]
    if level == "low":
        return f"{fld} <= {p33}"
    if level == "mid":
        return f"{p33} < {fld} <= {p66}"
    if level == "high":
        return f"{fld} > {p66}"
    return f"{fld} level={level}"


def _enumerate_score_elements() -> list[dict[str, Any]]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, TERTILE_CUTOFFS

    rows: list[dict[str, Any]] = []
    for token, pts in SCORE_POINTS_V2.items():
        label = token.split(":", 1)[0]
        rows.append(
            {
                "token": token,
                "label": label,
                "points": pts,
                "trigger_condition": _trigger_condition(token),
                "tertile_cutoffs": TERTILE_CUTOFFS.get(label),
            }
        )
    return rows


def _enrich_tokens(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import (
        SCORE_POINTS_V2,
        _feature_token,
        compute_entry_expectancy_score_fields,
    )

    active: dict[str, bool] = {}
    score = 0
    for token, pts in SCORE_POINTS_V2.items():
        lbl = token.split(":", 1)[0]
        tok = _feature_token(lbl, ev)
        hit = tok == token
        active[token] = hit
        if hit:
            score += pts

    sf = compute_entry_expectancy_score_fields(trade=ev)
    logged_v2 = _int(sf.get("entry_expectancy_score_v2"))
    if logged_v2 is not None and logged_v2 != score:
        mismatch = True
    else:
        mismatch = False

    pattern = "+".join(sorted(t for t, on in active.items() if on)) or "(none)"
    return {
        "active_tokens": active,
        "entry_score_v2": score,
        "entry_score_v2_ge5": score >= V2_GE5,
        "pattern": pattern,
        "score_mismatch": mismatch,
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
                    "origin": "structural_trades",
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
            "origin": "accepted_exit",
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
            "origin": "reject_replay",
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
    outcome_origin: str = ""


def _metrics_from_rows(rows: list[ObsRow]) -> dict[str, Any]:
    closed = [r for r in rows if r.pnl_pct is not None]
    if not closed:
        return {
            "outcome_count": 0,
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
        "outcome_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "win_rate": round(wins / n, 4),
        "avg_mfe_pct": round(sum(float(r.mfe_pct or 0) for r in closed) / n, 4),
        "avg_mae_pct": round(sum(float(r.mae_pct or 0) for r in closed) / n, 4),
    }


def _score_band_label(score: int) -> str:
    if score >= 6:
        return "score6_plus"
    return f"score{score}"


def _counterfactual_score(active: dict[str, bool], exclude_token: str) -> int:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    total = 0
    for token, pts in SCORE_POINTS_V2.items():
        if token == exclude_token:
            continue
        if active.get(token):
            total += pts
    return total


def _discover_sessions() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not SMALL_PAPER.is_dir():
        return found
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day):
            continue
        if not _load_events(summary_path.parent):
            continue
        summary = _read_summary(summary_path.parent)
        found.append(
            {
                "session_id": sid,
                "day": day,
                "stream": _session_stream(sid, summary),
                "session_dir": str(summary_path.parent),
            }
        )
    return found


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p289", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    sessions = _discover_sessions()
    appearance_rows: list[ObsRow] = []
    outcome_rows: list[ObsRow] = []
    score_mismatch_count = 0

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
            if tok["score_mismatch"]:
                score_mismatch_count += 1
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
                            outcome_origin=str(fin.get("origin") or ""),
                        )
                    )
            else:
                reject_keys.add((sym, ent))

        if reject_keys:
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
                        outcome_origin=str(fin.get("origin") or ""),
                    )
                )

    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    tokens = list(SCORE_POINTS_V2.keys())
    factor_rows: list[dict[str, Any]] = []
    for token in tokens:
        app = [r for r in appearance_rows if r.active_tokens.get(token)]
        acc = [r for r in app if r.event_type == "accepted"]
        out_active = [r for r in outcome_rows if r.active_tokens.get(token)]
        m = _metrics_from_rows(out_active)
        pf = m.get("profit_factor")
        factor_rows.append(
            {
                "token": token,
                "points": SCORE_POINTS_V2[token],
                "trigger_condition": _trigger_condition(token),
                "appearance_count": len(app),
                "accepted_count": len(acc),
                "outcome_count": m["outcome_count"],
                "profit_factor": pf,
                "total_pnl_pct": m["total_pnl_pct"],
                "avg_pnl_pct": m["avg_pnl_pct"],
                "win_rate": m["win_rate"],
                "avg_mfe_pct": m["avg_mfe_pct"],
                "avg_mae_pct": m["avg_mae_pct"],
                "classification": _classify_pf(pf if isinstance(pf, (int, float)) else None),
            }
        )

    pattern_counts: Counter[str] = Counter(r.pattern for r in appearance_rows)
    pattern_outcomes: dict[str, list[ObsRow]] = defaultdict(list)
    for r in outcome_rows:
        pattern_outcomes[r.pattern].append(r)

    top_patterns: list[dict[str, Any]] = []
    for pattern, cnt in pattern_counts.most_common(20):
        rows = pattern_outcomes.get(pattern, [])
        m = _metrics_from_rows(rows)
        active_tokens = [t for t in tokens if t in pattern.split("+")] if pattern != "(none)" else []
        top_patterns.append(
            {
                "pattern": pattern,
                "active_tokens": active_tokens,
                "appearance_count": cnt,
                "accepted_count": sum(1 for r in appearance_rows if r.pattern == pattern and r.event_type == "accepted"),
                **m,
            }
        )

    score_bands: dict[str, dict[str, Any]] = {}
    for band in [f"score{i}" for i in range(6)] + ["score6_plus"]:
        if band == "score6_plus":
            rows = [r for r in outcome_rows if r.entry_score_v2 >= 6]
        else:
            target = int(band.replace("score", ""))
            rows = [r for r in outcome_rows if r.entry_score_v2 == target]
        m = _metrics_from_rows(rows)
        score_bands[band] = {
            "appearance_count": sum(
                1
                for r in appearance_rows
                if (r.entry_score_v2 >= 6 if band == "score6_plus" else r.entry_score_v2 == int(band.replace("score", "")))
            ),
            "accepted_count": sum(
                1
                for r in appearance_rows
                if r.event_type == "accepted"
                and (r.entry_score_v2 >= 6 if band == "score6_plus" else r.entry_score_v2 == int(band.replace("score", "")))
            ),
            "profit_factor": m["profit_factor"],
            "total_pnl_pct": m["total_pnl_pct"],
            "win_rate": m["win_rate"],
            "outcome_count": m["outcome_count"],
            "avg_pnl_pct": m["avg_pnl_pct"],
        }

    baseline_v2_ge5 = [r for r in outcome_rows if r.entry_score_v2 >= V2_GE5]
    baseline_metrics = _metrics_from_rows(baseline_v2_ge5)

    counterfactual_rows: list[dict[str, Any]] = []
    for token in tokens:
        cf_rows = [
            r
            for r in outcome_rows
            if _counterfactual_score(r.active_tokens, token) >= V2_GE5
        ]
        cf_m = _metrics_from_rows(cf_rows)
        b_pf = baseline_metrics.get("profit_factor")
        c_pf = cf_m.get("profit_factor")
        delta_pf = (
            round(float(c_pf) - float(b_pf), 4)
            if b_pf is not None and c_pf is not None
            else None
        )
        delta_pnl = round(
            float(cf_m.get("total_pnl_pct") or 0) - float(baseline_metrics.get("total_pnl_pct") or 0),
            4,
        )
        dropped = [
            r
            for r in baseline_v2_ge5
            if _counterfactual_score(r.active_tokens, token) < V2_GE5
        ]
        counterfactual_rows.append(
            {
                "excluded_token": token,
                "baseline_v2_ge5_outcome_count": baseline_metrics["outcome_count"],
                "counterfactual_v2_ge5_outcome_count": cf_m["outcome_count"],
                "trades_dropped_from_v2_ge5": len(dropped),
                "baseline_profit_factor": b_pf,
                "counterfactual_profit_factor": c_pf,
                "delta_profit_factor": delta_pf,
                "baseline_total_pnl_pct": baseline_metrics["total_pnl_pct"],
                "counterfactual_total_pnl_pct": cf_m["total_pnl_pct"],
                "delta_total_pnl_pct": delta_pnl,
                "dropped_trade_avg_pnl_pct": (
                    round(sum(float(r.pnl_pct or 0) for r in dropped) / len(dropped), 6) if dropped else None
                ),
            }
        )

    classification_summary = {
        "effective": [r["token"] for r in factor_rows if r["classification"] == "effective"],
        "neutral": [r["token"] for r in factor_rows if r["classification"] == "neutral"],
        "suspicious": [r["token"] for r in factor_rows if r["classification"] == "suspicious"],
        "insufficient_data": [r["token"] for r in factor_rows if r["classification"] == "insufficient_data"],
    }

    by_stream = {
        stream: {
            "appearance_count": sum(1 for r in appearance_rows if r.stream == stream),
            "accepted_count": sum(1 for r in appearance_rows if r.stream == stream and r.event_type == "accepted"),
            "outcome_count": sum(1 for r in outcome_rows if r.stream == stream),
            "score5_appearance_count": sum(
                1 for r in appearance_rows if r.stream == stream and r.entry_score_v2 >= V2_GE5
            ),
            "metrics_v2_ge5": _metrics_from_rows([r for r in outcome_rows if r.stream == stream and r.entry_score_v2 >= V2_GE5]),
        }
        for stream in sorted({r.stream for r in appearance_rows})
    }

    report = {
        "phase": 289,
        "mode": "entry_score_v2_factor_attribution",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "production_logic_changes_forbidden": True,
            "score_source": "SCORE_POINTS_V2 (Phase236 Scenario B; RollingMAE:mid excluded)",
        },
        "date_range": {"start": DATE_START, "end": DATE_END, "label": "20260518-20260605"},
        "classification_thresholds": {
            "effective": f"PF > {PF_EFFECTIVE}",
            "neutral": f"{PF_SUSPICIOUS} <= PF <= {PF_EFFECTIVE}",
            "suspicious": f"PF < {PF_SUSPICIOUS}",
        },
        "method": {
            "decision_pool": "accepted + rejected excluding hard structural excludes",
            "appearance_count": "decision_pool events where token active",
            "accepted_count": "accepted events where token active",
            "outcome_metrics": "accepted realized + rejected counterfactual replay (phase71)",
            "counterfactual": "recompute v2 without token; compare v2>=5 cohort PF/PnL",
        },
        "score_elements": _enumerate_score_elements(),
        "sessions": {
            "count": len(sessions),
            "by_stream": Counter(s["stream"] for s in sessions),
            "ids": [s["session_id"] for s in sessions],
        },
        "population": {
            "appearance_count": len(appearance_rows),
            "accepted_count": sum(1 for r in appearance_rows if r.event_type == "accepted"),
            "outcome_count": len(outcome_rows),
            "score_recompute_mismatch_count": score_mismatch_count,
            "v2_ge5_appearance_count": sum(1 for r in appearance_rows if r.entry_score_v2 >= V2_GE5),
            "v2_ge5_accepted_count": sum(
                1 for r in appearance_rows if r.event_type == "accepted" and r.entry_score_v2 >= V2_GE5
            ),
            "v2_ge5_outcome_count": len(baseline_v2_ge5),
            "baseline_v2_ge5_metrics": baseline_metrics,
        },
        "per_factor": factor_rows,
        "top_20_patterns": top_patterns,
        "score_bands": score_bands,
        "counterfactual_exclusion": {
            "baseline_v2_ge5": baseline_metrics,
            "per_token": counterfactual_rows,
        },
        "verdict": classification_summary,
        "by_stream": by_stream,
    }

    def _json_default(val: Any) -> Any:
        if val == float("inf"):
            return "inf"
        if val == float("-inf"):
            return "-inf"
        raise TypeError(f"not serializable: {type(val)!r}")

    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}", flush=True)
    print(
        f"sessions={len(sessions)} appearance={len(appearance_rows)} "
        f"outcomes={len(outcome_rows)} v2_ge5_outcomes={len(baseline_v2_ge5)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
