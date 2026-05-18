#!/usr/bin/env python3
"""
Phase 47: Diagnose live feature bridge — quality distribution vs Phase45 fallback.

Reads small_paper_events (post-bridge) or replays push_jsonl through LiveFeatureBridge.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def _analyze_events(events_path: Path, *, top_n: int = 100) -> dict[str, Any]:
    quality_hist: Counter[str] = Counter()
    fallback = 0
    complete = 0
    candidates = 0
    mfe_vals: list[float] = []
    mae_vals: list[float] = []
    top: list[dict[str, Any]] = []

    with events_path.open(encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("event_type") != "candidate":
                continue
            candidates += 1
            q = float(e.get("continuation_quality_score") or 0)
            quality_hist[f"{q:.4f}"] += 1
            if e.get("quality_fallback_path"):
                fallback += 1
            if e.get("live_feature_complete"):
                complete += 1
            mfe = e.get("rolling_mfe_pct")
            mae = e.get("rolling_mae_pct")
            if mfe not in (None, ""):
                mfe_vals.append(float(mfe))
            if mae not in (None, ""):
                mae_vals.append(float(mae))
            top.append(dict(e))

    top.sort(key=lambda r: float(r.get("continuation_quality_score") or 0), reverse=True)
    ge55 = sum(1 for k, v in quality_hist.items() for _ in range(v) if float(k) >= 0.55)

    def _dist(vals: list[float]) -> dict[str, Any]:
        if not vals:
            return {}
        return {
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
            "mean": round(sum(vals) / len(vals), 6),
        }

    unique_scores = len(quality_hist)
    fixed_0323_only = unique_scores == 1 and "0.3230" in quality_hist

    return {
        "candidate_count": candidates,
        "unique_quality_scores": unique_scores,
        "fixed_0_323_only": fixed_0323_only,
        "quality_ge_0_55_count": ge55,
        "quality_ge_0_55_pct": round(100.0 * ge55 / max(1, candidates), 2),
        "quality_histogram_top20": dict(quality_hist.most_common(20)),
        "fallback_count": fallback,
        "fallback_rate_pct": round(100.0 * fallback / max(1, candidates), 2),
        "live_feature_complete_count": complete,
        "live_feature_complete_rate_pct": round(100.0 * complete / max(1, candidates), 2),
        "rolling_mfe_distribution": _dist(mfe_vals),
        "rolling_mae_distribution": _dist(mae_vals),
        "top_candidates": top[:top_n],
    }


def _replay_push_dir(
    push_dir: Path,
    *,
    profile: str,
    max_lines_per_file: int = 0,
) -> dict[str, Any]:
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from small_paper.pilot_runner import _candidate_trade_from_push

    bridge = LiveFeatureBridge()
    quality_hist: Counter[str] = Counter()
    fallback = 0
    complete = 0
    n = 0
    top: list[dict[str, Any]] = []

    for fp in sorted(push_dir.glob("*.jsonl")):
        sym = fp.stem
        for line in fp.open(encoding="utf-8"):
            if max_lines_per_file and n >= max_lines_per_file:
                break
            rec = json.loads(line)
            if rec.get("source") != "live_push":
                continue
            payload = rec.get("payload") or {}
            snap = bridge.update(sym, payload)
            enriched = bridge.enrich_payload(payload, snap)
            trade = _candidate_trade_from_push(
                enriched,
                symbol=sym,
                profile=profile,
                feature_snapshot=snap,
            )
            n += 1
            q = float(trade.get("continuation_quality_score") or 0)
            quality_hist[f"{q:.4f}"] += 1
            if snap.quality_fallback_path:
                fallback += 1
            if snap.live_feature_complete:
                complete += 1
            row = {
                "symbol": sym,
                "recorded_at": rec.get("recorded_at"),
                "continuation_quality_score": q,
                **{k: trade.get(k) for k in (
                    "quality_fallback_path",
                    "live_feature_complete",
                    "rolling_mfe_pct",
                    "rolling_mae_pct",
                    "momentum_continuation_score",
                    "favorable_continuation",
                    "max_continuation_duration",
                    "adverse_shrinking",
                )},
            }
            top.append(row)

    top.sort(key=lambda r: float(r["continuation_quality_score"]), reverse=True)
    ge55 = sum(1 for k, v in quality_hist.items() for _ in range(v) if float(k) >= 0.55)

    return {
        "mode": "push_jsonl_replay",
        "push_dir": str(push_dir),
        "evaluations": n,
        "unique_quality_scores": len(quality_hist),
        "quality_ge_0_55_count": ge55,
        "quality_ge_0_55_pct": round(100.0 * ge55 / max(1, n), 2),
        "quality_histogram_top20": dict(quality_hist.most_common(20)),
        "fallback_rate_pct": round(100.0 * fallback / max(1, n), 2),
        "live_feature_complete_rate_pct": round(100.0 * complete / max(1, n), 2),
        "top_candidates": top[:100],
    }


def main() -> int:
    repo_root, native_root = _bootstrap()
    from small_paper.config import load_pilot_config

    parser = argparse.ArgumentParser(description="Diagnose Phase47 live feature bridge")
    parser.add_argument(
        "--session-dir",
        type=Path,
        help="Live session dir with small_paper_events.jsonl (post-Phase47)",
    )
    parser.add_argument(
        "--push-dir",
        type=Path,
        help="Replay push_jsonl through bridge (e.g. data/push_jsonl/2026-05-18)",
    )
    parser.add_argument("--max-push-lines", type=int, default=0, help="Cap replay lines (0=all)")
    args = parser.parse_args()

    cfg = load_pilot_config(native_root / "configs" / "small_paper_pilot.yaml")
    profile = cfg.profile

    if args.session_dir:
        session_dir = args.session_dir if args.session_dir.is_absolute() else repo_root / args.session_dir
        events_path = session_dir / "small_paper_events.jsonl"
        if not events_path.is_file():
            print(f"Missing {events_path}", file=sys.stderr)
            return 2
        report = {"source": "session_events", "session_dir": str(session_dir), **_analyze_events(events_path)}
        out_dir = session_dir
    elif args.push_dir:
        push_dir = args.push_dir if args.push_dir.is_absolute() else repo_root / args.push_dir
        report = _replay_push_dir(
            push_dir, profile=profile, max_lines_per_file=args.max_push_lines
        )
        out_dir = native_root / "results" / "reports"
    else:
        default_session = (
            native_root / "results" / "small_paper" / "20260518" / "live_full_session_081121"
        )
        if (default_session / "small_paper_events.jsonl").is_file():
            report = {
                "source": "session_events",
                "session_dir": str(default_session),
                **_analyze_events(default_session / "small_paper_events.jsonl"),
                "note": "Pre-Phase47 session — use --push-dir to replay with bridge",
            }
            out_dir = default_session
        else:
            parser.error("Provide --session-dir or --push-dir")
            return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "live_feature_bridge_diagnosis.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    top = report.get("top_candidates") or []
    if top:
        csv_path = out_dir / "quality_top_debug_bridge.csv"
        fields = list(top[0].keys())
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in top:
                w.writerow(r)
        print(f"Wrote {csv_path}", file=sys.stderr)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
