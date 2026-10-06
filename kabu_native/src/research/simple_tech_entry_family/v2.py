"""Offline SIMPLE_TECH_PULLBACK_V2_EVENT_TRIGGER. Trigger timing only. No Runtime/Capture writes."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_c0_indicator_exit import C0_OVERLAY_LOCKED, CURRENT_LOCKED
from research.am_entry_profit_improvement import (
    C14_ID,
    CANCEL_N,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
)
from research.am_entry_profit_improvement.metrics import economic_pack, paired_delta
from research.am_current_utility_augment.overlay import overlay_replay
from research.canonical_entry_performance_rebase.analyze import session_of
from research.simple_tech_entry_family.analyze import classify_trade
from research.simple_tech_entry_family.harvest import CACHE, load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import (
    TODAY,
    V1_OUT,
    V2_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.portfolio import portfolio_replay
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v2_analyze import (
    fill_cliff,
    mechanism_supported,
    nested_mismatch,
    next_deficiency,
    recovery_pack,
    robustness,
    v1_cliff_locked,
    v1_v2_counterfactual,
)
from research.simple_tech_entry_family.v2_harvest import V2_CACHE, process_v2_day, save_v2_day_cache
from research.simple_tech_entry_family.v2_publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v2_spec import (
    ANALYSIS_ID,
    CHANGED_COMPONENT,
    PARENT_SPEC_SHA256_EXPECTED,
    PARENT_STRATEGY_ID,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    STRATEGY_ID,
    TRUE_OOS,
    V1_LOCKED,
    V3_IMPLEMENTED,
    canonical_v2_spec,
    spec_sha256_v2,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

REGIME_LABELED = (
    NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information" / "labeled_am_regime.json"
)

INTEGRITY_ZERO = (
    "LIVE_PROCESS_CONTROL_CALL_N",
    "RUNTIME_WRITE_N",
    "CAPTURE_WRITE_N",
    "ADDITIONAL_WEBSOCKET_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "KABUS_RESTART_N",
    "CAPTURE_RESTART_N",
    "RUNTIME_RESTART_N",
    "MACD_ADDED_N",
    "ADX_ADDED_N",
    "RSI_ADDED_N",
    "ML_USE_N",
    "SCORE_USE_N",
    "TOPK_RANKING_N",
    "FUTURE_FEATURE_USE_N",
    "FUTURE_LABEL_AS_FEATURE_N",
    "PM_ROWS_USED_N",
    "WAIT_SEC_RETUNE_N",
    "LIMIT_IMPROVEMENT_N",
    "ASK_ENTRY_N",
    "MARKETABLE_ENTRY_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _slim_pack(p: dict[str, Any]) -> dict[str, Any]:
    return {
        k: p.get(k)
        for k in (
            "trade_count",
            "net_pnl_yen_100",
            "profit_factor",
            "max_drawdown_yen_100",
            "win_n",
            "loss_n",
            "flat_n",
            "positive_day_n",
            "negative_day_n",
            "zero_day_n",
            "median_daily_pnl",
            "mean_daily_pnl",
        )
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v2_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    leak.setdefault("RUNTIME_WRITE_N", 0)
    leak.setdefault("CAPTURE_WRITE_N", 0)
    leak.setdefault("ADDITIONAL_WEBSOCKET_N", 0)
    leak.setdefault("ACTIVE_CAPTURE_INPUT_N", leak.get("RESEARCH_INPUT_ACTIVE_FILE_N") or 0)
    req = {
        "STRATEGY_ID": STRATEGY_ID,
        "VERDICT": "STOP",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "TRIGGER_MECHANISM_SUPPORTED": False,
        "PRIMARY_DEFICIENCY_AFTER_V2": None,
        "PARENT_SPEC_SHA256": parent_sha,
        "V2_SPEC_SHA256": v2_sha,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "extra": extra or {},
        "_markdown": build_markdown({"required": req}),
    }
    sheets = {
        "Precommit": kv_rows({"PARENT_SPEC_SHA256": parent_sha, "V2_SPEC_SHA256": v2_sha, "STRATEGY_ID": STRATEGY_ID}),
        "Non_Interference": kv_rows(pre),
        "Integrity": kv_rows({"blocker": msg, **leak}),
    }
    write_artifacts(report, sheets)
    print(msg, flush=True)
    return 2


def _load_current() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    locked = {
        "trade_count": CURRENT_LOCKED["TRADE_N"],
        "net_pnl_yen_100": CURRENT_LOCKED["NET"],
        "profit_factor": CURRENT_LOCKED["PF"],
        "max_drawdown_yen_100": CURRENT_LOCKED["DD"],
        "daily": [],
    }
    if not REGIME_LABELED.is_file():
        return locked, [], {"ok": False, "reason": "LABELED_MISSING"}
    body = _load(REGIME_LABELED)
    rows = list(body.get("rows") or [])
    pm = sum(1 for r in rows if session_of(r) != "AM")
    if pm:
        return locked, rows, {"ok": False, "reason": "PM_IN_LABELED", "pm_n": pm}
    try:
        base = overlay_replay(rows, include_augment=False)
        cur = [t for t in (base.get("trades") or []) if str(t.get("arm") or "CURRENT") == "CURRENT"]
        pack = economic_pack(cur, list(ELIGIBLE_DAYS))
        return pack, rows, {"ok": True, "source": "overlay_replay"}
    except Exception as exc:
        return locked, rows, {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}


def _verdict(supported: bool, defn: str, ni_ok: bool) -> dict[str, str]:
    if not ni_ok:
        return {"VERDICT": "STOP", "NEXT": "NON_INTERFERENCE_FAIL"}
    if supported:
        return {
            "VERDICT": "SIMPLE_TECH_V2_TRIGGER_MECHANISM_SUPPORTED",
            "NEXT": (
                f"V1 main problem was completed-bar ENTRY timing, not MA/BB/RCI/Volume thought. "
                f"Family stays open. Next PRIMARY_DEFICIENCY={defn}. Do not adopt Runtime. Do not implement V3 here."
            ),
        }
    return {
        "VERDICT": "SIMPLE_TECH_V2_TRIGGER_MECHANISM_NOT_SUPPORTED",
        "NEXT": (
            f"V2 did not causally repair the fill-quality cliff. PRIMARY_DEFICIENCY_AFTER_V2={defn}. "
            f"Do not search 2-tick/5s/10s confirms. Family stays open. Do not adopt Runtime."
        ),
    }


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v2_spec()
    v2_sha = spec_sha256_v2(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
    )
    print(
        f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} "
        f"parent={parent_sha[:12]} v2={v2_sha[:12]}",
        flush=True,
    )
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if spec.get("PARENT_SPEC_SHA256") != parent_sha:
        return _stop("STOP. V2 spec parent SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if CHANGED_COMPONENT != "price_trigger":
        return _stop("STOP. Changed component drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha, extra={"self_check": chk})
    if int(RESEARCH_PARALLELISM) != 1:
        return _stop("STOP. RESEARCH_PARALLELISM drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if SESSION != "AM":
        return _stop("STOP. SESSION not AM.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if RUNTIME_ADOPTION_ALLOWED or V3_IMPLEMENTED:
        return _stop("STOP. Adoption/V3 flags must stay false.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        leak["WAIT_SEC_RETUNE_N"] = 1
        return _stop("STOP. WAIT_SEC retune forbidden.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today active session.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live Runtime/Capture.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)

    v1_report = _load(V1_OUT / "report.json")
    v1_req = dict(v1_report.get("required") or {})
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA does not match parent.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if int(v1_req.get("BOARD_PASS_N") or -1) != int(V1_LOCKED["SIGNAL_N"]) or int(v1_req.get("FILL_N") or -1) != int(V1_LOCKED["FILL_N"]):
        return _stop("STOP. V1 locked funnel drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha, extra={"v1_req": v1_req})

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    input_paths = [str(c.get("capture_path") or "") for c in caps]
    if REGIME_LABELED.is_file():
        input_paths.append(str(REGIME_LABELED))
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        input_paths,
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Research input references active Capture/Paper files.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture days incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha, extra={"caps": caps})

    cur_pack, _labeled, cur_meta = _load_current()
    c0_pack = {
        "net_pnl_yen_100": C0_OVERLAY_LOCKED["NET"],
        "profit_factor": C0_OVERLAY_LOCKED["PF"],
        "max_drawdown_yen_100": C0_OVERLAY_LOCKED["DD"],
        "trade_count": None,
        "daily": list(cur_pack.get("daily") or []),
    }

    V2_CACHE.mkdir(parents=True, exist_ok=True)
    v1_opps: list[dict[str, Any]] = []
    v1_signals: list[dict[str, Any]] = []
    nested: list[dict[str, Any]] = []
    setups: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    bar_rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        v1_body = load_day_cache(CACHE / f"day_{day}.json", parent_sha)
        if not v1_body:
            return _stop(f"STOP. V1 day cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)
        v1_opps.extend(list(v1_body.get("opps") or []))
        v1_signals.extend(list(v1_body.get("signals") or []))
        cache_p = V2_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v2_sha)
        if not body:
            print(f"{day} v2 harvest start universe={len(cap.get('universe_symbols') or [])}", flush=True)
            body = process_v2_day(
                {
                    "date": day,
                    "capture_path": cap["capture_path"],
                    "universe": list(cap.get("universe_symbols") or []),
                    "spec_sha": v2_sha,
                }
            )
            if body.get("ok"):
                save_v2_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(
                f"STOP. Day harvest failed {day}: {body.get('blocker')}",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v2_sha=v2_sha,
                extra={"day": body},
            )
        nested.extend(list(body.get("nested") or []))
        setups.extend(list(body.get("setups") or []))
        signals.extend(list(body.get("signals") or []))
        bar_rows.extend(list(body.get("bar_rows") or []))
        lk = body.get("leak") or {}
        for k in ("ITAYOSE_SKIP_N", "SPECIAL_SKIP_N", "INVALID_SKIP_N", "FUTURE_FEATURE_USE_N", "FUTURE_LABEL_AS_FEATURE_N"):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)
        day_meta.append(
            {
                "date": day,
                "capture_path": cap["capture_path"],
                "universe_n": len(cap.get("universe_symbols") or []),
                "universe_source": cap.get("universe_source"),
                "events_n": body.get("events_n"),
                "setup_n": len(body.get("setups") or []),
                "signal_n": len(body.get("signals") or []),
                "elapsed_sec": body.get("elapsed_sec"),
                "integ_fail_n": body.get("integ_fail_n"),
            }
        )

    if len(v1_signals) != int(V1_LOCKED["SIGNAL_N"]):
        return _stop(
            f"STOP. V1 signal cache n={len(v1_signals)} != locked {V1_LOCKED['SIGNAL_N']}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v2_sha=v2_sha,
        )
    pm_n = sum(1 for r in signals if str(r.get("session") or "") != "AM")
    leak["PM_ROWS_USED_N"] = int(pm_n)
    if pm_n:
        return _stop("STOP. PM opportunities present.", pre=pre, leak=leak, parent_sha=parent_sha, v2_sha=v2_sha)

    mismatch = nested_mismatch(v1_opps, nested)
    leak["TREND_PASS_MISMATCH_N"] = int(mismatch.get("TREND_PASS_MISMATCH_N") or 0)
    leak["PULLBACK_PASS_MISMATCH_N"] = int(mismatch.get("PULLBACK_PASS_MISMATCH_N") or 0)
    leak["RCI_PASS_MISMATCH_N"] = int(mismatch.get("RCI_PASS_MISMATCH_N") or 0)
    leak["VOLUME_PASS_MISMATCH_N"] = int(mismatch.get("VOLUME_PASS_MISMATCH_N") or 0)
    leak["BOARD_NESTED_MISMATCH_N"] = int(mismatch.get("BOARD_NESTED_MISMATCH_N") or 0)
    leak["ROW_MISMATCH_N"] = int(mismatch.get("ROW_MISMATCH_N") or 0)
    if any(int(mismatch.get(k) or 0) for k in ("TREND_PASS_MISMATCH_N", "PULLBACK_PASS_MISMATCH_N", "RCI_PASS_MISMATCH_N", "VOLUME_PASS_MISMATCH_N", "BOARD_NESTED_MISMATCH_N", "ROW_MISMATCH_N")):
        return _stop(
            "STOP. Stage parity through Volume/Board nested flags failed.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v2_sha=v2_sha,
            extra={"mismatch": mismatch},
        )

    port = portfolio_replay(signals, wait_sec=float(DEV_WAIT_SEC), position_cap=int(POSITION_CAP))
    trades = list(port.get("trades") or [])
    for t in trades:
        t["failure"] = classify_trade(t)
    v1c = v1_cliff_locked()
    v2c = fill_cliff(signals, port)
    cf = v1_v2_counterfactual(v1_signals, setups, signals, list(port.get("candidates") or []))
    rec = recovery_pack(v1_signals, cf, list(v2c.get("filled") or []))
    pack = economic_pack(trades, list(ELIGIBLE_DAYS))
    rob = robustness(pack)
    paired = paired_delta(list(pack.get("daily") or []), list(cur_pack.get("daily") or [])) if cur_pack.get("daily") else {}

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = (
        int(leak["LIVE_PROCESS_CONTROL_CALL_N"]) == 0
        and int(leak["RUNTIME_WRITE_N"]) == 0
        and int(leak["CAPTURE_WRITE_N"]) == 0
        and int(leak["ADDITIONAL_WEBSOCKET_N"]) == 0
        and int(leak["ACTIVE_CAPTURE_INPUT_N"]) == 0
        and int(leak["SUBMIT_N"]) == 0
        and int(leak["CANCEL_N"]) == 0
        and int(leak["LIVE_ORDER_N"]) == 0
        and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    )
    if live_before:
        ni_ok = bool(
            ni_ok
            and adv.get("RUNTIME_PID_UNCHANGED")
            and adv.get("CAPTURE_PID_UNCHANGED")
            and adv.get("RUNTIME_STILL_ALIVE")
            and adv.get("CAPTURE_STILL_ALIVE")
        )
    integ_ok = ni_ok and all(int(leak.get(k) or 0) == 0 for k in (
        "MACD_ADDED_N",
        "ADX_ADDED_N",
        "RSI_ADDED_N",
        "ML_USE_N",
        "SCORE_USE_N",
        "TOPK_RANKING_N",
        "WAIT_SEC_RETUNE_N",
        "LIMIT_IMPROVEMENT_N",
        "ASK_ENTRY_N",
        "MARKETABLE_ENTRY_N",
        "PM_ROWS_USED_N",
    ))
    mech = mechanism_supported(
        v1_cliff=v1c,
        v2_cliff=v2c,
        rec=rec,
        cf=cf,
        mismatch=mismatch,
        ni_ok=bool(ni_ok),
        integrity_ok=bool(integ_ok),
    )
    defn = next_deficiency(
        supported=bool(mech.get("TRIGGER_MECHANISM_SUPPORTED")),
        v2_cliff=v2c,
        rec=rec,
        cf=cf,
        setups=setups,
        trades=trades,
    )
    verdict = _verdict(bool(mech.get("TRIGGER_MECHANISM_SUPPORTED")), str(defn.get("PRIMARY_DEFICIENCY_AFTER_V2")), bool(ni_ok))

    trigger_n = sum(1 for u in setups if u.get("triggered"))
    req = {
        "STRATEGY_ID": STRATEGY_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V2_SPEC_SHA256": v2_sha,
        "V1_SIGNAL_N": v1c["SIGNAL_N"],
        "V1_FILL_N": v1c["FILL_N"],
        "V1_FILL_RATE": v1c["FILL_RATE"],
        "V1_FILLED_FWD3": v1c["FILLED_FWD3"],
        "V1_NONFILLED_FWD3": v1c["NONFILLED_FWD3"],
        "V2_SETUP_N": len(setups),
        "V2_TRIGGER_N": trigger_n,
        "V2_SIGNAL_N": v2c["SIGNAL_N"],
        "V2_FILL_N": v2c["FILL_N"],
        "V2_FILL_RATE": v2c["FILL_RATE"],
        "V2_FILLED_FWD3": v2c["FILLED_FWD3"],
        "V2_NONFILLED_FWD3": v2c["NONFILLED_FWD3"],
        "V2_FILL_QUALITY_GAP": v2c["FILL_QUALITY_GAP"],
        "GOOD_UPMOVE_RECOVERED_N": rec["GOOD_UPMOVE_RECOVERED_N"],
        "GOOD_UPMOVE_STILL_NONFILL_N": rec["GOOD_UPMOVE_STILL_NONFILL_N"],
        "NEW_BAD_FILL_N": rec["NEW_BAD_FILL_N"],
        "V2_NET": pack.get("net_pnl_yen_100"),
        "V2_PF": pack.get("profit_factor"),
        "V2_DD": pack.get("max_drawdown_yen_100"),
        "TRADE_N": pack.get("trade_count"),
        "WIN_N": pack.get("win_n"),
        "LOSS_N": pack.get("loss_n"),
        "FLAT_N": pack.get("flat_n"),
        "POSITIVE_DAY_N": rob.get("positive_day_n"),
        "NEGATIVE_DAY_N": rob.get("negative_day_n"),
        "ZERO_DAY_N": rob.get("zero_day_n"),
        "DAILY_MEDIAN": rob.get("daily_median"),
        "EX_BEST": rob.get("EX_BEST"),
        "EX_TOP3": rob.get("EX_TOP3"),
        "TRIGGER_MECHANISM_SUPPORTED": mech.get("TRIGGER_MECHANISM_SUPPORTED"),
        "PRIMARY_DEFICIENCY_AFTER_V2": defn.get("PRIMARY_DEFICIENCY_AFTER_V2"),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "TRUE_OOS": bool(TRUE_OOS),
        "VERDICT": verdict.get("VERDICT"),
        "NEXT": verdict.get("NEXT"),
        "TREND_PASS_MISMATCH_N": mismatch.get("TREND_PASS_MISMATCH_N"),
        "PULLBACK_PASS_MISMATCH_N": mismatch.get("PULLBACK_PASS_MISMATCH_N"),
        "RCI_PASS_MISMATCH_N": mismatch.get("RCI_PASS_MISMATCH_N"),
        "VOLUME_PASS_MISMATCH_N": mismatch.get("VOLUME_PASS_MISMATCH_N"),
        "BOARD_NESTED_MISMATCH_N": mismatch.get("BOARD_NESTED_MISMATCH_N"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "strategy_id": STRATEGY_ID,
        "parent_strategy_id": PARENT_STRATEGY_ID,
        "changed_component": CHANGED_COMPONENT,
        "c14_id": C14_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "v1_locked": V1_LOCKED,
        "v1_cliff": {k: v1c[k] for k in v1c},
        "v2_cliff": {k: v2c[k] for k in v2c if k not in ("admitted", "filled", "nonfill")},
        "recovery": {k: rec[k] for k in rec if k not in ("recovered", "still", "new_bad")},
        "mechanism": {k: mech[k] for k in mech if k != "effect_days"},
        "deficiency": defn,
        "verdict": verdict,
        "economics": _slim_pack(pack),
        "robustness": rob,
        "current": _slim_pack(cur_pack) if isinstance(cur_pack, dict) else CURRENT_LOCKED,
        "current_meta": cur_meta,
        "c0_c14": _slim_pack(c0_pack),
        "c0_overlay_locked": C0_OVERLAY_LOCKED,
        "v1_economics": {"NET": V1_LOCKED["NET"], "PF": V1_LOCKED["PF"], "MAX_DD": V1_LOCKED["MAX_DD"], "TRADE_N": 3},
        "paired_vs_current": {
            k: paired.get(k)
            for k in (
                "PAIRED_POS_DAYS",
                "PAIRED_NEG_DAYS",
                "PAIRED_ZERO_DAYS",
                "PAIRED_MEDIAN_DAILY_DELTA",
                "EX_BEST_DAY_PNL_DELTA",
                "EX_TOP3_DAYS_PNL_DELTA",
            )
            if paired
        },
        "portfolio": {
            k: port.get(k)
            for k in ("admitted_n", "expired_n", "fill_n", "cap_blocked", "same_symbol_blocked", "open_leftover_n", "pending_leftover_n")
        },
        "stage_parity": mismatch,
        "setup_status": defn.get("status_counts"),
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "dev_wait_sec": float(DEV_WAIT_SEC),
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "runtime_adoption_allowed": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)

    setup_rows = []
    keep_s = (
        "date",
        "symbol",
        "bar_minute",
        "setup_t",
        "trigger_t",
        "status",
        "triggered",
        "expired",
        "ema_ok",
        "bb_ok",
        "board_ok",
        "high_k",
        "trigger_px",
        "fwd_3m",
        "limit",
        "WOULD_FILL",
    )
    for r in setups:
        setup_rows.append({k: r.get(k) for k in keep_s})
    sig_rows = []
    keep_sig = (
        "date",
        "symbol",
        "bar_minute",
        "t0",
        "setup_t",
        "limit",
        "WOULD_FILL",
        "fill_t",
        "fwd_1m",
        "fwd_3m",
        "fwd_5m",
        "up_first",
        "s8",
        "s9",
        "pnl_yen_100",
        "nonfill_class",
    )
    by_sig = {(str(r.get("date")), str(r.get("symbol")), float(r.get("t0") or 0.0)): r for r in port.get("candidates") or []}
    for r in signals:
        key = (str(r.get("date")), str(r.get("symbol")), float(r.get("t0") or 0.0))
        c = by_sig.get(key) or {}
        row = {k: r.get(k) for k in keep_sig}
        row["s8"] = bool(c.get("s8_pending"))
        row["s9"] = bool(c.get("s9_fill"))
        sig_rows.append(row)
    trade_rows = []
    for t in trades:
        src = t.get("src") or {}
        trade_rows.append(
            {
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "t0": t.get("t0"),
                "fill_time": t.get("fill_time"),
                "fill_price": t.get("fill_price"),
                "exit_time": t.get("exit_time"),
                "exit_price": t.get("exit_price"),
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "failure": t.get("failure"),
                "fwd_3m": src.get("fwd_3m"),
            }
        )
    sheets = {
        "Precommit": kv_rows(
            {
                "STRATEGY_ID": STRATEGY_ID,
                "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
                "PARENT_SPEC_SHA256": parent_sha,
                "V2_SPEC_SHA256": v2_sha,
                "CHANGED_COMPONENT": CHANGED_COMPONENT,
                "C14_ID": C14_ID,
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "POSITION_CAP": POSITION_CAP,
                "RESEARCH_PARALLELISM": RESEARCH_PARALLELISM,
                "TRUE_OOS": TRUE_OOS,
                "indicator_self_check": chk,
            }
        ),
        "Data_Manifest": day_meta,
        "Stage_Parity": kv_rows(mismatch),
        "V1_V2_Counterfactual": cf or [{"empty": True}],
        "Setups": setup_rows or [{"empty": True}],
        "Signals": sig_rows or [{"empty": True}],
        "Recovery": kv_rows({k: rec[k] for k in rec if k not in ("recovered", "still", "new_bad")})
        + (rec.get("still") or [{"recovered_or_still": "none"}]),
        "Trades": trade_rows or [{"empty": True}],
        "Daily": list(pack.get("daily") or []),
        "Economics": kv_rows(
            {
                **{f"V2_{k}": v for k, v in _slim_pack(pack).items()},
                **{f"V1_{k}": V1_LOCKED.get(k) for k in ("NET", "PF", "MAX_DD")},
                **{f"CURRENT_{k}": v for k, v in CURRENT_LOCKED.items()},
                **{f"C0_C14_{k}": v for k, v in C0_OVERLAY_LOCKED.items()},
            }
        ),
        "Mechanism": kv_rows(mech),
        "Deficiency_RCA": kv_rows({k: defn.get(k) for k in defn if k != "status_counts"}) + [defn.get("status_counts") or {"empty": True}],
        "Non_Interference": kv_rows(
            {
                **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
                **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
                **adv,
                "NON_INTERFERENCE_PASS": ni_ok,
            }
        ),
        "Integrity": kv_rows(
            {
                **leak,
                "bar_integ_fail_n": sum(int(r.get("integ_fail_n") or 0) for r in day_meta),
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C14_CHANGED": False,
                "FAMILY_CLOSED": False,
                "RUNTIME_ADOPTION_ALLOWED": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(
        f"DONE verdict={req.get('VERDICT')} supported={req.get('TRIGGER_MECHANISM_SUPPORTED')} "
        f"setup={req.get('V2_SETUP_N')} trigger={req.get('V2_TRIGGER_N')} fill={req.get('V2_FILL_N')} "
        f"net={req.get('V2_NET')} def={req.get('PRIMARY_DEFICIENCY_AFTER_V2')} ni={ni_ok} out={V2_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
