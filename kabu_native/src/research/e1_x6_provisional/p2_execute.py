"""P2 execution: BASE replay, dataset/labels, candidates, folds, determinism."""
from __future__ import annotations

import bisect
import itertools
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.e1_x6_provisional.constants import (
    CANDIDATE_CAP,
    DAYS,
    FOLD_DEFS,
    PREDICTOR_FEATURES,
    PRIMARY_HORIZON_SEC,
    PROVISIONAL_BANNER,
    QUANTILE_GRID,
    TARGET_BPS,
    THRESHOLD,
)
from research.e1_x6_provisional.analysis_mask import (
    assert_timestamps_in_confirm_mask,
    build_mask_index,
    classify_ts,
    filter_events_to_valid_window,
    row_in_analysis_mask,
    window_am_pm_tag,
)
from research.e1_x6_provisional.cost_contract import (
    post_cost_label_bps,
    verify_frozen_e1_x5_cost_contract,
    yen_roundtrip_cost,
)
from research.e1_x6_provisional.portfolio_replay import (
    PortfolioEvent,
    assert_no_confirm_reselection,
    replay_portfolio,
    select_candidate_build_only,
)
from research.e1_x6_provisional.quality_layers import (
    ALL_USABLE_CLASSES,
    day_quality_from_windows,
    include_in_core_base,
    summarize_quality_layers,
    window_quality_map,
)
from research.e1_x6_provisional.util import (
    am_pm_of,
    native_root,
    norm_cache_dir,
    norm_sym,
    parse_ts,
    progress,
    sha256_obj,
    sha256_text,
    stable_json,
    summarize_pnls,
    temp_work_root,
    write_json,
)


def _patch_normalize_cache(cache_dir: Path):
    import small_paper.e1_x5_canonical_replay as cr

    orig = cr.normalize_day

    def wrapped(native_root_p, day, *, cache_dir=None, use_cache=True):
        return orig(native_root_p, day, cache_dir=cache_dir or cache_dir_fixed, use_cache=use_cache)

    cache_dir_fixed = cache_dir
    cr.normalize_day = wrapped
    return orig


def _restore_normalize(orig):
    import small_paper.e1_x5_canonical_replay as cr

    cr.normalize_day = orig


def _build_windows_including_stress(
    day: str,
    events,
    report,
    day_label: str,
    *,
    mask_index: Optional[dict] = None,
):
    """Like build_valid_windows but does NOT short-circuit EXCLUDE_STRATEGY_PNL_DAYS.

    Day 28 remains STRESS_RECOVERABLE / excluded from CORE BASE aggregates, but F2
    confirm still needs windows + SCORE rows.

    When TIME_GAP fragmentation is extreme (ingress CPT jitter), coalesce into
    lunch-split continuous streams so provisional replay remains tractable while
    still recording gaps for trade adoption.

    After windows are built, segments are clipped to Source Manifest valid_window
    (AM/PM). INVALID_SOURCE windows yield empty feeds.
    """
    import small_paper.e1_x5_canonical_replay as cr
    from small_paper.e1_x5_canonical_replay import ExcludedWindow, ValidWindow

    saved = set(cr.EXCLUDE_STRATEGY_PNL_DAYS)
    gap_n = 0
    try:
        cr.EXCLUDE_STRATEGY_PNL_DAYS = set()
        gap_n = len(getattr(report, "gaps", []) or [])
        if gap_n > 40:
            # Skip expensive fragmented split; go straight to coalesce path
            windows, excluded, segs = [], [], []
            progress(f"P2: day={day} gap_n={gap_n} -> provisional lunch-split coalesce")
        else:
            windows, excluded, segs = cr.build_valid_windows(day, events, report, day_label=day_label)
    finally:
        cr.EXCLUDE_STRATEGY_PNL_DAYS = saved

    if len(windows) <= 40 and gap_n <= 40:
        return _clip_windows_to_mask(day, windows, segs, excluded, mask_index)

    progress(
        f"P2: day={day} coalescing fragmented windows={len(windows)} gaps={gap_n} into lunch-split streams"
    )
    am: list[Any] = []
    pm: list[Any] = []
    for e in events:
        # Paper clock only — BEFORE/LUNCH/AFTER excluded before valid_window clip
        session = classify_ts(day, e.ts)
        if session == "AM":
            am.append(e)
        elif session == "PM":
            pm.append(e)
    # Clip coalesced streams to manifest valid_window (not full coalesced outside bounds)
    if mask_index is not None:
        am = filter_events_to_valid_window(day, "AM", am, mask_index)
        pm = filter_events_to_valid_window(day, "PM", pm, mask_index)
    new_windows: list[Any] = []
    new_segs: list[list[Any]] = []
    for tag, seg in (("AM", am), ("PM", pm)):
        usable = []
        for e in seg:
            op = e.payload if isinstance(e.payload, dict) else {}
            if isinstance(op.get("Buy1"), dict) and isinstance(op.get("Sell1"), dict):
                usable.append(e)
        if len(usable) < 2:
            excluded.append(
                ExcludedWindow(day=day, reason=f"coalesce_{tag}_too_short", detail={"n": len(usable)})
            )
            continue
        # Skip INVALID_SOURCE entirely
        if mask_index is not None:
            info = mask_index.get((day, tag)) or {}
            if info.get("quality_class") == "INVALID_SOURCE" or not info.get("include_in_economics"):
                excluded.append(
                    ExcludedWindow(
                        day=day,
                        reason=f"INVALID_SOURCE_OR_NO_ECONOMICS_{tag}",
                        detail={"analysis_mask_id": info.get("analysis_mask_id")},
                    )
                )
                continue
        wid = f"{day}:{tag}:COALESCED:{usable[0].session_id[:12]}"
        new_windows.append(
            ValidWindow(
                day=day,
                window_id=wid,
                session_id=usable[0].session_id,
                start_key=usable[0].unique_key,
                end_key=usable[-1].unique_key,
                start_time=usable[0].event_time,
                end_time=usable[-1].event_time,
                event_count=len(usable),
                day_label=day_label,
                classification="PROVISIONAL_COALESCED_WINDOW",
            )
        )
        new_segs.append(usable)
    excluded.append(
        ExcludedWindow(
            day=day,
            reason="PROVISIONAL_COALESCED_FROM_FRAGMENTED_TIME_GAPS",
            detail={"raw_windows": len(windows), "coalesced": len(new_windows)},
        )
    )
    return new_windows, excluded, new_segs


