"""FAIL CLOSED vs Paper / OPVAL / Formal Cert / standard 50-stock Capture. Never unregister competitors."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1.isolation import NATIVE
from small_paper.capture_child_cleanup import query_process
from small_paper.runtime_clock import certification_mode

JST = ZoneInfo("Asia/Tokyo")


class ExclusiveBlocked(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_from_file(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        txt = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if txt.isdigit():
            return int(txt)
        body = json.loads(path.read_text(encoding="utf-8"))
        return int(body.get("pid") or 0)
    except Exception:
        return 0


def _alive(pid: int) -> dict[str, Any]:
    if int(pid or 0) <= 0:
        return {"pid": 0, "exists": False}
    q = query_process(int(pid))
    return {"pid": int(pid), "exists": bool(q.get("exists")), "name": q.get("name"), "cmdline": q.get("cmdline")}


def classify_owner_record(owner: dict[str, Any], *, trading_date: str) -> dict[str, Any]:
    """Yesterday's owner JSON is STALE_METADATA if the PID is dead. Live PID is a competitor."""
    if not owner:
        return {"class": "ABSENT", "competitor": False, "pid": 0, "alive": {"exists": False}}
    pid = int(owner.get("pid") or 0)
    alive = _alive(pid)
    owner_day = str(owner.get("trading_date") or "")
    if alive.get("exists"):
        return {
            "class": "LIVE_OWNER",
            "competitor": True,
            "pid": pid,
            "owner_trading_date": owner_day,
            "alive": alive,
        }
    klass = "STALE_METADATA"
    if owner_day == str(trading_date):
        klass = "STALE_SAME_DAY_DEAD_PID"
    elif owner_day:
        klass = "STALE_METADATA"
    return {
        "class": klass,
        "competitor": False,
        "pid": pid,
        "owner_trading_date": owner_day,
        "alive": alive,
    }


def probe_exclusive(*, native_root: Optional[Path] = None, trading_date: Optional[str] = None) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date or datetime.now(JST).strftime("%Y%m%d"))
    competitors: list[dict[str, Any]] = []

    cap_day = root / "data" / "market_capture" / day
    for label, path in (
        ("ingress.pid", cap_day / "ingress.pid"),
        ("capture.pid", cap_day / "capture.pid"),
    ):
        meta = _alive(_pid_from_file(path))
        meta["label"] = label
        meta["path"] = str(path)
        if meta.get("exists"):
            competitors.append(meta)

    try:
        from small_paper.market_ingress_spawn import _live_ingress_pids

        for row in _live_ingress_pids(native_root=root, trading_date=day):
            pid = int(row.get("pid") or 0)
            if pid > 0:
                competitors.append({"label": "live_ingress_scan", "pid": pid, "exists": True, "cmdline": row.get("cmdline")})
    except Exception:
        pass

    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_pilots

        for row in list_live_pilots(trading_date=day):
            pid = int(row.get("pid") or 0)
            if pid > 0:
                competitors.append({"label": "live_paper_pilot", "pid": pid, "exists": True, "cmdline": row.get("cmdline")})
    except Exception:
        pass

    opval = _read_json(root / "runtime" / "opval_launcher_state.json")
    for key in ("paper_pid", "capture_pid"):
        meta = _alive(int(opval.get(key) or 0))
        meta["label"] = f"opval.{key}"
        meta["opval_trading_date"] = opval.get("trading_date")
        if meta.get("exists"):
            competitors.append(meta)

    lock = _read_json(root / "runtime" / "v1r_primary_live.lock")
    meta = _alive(int(lock.get("pid") or 0))
    meta["label"] = "v1r_primary_live.lock"
    if meta.get("exists"):
        competitors.append(meta)

    paper_state = _read_json(root / "runtime" / "paper_register_state.json")
    owner = _read_json(root / "runtime" / "kabu_registration_owner.json")
    owner_class = classify_owner_record(owner, trading_date=day)
    if owner_class.get("competitor"):
        competitors.append(
            {
                "label": "kabu_registration_owner",
                "pid": owner_class.get("pid"),
                "exists": True,
                "owner": owner.get("owner") or owner.get("owner_role"),
                "owner_trading_date": owner.get("trading_date"),
                "class": owner_class.get("class"),
                "cmdline": (owner_class.get("alive") or {}).get("cmdline"),
            }
        )

    cert = bool(certification_mode())
    if cert:
        competitors.append({"label": "TRADEBOT_CERTIFICATION_MODE", "pid": 0, "exists": True})

    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress, list_live_pilots

        for row in list_live_ingress(trading_date="", native_root=root):
            pid = int(row.get("pid") or 0)
            if pid > 0:
                competitors.append(
                    {
                        "label": "actual_ingress_any_date",
                        "pid": pid,
                        "exists": True,
                        "cmdline": row.get("cmdline"),
                    }
                )
        for row in list_live_pilots(trading_date=""):
            pid = int(row.get("pid") or 0)
            if pid > 0:
                competitors.append(
                    {
                        "label": "actual_paper_pilot_any_date",
                        "pid": pid,
                        "exists": True,
                        "cmdline": row.get("cmdline"),
                    }
                )
    except Exception:
        pass

    # Dedup by pid+label
    seen: set[tuple[str, int]] = set()
    uniq: list[dict[str, Any]] = []
    for c in competitors:
        key = (str(c.get("label")), int(c.get("pid") or 0))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    ok = len(uniq) == 0
    return {
        "ok": ok,
        "trading_date": day,
        "competitors": uniq,
        "paper_simultaneous": any(
            any(
                tok in str(c.get("label") or "")
                for tok in ("live_paper", "opval.paper", "v1r_primary", "actual_paper_pilot")
            )
            for c in uniq
        ),
        "opval_simultaneous": any(str(c.get("label") or "").startswith("opval.") for c in uniq),
        "standard_capture_simultaneous": any(
            str(c.get("label") or "")
            in {
                "ingress.pid",
                "capture.pid",
                "live_ingress_scan",
                "opval.capture_pid",
                "actual_ingress_any_date",
            }
            for c in uniq
        ),
        "certification_mode": cert,
        "registration_owner": owner,
        "registration_owner_class": owner_class,
        "paper_register_state_day": paper_state.get("trading_date"),
        "unregister_on_conflict": False,
        "fail_closed": (not ok),
    }


def require_exclusive(*, native_root: Optional[Path] = None, trading_date: Optional[str] = None) -> dict[str, Any]:
    probe = probe_exclusive(native_root=native_root, trading_date=trading_date)
    if not probe.get("ok"):
        raise ExclusiveBlocked(
            "NEW_INFO acquisition is mutually exclusive with Paper/OPVAL/Cert/standard Capture. "
            f"competitors={probe.get('competitors')}. Do not unregister the existing 50-stock session."
        )
    return probe
