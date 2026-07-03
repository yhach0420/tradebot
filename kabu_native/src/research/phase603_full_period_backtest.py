"""
Phase603 full-period backtest: Phase602 (fallback OFF) vs Phase603 (fallback ON).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_observer_review import (
    _session_end_time,
    _summarize_structural_trades,
    replay_combined_structural_exit_v1,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase603_full_period_backtest_done"
PROD_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
CHECKPOINT_DIR = "_phase603_backtest_checkpoints"

VS_FIELDS = [
    "metric",
    "phase602_baseline",
    "phase603_candidate",
    "delta",
    "notes",
]
FALLBACK_TRADE_FIELDS = [
    "segment",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "avg_hold_sec",
    "exit_reason_top",
    "notes",
]


def _num(v: Any) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _discover_push_days(push_root: Path) -> list[str]:
    if not push_root.is_dir():
        return []
    days = sorted(
        d.name for d in push_root.iterdir() if d.is_dir() and list(d.glob("*.jsonl"))
    )
    return days


def _pnl_yen_100_from_pct(pct: float, entry_price: float) -> float:
    if entry_price <= 0:
        return pct * 100.0
    return pct / 100.0 * entry_price * 100.0


def _trade_rows_from_structural(
    trades: Sequence[Any],
    *,
    accept_meta: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        key = (str(t.symbol), str(t.entry_time))
        meta = accept_meta.get(key, {})
        pnl_yen = _pnl_yen_100_from_pct(float(t.realized_pnl_pct), float(t.entry_price or 0))
        rows.append(
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "exit_time": t.close_time,
                "hold_sec": t.hold_duration_sec,
                "pnl_pct": t.realized_pnl_pct,
                "pnl_yen_100": round(pnl_yen, 2),
                "exit_reason": t.close_reason,
                "entry_price": t.entry_price,
                "price_freshness_source": meta.get("price_freshness_source") or "",
                "fallback_used": bool(meta.get("fallback_used")),
                "spread_bps": meta.get("spread_bps"),
            }
        )
    return rows


def _metrics_from_trade_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "total_pnl_yen_100": 0.0,
            "profit_factor": None,
            "win_rate": None,
            "avg_pnl_yen_100": 0.0,
            "max_drawdown_yen_100": 0.0,
        }
    pnls = [_num(r.get("pnl_yen_100")) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    holds = [_num(r.get("hold_sec")) for r in rows if r.get("hold_sec")]
    pf_val = _pf(pnls) if pnls else None
    return {
        "trade_count": len(rows),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": round(pf_val, 4) if pf_val is not None else None,
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "max_drawdown_yen_100": round(max_dd, 2),
        "avg_hold_sec": round(statistics.mean(holds), 1) if holds else 0.0,
        "exit_reasons": dict(Counter(str(r.get("exit_reason") or "") for r in rows)),
    }


def _parse_entry_scan_audit(audit_path: Path) -> dict[str, Any]:
    stale = rescued = fallback_pass = fallback_evals = 0
    spread_blocked = 0
    for line in audit_path.read_text(encoding="utf-8").splitlines() if audit_path.is_file() else []:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        rr = str(row.get("reject_reason") or "")
        if rr == "data_stale_price":
            stale += 1
        src = str(row.get("price_freshness_source") or "")
        if src == "board_fallback":
            fallback_evals += 1
        if src == "board_fallback" and row.get("entry_decision"):
            fallback_pass += 1
        if "spread_above_max" in str(row.get("fallback_reject_reason") or ""):
            spread_blocked += 1
    return {
        "data_stale_price_rejects": stale,
        "board_fallback_rescues": fallback_evals,
        "fallback_gate_pass_evals": fallback_pass,
        "spread_blocked": spread_blocked,
    }


def _accept_meta_from_rows(accepted: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in accepted:
        sym = str(row.get("symbol") or "")
        et = str(row.get("entry_time") or row.get("eval_end_ts") or "")
        out[(sym, et)] = {
            "price_freshness_source": row.get("price_freshness_source"),
            "fallback_used": row.get("fallback_used"),
            "spread_bps": row.get("spread_bps"),
        }
    return out


def _run_single_day(
    repo_root: Path,
    day_iso: str,
    *,
    board_fallback: bool,
    poll_interval_sec: float,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    for p in (kabu / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run

    cfg_path = repo_root / PROD_YAML if (repo_root / PROD_YAML).is_file() else kabu / "configs" / Path(PROD_YAML).name
    base = load_pilot_config(cfg_path)
    cfg = replace(
        base,
        discord_enabled=False,
        discord_observer_only=True,
        entry_freshness_board_fallback_enabled=board_fallback,
    )
    push_dir = kabu / "data" / "push_jsonl" / day_iso
    tag = "phase603" if board_fallback else "phase602"
    out_dir = kabu / "results" / "small_paper" / CHECKPOINT_DIR / f"{day_iso.replace('-', '')}_{tag}"
    t0 = time.monotonic()
    result = run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=out_dir,
        repo_root=repo_root,
        poll_interval_sec=poll_interval_sec,
        streaming_push_replay=True,
        enable_discord=False,
        write_board_shadow_reports=False,
    )
    runtime = time.monotonic() - t0
    events = list(result.events or [])
    accepted = list(result.accepted or [])
    summary = dict(result.summary or {})
    rc = summary.get("reject_reason_counts") or {}
    accept_meta = _accept_meta_from_rows(accepted)
    session_end = _session_end_time(events)
    struct_trades, _ = replay_combined_structural_exit_v1(
        events,
        pilot_config=cfg,
        poll_interval_sec=poll_interval_sec,
        session_end=session_end,
    )
    trade_rows = _trade_rows_from_structural(struct_trades, accept_meta=accept_meta)
    perf = _metrics_from_trade_rows(trade_rows)
    audit_stats = _parse_entry_scan_audit(out_dir / "entry_scan_audit.jsonl")
    gate_eval = int(summary.get("gate_evaluations") or summary.get("evaluated_symbols") or 0)
    return {
        "day": day_iso,
        "variant": tag,
        "board_fallback": board_fallback,
        "runtime_sec": round(runtime, 1),
        "push_rows": int(summary.get("push_rows") or 0),
        "gate_evaluations": gate_eval,
        "entry_candidates": int(summary.get("candidate_count") or len([e for e in events if e.get("event_type") == "candidate"])),
        "accepts": len(accepted),
        "reject_reason_counts": dict(rc),
        "data_stale_price": int(rc.get("data_stale_price") or 0),
        "entry_scan": audit_stats,
        "performance": perf,
        "trade_rows": trade_rows,
        "output_dir": str(out_dir),
    }


def _day_pair_complete(job: "Phase603FullPeriodJob", day_iso: str) -> bool:
    return (
        job._load_ckpt(day_iso, "phase602") is not None
        and job._load_ckpt(day_iso, "phase603") is not None
    )


def _run_day_pair_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """ProcessPool worker: baseline + candidate for one day (module-level for Windows spawn)."""
    repo_root = Path(str(task["repo_root"]))
    day_iso = str(task["day"])
    poll_interval = float(task["poll_interval_sec"])
    resume = bool(task.get("resume", True))

    job = Phase603FullPeriodJob(repo_root, poll_interval_sec=poll_interval)

    b: Optional[dict[str, Any]] = job._load_day_with_trades(day_iso, "phase602") if resume else None
    c: Optional[dict[str, Any]] = job._load_day_with_trades(day_iso, "phase603") if resume else None

    if b is None:
        print(f"[worker] phase602 {day_iso} ...", flush=True)
        b = _run_single_day(
            repo_root, day_iso, board_fallback=False, poll_interval_sec=poll_interval
        )
        job._save_ckpt(b)
    if c is None:
        print(f"[worker] phase603 {day_iso} ...", flush=True)
        c = _run_single_day(
            repo_root, day_iso, board_fallback=True, poll_interval_sec=poll_interval
        )
        job._save_ckpt(c)

    print(
        f"[worker] day done {day_iso} b_accepts={b.get('accepts')} c_accepts={c.get('accepts')}",
        flush=True,
    )
    return {"day": day_iso, "baseline": b, "candidate": c}


@dataclass
class Phase603FullPeriodJob:
    repo_root: Path
    poll_interval_sec: float = 5.0

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.push_root = self.kabu / "data" / "push_jsonl"
        self.ckpt_root = self.kabu / "results" / "small_paper" / CHECKPOINT_DIR
        self.ckpt_root.mkdir(parents=True, exist_ok=True)

    def _ckpt_path(self, day_iso: str, variant: str) -> Path:
        return self.ckpt_root / f"{day_iso.replace('-', '')}_{variant}.json"

    def _load_ckpt(self, day_iso: str, variant: str) -> Optional[dict[str, Any]]:
        p = self._ckpt_path(day_iso, variant)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save_ckpt(self, payload: Mapping[str, Any]) -> None:
        p = self._ckpt_path(str(payload["day"]), str(payload["variant"]))
        slim = dict(payload)
        slim.pop("trade_rows", None)
        p.write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        trades_path = p.with_suffix(".trades.json")
        trades_path.write_text(
            json.dumps(payload.get("trade_rows") or [], ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _load_day_with_trades(self, day_iso: str, variant: str) -> Optional[dict[str, Any]]:
        base = self._load_ckpt(day_iso, variant)
        if base is None:
            return None
        trades_path = self._ckpt_path(day_iso, variant).with_suffix(".trades.json")
        if trades_path.is_file():
            base["trade_rows"] = json.loads(trades_path.read_text(encoding="utf-8"))
        else:
            base["trade_rows"] = []
        audit_path = Path(str(base.get("output_dir") or "")) / "entry_scan_audit.jsonl"
        if audit_path.is_file():
            base["entry_scan"] = _parse_entry_scan_audit(audit_path)
        return base

    def run(
        self,
        *,
        days: Optional[Sequence[str]] = None,
        resume: bool = True,
        workers: int = 1,
    ) -> dict[str, Any]:
        if workers > 1:
            return self.run_parallel(days=days, resume=resume, workers=workers)
        return self._run_sequential(days=days, resume=resume)

    def run_parallel(
        self,
        *,
        days: Optional[Sequence[str]] = None,
        resume: bool = True,
        workers: int = 4,
    ) -> dict[str, Any]:
        all_days = list(days or _discover_push_days(self.push_root))
        workers = max(1, int(workers))
        pending: list[str] = []
        preloaded: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

        for day_iso in all_days:
            if resume and _day_pair_complete(self, day_iso):
                b = self._load_day_with_trades(day_iso, "phase602")
                c = self._load_day_with_trades(day_iso, "phase603")
                assert b is not None and c is not None
                preloaded[day_iso] = (b, c)
                print(f"phase603 skip (cached) {day_iso}", flush=True)
            else:
                pending.append(day_iso)

        print(
            f"phase603 parallel: workers={workers} pending={len(pending)} cached={len(preloaded)}",
            flush=True,
        )
        results_by_day: dict[str, tuple[dict[str, Any], dict[str, Any]]] = dict(preloaded)
        failed_days: list[str] = []
        if pending:
            tasks = [
                {
                    "repo_root": str(self.repo_root),
                    "day": day_iso,
                    "poll_interval_sec": self.poll_interval_sec,
                    "resume": resume,
                }
                for day_iso in pending
            ]
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_day_pair_task, t): t["day"] for t in tasks}
                for fut in as_completed(futures):
                    day_key = futures[fut]
                    try:
                        row = fut.result()
                        results_by_day[str(row["day"])] = (row["baseline"], row["candidate"])
                    except Exception as exc:
                        print(f"phase603 FAILED {day_key}: {exc}", flush=True)
                        failed_days.append(day_key)

        baseline_days: list[dict[str, Any]] = []
        candidate_days: list[dict[str, Any]] = []
        for day_iso in all_days:
            pair = results_by_day.get(day_iso)
            if pair is None:
                raise RuntimeError(f"missing results for {day_iso} (failed={failed_days})")
            baseline_days.append(pair[0])
            candidate_days.append(pair[1])

        if failed_days:
            raise RuntimeError(f"failed days: {failed_days}")

        return self._aggregate(baseline_days, candidate_days, all_days)

    def _run_sequential(
        self,
        *,
        days: Optional[Sequence[str]] = None,
        resume: bool = True,
    ) -> dict[str, Any]:
        all_days = list(days or _discover_push_days(self.push_root))
        baseline_days: list[dict[str, Any]] = []
        candidate_days: list[dict[str, Any]] = []

        for day_iso in all_days:
            b: Optional[dict[str, Any]] = None
            c: Optional[dict[str, Any]] = None
            if resume:
                b = self._load_day_with_trades(day_iso, "phase602")
                c = self._load_day_with_trades(day_iso, "phase603")
            if b is None:
                print(f"phase603 backtest baseline {day_iso} ...", flush=True)
                b = _run_single_day(
                    self.repo_root, day_iso, board_fallback=False, poll_interval_sec=self.poll_interval_sec
                )
                self._save_ckpt(b)
            if c is None:
                print(f"phase603 backtest candidate {day_iso} ...", flush=True)
                c = _run_single_day(
                    self.repo_root, day_iso, board_fallback=True, poll_interval_sec=self.poll_interval_sec
                )
                self._save_ckpt(c)
            baseline_days.append(b)
            candidate_days.append(c)
            print(
                f"phase603 day done {day_iso} b_accepts={b.get('accepts')} c_accepts={c.get('accepts')}",
                flush=True,
            )

        return self._aggregate(baseline_days, candidate_days, all_days)

    def _aggregate(
        self,
        baseline_days: Sequence[Mapping[str, Any]],
        candidate_days: Sequence[Mapping[str, Any]],
        all_days: Sequence[str],
    ) -> dict[str, Any]:
        def _sum(key: str, days: Sequence[Mapping[str, Any]], nested: str = "") -> float:
            total = 0.0
            for d in days:
                if nested:
                    total += _num((d.get(nested) or {}).get(key))
                else:
                    total += _num(d.get(key))
            return total

        b_trades: list[dict[str, Any]] = []
        c_trades: list[dict[str, Any]] = []
        for d in baseline_days:
            b_trades.extend(list(d.get("trade_rows") or []))
        for d in candidate_days:
            c_trades.extend(list(d.get("trade_rows") or []))

        b_perf = _metrics_from_trade_rows(b_trades)
        c_perf = _metrics_from_trade_rows(c_trades)

        b_entry = {
            "accepts": int(_sum("accepts", baseline_days)),
            "data_stale_price": int(_sum("data_stale_price", baseline_days)),
            "board_fallback_rescues": 0,
            "fallback_accepts": 0,
        }
        c_entry = {
            "accepts": int(_sum("accepts", candidate_days)),
            "data_stale_price": int(_sum("data_stale_price", candidate_days)),
            "board_fallback_rescues": int(_sum("board_fallback_rescues", candidate_days, "entry_scan")),
            "fallback_accepts": sum(
                1 for t in c_trades if str(t.get("price_freshness_source")) == "board_fallback"
            ),
        }
        stale_rescue_delta = b_entry["data_stale_price"] - c_entry["data_stale_price"]

        vs_rows = [
            _vs("ENTRY accepts", b_entry["accepts"], c_entry["accepts"]),
            _vs("data_stale_price rejects", b_entry["data_stale_price"], c_entry["data_stale_price"]),
            _vs("data_stale_rescue_delta", 0, stale_rescue_delta),
            _vs("board_fallback freshness evals", 0, c_entry["board_fallback_rescues"]),
            _vs("fallback-path accepts", 0, c_entry["fallback_accepts"]),
            _vs(
                "fallback accept rate",
                0,
                round(c_entry["fallback_accepts"] / max(c_entry["accepts"], 1), 4),
                pct=True,
            ),
            _vs("Trade count", b_perf["trade_count"], c_perf["trade_count"]),
            _vs("Total PnL yen_100", b_perf["total_pnl_yen_100"], c_perf["total_pnl_yen_100"]),
            _vs("Profit factor", b_perf.get("profit_factor"), c_perf.get("profit_factor")),
            _vs("Win rate", b_perf.get("win_rate"), c_perf.get("win_rate")),
            _vs("Avg PnL yen_100", b_perf["avg_pnl_yen_100"], c_perf["avg_pnl_yen_100"]),
            _vs("Max DD yen_100", b_perf["max_drawdown_yen_100"], c_perf["max_drawdown_yen_100"]),
        ]

        fallback_analysis = self._fallback_trade_analysis(c_trades, b_trades)
        symbol_rank = self._symbol_fallback_rank(c_trades)
        spread_buckets = self._spread_bucket_analysis(c_trades)
        adoption = self._adoption_verdict(b_perf, c_perf, c_entry, fallback_analysis)

        return {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "period_days": list(all_days),
            "day_count": len(all_days),
            "poll_interval_sec": self.poll_interval_sec,
            "baseline": {"entry": b_entry, "performance": b_perf},
            "candidate": {"entry": c_entry, "performance": c_perf},
            "vs_rows": vs_rows,
            "fallback_trade_analysis": fallback_analysis,
            "symbol_fallback_ranking": symbol_rank,
            "spread_bucket_performance": spread_buckets,
            "source_comparison": self._source_comparison(c_trades),
            "adoption": adoption,
            "daily_baseline": [{k: v for k, v in d.items() if k != "trade_rows"} for d in baseline_days],
            "daily_candidate": [{k: v for k, v in d.items() if k != "trade_rows"} for d in candidate_days],
        }

    def _fallback_trade_analysis(
        self, c_trades: Sequence[Mapping[str, Any]], b_trades: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        fb = [
            t
            for t in c_trades
            if str(t.get("price_freshness_source")) == "board_fallback"
            and str(t.get("exit_reason")) != "overlap_replaced_review"
        ]
        cur = [t for t in c_trades if str(t.get("price_freshness_source")) == "current_price_time"]
        normal_b = list(b_trades)
        rows = []
        for seg, subset in (
            ("fallback_entry", fb),
            ("current_price_time_entry", cur),
            ("all_phase603", list(c_trades)),
            ("all_phase602_baseline", normal_b),
        ):
            m = _metrics_from_trade_rows(subset)
            top_exit = Counter(str(t.get("exit_reason") or "") for t in subset).most_common(1)
            rows.append(
                {
                    "segment": seg,
                    "trade_count": m["trade_count"],
                    "total_pnl_yen_100": m["total_pnl_yen_100"],
                    "profit_factor": m.get("profit_factor"),
                    "win_rate": m.get("win_rate"),
                    "avg_pnl_yen_100": m["avg_pnl_yen_100"],
                    "avg_hold_sec": m.get("avg_hold_sec"),
                    "exit_reason_top": top_exit[0][0] if top_exit else "",
                    "notes": "",
                }
            )
        return rows

    def _symbol_fallback_rank(self, c_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for t in c_trades:
            if str(t.get("price_freshness_source")) == "board_fallback":
                by_sym[str(t.get("symbol"))].append(t)
        rows = []
        for sym, trades in sorted(by_sym.items(), key=lambda kv: -len(kv[1])):
            m = _metrics_from_trade_rows(trades)
            rows.append({"symbol": sym, "fallback_trades": m["trade_count"], "pnl_yen_100": m["total_pnl_yen_100"], "pf": m.get("profit_factor")})
        return rows

    def _spread_bucket_analysis(self, c_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        buckets = {"le20": [], "20_50": [], "gt50": [], "unknown": []}
        for t in c_trades:
            sp = t.get("spread_bps")
            if sp is None or sp == "":
                buckets["unknown"].append(t)
            elif _num(sp) <= 20:
                buckets["le20"].append(t)
            elif _num(sp) <= 50:
                buckets["20_50"].append(t)
            else:
                buckets["gt50"].append(t)
        return [
            {"spread_bucket": k, **_metrics_from_trade_rows(v), "notes": "phase603 trades"}
            for k, v in buckets.items()
        ]

    def _source_comparison(self, c_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for src in ("current_price_time", "board_fallback"):
            subset = [t for t in c_trades if str(t.get("price_freshness_source")) == src]
            m = _metrics_from_trade_rows(subset)
            out.append({"price_freshness_source": src, **m})
        return out

    def _adoption_verdict(
        self,
        b_perf: Mapping[str, Any],
        c_perf: Mapping[str, Any],
        c_entry: Mapping[str, Any],
        fallback_analysis: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        delta_pnl = _num(c_perf.get("total_pnl_yen_100")) - _num(b_perf.get("total_pnl_yen_100"))
        fb_row = next((r for r in fallback_analysis if r.get("segment") == "fallback_entry"), {})
        fb_pf = fb_row.get("profit_factor")
        fb_wr = fb_row.get("win_rate")
        fb_n = fb_row.get("trade_count")
        all_pf = c_perf.get("profit_factor")
        b_pf = b_perf.get("profit_factor")
        reasons: list[str] = []

        if delta_pnl > 5000 and _num(all_pf) >= _num(b_pf) * 0.95:
            verdict = "採用可"
            reasons.append(
                f"総PnL +{delta_pnl:.0f} yen_100、PF baseline={b_pf} candidate={all_pf}"
            )
        elif delta_pnl > 0 and _num(all_pf) >= 1.0:
            verdict = "採用保留"
            reasons.append(f"PnL改善(+{delta_pnl:.0f})だがPF/DD要確認 baseline_PF={b_pf} candidate_PF={all_pf}")
        elif delta_pnl < -5000 or (_num(all_pf) < 1.0 and fb_n and _num(fb_n) >= 5):
            verdict = "採用不可"
            reasons.append(
                f"PnL {delta_pnl:.0f} yen_100、candidate PF={all_pf}、fallback trades={fb_n} PF={fb_pf} WR={fb_wr}"
            )
        else:
            verdict = "採用保留"
            reasons.append(
                f"PnL差 {delta_pnl:.0f}、fallback trades={fb_n}、要全期間継続監視"
            )
        reasons.append(
            f"data_stale_price delta={c_entry.get('data_stale_price')} vs baseline (lower=better)"
        )
        return {"verdict": verdict, "reasons": reasons, "delta_pnl_yen_100": round(delta_pnl, 2)}

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "json": rep / "phase603_full_period_report.json",
            "vs": rep / "phase603_vs_phase602.csv",
            "fallback": rep / "phase603_fallback_trade_analysis.csv",
            "summary": rep / "phase603_summary.md",
        }
        paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        _write_csv(paths["vs"], VS_FIELDS, result.get("vs_rows") or [])
        _write_csv(paths["fallback"], FALLBACK_TRADE_FIELDS, result.get("fallback_trade_analysis") or [])
        adoption = result.get("adoption") or {}
        paths["summary"].write_text(
            "\n".join(
                [
                    "# Phase603 Full Period Backtest vs Phase602",
                    "",
                    f"**Period:** {result.get('day_count')} days ({', '.join((result.get('period_days') or [])[:3])} …)",
                    f"**Poll interval:** {result.get('poll_interval_sec')}s (live parity)",
                    "",
                    "## Comparison",
                    "",
                    *[f"- {r['metric']}: baseline={r['phase602_baseline']} candidate={r['phase603_candidate']} delta={r.get('delta')}" for r in (result.get('vs_rows') or [])],
                    "",
                    f"## Adoption: **{adoption.get('verdict')}**",
                    "",
                    *[f"- {x}" for x in (adoption.get('reasons') or [])],
                ]
            ),
            encoding="utf-8",
        )
        return paths


def _vs(metric: str, b: Any, c: Any, *, pct: bool = False) -> dict[str, Any]:
    try:
        delta = round(float(c) - float(b), 4) if b is not None and c is not None else ""
    except (TypeError, ValueError):
        delta = ""
    return {
        "metric": metric,
        "phase602_baseline": b,
        "phase603_candidate": c,
        "delta": delta,
        "notes": "pct" if pct else "",
    }


def run_phase603_full_period(
    repo_root: Optional[Path] = None,
    *,
    poll_interval_sec: float = 5.0,
    resume: bool = True,
    days: Optional[Sequence[str]] = None,
    workers: int = 4,
) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[3]
    job = Phase603FullPeriodJob(root, poll_interval_sec=poll_interval_sec)
    result = job.run(days=days, resume=resume, workers=workers)
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    result["workers"] = workers
    return result
