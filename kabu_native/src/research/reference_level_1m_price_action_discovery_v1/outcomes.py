"""Forward path labels. Outcomes never feed event generation. Lunch bars skipped; PM resumed."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch
from research.reference_level_1m_price_action_discovery_v1 import FAVOR_BPS, PATH_BARS, SESSION_FLAT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _bps(exit_px: Any, entry_px: Any) -> float | None:
    if not _finite(exit_px) or not _finite(entry_px) or float(entry_px) == 0:
        return None
    return float((float(exit_px) / float(entry_px) - 1.0) * 10_000.0)


def attach_fwd(ep: dict[str, Any], rec: dict[str, Any], *, until_session_flat: bool = True) -> None:
    """Observed trading bars after ENTRY, skipping lunch, continuing PM.

    Path labels use the first PATH_BARS session bars.
    Replay needs until_session_flat=True so 15:20 flatten is reachable.
    """
    entry_t = str(ep.get("event_time") or "")
    ie = rec["idx"].get(entry_t)
    px = rec["o"][ie] if ie is not None else float("nan")
    ep["x0_entry_open"] = float(px) if ie is not None and np.isfinite(px) and px > 0 else None
    fwd: list[tuple[Any, ...]] = []
    mfe = mae = None
    mfe_i = mae_i = None
    last_bps = None
    path_last_bps = None
    n = int(rec["n"])
    if ie is not None and np.isfinite(px) and px > 0:
        for k in range(ie, n):
            hh = rec["t"][k]
            if in_lunch(hh):
                continue
            o = float(rec["o"][k])
            h = float(rec["h"][k])
            l = float(rec["l"][k])
            cl = float(rec["c"][k])
            vw = rec["vw"][k]
            fwd.append(
                (
                    hh,
                    o,
                    h,
                    l,
                    cl if cl == cl else float("nan"),
                    float(vw) if _finite(vw) else None,
                    float(ep.get("level_value") or np.nan),
                )
            )
            if len(fwd) <= PATH_BARS:
                step = len(fwd) - 1
                hbps = _bps(h, px)
                lbps = _bps(l, px)
                if hbps is not None and (mfe is None or hbps > mfe):
                    mfe, mfe_i = hbps, step
                if lbps is not None and (mae is None or lbps < mae):
                    mae, mae_i = lbps, step
                path_last_bps = _bps(cl, px)
            if str(hh) >= SESSION_FLAT:
                break
            if (not until_session_flat) and len(fwd) >= PATH_BARS:
                break
    ep["fwd_bars"] = fwd
    ep["mfe_bps"] = mfe
    ep["mae_bps"] = mae
    ep["time_to_mfe_min"] = mfe_i
    ep["time_to_mae_min"] = mae_i
    ep["last_path_bps"] = path_last_bps
    ep["fwd_n"] = len(fwd)
    ep["fwd_last_hh"] = str(fwd[-1][0]) if fwd else None
    ep["fwd_crosses_lunch"] = bool(fwd and str(fwd[0][0]) < "11:30" and str(fwd[-1][0]) >= "12:30")
    ep["implicit_1130_truncation"] = False
    ep["outcomes_attached"] = True
    ep["outcomes_are_labels_only"] = True
    label_path(ep)


def label_path(ep: dict[str, Any]) -> None:
    mfe = ep.get("mfe_bps")
    mae = ep.get("mae_bps")
    mfe_i = ep.get("time_to_mfe_min")
    mae_i = ep.get("time_to_mae_min")
    last_bps = ep.get("last_path_bps")
    if mfe is None or mae is None:
        ep["path_type"] = "UNLABELED"
        ep["favorable_first"] = False
        ep["adverse_first"] = False
        ep["continuation"] = False
        ep["reversal"] = False
        ep["stall"] = False
        return
    mfe_v, mae_v = float(mfe), float(mae)
    fav_first = mfe_i is not None and mfe_v >= FAVOR_BPS and (mae_i is None or int(mfe_i) <= int(mae_i))
    adv_first = mae_i is not None and mae_v <= -FAVOR_BPS and (mfe_i is None or int(mae_i) < int(mfe_i))
    stall = abs(mfe_v) < FAVOR_BPS and abs(mae_v) < FAVOR_BPS
    cont = (not stall) and last_bps is not None and float(last_bps) > 0 and mfe_v >= FAVOR_BPS
    rev = (not stall) and last_bps is not None and float(last_bps) <= 0 and mae_v <= -FAVOR_BPS
    if stall:
        path = "stall"
    elif cont:
        path = "continuation"
    elif rev:
        path = "reversal"
    elif mfe_v >= FAVOR_BPS and last_bps is not None and float(last_bps) <= 0:
        path = "giveback"
    else:
        path = "other"
    ep["path_type"] = path
    ep["favorable_first"] = bool(fav_first)
    ep["adverse_first"] = bool(adv_first)
    ep["continuation"] = bool(cont)
    ep["reversal"] = bool(rev)
    ep["stall"] = bool(stall)
    kind = str(ep.get("event_kind") or "")
    ep["retest_success"] = bool(kind.startswith("RETEST_HOLD") and cont)
    ep["break_failure"] = bool("FAILED_BREAK" in kind or kind.endswith("FILL_FAILURE"))


def stamp_event(ev: dict[str, Any]) -> None:
    feat = str(ev.get("feature_bar") or "")
    avail = hhmm_add(feat, 1)
    ev["available_at"] = avail
    ev["event_time"] = avail
    ev["same_bar_execution"] = False
    if avail is None:
        ev["causal_ok"] = False
        ev["retroactive_timestamp"] = True
        return
    ev["causal_ok"] = True
    ev["retroactive_timestamp"] = False
    ev["future_dependent"] = False
    brk = ev.get("break_feature_bar")
    if brk and str(ev.get("event_kind") or "").startswith("ACCEPT2"):
        ev["accept_not_moved_to_break"] = str(feat) != str(brk)
    else:
        ev["accept_not_moved_to_break"] = True
