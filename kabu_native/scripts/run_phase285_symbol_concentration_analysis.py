#!/usr/bin/env python3
"""
Phase285: Symbol concentration analysis for Phase284 replay trades (20260603).

Output: kabu_native/results/reports/phase285_symbol_concentration_analysis.json
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_REPO = Path(__file__).resolve().parents[2]
_OUT = _REPO / "kabu_native/results/reports/phase285_symbol_concentration_analysis.json"
_MIN_CLOSE = 300.0
_V2_MIN = 5
_FOCUS_SYM = "3110.T"


def _bootstrap() -> None:
    native = _REPO / "kabu_native"
    for p in (native / "src", _REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _norm_sym(sym: str) -> str:
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if "." not in s and s.isdigit():
        return f"{s}.T"
    return s


def _int_score(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _symbols_close_ge_300(push_dir: Path, features_csv: Path) -> set[str]:
    push_syms = {_norm_sym(p.stem) for p in push_dir.glob("*.jsonl")}
    if not features_csv.is_file():
        return push_syms
    close_by: dict[str, float] = {}
    with features_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_sym(row.get("symbol") or "")
            if not sym:
                continue
            try:
                close_by[sym] = float(row.get("close") or 0)
            except (TypeError, ValueError):
                continue
    ok = {s for s in push_syms if close_by.get(s, 0) >= _MIN_CLOSE}
    return ok or push_syms


def _patch_push_loader(allowed: set[str], windows: list[Any]) -> Any:
    import json as _json

    import small_paper.pilot_runner as pr
    from small_paper.allowed_trading_windows import is_in_allowed_trading_window

    orig = pr._load_push_replay_records

    def _filtered(push_dir: Path, *, max_rows: Optional[int] = None) -> list[tuple[str, str, dict]]:
        rows: list[tuple[str, str, dict]] = []
        for fp in sorted(push_dir.glob("*.jsonl")):
            sym = _norm_sym(fp.stem)
            if sym not in allowed:
                continue
            with fp.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    src = str(rec.get("source") or "")
                    if src and src not in ("live_push", "push", "dry_run"):
                        continue
                    payload = rec.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    recorded_at = str(rec.get("recorded_at") or "")
                    if not is_in_allowed_trading_window(recorded_at, windows):
                        continue
                    rows.append((recorded_at, sym, payload))
                    if max_rows is not None and len(rows) >= max_rows:
                        return sorted(rows, key=lambda r: r[0])
        return sorted(rows, key=lambda r: r[0])

    pr._load_push_replay_records = _filtered  # type: ignore[assignment]
    return orig


def _restore_push_loader(orig: Any) -> None:
    import small_paper.pilot_runner as pr

    pr._load_push_replay_records = orig  # type: ignore[assignment]


def _load_events_from_session(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return []


def _accepted_v2_index(events: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    idx: dict[tuple[str, str], int] = {}
    for e in events:
        if str(e.get("event_type") or "") != "accepted":
            continue
        sym = _norm_sym(str(e.get("symbol") or ""))
        et = str(e.get("entry_time") or e.get("event_time") or "")
        v2 = _int_score(e.get("entry_expectancy_score_v2"))
        if sym and et and v2 is not None:
            idx[(sym, et)] = v2
    return idx


def _extract_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structural observer exits = completed trades."""
    v2_idx = _accepted_v2_index(events)
    trades: list[dict[str, Any]] = []
    for e in events:
        if str(e.get("event_type") or "") != "observer_exit":
            continue
        pnl = e.get("pnl_pct")
        if pnl is None or pnl == "":
            continue
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            continue
        sym = _norm_sym(str(e.get("symbol") or ""))
        et = str(e.get("entry_time") or "")
        v2 = _int_score(e.get("entry_expectancy_score_v2")) or v2_idx.get((sym, et))
        trades.append(
            {
                "symbol": sym,
                "entry_time": str(e.get("entry_time") or ""),
                "exit_time": str(e.get("exit_time") or e.get("event_time") or ""),
                "pnl_pct": round(pnl_f, 4),
                "win": pnl_f > 0,
                "entry_score_v2": v2,
                "exit_reason": str(e.get("exit_reason") or e.get("structural_exit_reason") or ""),
                "hold_sec": e.get("hold_sec"),
            }
        )
    return trades


