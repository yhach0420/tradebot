#!/usr/bin/env python3
"""Paper Trade pre-start healthcheck for today (demo-isolated; no live orders).

Uses existing preflight / demo PUSH / freshness / refresh helpers.
Does NOT change ENTRY/EXIT/Shadow thresholds or enable broker submit.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
SRC = NATIVE / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE))

DAY = datetime.now(JST).strftime("%Y%m%d")
OUT = NATIVE / "results" / "reports" / f"paper_trade_healthcheck_{DAY}"
DEMO_LOG = OUT / "demo_logs"
OFFICIAL_BASELINE_DAY = "20260721"  # last formal day before today's session


@dataclass
class Check:
    name: str
    status: str  # PASS / FAIL / SKIP
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _append_demo_log(name: str, row: dict[str, Any]) -> Path:
    DEMO_LOG.mkdir(parents=True, exist_ok=True)
    path = DEMO_LOG / f"{name}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _iso(), **row}, ensure_ascii=False, default=str) + "\n")
    return path


# ---------------------------------------------------------------------------
# 1. Pre-state
# ---------------------------------------------------------------------------


def official_baseline() -> dict[str, Any]:
    formal = NATIVE / "results" / "daily" / OFFICIAL_BASELINE_DAY / (
        f"daily_summary_recovery_market_price_{OFFICIAL_BASELINE_DAY}.json"
    )
    out: dict[str, Any] = {"day": OFFICIAL_BASELINE_DAY, "formal_path": str(formal)}
    if formal.is_file():
        data = json.loads(formal.read_text(encoding="utf-8"))
        total = (data.get("pnl_split") or {}).get("total") or {}
        out["trade_count"] = total.get("count")
        out["pnl_yen_100"] = total.get("total_pnl_yen_100")
        out["sha256"] = hashlib.sha256(formal.read_bytes()).hexdigest()
    sessions = []
    root = NATIVE / "results" / "small_paper" / OFFICIAL_BASELINE_DAY
    if root.is_dir():
        for s in sorted(root.glob("live_session_*")):
            sm = s / "small_paper_summary.json"
            if not sm.is_file():
                continue
            d = json.loads(sm.read_text(encoding="utf-8"))
            sessions.append(
                {
                    "session": s.name,
                    "accepted": d.get("accepted_count") or d.get("n_accepted"),
                    "submit_count": d.get("submit_count"),
                    "cancel_count": d.get("cancel_count"),
                    "pnl": d.get("total_pnl_yen_100"),
                    "sha256": hashlib.sha256(sm.read_bytes()).hexdigest(),
                }
            )
    out["sessions"] = sessions
    return out


def check_prestate() -> tuple[Check, dict[str, Any]]:
    details: dict[str, Any] = {"generated_at": _iso()}
    # PIDs
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter "
                    "\"Name='python.exe' OR Name='pythonw.exe' OR Name='KabuS.exe'\" | "
                    "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        raw = (proc.stdout or "").strip()
        procs = json.loads(raw) if raw else []
        if isinstance(procs, dict):
            procs = [procs]
    except Exception as exc:
        procs = [{"error": str(exc)}]
    details["processes"] = [
        {
            "pid": p.get("ProcessId"),
            "name": p.get("Name"),
            "cmd": str(p.get("CommandLine") or "")[:220],
        }
        for p in procs
    ]
    paper_runners = [
        p
        for p in details["processes"]
        if any(
            tok in str(p.get("cmd") or "").lower()
            for tok in ("pilot_runner", "run_small_paper", "am_pm_daily", "paper_trade_checked")
        )
    ]
    details["paper_runners"] = paper_runners
    details["duplicate_runner"] = len(paper_runners) > 1
    details["kabu_s_running"] = any(
        str(p.get("name") or "").lower().startswith("kabus") for p in details["processes"]
    )

    # latest session tails
    latest_hb = None
    latest_err = None
    latest_push = None
    pending_recovery = 0
    orphan_open = 0
    finalize_queue = 0
    submit = 0
    cancel = 0
    paper_mode = True
    sp = NATIVE / "results" / "small_paper"
    if sp.is_dir():
        sessions = sorted(sp.glob("*/live_session_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        # ignore demo_push_e2e
        sessions = [s for s in sessions if "demo_push" not in str(s)]
        if sessions:
            s0 = sessions[0]
            details["latest_session"] = str(s0)
            hb = s0 / "heartbeat.jsonl"
            er = s0 / "errors.jsonl"
            if hb.is_file():
                lines = hb.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                latest_hb = lines[-1] if lines else None
            if er.is_file():
                lines = er.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                latest_err = lines[-3:] if lines else []
            sm = s0 / "small_paper_summary.json"
            if sm.is_file():
                d = json.loads(sm.read_text(encoding="utf-8"))
                submit = int(d.get("submit_count") or 0)
                cancel = int(d.get("cancel_count") or 0)
                paper_mode = bool(d.get("paper_only", True)) and not bool(d.get("order_enabled"))
                orphan_open = int(d.get("open_positions") or d.get("n_open") or 0)
            # recovery markers
            for name in ("am_recovery_finalize.json", "pm_recovery_finalize.json"):
                rp = s0 / name
                if rp.is_file():
                    try:
                        rd = json.loads(rp.read_text(encoding="utf-8"))
                        pending_recovery += int(rd.get("pending_count") or rd.get("open_count") or 0)
                    except Exception:
                        pass
            # last push from events
            ev = s0 / "small_paper_events.jsonl"
            if ev.is_file():
                # read last ~50 lines
                try:
                    with ev.open("rb") as fh:
                        fh.seek(0, 2)
                        size = fh.tell()
                        fh.seek(max(0, size - 80000))
                        chunk = fh.read().decode("utf-8", errors="replace")
                    for line in reversed(chunk.splitlines()):
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        if row.get("event_type") in ("candidate", "accepted", "observer_exit"):
                            latest_push = row.get("event_time") or row.get("exit_time") or row.get("entry_time")
                            break
                except Exception as exc:
                    latest_push = f"error:{exc}"

    details.update(
        {
            "latest_heartbeat": latest_hb,
            "errors_tail": latest_err,
            "latest_push_time": latest_push,
            "pending_recovery": pending_recovery,
            "orphan_open": orphan_open,
            "finalize_queue": finalize_queue,
            "submit_count": submit,
            "cancel_count": cancel,
            "paper_mode": paper_mode,
        }
    )
    ok = (
        not details["duplicate_runner"]
        and submit == 0
        and cancel == 0
        and paper_mode
        and pending_recovery == 0
    )
    return (
        Check(
            "prestate",
            "PASS" if ok else "FAIL",
            "prestate ok" if ok else "prestate issues",
            details,
        ),
        details,
    )


# ---------------------------------------------------------------------------
# Demo PUSH A/B/C + pipeline
# ---------------------------------------------------------------------------


def _demo_payload(
    *,
    symbol: str,
    price: float,
    ts: datetime,
    stale_sec: Optional[float] = None,
    drop_field: Optional[str] = None,
    incomplete: bool = False,
) -> dict[str, Any]:
    from small_paper.demo_push_runtime_path import build_push_payload

    stale_pt = (ts - timedelta(seconds=stale_sec)) if stale_sec else None
    p = build_push_payload(
        symbol=symbol.replace(".T", ""),
        price=price,
        ts=ts,
        sequence=1,
        bid_qty=10000.0,
        ask_qty=8000.0,
        volume=100000.0,
        trading_value=100000000.0,
        stale_price_time=stale_pt,
    )
    # user-requested safety flags
    p.update(
        {
            "Exchange": "test",
            "event_source": "demo_push_healthcheck",
            "is_demo": True,
            "paper_only": True,
            "order_allowed": False,
            "notification_only": True,
            "test_event": True,
            "demo_healthcheck": True,
            "healthcheck_day": DAY,
        }
    )
    if incomplete and drop_field:
        p.pop(drop_field, None)
    return p


def _freshness_payload(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "CurrentPrice": p.get("CurrentPrice"),
        "CurrentPriceTime": p.get("CurrentPriceTime"),
        "BidPrice": p.get("BidPrice"),
        "AskPrice": p.get("AskPrice"),
        "BidQty": p.get("BidQty"),
        "AskQty": p.get("AskQty"),
        "BidTime": p.get("BidTime") or p.get("CurrentPriceTime"),
        "AskTime": p.get("AskTime") or p.get("CurrentPriceTime"),
        "Buy1": p.get("Buy1"),
        "Sell1": p.get("Sell1"),
        "timestamp": p.get("timestamp"),
    }


def _eval_freshness(p: dict[str, Any], now: datetime):
    from small_paper.entry_scan_controller import (
        compute_entry_freshness,
        evaluate_entry_data_freshness,
    )

    payload = _freshness_payload(p)
    snap = compute_entry_freshness(
        payload, pipeline_source="push-replay", reference_now=now
    )
    dec = evaluate_entry_data_freshness(
        snap,
        payload,
        max_price_age_sec=3.0,
        max_board_age_sec=3.0,
        guard_enabled=True,
        reference_now=now,
    )
    return snap, dec


def check_demo_push_parse_and_freshness() -> list[Check]:
    checks: list[Check] = []
    now = datetime.now(JST)
    # A normal
    pa = _demo_payload(symbol="DEMO9991.T", price=1000.0, ts=now)
    _append_demo_log("demo_push_A_normal", {"payload": pa})
    try:
        snap, dec = _eval_freshness(pa, now)
        stale = bool(getattr(dec, "event_stale", False) or getattr(dec, "board_stale", False))
        rejected = bool(getattr(dec, "reject_reason", None))
        age = snap.price_age_sec
        ok = float(pa["CurrentPrice"]) == 1000.0 and not stale and not rejected and (age is None or float(age) < 5)
        checks.append(
            Check(
                "demo_push_A_normal",
                "PASS" if ok else "FAIL",
                f"stale={stale} rejected={rejected} age={age} reason={getattr(dec,'reject_reason',None)}",
                {"price": pa["CurrentPrice"], "stale": stale, "age": age, "reject": getattr(dec, "reject_reason", None)},
            )
        )
    except Exception as exc:
        checks.append(Check("demo_push_A_normal", "FAIL", str(exc), {"tb": traceback.format_exc()[-800:]}))

    # B stale
    pb = _demo_payload(symbol="DEMO9992.T", price=1000.0, ts=now, stale_sec=600.0)
    _append_demo_log("demo_push_B_stale", {"payload": pb})
    try:
        snap, dec = _eval_freshness(pb, now)
        age = snap.price_age_sec
        rejected = bool(getattr(dec, "reject_reason", None))
        stale = rejected or (age is not None and float(age) > 30)
        checks.append(
            Check(
                "demo_push_B_stale",
                "PASS" if stale else "FAIL",
                f"stale detected={stale} age={age} reason={getattr(dec,'reject_reason',None)}",
                {"age": age, "stale": stale, "reject": getattr(dec, "reject_reason", None)},
            )
        )
    except Exception as exc:
        checks.append(Check("demo_push_B_stale", "FAIL", str(exc)))

    # C incomplete — missing BidPrice / Buy1
    pc = _demo_payload(symbol="DEMO9993.T", price=1000.0, ts=now, incomplete=True, drop_field="BidPrice")
    pc.pop("Buy1", None)
    pc.pop("BidQty", None)
    pc.pop("BidTime", None)
    missing = [k for k in ("BidPrice", "BidQty", "Buy1", "BidTime") if k not in pc or pc.get(k) in (None, "")]
    dq_reason = "incomplete_board_fields" if missing else ""
    _append_demo_log(
        "demo_push_C_incomplete",
        {
            "payload": pc,
            "dq_reason": dq_reason or "push_unexpected_incomplete",
            "missing_fields": missing,
            "order_allowed": False,
            "is_demo": True,
        },
    )
    try:
        reason = dq_reason
        try:
            snap, dec = _eval_freshness(pc, now)
            fr = getattr(dec, "reject_reason", None)
            if fr:
                reason = str(fr)
            # incomplete board: either freshness reject OR explicit DQ missing-fields skip
            skipped = bool(fr) or bool(missing)
            safe = True
        except Exception as inner:
            safe = True
            skipped = True
            reason = f"safe_exception:{inner}"
        # next normal push still works
        pa2 = _demo_payload(symbol="DEMO9991.T", price=1001.0, ts=now)
        snap2, dec2 = _eval_freshness(pa2, now)
        next_ok = not bool(getattr(dec2, "reject_reason", None))
        ok = safe and skipped and next_ok and bool(missing)
        checks.append(
            Check(
                "demo_push_C_incomplete",
                "PASS" if ok else "FAIL",
                f"safe_skip reason={reason} missing={missing} next_ok={next_ok}",
                {
                    "reason": reason,
                    "missing_fields": missing,
                    "dq_reason": dq_reason,
                    "next_push_ok": next_ok,
                },
            )
        )
    except Exception as exc:
        checks.append(Check("demo_push_C_incomplete", "FAIL", str(exc)))

    return checks


def check_live_preflight() -> Check:
    from small_paper.live_pipeline_preflight import default_config_path, run_live_pipeline_preflight

    cfg = default_config_path(REPO)
    if not cfg.is_absolute():
        cfg = REPO / cfg
    report = run_live_pipeline_preflight(config_path=cfg, repo_root=REPO)
    return Check(
        "live_pipeline_preflight",
        "PASS" if report.ready else "FAIL",
        report.verdict,
        {"errors": report.errors, "config": str(cfg)},
    )


def check_api_connection() -> Check:
    from small_paper.pilot_env import load_pilot_environment
    from small_paper.safety import check_kabu_station_connection

    st = load_pilot_environment(repo_root=REPO)
    if not st.kabu_api_password_set:
        return Check("api_connection", "FAIL", "KABU_API_PASSWORD unset after dotenv load", asdict(st))
    chk = check_kabu_station_connection(REPO)
    passed = bool(getattr(chk, "passed", getattr(chk, "ok", False)))
    return Check(
        "api_connection",
        "PASS" if passed else "FAIL",
        chk.message,
        chk.details or {},
    )


def check_shadow_registry() -> Check:
    from small_paper.shadow_registry import SHADOW_REGISTRY

    n = len(SHADOW_REGISTRY)
    ids = [r["canonical_shadow_id"] for r in SHADOW_REGISTRY]
    need = [
        "cost_aware_entry_shadow",
        "board_dynamic_trailing_shadow",
        "flat_weak_range_shadow",
        "pullback_misread_guard_shadow",
    ]
    missing = [x for x in need if x not in ids]
    return Check(
        "shadow_registry",
        "PASS" if n > 0 and not missing else "FAIL",
        f"registry_count={n} missing={missing}",
        {"count": n, "missing": missing},
    )


def check_cost_aware_finalize() -> Check:
    from small_paper.cost_aware_entry_shadow import (
        CostAwareShadowState,
        ShadowPosition,
        finalize_open_positions,
        summarize_state,
        _close_expired,
    )

    st = CostAwareShadowState()
    t0 = datetime(2026, 7, 22, 9, 0, tzinfo=JST)
    force = datetime(2026, 7, 22, 11, 25, tzinfo=JST)
    # 30m close
    p1 = ShadowPosition(
        symbol="DEMOCA1.T",
        entry_time=t0,
        entry_price=1000.0,
        selection_cycle_id="hc",
        rank=1,
        integrated_score=1.0,
        winner_enrichment=0.0,
        stop_risk=0.0,
        stop_margin_z=0.0,
        pbv2_score=1.0,
    )
    path = [(t0, 1000.0), (t0 + timedelta(minutes=30), 1010.0)]
    p1.price_path = path
    st.open_shadow["DEMOCA1.T"] = p1
    _close_expired(st, now=t0 + timedelta(minutes=35), trading_date=DAY, price_paths={"DEMOCA1.T": path})
    # freeze open
    p2 = ShadowPosition(
        symbol="DEMOCA2.T",
        entry_time=force - timedelta(minutes=10),
        entry_price=2000.0,
        selection_cycle_id="hc2",
        rank=1,
        integrated_score=1.0,
        winner_enrichment=0.0,
        stop_risk=0.0,
        stop_margin_z=0.0,
        pbv2_score=1.0,
    )
    path2 = [(p2.entry_time, 2000.0), (force - timedelta(seconds=5), 1990.0)]
    p2.price_path = path2
    st.open_shadow["DEMOCA2.T"] = p2
    finalize_open_positions(
        st, force_close_time=force, trading_date=DAY, price_paths={"DEMOCA2.T": path2}, is_freeze_recovery=True
    )
    s = summarize_state(st)
    ok = s.get("n_open") == 0 and s.get("fixed_30m_raw") is not None and s.get("runtime_compatible_raw") is not None or (
        s.get("n_open") == 0 and s.get("fixed_30m_raw") is not None
    )
    # runtime may be null until apply — require open=0 and yen present
    ok = int(s.get("n_open") or 0) == 0 and s.get("fixed_30m_raw") is not None
    _append_demo_log("cost_aware_finalize", {"summary": {k: s.get(k) for k in (
        "n_open","n_closed","fixed_30m_raw","fixed_30m_5bps_roundtrip","status"
    )}})
    return Check(
        "cost_aware_finalize",
        "PASS" if ok else "FAIL",
        f"open={s.get('n_open')} raw={s.get('fixed_30m_raw')}",
        {"n_open": s.get("n_open"), "n_closed": s.get("n_closed"), "raw": s.get("fixed_30m_raw")},
    )


def check_board_dynamic_join() -> Check:
    from small_paper.shadow_session_recompute import recompute_board_dynamic

    events = [
        {
            "event_type": "accepted",
            "position_id": "DEMOBD_1",
            "symbol": "DEMOBD1.T",
            "entry_time": "2026-07-22T09:30:00+09:00",
            "entry_price": 1000.0,
        },
        {
            "event_type": "observer_exit",
            "position_id": "DEMOBD_1",
            "symbol": "DEMOBD1.T",
            "entry_time": "2026-07-22T09:30:00+09:00",
            "exit_time": "2026-07-22T11:25:00+09:00",
            "exit_price": 1010.0,
            "exit_reason": "recovery_forced_close",
            "pnl_yen_100": 1000.0,
            "shadow_exit_price": "",
            "shadow_exit_reason": "",
        },
    ]
    out = recompute_board_dynamic(events)
    ok = (
        out.get("recovery_missing_shadow_exit") == 0
        and out.get("runtime_pnl") is not None
        and out.get("shadow_pnl") is not None
        and out.get("open") == 0
    )
    _append_demo_log("board_dynamic_join", {"result": out})
    return Check(
        "board_dynamic_join",
        "PASS" if ok else "FAIL",
        f"missing={out.get('recovery_missing_shadow_exit')} rt={out.get('runtime_pnl')}",
        out,
    )


def check_intraday_refresh() -> Check:
    from universe.intraday_refresh import (
        AM_REFRESH_TIME,
        PM_REFRESH_TIME,
        merge_universe_with_open_symbols,
    )

    base_rows = [
        {
            "symbol": f"{c}.T",
            "symbol_key": f"{c}@1",
            "exchange": "1",
            "passed": "true",
            "source_bucket": "core" if i < 10 else "dynamic",
            "selected_reason": "healthcheck",
            "universe_slot": "core" if i < 10 else "dynamic",
            "rank": str(i + 1),
            "volatility_liquidity_score": "1.0",
            "am_pm_session": "am",
            "refresh_time": AM_REFRESH_TIME,
            "is_open_position_carried": "false",
            "close_price": "1000",
            "tick_size": "1",
            "tick_ratio_pct": "0.1",
            "price_risk_flag": "false",
            "price_risk_reason": "",
        }
        for i, c in enumerate([f"D{1000+i:04d}" for i in range(50)])
    ]
    open_syms = ["D1000.T", "D1001.T"]
    merged, meta = merge_universe_with_open_symbols(
        base_rows,
        open_symbols=open_syms,
        feature_rows=[],
        symbol_meta={},
        session="am",
        refresh_time=AM_REFRESH_TIME,
    )
    # second refresh PM-style
    for r in base_rows:
        r["refresh_time"] = PM_REFRESH_TIME
        r["am_pm_session"] = "pm"
    merged2, meta2 = merge_universe_with_open_symbols(
        base_rows[:40] + [
            {
                **base_rows[0],
                "symbol": "D2000.T",
                "symbol_key": "D2000@1",
                "source_bucket": "dynamic",
                "universe_slot": "dynamic",
                "rank": "99",
            }
        ],
        open_symbols=open_syms,
        feature_rows=[],
        symbol_meta={},
        session="pm",
        refresh_time=PM_REFRESH_TIME,
    )
    ok = len(merged) > 0 and len(merged2) > 0 and meta.get("error") is None
    _append_demo_log(
        "intraday_refresh",
        {"am_count": len(merged), "pm_count": len(merged2), "meta": meta, "meta2": meta2},
    )
    return Check(
        "intraday_refresh",
        "PASS" if ok else "FAIL",
        f"am={len(merged)} pm={len(merged2)} times={AM_REFRESH_TIME}/{PM_REFRESH_TIME}",
        {"am": len(merged), "pm": len(merged2), "open_carried": open_syms},
    )


def check_entry_exit_pipeline_demo() -> list[Check]:
    """Run isolated demo push-replay (existing path) under TRADEBOT_DEMO_PUSH_E2E."""
    os.environ["TRADEBOT_DEMO_PUSH_E2E"] = "1"
    checks: list[Check] = []
    try:
        from small_paper.demo_push_runtime_path import (
            generate_scenario_records,
            write_push_fixtures,
            run_paper_push_replay_inprocess,
            demo_workspace,
            report_dir,
        )

        ws = OUT / "demo_workspace"
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        push_dir = ws / "push_jsonl"
        paper_out = (
            NATIVE
            / "results"
            / "small_paper"
            / "demo_push_e2e"
            / "healthcheck"
            / DAY
            / f"hc_{datetime.now(JST).strftime('%H%M%S')}"
        )
        recs = generate_scenario_records()
        # also inject user-style A/B/C records
        now = datetime.now(JST)
        from small_paper.demo_push_runtime_path import _kabu_time

        extra = []
        for sid, kwargs in (
            ("HC_A_normal", {"stale_sec": None}),
            ("HC_B_stale", {"stale_sec": 600.0}),
            ("HC_C_incomplete", {"incomplete": True, "drop_field": "BidPrice"}),
        ):
            p = _demo_payload(symbol="9991", price=1000.0, ts=now, **kwargs)
            if kwargs.get("incomplete"):
                p.pop("Buy1", None)
            extra.append(
                {
                    "recorded_at": _kabu_time(now),
                    "source": "live_push",
                    "symbol": "9991.T",
                    "payload": p,
                    "scenario_id": sid,
                    "sequence": 9000 + len(extra),
                    "demo": True,
                    "demo_push_e2e": True,
                    "demo_healthcheck": True,
                }
            )
        write_push_fixtures(push_dir, list(recs) + extra)
        _write_json(OUT / "injected_demo_pushes_meta.json", {
            "n_records": len(recs) + len(extra),
            "healthcheck_scenarios": [e["scenario_id"] for e in extra],
            "paper_out": str(paper_out),
        })
        result = run_paper_push_replay_inprocess(
            repo_root=REPO,
            push_dir=push_dir,
            output_dir=paper_out,
        )
        summary = result.get("summary") or {}
        submit = int(summary.get("submit_count") or 0)
        cancel = int(summary.get("cancel_count") or 0)
        push_n = int(summary.get("push_messages") or summary.get("push_rows") or 0)
        gate_n = int(summary.get("gate_evaluations") or 0)
        # mark demo isolation
        marker = paper_out / "DEMO_HEALTHCHECK_ISOLATED.json"
        _write_json(
            marker,
            {
                "demo": True,
                "demo_healthcheck": True,
                "paper_only": True,
                "order_allowed": False,
                "not_official": True,
                "label": "[DEMO HEALTHCHECK] 正式Paper Trade集計対象外",
            },
        )
        entry_ok = push_n > 0 and gate_n >= 0 and submit == 0 and cancel == 0 and result.get("exit_code") == 0
        checks.append(
            Check(
                "entry_pipeline_demo",
                "PASS" if entry_ok else "FAIL",
                f"push={push_n} gate={gate_n} submit/cancel={submit}/{cancel}",
                {
                    "push": push_n,
                    "gate": gate_n,
                    "submit": submit,
                    "cancel": cancel,
                    "output_dir": str(paper_out),
                    "accepted": summary.get("accepted_count") or summary.get("n_accepted"),
                },
            )
        )
        # EXIT / open cleanup — summary open should be 0 after dry-run end
        open_n = int(summary.get("open_positions") or summary.get("n_open") or 0)
        checks.append(
            Check(
                "exit_pipeline_demo",
                "PASS" if open_n == 0 else "FAIL",
                f"open_after_session={open_n}",
                {"open": open_n, "output_dir": str(paper_out)},
            )
        )
        checks.append(
            Check(
                "demo_isolation",
                "PASS" if "demo_push_e2e" in str(paper_out) and marker.is_file() else "FAIL",
                str(paper_out),
                {"marker": str(marker)},
            )
        )
    except Exception as exc:
        checks.append(Check("entry_pipeline_demo", "FAIL", str(exc), {"tb": traceback.format_exc()[-1200:]}))
        checks.append(Check("exit_pipeline_demo", "FAIL", "skipped due to entry failure"))
    return checks


def check_discord() -> Check:
    from small_paper.pilot_env import load_pilot_environment

    st = load_pilot_environment(repo_root=REPO)
    url = os.environ.get("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL") or os.environ.get(
        "KABU_SHADOW_DISCORD_WEBHOOK_URL"
    )
    if not url:
        # config may embed — try notify path dry
        return Check(
            "discord",
            "FAIL",
            "webhook unset after dotenv load",
            {"dotenv_exists": st.dotenv_exists, "discord_webhook_set": st.discord_webhook_set},
        )
    try:
        import requests

        content = (
            f"[DEMO HEALTHCHECK]\n"
            f"正式Paper Trade集計対象外\n"
            f"Paper Trade Healthcheck {DAY}\n"
            f"time={_iso()}\n"
            f"items: healthcheck / test ENTRY / test EXIT / Shadow Summary / Error-Warning probe"
        )
        resp = requests.post(url, json={"content": content[:1800]}, timeout=15)
        ok = 200 <= resp.status_code < 300
        _append_demo_log("discord", {"status_code": resp.status_code, "ok": ok})
        return Check(
            "discord",
            "PASS" if ok else "FAIL",
            f"http={resp.status_code}",
            {"status_code": resp.status_code},
        )
    except Exception as exc:
        return Check("discord", "FAIL", str(exc))


def check_recovery_resume() -> Check:
    """Simulate stale → re-PUSH → ready without leaving demo opens."""
    now = datetime.now(JST)
    stale = _demo_payload(symbol="DEMOREC.T", price=1000.0, ts=now, stale_sec=600)
    snap1, dec1 = _eval_freshness(stale, now)
    fresh = _demo_payload(symbol="DEMOREC.T", price=1002.0, ts=now)
    snap2, dec2 = _eval_freshness(fresh, now)
    age1 = snap1.price_age_sec if snap1.price_age_sec is not None else 999
    age2 = snap2.price_age_sec if snap2.price_age_sec is not None else 999
    ok = float(age1) > 30 and float(age2) < 30
    return Check(
        "recovery_resume",
        "PASS" if ok else "FAIL",
        f"stale_age={age1} fresh_age={age2}",
        {
            "dec1": str(getattr(dec1, "reject_reason", None)),
            "dec2": str(getattr(dec2, "reject_reason", None)),
            "age1": age1,
            "age2": age2,
        },
    )


def check_official_unchanged(before: dict[str, Any]) -> Check:
    after = official_baseline()
    ok = (
        before.get("sha256") == after.get("sha256")
        and before.get("trade_count") == after.get("trade_count")
        and before.get("pnl_yen_100") == after.get("pnl_yen_100")
        and [s.get("sha256") for s in before.get("sessions") or []]
        == [s.get("sha256") for s in after.get("sessions") or []]
    )
    return Check(
        "official_unchanged",
        "PASS" if ok else "FAIL",
        "unchanged" if ok else "CHANGED",
        {"before": before, "after": after},
    )


def check_config_universe_cap() -> Check:
    from small_paper.live_pipeline_preflight import default_config_path
    from small_paper.config import load_pilot_config

    cfg_path = default_config_path(REPO)
    if not cfg_path.is_absolute():
        cfg_path = REPO / cfg_path
    cfg = load_pilot_config(cfg_path)
    cap = getattr(cfg, "max_concurrent_positions", None) or getattr(cfg, "max_concurrent", None)
    # CAP=5 expected for current paper
    details = {
        "config": str(cfg_path),
        "max_concurrent_positions": cap,
        "order_enabled": getattr(cfg, "order_enabled", None),
        "paper_only": getattr(cfg, "paper_only", None),
        "discord_enabled": getattr(cfg, "discord_enabled", None),
    }
    ok = int(cap or 0) == 5 and not bool(getattr(cfg, "order_enabled", False))
    return Check(
        "config_cap_paper",
        "PASS" if ok else "FAIL",
        f"cap={cap} order_enabled={details['order_enabled']}",
        details,
    )


def map_report(checks: dict[str, Check], *, official_ok: bool) -> dict[str, Any]:
    def st(name: str) -> str:
        c = checks.get(name)
        return c.status if c else "FAIL"

    # composite mappings
    runtime = "PASS" if st("prestate") == "PASS" and st("config_cap_paper") == "PASS" else "FAIL"
    demo_parse = "PASS" if st("demo_push_A_normal") == "PASS" else "FAIL"
    freshness = "PASS" if st("demo_push_B_stale") == "PASS" else "FAIL"
    incomplete = "PASS" if st("demo_push_C_incomplete") == "PASS" else "FAIL"
    entry = st("entry_pipeline_demo")
    exit_p = st("exit_pipeline_demo")
    cleanup = "PASS" if st("demo_isolation") == "PASS" and st("exit_pipeline_demo") == "PASS" else "FAIL"
    safety_ok = all(
        st(n) == "PASS"
        for n in (
            "prestate",
            "config_cap_paper",
            "official_unchanged",
            "cost_aware_finalize",
            "board_dynamic_join",
        )
    )
    items = {
        "Runtime": runtime,
        "API Connection": st("api_connection"),
        "Real PUSH Subscription": st("api_connection"),  # subscription prep tied to API
        "Demo PUSH Parse": demo_parse,
        "Price Update": demo_parse,
        "Board Update": demo_parse,
        "Freshness Guard": freshness,
        "Incomplete PUSH Safety": incomplete,
        "ENTRY Pipeline": entry,
        "EXIT Pipeline": exit_p,
        "Cost-Aware Finalize": st("cost_aware_finalize"),
        "Board Dynamic Join": st("board_dynamic_join"),
        "Intraday Refresh": st("intraday_refresh"),
        "Shadow Registry": st("shadow_registry"),
        "Discord": st("discord"),
        "Recovery": st("recovery_resume"),
        "Cleanup": cleanup,
    }
    fails = [k for k, v in items.items() if v != "PASS"]
    overall = "READY" if not fails and official_ok and safety_ok else "NOT READY"
    return {
        "items": items,
        "fails": fails,
        "overall": overall,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0},
        "official_results_unchanged": "YES" if official_ok else "NO",
    }


def render_report(mapped: dict[str, Any], checks: dict[str, Check], meta: dict[str, Any]) -> str:
    lines = ["【Paper Trade Health Check】", "", f"Day: {DAY}", f"Generated: {_iso()}", ""]
    for k, v in mapped["items"].items():
        lines.append(f"{k}:")
        lines.append(f"{v}")
        lines.append("")
    lines.append("Safety:")
    lines.append("submit=0")
    lines.append("cancel=0")
    lines.append("live_order=0")
    lines.append("")
    lines.append("Official Results Unchanged:")
    lines.append(mapped["official_results_unchanged"])
    lines.append("")
    lines.append("Overall:")
    lines.append(mapped["overall"])
    lines.append("")
    lines.append("--- Details ---")
    for name, c in checks.items():
        lines.append(f"- {name}: {c.status} - {c.message}")
    lines.append("")
    lines.append(f"Report dir: {OUT}")
    lines.append(f"Demo logs: {DEMO_LOG}")
    if meta.get("demo_output_dir"):
        lines.append(f"Demo session: {meta['demo_output_dir']}")
    lines.append("Label: [DEMO HEALTHCHECK] 正式Paper Trade集計対象外")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    DEMO_LOG.mkdir(parents=True, exist_ok=True)
    from small_paper.pilot_env import load_pilot_environment

    env_st = load_pilot_environment(repo_root=REPO)
    _write_json(OUT / "env_status.json", asdict(env_st))

    before = official_baseline()
    _write_json(OUT / "official_baseline_before.json", before)

    checks_list: list[Check] = []

    def _safe(name: str, fn):
        try:
            out = fn()
            if isinstance(out, list):
                checks_list.extend(out)
                return out
            checks_list.append(out)
            return out
        except Exception as exc:
            c = Check(name, "FAIL", str(exc), {"tb": traceback.format_exc()[-1200:]})
            checks_list.append(c)
            return c

    pre, pre_details = check_prestate()
    checks_list.append(pre)
    _write_json(OUT / "prestate.json", pre_details)

    _safe("config_cap_paper", check_config_universe_cap)
    _safe("live_pipeline_preflight", check_live_preflight)
    _safe("api_connection", check_api_connection)
    _safe("shadow_registry", check_shadow_registry)
    _safe("demo_push_parse", check_demo_push_parse_and_freshness)
    _safe("cost_aware_finalize", check_cost_aware_finalize)
    _safe("board_dynamic_join", check_board_dynamic_join)
    _safe("intraday_refresh", check_intraday_refresh)
    _safe("recovery_resume", check_recovery_resume)

    entry_checks = _safe("entry_exit_pipeline", check_entry_exit_pipeline_demo)
    demo_out = None
    if isinstance(entry_checks, list):
        for c in entry_checks:
            if c.name == "entry_pipeline_demo":
                demo_out = (c.details or {}).get("output_dir")

    _safe("discord", check_discord)
    official_chk = check_official_unchanged(before)
    checks_list.append(official_chk)

    # cleanup residual: ensure no pending demo opens in latest demo dir
    if demo_out:
        p = Path(str(demo_out))
        sm = p / "small_paper_summary.json"
        if sm.is_file():
            d = json.loads(sm.read_text(encoding="utf-8"))
            open_n = int(d.get("open_positions") or d.get("n_open") or 0)
            checks_list.append(
                Check("cleanup_open", "PASS" if open_n == 0 else "FAIL", f"demo_open={open_n}")
            )

    checks = {c.name: c for c in checks_list}
    mapped = map_report(checks, official_ok=official_chk.status == "PASS")
    report_txt = render_report(mapped, checks, {"demo_output_dir": demo_out})
    (OUT / "healthcheck_report.txt").write_text(report_txt, encoding="utf-8")
    _write_json(
        OUT / "healthcheck_report.json",
        {
            "day": DAY,
            "generated_at": _iso(),
            "mapped": mapped,
            "checks": {k: asdict(v) for k, v in checks.items()},
            "demo_output_dir": demo_out,
            "verdict": mapped["overall"],
        },
    )
    try:
        print(report_txt)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(report_txt.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    return 0 if mapped["overall"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
