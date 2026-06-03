#!/usr/bin/env python3
"""
Phase238: Full-history validation of entry score v2 (Phase236 Scenario B).

Compare Phase229 score (v1) vs RollingMAE:mid-zeroed score (v2) on all available
push_replay and replay sessions. Review only — no production/YAML/hard-reject changes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase238_entry_score_v2_full_history_validation.json"
SMALL_PAPER = REPO / "kabu_native/results/small_paper"

SCORE_GE5 = 5
SCORE_GE6 = 6


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel_path: str) -> Any:
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


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _split_label(session_id: str, mod: Any) -> str:
    if session_id in mod.IN_SAMPLE:
        return "in_sample"
    if session_id in mod.OOS:
        return "oos"
    day = session_id.split("/")[0]
    live_is = [s for s in mod.IN_SAMPLE if s.split("/")[0] == day and "live" in s]
    live_oos = [s for s in mod.OOS if s.split("/")[0] == day and "live" in s]
    if live_is and not live_oos:
        return "in_sample"
    if live_oos and not live_is:
        return "oos"
    is_n = sum(1 for s in mod.IN_SAMPLE if s.split("/")[0] == day)
    oos_n = sum(1 for s in mod.OOS if s.split("/")[0] == day)
    if is_n > 0 and oos_n == 0:
        return "in_sample"
    if oos_n > 0 and is_n == 0:
        return "oos"
    return "unknown"


def _classify_session(session_id: str, summary: Optional[dict[str, Any]]) -> Optional[str]:
    """Return push_replay | replay | None (exclude live-only sessions)."""
    sid = session_id.replace("\\", "/")
    base_name = sid.split("/")[-1].lower()
    mode = str((summary or {}).get("mode") or "").lower()
    source = str((summary or {}).get("source") or "").lower()

    if "live_session" in base_name or "live_full_session" in base_name:
        return None
    if "push_replay" in base_name or "push_replay_sim" in sid.lower():
        return "push_replay"
    if source in ("push-replay", "push_replay") or "push_replay" in mode:
        return "push_replay"
    if source == "replay" or (
        "replay" in mode and "push" not in mode and "live" not in mode
    ):
        return "replay"
    if "/" not in sid and len(sid) == 8 and sid.isdigit():
        return "replay"
    return None


def _session_has_trades(session_dir: Path, mod: Any, p71: Any) -> bool:
    if (session_dir / "structural_trades.csv").is_file():
        return True
    if (session_dir / "small_paper_trades_review.csv").is_file():
        return True
    if (session_dir / "small_paper_events.jsonl").is_file():
        return True
    return bool(mod.replay_trades_from_events(p71, session_dir))


def discover_replay_sessions(base: Path, mod: Any, p71: Any) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def consider(session_id: str, summary: Optional[dict[str, Any]] = None) -> None:
        kind = _classify_session(session_id, summary)
        if kind is None:
            return
        sdir = base / session_id
        if not sdir.is_dir():
            return
        if not _session_has_trades(sdir, mod, p71):
            return
        prev = found.get(session_id)
        if prev is None or summary:
            found[session_id] = {
                "session_id": session_id,
                "stream": kind,
                "summary_mode": (summary or {}).get("mode"),
                "summary_source": (summary or {}).get("source"),
            }

    if base.is_dir():
        for summary_path in sorted(base.rglob("small_paper_summary.json")):
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                summary = {}
            rel = summary_path.parent.relative_to(base).as_posix()
            consider(rel, summary)

        for day_dir in sorted(base.iterdir()):
            if not day_dir.is_dir():
                continue
            day = day_dir.name
            if len(day) == 8 and day.isdigit():
                consider(day, None)
            if not (len(day) == 8 and day.isdigit()):
                continue
            for sub in sorted(day_dir.iterdir()):
                if sub.is_dir():
                    consider(f"{day}/{sub.name}", None)

    return sorted(found.values(), key=lambda x: x["session_id"])


def _gate_impact(excluded: list[dict[str, Any]]) -> dict[str, Any]:
    win_ex = [r for r in excluded if float(r.get("pnl_pct") or 0) > 0]
    lose_ex = [r for r in excluded if float(r.get("pnl_pct") or 0) < 0]
    win_pnl = round(sum(float(r.get("pnl_pct") or 0) for r in win_ex), 4)
    lose_pnl = round(sum(float(r.get("pnl_pct") or 0) for r in lose_ex), 4)
    return {
        "winner_missed_count": len(win_ex),
        "winner_missed_pnl_pct": win_pnl,
        "loser_avoided_count": len(lose_ex),
        "loser_avoided_pnl_pct": lose_pnl,
        "net_excluded_pnl_pct": round(win_pnl + lose_pnl, 4),
    }


def _trade_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "stop_rate": None,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _cohort_metrics(rows: list[dict[str, Any]], flag_key: str) -> dict[str, Any]:
    subset = [r for r in rows if r.get(flag_key)]
    base = _trade_metrics(subset)
    is_rows = [r for r in subset if r.get("split") == "in_sample"]
    oos_rows = [r for r in subset if r.get("split") == "oos"]
    is_pnls = [float(r["pnl_pct"]) for r in is_rows]
    oos_pnls = [float(r["pnl_pct"]) for r in oos_rows]
    base["IS_profit_factor"] = _pf(is_pnls) if is_pnls else None
    base["OOS_profit_factor"] = _pf(oos_pnls) if oos_pnls else None
    base["IS_trade_count"] = len(is_rows)
    base["OOS_trade_count"] = len(oos_rows)
    return base


def _cohort_compare(
    rows: list[dict[str, Any]],
    *,
    v1_flag: str,
    v2_flag: str,
) -> dict[str, Any]:
    v1_set = [r for r in rows if r.get(v1_flag)]
    v2_set = [r for r in rows if r.get(v2_flag)]
    v1_only = [r for r in rows if r.get(v1_flag) and not r.get(v2_flag)]
    v2_only = [r for r in rows if r.get(v2_flag) and not r.get(v1_flag)]
    return {
        "score_v1": _cohort_metrics(rows, v1_flag),
        "score_v2": _cohort_metrics(rows, v2_flag),
        "v1_ge_cohort_not_v2": _gate_impact(v1_only),
        "v2_ge_cohort_not_v1": _gate_impact(v2_only),
        "cohort_overlap_count": len([r for r in rows if r.get(v1_flag) and r.get(v2_flag)]),
        "v1_only_count": len(v1_only),
        "v2_only_count": len(v2_only),
    }


def _score_rows(rows: list[dict[str, Any]]) -> None:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    for r in rows:
        fields = compute_entry_expectancy_score_fields(trade=r)
        r.update(fields)


def _evaluate_population(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    all_m = _trade_metrics(rows)
    ge5 = _cohort_compare(
        rows,
        v1_flag="entry_expectancy_score_ge5_flag",
        v2_flag="entry_expectancy_score_v2_ge5_flag",
    )
    ge6 = _cohort_compare(
        rows,
        v1_flag="entry_expectancy_score_ge6_flag",
        v2_flag="entry_expectancy_score_v2_ge6_flag",
    )
    v1_ge5_pf = ge5["score_v1"].get("profit_factor")
    v2_ge5_pf = ge5["score_v2"].get("profit_factor")
    v1_ge6_pf = ge6["score_v1"].get("profit_factor")
    v2_ge6_pf = ge6["score_v2"].get("profit_factor")
    return {
        "label": label,
        "trade_count": len(rows),
        "all_trades": {
            "note": "v1 and v2 all_trades metrics are identical (same trade population)",
            "metrics": all_m,
        },
        "score_ge5": ge5,
        "score_ge6": ge6,
        "verdict": {
            "v2_improves_ge5_pf": (
                isinstance(v1_ge5_pf, (int, float))
                and isinstance(v2_ge5_pf, (int, float))
                and v2_ge5_pf > v1_ge5_pf
            ),
            "v2_improves_ge6_pf": (
                isinstance(v1_ge6_pf, (int, float))
                and isinstance(v2_ge6_pf, (int, float))
                and v2_ge6_pf > v1_ge6_pf
            ),
            "v2_improves_ge5_pnl": ge5["score_v2"].get("total_pnl_pct", 0)
            > ge5["score_v1"].get("total_pnl_pct", 0),
            "v2_improves_ge6_pnl": ge6["score_v2"].get("total_pnl_pct", 0)
            > ge6["score_v1"].get("total_pnl_pct", 0),
        },
    }


def _load_population(
    sessions: list[dict[str, Any]],
    mod: Any,
    p217: Any,
    p221: Any,
    p71: Any,
) -> list[dict[str, Any]]:
    book_cache: dict[tuple[str, Any], list[Any]] = {}
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
    rows: list[dict[str, Any]] = []
    for i, meta in enumerate(sessions, 1):
        sid = meta["session_id"]
        trades = p217._load_session_full_trades(mod, sid, p71)
        if not trades:
            print(f"  [{i}/{len(sessions)}] skip {sid}", flush=True)
            continue
        enriched = p217._enrich_session(mod, sid, trades, book_cache, ring_cache)
        for r in enriched:
            r["stream"] = meta["stream"]
            r["split"] = _split_label(sid, mod)
        rows.extend(enriched)
        print(f"  [{i}/{len(sessions)}] {sid} n={len(enriched)}", flush=True)
    p221._augment_early_features(mod, rows)
    _score_rows(rows)
    return rows


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    mod = _load_module(
        "phase213c_loader_p238",
        "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py",
    )
    p217 = _load_module(
        "phase217_loader_p238",
        "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py",
    )
    p221 = _load_module(
        "phase221_loader_p238",
        "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py",
    )
    p71 = mod._load_phase71()

    print("discovering replay + push_replay sessions...", flush=True)
    sessions = discover_replay_sessions(SMALL_PAPER, mod, p71)
    print(f"  sessions={len(sessions)}", flush=True)

    print("loading trades...", flush=True)
    all_rows = _load_population(sessions, mod, p217, p221, p71)

    push_rows = [r for r in all_rows if r.get("stream") == "push_replay"]
    replay_rows = [r for r in all_rows if r.get("stream") == "replay"]

    populations = {
        "combined": _evaluate_population(all_rows, label="combined"),
        "push_replay": _evaluate_population(push_rows, label="push_replay"),
        "replay": _evaluate_population(replay_rows, label="replay"),
    }

    report = {
        "phase": 238,
        "mode": "entry_score_v2_full_history_validation",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "hard_reject_forbidden": True,
            "new_feature_exploration_forbidden": True,
            "symbol_analysis_forbidden": True,
            "time_of_day_analysis_forbidden": True,
            "stop_cause_analysis_forbidden": True,
        },
        "method": {
            "score_v1": "Phase229 SCORE_POINTS (includes RollingMAE:mid +2)",
            "score_v2": "Phase236 Scenario B — RollingMAE:mid +0 (HBRecent:no unchanged)",
            "data_sources": ["all available push_replay sessions", "all available replay sessions"],
            "trade_loading": "phase217 _load_session_full_trades + _enrich_session + phase221 augment",
            "scoring": "entry_expectancy_score_shadow.compute_entry_expectancy_score_fields",
            "cohort_gate_impact": "v1 cohort minus v2 cohort (v2 score <= v1 always)",
            "is_oos_split": "phase213c IN_SAMPLE / OOS lists + day-level fallback",
        },
        "sessions": {
            "session_count": len(sessions),
            "push_replay_session_count": sum(1 for s in sessions if s["stream"] == "push_replay"),
            "replay_session_count": sum(1 for s in sessions if s["stream"] == "replay"),
            "session_ids": [s["session_id"] for s in sessions],
            "by_stream": {
                "push_replay": [s["session_id"] for s in sessions if s["stream"] == "push_replay"],
                "replay": [s["session_id"] for s in sessions if s["stream"] == "replay"],
            },
        },
        "population": {
            "total_trades": len(all_rows),
            "push_replay_trades": len(push_rows),
            "replay_trades": len(replay_rows),
        },
        "results": populations,
        "summary": {
            "combined_v2_improves_ge5_pf": populations["combined"]["verdict"]["v2_improves_ge5_pf"],
            "combined_v2_improves_ge6_pf": populations["combined"]["verdict"]["v2_improves_ge6_pf"],
            "combined_ge5_v1_pf": populations["combined"]["score_ge5"]["score_v1"].get(
                "profit_factor"
            ),
            "combined_ge5_v2_pf": populations["combined"]["score_ge5"]["score_v2"].get(
                "profit_factor"
            ),
            "combined_ge6_v1_pf": populations["combined"]["score_ge6"]["score_v1"].get(
                "profit_factor"
            ),
            "combined_ge6_v2_pf": populations["combined"]["score_ge6"]["score_v2"].get(
                "profit_factor"
            ),
            "combined_ge5_winner_missed": populations["combined"]["score_ge5"][
                "v1_ge_cohort_not_v2"
            ]["winner_missed_count"],
            "combined_ge5_loser_avoided": populations["combined"]["score_ge5"][
                "v1_ge_cohort_not_v2"
            ]["loser_avoided_count"],
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    comb = populations["combined"]
    print(
        f"wrote {OUT} trades={len(all_rows)} sessions={len(sessions)} "
        f"ge5_v1_pf={comb['score_ge5']['score_v1'].get('profit_factor')} "
        f"ge5_v2_pf={comb['score_ge5']['score_v2'].get('profit_factor')} "
        f"ge6_v1_pf={comb['score_ge6']['score_v1'].get('profit_factor')} "
        f"ge6_v2_pf={comb['score_ge6']['score_v2'].get('profit_factor')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