def _clip_windows_to_mask(day, windows, segs, excluded, mask_index):
    """Clip non-coalesced window segments to manifest valid_window; drop INVALID_SOURCE.

    Legacy flat captures often produce one continuous window spanning BEFORE→AM→LUNCH→PM.
    Those must be split into AM/PM partitions by Paper clock, not discarded because
    start_time is pre-open (window_am_pm_tag=None).
    """
    if mask_index is None:
        return windows, excluded, segs
    from small_paper.e1_x5_canonical_replay import ExcludedWindow, ValidWindow

    new_windows = []
    new_segs = []
    for w, seg in zip(windows, segs):
        tag = window_am_pm_tag(w, day)
        # Split multi-session continuous streams into AM/PM buckets
        buckets: dict[str, list] = {"AM": [], "PM": []}
        if tag in ("AM", "PM"):
            buckets[tag] = list(seg)
        else:
            for e in seg:
                session = classify_ts(day, e.ts)
                if session in ("AM", "PM"):
                    buckets[session].append(e)
            if not buckets["AM"] and not buckets["PM"]:
                excluded.append(
                    ExcludedWindow(
                        day=day,
                        reason="WINDOW_OUTSIDE_AM_PM_CLOCK",
                        detail={"window_id": getattr(w, "window_id", None)},
                    )
                )
                continue
        for ap, bucket in buckets.items():
            if len(bucket) < 2:
                if bucket:
                    excluded.append(
                        ExcludedWindow(
                            day=day,
                            reason=f"SPLIT_{ap}_TOO_SHORT",
                            detail={"window_id": getattr(w, "window_id", None), "n": len(bucket)},
                        )
                    )
                continue
            info = mask_index.get((day, ap)) or {}
            if info.get("quality_class") == "INVALID_SOURCE" or not info.get("include_in_economics"):
                excluded.append(
                    ExcludedWindow(
                        day=day,
                        reason=f"INVALID_SOURCE_OR_NO_ECONOMICS_{ap}",
                        detail={"window_id": getattr(w, "window_id", None)},
                    )
                )
                continue
            clipped = filter_events_to_valid_window(day, ap, bucket, mask_index)
            if len(clipped) < 2:
                excluded.append(
                    ExcludedWindow(
                        day=day,
                        reason=f"MASK_CLIP_{ap}_TOO_SHORT",
                        detail={"before": len(bucket), "after": len(clipped)},
                    )
                )
                continue
            # Deterministic partition window id (preserve lineage to source window)
            base_wid = str(getattr(w, "window_id", "") or f"{day}:W")
            wid = f"{day}:{ap}:MASKCLIP:{base_wid.split(':')[-1][:16]}"
            new_windows.append(
                ValidWindow(
                    day=day,
                    window_id=wid,
                    session_id=getattr(w, "session_id", clipped[0].session_id),
                    start_key=clipped[0].unique_key,
                    end_key=clipped[-1].unique_key,
                    start_time=clipped[0].event_time,
                    end_time=clipped[-1].event_time,
                    event_count=len(clipped),
                    day_label=getattr(w, "day_label", ""),
                    classification=getattr(w, "classification", "MASK_CLIPPED_PARTITION")
                    or "MASK_CLIPPED_PARTITION",
                )
            )
            new_segs.append(clipped)
    return new_windows, excluded, new_segs


def _mid_from_payload(payload: dict) -> Optional[float]:
    from small_paper.canonical_board import best_bid_ask_for_mode

    bid, ask = best_bid_ask_for_mode(payload, mode="canonical")
    if bid and ask and bid > 0 and ask > 0 and ask >= bid:
        return (float(bid) + float(ask)) / 2.0
    cp = payload.get("CurrentPrice")
    try:
        v = float(cp) if cp is not None else None
        return v if v and v > 0 else None
    except Exception:
        return None


def _selected_session_id(day: str) -> Optional[str]:
    """Match P0 preferred sealed session; None = keep all (legacy flat)."""
    from research.e1_x6_provisional.constants import DAY27_PREFERRED_SESSION
    from research.e1_x6_provisional.util import native_root, read_json

    day_dir = native_root() / "data" / "market_capture" / day
    sessions = sorted(day_dir.glob("session_*"))
    if not sessions:
        return None
    if day == "20260727":
        preferred = day_dir / DAY27_PREFERRED_SESSION
        if preferred.is_dir() and (preferred / "seal.json").is_file():
            seal = read_json(preferred / "seal.json")
            return str(seal.get("ingress_session_id") or DAY27_PREFERRED_SESSION.replace("session_", ""))
    # single sealed session preferred
    best = None
    best_raw = -1
    for sid in sessions:
        seal_p = sid / "seal.json"
        if not seal_p.is_file():
            continue
        seal = read_json(seal_p)
        raw = int(seal.get("raw_rows") or 0)
        if raw >= best_raw:
            best_raw = raw
            best = str(seal.get("ingress_session_id") or sid.name.replace("session_", ""))
    return best


def _replay_window_collect(
    *,
    day: str,
    window,
    events: Sequence[Any],
    gap_intervals,
    universe: Optional[set[str]],
    score_jsonl,
    mid_index: dict[str, list[tuple[float, float]]],
    provider: Any,
    mask_index: Optional[dict] = None,
    banner: str = PROVISIONAL_BANNER,
) -> dict[str, Any]:
    """One-pass BASE replay via canonical_partition_replay + SCORE rows + mid timeline.

    SCORE rows and adopted BASE trades are written only when in_analysis_mask=true.
    Mid timeline still indexes quotes from the (already mask-clipped) event feed.
    """
    from research.e1_x6_provisional.canonical_partition_replay import replay_partition
    from small_paper.e1_x5_canonical_replay import parse_ts as cr_parse_ts, trade_ledger_hash

    mi = mask_index or {}
    am_pm = window_am_pm_tag(window, day) or "AM"
    info = mi.get((day, am_pm)) or {}
    mask_meta = {
        "window_id": info.get("window_id") or getattr(window, "window_id", None),
        "analysis_mask_id": info.get("analysis_mask_id"),
        "quality_class": info.get("quality_class"),
        "valid_window_start": info.get("valid_window_start"),
        "valid_window_end": info.get("valid_window_end"),
    }

    uni = universe
    for e in events:
        sym = norm_sym(e.symbol)
        if uni and sym not in uni:
            continue
        payload = dict(e.payload or {})
        recv = cr_parse_ts(e.received_at) or e.ts
        mid = _mid_from_payload(payload)
        if mid is not None and recv is not None:
            mid_index.setdefault(sym, []).append((recv.timestamp(), float(mid)))

    _provider_box = {"p": provider, "used": False}

    def _factory():
        if _provider_box["used"]:
            from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider

            p2 = DMidD4H6ScoreProvider.maybe_create()
            if p2 is None or not p2.ready:
                raise RuntimeError("DMidD4H6ScoreProvider unavailable")
            return p2
        _provider_box["used"] = True
        return _provider_box["p"]

    part = replay_partition(
        day=day,
        am_pm=am_pm,
        events_in_valid_window=events,
        universe=universe,
        provider_factory=_factory,
        entry_mode="X5",
        mask_meta=mask_meta,
        gap_intervals=gap_intervals,
        collect_score_rows=True,
        banner=banner,
    )

    score_n = 0
    score_skipped_mask = 0
    for row in part.score_rows:
        if not row.get("in_analysis_mask"):
            score_skipped_mask += 1
            continue
        score_jsonl.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        score_n += 1

    adopted = list(part.completed_trades)
    orphan_open = [
        {
            "symbol": c.get("symbol"),
            "entry_time": c.get("entry_time"),
            "reason": c.get("reason") or "WINDOW_CENSORED",
            "reason_alias": c.get("reason_alias") or "WINDOW_END_OPEN_EXCLUDED",
            "window_id": mask_meta.get("window_id") or window.window_id,
            "analysis_mask_id": mask_meta.get("analysis_mask_id"),
            "replay_partition_id": part.replay_partition_id,
            "am_pm": am_pm,
            "day": day,
        }
        for c in part.censored_ledger
    ]

    return {
        "day": day,
        "window": window.to_dict(),
        "events_fed": part.events_fed,
        "score_rows": score_n,
        "score_skipped_mask": score_skipped_mask,
        "trades": adopted,
        "excluded_trades": [],
        "orphan_open": orphan_open,
        "censored_ledger": list(part.censored_ledger),
        "completed_trades": len(adopted),
        "realized_pnl_yen_100": float(sum(float(t["net_pnl_yen_100"]) for t in adopted)),
        "ledger_sha256": trade_ledger_hash(adopted),
        "exit_reasons": dict(part.exit_reason_counts),
        "cap_blocked": part.cap_blocked,
        "same_symbol_blocked": part.same_symbol_blocked,
        "evaluated_count": 0,
        "evaluation_mode": part.evaluation_mode,
        "replay_partition_id": part.replay_partition_id,
    }


