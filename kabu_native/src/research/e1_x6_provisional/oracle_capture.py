"""BASE session pass that captures oracle bundles per partition (research-only).

One full canonical session replay per valid window (identical to Stage-1 BASE):
fresh provider+session, X5 entry mode, SCORE rows + exit streams collected.

Durable store location: OS temp was wiped on 2026-08-02 (took Stage-1 artifacts),
and kabu_native/results was wiped a second time the same day (took the norm cache
and smoke bundles, killed the first Plan 2.1 pipeline run). Neither location is
trustworthy, so all research state lives OUTSIDE the repo in the user profile.

No Shadow / Runtime / Paper / Live changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from research.e1_x6_provisional.analysis_mask import window_am_pm_tag
from research.e1_x6_provisional.joint_oracle_replay import (
    PartitionBundle,
    build_bundle_from_partition,
)
from research.e1_x6_provisional.util import native_root, norm_sym, progress


def durable_store_root() -> Path:
    """Research store outside the repo and outside OS temp (both were wiped 2026-08-02)."""
    p = Path.home() / "e1x6_research_store"
    p.mkdir(parents=True, exist_ok=True)
    return p


def durable_bundle_root(run_id: str) -> Path:
    p = durable_store_root() / "oracle_bundles" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def durable_norm_cache() -> Path:
    p = durable_store_root() / "e1_x5_norm_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def capture_day(
    day: str,
    *,
    mask_index: dict,
    out_dir: Path,
    cache_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Full canonical BASE replay of one day; saves one bundle per valid window."""
    import small_paper.e1_x5_canonical_replay as cr
    from small_paper.e1_x5_canonical_replay import GAP_THRESHOLD_SEC
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider

    from research.e1_x6_provisional.canonical_partition_replay import replay_partition
    from research.e1_x6_provisional.p2_execute import (
        _build_windows_including_stress,
        _selected_session_id,
    )

    cdir = cache_dir or durable_norm_cache()
    progress(f"CAPTURE: normalize day={day}")
    events, report = cr.normalize_day(native_root(), day, cache_dir=cdir, use_cache=True)
    selected = _selected_session_id(day)
    if selected:
        before = len(events)
        events = [e for e in events if str(e.session_id) == selected]
        progress(f"CAPTURE: day={day} session={selected} events {before} -> {len(events)}")
        gaps = []
        for i in range(1, len(events)):
            a, b = events[i - 1], events[i]
            dt = (b.ts - a.ts).total_seconds()
            if dt > GAP_THRESHOLD_SEC or a.session_id != b.session_id:
                gaps.append(
                    {
                        "kind": "SESSION_BOUNDARY" if a.session_id != b.session_id else "TIME_GAP",
                        "from": a.event_time,
                        "to": b.event_time,
                        "from_key": a.unique_key,
                        "to_key": b.unique_key,
                        "gap_sec": dt,
                    }
                )
        report.gaps = gaps
        report.sessions = [selected]
        report.normalized_rows = len(events)
    label = cr.day_label_strict(native_root(), day, report)
    windows, excluded_w, segs = _build_windows_including_stress(
        day, events, report, label, mask_index=mask_index
    )
    uni = cr.load_universe(native_root(), day)
    coalesced = any("COALESCED" in str(getattr(w, "window_id", "")) for w in windows)
    if coalesced:
        gap_intervals = []
    else:
        gap_intervals = [(g.get("from"), g.get("to")) for g in report.gaps]
    progress(f"CAPTURE: day={day} windows={len(windows)} excluded={len(excluded_w)}")

    metas: list[dict[str, Any]] = []
    for idx, (w, seg) in enumerate(zip(windows, segs)):
        am_pm = window_am_pm_tag(w, day) or "AM"
        info = mask_index.get((day, am_pm)) or {}
        mask_meta = {
            "window_id": info.get("window_id") or getattr(w, "window_id", None),
            "analysis_mask_id": info.get("analysis_mask_id"),
            "quality_class": info.get("quality_class"),
            "valid_window_start": info.get("valid_window_start"),
            "valid_window_end": info.get("valid_window_end"),
            "session_id": getattr(w, "session_id", None),
        }
        provider = DMidD4H6ScoreProvider.maybe_create()
        if provider is None or not provider.ready:
            raise RuntimeError("DMidD4H6ScoreProvider unavailable")
        lookback = float(provider.required_feature_lookback_sec())
        box = {"p": provider, "used": False}

        def _factory():
            if box["used"]:
                p2 = DMidD4H6ScoreProvider.maybe_create()
                if p2 is None or not p2.ready:
                    raise RuntimeError("DMidD4H6ScoreProvider unavailable")
                return p2
            box["used"] = True
            return box["p"]

        progress(f"CAPTURE: day={day} window={getattr(w, 'window_id', idx)} events={len(seg)}")
        part = replay_partition(
            day=day,
            am_pm=am_pm,
            events_in_valid_window=seg,
            universe=uni,
            provider_factory=_factory,
            entry_mode="X5",
            mask_meta=mask_meta,
            gap_intervals=gap_intervals,
            collect_score_rows=True,
            collect_exit_stream=True,
            banner="ORACLE_CAPTURE_BASE_X5",
        )
        bundle = build_bundle_from_partition(
            day=day,
            am_pm=am_pm,
            window_id=str(getattr(w, "window_id", f"{day}:{am_pm}:{idx}")),
            part=part,
            seg_events=seg,
            gap_intervals_raw=gap_intervals,
            lookback_sec=lookback,
        )
        fname = f"{day}_{idx:02d}_{am_pm}.pkl.gz"
        bundle.save(out_dir / fname)
        base_pnl = float(sum(float(t.get("net_pnl_yen_100") or 0) for t in part.completed_trades))
        metas.append(
            {
                "day": day,
                "am_pm": am_pm,
                "idx": idx,
                "window_id": bundle.window_id,
                "file": fname,
                "events_fed": part.events_fed,
                "score_rows": len(part.score_rows),
                "exit_stream_n": len(part.exit_stream),
                "exit_stream_ts_mismatch": part.exit_stream_ts_mismatch,
                "x5_trades": len(part.completed_trades),
                "x5_pnl": base_pnl,
                "x5_censored": len(part.censored_ledger),
                "quality_class": mask_meta.get("quality_class"),
                "analysis_mask_id": mask_meta.get("analysis_mask_id"),
            }
        )
        progress(
            f"CAPTURE: day={day} {am_pm} done trades={len(part.completed_trades)} "
            f"pnl={base_pnl:.2f} scores={len(part.score_rows)} stream={len(part.exit_stream)}"
        )
        del part, bundle, provider, box
    del events
    return metas
