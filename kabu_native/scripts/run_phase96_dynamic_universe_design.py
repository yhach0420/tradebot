#!/usr/bin/env python3
"""
Phase 96: Dynamic universe trial design — data-source inventory, options, shadow CSV (review-only).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"

STATIC_UNIVERSE = NATIVE / "data/universe/universe_intraday_full.csv"
UNIVERSE_YAML = NATIVE / "configs/universe.yaml"
PUSH_CAP = 50
FOCUS = ("6613.T", "3905.T")


@dataclass
class UniverseOption:
    option_id: str
    name: str
    description: str
    required_data: str
    implementation_difficulty: str
    api_load: str
    push_subscription_estimate: int
    expected_coverage: str
    overfit_risk: str
    focus_mover_capture: str
    vol_liq_trial_alignment: str
    keep_static_27: str
    recommended_top_n: int
    verdict_notes: str


def _norm(sym: str) -> str:
    s = sym.strip().upper().split("@")[0]
    return s if s.endswith(".T") else f"{s}.T"


def load_static_universe() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not STATIC_UNIVERSE.is_file():
        return rows
    with STATIC_UNIVERSE.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(str(row.get("symbol", "")))
            if sym:
                rows.append({**row, "symbol": sym})
    return rows


def pipeline_inventory() -> list[dict[str, Any]]:
    return [
        {
            "component": "universe_intraday_full.csv",
            "path": "kabu_native/data/universe/universe_intraday_full.csv",
            "role": "Live small_paper / record_push_jsonl default watch list (27 passed rows)",
            "refresh": "none — frozen since Phase 2 data inventory build",
            "feeds": "run_small_paper_pilot.py --source live",
        },
        {
            "component": "data/intraday_1m",
            "path": "data/intraday_1m/ (repo root)",
            "role": "Yahoo-derived 1m CSV; 27 symbols × ~20 days (source of intraday_full)",
            "refresh": "manual EOD save; not market-wide",
            "feeds": "run_replay.py, logic_lab (synthetic PUSH)",
        },
        {
            "component": "build_universe.py",
            "path": "kabu_native/scripts/build_universe.py",
            "role": "kabu GET /board per include_symbols; filters + max_symbols cap",
            "refresh": "on demand; outputs universe_YYYYMMDD.csv/json",
            "feeds": "morning_screen input (when run)",
        },
        {
            "component": "universe.yaml include_symbols",
            "path": "kabu_native/configs/universe.yaml",
            "role": "Candidate seed list (currently 6 manual codes only)",
            "refresh": "config edit",
            "feeds": "build_universe.py only",
        },
        {
            "component": "run_morning_screen.py",
            "path": "kabu_native/scripts/run_morning_screen.py",
            "role": "Scores passed universe rows via /board; top max_symbols=10",
            "refresh": "per run; needs universe_YYYYMMDD.csv input",
            "feeds": "shadow.yaml watchlist source=morning_screen (optional)",
        },
        {
            "component": "kabu PUSH /register",
            "path": "kabu_native/src/api/push_client.py",
            "role": "Live + push-replay tick subscription",
            "refresh": "per session register list",
            "feeds": "run_small_paper_pilot live, record_push_jsonl",
        },
    ]


def data_source_inventory() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "kabu_board_rest",
            "available": True,
            "implemented_in_repo": True,
            "fields": "TradingValue, TradingVolume, ChangePreviousClosePer, CurrentPrice, Bid/Ask, VWAP, High/Low, TotalMarketValue",
            "ranking_api": False,
            "notes": "Per-symbol only; requires upstream candidate list (no market-wide rank endpoint in rest_client).",
        },
        {
            "source_id": "kabu_push_register",
            "available": True,
            "implemented_in_repo": True,
            "fields": "Board-like push updates for registered symbols",
            "ranking_api": False,
            "symbol_limit": PUSH_CAP,
            "notes": "Official limit: max 50 registered symbols (REST+PUSH share list). Current live uses 27.",
        },
        {
            "source_id": "kabu_ranking_endpoint",
            "available": False,
            "implemented_in_repo": False,
            "fields": None,
            "ranking_api": False,
            "notes": "No ranking/top-gainers API wired in kabu_native; /symbol is lookup not screener.",
        },
        {
            "source_id": "intraday_1m_csv",
            "available": True,
            "implemented_in_repo": True,
            "fields": "OHLCV 1m",
            "ranking_api": False,
            "symbol_count": 27,
            "notes": "Cannot discover new movers; Yahoo lag; not suitable alone for dynamic universe.",
        },
        {
            "source_id": "push_jsonl_archive",
            "available": True,
            "implemented_in_repo": True,
            "fields": "Recorded PUSH payloads per watched symbol/day",
            "ranking_api": False,
            "notes": "Post-hoc only for symbols already subscribed; not a discovery source.",
        },
        {
            "source_id": "morning_screen_output",
            "available": True,
            "implemented_in_repo": True,
            "fields": "score, pass_screen, board metrics",
            "ranking_api": False,
            "notes": "Downstream of universe CSV; cannot expand beyond input candidates.",
        },
        {
            "source_id": "jpx_prime_master",
            "available": False,
            "implemented_in_repo": False,
            "fields": "Full prime symbol list",
            "ranking_api": False,
            "notes": "Documented future extension in universe.md; needed for board-only turnover top-N.",
        },
        {
            "source_id": "yfinance_proxy",
            "available": True,
            "implemented_in_repo": False,
            "fields": "Daily OHLCV, approx change% / volume",
            "ranking_api": "proxy_only",
            "notes": "Phase96 shadow CSV only; not for production live without validation vs kabu board.",
        },
        {
            "source_id": "vol_liq_gate_metrics",
            "available": True,
            "implemented_in_repo": True,
            "fields": "atr_pct, intraday_range_pct, turnover_proxy from PUSH at entry",
            "ranking_api": False,
            "notes": "Entry-time gate only; requires symbol already in PUSH feed — not a universe builder.",
        },
    ]


def universe_options() -> list[UniverseOption]:
    return [
        UniverseOption(
            option_id="A",
            name="dynamic_turnover_topN",
            description="Prime candidates: board TradingValue rank at open; filter price/spread/ETF; take top N.",
            required_data="JPX prime master OR daily turnover seed + kabu /board batch",
            implementation_difficulty="medium",
            api_load="N board calls pre-open (N=100–300) + 50 PUSH",
            push_subscription_estimate=50,
            expected_coverage="high for liquid large/mid caps",
            overfit_risk="low if rules frozen (TV rank + liquidity floors only)",
            focus_mover_capture="high if N=50 and movers are liquid; misses illiquid small caps",
            vol_liq_trial_alignment="strong — high TV names often pass vol_liq",
            keep_static_27="replace with dynamic 50 (PUSH cap)",
            recommended_top_n=50,
            verdict_notes="Best single-pool design under PUSH cap; needs prime master ingestion first.",
        ),
        UniverseOption(
            option_id="B",
            name="dynamic_turnover_plus_gap_topN",
            description="Composite score: TradingValue + ChangePreviousClosePer (and spread penalty).",
            required_data="Same as A + board change fields",
            implementation_difficulty="medium-high",
            api_load="Same as A; scoring in build_universe extension",
            push_subscription_estimate=50,
            expected_coverage="high for gap-up day movers (6613/3905 style)",
            overfit_risk="medium — gap weight needs OOS guard",
            focus_mover_capture="highest among pure-dynamic options for gap days",
            vol_liq_trial_alignment="good — may admit extreme gaps; morning_screen max_change_pct already caps",
            keep_static_27="replace or cap-merge to 50",
            recommended_top_n=50,
            verdict_notes="Best capture for Phase95-style misses; validate gap weight on 5+ sessions shadow.",
        ),
        UniverseOption(
            option_id="C",
            name="dynamic_vol_liq_prescreen_topN",
            description="Pre-open rank by ATR/range/TV proxy similar to vol_liq gate, then board confirm.",
            required_data="Prior-day intraday or board open snapshot; push history for calibration",
            implementation_difficulty="high",
            api_load="Board + optional prior push replay metrics",
            push_subscription_estimate=50,
            expected_coverage="aligned with accepted trades, may miss early gap names before metrics warm",
            overfit_risk="medium-high — overlaps trial gate",
            focus_mover_capture="medium — 6613/3905 likely pass if TV high on day",
            vol_liq_trial_alignment="very strong but redundant with entry gate",
            keep_static_27="optional",
            recommended_top_n=40,
            verdict_notes="Use as secondary score within B, not standalone universe.",
        ),
        UniverseOption(
            option_id="D",
            name="hybrid_static_plus_dynamic",
            description="Union static 27 + dynamic top (50−27)=23 from turnover/gap screen; dedupe.",
            required_data="Same as A/B",
            implementation_difficulty="medium",
            api_load="27 PUSH + 23 dynamic = 50 cap",
            push_subscription_estimate=50,
            expected_coverage="preserves replay continuity + adds movers",
            overfit_risk="low-medium — static leg is legacy bias",
            focus_mover_capture="medium-high — 23 slots for new names; 6613/3905 if in top 23 dynamic",
            vol_liq_trial_alignment="good",
            keep_static_27="yes — 27 static + up to 23 dynamic",
            recommended_top_n=23,
            verdict_notes="Pragmatic Phase96–97 shadow trial; rotate dynamic leg daily.",
        ),
    ]


def fetch_nikkei225_tickers() -> list[str]:
    try:
        import pandas as pd

        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/Nikkei_225",
            match="Ticker",
        )
        for tbl in tables:
            for col in tbl.columns:
                if "ticker" in str(col).lower() or "code" in str(col).lower():
                    codes = []
                    for v in tbl[col].dropna().astype(str):
                        v = v.strip().replace(".T", "")
                        if v.isdigit() and len(v) == 4:
                            codes.append(f"{v}.T")
                    if len(codes) >= 200:
                        return sorted(set(codes))
        # fallback column scan
        for tbl in tables:
            for c in tbl.columns:
                series = tbl[c].astype(str)
                codes = [f"{x.zfill(4)}.T" for x in series if x.isdigit() and len(x) <= 4]
                if len(codes) >= 200:
                    return sorted(set(codes))
    except Exception:
        pass
    return []


def yfinance_board_proxy(
    tickers: list[str],
    trade_date: str,
) -> list[dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    start = trade_date
    end = (datetime.strptime(trade_date, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    rows: list[dict[str, Any]] = []
    chunk = 40
    for i in range(0, len(tickers), chunk):
        batch = tickers[i : i + chunk]
        try:
            data = yf.download(
                batch,
                start=start,
                end=end,
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=True,
            )
        except Exception:
            continue
        if data is None or data.empty:
            continue
        for sym in batch:
            try:
                if len(batch) == 1:
                    sub = data
                else:
                    sub = data[sym]
                if sub is None or sub.empty:
                    continue
                row = sub.iloc[-1]
                o = float(row.get("Open", 0) or 0)
                c = float(row.get("Close", 0) or 0)
                v = float(row.get("Volume", 0) or 0)
                h = float(row.get("High", 0) or 0)
                lo = float(row.get("Low", 0) or 0)
                if o <= 0:
                    continue
                chg = (c / o - 1.0) * 100.0
                tv_proxy = c * v
                rng = (h - lo) / o * 100.0 if h and lo else 0.0
                rows.append(
                    {
                        "symbol": sym,
                        "open": o,
                        "close": c,
                        "volume": v,
                        "change_pct": round(chg, 4),
                        "trading_value_proxy": tv_proxy,
                        "intraday_range_pct": round(rng, 4),
                        "composite_score": tv_proxy * (1.0 + max(chg, 0) / 100.0),
                    }
                )
            except Exception:
                continue
    return rows


def build_shadow_universe(
    trade_date: str,
    *,
    dynamic_top_n: int = 23,
    option_id: str = "D",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    static_rows = load_static_universe()
    static_syms = {_norm(str(r.get("symbol", ""))) for r in static_rows}

    basket = sorted(static_syms)
    n225 = fetch_nikkei225_tickers()
    for s in n225:
        if s not in basket:
            basket.append(s)
    for s in FOCUS:
        if s not in basket:
            basket.append(s)

    metrics = yfinance_board_proxy(basket, trade_date)
    meta: dict[str, Any] = {
        "trade_date": trade_date,
        "option_id": option_id,
        "data_source": "yfinance_proxy_nikkei225_plus_static",
        "basket_size": len(basket),
        "metrics_rows": len(metrics),
        "push_cap": PUSH_CAP,
        "warning": "Shadow only — validate against kabu /board before live.",
    }

    if not metrics:
        meta["error"] = "yfinance_proxy_unavailable"
        out: list[dict[str, Any]] = []
        for r in static_rows:
            out.append(
                {
                    "symbol": r["symbol"],
                    "exchange": r.get("exchange", "1"),
                    "symbol_key": r.get("symbol_key", ""),
                    "passed": "True",
                    "selection_reason": "static_intraday_full",
                    "rank": "",
                    "change_pct": "",
                    "trading_value_proxy": "",
                    "composite_score": "",
                    "in_static_27": True,
                    "in_focus_pair": r["symbol"] in FOCUS,
                }
            )
        return out, meta

    # Dynamic pool: not in static, rank by composite (turnover + gap)
    dynamic_candidates = [m for m in metrics if m["symbol"] not in static_syms]
    dynamic_candidates.sort(key=lambda x: x["composite_score"], reverse=True)
    dynamic_pick = dynamic_candidates[:dynamic_top_n]

    # Static rows with metrics
    static_metrics = {m["symbol"]: m for m in metrics}
    out: list[dict[str, Any]] = []
    rank = 0
    for r in static_rows:
        sym = r["symbol"]
        m = static_metrics.get(sym, {})
        rank += 1
        out.append(
            {
                "symbol": sym,
                "exchange": r.get("exchange", "1"),
                "symbol_key": r.get("symbol_key", f"{sym.replace('.T','')}@1"),
                "passed": "True",
                "selection_reason": "static_intraday_full",
                "rank": rank,
                "change_pct": m.get("change_pct", ""),
                "trading_value_proxy": m.get("trading_value_proxy", ""),
                "composite_score": m.get("composite_score", ""),
                "in_static_27": True,
                "in_focus_pair": sym in FOCUS,
            }
        )

    base_rank = len(out)
    for i, m in enumerate(dynamic_pick, start=1):
        code = m["symbol"].replace(".T", "")
        out.append(
            {
                "symbol": m["symbol"],
                "exchange": "1",
                "symbol_key": f"{code}@1",
                "passed": "True",
                "selection_reason": "dynamic_turnover_plus_gap_proxy",
                "rank": base_rank + i,
                "change_pct": m.get("change_pct", ""),
                "trading_value_proxy": m.get("trading_value_proxy", ""),
                "composite_score": m.get("composite_score", ""),
                "in_static_27": False,
                "in_focus_pair": m["symbol"] in FOCUS,
            }
        )

    meta["static_count"] = len(static_rows)
    meta["dynamic_count"] = len(dynamic_pick)
    meta["total_count"] = len(out)
    meta["focus_in_shadow"] = {s: any(r["symbol"] == s for r in out) for s in FOCUS}
    meta["focus_change_pct"] = {
        s: static_metrics.get(s, {}).get("change_pct")
        for s in FOCUS
        if s in static_metrics
    }
    # Rank of focus in dynamic pool
    for s in FOCUS:
        if s in static_syms:
            meta.setdefault("focus_notes", {})[s] = "already_in_static_27=false; in_static=false"
        else:
            pos = next(
                (i + 1 for i, m in enumerate(dynamic_candidates) if m["symbol"] == s),
                None,
            )
            meta.setdefault("focus_dynamic_rank", {})[s] = pos

    return out, meta


def write_recommendation_md(
    path: Path,
    *,
    verdict: str,
    design: dict[str, Any],
) -> None:
    lines = [
        "# Phase 96 — Dynamic Universe Recommendation",
        "",
        f"**Trade date reference:** {design.get('trade_date')}",
        f"**Verdict:** `{verdict}`",
        "",
        "## Summary",
        "",
        design.get("executive_summary", ""),
        "",
        "## Current path (Phase 95 finding)",
        "",
        "- Live observer reads `universe_intraday_full.csv` (27 symbols, never refreshed).",
        "- Source: `data/intraday_1m` Yahoo inventory — not market-wide.",
        "- `build_universe.py` only evaluates `universe.yaml` `include_symbols` (6 codes).",
        "- kabu PUSH register **max 50 symbols** ([official PUSH doc](https://kabucom.github.io/kabusapi/ptal/push.html)).",
        "",
        "## Recommended approach",
        "",
        f"- **Mode:** {design.get('recommended_mode')}",
        f"- **Top N (dynamic leg):** {design.get('recommended_dynamic_top_n')}",
        f"- **Keep static 27:** {design.get('keep_static_27')}",
        f"- **PUSH subscription target:** {design.get('push_subscription_target')}",
        "",
        "## Shadow verification (no production)",
        "",
        "```bash",
        "python kabu_native/scripts/run_phase96_dynamic_universe_design.py",
        "python kabu_native/scripts/build_universe.py --config kabu_native/configs/universe_dynamic_trial.yaml  # Phase 97+",
        "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live \\",
        "  --universe kabu_native/results/reports/phase96_dynamic_universe_shadow_YYYYMMDD.csv \\",
        "  --config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml",
        "```",
        "",
        "## Implementation phases",
        "",
    ]
    for step in design.get("implementation_phases", []):
        lines.append(f"- {step}")
    lines.append("")
    lines.append("## Constraints respected")
    lines.append("")
    for c in design.get("constraints", []):
        lines.append(f"- {c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 96 dynamic universe design")
    parser.add_argument("--trade-date", default="2026-05-22")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--dynamic-top-n", type=int, default=23)
    args = parser.parse_args()

    trade_date = args.trade_date
    day_stamp = trade_date.replace("-", "")
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    options = universe_options()
    shadow_rows, shadow_meta = build_shadow_universe(
        trade_date, dynamic_top_n=args.dynamic_top_n, option_id="D"
    )

    focus_in_shadow = shadow_meta.get("focus_in_shadow", {})
    focus_dynamic_rank = shadow_meta.get("focus_dynamic_rank", {})
    focus_captured = all(focus_in_shadow.get(s) for s in FOCUS)

    # Verdict logic
    if shadow_meta.get("error"):
        verdict = "need_data_source_first"
        verdict_detail = "Shadow proxy failed; implement JPX prime master + kabu board batch first."
    elif focus_captured:
        verdict = "hybrid_static_plus_dynamic_recommended"
        verdict_detail = (
            "Hybrid D captures focus movers in shadow proxy; proceed to kabu-board shadow trial."
        )
    else:
        verdict = "hybrid_static_plus_dynamic_recommended"
        verdict_detail = (
            "Hybrid D recommended but proxy basket missed focus symbols — "
            "need prime-wide master (not Nikkei225-only) before live shadow."
        )

    design = {
        "phase": 96,
        "trade_date": trade_date,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "phase95_verdict": "universe_static_too_narrow",
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "verdict_also_valid": "proceed_dynamic_universe_shadow",
        "recommended_mode": "hybrid_static_plus_dynamic (option D)",
        "recommended_option_id": "D",
        "secondary_option_id": "B",
        "recommended_dynamic_top_n": args.dynamic_top_n,
        "recommended_push_top_n": PUSH_CAP,
        "keep_static_27": "yes for Phase 96–97 shadow; dedupe union capped at 50 PUSH",
        "push_subscription_target": f"27 static + up to {args.dynamic_top_n} dynamic = {min(27 + args.dynamic_top_n, PUSH_CAP)}",
        "executive_summary": (
            "Replace date-blind intraday_full with a daily hybrid universe: keep the existing 27 "
            "for replay continuity, add up to 23 symbols from a turnover+gap board screen (general rules, "
            "no ticker hardcoding). Requires a prime symbol master and morning build_universe extension; "
            "vol_liq/quality/entry/exit unchanged."
        ),
        "pipeline_inventory": pipeline_inventory(),
        "data_sources": data_source_inventory(),
        "options": [asdict(o) for o in options],
        "shadow_universe_meta": shadow_meta,
        "constraints": [
            "no per-symbol hardcode add/exclude",
            "no time-of-day filter on universe",
            "no entry/exit/quality/vol_liq/cap/production YAML changes in Phase 96",
            "shadow/review-only",
        ],
        "implementation_phases": [
            "Phase 97: Add universe_dynamic_trial.yaml + scripts/build_dynamic_universe.py (prime master CSV ingest + board batch).",
            "Phase 98: Morning job writes universe_YYYYMMDD.csv; shadow/live accept --universe path override.",
            "Phase 99: 3–5 session shadow compare — candidate count, focus-mover hit rate, vol_liq reject rate vs static 27.",
            "Phase 100: Promote only if OOS shadow PF/coverage improves without symbol-specific tuning.",
        ],
        "kabu_push_limit": {
            "max_registered_symbols": PUSH_CAP,
            "source": "https://kabucom.github.io/kabusapi/ptal/push.html",
            "current_live_count": 27,
            "headroom": PUSH_CAP - 27,
        },
    }

    design_path = reports_dir / "phase96_dynamic_universe_design.json"
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")

    options_path = reports_dir / "phase96_dynamic_universe_options.csv"
    with options_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(options[0]).keys()))
        w.writeheader()
        for o in options:
            w.writerow(asdict(o))

    ds_rows = data_source_inventory()
    ds_fields = sorted({k for row in ds_rows for k in row})
    ds_path = reports_dir / "phase96_dynamic_universe_data_sources.csv"
    with ds_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ds_fields, extrasaction="ignore")
        w.writeheader()
        for row in ds_rows:
            w.writerow(row)

    shadow_path = reports_dir / f"phase96_dynamic_universe_shadow_{day_stamp}.csv"
    if shadow_rows:
        with shadow_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(shadow_rows[0].keys()))
            w.writeheader()
            w.writerows(shadow_rows)

    write_recommendation_md(
        reports_dir / "phase96_dynamic_universe_recommendation.md",
        verdict=verdict,
        design=design,
    )

    print(
        json.dumps(
            {
                "verdict": verdict,
                "shadow_total": len(shadow_rows),
                "focus_in_shadow": focus_in_shadow,
                "focus_dynamic_rank": focus_dynamic_rank,
                "outputs": {
                    "design": str(design_path),
                    "options": str(options_path),
                    "data_sources": str(ds_path),
                    "shadow": str(shadow_path),
                    "md": str(reports_dir / "phase96_dynamic_universe_recommendation.md"),
                },
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
