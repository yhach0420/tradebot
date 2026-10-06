"""Distinct thesis/execution states. Identities minted early. No mixed eligibility blob."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v4.active import (
    classify_active_loss,
    classify_progress,
    note_failed_probe_pending,
    opening_net,
)
from research.pb1_v4_clarified_machine_correction_v4.continuation import classify_continuation
from research.pb1_v4_clarified_machine_correction_v4.location import classify_interaction, five_m_left, identify_location
from research.pb1_v4_clarified_machine_correction_v4.thesis import hidden_1m_snapshot, thesis_ready


def _id(*parts: Any) -> str:
    return "|".join(str(p) for p in parts)


@dataclass
class SideState:
    sign: int
    why_this_stock: bool = False
    seed: str | None = None
    seed_subtype: str | None = None
    opening_drive_active: bool = False
    opening_drive_reached: bool = False
    opening_drive_live: bool = False
    location_identified: bool = False
    thesis_ready: bool = False
    thesis_reached: bool = False
    thesis_live: bool = False
    e0_5m_confirmation: bool = False
    e1_1m_level_interaction: bool = False
    execution_ready: bool = False
    thesis_lost: bool = False
    thesis_lost_at: str | None = None
    thesis_lost_reason: str | None = None
    interaction: str = "NO_INTERACTION_YET"
    candidate_day_id: str | None = None
    opening_seed_id: str | None = None
    opening_drive_id: str | None = None
    location_id: str | None = None
    interaction_id: str | None = None
    thesis_id: str | None = None
    execution_id: str | None = None
    location: dict[str, Any] | None = None
    death: str | None = None
    left: bool = False
    recross_closes: int = 0
    both_or_extremes: bool = False
    five_m_no_expansion_n: int = 0
    wick_only_n: int = 0
    micro_break_n: int = 0
    peak_disp: float | None = None
    through_closes: int = 0
    retest_high: float | None = None
    retest_low: float | None = None
    e0_pos: int | None = None
    e1_pos: int | None = None
    thesis_ready_at: str | None = None
    thesis_ready_pos: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    hidden_snap: dict[str, Any] | None = None
    identity_mismatch: int = 0
    revived_by_execution: int = 0
    one_m_created_location: int = 0
    one_m_changed_direction: int = 0
    one_m_changed_drive: int = 0


def new_side(sign: int) -> SideState:
    return SideState(sign=int(sign))


def mint_why(st: SideState, *, symbol: str, date: str) -> None:
    st.candidate_day_id = _id(symbol, date)
    st.why_this_stock = True


def mint_seed(st: SideState, *, seed: str, subtype: str | None = None) -> None:
    st.seed = str(seed)
    st.seed_subtype = subtype or str(seed)
    st.opening_seed_id = _id(st.candidate_day_id, "SEED", st.seed, "BULL" if st.sign > 0 else "BEAR")


def mint_active(st: SideState) -> None:
    if not st.opening_seed_id:
        st.identity_mismatch += 1
        return
    st.opening_drive_active = True
    st.opening_drive_reached = True
    st.opening_drive_live = True
    st.opening_drive_id = _id(st.opening_seed_id, "ACTIVE")


def mint_location(st: SideState, loc: dict[str, Any], *, t: str) -> None:
    if not st.opening_drive_live or not st.opening_drive_id:
        st.identity_mismatch += 1
        return
    st.location_identified = True
    st.location = dict(loc)
    st.location_id = _id(st.opening_drive_id, loc.get("family"), loc.get("level_kind"), t)


def mint_thesis(st: SideState, *, symbol: str, date: str, t: str, pos: int) -> None:
    if not (st.why_this_stock and st.opening_drive_live and st.location_identified) or st.thesis_lost:
        return
    if not thesis_ready(why=True, active=True, location=True, lost=False):
        return
    st.thesis_ready = True
    st.thesis_reached = True
    st.thesis_live = True
    st.thesis_ready_at = t[:5]
    st.thesis_ready_pos = int(pos)
    st.thesis_id = _id(st.location_id, "THESIS")
    st.hidden_snap = hidden_1m_snapshot(
        symbol=symbol,
        date=date,
        direction="bull" if st.sign > 0 else "bear",
        opening_drive_id=st.opening_drive_id,
        location_id=st.location_id,
        thesis_id=st.thesis_id,
        thesis_ready_flag=True,
    )


def bind_chain(st: SideState) -> None:
    if st.opening_drive_id and st.opening_seed_id and not str(st.opening_drive_id).startswith(str(st.opening_seed_id)):
        st.identity_mismatch += 1
    if st.location_id and st.opening_drive_id and not str(st.location_id).startswith(str(st.opening_drive_id)):
        st.identity_mismatch += 1
    if st.thesis_id and st.location_id and not str(st.thesis_id).startswith(str(st.location_id)):
        st.identity_mismatch += 1


def lose_thesis(st: SideState, reason: str, *, t: str | None = None) -> None:
    if st.thesis_lost:
        return
    st.thesis_lost = True
    st.opening_drive_active = False
    st.opening_drive_live = False
    st.thesis_ready = False
    st.thesis_live = False
    st.execution_ready = False
    st.death = str(reason)
    st.thesis_lost_reason = str(reason)
    if t:
        st.thesis_lost_at = str(t)[:5]
    # Identities stay auditable. Do not clear thesis_id / opening_drive_id.


def note_or_leave(st: SideState, *, bar: dict[str, Any], or_high: float, or_low: float) -> None:
    if five_m_left(sign=st.sign, bar=bar, or_high=or_high, or_low=or_low):
        st.left = True


def note_or_recross(st: SideState, *, close: float, or_high: float, or_low: float) -> None:
    if not st.left:
        return
    mid_hi, mid_lo = float(or_high), float(or_low)
    inside = float(mid_lo) <= float(close) <= float(mid_hi)
    if inside:
        st.recross_closes += 1


def note_both_extremes(st: SideState, *, high: float, low: float, or_high: float, or_low: float) -> None:
    if float(high) >= float(or_high) and float(low) <= float(or_low):
        st.both_or_extremes = True


def note_expansion(st: SideState, *, bar: dict[str, Any]) -> None:
    ext = bar.get("h") if st.sign > 0 else bar.get("l")
    prev = st.extra.get("drive_ext")
    prev_bar = st.extra.get("progress_bar")
    if not _finite(ext):
        return
    if prev is None:
        st.extra["drive_ext"] = float(ext)
        st.extra["progress_bar"] = dict(bar)
        st.extra["last_progress_class"] = "NO_DIRECTIONAL_PROGRESS"
        st.five_m_no_expansion_n = 0
        return
    prog = classify_progress(sign=st.sign, bar=bar, prev_bar=prev_bar, prev_ext=prev)
    cls = str(prog.get("class") or "NO_DIRECTIONAL_PROGRESS")
    st.extra["last_progress_class"] = cls
    note_failed_probe_pending(st.extra, sign=st.sign, bar=bar, prog=prog)
    log = list(st.extra.get("progress_log") or [])
    log.append(
        {
            "t1": bar.get("t1"),
            "class": cls,
            "resets_stall": bool(prog.get("resets_stall")),
            "delta": prog.get("delta"),
            "failed_probe_state": st.extra.get("failed_probe_state"),
        }
    )
    st.extra["progress_log"] = log[-48:]
    if prog.get("resets_stall"):
        st.extra["drive_ext"] = float(ext)
        st.extra["progress_bar"] = dict(bar)
        st.extra["had_renewed_auction"] = True
        st.five_m_no_expansion_n = 0
    else:
        st.five_m_no_expansion_n += 1
        if cls in ("RANGE_DRIFT_EXTREME", "MARGINAL_EXTREME_ONLY") and progressed_extreme(sign=st.sign, ext=ext, prev=prev):
            st.extra["drive_ext"] = float(ext)
            st.extra["progress_bar"] = dict(bar)


def progressed_extreme(*, sign: int, ext: Any, prev: Any) -> bool:
    if not (_finite(ext) and _finite(prev)):
        return False
    return (int(sign) > 0 and float(ext) > float(prev)) or (int(sign) < 0 and float(ext) < float(prev))


def note_peak(st: SideState, *, close: float, open_0900: Any) -> None:
    disp = opening_net(sign=st.sign, open_0900=open_0900, close_now=close)
    if disp is None:
        return
    if st.peak_disp is None or float(disp) > float(st.peak_disp):
        st.peak_disp = float(disp)


def step_5m_thesis(
    st: SideState,
    *,
    symbol: str,
    date: str,
    t: str,
    pos: int,
    bar: dict[str, Any],
    or_high: float,
    or_low: float,
    open_0900: Any,
    zones: list[dict[str, Any]],
    pdh: Any,
    pdl: Any,
    pdc: Any,
    vwap: Any,
    n1m: Any,
    session_open: Any = None,
) -> None:
    if st.thesis_lost or not st.why_this_stock:
        return
    close = float(bar["c"])
    note_peak(st, close=close, open_0900=open_0900)
    note_or_leave(st, bar=bar, or_high=or_high, or_low=or_low)
    note_or_recross(st, close=close, or_high=or_high, or_low=or_low)
    note_both_extremes(st, high=float(bar["h"]), low=float(bar["l"]), or_high=or_high, or_low=or_low)
    prev_progress_bar = st.extra.get("progress_bar")
    note_expansion(st, bar=bar)
    if st.opening_drive_live or st.opening_drive_active:
        loss = classify_active_loss(
            sign=st.sign,
            close=close,
            or_high=or_high,
            or_low=or_low,
            open_0900=open_0900,
            peak_disp=st.peak_disp,
            wick_only_n=st.wick_only_n,
            micro_break_n=st.micro_break_n,
            recross_closes=st.recross_closes,
            left=st.left,
            five_m_no_expansion_n=st.five_m_no_expansion_n,
            both_or_extremes_revisited=st.both_or_extremes,
            location_identified=st.location_identified,
            had_renewed_auction=bool(st.extra.get("had_renewed_auction")),
            bar=bar,
            prev_bar=prev_progress_bar,
            extra=st.extra,
        )
        if loss.get("lost"):
            if loss.get("auction_end_family"):
                st.extra["auction_end_family"] = loss.get("auction_end_family")
            lose_thesis(st, str(loss.get("reason") or "THESIS_LOST"), t=t)
            return
    if st.opening_drive_live and not st.location_identified:
        loc = identify_location(
            sign=st.sign,
            active=True,
            bar=bar,
            or_high=or_high,
            or_low=or_low,
            zones=zones,
            pdh=pdh,
            pdl=pdl,
            pdc=pdc,
            vwap=vwap,
            n1m=n1m,
            left=st.left,
            session_open=session_open,
        )
        if loc:
            if not st.extra.get("family_a_logged"):
                st.extra["family_a_seen"] = list(loc.get("family_a_seen") or [])
                st.extra["family_a_logged"] = True
            if loc.get("family") in ("A_CLEARED_ZONE", "B_OR_AFTER_LEAVE", "C_CONFLUENT"):
                mint_location(st, loc, t=t[:5])
    if (not st.thesis_ready) and thesis_ready(
        why=st.why_this_stock,
        active=st.opening_drive_live,
        location=st.location_identified,
        lost=st.thesis_lost,
    ):
        mint_thesis(st, symbol=symbol, date=date, t=t, pos=pos)
    bind_chain(st)
    if st.thesis_ready and st.location:
        inter = classify_interaction(
            sign=st.sign,
            high=float(bar["h"]),
            low=float(bar["l"]),
            close=close,
            level=float(st.location["level"]),
            prev=st.interaction,
        )
        st.interaction = str(inter.get("state") or st.interaction)
        if inter.get("state") in ("TOUCH", "TESTING", "HOLD", "REJECTION", "TEMPORARY_PENETRATION"):
            st.interaction_id = _id(st.location_id, "IX", t[:5], st.interaction)
            if st.retest_high is None:
                st.retest_high = float(bar["h"])
            if st.retest_low is None:
                st.retest_low = float(bar["l"])
        if inter.get("kills_thesis"):
            if st.through_closes >= 1 or str(inter.get("state")) == "ACCEPTED_FAILURE":
                lose_thesis(st, "ACCEPTED_STRUCTURAL_FAILURE", t=t)
            else:
                st.through_closes += 1
        elif str(inter.get("state")) == "TEMPORARY_PENETRATION":
            st.through_closes += 1
            if st.through_closes >= 2:
                lose_thesis(st, "ACCEPTED_STRUCTURAL_FAILURE", t=t)
        if (
            st.thesis_live
            and (not st.e0_5m_confirmation)
            and inter.get("validates_execution")
            and st.thesis_ready_pos is not None
            and int(pos) > int(st.thesis_ready_pos)
        ):
            cont = classify_continuation(
                sign=st.sign,
                bar=bar,
                level=float(st.location["level"]),
                retest_high=st.retest_high,
                retest_low=st.retest_low,
            )
            if cont.get("ok"):
                st.e0_5m_confirmation = True
                st.execution_ready = True
                st.e0_pos = int(pos)
                st.extra["e0_cont"] = cont


def observe_1m_interaction(
    st: SideState,
    *,
    t: str,
    high: float,
    low: float,
    close: float,
) -> dict[str, Any]:
    """1m may observe a pre-identified level. It cannot mint location, direction, or drive."""
    if not st.thesis_live or st.thesis_lost or not st.location:
        return {"state": st.interaction, "cancel_attempt": False}
    prev_loc = st.location_id
    prev_dir = st.sign
    prev_drive = st.opening_drive_id
    prev_thesis = st.thesis_ready
    inter = classify_interaction(
        sign=st.sign,
        high=float(high),
        low=float(low),
        close=float(close),
        level=float(st.location["level"]),
        prev=st.interaction,
    )
    st.interaction = str(inter.get("state") or st.interaction)
    if inter.get("state") not in ("NO_INTERACTION_YET",):
        st.interaction_id = _id(st.location_id, "IX1M", t[:5], st.interaction)
        if st.retest_high is None:
            st.retest_high = float(high)
        if st.retest_low is None:
            st.retest_low = float(low)
    if st.location_id != prev_loc:
        st.one_m_created_location += 1
        st.location_id = prev_loc
    if st.sign != prev_dir:
        st.one_m_changed_direction += 1
        st.sign = prev_dir
    if st.opening_drive_id != prev_drive:
        st.one_m_changed_drive += 1
        st.opening_drive_id = prev_drive
    if (not prev_thesis) and st.thesis_ready:
        st.revived_by_execution += 1
        st.thesis_ready = False
    if inter.get("kills_thesis") or str(inter.get("state")) == "TEMPORARY_PENETRATION":
        st.through_closes += 1
        if st.through_closes >= 2 and str(inter.get("state")) == "ACCEPTED_FAILURE":
            lose_thesis(st, "ACCEPTED_STRUCTURAL_FAILURE", t=t)
        # first unsuccessful does not automatically kill
    return inter
