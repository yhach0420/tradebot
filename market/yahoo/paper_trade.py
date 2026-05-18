"""
Shared paper_trade / Phase2 / divergence / shadow-validation helpers.

Imported lazily from `market.yahoo.watch` via `__getattr__` to avoid import cycles.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Logic / engine versions (continuation-v1)
# ---------------------------------------------------------------------------
PAPER_TRADE_LOGIC_VERSION = "2026-05-15-continuation-v1"
REPLAY_PAPER_TRADE_LOGIC_VERSION = "2026-05-15-continuation-v1"
PAPER_TRADE_DRY_RUN_LOGIC_VERSION = "2026-05-15-continuation-v1"
SHARED_SIGNAL_ENGINE_VERSION = "entry-cross-v1"
SHARED_EXIT_ENGINE_VERSION = "paper-position-exec-v1"

STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

JST = timezone(timedelta(hours=9))


def _run_replay_validate_params(replay_range: str, replay_date_fixed: str) -> tuple[int, str]:
    rr = str(replay_range or "").strip().lower()
    rf = str(replay_date_fixed or "").strip()
    if rr == "fixed":
        if not rf:
            return 2, "replay_range_invalid"
        try:
            datetime.strptime(rf, "%Y-%m-%d")
        except ValueError:
            return 2, "replay_range_invalid"
        return 0, ""
    return 2, "replay_range_invalid"


def _parse_replay_shadow_multi_day_list(raw: str) -> tuple[list[str], str]:
    s = str(raw or "").strip()
    if not s:
        return [], "empty"
    out: list[str] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            datetime.strptime(p, "%Y-%m-%d")
        except ValueError:
            return [], f"invalid_date:{p}"
        out.append(p)
    return out, ""


def _chase_extension_bucket(pct: float) -> str:
    if pct < 0.3:
        return "lt_0.3"
    if pct < 0.5:
        return "0.3_0.5"
    if pct < 0.7:
        return "0.5_0.7"
    if pct < 1.0:
        return "0.7_1.0"
    return "ge_1.0"


def _compute_market_context_scores(
    *,
    rising_ratio: Optional[float],
    high_ratio: Optional[float],
    topix_chg: Optional[float],
    market_regime: str,
    fail_rate30: Optional[float],
) -> dict[str, float]:
    rr = float(rising_ratio) if isinstance(rising_ratio, (int, float)) else 0.5
    fr = float(fail_rate30) if isinstance(fail_rate30, (int, float)) else 0.0
    tp = float(topix_chg) if isinstance(topix_chg, (int, float)) else 0.0
    hr = float(high_ratio) if isinstance(high_ratio, (int, float)) else 0.5
    weakness = 0.0
    weakness += max(0.0, (-tp) / 3.0) * 0.35
    weakness += max(0.0, (0.5 - rr)) * 2.0 * 0.25
    weakness += max(0.0, fr - 0.5) * 2.0 * 0.2
    if str(market_regime).upper() in ("WEAK", "CRASH"):
        weakness += 0.35
        if str(market_regime).upper() == "CRASH":
            weakness += 0.2
    weakness = float(min(1.0, weakness))
    lt50 = max(0.0, min(1.0, 1.0 - rr))
    return {
        "market_weakness_score": weakness,
        "market_breadth_score": rr,
        "market_trend_pressure_score": hr,
        "lt50_ratio": lt50,
    }


def _baseline_pnl(xs: list[dict[str, Any]]) -> float:
    return float(sum(float(x.get("pnl_yen_100_shares") or 0.0) for x in xs))


def _shadow_filter_table_row(
    cohort: list[dict[str, Any]],
    *,
    threshold: str,
    match_fn: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    blocked = [x for x in cohort if match_fn(x)]
    kept = [x for x in cohort if not match_fn(x)]
    base = _baseline_pnl(cohort)
    after = _baseline_pnl(kept)
    ec = _shadow_exit_removed_counts(blocked)
    return {
        "threshold": threshold,
        "blocked_count": int(len(blocked)),
        "blocked_pnl": _baseline_pnl(blocked),
        "pnl_after_block": float(after),
        "pnl_improvement": float(after - base),
        **ec,
    }


def _shadow_exit_removed_counts(xs: list[dict[str, Any]]) -> dict[str, int]:
    tk = st = vw = 0
    for s in xs:
        er = str(s.get("exit_reason") or "").upper()
        if "TAKE" in er and "VWAP" not in er:
            tk += 1
        elif er.startswith("STOP") or er == "STOP_HIT":
            st += 1
        elif "VWAP" in er:
            vw += 1
    return {
        "take_removed": int(tk),
        "stop_removed": int(st),
        "vwap_exit_removed": int(vw),
    }


def _mdf_float(s: dict[str, Any], key: str) -> Optional[float]:
    m = s.get("momentum_decay_features")
    if isinstance(m, dict) and isinstance(m.get(key), (int, float)):
        return float(m[key])
    return None


def _eval_base_signal_dicts_for_extension(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(x)
        for x in (xs or [])
        if isinstance(x, dict)
        and str(x.get("position_kind") or "BASE") == "BASE"
        and not bool(x.get("excluded_from_eval"))
    ]


def _rollup_eval_base_signal_dict_metrics(cohort: list[dict[str, Any]]) -> dict[str, Any]:
    if not cohort:
        return {"total_pnl_yen_100_shares": 0.0}
    return {"total_pnl_yen_100_shares": _baseline_pnl(cohort)}


def _chase_match(s: dict[str, Any], thr_pct: float) -> bool:
    mdf = s.get("momentum_decay_features") if isinstance(s.get("momentum_decay_features"), dict) else {}
    if not bool(mdf.get("prev_signal_exists", False)):
        return False
    pc = _mdf_float(s, "price_change_pct_from_prev_signal")
    if pc is None and isinstance(mdf.get("price_change_pct_from_prev_signal"), (int, float)):
        pc = float(mdf["price_change_pct_from_prev_signal"])
    return pc is not None and float(pc) >= float(thr_pct)


def _signal_structure_relaxed_shadow_candidate(s: dict[str, Any]) -> bool:
    sel = str(s.get("take_structure_selection") or "").upper()
    if sel in ("STRUCTURE", "STRUCTURE_RELAXED"):
        return False
    rej = str(s.get("structure_take_reject_reason") or "").strip()
    if not rej or rej in ("STRUCTURE_TAKE_DISABLED_OR_OFF", "NO_RESISTANCE_ABOVE_ENTRY_GAP"):
        return False
    nr = s.get("nearest_resistance")
    ep = float(s.get("entry_price") or 0.0)
    if isinstance(nr, (int, float)) and float(nr) > ep:
        return True
    return bool(rej)


def _build_replay_shadow_filter_validation(signal_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    cohort = _eval_base_signal_dicts_for_extension(signal_dicts or [])
    base_m = dict(_rollup_eval_base_signal_dict_metrics(cohort))
    chase_rows: list[dict[str, Any]] = []
    for thr in (0.3, 0.5, 0.7, 1.0):
        chase_rows.append(
            _shadow_filter_table_row(
                cohort,
                threshold=f">={thr:.1f}%",
                match_fn=lambda s, t=thr: _chase_match(s, t),
            )
        )
    chase_autoblock_rows: list[dict[str, Any]] = []
    for thr in (0.3, 0.5, 0.7, 1.0):
        chase_autoblock_rows.append(
            _shadow_filter_table_row(
                cohort,
                threshold=f"CHASE_EXTENSION_BLOCK>={thr:.1f}%",
                match_fn=lambda s, t=thr: (
                    isinstance(s.get("chase_extension_pct"), (int, float))
                    and float(s.get("chase_extension_pct")) >= float(t)
                ),
            )
        )

    cooldown_rows: list[dict[str, Any]] = []
    for sec in (180, 300, 600):

        def _cool_fn(_s: dict[str, Any]) -> bool:
            return False

        cooldown_rows.append(
            {
                "cooldown_sec": int(sec),
                "blocked_count": 0,
                "blocked_pnl": 0.0,
                "pnl_after_block": float(base_m.get("total_pnl_yen_100_shares") or 0.0),
                "pnl_improvement": 0.0,
                "take_removed": 0,
                "stop_removed": 0,
                "vwap_exit_removed": 0,
            }
        )

    hu_vol_rows: list[dict[str, Any]] = [
        _shadow_filter_table_row(
            cohort,
            threshold="HU>=5 & vol_eff<0.3",
            match_fn=lambda s: int(s.get("high_update_count_before_entry") or 0) >= 5
            and ((_mdf_float(s, "volume_efficiency_pct") or 999.0) < 0.3),
        ),
        _shadow_filter_table_row(
            cohort,
            threshold="HU>=5 & vol_cont<0.4",
            match_fn=lambda s: int(s.get("high_update_count_before_entry") or 0) >= 5
            and float(s.get("breakout_volume_continuation_score") or 0.5) < 0.4,
        ),
    ]

    eq_rows: list[dict[str, Any]] = []
    for thr in (0.30, 0.35, 0.40, 0.45):
        eq_rows.append(
            _shadow_filter_table_row(
                cohort,
                threshold=f"<{thr:.2f}",
                match_fn=lambda s, t=thr: float(s.get("entry_quality_score") or 0.5) < float(t),
            )
        )

    relaxed_candidates = [s for s in cohort if _signal_structure_relaxed_shadow_candidate(s)]
    relaxed_m = dict(_rollup_eval_base_signal_dict_metrics(relaxed_candidates))
    relaxed_virtual_take = sum(
        1 for s in relaxed_candidates if "TAKE" in str(s.get("exit_reason") or "").upper()
    )
    relaxed_virtual_vwap_saved = sum(
        1
        for s in relaxed_candidates
        if "VWAP" in str(s.get("exit_reason") or "").upper()
        and float(s.get("pnl_yen_100_shares") or 0.0) < 0
    )
    dynamic_low_rr_rows: list[dict[str, Any]] = []
    for thr in (0.15, 0.20, 0.30):
        dynamic_low_rr_rows.append(
            _shadow_filter_table_row(
                cohort,
                threshold=f"shadow_dynamic_low_rr<{thr:.2f}",
                match_fn=lambda s, t=thr: (
                    str(s.get("take_structure_selection") or "").upper() == "DYNAMIC"
                    and float(s.get("structure_take_best_rr") or 999.0) < float(t)
                ),
            )
        )
    reject_counts: dict[str, int] = {}
    for s in cohort:
        rej = str(s.get("structure_take_reject_reason") or "").strip()
        if rej:
            reject_counts[rej] = int(reject_counts.get(rej, 0)) + 1
    mkt_rows: list[dict[str, Any]] = []
    for lab, fn in (
        ("market_weakness>0.7", lambda s: float(s.get("market_weakness_score") or 0.0) > 0.7),
        ("rising_ratio<0.4", lambda s: float(s.get("market_breadth_score") or 1.0) < 0.4),
        ("lt50>0.7", lambda s: float(s.get("lt50_ratio") or 0.0) > 0.7),
    ):
        mkt_rows.append(_shadow_filter_table_row(cohort, threshold=lab, match_fn=fn))
    vwap_risk_buckets: dict[str, list[dict[str, Any]]] = {"take_hit": [], "stop_hit": [], "vwap_exit": []}
    for s in cohort:
        er = str(s.get("exit_reason") or "").upper()
        if "TAKE" in er and "VWAP" not in er:
            vwap_risk_buckets["take_hit"].append(s)
        elif er in ("STOP_HIT", "STOP"):
            vwap_risk_buckets["stop_hit"].append(s)
        elif "VWAP" in er:
            vwap_risk_buckets["vwap_exit"].append(s)

    def _avg_risk(zs: list[dict[str, Any]]) -> float:
        if not zs:
            return 0.0
        return float(sum(float(x.get("vwap_break_early_risk_score") or 0.0) for x in zs) / len(zs))

    return {
        "baseline": base_m,
        "shadow_chase_extension_table": chase_rows,
        "shadow_chase_extension_autoblock_table": chase_autoblock_rows,
        "shadow_hu_volume_exhaustion_table": hu_vol_rows,
        "shadow_same_symbol_cooldown_table": cooldown_rows,
        "shadow_market_weakness_block_table": mkt_rows,
        "shadow_entry_quality_filter_table": eq_rows,
        "shadow_structure_relaxed_candidate_count": int(len(relaxed_candidates)),
        "relaxed_shadow_selected": int(len(relaxed_candidates)),
        "shadow_structure_relaxed_virtual_pnl": float(relaxed_m.get("total_pnl_yen_100_shares") or 0.0),
        "relaxed_shadow_virtual_pnl": float(relaxed_m.get("total_pnl_yen_100_shares") or 0.0),
        "shadow_structure_relaxed_virtual_take_hit": int(relaxed_virtual_take),
        "relaxed_shadow_virtual_take_hit": int(relaxed_virtual_take),
        "relaxed_shadow_virtual_vwap_exit_saved": int(relaxed_virtual_vwap_saved),
        "structure_reject_reason_counts": dict(sorted(reject_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "shadow_dynamic_low_rr_filter": dynamic_low_rr_rows,
        "vwap_break_early_risk_summary": {
            "avg_vwap_break_early_risk_take_hit": _avg_risk(vwap_risk_buckets["take_hit"]),
            "avg_vwap_break_early_risk_stop_hit": _avg_risk(vwap_risk_buckets["stop_hit"]),
            "avg_vwap_break_early_risk_vwap_exit": _avg_risk(vwap_risk_buckets["vwap_exit"]),
        },
        "note": "shadow-only; does not change excluded_from_eval or live entry",
    }


def _aggregate_multi_day_shadow_table_rows(
    day_reports: list[dict[str, Any]], *, table_key: str, label_key: str
) -> list[dict[str, Any]]:
    by_label: dict[str, list[dict[str, Any]]] = {}
    for dr in day_reports:
        if not isinstance(dr, dict):
            continue
        sv = dr.get("shadow_validation") if isinstance(dr.get("shadow_validation"), dict) else {}
        rows = sv.get(table_key) or []
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            lab = str(r.get(label_key) or "")
            by_label.setdefault(lab, []).append(r)
    out: list[dict[str, Any]] = []
    for lab, rs in sorted(by_label.items()):
        imps = [float(x.get("pnl_improvement") or 0.0) for x in rs]
        days_pos = sum(1 for x in imps if x > 0)
        out.append(
            {
                str(label_key): lab,
                "avg_improvement": float(sum(imps) / len(imps)) if imps else 0.0,
                "days_positive_ratio": float(days_pos) / float(len(imps)) if imps else 0.0,
                "worst_day_improvement": float(min(imps)) if imps else 0.0,
            }
        )
    return out


def _build_multi_day_shadow_summary(day_reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "multi_day_shadow_summary": _aggregate_multi_day_shadow_table_rows(
            day_reports, table_key="shadow_chase_extension_table", label_key="threshold"
        ),
        "multi_day_cooldown_summary": _aggregate_multi_day_shadow_table_rows(
            day_reports, table_key="shadow_same_symbol_cooldown_table", label_key="cooldown_sec"
        ),
        "multi_day_market_weakness_summary": _aggregate_multi_day_shadow_table_rows(
            day_reports, table_key="shadow_market_weakness_block_table", label_key="threshold"
        ),
        "multi_day_chase_autoblock_summary": _aggregate_multi_day_shadow_table_rows(
            day_reports, table_key="shadow_chase_extension_autoblock_table", label_key="threshold"
        ),
        "days_included": [str(dr.get("day_jst") or "") for dr in day_reports if isinstance(dr, dict)],
    }


def _enrich_signal_dicts_quality_ranks(signal_dicts: list[dict[str, Any]]) -> None:
    by_group: dict[tuple[str, str], list[int]] = {}
    for i, s in enumerate(signal_dicts or []):
        if not isinstance(s, dict):
            continue
        g = (str(s.get("day_jst") or ""), str(s.get("symbol") or ""))
        by_group.setdefault(g, []).append(i)
    for _g, ix in by_group.items():
        idxs = list(ix)
        scored = sorted(
            idxs,
            key=lambda j: float((signal_dicts[j] or {}).get("entry_quality_score") or 0.5),
        )
        n = len(scored)
        for rank_pos, j in enumerate(scored):
            score = float((signal_dicts[j] or {}).get("entry_quality_score") or 0.5)
            signal_dicts[j]["quality_rank_in_symbol"] = float(rank_pos) / float(max(1, n - 1)) if n > 1 else 0.5
            signal_dicts[j]["quality_percentile"] = float(rank_pos) / float(max(1, n - 1)) if n > 1 else 0.5


def run_replay_multi_day_shadow_validation_impl(
    *,
    days: list[str],
    replay_config_path: str,
    run_replay_fn: Any,
    script_dir: str,
) -> int:
    if not days:
        print("[replay-shadow-multi-day] empty days")
        return 2
    batch = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    collect: list[dict[str, Any]] = []
    import market.yahoo.watch as yk

    cfg_raw = yk._load_replay_config(replay_config_path)
    cfg_flags = yk._apply_replay_config_to_flags(cfg=cfg_raw)
    for i_d, day in enumerate(days):
        kw = dict(
            interval_sec=1.0,
            only_changes=False,
            fixed_watch=None,
            replay_range="1d",
            replay_random_days=0,
            replay_random_months=3,
            replay_seed=None,
            replay_mode="normal",
            replay_fast_discord=True,
            replay_fast_verbose=False,
            replay_fast_print_signal_details=False,
            replay_market_debug=False,
            replay_repeat_run_no=int(i_d + 1),
            replay_repeat_total=int(len(days)),
            replay_output_subdir=f"replay_shadow_multi_{batch}",
            replay_batch_stamp=batch,
            replay_morning_screen_hhmm="",
            one_trade_per_symbol_per_day=False,
            enable_add=False,
            replay_early_exit_before_stop=bool(cfg_flags.get("replay_early_exit_before_stop", False)),
            replay_early_exit_vwap=bool(cfg_flags.get("replay_early_exit_vwap", True)),
            replay_early_exit_recent_low=bool(cfg_flags.get("replay_early_exit_recent_low", True)),
            replay_disable_afternoon_entry=bool(cfg_flags.get("replay_disable_afternoon_entry", False)),
            replay_strict_afternoon_entry=bool(cfg_flags.get("replay_strict_afternoon_entry", False)),
            replay_afternoon_topix_weak_block=bool(cfg_flags.get("replay_afternoon_topix_weak_block", True)),
            replay_config_name=str(cfg_flags.get("replay_config_name") or ""),
            replay_config_path=str(replay_config_path),
            aft_volume_spike_ratio_min=float(cfg_flags["aft_volume_spike_ratio_min"]),
            aft_vwap_dist_pct_max=float(cfg_flags["aft_vwap_dist_pct_max"]),
            aft_rebreak_mult=float(cfg_flags["aft_rebreak_mult"]),
            entry_filter_rsi_enabled=bool(cfg_flags["entry_filter_rsi_enabled"]),
            entry_filter_rsi_exclude_above=float(cfg_flags["entry_filter_rsi_exclude_above"]),
            entry_filter_vwap_distance_enabled=bool(cfg_flags["entry_filter_vwap_distance_enabled"]),
            entry_filter_vwap_distance_exclude_above=float(cfg_flags["entry_filter_vwap_distance_exclude_above"]),
            entry_filter_atr_pct_enabled=bool(cfg_flags["entry_filter_atr_pct_enabled"]),
            entry_filter_atr_pct_exclude_above=float(cfg_flags["entry_filter_atr_pct_exclude_above"]),
            daily_loss_stop_enabled=bool(cfg_flags.get("daily_loss_stop_enabled", False)),
            daily_loss_stop_threshold_yen_100_shares=float(
                cfg_flags.get("daily_loss_stop_threshold_yen_100_shares", 50_000.0)
            ),
            regime_filter_disable_morning_weak=bool(cfg_flags.get("regime_filter_disable_morning_weak", False)),
            regime_filter_disable_rising_ratio_lt50=bool(
                cfg_flags.get("regime_filter_disable_rising_ratio_lt50", False)
            ),
            regime_filter_disable_topix_weak=bool(cfg_flags.get("regime_filter_disable_topix_weak", False)),
            regime_filter_topix_weak_threshold_pct=cfg_flags.get("regime_filter_topix_weak_threshold_pct"),
            regime_filter_rising_ratio_threshold_pct=cfg_flags.get("regime_filter_rising_ratio_threshold_pct"),
            signal_filter_disable_gap_ge_pct=bool(cfg_flags.get("signal_filter_disable_gap_ge_pct", False)),
            signal_filter_gap_ge_threshold_pct=float(cfg_flags.get("signal_filter_gap_ge_threshold_pct", 3.0)),
            signal_filter_disable_vwap_distance_ge_pct=bool(
                cfg_flags.get("signal_filter_disable_vwap_distance_ge_pct", False)
            ),
            signal_filter_vwap_distance_ge_threshold_pct=float(
                cfg_flags.get("signal_filter_vwap_distance_ge_threshold_pct", 1.5)
            ),
            signal_filter_disable_entry_after_hhmm=bool(
                cfg_flags.get("signal_filter_disable_entry_after_hhmm", False)
            ),
            signal_filter_entry_after_hhmm=str(cfg_flags.get("signal_filter_entry_after_hhmm", "10:30")),
            **yk._replay_composite_signal_filter_kwargs_from_flags(cfg_flags),
            **yk._replay_regime_control_kwargs_from_flags(cfg_flags),
            replay_settings=None,
            paper_trade_mode=False,
            paper_trade_collect=None,
            forward_split_validation=False,
            forward_split_periods_path="",
            replay_date_fixed=str(day),
            use_paper_position_exec=bool(cfg_flags.get("use_paper_position_exec", True)),
            replay_shadow_collect=collect,
        )
        vc, vs = _run_replay_validate_params("fixed", day)
        if vc != 0:
            print(f"[replay-shadow-multi-day] validate failed: {vs}")
            return int(vc)
        code = int(run_replay_fn(**kw))
        if code != 0:
            return int(code)
    multi = _build_multi_day_shadow_summary(collect)
    out_dir = yk._build_results_output_dir("shadow_multi_day", batch, script_dir=script_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "multi_day_shadow_summary.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"days": collect, **multi}, f, ensure_ascii=False, indent=2)
    print(f"[replay-shadow-multi-day] saved {out_path}")
    return 0



from market.yahoo.paper_trade_extended import *  # noqa: F403