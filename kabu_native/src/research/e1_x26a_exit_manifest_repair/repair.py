"""Discovery-only trailing/stop repair + semantic canonicalization."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x26_exit_library.snap import snap_ceil, snap_floor

from . import (
    GIVEBACK_GRID_BPS,
    NO_PROGRESS_ABS_RET_BPS,
    NO_PROGRESS_MFE_BPS,
    NO_PROGRESS_SOURCE,
    STOP_GRID_V2_BPS,
    TARGET_GRID_BPS,
    TRAIL_ACTIVATION_GRID_BPS,
)
from .audit import locked_profit_bps, semantic_exit_sha


def _pick_giveback(cb: Optional[float], aw: Optional[float], *, prefer_small: bool) -> Optional[float]:
    cbs = snap_ceil(cb, GIVEBACK_GRID_BPS)
    aws = snap_ceil(aw, GIVEBACK_GRID_BPS)
    vals = [x for x in (cbs, aws) if x is not None]
    if not vals:
        return None
    return min(vals) if prefer_small else max(vals)


def repair_activation(
    *,
    giveback: float,
    variant: str,
) -> tuple[float, float]:
    """Return (activation, locked_profit). Raises if cannot satisfy invariant on grid."""
    if variant in ("PROTECT", "TIGHT_TRAIL"):
        need = giveback + 10.0
        min_locked = 10.0
    else:
        need = giveback  # ROOM / TRAIL
        min_locked = 0.0
    act = snap_ceil(need, TRAIL_ACTIVATION_GRID_BPS)
    if act is None:
        raise RuntimeError("activation snap failed")
    # if snapped activation still insufficient (shouldn't happen with ceil), bump
    while act - giveback + 1e-12 < min_locked:
        # next grid step
        higher = [g for g in TRAIL_ACTIVATION_GRID_BPS if g > act + 1e-12]
        if not higher:
            raise RuntimeError(f"cannot satisfy locked>={min_locked} giveback={giveback}")
        act = float(higher[0])
    locked = locked_profit_bps(act, giveback)
    assert locked is not None and locked + 1e-12 >= min_locked
    return act, locked


def build_repaired_family_exits(
    *,
    cand_calib: dict[str, Any],
    anch_calib: dict[str, Any],
    v1_params: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (exit_rows, trailing_v2_audit, stop_v2_audit).
    """
    exits: list[dict[str, Any]] = []
    trail_audit: list[dict[str, Any]] = []
    stop_audit: list[dict[str, Any]] = []

    def stop_from(raw_cb: Optional[float], raw_aw: Optional[float], *, room: bool) -> tuple[float, dict]:
        # PROTECT: smaller abs stop; ROOM: larger
        cbs = snap_ceil(raw_cb, STOP_GRID_V2_BPS)
        aws = snap_ceil(raw_aw, STOP_GRID_V2_BPS)
        vals = [x for x in (cbs, aws) if x is not None]
        if not vals:
            stop = 20.0
            raw_used = None
        else:
            stop = max(vals) if room else min(vals)
            raw_used = max(x for x in (raw_cb, raw_aw) if x is not None) if room else min(
                x for x in (raw_cb, raw_aw) if x is not None
            )
        # never round below required: snap_ceil already; verify
        req = raw_used
        if req is not None and stop + 1e-12 < req:
            stop = snap_ceil(req, STOP_GRID_V2_BPS) or stop
        return float(stop), {
            "raw_cb": raw_cb, "raw_aw": raw_aw, "stop_bps": stop,
            "never_below_required": req is None or stop + 1e-12 >= req,
            "grid": list(STOP_GRID_V2_BPS),
        }

    # --- QUICK ---
    cb, aw = cand_calib["QUICK_MOVE"], anch_calib["QUICK_MOVE"]
    # TARGET
    tgt = snap_floor(cb.get("mfe300_q25") or cb.get("mfe300_q50"), TARGET_GRID_BPS) or 20.0
    st_t, st_info = stop_from(cb.get("pre30_abs_q50"), aw.get("pre30_abs_q50"), room=False)
    stop_audit.append({"exit_id": "EXIT_QUICK_TARGET_V2", "family": "QUICK_MOVE", **st_info})
    v1q = v1_params["EXIT_QUICK_TARGET_V1"]
    exits.append({
        "exit_id": "EXIT_QUICK_TARGET_V2", "path_family": "QUICK_MOVE", "variant": "TARGET",
        "legacy_alias_hint": "EXIT_QUICK_TARGET_V1",
        "stop_bps": st_t, "target_bps": tgt, "trail_activation_bps": None, "giveback_bps": None,
        "giveback_mode": None,
        "no_progress_sec": v1q["no_progress_sec"], "max_hold_sec": v1q["max_hold_sec"],
        "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
        "no_progress_source": NO_PROGRESS_SOURCE,
    })
    # TRAIL (ROOM-like: locked >= 0); prefer larger giveback
    gb = _pick_giveback(cb.get("max_gb_300_q25"), aw.get("max_gb_300_q25"), prefer_small=False)
    if gb is None:
        gb = 20.0
    # Cap extremely wide giveback for quick trail feasibility: still on grid, but activation must be reachable
    # Keep mechanical: use picked giveback; if activation would exceed grid max, mark unavailable later
    st_r, st_info_r = stop_from(cb.get("pre30_abs_q75"), aw.get("pre30_abs_q75"), room=True)
    stop_audit.append({"exit_id": "EXIT_QUICK_TRAIL_V2", "family": "QUICK_MOVE", **st_info_r})
    try:
        act, locked = repair_activation(giveback=gb, variant="TRAIL")
        exits.append({
            "exit_id": "EXIT_QUICK_TRAIL_V2", "path_family": "QUICK_MOVE", "variant": "TRAIL",
            "legacy_alias_hint": "EXIT_QUICK_TRAIL_V1",
            "stop_bps": st_r, "target_bps": None, "trail_activation_bps": act, "giveback_bps": gb,
            "giveback_mode": "from_MFE",
            "no_progress_sec": v1_params["EXIT_QUICK_TRAIL_V1"]["no_progress_sec"],
            "max_hold_sec": v1_params["EXIT_QUICK_TRAIL_V1"]["max_hold_sec"],
            "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
            "no_progress_source": NO_PROGRESS_SOURCE,
            "locked_profit_at_activation_bps": locked,
            "status": "ACTIVE",
        })
        trail_audit.append({"exit_id": "EXIT_QUICK_TRAIL_V2", "variant": "TRAIL", "giveback": gb, "activation": act, "locked": locked, "ok": True})
    except RuntimeError as e:
        exits.append({
            "exit_id": "EXIT_QUICK_TRAIL_V2", "path_family": "QUICK_MOVE", "variant": "TRAIL",
            "status": "EXIT_VARIANT_UNAVAILABLE", "reason": str(e),
        })
        trail_audit.append({"exit_id": "EXIT_QUICK_TRAIL_V2", "ok": False, "reason": str(e)})

    # --- PULLBACK ---
    cb, aw = cand_calib["PULLBACK_THEN_RISE"], anch_calib["PULLBACK_THEN_RISE"]
    for eid, var, prefer_small, stop_room, v1id in (
        ("EXIT_PULLBACK_PROTECT_V2", "PROTECT", True, False, "EXIT_PULLBACK_PROTECT_V1"),
        ("EXIT_PULLBACK_ROOM_V2", "ROOM", False, True, "EXIT_PULLBACK_ROOM_V1"),
    ):
        gb = _pick_giveback(cb.get("max_gb_900_q25"), aw.get("max_gb_900_q25"), prefer_small=prefer_small)
        if gb is None:
            gb = 20.0
        st, st_info = stop_from(
            cb.get("pre50_abs_q50" if not stop_room else "pre50_abs_q75"),
            aw.get("pre50_abs_q50" if not stop_room else "pre50_abs_q75"),
            room=stop_room,
        )
        stop_audit.append({"exit_id": eid, "family": "PULLBACK_THEN_RISE", **st_info})
        v1 = v1_params[v1id]
        try:
            act, locked = repair_activation(giveback=gb, variant=var)
            exits.append({
                "exit_id": eid, "path_family": "PULLBACK_THEN_RISE", "variant": var,
                "legacy_alias_hint": v1id,
                "stop_bps": st, "target_bps": None, "trail_activation_bps": act, "giveback_bps": gb,
                "giveback_mode": "from_MFE",
                "no_progress_sec": v1["no_progress_sec"], "max_hold_sec": v1["max_hold_sec"],
                "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
                "no_progress_source": NO_PROGRESS_SOURCE,
                "locked_profit_at_activation_bps": locked, "status": "ACTIVE",
            })
            trail_audit.append({"exit_id": eid, "variant": var, "giveback": gb, "activation": act, "locked": locked, "ok": True})
        except RuntimeError as e:
            exits.append({"exit_id": eid, "path_family": "PULLBACK_THEN_RISE", "variant": var,
                          "status": "EXIT_VARIANT_UNAVAILABLE", "reason": str(e)})
            trail_audit.append({"exit_id": eid, "ok": False, "reason": str(e)})

    # --- CONTINUATION ---
    cb, aw = cand_calib["CONTINUATION"], anch_calib["CONTINUATION"]
    for eid, var, prefer_small, stop_room, v1id, qkey in (
        ("EXIT_CONTINUATION_PROTECT_V2", "PROTECT", True, False, "EXIT_CONTINUATION_PROTECT_V1", "pre60_abs_q50"),
        ("EXIT_CONTINUATION_ROOM_V2", "ROOM", False, True, "EXIT_CONTINUATION_ROOM_V1", "pre60_abs_q75"),
    ):
        gb = _pick_giveback(cb.get("max_gb_900_q25"), aw.get("max_gb_900_q25"), prefer_small=prefer_small)
        if gb is None:
            gb = 20.0
        # giveback may exceed grid max 60 — snap_ceil pins to 60
        st, st_info = stop_from(cb.get(qkey), aw.get(qkey), room=stop_room)
        stop_audit.append({"exit_id": eid, "family": "CONTINUATION", **st_info})
        v1 = v1_params[v1id]
        try:
            act, locked = repair_activation(giveback=gb, variant=var)
            exits.append({
                "exit_id": eid, "path_family": "CONTINUATION", "variant": var,
                "legacy_alias_hint": v1id,
                "stop_bps": st, "target_bps": None, "trail_activation_bps": act, "giveback_bps": gb,
                "giveback_mode": "from_MFE",
                "no_progress_sec": v1["no_progress_sec"], "max_hold_sec": v1["max_hold_sec"],
                "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
                "no_progress_source": NO_PROGRESS_SOURCE,
                "locked_profit_at_activation_bps": locked, "status": "ACTIVE",
                "stop_risk_note": "120bps stop risk display-only; not deleted in X26A" if st >= 120 else None,
            })
            trail_audit.append({"exit_id": eid, "variant": var, "giveback": gb, "activation": act, "locked": locked, "ok": True})
        except RuntimeError as e:
            exits.append({"exit_id": eid, "path_family": "CONTINUATION", "variant": var,
                          "status": "EXIT_VARIANT_UNAVAILABLE", "reason": str(e)})
            trail_audit.append({"exit_id": eid, "ok": False, "reason": str(e)})

    # --- DELAYED ---
    cb, aw = cand_calib["DELAYED_MOVE"], anch_calib["DELAYED_MOVE"]
    for eid, var, prefer_small, stop_room, v1id in (
        ("EXIT_DELAYED_PROTECT_V2", "PROTECT", True, False, "EXIT_DELAYED_PROTECT_V1"),
        ("EXIT_DELAYED_ROOM_V2", "ROOM", False, True, "EXIT_DELAYED_ROOM_V1"),
    ):
        gb = _pick_giveback(cb.get("max_gb_1800_q25"), aw.get("max_gb_1800_q25"), prefer_small=prefer_small)
        if gb is None:
            gb = 20.0
        raw_cb = max(x for x in (cb.get("pre50_abs_q75"), cb.get("pre60_abs_q75")) if x is not None) if any(
            cb.get(k) is not None for k in ("pre50_abs_q75", "pre60_abs_q75")
        ) else None
        raw_aw = max(x for x in (aw.get("pre50_abs_q75"), aw.get("pre60_abs_q75")) if x is not None) if any(
            aw.get(k) is not None for k in ("pre50_abs_q75", "pre60_abs_q75")
        ) else None
        # PROTECT uses same q75 family stop but prefer_small on stop pair via room flag
        st, st_info = stop_from(raw_cb, raw_aw, room=stop_room)
        stop_audit.append({"exit_id": eid, "family": "DELAYED_MOVE", **st_info})
        v1 = v1_params[v1id]
        try:
            act, locked = repair_activation(giveback=gb, variant=var)
            exits.append({
                "exit_id": eid, "path_family": "DELAYED_MOVE", "variant": var,
                "legacy_alias_hint": v1id,
                "stop_bps": st, "target_bps": None, "trail_activation_bps": act, "giveback_bps": gb,
                "giveback_mode": "from_MFE",
                "no_progress_sec": v1["no_progress_sec"], "max_hold_sec": v1["max_hold_sec"],
                "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
                "no_progress_source": NO_PROGRESS_SOURCE,
                "locked_profit_at_activation_bps": locked, "status": "ACTIVE",
            })
            trail_audit.append({"exit_id": eid, "variant": var, "giveback": gb, "activation": act, "locked": locked, "ok": True})
        except RuntimeError as e:
            exits.append({"exit_id": eid, "path_family": "DELAYED_MOVE", "variant": var,
                          "status": "EXIT_VARIANT_UNAVAILABLE", "reason": str(e)})
            trail_audit.append({"exit_id": eid, "ok": False, "reason": str(e)})

    # --- SPIKE ---
    cb, aw = cand_calib["SPIKE_AND_GIVEBACK"], anch_calib["SPIKE_AND_GIVEBACK"]
    tgt = snap_floor(cb.get("mfe300_q25"), TARGET_GRID_BPS) or 20.0
    st_t, st_info = stop_from(cb.get("pre30_abs_q50"), aw.get("pre30_abs_q50"), room=False)
    stop_audit.append({"exit_id": "EXIT_SPIKE_TARGET_V2", "family": "SPIKE_AND_GIVEBACK", **st_info})
    v1s = v1_params["EXIT_SPIKE_TARGET_V1"]
    exits.append({
        "exit_id": "EXIT_SPIKE_TARGET_V2", "path_family": "SPIKE_AND_GIVEBACK", "variant": "TARGET",
        "legacy_alias_hint": "EXIT_SPIKE_TARGET_V1",
        "stop_bps": st_t, "target_bps": tgt, "trail_activation_bps": None, "giveback_bps": None,
        "giveback_mode": None,
        "no_progress_sec": v1s["no_progress_sec"], "max_hold_sec": v1s["max_hold_sec"],
        "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
        "no_progress_source": NO_PROGRESS_SOURCE, "status": "ACTIVE",
    })
    gb = _pick_giveback(cb.get("max_gb_300_q25"), aw.get("max_gb_300_q25"), prefer_small=True)
    if gb is None:
        gb = 15.0
    # tight: cap giveback at 30
    if gb > 30:
        gb = 30.0
    st_r, st_info_r = stop_from(cb.get("pre30_abs_q75"), aw.get("pre30_abs_q75"), room=True)
    stop_audit.append({"exit_id": "EXIT_SPIKE_TIGHT_TRAIL_V2", "family": "SPIKE_AND_GIVEBACK", **st_info_r})
    v1t = v1_params["EXIT_SPIKE_TIGHT_TRAIL_V1"]
    try:
        act, locked = repair_activation(giveback=gb, variant="TIGHT_TRAIL")
        exits.append({
            "exit_id": "EXIT_SPIKE_TIGHT_TRAIL_V2", "path_family": "SPIKE_AND_GIVEBACK", "variant": "TIGHT_TRAIL",
            "legacy_alias_hint": "EXIT_SPIKE_TIGHT_TRAIL_V1",
            "stop_bps": st_r, "target_bps": None, "trail_activation_bps": act, "giveback_bps": gb,
            "giveback_mode": "from_MFE",
            "no_progress_sec": v1t["no_progress_sec"], "max_hold_sec": v1t["max_hold_sec"],
            "no_progress_mfe_bps": NO_PROGRESS_MFE_BPS, "no_progress_abs_ret_bps": NO_PROGRESS_ABS_RET_BPS,
            "no_progress_source": NO_PROGRESS_SOURCE,
            "locked_profit_at_activation_bps": locked, "status": "ACTIVE",
        })
        trail_audit.append({"exit_id": "EXIT_SPIKE_TIGHT_TRAIL_V2", "variant": "TIGHT_TRAIL", "giveback": gb, "activation": act, "locked": locked, "ok": True})
    except RuntimeError as e:
        exits.append({"exit_id": "EXIT_SPIKE_TIGHT_TRAIL_V2", "path_family": "SPIKE_AND_GIVEBACK", "variant": "TIGHT_TRAIL",
                      "status": "EXIT_VARIANT_UNAVAILABLE", "reason": str(e)})
        trail_audit.append({"exit_id": "EXIT_SPIKE_TIGHT_TRAIL_V2", "ok": False, "reason": str(e)})

    return exits, trail_audit, stop_audit


