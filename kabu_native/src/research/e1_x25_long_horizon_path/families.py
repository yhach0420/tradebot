"""Discovery-only path family tags + evidence status (fixed rules; no retune)."""
from __future__ import annotations

from typing import Any, Optional

# Rules frozen at run start — Evaluation / 20260803 / 20260804 must NOT change these.


def _get(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _delta_pt(sel: Optional[float], all_: Optional[float]) -> Optional[float]:
    if sel is None or all_ is None:
        return None
    return (sel - all_) * 100.0


def assign_discovery_families(disc_agg: dict[str, Any]) -> list[str]:
    """Multi-label family tags from Discovery aggregation only."""
    tags: list[str] = []

    # QUICK_MOVE
    r30_sel = _get(disc_agg, "reach", "up_30", "SELECTED", "reach_rate")
    r30_all = _get(disc_agg, "reach", "up_30", "ALL_ANCHORS", "reach_rate")
    t30 = _get(disc_agg, "reach", "up_30", "SELECTED", "median_reach_time")
    d30 = _delta_pt(r30_sel, r30_all)
    if d30 is not None and d30 >= 2.0 and t30 is not None and t30 <= 300.0:
        tags.append("QUICK_MOVE")

    # PULLBACK_THEN_RISE
    r50_sel = _get(disc_agg, "reach", "up_50", "SELECTED", "reach_rate")
    r50_all = _get(disc_agg, "reach", "up_50", "ALL_ANCHORS", "reach_rate")
    d50 = _delta_pt(r50_sel, r50_all)
    pre30 = _get(disc_agg, "pre_rise", "up_30", "SELECTED_median_pre_MAE")
    pre50 = _get(disc_agg, "pre_rise", "up_50", "SELECTED_median_pre_MAE")
    t50 = _get(disc_agg, "reach", "up_50", "SELECTED", "median_reach_time")
    reach_ok = (d30 is not None and d30 >= 2.0) or (d50 is not None and d50 >= 2.0)
    pre_ok = (pre30 is not None and pre30 <= -10.0) or (pre50 is not None and pre50 <= -10.0)
    time_ok = (t30 is not None and t30 <= 900.0) or (t50 is not None and t50 <= 900.0)
    if reach_ok and pre_ok and time_ok:
        tags.append("PULLBACK_THEN_RISE")

    # CONTINUATION
    mfe900 = _get(disc_agg, "horizons", "900s", "SELECTED", "MFE", "mean")
    mfe300 = _get(disc_agg, "horizons", "300s", "SELECTED", "MFE", "mean")
    cont_mfe = (
        mfe900 is not None and mfe300 is not None and (mfe900 - mfe300) >= 20.0
    )
    r60_300 = _get(disc_agg, "reach", "up_60", "within_300s", "SELECTED_rate")
    r60_900 = _get(disc_agg, "reach", "up_60", "within_900s", "SELECTED_rate")
    cont_reach = (
        r60_300 is not None and r60_900 is not None and (r60_900 - r60_300) * 100.0 >= 5.0
    )
    ret900_delta = _get(disc_agg, "horizons", "900s", "delta_vs_ALL", "mean_return")
    if (cont_mfe or cont_reach) and ret900_delta is not None and ret900_delta > 0:
        tags.append("CONTINUATION")

    # DELAYED_MOVE
    ret300_d = _get(disc_agg, "horizons", "300s", "delta_vs_ALL", "mean_return")
    r30_300_d = _get(disc_agg, "reach", "up_30", "within_300s", "delta_vs_ALL_pt")
    early_flat = (ret300_d is not None and ret300_d <= 0) or (r30_300_d is not None and r30_300_d <= 0)
    ret900_d = ret900_delta
    ret1800_d = _get(disc_agg, "horizons", "1800s", "delta_vs_ALL", "mean_return")
    late_ret = (ret900_d is not None and ret900_d > 0) or (ret1800_d is not None and ret1800_d > 0)
    late_r30 = _get(disc_agg, "reach", "up_30", "within_900s", "delta_vs_ALL_pt")
    late_r50 = _get(disc_agg, "reach", "up_50", "within_900s", "delta_vs_ALL_pt")
    # late reach vs early: use session reach delta as late proxy if within_900 available
    late_reach = (
        (late_r30 is not None and late_r30 >= 3.0)
        or (late_r50 is not None and late_r50 >= 3.0)
        or (d30 is not None and d30 >= 3.0 and (r30_300_d is None or r30_300_d < d30))
    )
    if early_flat and late_ret and late_reach:
        tags.append("DELAYED_MOVE")

    # SPIKE_AND_GIVEBACK
    mfe300_d = _get(disc_agg, "horizons", "300s", "delta_vs_ALL", "mean_MFE")
    ret_sess_d = _get(disc_agg, "horizons", "session", "delta_vs_ALL", "mean_return")
    med_gb = _get(disc_agg, "horizons", "900s", "SELECTED", "median_terminal_giveback")
    if med_gb is None:
        med_gb = _get(disc_agg, "horizons", "300s", "SELECTED", "median_terminal_giveback")
    if (
        mfe300_d is not None and mfe300_d > 0
        and ((ret900_d is not None and ret900_d <= 0) or (ret_sess_d is not None and ret_sess_d <= 0))
        and med_gb is not None and med_gb >= 20.0
    ):
        tags.append("SPIKE_AND_GIVEBACK")

    if not tags:
        tags.append("NO_CLEAR_PATH_EDGE")
    return tags


MAJOR_METRICS = (
    "return_900s",
    "MFE_900s",
    "up_30_reach",
    "up_50_reach",
    "ft_30_20_up_first",
)


def _metric_delta(agg: dict[str, Any], name: str) -> Optional[float]:
    if name == "return_900s":
        return _get(agg, "horizons", "900s", "delta_vs_ALL", "mean_return")
    if name == "MFE_900s":
        return _get(agg, "horizons", "900s", "delta_vs_ALL", "mean_MFE")
    if name == "up_30_reach":
        s = _get(agg, "reach", "up_30", "SELECTED", "reach_rate")
        a = _get(agg, "reach", "up_30", "ALL_ANCHORS", "reach_rate")
        return None if s is None or a is None else (s - a)
    if name == "up_50_reach":
        s = _get(agg, "reach", "up_50", "SELECTED", "reach_rate")
        a = _get(agg, "reach", "up_50", "ALL_ANCHORS", "reach_rate")
        return None if s is None or a is None else (s - a)
    if name == "ft_30_20_up_first":
        s = _get(agg, "first_touch", "ft_30_20", "SELECTED", "up_first_rate")
        a = _get(agg, "first_touch", "ft_30_20", "ALL_ANCHORS", "up_first_rate")
        return None if s is None or a is None else (s - a)
    return None


def assign_path_evidence_status(
    *,
    disc_agg: dict[str, Any],
    eval_agg: dict[str, Any],
    min_support: int = 30,
) -> str:
    sel_n = disc_agg.get("selected_anchors") or 0
    elig = _get(disc_agg, "horizons", "900s", "eligible_n") or 0
    if sel_n < min_support or elig < min_support:
        return "ENTRY_PATH_INSUFFICIENT"

    disc_pos = []
    eval_pos = []
    for m in MAJOR_METRICS:
        d = _metric_delta(disc_agg, m)
        e = _metric_delta(eval_agg, m)
        if d is not None:
            disc_pos.append(d > 0)
        if e is not None:
            eval_pos.append(e > 0)

    if not disc_pos:
        return "ENTRY_PATH_INSUFFICIENT"
    if any(disc_pos) and any(eval_pos):
        # same direction confirmation: at least one eval metric positive when disc had improvement
        disc_improved = any(disc_pos)
        eval_confirm = any(eval_pos)
        if disc_improved and eval_confirm:
            # check mixed: some major disc negative and some positive
            if any(not x for x in disc_pos) and any(disc_pos):
                return "ENTRY_PATH_MIXED"
            return "ENTRY_PATH_SUPPORTED"
    if any(disc_pos) and eval_pos and not any(eval_pos):
        return "ENTRY_PATH_MIXED"
    if not any(disc_pos):
        return "ENTRY_PATH_WEAK"
    return "ENTRY_PATH_MIXED"


FAMILY_RULES_FROZEN = {
    "QUICK_MOVE": "selected +30bps reach >= ALL+2pt AND median reach time <= 300s (Discovery)",
    "PULLBACK_THEN_RISE": "+30/+50 reach >= ALL+2pt AND pre-reach median MAE <= -10bps AND median reach <= 900s",
    "CONTINUATION": "(MFE_900-MFE_300>=20 OR +60 reach 900s-300s>=5pt) AND return_900 delta>0",
    "DELAYED_MOVE": "early flat/neg AND late return>0 AND late reach delta>=3pt",
    "SPIKE_AND_GIVEBACK": "MFE_300 delta>0 AND (return_900 or session delta<=0) AND median terminal giveback>=20bps",
    "NO_CLEAR_PATH_EDGE": "none of the above; not a reject",
    "fixed_at_run_start": True,
    "evaluation_may_retune": False,
    "consumed_20260804_may_retune": False,
}