def process_day(
    day: str,
    *,
    work: Path,
    include_in_core_base: bool,
    mask_index: Optional[dict] = None,
    banner: str = PROVISIONAL_BANNER,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    import small_paper.e1_x5_canonical_replay as cr
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider

    progress(f"P2: normalize+replay day={day}")
    cdir = cache_dir or norm_cache_dir()
    events, report = cr.normalize_day(native_root(), day, cache_dir=cdir, use_cache=True)
    selected = _selected_session_id(day)
    if selected:
        before = len(events)
        events = [e for e in events if str(e.session_id) == selected]
        progress(f"P2: day={day} filter session={selected} events {before} -> {len(events)}")
        from small_paper.e1_x5_canonical_replay import GAP_THRESHOLD_SEC

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
        # CPT/event_time jitter creates thousands of false TIME_GAPs under ingress
        # sequence order. Keep audit gaps on report, but do not use them to void
        # every trade during provisional coalesced replay.
        gap_intervals = []
        progress(f"P2: day={day} coalesced => gap_intervals cleared for trade adoption")
    else:
        gap_intervals = [(g.get("from"), g.get("to")) for g in report.gaps]
    progress(f"P2: day={day} windows={len(windows)} excluded={len(excluded_w)}")

    score_path = work / "dataset" / f"{day}_score.jsonl"
    score_path.parent.mkdir(parents=True, exist_ok=True)
    mid_index: dict[str, list[tuple[float, float]]] = {}
    window_results = []
    all_trades: list[dict[str, Any]] = []
    with open(score_path, "w", encoding="utf-8") as fh:
        for w, seg in zip(windows, segs):
            if len(seg) >= 1000 or len(windows) <= 20:
                progress(f"P2: day={day} window={w.window_id} events={len(seg)}")
            # Fresh provider per window (matches canonical replay_window isolation)
            provider = DMidD4H6ScoreProvider.maybe_create()
            if provider is None or not provider.ready:
                raise RuntimeError("DMidD4H6ScoreProvider unavailable")
            r = _replay_window_collect(
                day=day,
                window=w,
                events=seg,
                gap_intervals=gap_intervals,
                universe=uni,
                score_jsonl=fh,
                mid_index=mid_index,
                provider=provider,
                mask_index=mask_index,
                banner=banner,
            )
            window_results.append(r)
            all_trades.extend(r["trades"])
            del provider

    # Sort mid timelines once; store pickle (JSON too large for ingress days)
    for sym in mid_index:
        mid_index[sym].sort(key=lambda x: x[0])

    import pickle

    mid_path = work / "mids" / f"{day}_mids.pkl"
    mid_path.parent.mkdir(parents=True, exist_ok=True)
    with open(mid_path, "wb") as fh:
        pickle.dump(mid_index, fh, protocol=pickle.HIGHEST_PROTOCOL)

    trades_path = work / "base" / f"{day}_trades.json"
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(trades_path, all_trades)

    day_out = {
        "day": day,
        "day_label": label,
        "normalized_rows": report.normalized_rows,
        "selected_session_id": selected,
        "windows": [w.to_dict() for w in windows],
        "excluded_windows": [e.to_dict() for e in excluded_w],
        "completed_trades": len(all_trades),
        "realized_pnl_yen_100": float(sum(float(t["net_pnl_yen_100"]) for t in all_trades)),
        "ledger_sha256": cr.trade_ledger_hash(all_trades),
        "include_in_core_base": include_in_core_base,
        "score_jsonl": str(score_path),
        "mid_pkl": str(mid_path),
        "trades_json": str(trades_path),
        "mask_in_score_rows": sum(int(wr.get("score_rows") or 0) for wr in window_results),
        "score_skipped_mask": sum(int(wr.get("score_skipped_mask") or 0) for wr in window_results),
        "window_results_summary": [
            {
                "window_id": wr["window"]["window_id"],
                "events_fed": wr["events_fed"],
                "score_rows": wr["score_rows"],
                "score_skipped_mask": wr.get("score_skipped_mask"),
                "completed_trades": wr["completed_trades"],
                "pnl": wr["realized_pnl_yen_100"],
                "ledger_sha256": wr["ledger_sha256"],
            }
            for wr in window_results
            if wr["events_fed"] >= 100 or len(window_results) <= 30
        ],
        "banner": banner,
    }
    write_json(work / "base" / f"{day}_summary.json", day_out)
    progress(
        f"P2: day={day} done trades={day_out['completed_trades']} pnl={day_out['realized_pnl_yen_100']:.2f} "
        f"norm={day_out['normalized_rows']} mask_scores={day_out['mask_in_score_rows']}"
    )
    return day_out


def _fwd_mid(mid_series: list[tuple[float, float]], t0: float, horizon: float) -> Optional[float]:
    if not mid_series:
        return None
    target = t0 + horizon
    times = [x[0] for x in mid_series]
    # first mid at or after target within small grace; else last mid <= target+grace if any after t0
    i = bisect.bisect_left(times, target)
    # allow up to +2s grace for discrete ticks
    grace = 2.0
    if i < len(mid_series) and mid_series[i][0] <= target + grace:
        return mid_series[i][1]
    # if exact/near not found, accept nearest mid in [target, target+grace]
    # else CENSORED
    if i < len(mid_series) and abs(mid_series[i][0] - target) <= grace:
        return mid_series[i][1]
    # try previous if within grace after target? No — need observation at/after horizon
    # Search forward for first within [target, target+30] as soft availability window end
    soft = 30.0
    j = i
    while j < len(mid_series) and mid_series[j][0] <= target + soft:
        return mid_series[j][1]
    return None


def label_score_rows(
    day: str,
    work: Path,
    day_trades: list[dict[str, Any]],
    *,
    banner: str = PROVISIONAL_BANNER,
) -> dict[str, Any]:
    progress(f"P2: labeling day={day}")
    import pickle

    mid_path = work / "mids" / f"{day}_mids.pkl"
    with open(mid_path, "rb") as fh:
        mid_index = pickle.load(fh)
    # index entries by symbol+approx entry time for X5 outcome attach
    entry_map: dict[str, list[dict[str, Any]]] = {}
    for t in day_trades:
        entry_map.setdefault(str(t["symbol"]), []).append(t)

    src = work / "dataset" / f"{day}_score.jsonl"
    out = work / "labels" / f"{day}_labeled.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    censored = 0
    missed = 0
    unnec = 0
    with open(src, "r", encoding="utf-8") as fin, open(out, "w", encoding="utf-8") as fout:
        for line in fin:
            row = json.loads(line)
            # Dataset already mask-filtered; keep only in_analysis_mask=true (defense)
            if row.get("in_analysis_mask") is False:
                continue
            t0 = row.get("decision_ts")
            mid0 = row.get("mid")
            sym = row["symbol"]
            label_val: Any = "CENSORED"
            censor_reason = None
            if t0 is None or mid0 is None or float(mid0) <= 0:
                censor_reason = "NO_MID_AT_DECISION"
                censored += 1
            else:
                mid1 = _fwd_mid(mid_index.get(sym, []), float(t0), float(PRIMARY_HORIZON_SEC))
                if mid1 is None or mid1 <= 0:
                    censor_reason = "NO_MID_WITHIN_HORIZON"
                    censored += 1
                else:
                    label_val = post_cost_label_bps(float(mid0), float(mid1))
            row["post_5bps_expectancy_h300"] = label_val
            row["censor_reason"] = censor_reason
            row["yen_roundtrip_cost_at_mid"] = (
                yen_roundtrip_cost(float(mid0)) if mid0 else None
            )
            # attach X5 trade if entered near this sample
            x5_trade = None
            if row.get("x5_accept"):
                cands = entry_map.get(sym, [])
                best = None
                for tr in cands:
                    et = parse_ts(tr.get("entry_time"))
                    if et is None or t0 is None:
                        continue
                    dt = abs(et.timestamp() - float(t0))
                    if dt <= 2.0 and (best is None or dt < best[0]):
                        best = (dt, tr)
                if best:
                    x5_trade = {
                        "exit_reason": best[1].get("exit_reason"),
                        "net_pnl_yen_100": best[1].get("net_pnl_yen_100"),
                        "holding_sec": best[1].get("holding_sec"),
                    }
            row["x5_trade"] = x5_trade
            entered = bool(row.get("x5_accept"))
            is_missed = (
                (not entered)
                and label_val != "CENSORED"
                and isinstance(label_val, (int, float))
                and float(label_val) > float(TARGET_BPS)
            )
            is_unnec = False
            if entered:
                hit_stop = bool(x5_trade and str(x5_trade.get("exit_reason") or "").upper().startswith("STOP"))
                neg_exp = label_val != "CENSORED" and isinstance(label_val, (int, float)) and float(label_val) < 0
                is_unnec = hit_stop or neg_exp
            row["MISSED_WINNER"] = bool(is_missed)
            row["UNNECESSARY_ENTRY"] = bool(is_unnec)
            row["banner"] = banner
            row["in_analysis_mask"] = True
            if is_missed:
                missed += 1
            if is_unnec:
                unnec += 1
            fout.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            n += 1
    summary = {
        "day": day,
        "rows": n,
        "censored": censored,
        "MISSED_WINNER": missed,
        "UNNECESSARY_ENTRY": unnec,
        "labeled_path": str(out),
        "banner": banner,
    }
    write_json(work / "labels" / f"{day}_label_summary.json", summary)
    return summary


def _quantile(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _threshold_code(v: float) -> str:
    return f"{v:.8g}"


def _cand_id(family: str, features: Sequence[str], direction: str, thr_code: str) -> str:
    feats = ",".join(sorted(features))
    return f"C|{family}|{feats}|{direction}|{thr_code}"


def enumerate_candidates(build_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic candidate registry from build-window rows only. Cap 200."""
    # Collect feature values
    feat_vals: dict[str, list[float]] = {f: [] for f in PREDICTOR_FEATURES}
    for r in build_rows:
        for f in PREDICTOR_FEATURES:
            v = r.get(f)
            if v is None:
                continue
            try:
                feat_vals[f].append(float(v))
            except Exception:
                pass

    dirs = {
        "score": ["higher_better"],
        "spread_bps": ["lower_better"],
        "score_vs_threshold_gap": ["higher_better"],
    }
    cands: list[dict[str, Any]] = []

    # SINGLE_FEATURE
    for feat in sorted(PREDICTOR_FEATURES):
        for direction in dirs[feat]:
            for q in QUANTILE_GRID:
                thr = _quantile(feat_vals[feat], q)
                if thr != thr:  # nan
                    continue
                thr_c = _threshold_code(thr)
                cid = _cand_id("SINGLE_FEATURE", [feat], direction, f"q{q}:{thr_c}")
                cands.append(
                    {
                        "candidate_id": cid,
                        "family": "SINGLE_FEATURE",
                        "features": [feat],
                        "direction": direction,
                        "thresholds": {feat: thr},
                        "quantile": q,
                        "threshold_code": f"q{q}:{thr_c}",
                    }
                )

    # TWO_FEATURE_AND
    for f1, f2 in itertools.combinations(sorted(PREDICTOR_FEATURES), 2):
        for d1 in dirs[f1]:
            for d2 in dirs[f2]:
                direction = f"{f1}:{d1}&{f2}:{d2}"
                for q1, q2 in itertools.product(QUANTILE_GRID, repeat=2):
                    thr1 = _quantile(feat_vals[f1], q1)
                    thr2 = _quantile(feat_vals[f2], q2)
                    if thr1 != thr1 or thr2 != thr2:
                        continue
                    thr_c = f"q{q1}:{_threshold_code(thr1)}|q{q2}:{_threshold_code(thr2)}"
                    cid = _cand_id("TWO_FEATURE_AND", [f1, f2], direction, thr_c)
                    cands.append(
                        {
                            "candidate_id": cid,
                            "family": "TWO_FEATURE_AND",
                            "features": [f1, f2],
                            "direction": direction,
                            "thresholds": {f1: thr1, f2: thr2},
                            "quantile": (q1, q2),
                            "threshold_code": thr_c,
                        }
                    )

    # Sort by enumerate order then candidate_id
    def sort_key(c):
        fam_ord = 0 if c["family"] == "SINGLE_FEATURE" else 1
        return (fam_ord, tuple(c["features"]), c["direction"], c["threshold_code"], c["candidate_id"])

    cands.sort(key=sort_key)
    # Deterministic truncate by enumerate order
    return cands[:CANDIDATE_CAP]


def _passes(row: dict[str, Any], cand: dict[str, Any]) -> bool:
    for feat in cand["features"]:
        v = row.get(feat)
        if v is None:
            return False
        try:
            fv = float(v)
        except Exception:
            return False
        thr = float(cand["thresholds"][feat])
        # direction per feature
        if cand["family"] == "SINGLE_FEATURE":
            direction = cand["direction"]
        else:
            # parse "feat:dir&feat2:dir2"
            part = [p for p in cand["direction"].split("&") if p.startswith(feat + ":")]
            direction = part[0].split(":", 1)[1] if part else "higher_better"
        if direction == "higher_better":
            if not (fv >= thr):
                return False
        else:
            if not (fv <= thr):
                return False
    return True


def _load_labeled_days(
    work: Path,
    days: Sequence[str],
    *,
    mask_only: bool = True,
) -> list[dict[str, Any]]:
    """Load labeled SCORE rows; by default only in_analysis_mask=true rows."""
    rows: list[dict[str, Any]] = []
    for day in days:
        p = work / "labels" / f"{day}_labeled.jsonl"
        if not p.is_file():
            continue
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if mask_only and row.get("in_analysis_mask") is False:
                    continue
                if mask_only and "in_analysis_mask" in row and not row.get("in_analysis_mask"):
                    continue
                rows.append(row)
    return rows


def _proxy_expectancy(rows: list[dict[str, Any]]) -> float:
    vals = []
    for r in rows:
        v = r.get("post_5bps_expectancy_h300")
        if v == "CENSORED" or v is None:
            continue
        try:
            vals.append(float(v))
        except Exception:
            pass
    return float(sum(vals) / len(vals)) if vals else float("-inf")


def _rows_to_portfolio_events(rows: list[dict[str, Any]], cand: dict[str, Any]) -> list[PortfolioEvent]:
    events: list[PortfolioEvent] = []
    for i, r in enumerate(rows):
        # Candidate / confirm: only mask-in labeled rows
        if r.get("in_analysis_mask") is False:
            continue
        ts = parse_ts(r.get("decision_time"))
        if ts is None:
            continue
        bid = r.get("bid")
        ask = r.get("ask")
        mid = r.get("mid")
        if bid is None or ask is None:
            if mid is None:
                continue
            bid = float(mid)
            ask = float(mid)
        events.append(
            PortfolioEvent(
                ts=ts,
                symbol=str(r.get("symbol_norm") or r.get("symbol")),
                signal=_passes(r, cand),
                bid=float(bid),
                ask=float(ask),
                mid=float(mid) if mid is not None else None,
                x5_accept=bool(r.get("x5_accept")),
                event_id=f"{r.get('day')}|{r.get('event_sequence')}|{i}",
            )
        )
    events.sort(key=lambda e: (e.ts, e.symbol, e.event_id))
    return events



def load_partition_events(
    day: str,
    am_pm: str,
    mask_index: dict,
    *,
    cache_dir: Optional[Path] = None,
) -> tuple[list[Any], dict[str, Any], Optional[set[str]], list]:
    """Load canonical events clipped to analysis_mask valid_window for day×AM|PM."""
    import small_paper.e1_x5_canonical_replay as cr

    info = mask_index.get((day, am_pm)) or {}
    if info.get("quality_class") == "INVALID_SOURCE" or not info.get("include_in_economics"):
        return [], dict(info), None, []
    cdir = cache_dir or norm_cache_dir()
    events, report = cr.normalize_day(native_root(), day, cache_dir=cdir, use_cache=True)
    selected = _selected_session_id(day)
    if selected:
        events = [e for e in events if str(e.session_id) == selected]
    # Paper clock filter then valid_window clip
    clock = []
    for e in events:
        if classify_ts(day, e.ts) == am_pm:
            clock.append(e)
    clipped = filter_events_to_valid_window(day, am_pm, clock, mask_index)
    uni = cr.load_universe(native_root(), day)
    # Coalesced ingress: do not void trades via CPT jitter gaps
    gap_intervals: list = []
    return clipped, dict(info), uni, gap_intervals


def replay_candidate_day_partitions(
    days: Sequence[str],
    candidate: Mapping[str, Any],
    mask_index: dict,
    *,
    cache_dir: Optional[Path] = None,
    banner: str = PROVISIONAL_BANNER,
) -> dict[str, Any]:
    """Full canonical replay of each day×AM|PM partition with fresh session (NO carry)."""
    from research.e1_x6_provisional.canonical_partition_replay import (
        merge_partition_results,
        replay_partition,
    )
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider

    parts = []
    for day in days:
        for am_pm in ("AM", "PM"):
            events, info, uni, gaps = load_partition_events(
                day, am_pm, mask_index, cache_dir=cache_dir
            )
            if not events:
                continue
            progress(
                f"P2: canonical candidate replay day={day} {am_pm} events={len(events)} "
                f"cand={str(candidate.get('candidate_id') or '')[:40]}"
            )

            def _factory():
                p = DMidD4H6ScoreProvider.maybe_create()
                if p is None or not p.ready:
                    raise RuntimeError("DMidD4H6ScoreProvider unavailable")
                return p

            part = replay_partition(
                day=day,
                am_pm=am_pm,
                events_in_valid_window=events,
                universe=uni,
                provider_factory=_factory,
                entry_mode="CANDIDATE",
                candidate_spec=candidate,
                mask_meta=info,
                gap_intervals=gaps,
                passes_fn=_passes,
                collect_score_rows=False,
                banner=banner,
            )
            parts.append(part)
    return merge_partition_results(parts)



def evaluate_folds(
    work: Path,
    day_quality: dict[str, str],
    *,
    banner: str = PROVISIONAL_BANNER,
    mask_index: Optional[dict] = None,
    source_manifest: Optional[dict[str, Any]] = None,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """BUILD rank on mask-in SCORE rows; CONFIRM via FULL_CANONICAL_EVENT_REPLAY partitions."""
    from research.e1_x6_provisional.canonical_partition_replay import assert_selected_in_registry
    from research.e1_x6_provisional.replay_lifecycle_contract import EVALUATION_MODE_REQUIRED

    progress("P2: evaluating F1-F5 via BUILD rank + CONFIRM FULL_CANONICAL_EVENT_REPLAY")
    fold_out: dict[str, Any] = {}
    registry_primary: list[dict[str, Any]] = []
    mi = mask_index if mask_index is not None else build_mask_index(source_manifest or {})
    cdir = cache_dir or norm_cache_dir()

    for fold_id, spec in FOLD_DEFS.items():
        if spec.get("deferred"):
            fold_out[fold_id] = {
                "status": "DEFERRED",
                "note": spec.get("note"),
                "banner": banner,
            }
            continue
        build_days = spec["build"]
        confirm_days = spec["confirm"]
        build_rows = _load_labeled_days(work, build_days, mask_only=True)
        confirm_mask_ids: list[str] = []
        for d in confirm_days:
            for ap in ("AM", "PM"):
                info = mi.get((d, ap)) or {}
                mid = info.get("analysis_mask_id")
                if mid:
                    confirm_mask_ids.append(str(mid))

        cands = enumerate_candidates(build_rows)
        ranked = []
        for c in cands:
            matched = [r for r in build_rows if _passes(r, c)]
            support = len(matched)
            proxy = _proxy_expectancy(matched)
            ranked.append({**c, "build_support": support, "build_expectancy_proxy": proxy})
        ranked.sort(
            key=lambda x: (
                -(x["build_support"] > 0),
                -x["build_expectancy_proxy"],
                -x["build_support"],
                x["candidate_id"],
            )
        )
        registry = list(ranked[:CANDIDATE_CAP])
        if fold_id == "F5" or (fold_id == "F4" and not registry_primary) or (
            fold_id == "F1" and not registry_primary
        ):
            registry_primary = list(registry)

        selected = select_candidate_build_only(ranked)
        selected_id_before = selected["candidate_id"]
        assert_selected_in_registry(selected_id_before, registry)

        # Confirm: FULL canonical replay of confirm-day AM+PM partitions (fresh each; NO AM→PM carry)
        merged = replay_candidate_day_partitions(
            confirm_days,
            selected,
            mi,
            cache_dir=cdir,
            banner=banner,
        )
        metrics = merged["metrics"]
        assert_no_confirm_reselection(selected_id_before, selected["candidate_id"])

        # Persist per-fold registry 200 + SHA
        fold_dir = work / "folds" / fold_id
        fold_dir.mkdir(parents=True, exist_ok=True)
        reg_sha = sha256_obj(registry)
        write_json(fold_dir / "candidate_registry.json", registry)
        write_json(fold_dir / "candidate_registry_sha.json", {"sha256": reg_sha, "n": len(registry)})
        selected_spec = {
            "candidate_id": selected["candidate_id"],
            "selection_basis": selected["selection_basis"],
            "features": selected.get("features"),
            "direction": selected.get("direction"),
            "thresholds": selected.get("thresholds"),
            "family": selected.get("family"),
            "threshold_code": selected.get("threshold_code"),
        }
        selected_spec_sha = sha256_obj(selected_spec)
        write_json(fold_dir / "selected_candidate.json", {
            **selected_spec,
            "in_registry": True,
            "registry_sha256": reg_sha,
            "registry_sot_namespace": "CandidateRegistry_FULL_CAP200",
            "selected_spec_sha256": selected_spec_sha,
            "selected_spec_namespace": "SelectedSpec_BY_CANDIDATE_ID",
        })
        write_json(fold_dir / "decision_ledger.json", merged["decision_ledger"])
        write_json(fold_dir / "completed_trades.json", merged["completed_trades"])
        write_json(fold_dir / "censored_ledger.json", merged["censored_ledger"])
        write_json(fold_dir / "signal_ledger.json", merged.get("signal_ledger") or [])
        from research.e1_x6_provisional.canonical_partition_replay import (
            assert_signal_ledger_nonempty_when_decisions_or_trades,
        )

        assert_signal_ledger_nonempty_when_decisions_or_trades(
            signal_ledger=merged.get("signal_ledger") or [],
            decision_ledger=merged["decision_ledger"],
            completed_trades=merged["completed_trades"],
        )

        confirm_qc = [day_quality.get(d, "UNKNOWN") for d in confirm_days]
        fold_out[fold_id] = {
            "build_days": build_days,
            "confirm_days": confirm_days,
            "build_rows": len(build_rows),
            "confirm_rows": None,
            "candidates_enumerated": len(cands),
            "analysis_mask_ids": confirm_mask_ids,
            "fold_registry_sha256": reg_sha,
            "fold_registry_n": len(registry),
            "selected_in_registry": True,
            "selected_spec_sha256": selected_spec_sha,
            "selected_candidate": {
                **selected_spec,
                "selected_spec_sha256": selected_spec_sha,
            },
            "confirm_portfolio": {
                "evaluation_mode": EVALUATION_MODE_REQUIRED,
                "not_row_level_label_pnl": True,
                "am_pm_carry": False,
                "completed_trades": metrics["n"],
                "pnl": metrics["pnl"],
                "pf": metrics["pf"],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "draws": metrics["draws"],
                "max_dd": metrics["max_dd"],
                "exit_reason_counts": metrics["exit_reason_counts"],
                "cap_blocked": metrics["cap_blocked"],
                "duplicate_open_symbol_reject": metrics.get("same_symbol_blocked", 0),
                "open_at_end_n": metrics.get("open_at_end_n", 0),
                "open_at_end_symbols": metrics.get("open_at_end_symbols") or [],
                "censored_n": metrics.get("censored_n", 0),
                "noise_audit": {},
                "signal_ledger_sha256": metrics.get("signal_ledger_sha256"),
                "portfolio_decision_ledger_sha256": metrics["decision_ledger_sha256"],
                "completed_trade_ledger_sha256": metrics["completed_trade_ledger_sha256"],
                "censored_ledger_sha256": metrics.get("censored_ledger_sha256"),
                "completed_trades_detail": merged["completed_trades"],
                "decision_ledger": merged["decision_ledger"],
                "signal_ledger": merged.get("signal_ledger") or [],
                "censored_ledger": merged["censored_ledger"],
            },
            "confirm_quality_classes": confirm_qc,
            "status": "EXECUTED_FULL_CANONICAL_CONFIRM",
            "banner": banner,
            "adoption": banner,
            "note": (
                "row-level labels used for BUILD ranking only; "
                "confirm PnL is FULL_CANONICAL_EVENT_REPLAY per analysis_mask partition"
            ),
        }
        progress(
            f"P2: {fold_id} selected={selected_id_before[:48]}... "
            f"confirm_trades={metrics['n']} pnl={metrics['pnl']} "
            f"censored={metrics.get('censored_n')} open_at_end={metrics.get('open_at_end_n')}"
        )

    (work / "candidates").mkdir(parents=True, exist_ok=True)
    write_json(work / "candidates" / "registry.json", registry_primary[:CANDIDATE_CAP])
    return fold_out



def _tag_trades_quality(
    trades: list[dict[str, Any]],
    day: str,
    wq: dict[tuple[str, str], str],
    day_quality: dict[str, str],
    *,
    mask_index: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """Tag each trade with quality_class from analysis mask (mask-in only)."""
    out = []
    mi = mask_index or {}
    for t in trades:
        t2 = dict(t)
        et = parse_ts(t.get("entry_time"))
        if mi and et is not None:
            mf = row_in_analysis_mask(day, et, mi)
            if not mf.get("in_analysis_mask"):
                # Should already be filtered at adopt; skip residual
                continue
            t2["quality_class"] = mf.get("quality_class") or day_quality.get(day, "UNKNOWN")
            t2["am_pm"] = mf.get("session_class")
            t2["analysis_mask_id"] = mf.get("analysis_mask_id")
            t2["in_analysis_mask"] = True
            t2["valid_window_start"] = mf.get("valid_window_start")
            t2["valid_window_end"] = mf.get("valid_window_end")
        else:
            am_pm = classify_ts(day, et) if et else None
            if am_pm in ("AM", "PM") and (day, am_pm) in wq:
                qc = wq[(day, am_pm)]
            elif am_pm in ("LUNCH", "AFTER", "BEFORE"):
                continue  # outside paper windows — not usable
            else:
                qc = day_quality.get(day, "UNKNOWN")
            if qc == "INVALID_SOURCE":
                continue
            t2["quality_class"] = qc
            t2["am_pm"] = am_pm
            t2["in_analysis_mask"] = True
        t2["day"] = t2.get("day") or day
        out.append(t2)
    return out


def run_p2_pass(
    work: Path,
    *,
    pass_name: str,
    resume: bool = False,
    source_manifest: Optional[dict[str, Any]] = None,
    banner: str = PROVISIONAL_BANNER,
    run_entry_robustness: bool = False,
) -> dict[str, Any]:
    from research.e1_x6_provisional.util import read_json

    progress(f"P2 pass={pass_name}: start resume={resume}")
    verify_frozen_e1_x5_cost_contract()
    # A/B isolation: separate norm caches (sequential A then B preferred for memory)
    cache_dir = norm_cache_dir(pass_name)
    orig = _patch_normalize_cache(cache_dir)

    # Derive day/window quality from Source Manifest (not hardcoded-only)
    if source_manifest is None:
        p0_path = work.parent / "p0_source_manifest.json" if work.name.startswith("run_") else work / "p0_source_manifest.json"
        # work is temp_work_root/run_id; pass dir is work/run_a
        # Prefer sibling p0 at run root
        run_root = work if (work / "p0_source_manifest.json").is_file() else work.parent
        p0_path = run_root / "p0_source_manifest.json"
        if p0_path.is_file():
            source_manifest = read_json(p0_path)
        else:
            source_manifest = {"windows": []}

    windows = list(source_manifest.get("windows") or [])
    wq = window_quality_map(windows)
    day_quality = day_quality_from_windows(windows)
    mask_index = build_mask_index(source_manifest)
    # Ensure all DAYS have an entry
    for d in DAYS:
        day_quality.setdefault(d, "UNKNOWN")

    day_results = {}
    try:
        for day in DAYS:
            include_core = include_in_core_base(day_quality.get(day, "UNKNOWN"))
            summary_p = work / pass_name / "base" / f"{day}_summary.json"
            label_p = work / pass_name / "labels" / f"{day}_label_summary.json"
            trades_p = work / pass_name / "base" / f"{day}_trades.json"
            if resume and summary_p.is_file() and label_p.is_file() and trades_p.is_file():
                day_results[day] = read_json(summary_p)
                # Re-tag trades if quality_class missing
                trades = read_json(trades_p)
                if trades and ("quality_class" not in trades[0] or "in_analysis_mask" not in trades[0]):
                    trades = _tag_trades_quality(trades, day, wq, day_quality, mask_index=mask_index)
                    write_json(trades_p, trades)
                progress(f"P2: resume skip replay day={day}")
                continue
            day_results[day] = process_day(
                day,
                work=work / pass_name,
                include_in_core_base=include_core,
                mask_index=mask_index,
                banner=banner,
                cache_dir=cache_dir,
            )
            trades = read_json(work / pass_name / "base" / f"{day}_trades.json")
            trades = _tag_trades_quality(trades, day, wq, day_quality, mask_index=mask_index)
            write_json(work / pass_name / "base" / f"{day}_trades.json", trades)
        # labels
        label_summaries = {}
        for day in DAYS:
            label_p = work / pass_name / "labels" / f"{day}_label_summary.json"
            if resume and label_p.is_file():
                label_summaries[day] = read_json(label_p)
                progress(f"P2: resume skip label day={day}")
                continue
            trades = read_json(work / pass_name / "base" / f"{day}_trades.json")
            label_summaries[day] = label_score_rows(
                day, work / pass_name, trades, banner=banner
            )
        folds = evaluate_folds(
            work / pass_name,
            day_quality,
            banner=banner,
            mask_index=mask_index,
            source_manifest=source_manifest,
            cache_dir=cache_dir,
        )

        trades_by_day = {
            d: read_json(work / pass_name / "base" / f"{d}_trades.json") for d in DAYS
        }
        quality_summary = summarize_quality_layers(
            trades_by_day, day_quality, summarize_pnls=summarize_pnls
        )

        def ledger_sha(trades):
            from small_paper.e1_x5_canonical_replay import trade_ledger_hash

            return trade_ledger_hash(list(trades))

        partial = quality_summary["PARTIAL_VALID_WINDOW"]
        stress = quality_summary["STRESS_RECOVERABLE"]
        all_u = quality_summary["ALL_USABLE"]
        core = quality_summary["CORE_VALID"]

        usable_trades = [
            t
            for d in DAYS
            for t in trades_by_day[d]
            if (t.get("quality_class") or day_quality.get(d)) in ALL_USABLE_CLASSES
        ]
        partial_trades = [
            t
            for d in DAYS
            for t in trades_by_day[d]
            if (t.get("quality_class") or day_quality.get(d)) == "PARTIAL_VALID_WINDOW"
        ]
        core_trades = [
            t
            for d in DAYS
            for t in trades_by_day[d]
            if (t.get("quality_class") or day_quality.get(d)) == "CORE_VALID"
        ]

        base_block = {
            "banner": banner,
            "role": "E1_X5_comparison_BASE",
            "not_runtime_oracle": True,
            "runtime_ledger_note": "results/small_paper/* must NOT be BASE oracle",
            "day_quality": day_quality,
            "window_quality": {f"{d}|{ap}": qc for (d, ap), qc in sorted(wq.items())},
            "per_day": {
                d: {
                    "completed_trades": day_results[d]["completed_trades"],
                    "pnl": day_results[d]["realized_pnl_yen_100"],
                    "ledger_sha256": day_results[d]["ledger_sha256"],
                    "day_label": day_results[d]["day_label"],
                    "include_in_core_base": include_in_core_base(day_quality.get(d, "UNKNOWN")),
                    "quality_class": day_quality.get(d, "UNKNOWN"),
                    "normalized_rows": day_results[d]["normalized_rows"],
                }
                for d in DAYS
                if d in day_results
            },
            "quality_layers": quality_summary,
            "CORE_VALID": core,
            "PARTIAL_VALID_WINDOW": {
                "trades_n": partial.get("trades_n"),
                "pnl": partial.get("pnl"),
                "ledger_sha256": ledger_sha(partial_trades),
                "metrics": partial.get("metrics"),
                "note": "do NOT call this CORE",
            },
            "STRESS_RECOVERABLE": {
                "trades_n": stress.get("trades_n"),
                "pnl": stress.get("pnl"),
                "metrics": stress.get("metrics"),
            },
            "ALL_USABLE_trades_n": all_u.get("trades_n"),
            "ALL_USABLE_pnl": all_u.get("pnl"),
            "ALL_USABLE_ledger_sha256": ledger_sha(usable_trades),
            "ALL_USABLE_metrics": all_u.get("metrics"),
            "CORE_layer_trades_n": core.get("trades_n"),
            "CORE_layer_pnl": core.get("pnl"),
            "CORE_layer_status": core.get("status"),
            "CORE_layer_note": "CORE_VALID only; PARTIAL never relabeled CORE; INVALID excluded",
            "ex_20260722_metrics": {
                "PARTIAL_VALID_WINDOW": (
                    summarize_pnls(
                        [
                            float(t["net_pnl_yen_100"])
                            for t in partial_trades
                            if str(t.get("day")) != "20260722"
                        ]
                    )
                    if any(str(t.get("day")) != "20260722" for t in partial_trades)
                    else {"status": "NOT_EVALUABLE", "n": 0, "pnl": None}
                ),
                "CORE_VALID": (
                    summarize_pnls(
                        [
                            float(t["net_pnl_yen_100"])
                            for t in core_trades
                            if str(t.get("day")) != "20260722"
                        ]
                    )
                    if any(str(t.get("day")) != "20260722" for t in core_trades)
                    else {"status": "NOT_EVALUABLE", "n": 0, "pnl": None}
                ),
                "ALL_USABLE": (
                    summarize_pnls(
                        [
                            float(t["net_pnl_yen_100"])
                            for t in usable_trades
                            if str(t.get("day")) != "20260722"
                        ]
                    )
                    if any(str(t.get("day")) != "20260722" for t in usable_trades)
                    else {"status": "NOT_EVALUABLE", "n": 0, "pnl": None}
                ),
            },
        }

        # dataset / label SHAs
        ds_counter = 0
        for day in DAYS:
            p = work / pass_name / "dataset" / f"{day}_score.jsonl"
            if p.is_file():
                ds_counter += sum(1 for _ in open(p, "r", encoding="utf-8"))
        label_counter = sum(int(label_summaries[d]["rows"]) for d in DAYS if d in label_summaries)

        dataset_sha = sha256_obj(
            {
                d: sha256_text(
                    Path(work / pass_name / "dataset" / f"{d}_score.jsonl").read_text(encoding="utf-8")
                    if (work / pass_name / "dataset" / f"{d}_score.jsonl").is_file()
                    else ""
                )
                for d in DAYS
            }
        )
        label_sha = sha256_obj(
            {
                d: sha256_text(
                    Path(work / pass_name / "labels" / f"{d}_labeled.jsonl").read_text(encoding="utf-8")
                    if (work / pass_name / "labels" / f"{d}_labeled.jsonl").is_file()
                    else ""
                )
                for d in DAYS
            }
        )
        cand_reg_path = work / pass_name / "candidates" / "registry.json"
        cand_sha = sha256_text(cand_reg_path.read_text(encoding="utf-8")) if cand_reg_path.is_file() else None
        cand_n = 0
        if cand_reg_path.is_file():
            cand_n = len(read_json(cand_reg_path))
        fold_sha = sha256_obj(
            {
                fid: (fr.get("confirm_portfolio") or {}).get("completed_trade_ledger_sha256")
                for fid, fr in folds.items()
                if isinstance(fr, dict)
            }
        )

        entry_rob = None
        if run_entry_robustness:
            from research.e1_x6_provisional.entry_robustness import evaluate_entry_robustness

            entry_rob = evaluate_entry_robustness(
                work / pass_name,
                folds=folds,
                base=base_block,
                source_manifest=source_manifest,
                day_quality=day_quality,
                banner=banner,
                cache_dir=cache_dir,
            )
            # Prefer final registry if produced
            final_reg = work / pass_name / "candidates" / "registry_final.json"
            if final_reg.is_file():
                reg_final = read_json(final_reg)
                write_json(cand_reg_path, reg_final[:CANDIDATE_CAP])
                cand_n = len(reg_final[:CANDIDATE_CAP])
                cand_sha = sha256_text(cand_reg_path.read_text(encoding="utf-8"))

        out = {
            "pass_name": pass_name,
            "base": base_block,
            "dataset": {
                "rows": ds_counter,
                "sha256": dataset_sha,
                "label_summaries": label_summaries,
            },
            "labels": {"rows": label_counter, "sha256": label_sha},
            "candidates": {
                "registry_sha256": cand_sha,
                "registry_sot_namespace": "CandidateRegistry_FULL_CAP200",
                "cap": CANDIDATE_CAP,
                "count": cand_n,
            },
            "folds": folds,
            "fold_ledger_sha256": fold_sha,
            "entry_robustness": entry_rob,
            "counters": {
                "dataset_rows": ds_counter,
                "label_rows": label_counter,
                "partial_valid_trades": partial.get("trades_n"),
                "stress_trades": stress.get("trades_n"),
                "all_usable_trades": all_u.get("trades_n"),
                "core_valid_trades": core.get("trades_n"),
                "invalid_source_trades": (quality_summary.get("INVALID_SOURCE") or {}).get("trades_n"),
                "candidate_registry": cand_n,
            },
            "banner": banner,
            "safety": {"submit": 0, "cancel": 0, "live": 0},
        }
        write_json(work / pass_name / "pass_summary.json", out)
        progress(f"P2 pass={pass_name}: complete")
        return out
    finally:
        _restore_normalize(orig)
