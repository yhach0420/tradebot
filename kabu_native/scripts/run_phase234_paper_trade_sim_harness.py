#!/usr/bin/env python3
"""
Phase234: Paper-trade simulation harness (review only).

Replay push_jsonl through the live-equivalent small_paper pipeline
(candidate → quality → accept → observer_exit) with Phase230 score shadow.
No post-hoc CSV aggregation; entry-time fields only at accept.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "kabu_native"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
OUT = NATIVE / "results/reports/phase234_paper_trade_sim_harness_summary.json"
OUT_BASE = NATIVE / "results/small_paper/phase234"
DEFAULT_CONFIG = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_low_liquidity_shadow.yaml"
)

FLAG_GE5 = "entry_expectancy_score_ge5_flag"
FLAG_GE6 = "entry_expectancy_score_ge6_flag"


def _bootstrap() -> None:
    for p in (NATIVE / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel_path: str) -> Any:
    import importlib.util

    path = REPO / rel_path
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


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _discover_push_days() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not PUSH_ROOT.is_dir():
        return out
    for child in sorted(PUSH_ROOT.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("."):
            continue
        if not any(child.glob("*.jsonl")):
            continue
        day_stamp = name.replace("-", "")
        out.append(
            {
                "day_stamp": day_stamp,
                "day_dir": name,
                "push_dir": str(child),
            }
        )
    return out


def _day_split(day_stamp: str, mod: Any) -> str:
    """Map push_jsonl day replay to IS/OOS using phase213c session lists."""
    live_is = [s for s in mod.IN_SAMPLE if s.split("/")[0] == day_stamp and "live" in s]
    live_oos = [s for s in mod.OOS if s.split("/")[0] == day_stamp and "live" in s]
    if live_is and not live_oos:
        return "in_sample"
    if live_oos and not live_is:
        return "oos"
    is_n = sum(1 for s in mod.IN_SAMPLE if s.split("/")[0] == day_stamp)
    oos_n = sum(1 for s in mod.OOS if s.split("/")[0] == day_stamp)
    if is_n > 0 and oos_n == 0:
        return "in_sample"
    if oos_n > 0 and is_n == 0:
        return "oos"
    if day_stamp in mod.IN_SAMPLE:
        return "in_sample"
    if day_stamp in mod.OOS:
        return "oos"
    return "unknown"


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if not jsonl.is_file():
        return []
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


def _extract_closed_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[1]:
            accepts[key] = ev

    trades: list[dict[str, Any]] = []
    for key, acc in accepts.items():
        exit_ev: Optional[dict[str, Any]] = None
        for ev in events:
            if ev.get("event_type") != "observer_exit":
                continue
            if (str(ev.get("symbol") or ""), str(ev.get("entry_time") or "")) == key:
                exit_ev = ev
                break
        pnl = _float(exit_ev.get("pnl_pct")) if exit_ev else None
        if pnl is None:
            continue
        reason = str(exit_ev.get("exit_reason") or "") if exit_ev else ""
        stop_hit = bool(exit_ev.get("stop_hit")) if exit_ev else False
        if exit_ev and not stop_hit and reason == "stop_hit":
            stop_hit = True
        ge5 = _boolish(acc.get(FLAG_GE5))
        ge6 = _boolish(acc.get(FLAG_GE6))
        if exit_ev:
            if FLAG_GE5 in exit_ev:
                ge5 = _boolish(exit_ev.get(FLAG_GE5))
            if FLAG_GE6 in exit_ev:
                ge6 = _boolish(exit_ev.get(FLAG_GE6))
        trades.append(
            {
                "symbol": key[0],
                "entry_time": key[1],
                "entry_expectancy_score": acc.get("entry_expectancy_score"),
                FLAG_GE5: ge5,
                FLAG_GE6: ge6,
                "pnl_pct": pnl,
                "stop_hit": stop_hit,
            }
        )
    return trades


def _cohort_metrics(trades: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    rows = [t for t in trades if t.get(flag)]
    pnls = [float(t["pnl_pct"]) for t in rows]
    n = len(rows)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in rows if t.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _split_metrics(trades: list[dict[str, Any]], flag: str, mod: Any) -> dict[str, Any]:
    all_m = _cohort_metrics(trades, flag)
    is_rows = [t for t in trades if t.get("split") == "in_sample" and t.get(flag)]
    oos_rows = [t for t in trades if t.get("split") == "oos" and t.get(flag)]
    is_pnls = [float(t["pnl_pct"]) for t in is_rows]
    oos_pnls = [float(t["pnl_pct"]) for t in oos_rows]
    return {
        **all_m,
        "IS_trade_count": len(is_rows),
        "IS_profit_factor": _pf(is_pnls) if is_pnls else None,
        "IS_total_pnl_pct": round(sum(is_pnls), 4) if is_pnls else 0.0,
        "OOS_trade_count": len(oos_rows),
        "OOS_profit_factor": _pf(oos_pnls) if oos_pnls else None,
        "OOS_total_pnl_pct": round(sum(oos_pnls), 4) if oos_pnls else 0.0,
    }


def _passes_validation(m: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    pf = m.get("profit_factor")
    if pf is None or pf <= 1:
        reasons.append("pf_not_gt_1")
    if (m.get("total_pnl_pct") or 0) <= 0:
        reasons.append("pnl_not_gt_0")
    is_pf = m.get("IS_profit_factor")
    if is_pf is None or is_pf <= 1:
        reasons.append("is_pf_not_gt_1")
    oos_pf = m.get("OOS_profit_factor")
    if oos_pf is None or oos_pf <= 1:
        reasons.append("oos_pf_not_gt_1")
    if m.get("trade_count", 0) == 0:
        reasons.append("no_trades")
    return not reasons, reasons


def _stamp() -> str:
    return datetime.now(JST).strftime("%H%M%S")


def _run_session(
    *,
    push_dir: Path,
    output_dir: Path,
    config: Any,
    poll_interval_sec: float,
) -> dict[str, Any]:
    from small_paper.pilot_runner import run_push_replay_dry_run

    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_push_replay_dry_run(
        config,
        push_dir=push_dir,
        output_dir=output_dir,
        repo_root=REPO,
        poll_interval_sec=poll_interval_sec,
        replay_speed_sec=0.0,
        enable_discord=True,
    )
    summary_path = output_dir / "small_paper_summary.json"
    summary = dict(result.summary)
    summary["phase234_paper_trade_sim"] = True
    summary["phase234_harness"] = True
    summary["phase234_push_dir"] = str(push_dir)
    if summary_path.is_file():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "summary": summary,
        "accepted_count": len(result.accepted),
        "event_count": len(result.events),
        "events_path": str(output_dir / "small_paper_events.jsonl"),
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase234 paper-trade simulation harness")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-days", type=int, default=0, help="Limit days (0=all)")
    parser.add_argument("--day", action="append", default=[], help="Run specific YYYYMMDD day only")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--poll-interval-sec", type=float, default=0.0)
    parser.add_argument("--list-days", action="store_true")
    args = parser.parse_args()

    days = _discover_push_days()
    if args.day:
        allow = set(args.day)
        days = [d for d in days if d["day_stamp"] in allow]
    if args.max_days > 0:
        days = days[: args.max_days]

    if args.list_days:
        for d in days:
            print(d["day_stamp"], d["push_dir"])
        return 0

    from small_paper.config import load_pilot_config

    cfg_path = args.config if args.config.is_absolute() else REPO / args.config
    config = load_pilot_config(cfg_path)
    p213 = _load_module(
        "phase213c_loader_p234",
        "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py",
    )

    session_results: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []

    print(f"phase234 days={len(days)} config={cfg_path.name}", flush=True)
    for i, day in enumerate(days, 1):
        day_stamp = day["day_stamp"]
        push_dir = Path(day["push_dir"])
        session_id = f"phase234/{day_stamp}/push_replay_sim"
        output_dir = OUT_BASE / day_stamp / "push_replay_sim"

        if args.skip_existing and (output_dir / "small_paper_summary.json").is_file():
            print(f"[{i}/{len(days)}] skip existing {day_stamp}", flush=True)
            session_results.append(
                {
                    "day_stamp": day_stamp,
                    "session_id": session_id,
                    "push_dir": str(push_dir),
                    "status": "ok",
                    "output_dir": str(output_dir),
                    "skipped_replay": True,
                }
            )
        else:
            print(f"[{i}/{len(days)}] replay {day_stamp} ...", flush=True)
            try:
                run_info = _run_session(
                    push_dir=push_dir,
                    output_dir=output_dir,
                    config=config,
                    poll_interval_sec=float(args.poll_interval_sec),
                )
            except Exception as exc:
                print(f"  FAILED {day_stamp}: {exc}", flush=True)
                session_results.append(
                    {
                        "day_stamp": day_stamp,
                        "session_id": session_id,
                        "push_dir": str(push_dir),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue
            session_results.append(
                {
                    "day_stamp": day_stamp,
                    "session_id": session_id,
                    "push_dir": str(push_dir),
                    "status": "ok",
                    **run_info,
                }
            )

        events = _load_events(output_dir)
        split = _day_split(day_stamp, p213)
        for t in _extract_closed_trades(events):
            all_trades.append({**t, "session_id": session_id, "day_stamp": day_stamp, "split": split})

    total_sessions = sum(1 for s in session_results if s.get("status") == "ok")
    total_trades = len(all_trades)
    score5 = _split_metrics(all_trades, FLAG_GE5, p213)
    score6 = _split_metrics(all_trades, FLAG_GE6, p213)
    s5_ok, s5_fail = _passes_validation(score5)
    s6_ok, s6_fail = _passes_validation(score6)

    report = {
        "phase": 234,
        "mode": "paper_trade_sim_harness",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "post_hoc_csv_aggregation_forbidden": True,
            "future_leak_forbidden": True,
            "hard_reject_forbidden": True,
            "discord_send_forbidden": True,
            "orders_forbidden": True,
        },
        "method": {
            "pipeline": "push_jsonl chronological stream → live-equivalent small_paper push_replay",
            "path": "candidate → quality → accept → observer_exit",
            "phase230_score": "computed at accept via entry_expectancy_score_shadow",
            "poll_interval_sec": float(args.poll_interval_sec),
            "observer_enabled": True,
            "discord_active": False,
            "config": str(cfg_path),
            "output_session_root": str(OUT_BASE),
        },
        "evaluation": {
            "total_sessions": total_sessions,
            "total_trades": total_trades,
            "score5_trades": score5["trade_count"],
            "score5_pf": score5["profit_factor"],
            "score5_pnl": score5["total_pnl_pct"],
            "score5_win_rate": score5["win_rate"],
            "score5_stop_rate": score5["stop_rate"],
            "score5_IS_pf": score5["IS_profit_factor"],
            "score5_OOS_pf": score5["OOS_profit_factor"],
            "score6_trades": score6["trade_count"],
            "score6_pf": score6["profit_factor"],
            "score6_pnl": score6["total_pnl_pct"],
            "score6_win_rate": score6["win_rate"],
            "score6_stop_rate": score6["stop_rate"],
            "score6_IS_pf": score6["IS_profit_factor"],
            "score6_OOS_pf": score6["OOS_profit_factor"],
        },
        "validation": {
            "score_ge5": {
                "criteria": "PF>1, PnL>0, IS_PF>1, OOS_PF>1",
                "pass": s5_ok,
                "fail_reasons": s5_fail,
            },
            "score_ge6": {
                "criteria": "PF>1, PnL>0, IS_PF>1, OOS_PF>1",
                "pass": s6_ok,
                "fail_reasons": s6_fail,
            },
        },
        "score_ge5_detail": score5,
        "score_ge6_detail": score6,
        "sessions": session_results,
        "discovered_push_days": days,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT} sessions={total_sessions} trades={total_trades} "
        f"score5={score5['trade_count']} score6={score6['trade_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
