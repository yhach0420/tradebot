#!/usr/bin/env python3
"""
Phase235: Phase229 entry score attribution on Phase230 shadow sessions (review only).

Per score token: compare trades WITH token points vs WITHOUT.
Decompose Score>=5 and Score>=6 cohorts by token presence.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase235_entry_score_attribution.json"
OUT_CSV = REPO / "kabu_native/results/reports/phase235_entry_score_attribution.csv"
SEARCH_ROOTS = (
    REPO / "kabu_native/results/small_paper",
    REPO / "kabu_native/results/small_paper/phase234",
)


def _bootstrap() -> None:
    native = REPO / "kabu_native"
    for p in (native / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


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


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r["pnl_pct"]) for r in rows]
    n = len(rows)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
    }


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


def _is_phase230_session(summary: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    if summary.get("phase230_entry_expectancy_shadow") or summary.get(
        "entry_expectancy_score_shadow_enabled"
    ):
        return True
    for ev in events:
        if ev.get("entry_expectancy_score") is not None:
            return True
    return False


def _discover_sessions() -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for base in SEARCH_ROOTS:
        if not base.is_dir():
            continue
        for summary_path in sorted(base.rglob("small_paper_summary.json")):
            session_dir = str(summary_path.parent.resolve())
            if session_dir in seen:
                continue
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            events = _load_events(Path(session_dir))
            if not _is_phase230_session(summary, events):
                continue
            seen.add(session_dir)
            rel = summary_path.parent
            try:
                rel_id = rel.relative_to(REPO / "kabu_native/results/small_paper").as_posix()
            except ValueError:
                rel_id = str(rel)
            out.append({"session_id": rel_id, "session_dir": session_dir})
    return out


def _extract_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        trades.append({**acc, "pnl_pct": pnl})
    return trades


def _enrich_tokens(trades: list[dict[str, Any]]) -> None:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS, _feature_token

    for t in trades:
        active: dict[str, bool] = {}
        points = 0
        for token, pts in SCORE_POINTS.items():
            lbl = token.split(":", 1)[0]
            tok = _feature_token(lbl, t)
            hit = tok == token
            active[token] = hit
            if hit:
                points += pts
        t["_active_tokens"] = active
        t["_recomputed_score"] = points
        logged = _float(t.get("entry_expectancy_score"))
        if logged is not None and int(logged) != points:
            t["_score_mismatch"] = True
        t["entry_expectancy_score_ge5_flag"] = points >= 5
        t["entry_expectancy_score_ge6_flag"] = points >= 6


def _compare_row(token: str, rows: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    with_rows = [r for r in rows if (r.get("_active_tokens") or {}).get(token)]
    without_rows = [r for r in rows if not (r.get("_active_tokens") or {}).get(token)]
    with_m = _metrics(with_rows)
    without_m = _metrics(without_rows)
    pts = 0
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS

    pts = SCORE_POINTS.get(token, 0)
    return {
        "token": token,
        "points_if_active": pts,
        "cohort_scope": cohort,
        "with_token": with_m,
        "without_token": without_m,
        "delta_pf": (
            round((with_m["profit_factor"] or 0) - (without_m["profit_factor"] or 0), 4)
            if with_m["profit_factor"] is not None and without_m["profit_factor"] is not None
            else None
        ),
        "delta_pnl_pct": round(with_m["total_pnl_pct"] - without_m["total_pnl_pct"], 4),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions = _discover_sessions()
    all_trades: list[dict[str, Any]] = []
    for sess in sessions:
        events = _load_events(Path(sess["session_dir"]))
        for t in _extract_trades(events):
            all_trades.append({**t, "session_id": sess["session_id"]})

    _enrich_tokens(all_trades)

    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS

    tokens = list(SCORE_POINTS.keys())

    token_attribution = [_compare_row(tok, all_trades, cohort="all_trades") for tok in tokens]

    ge5 = [t for t in all_trades if t.get("entry_expectancy_score_ge5_flag")]
    ge6 = [t for t in all_trades if t.get("entry_expectancy_score_ge6_flag")]
    score_ge5_breakdown = [_compare_row(tok, ge5, cohort="score_ge5") for tok in tokens]
    score_ge6_breakdown = [_compare_row(tok, ge6, cohort="score_ge6") for tok in tokens]

    flat_rows: list[dict[str, Any]] = []
    for block in token_attribution + score_ge5_breakdown + score_ge6_breakdown:
        tok = block["token"]
        scope = block["cohort_scope"]
        for side in ("with_token", "without_token"):
            m = block[side]
            flat_rows.append(
                {
                    "cohort_scope": scope,
                    "token": tok,
                    "side": "with" if side == "with_token" else "without",
                    "points_if_active": block["points_if_active"],
                    "trade_count": m["trade_count"],
                    "profit_factor": m["profit_factor"],
                    "total_pnl_pct": m["total_pnl_pct"],
                    "win_rate": m["win_rate"],
                }
            )

    mismatch_n = sum(1 for t in all_trades if t.get("_score_mismatch"))

    report = {
        "phase": 235,
        "mode": "entry_score_attribution",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "phase230_shadow_sessions_only": True,
            "new_feature_exploration_forbidden": True,
            "new_score_exploration_forbidden": True,
        },
        "method": {
            "score_source": "Phase229 SCORE_POINTS + accept-time fields (entry_expectancy_score_shadow)",
            "comparison": "with token active vs without token active",
            "cohorts": ["all_trades", "score_ge5", "score_ge6"],
        },
        "observed_session_count": len(sessions),
        "sessions": sessions,
        "population": {
            "closed_trades": len(all_trades),
            "score_ge5_trades": len(ge5),
            "score_ge6_trades": len(ge6),
            "score_recompute_mismatch_count": mismatch_n,
        },
        "score_map": dict(SCORE_POINTS),
        "token_attribution_all_trades": token_attribution,
        "score_ge5_token_breakdown": score_ge5_breakdown,
        "score_ge6_token_breakdown": score_ge6_breakdown,
        "summary": {
            "all_trades": _metrics(all_trades),
            "score_ge5": _metrics(ge5),
            "score_ge6": _metrics(ge6),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if flat_rows:
        fields = list(flat_rows[0].keys())
        with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(flat_rows)

    print(
        f"wrote {OUT} sessions={len(sessions)} trades={len(all_trades)} ge5={len(ge5)} ge6={len(ge6)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
