"""Large-rise episode extraction and miss-reason audit on Watch50 panel."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Mapping, Optional, Sequence

from research.pbv2_zero_base_revalidation.constants import (
    LARGE_RISE_MFE_10M_PCT,
    LARGE_RISE_MFE_15M_PCT,
    LARGE_RISE_MFE_5M_PCT,
)
from research.pbv2_zero_base_revalidation.panel import CandidateRow, PricePoint, price_window
from research.pbv2_zero_base_revalidation.util import parse_ts


def extract_large_rise_episodes(
    panel: Sequence[CandidateRow],
    price_paths: Mapping[tuple[str, str], list[PricePoint]],
) -> list[dict[str, Any]]:
    """Episode starts: local times where 5/10/15m MFE crosses thresholds, deduped per symbol/30m."""
    episodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    # Use panel rows as evaluation grid (Watch50 coverage)
    for row in panel:
        if not getattr(row, "large_rise_evaluable", True):
            continue
        mfe5 = row.forward.get("forward_MFE_5m")
        mfe10 = row.forward.get("forward_MFE_10m")
        mfe15 = row.forward.get("forward_MFE_15m")
        mae_pre = row.forward.get("forward_MAE_5m")
        hit = bool(
            (mfe5 is not None and mfe5 >= LARGE_RISE_MFE_5M_PCT)
            or (mfe10 is not None and mfe10 >= LARGE_RISE_MFE_10M_PCT)
            or (mfe15 is not None and mfe15 >= LARGE_RISE_MFE_15M_PCT)
        )
        if not hit:
            continue
        bucket = int(row.evaluation_time.timestamp() // 1800)
        key = (row.day, row.symbol, bucket)
        if key in seen:
            continue
        seen.add(key)
        episodes.append(
            {
                "symbol": row.symbol,
                "day": row.day,
                "start_time": row.evaluation_time.isoformat(),
                "start_price": row.current_price,
                "mfe_5m": mfe5,
                "mfe_10m": mfe10,
                "mfe_15m": mfe15,
                "mae_before_or_early": mae_pre,
                "trailing_activation_proxy": bool((mfe5 or 0) >= 1.0),
                "cf_pnl_5bps": row.cf_pnl_5bps,
                "pbv2_candidate": row.pbv2_candidate,
                "pbv2_decision": row.pbv2_decision,
                "pbv2_score": row.pbv2_score,
                "reject_reason": row.reject_reason,
                "universe_source": row.universe_source,
                "board_quality": row.board_quality,
                "board_stale": bool(row.board_age_sec is not None and row.board_age_sec > 5),
                "price_stale": bool(row.price_age_sec is not None and row.price_age_sec > 5),
                "cap_blocked": row.cap_blocked,
                "feature_ready": row.evaluability in ("FEATURE_EVALUABLE", "OUTCOME_EVALUABLE", "PNL_EVALUABLE"),
                "board_ready": row.board_quality not in ("MISSING", "STALE", "FALLBACK_0_5"),
                "evaluation_ran": True,
                "push_received": True,  # candidate event implies push/eval path
                "in_universe": True,
            }
        )
    return episodes


def annotate_capture(
    episodes: list[dict[str, Any]],
    panel: Sequence[CandidateRow],
    *,
    zero_base_keep: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    # index panel by day,symbol for capture latency
    by_sym: dict[tuple[str, str], list[CandidateRow]] = defaultdict(list)
    for r in panel:
        by_sym[(r.day, r.symbol)].append(r)
    for e in episodes:
        day, sym = e["day"], e["symbol"]
        start = parse_ts(e["start_time"])
        rows = by_sym.get((day, sym)) or []
        pbv2_lat = None
        zb_lat = None
        zb_rank = None
        miss = "unknown"
        for r in rows:
            if start is None:
                break
            dt = (r.evaluation_time - start).total_seconds()
            if dt < -5:
                continue
            if dt > 600:
                break
            if r.pbv2_decision or r.accept:
                if pbv2_lat is None:
                    pbv2_lat = max(0.0, dt)
            if zero_base_keep and _row_matches_rule(r, zero_base_keep):
                if zb_lat is None:
                    zb_lat = max(0.0, dt)
                    zb_rank = 1
        e["pbv2_capture_sec"] = pbv2_lat
        e["zero_base_capture_sec"] = zb_lat
        e["zero_base_rank"] = zb_rank
        if pbv2_lat is None:
            # classify miss
            sample = next((r for r in rows if start and abs((r.evaluation_time - start).total_seconds()) < 120), None)
            if sample is None:
                miss = "unknown"
            elif sample.cap_blocked:
                miss = "cap_blocked"
            elif sample.board_quality == "STALE" or (sample.board_age_sec or 0) > 5:
                miss = "board_stale"
            elif (sample.price_age_sec or 0) > 5:
                miss = "price_stale"
            elif sample.pbv2_score is not None and sample.pbv2_score < 5:
                miss = "score_insufficient"
            elif sample.reject_reason:
                miss = sample.reject_reason[:80]
            else:
                miss = "not_selected"
        else:
            miss = "captured"
        e["miss_reason"] = miss
        e["zero_base_candidate"] = zb_lat is not None
        e["pbv2_candidate_flag"] = pbv2_lat is not None
    return episodes


def _row_matches_rule(row: CandidateRow, rule: Mapping[str, Any]) -> bool:
    feats = rule.get("features") or []
    ops = rule.get("ops") or []
    thrs = rule.get("last_thresholds") or []
    if not feats or len(feats) != len(thrs):
        return False
    for k, op, thr in zip(feats, ops, thrs):
        v = row.features.get(k)
        if v is None:
            return False
        if op == ">=" and not (float(v) >= float(thr)):
            return False
        if op == "<=" and not (float(v) <= float(thr)):
            return False
    return True


def summarize_capture(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(episodes)
    def rate(sec_key: str, limit: float) -> Optional[float]:
        if not n:
            return None
        return round(sum(1 for e in episodes if e.get(sec_key) is not None and float(e[sec_key]) <= limit) / n, 4)

    pbv2_cap = sum(1 for e in episodes if e.get("pbv2_candidate_flag"))
    zb_cap = sum(1 for e in episodes if e.get("zero_base_candidate"))
    miss_counts: dict[str, int] = defaultdict(int)
    for e in episodes:
        miss_counts[str(e.get("miss_reason") or "unknown")] += 1
    return {
        "large_rise_episode_total": n,
        "capture_30s_pbv2": rate("pbv2_capture_sec", 30),
        "capture_60s_pbv2": rate("pbv2_capture_sec", 60),
        "capture_5m_pbv2": rate("pbv2_capture_sec", 300),
        "capture_10m_pbv2": rate("pbv2_capture_sec", 600),
        "uncaptured_pbv2": n - pbv2_cap,
        "pbv2_capture_rate": round(pbv2_cap / n, 4) if n else None,
        "zero_base_capture_rate": round(zb_cap / n, 4) if n else None,
        "random_baseline_note": "random baseline ≈ candidate_rate of panel; reported separately",
        "miss_reason_counts": dict(sorted(miss_counts.items(), key=lambda x: -x[1])),
    }
