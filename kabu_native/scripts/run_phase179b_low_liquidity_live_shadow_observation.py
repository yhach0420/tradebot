#!/usr/bin/env python3
"""
Phase179b: Live shadow observation for low_liquidity_shadow logging-only.

Goal:
- Confirm only thin names are detected (e.g., 2693.T / 6969.T) and liquid low-priced names (e.g., 6659.T) are NOT detected.
- Provide post-detection price movement snapshots for shadow-rejected symbols.

Reads:
- kabu_native/results/small_paper/**/small_paper_summary.json
- small_paper_events.csv

Writes:
- kabu_native/results/reports/phase179b_low_liquidity_live_shadow_observation.json
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
OUT = Path("kabu_native/results/reports/phase179b_low_liquidity_live_shadow_observation.json")

FOCUS_REJECT = {"2693.T", "6969.T"}
FOCUS_KEEP = {"6659.T"}

# Observation window for "その後の値動き"
POST_WINDOW_MIN = 15


def _f(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _is_post_phase179_session(summary: dict[str, Any], events_csv: Path) -> bool:
    # Prefer summary flag if present; otherwise detect new event columns in header.
    if bool(summary.get("low_liquidity_shadow_enabled")):
        return True
    if not events_csv.is_file():
        return False
    try:
        with events_csv.open("r", encoding="utf-8") as f:
            header = f.readline()
        return "low_liquidity_shadow_rejected" in header
    except OSError:
        return False


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = Path("kabu_native/results/small_paper")
    if not base.is_dir():
        OUT.write_text(json.dumps({"phase": "179b", "verdict": "error", "error": "missing_results_dir"}, indent=2), encoding="utf-8")
        return 0

    # Scan recent sessions (last ~30 summaries by mtime)
    summaries = sorted(base.rglob("small_paper_summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    sessions: list[Path] = []
    for sp in summaries:
        sdir = sp.parent
        summ = json.loads(sp.read_text(encoding="utf-8"))
        ev = sdir / "small_paper_events.csv"
        if _is_post_phase179_session(summ, ev):
            sessions.append(sdir)

    if not sessions:
        OUT.write_text(
            json.dumps(
                {
                    "phase": "179b",
                    "verdict": "insufficient_data_post_phase179_run_needed",
                    "ok": None,
                    "audited_session_count": 0,
                    "notes": [
                        "No live shadow session found with low_liquidity_shadow fields.",
                        "Run a live shadow with a non-prod YAML enabling low_liquidity_shadow_enabled=true, then rerun this script.",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 0

    audited: list[dict[str, Any]] = []
    overall_ok = True

    for sdir in sessions:
        summ = json.loads((sdir / "small_paper_summary.json").read_text(encoding="utf-8"))
        ev_path = sdir / "small_paper_events.csv"
        if not ev_path.is_file():
            continue

        # Collect per-accepted evidence and later price path points (candidate events) for post-window.
        accepted: list[dict[str, Any]] = []
        candidates_by_symbol: dict[str, list[tuple[datetime, float]]] = defaultdict(list)

        with ev_path.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                et = str(r.get("event_type") or "")
                sym = str(r.get("symbol") or "").strip()
                if not sym:
                    continue
                t = _parse_iso(str(r.get("event_time") or ""))
                px = _f(r.get("current_price"))
                if t and px is not None:
                    if et == "candidate":
                        candidates_by_symbol[sym].append((t, px))
                if et != "accepted":
                    continue

                accepted.append(
                    {
                        "symbol": sym,
                        "event_time": r.get("event_time"),
                        "entry_time": r.get("entry_time"),
                        "current_price": px,
                        "rolling_mfe_pct": _f(r.get("rolling_mfe_pct")),
                        "rolling_mae_pct": _f(r.get("rolling_mae_pct")),
                        "trading_value": _f(r.get("trading_value")),
                        "turnover_proxy": _f(r.get("turnover_proxy")),
                        "low_liquidity_shadow_rejected": str(r.get("low_liquidity_shadow_rejected") or "").lower() in ("true", "1", "yes"),
                        "low_liquidity_shadow_reason": r.get("low_liquidity_shadow_reason") or "",
                    }
                )

        ll_count = sum(1 for a in accepted if a["low_liquidity_shadow_rejected"])
        ll_symbols = sorted({a["symbol"] for a in accepted if a["low_liquidity_shadow_rejected"]})
        accepted_count = len(accepted)

        # trailing_mfe到達率（近似）: accepted row の rolling_mfe_pct >= 0.8
        mfe_reached = sum(1 for a in accepted if (a.get("rolling_mfe_pct") is not None and float(a["rolling_mfe_pct"]) >= 0.8))
        mfe_reach_rate = (mfe_reached / accepted_count) if accepted_count else None

        # Stop_hit tendency: use session-level structural_exit_reason_counts (best available without per-trade observer rows)
        stop_hit_count = int(((summ.get("structural_exit_reason_counts") or {}).get("stop_hit")) or 0)
        trailing_exit_count = int(((summ.get("structural_exit_reason_counts") or {}).get("trailing_mfe_exit")) or 0)

        # Post-window price movement for shadow rejected accepts
        post_moves: list[dict[str, Any]] = []
        for a in accepted:
            if not a["low_liquidity_shadow_rejected"]:
                continue
            ent_t = _parse_iso(str(a.get("entry_time") or ""))
            ent_px = _f(a.get("current_price"))
            if not ent_t or ent_px is None or ent_px <= 0:
                continue
            end_t = ent_t + timedelta(minutes=POST_WINDOW_MIN)
            path = [(t, px) for (t, px) in candidates_by_symbol.get(a["symbol"], []) if ent_t <= t <= end_t]
            if not path:
                continue
            max_px = max(px for (_t, px) in path)
            min_px = min(px for (_t, px) in path)
            post_moves.append(
                {
                    "symbol": a["symbol"],
                    "entry_time": a["entry_time"],
                    "entry_px": ent_px,
                    "window_min": POST_WINDOW_MIN,
                    "max_px": max_px,
                    "min_px": min_px,
                    "max_move_pct": round((max_px - ent_px) / ent_px * 100.0, 4),
                    "min_move_pct": round((min_px - ent_px) / ent_px * 100.0, 4),
                    "points": len(path),
                    "shadow_reason": a["low_liquidity_shadow_reason"],
                    "trading_value": a.get("trading_value"),
                    "turnover_proxy": a.get("turnover_proxy"),
                }
            )

        # Focus checks
        focus = {}
        for sym in sorted(FOCUS_REJECT | FOCUS_KEEP):
            acc_sym = [a for a in accepted if a["symbol"] == sym]
            focus[sym] = {
                "accepted": len(acc_sym),
                "shadow_rejected": sum(1 for a in acc_sym if a["low_liquidity_shadow_rejected"]),
                "examples": acc_sym[:5],
            }

        # Liquidity distribution (accepted only)
        tvs = [float(a["trading_value"]) for a in accepted if a.get("trading_value") is not None]
        tos = [float(a["turnover_proxy"]) for a in accepted if a.get("turnover_proxy") is not None]
        def q(xs: list[float], p: float) -> Optional[float]:
            if not xs:
                return None
            ys = sorted(xs)
            idx = int(round((len(ys) - 1) * p))
            return ys[max(0, min(len(ys) - 1, idx))]
        liq_dist = {
            "trading_value": {"p10": q(tvs, 0.10), "p50": q(tvs, 0.50), "p90": q(tvs, 0.90)},
            "turnover_proxy": {"p10": q(tos, 0.10), "p50": q(tos, 0.50), "p90": q(tos, 0.90)},
        }

        # Pass criteria (soft): focus thin names detected, 6659 not detected.
        ok = True
        notes: list[str] = []
        if focus.get("2693.T", {}).get("accepted", 0) > 0 and focus["2693.T"]["shadow_rejected"] == 0:
            ok = False
            notes.append("2693.T accepted but not shadow-rejected")
        if focus.get("6969.T", {}).get("accepted", 0) > 0 and focus["6969.T"]["shadow_rejected"] == 0:
            ok = False
            notes.append("6969.T accepted but not shadow-rejected")
        if focus.get("6659.T", {}).get("shadow_rejected", 0) > 0:
            ok = False
            notes.append("6659.T should be preserved but was shadow-rejected")

        overall_ok = overall_ok and ok

        audited.append(
            {
                "session_dir": str(sdir).replace("\\", "/"),
                "generated_at": summ.get("generated_at"),
                "ended_at": summ.get("ended_at"),
                "policy_label": summ.get("policy_label"),
                "low_liquidity_shadow_reject_count_summary": summ.get("low_liquidity_shadow_reject_count"),
                "accepted_count": accepted_count,
                "low_liquidity_shadow_reject_count_events": ll_count,
                "shadow_rejected_symbols": ll_symbols,
                "focus": focus,
                "post_moves_shadow_rejected": post_moves,
                "stop_hit_count_session": stop_hit_count,
                "trailing_mfe_exit_count_session": trailing_exit_count,
                "trailing_mfe_reach_rate_proxy": mfe_reach_rate,
                "liquidity_distribution_accepted": liq_dist,
                "ok": ok,
                "notes": notes,
            }
        )

    out = {
        "phase": "179b",
        "ok": overall_ok,
        "verdict": "pass" if overall_ok else "fail",
        "audited_session_count": len(audited),
        "audits": audited,
        "requirements": {
            "logging_only": True,
            "detect_thin_names": sorted(list(FOCUS_REJECT)),
            "preserve_liquid_low_price": sorted(list(FOCUS_KEEP)),
            "post_window_min": POST_WINDOW_MIN,
            "notes": [
                "post-move uses candidate events current_price within entry_time..+15min",
                "stop_hit/trailing_mfe_exit are session-level counts (not per symbol) due to current output format",
                "trailing_mfe reach rate uses accepted rolling_mfe_pct>=0.8 proxy",
            ],
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

