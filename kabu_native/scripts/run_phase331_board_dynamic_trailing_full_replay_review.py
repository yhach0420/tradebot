#!/usr/bin/env python3
"""
Phase331: Board-dynamic trailing generalization on Phase318 replay cohort (43 trades).

Output: phase331_board_dynamic_trailing_full_replay_review.json
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase331_board_dynamic_trailing_full_replay_review.json"
PHASE318_CK = REPO / "kabu_native/results/reports/phase318_current_production_logic_replay.checkpoint.json"

DATE_START = 20260518
DATE_END = 20260603
JST = ZoneInfo("Asia/Tokyo")

HARD_STOP_PCT = 1.20
BOARD_SPLIT_PCT = 47.62

SCENARIOS = {
    "A": {"label": "current_fixed", "activate_pct": 0.80, "giveback_frac": 0.50, "board_dynamic": False},
    "B": {
        "label": "board_dynamic",
        "board_dynamic": True,
        "board_high": {"activate_pct": 1.00, "giveback_frac": 0.60},
        "board_low": {"activate_pct": 0.60, "giveback_frac": 0.40},
    },
}


@dataclass
class PathTrade:
    symbol: str
    day: str
    session_id: str
    stream: str
    entry_time: str
    entry_price: float
    entry_imbalance_percentile: Optional[float]
    board_tier: str
    prices: list[float]


@dataclass
class ExitTrade:
    symbol: str
    day: str
    session_id: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_yen_100: float
    exit_reason: str
    board_tier: str
    activate_pct_used: float
    giveback_frac_used: float


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _load_p318() -> Any:
    path = REPO / "kabu_native/scripts/run_phase318_current_production_logic_replay.py"
    spec = importlib.util.spec_from_file_location("phase318_p331", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase318_p331"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_p71() -> Any:
    path = REPO / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    spec = importlib.util.spec_from_file_location("phase71_p331", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p331"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _board_tier(imb_pct: Optional[float]) -> str:
    if imb_pct is None:
        return "board_low"
    return "board_high" if imb_pct >= BOARD_SPLIT_PCT else "board_low"


def _trailing_params(trade: PathTrade, scenario: dict[str, Any]) -> tuple[float, float]:
    if not scenario.get("board_dynamic"):
        return float(scenario["activate_pct"]), float(scenario["giveback_frac"])
    block = scenario["board_high"] if trade.board_tier == "board_high" else scenario["board_low"]
    return float(block["activate_pct"]), float(block["giveback_frac"])


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _session_imbalance_percentiles(events: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    """Map (symbol, entry_time) -> session percentile from entry_order_book_imbalance."""
    rows: list[tuple[str, str, float]] = []
    for ev in events:
        if str(ev.get("event_type") or "") != "accepted":
            continue
        imb = _float(ev.get("entry_order_book_imbalance"))
        if imb is None:
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        rows.append((sym, ent, imb))
    if not rows:
        return {}
    values = sorted(r[2] for r in rows)
    n = len(values)
    out: dict[tuple[str, str], float] = {}
    for sym, ent, imb in rows:
        le = sum(1 for v in values if v <= imb)
        out[(sym, ent)] = round(100.0 * le / n, 2)
    return out


def _load_checkpoint() -> list[dict[str, Any]]:
    if not PHASE318_CK.is_file():
        return []
    return list(json.loads(PHASE318_CK.read_text(encoding="utf-8")).get("trades") or [])


def _price_match(a: float, b: float) -> bool:
    tol = max(0.05, abs(a) * 0.0005)
    return abs(a - b) <= tol


def _build_path(
    *,
    day: str,
    symbol: str,
    entry_price: float,
    session_id: str,
    stream: str,
    events: list[dict[str, Any]],
    session_end_ts: float,
    imb_pct_map: dict[tuple[str, str], float],
    used_keys: set[tuple[str, str, float, str]],
) -> Optional[PathTrade]:
    entry_time = ""
    entry_ts = 0.0
    imb_pct: Optional[float] = None
    for ev in events:
        if str(ev.get("event_type") or "") != "accepted":
            continue
        if str(ev.get("symbol") or "") != symbol:
            continue
        px = _float(ev.get("current_price")) or _float(ev.get("entry_price")) or 0.0
        if not _price_match(px, entry_price):
            continue
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        key = (day, symbol, round(entry_price, 4), ent)
        if key in used_keys:
            continue
        entry_time = ent
        entry_ts = _parse_ts(ent)
        used_keys.add(key)
        imb_pct = _float(ev.get("entry_imbalance_percentile"))
        if imb_pct is None:
            imb_pct = imb_pct_map.get((symbol, ent))
        break
    if not entry_time:
        return None

    prices = [float(entry_price)]
    for ev in events:
        if str(ev.get("event_type") or "") != "candidate":
            continue
        if str(ev.get("symbol") or "") != symbol:
            continue
        ts = _parse_ts(str(ev.get("entry_time") or ev.get("event_time") or ""))
        if ts < entry_ts or ts > session_end_ts:
            continue
        px = _float(ev.get("current_price"))
        if px is not None and px > 0:
            prices.append(float(px))
    if len(prices) < 2:
        prices.append(prices[-1])

    return PathTrade(
        symbol=symbol,
        day=day,
        session_id=session_id,
        stream=stream,
        entry_time=entry_time,
        entry_price=float(entry_price),
        entry_imbalance_percentile=imb_pct,
        board_tier=_board_tier(imb_pct),
        prices=prices,
    )


def _collect_paths(
    p270: Any,
    p318: Any,
    p71: Any,
    checkpoint: list[dict[str, Any]],
) -> tuple[list[PathTrade], list[dict[str, Any]], list[dict[str, Any]]]:
    sessions, skipped = p318._discover_sessions(p270)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sessions:
        by_day[s["day"]].append(s)

    event_cache: dict[str, list[dict[str, Any]]] = {}
    end_cache: dict[str, float] = {}
    stream_cache: dict[str, str] = {}
    imb_cache: dict[str, dict[tuple[str, str], float]] = {}
    used: set[tuple[str, str, float, str]] = set()
    paths: list[PathTrade] = []
    unmatched: list[dict[str, Any]] = []

    for day in sorted({str(t.get("day") or "") for t in checkpoint}):
        for meta in by_day.get(day, []):
            sid = meta["session_id"]
            if sid in event_cache:
                continue
            events = p270._load_events(SMALL_PAPER / sid)
            event_cache[sid] = events
            end_cache[sid] = _parse_ts(p71._session_end(events))
            imb_cache[sid] = _session_imbalance_percentiles(events)
            try:
                summary = json.loads(
                    (SMALL_PAPER / sid / "small_paper_summary.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                summary = {}
            stream_cache[sid] = p270._session_stream(sid, summary)

    for t in checkpoint:
        day = str(t.get("day") or "")
        sym = str(t.get("symbol") or "")
        entry_px = float(t.get("entry_price") or 0.0)
        found: Optional[PathTrade] = None
        for meta in by_day.get(day, []):
            sid = meta["session_id"]
            events = event_cache.get(sid, [])
            if not events:
                continue
            path = _build_path(
                day=day,
                symbol=sym,
                entry_price=entry_px,
                session_id=sid,
                stream=stream_cache.get(sid, "unknown"),
                events=events,
                session_end_ts=end_cache.get(sid, 0.0),
                imb_pct_map=imb_cache.get(sid, {}),
                used_keys=used,
            )
            if path is not None:
                found = path
                break
        if found is None:
            unmatched.append(t)
        else:
            paths.append(found)

    return paths, unmatched, skipped


def _simulate(path: PathTrade, scenario: dict[str, Any]) -> ExitTrade:
    from replay.pnl_yen import compute_pnl_yen_100

    activate, giveback = _trailing_params(path, scenario)
    entry = path.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    peak = 0.0
    for px in path.prices:
        pnl = _pnl_pct(entry, px)
        peak = max(peak, pnl)
        if px <= stop:
            return ExitTrade(
                path.symbol,
                path.day,
                path.session_id,
                entry,
                px,
                pnl,
                compute_pnl_yen_100(entry, px),
                "stop_hit",
                path.board_tier,
                activate,
                giveback,
            )
        if peak >= activate and pnl <= peak * giveback:
            return ExitTrade(
                path.symbol,
                path.day,
                path.session_id,
                entry,
                px,
                pnl,
                compute_pnl_yen_100(entry, px),
                "trailing_mfe_exit",
                path.board_tier,
                activate,
                giveback,
            )
    exit_px = float(path.prices[-1])
    pnl = _pnl_pct(entry, exit_px)
    return ExitTrade(
        path.symbol,
        path.day,
        path.session_id,
        entry,
        exit_px,
        pnl,
        compute_pnl_yen_100(entry, exit_px),
        "session_close",
        path.board_tier,
        activate,
        giveback,
    )


def _pf(yens: list[float]) -> Optional[float]:
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return round(sum(wins) / gl, 4)


def _summarize(
    exits: list[ExitTrade],
    *,
    baseline: Optional[list[ExitTrade]] = None,
) -> dict[str, Any]:
    yens = [t.pnl_yen_100 for t in exits]
    pnls = [t.pnl_pct for t in exits]
    ec = Counter(t.exit_reason for t in exits)
    base_map = {(t.day, t.symbol, t.entry_price): t for t in baseline} if baseline else {}

    stop_red = None
    if baseline:
        stop_red = int(Counter(b.exit_reason for b in baseline).get("stop_hit", 0)) - int(
            ec.get("stop_hit", 0)
        )

    premature = premature_yen = 0
    trailing_up = trailing_up_yen = 0.0
    for t in exits:
        b = base_map.get((t.day, t.symbol, t.entry_price))
        if not b:
            continue
        d = t.pnl_yen_100 - b.pnl_yen_100
        if d < -0.01 and t.exit_reason == "trailing_mfe_exit":
            premature += 1
            premature_yen += d
        if d > 0.01:
            trailing_up += 1
            trailing_up_yen += d

    return {
        "trade_count": len(exits),
        "total_pnl_yen_100": round(sum(yens), 2),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(statistics.mean(pnls), 6) if pnls else None,
        "profit_factor_yen_100": _pf(yens),
        "win_rate": round(sum(1 for y in yens if y > 0) / len(yens), 4) if yens else None,
        "exit_reason_counts": dict(sorted(ec.items())),
        "stop_hit_count": int(ec.get("stop_hit", 0)),
        "trailing_mfe_exit_count": int(ec.get("trailing_mfe_exit", 0)),
        "session_close_count": int(ec.get("session_close", 0)),
        "stop_hit_reduction_vs_A": stop_red,
        "premature_profit_taking_count": premature,
        "premature_profit_taking_yen_100": round(premature_yen, 2),
        "trailing_profit_increased_count": trailing_up,
        "trailing_profit_increased_yen_100": round(trailing_up_yen, 2),
    }


def _group_pnl(exits: list[ExitTrade], key_fn: Any) -> list[dict[str, Any]]:
    g: dict[str, list[ExitTrade]] = defaultdict(list)
    for t in exits:
        g[str(key_fn(t))].append(t)
    return [
        {
            "key": k,
            "trade_count": len(v),
            "total_pnl_yen_100": round(sum(x.pnl_yen_100 for x in v), 2),
            "avg_pnl_yen_100": round(statistics.mean(x.pnl_yen_100 for x in v), 2),
        }
        for k, v in sorted(g.items())
    ]


def _concentration(exits_a: list[ExitTrade], exits_b: list[ExitTrade]) -> dict[str, Any]:
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for a, b in zip(exits_a, exits_b):
        d = b.pnl_yen_100 - a.pnl_yen_100
        by_day[a.day] += d
        by_sym[a.symbol] += d
    total = sum(by_day.values())
    if abs(total) < 1e-6:
        return {"single_day_dominance": None, "single_symbol_dominance": None}
    top_day = max(by_day.items(), key=lambda kv: abs(kv[1]))
    top_sym = max(by_sym.items(), key=lambda kv: abs(kv[1]))
    return {
        "improvement_by_day": dict(sorted(by_day.items())),
        "improvement_by_symbol_top15": dict(
            sorted(by_sym.items(), key=lambda kv: -abs(kv[1]))[:15]
        ),
        "top_day_share": round(top_day[1] / total, 4),
        "top_symbol_share": round(top_sym[1] / total, 4),
        "single_day_dominance": abs(top_day[1] / total) > 0.8,
        "single_symbol_dominance": abs(top_sym[1] / total) > 0.5,
    }


def _verdict(ma: dict[str, Any], mb: dict[str, Any], conc: dict[str, Any]) -> dict[str, Any]:
    yen_up = float(mb["total_pnl_yen_100"]) > float(ma["total_pnl_yen_100"])
    pf_a, pf_b = ma.get("profit_factor_yen_100"), mb.get("profit_factor_yen_100")
    pf_up = pf_a is not None and pf_b is not None and float(pf_b) > float(pf_a)
    stop_ok = int(mb["stop_hit_count"]) <= int(ma["stop_hit_count"])
    not_dom = not conc.get("single_day_dominance") and not conc.get("single_symbol_dominance")
    generalizes = yen_up and pf_up and stop_ok and not_dom
    delta = float(mb["total_pnl_yen_100"]) - float(ma["total_pnl_yen_100"])
    return {
        "board_dynamic_trailing_generalizes": generalizes,
        "criteria": {
            "total_pnl_yen_100_improved": yen_up,
            "profit_factor_improved": pf_up,
            "stop_hit_not_increased": stop_ok,
            "not_single_day_or_symbol_dominant": not_dom,
        },
        "delta_vs_A_yen": round(delta, 2),
        "conclusion": (
            "Board-dynamic trailing generalizes on Phase318 replay cohort"
            if generalizes
            else f"Board-dynamic trailing does not generalize (delta {delta:+.0f} yen vs A)"
        ),
    }


def main() -> int:
    _bootstrap()
    p270 = _bootstrap()
    p318 = _load_p318()
    p71 = _load_p71()
    checkpoint = _load_checkpoint()
    if not checkpoint:
        print("missing checkpoint", file=sys.stderr)
        return 1

    paths, unmatched, skipped = _collect_paths(p270, p318, p71, checkpoint)
    exits_a = [_simulate(p, SCENARIOS["A"]) for p in paths]
    exits_b = [_simulate(p, SCENARIOS["B"]) for p in paths]

    ma = _summarize(exits_a)
    mb = _summarize(exits_b, baseline=exits_a)
    mb["vs_scenario_A_improvement_yen"] = round(
        float(mb["total_pnl_yen_100"]) - float(ma["total_pnl_yen_100"]), 2
    )
    conc = _concentration(exits_a, exits_b)

    report = {
        "phase": 331,
        "title": "board_dynamic_trailing_full_replay_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; trailing board adjustment only",
        "replay_window": {"start": DATE_START, "end": DATE_END},
        "entry_logic": "Phase318 (Momentum:low + Board:mid, score_min=3)",
        "board_split": {"threshold_percentile": BOARD_SPLIT_PCT},
        "scenarios": SCENARIOS,
        "phase318_checkpoint": {
            "trade_count": len(checkpoint),
            "matched_path_count": len(paths),
            "unmatched_count": len(unmatched),
        },
        "sessions_skipped": skipped,
        "scenario_A": ma,
        "scenario_B": mb,
        "breakdown": {
            "daily_pnl_A": _group_pnl(exits_a, lambda t: t.day),
            "daily_pnl_B": _group_pnl(exits_b, lambda t: t.day),
            "daily_delta_B_minus_A": _group_pnl(
                [
                    ExitTrade(
                        a.symbol,
                        a.day,
                        a.session_id,
                        a.entry_price,
                        a.exit_price,
                        a.pnl_pct,
                        b.pnl_yen_100 - a.pnl_yen_100,
                        "delta",
                        a.board_tier,
                        b.activate_pct_used,
                        b.giveback_frac_used,
                    )
                    for a, b in zip(exits_a, exits_b)
                ],
                lambda t: t.day,
            ),
            "symbol_pnl_A": _group_pnl(exits_a, lambda t: t.symbol),
            "symbol_pnl_B": _group_pnl(exits_b, lambda t: t.symbol),
            "board_tier_A": _group_pnl(exits_a, lambda t: t.board_tier),
            "board_tier_B": _group_pnl(exits_b, lambda t: t.board_tier),
        },
        "concentration": conc,
        "verdict": _verdict(ma, mb, conc),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"paths={len(paths)} A={ma['total_pnl_yen_100']} B={mb['total_pnl_yen_100']} "
        f"generalizes={report['verdict']['board_dynamic_trailing_generalizes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