def canonicalize_exits(exit_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """
    Merge semantic duplicates into canonical + aliases.
    Returns (canonical_registry, alias_registry, map legacy_or_v2_id -> canonical_id).
    """
    active = [e for e in exit_rows if e.get("status", "ACTIVE") == "ACTIVE" and e.get("stop_bps") is not None]
    unavailable = [e for e in exit_rows if e.get("status") == "EXIT_VARIANT_UNAVAILABLE"]

    groups: dict[str, list[dict[str, Any]]] = {}
    for e in active:
        sha = semantic_exit_sha(e)
        e = {**e, "semantic_exit_sha256": sha}
        groups.setdefault(sha, []).append(e)

    canonical: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}

    for sha, members in groups.items():
        families = sorted({m["path_family"] for m in members if m.get("path_family")})
        # choose canonical id
        if len(members) == 1:
            can_id = members[0]["exit_id"]
            # if target 20/20 quick+spike style naming when duplicated later
        else:
            # prefer FAST_TARGET naming for target dups
            if all(m.get("variant") == "TARGET" for m in members):
                stop = members[0]["stop_bps"]
                tgt = members[0]["target_bps"]
                can_id = f"EXIT_FAST_TARGET_{int(stop)}_{int(tgt)}_V1"
            else:
                can_id = members[0]["exit_id"]
        base = {k: v for k, v in members[0].items() if k not in ("exit_id", "path_family", "legacy_alias_hint")}
        alias_ids = [m["exit_id"] for m in members]
        legacy = [m.get("legacy_alias_hint") for m in members if m.get("legacy_alias_hint")]
        canonical.append({
            "canonical_exit_id": can_id,
            "semantic_exit_sha256": sha,
            "alias_exit_ids": alias_ids,
            "legacy_v1_aliases": legacy,
            "applicable_path_families": families,
            **base,
            "status": "ACTIVE",
        })
        for m in members:
            id_map[m["exit_id"]] = can_id
            aliases.append({
                "alias_exit_id": m["exit_id"],
                "canonical_exit_id": can_id,
                "semantic_exit_sha256": sha,
                "path_family": m.get("path_family"),
                "legacy_v1": m.get("legacy_alias_hint"),
            })

    for e in unavailable:
        canonical.append({
            "canonical_exit_id": e["exit_id"],
            "status": "EXIT_VARIANT_UNAVAILABLE",
            "path_family": e.get("path_family"),
            "reason": e.get("reason"),
            "applicable_path_families": [e["path_family"]] if e.get("path_family") else [],
        })
        id_map[e["exit_id"]] = e["exit_id"]

    return canonical, aliases, id_map