def _symbol_trade_stats(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[t["symbol"]].append(t)
    rows: list[dict[str, Any]] = []
    for sym, ts in by.items():
        pnls = [float(t["pnl_pct"]) for t in ts]
        wins = sum(1 for p in pnls if p > 0)
        v2s = [t["entry_score_v2"] for t in ts if t.get("entry_score_v2") is not None]
        rows.append(
            {
                "symbol": sym,
                "trade_count": len(ts),
                "total_pnl_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else 0.0,
                "win_rate": round(wins / len(ts), 4) if ts else 0.0,
                "win_count": wins,
                "loss_count": len(ts) - wins,
                "entry_score_v2_min": min(v2s) if v2s else None,
                "entry_score_v2_max": max(v2s) if v2s else None,
                "entry_score_v2_values": sorted(set(v2s)),
            }
        )
    rows.sort(key=lambda r: (-r["trade_count"], -r["total_pnl_pct"]))
    return rows


def _gate_stats_by_symbol(
    events: list[dict[str, Any]],
    universe: set[str],
) -> dict[str, dict[str, Any]]:
    """Per-symbol gate evaluation stats from replay events."""
    stats: dict[str, dict[str, Any]] = {
        s: {
            "symbol": s,
            "candidate_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "observer_exit_count": 0,
            "reject_reason_counts": Counter(),
            "max_entry_score_v2_seen": None,
            "max_v2_on_reject": None,
            "reject_v2_histogram": Counter(),
            "ever_v2_ge5": False,
            "ever_accepted": False,
        }
        for s in sorted(universe)
    }

    for e in events:
        sym = _norm_sym(str(e.get("symbol") or ""))
        if sym not in stats:
            continue
        st = stats[sym]
        et = str(e.get("event_type") or "")
        v2 = _int_score(e.get("entry_expectancy_score_v2"))
        if v2 is not None:
            prev = st["max_entry_score_v2_seen"]
            st["max_entry_score_v2_seen"] = v2 if prev is None else max(prev, v2)
            if v2 >= _V2_MIN:
                st["ever_v2_ge5"] = True
        if et == "candidate":
            st["candidate_count"] += 1
        elif et == "accepted":
            st["accepted_count"] += 1
            st["ever_accepted"] = True
        elif et == "rejected":
            st["rejected_count"] += 1
            reason = str(e.get("gate_reject_reason") or "unknown")
            st["reject_reason_counts"][reason] += 1
            if v2 is not None:
                prev_r = st["max_v2_on_reject"]
                st["max_v2_on_reject"] = v2 if prev_r is None else max(prev_r, v2)
                st["reject_v2_histogram"][str(v2)] += 1
        elif et == "observer_exit":
            st["observer_exit_count"] += 1

    for st in stats.values():
        st["reject_reason_counts"] = dict(st["reject_reason_counts"])
        st["reject_v2_histogram"] = dict(sorted(st["reject_v2_histogram"].items(), key=lambda x: int(x[0])))
    return stats


def _v2_distribution_global(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Score distribution on rejected evaluations (gate touch)."""
    hist = Counter()
    ge5 = 0
    below5 = 0
    for e in events:
        if str(e.get("event_type") or "") != "rejected":
            continue
        v2 = _int_score(e.get("entry_expectancy_score_v2"))
        if v2 is None:
            continue
        hist[str(v2)] += 1
        if v2 >= _V2_MIN:
            ge5 += 1
        else:
            below5 += 1
    return {
        "rejected_with_v2_count": ge5 + below5,
        "rejected_v2_below_5": below5,
        "rejected_v2_ge5": ge5,
        "rejected_v2_histogram": dict(sorted(hist.items(), key=lambda x: int(x[0]))),
    }


def _why_not_adopted(
    *,
    trades: list[dict[str, Any]],
    gate_by_symbol: dict[str, dict[str, Any]],
    universe: set[str],
    global_v2: dict[str, Any],
) -> dict[str, Any]:
    traded_syms = {t["symbol"] for t in trades}
    non_traded = sorted(universe - traded_syms)
    top_symbol = trades[0]["symbol"] if len({t["symbol"] for t in trades}) == 1 else None
    concentration_pct = (
        round(100.0 * max(Counter(t["symbol"] for t in trades).values()) / len(trades), 2)
        if trades
        else 0.0
    )

    non_traded_rows: list[dict[str, Any]] = []
    for sym in non_traded:
        st = gate_by_symbol.get(sym, {})
        non_traded_rows.append(
            {
                "symbol": sym,
                "candidate_count": st.get("candidate_count", 0),
                "rejected_count": st.get("rejected_count", 0),
                "max_entry_score_v2_seen": st.get("max_entry_score_v2_seen"),
                "max_v2_on_reject": st.get("max_v2_on_reject"),
                "ever_v2_ge5": st.get("ever_v2_ge5", False),
                "ever_accepted": st.get("ever_accepted", False),
                "reject_reason_counts": st.get("reject_reason_counts", {}),
                "primary_blocker": _primary_blocker(st.get("reject_reason_counts", {})),
            }
        )
    non_traded_rows.sort(key=lambda r: (-(r.get("max_entry_score_v2_seen") or -1), -r["candidate_count"]))

    ever_ge5_not_traded = [r for r in non_traded_rows if r.get("ever_v2_ge5")]
    never_ge5 = [r for r in non_traded_rows if not r.get("ever_v2_ge5")]

    focus: Optional[dict[str, Any]] = None
    if top_symbol == _FOCUS_SYM and concentration_pct >= 99.9:
        focus = {
            "symbol": _FOCUS_SYM,
            "concentration_pct": concentration_pct,
            "headline": f"全{len(trades)}トレードが {_FOCUS_SYM} に集中",
            "3110_gate": gate_by_symbol.get(_FOCUS_SYM, {}),
            "other_symbols_count": len(non_traded),
            "other_symbols_never_reached_v2_ge5": len(never_ge5),
            "other_symbols_reached_v2_ge5_but_not_traded": len(ever_ge5_not_traded),
            "top_other_by_max_v2": non_traded_rows[:15],
            "global_reject_v2_distribution": global_v2,
            "interpretation": _interpret_concentration(
                gate_3110=gate_by_symbol.get(_FOCUS_SYM, {}),
                non_traded=non_traded_rows,
                ever_ge5_not_traded=ever_ge5_not_traded,
                never_ge5=never_ge5,
            ),
        }

    return {
        "traded_symbol_count": len(traded_syms),
        "universe_symbol_count": len(universe),
        "non_traded_symbol_count": len(non_traded),
        "concentration_pct_top_symbol": concentration_pct,
        "top_symbol": top_symbol,
        "non_traded_symbols": non_traded_rows,
        "symbols_ever_v2_ge5_but_no_trade": ever_ge5_not_traded,
        "symbols_never_v2_ge5": never_ge5,
        "focus_3110_analysis": focus,
    }


def _primary_blocker(reasons: dict[str, Any]) -> str:
    if not reasons:
        return "no_gate_eval"
    return max(reasons.items(), key=lambda x: x[1])[0]


def _interpret_concentration(
    *,
    gate_3110: dict[str, Any],
    non_traded: list[dict[str, Any]],
    ever_ge5_not_traded: list[dict[str, Any]],
    never_ge5: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"採用27件はすべて {_FOCUS_SYM}。他{len(non_traded)}銘柄は observer_exit まで到達せず。"
    )
    n_ge5 = len(ever_ge5_not_traded)
    n_never = len(never_ge5)
    lines.append(
        f"他銘柄: v2>=5到達 {n_ge5} 銘柄 / 一度もv2<5のみ {n_never} 銘柄（max_v2で判定）。"
    )
    if never_ge5:
        top_block = Counter()
        for r in never_ge5:
            top_block[r.get("primary_blocker") or "unknown"] += 1
        lines.append(
            "v2未達銘柄の主因: "
            + ", ".join(f"{k}={v}" for k, v in top_block.most_common(4))
        )
    if ever_ge5_not_traded:
        block = Counter()
        for r in ever_ge5_not_traded:
            block[r.get("primary_blocker") or "unknown"] += 1
        lines.append(
            "v2>=5到達も未採用の主因: "
            + ", ".join(f"{k}={v}" for k, v in block.most_common(4))
        )
    r3110 = gate_3110.get("reject_reason_counts") or {}
    if r3110:
        lines.append(
            f"{_FOCUS_SYM} 自身のreject内訳(参考): "
            + ", ".join(f"{k}={v}" for k, v in sorted(r3110.items(), key=lambda x: -x[1])[:5])
        )
    return lines


def _silence_discord_posts() -> Any:
    """Observer tracker requires discord_enabled; suppress webhook posts for analysis-only."""
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    orig = SmallPaperDiscordNotifier._post

    def _silent(self, **kwargs: Any) -> bool:
        return False

    SmallPaperDiscordNotifier._post = _silent  # type: ignore[method-assign]
    return orig


def _restore_discord_posts(orig: Any) -> None:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    SmallPaperDiscordNotifier._post = orig  # type: ignore[method-assign]


def _run_replay(
    *,
    day_key: str,
    push_dir: Path,
    config_path: Path,
    max_push_rows: Optional[int],
) -> tuple[list[dict[str, Any]], dict[str, Any], set[str]]:
    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run

    features_csv = _REPO / "kabu_native/results/reports" / f"features_{day_key}.csv"
    allowed = _symbols_close_ge_300(push_dir, features_csv)
    cfg = load_pilot_config(config_path)
    # Observer lifecycle is wired when discord_enabled + discord_observer_only (Phase284 path).
    cfg = replace(cfg, discord_enabled=True, discord_observer_only=True)
    stamp = datetime.now(JST).strftime("%H%M%S")
    out_dir = resolve_output_dir(cfg, repo_root=_REPO, day_key=day_key) / f"phase285_resim_{stamp}"
    orig_loader = _patch_push_loader(allowed, cfg.allowed_windows())
    orig_post = _silence_discord_posts()
    try:
        result = run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=out_dir,
            repo_root=_REPO,
            poll_interval_sec=0.0,
            replay_speed_sec=0.0,
            max_push_rows=max_push_rows,
            enable_discord=True,
        )
    finally:
        _restore_push_loader(orig_loader)
        _restore_discord_posts(orig_post)
    events = list(result.events) if result else []
    summary = dict(result.summary) if result else {}
    summary["output_dir"] = str(out_dir)
    return events, summary, allowed


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase285 symbol concentration analysis")
    parser.add_argument("--day-key", default="20260603")
    parser.add_argument("--session-dir", type=Path, default=None, help="Phase284 replay dir (skip re-sim)")
    parser.add_argument(
        "--config",
        type=Path,
        default=_REPO
        / "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    )
    parser.add_argument(
        "--push-dir",
        type=Path,
        default=_REPO / "kabu_native/data/push_jsonl/2026-06-03",
    )
    parser.add_argument("--max-push-rows", type=int, default=None)
    args = parser.parse_args()

    _bootstrap()

    day_key = args.day_key
    push_dir = args.push_dir if args.push_dir.is_absolute() else _REPO / args.push_dir
    cfg_path = args.config if args.config.is_absolute() else _REPO / args.config
    features_csv = _REPO / "kabu_native/results/reports" / f"features_{day_key}.csv"
    universe = _symbols_close_ge_300(push_dir, features_csv)

    replay_summary: dict[str, Any] = {}
    if args.session_dir and args.session_dir.is_dir():
        events = _load_events_from_session(args.session_dir)
        replay_summary = {"source": "session_dir", "path": str(args.session_dir)}
    else:
        events, replay_summary, universe = _run_replay(
            day_key=day_key,
            push_dir=push_dir,
            config_path=cfg_path,
            max_push_rows=args.max_push_rows,
        )

    trades = _extract_trades(events)
    by_symbol = _symbol_trade_stats(trades)
    gate_by_symbol = _gate_stats_by_symbol(events, universe)
    global_v2 = _v2_distribution_global(events)
    adoption = _why_not_adopted(
        trades=trades,
        gate_by_symbol=gate_by_symbol,
        universe=universe,
        global_v2=global_v2,
    )

    total_trades = len(trades)
    sym_counts = Counter(t["symbol"] for t in trades)
    top_sym, top_n = sym_counts.most_common(1)[0] if sym_counts else ("", 0)
    concentration = round(100.0 * top_n / total_trades, 2) if total_trades else 0.0

    report = {
        "phase": 285,
        "title": "Phase284 trade symbol concentration analysis",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source": {
            "day_key": day_key,
            "phase284_equivalent_config": str(cfg_path),
            "replay_summary": replay_summary,
            "universe_symbols_after_close_filter": len(universe),
        },
        "trade_summary": {
            "total_trades": total_trades,
            "total_pnl_pct": round(sum(t["pnl_pct"] for t in trades), 4),
            "overall_win_rate": round(sum(1 for t in trades if t["win"]) / total_trades, 4)
            if total_trades
            else 0.0,
            "unique_symbols_traded": len(sym_counts),
            "top_symbol": top_sym,
            "top_symbol_trade_share_pct": concentration,
            "is_single_symbol_100pct": concentration >= 99.9 and total_trades > 0,
        },
        "by_symbol": by_symbol,
        "trades": trades,
        "global_reject_score_distribution": global_v2,
        "non_adoption_analysis": adoption,
        "constraints": {"production_logic_changed": False, "analysis_only": True},
    }

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {_OUT}")
    print(
        f"trades={total_trades} symbols={len(sym_counts)} "
        f"top={top_sym} share={concentration}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
