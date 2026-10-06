"""Offline SIMPLE_TECH_PULLBACK_V1 baseline + deficiency RCA. No Runtime/Capture writes. No V2."""
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
from research.simple_tech_entry_family import (
    ANALYSIS_ID,
    ARCHITECTURE_ID,
    FORMAL_CANDIDATE,
    ML_USED,
    PAPER_STRATEGY_ADOPTION_ALLOWED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    SCORE_USED,
    STRATEGY_ID,
    TOPK_RANKING_USED,
    TRUE_OOS,
    V2_IMPLEMENTED,
)
from research.simple_tech_entry_family.analyze import (
    board_rca,
    decide_verdict,
    execution_rca,
    exit_attribution,
    failure_taxonomy,
    funnel_counts,
    rci_rca,
    robustness,
    select_primary,
    stage_contribution,
    trigger_rca,
    trend_rca,
    volume_rca,
    winner_loser_paths,
)
from research.simple_tech_entry_family.harvest import (
    CACHE,
    load_day_cache,
    process_day,
    save_day_cache,
    sealed_day_caps,
)
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import (
    TODAY,
    V1_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.portfolio import portfolio_replay
from research.simple_tech_entry_family.publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
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
    "V2_IMPLEMENT_N",
    "FUTURE_FEATURE_USE_N",
    "FUTURE_LABEL_AS_FEATURE_N",
    "PM_ROWS_USED_N",
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


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, extra: dict[str, Any] | None = None) -> int:
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
        "PRIMARY_DEFICIENCY": None,
        "SPEC_SHA256": sha,
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
        "Precommit": kv_rows({"SPEC_SHA256": sha, "STRATEGY_ID": STRATEGY_ID}),
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
    c0_locked = {
        "net_pnl_yen_100": C0_OVERLAY_LOCKED["NET"],
        "profit_factor": C0_OVERLAY_LOCKED["PF"],
        "max_drawdown_yen_100": C0_OVERLAY_LOCKED["DD"],
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


def main() -> int:
    set_research_priority_below_normal()
    spec = canonical_v1_spec()
    sha = spec_sha256(spec)
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
        f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} spec={sha[:12]}",
        flush=True,
    )
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, sha=sha, extra={"self_check": chk})
    if int(RESEARCH_PARALLELISM) != 1:
        return _stop("STOP. RESEARCH_PARALLELISM drifted.", pre=pre, leak=leak, sha=sha)
    if SESSION != "AM":
        return _stop("STOP. SESSION not AM.", pre=pre, leak=leak, sha=sha)
    if V2_IMPLEMENTED or ML_USED or SCORE_USED or TOPK_RANKING_USED:
        return _stop("STOP. Family drift flags set.", pre=pre, leak=leak, sha=sha)
    if RUNTIME_ADOPTION_ALLOWED or PAPER_STRATEGY_ADOPTION_ALLOWED or FORMAL_CANDIDATE:
        return _stop("STOP. Adoption flags must stay false.", pre=pre, leak=leak, sha=sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today active session.", pre=pre, leak=leak, sha=sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live Runtime/Capture.", pre=pre, leak=leak, sha=sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, sha=sha)
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
        return _stop("STOP. Research input references active Capture/Paper files.", pre=pre, leak=leak, sha=sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture days incomplete.", pre=pre, leak=leak, sha=sha, extra={"caps": caps})

    cur_pack, _labeled, cur_meta = _load_current()
    c0_pack = {
        "net_pnl_yen_100": C0_OVERLAY_LOCKED["NET"],
        "profit_factor": C0_OVERLAY_LOCKED["PF"],
        "max_drawdown_yen_100": C0_OVERLAY_LOCKED["DD"],
        "trade_count": None,
        "daily": list(cur_pack.get("daily") or []),
    }

    CACHE.mkdir(parents=True, exist_ok=True)
    opps: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    bar_rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        cache_p = CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, sha)
        if not body:
            print(f"{day} harvest start universe={len(cap.get('universe_symbols') or [])}", flush=True)
            body = process_day(
                {
                    "date": day,
                    "capture_path": cap["capture_path"],
                    "universe": list(cap.get("universe_symbols") or []),
                    "spec_sha": sha,
                }
            )
            if body.get("ok"):
                save_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(
                f"STOP. Day harvest failed {day}: {body.get('blocker')}",
                pre=pre,
                leak=leak,
                sha=sha,
                extra={"day": body},
            )
        opps.extend(list(body.get("opps") or []))
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
                "opp_n": len(body.get("opps") or []),
                "signal_n": len(body.get("signals") or []),
                "elapsed_sec": body.get("elapsed_sec"),
                "integ_fail_n": body.get("integ_fail_n"),
            }
        )

    pm_n = sum(1 for r in opps if str(r.get("session") or "") != "AM")
    leak["PM_ROWS_USED_N"] = int(pm_n)
    if pm_n:
        return _stop("STOP. PM opportunities present.", pre=pre, leak=leak, sha=sha)

    port = portfolio_replay(signals, wait_sec=float(DEV_WAIT_SEC), position_cap=int(POSITION_CAP))
    trades = list(port.get("trades") or [])
    by_sig = {(str(r.get("date")), str(r.get("symbol")), float(r.get("t0") or r.get("signal_time") or 0.0)): r for r in port.get("candidates") or []}
    for r in opps:
        key = (str(r.get("date")), str(r.get("symbol")), float(r.get("t0") or 0.0))
        c = by_sig.get(key)
        if c is None:
            continue
        r["s8"] = bool(c.get("s8_pending"))
        r["s9"] = bool(c.get("s9_fill"))
        r["s10"] = bool(c.get("s10_trade"))
        r["block_reason"] = c.get("block_reason")

    pack = economic_pack(trades, list(ELIGIBLE_DAYS))
    rob = robustness(pack)
    funnel = funnel_counts(opps, port, trades)
    contrib = stage_contribution(opps)
    tax_rows, tax_agg = failure_taxonomy(trades)
    trend = trend_rca(trades, opps)
    rci = rci_rca(trades, opps)
    vol = volume_rca(trades, opps)
    trig = trigger_rca(trades)
    brd = board_rca(opps)
    exe = execution_rca(list(port.get("candidates") or signals), port)
    exa = exit_attribution(trades)
    win_p, loss_p = winner_loser_paths(trades)
    s7_days = len({str(r.get("date") or "") for r in opps if r.get("s7")})
    primary = select_primary(
        tax_agg,
        contrib,
        trades,
        brd,
        len(ELIGIBLE_DAYS),
        execution=exe,
        signal_days=s7_days,
    )
    paired = paired_delta(list(pack.get("daily") or []), list(cur_pack.get("daily") or [])) if cur_pack.get("daily") else {}
    verdict = decide_verdict(pack, rob, cur_pack if cur_meta.get("ok") else CURRENT_LOCKED, primary)

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
    if not ni_ok:
        verdict["VERDICT"] = "STOP"
        verdict["NEXT"] = "NON_INTERFERENCE_FAIL"

    req = {
        "STRATEGY_ID": STRATEGY_ID,
        **funnel,
        "NET": pack.get("net_pnl_yen_100"),
        "PF": pack.get("profit_factor"),
        "MAX_DD": pack.get("max_drawdown_yen_100"),
        "POSITIVE_DAY_N": rob.get("positive_day_n"),
        "NEGATIVE_DAY_N": rob.get("negative_day_n"),
        "DAILY_MEDIAN": rob.get("daily_median"),
        "EX_BEST": rob.get("EX_BEST"),
        "EX_TOP3": rob.get("EX_TOP3"),
        "F1_TREND_FALSE_N": int((tax_agg.get("F1_TREND_FALSE") or {}).get("N") or 0),
        "F2_PULLBACK_FALSE_N": int((tax_agg.get("F2_PULLBACK_FALSE") or {}).get("N") or 0),
        "F3_REVERSAL_FALSE_N": int((tax_agg.get("F3_REVERSAL_FALSE") or {}).get("N") or 0),
        "F4_VOLUME_FALSE_CONFIRM_N": int((tax_agg.get("F4_VOLUME_FALSE_CONFIRM") or {}).get("N") or 0),
        "F5_PRICE_TRIGGER_LATE_N": int((tax_agg.get("F5_PRICE_TRIGGER_LATE") or {}).get("N") or 0),
        "F6_BOARD_FALSE_SUPPORT_N": int((tax_agg.get("F6_BOARD_FALSE_SUPPORT") or {}).get("N") or 0),
        "F7_EXECUTION_COST_N": int((tax_agg.get("F7_EXECUTION_COST") or {}).get("N") or 0),
        "F8_EXIT_GIVEBACK_N": int((tax_agg.get("F8_EXIT_GIVEBACK") or {}).get("N") or 0),
        "PRIMARY_DEFICIENCY": primary.get("PRIMARY_DEFICIENCY"),
        "PRIMARY_DEFICIENCY_GROSS_LOSS": primary.get("PRIMARY_DEFICIENCY_GROSS_LOSS"),
        "PRIMARY_DEFICIENCY_DAY_COVERAGE": primary.get("PRIMARY_DEFICIENCY_DAY_COVERAGE"),
        "V2_RECOMMENDED_COMPONENT": primary.get("V2_RECOMMENDED_COMPONENT"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": verdict.get("VERDICT"),
        "NEXT": verdict.get("NEXT"),
        "SPEC_SHA256": sha,
        "WIN_N": pack.get("win_n"),
        "LOSS_N": pack.get("loss_n"),
        "FLAT_N": pack.get("flat_n"),
        "AVG": pack.get("mean_daily_pnl"),
        "MEDIAN": pack.get("median_daily_pnl"),
        "BEST_DAY": rob.get("best_day"),
        "BEST_DAY_SHARE": rob.get("best_day_share"),
        "TOP3_DAY_SHARE": rob.get("top3_day_share"),
        "EX_BEST_NET": rob.get("EX_BEST_NET"),
        "EX_TOP3_NET": rob.get("EX_TOP3_NET"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "architecture_id": ARCHITECTURE_ID,
        "strategy_id": STRATEGY_ID,
        "c14_id": C14_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "economics": _slim_pack(pack),
        "robustness": rob,
        "primary": primary,
        "verdict": verdict,
        "funnel": funnel,
        "current": _slim_pack(cur_pack) if isinstance(cur_pack, dict) else CURRENT_LOCKED,
        "current_meta": cur_meta,
        "c0_c14": _slim_pack(c0_pack),
        "c0_overlay_locked": C0_OVERLAY_LOCKED,
        "paired_vs_current": {k: paired.get(k) for k in ("PAIRED_POS_DAYS", "PAIRED_NEG_DAYS", "PAIRED_ZERO_DAYS", "PAIRED_MEDIAN_DAILY_DELTA", "EX_BEST_DAY_PNL_DELTA", "EX_TOP3_DAYS_PNL_DELTA") if paired},
        "portfolio": {k: port.get(k) for k in ("admitted_n", "expired_n", "fill_n", "cap_blocked", "same_symbol_blocked", "open_leftover_n", "pending_leftover_n")},
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "dev_wait_sec": float(DEV_WAIT_SEC),
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    sig_state = [r for r in opps if r.get("s3")]
    slim_state = []
    keep = (
        "date",
        "symbol",
        "bar_minute",
        "t0",
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "s7",
        "s8",
        "s9",
        "s10",
        "close",
        "volume",
        "ema9",
        "ema21",
        "rci9",
        "rci9_prev",
        "fwd_1m",
        "fwd_3m",
        "fwd_5m",
        "mfe_5m",
        "mae_5m",
        "up_first",
        "down_first",
        "up_vol",
        "down_vol",
        "ask_vol",
        "bid_vol",
        "spread_bps",
        "board_reason",
        "WOULD_FILL",
        "pnl_yen_100",
    )
    for r in sig_state:
        slim_state.append({k: r.get(k) for k in keep})
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
                "rci9": src.get("rci9"),
                "volume": src.get("volume"),
                "up_vol": src.get("up_vol"),
                "down_vol": src.get("down_vol"),
            }
        )
    sheets = {
        "Precommit": kv_rows(
            {
                "STRATEGY_ID": STRATEGY_ID,
                "SPEC_SHA256": sha,
                "C14_ID": C14_ID,
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "POSITION_CAP": POSITION_CAP,
                "RESEARCH_PARALLELISM": RESEARCH_PARALLELISM,
                "TRUE_OOS": TRUE_OOS,
                "V2_IMPLEMENTED": V2_IMPLEMENTED,
                "indicator_self_check": chk,
            }
        ),
        "Data_Manifest": day_meta,
        "Bar_Integrity": bar_rows,
        "Opportunity_Funnel": kv_rows(funnel),
        "Stage_Contribution": contrib,
        "Signal_State": slim_state or [{"empty": True}],
        "Winner_Path": win_p or [{"empty": True}],
        "Loser_Path": loss_p or [{"empty": True}],
        "Failure_Taxonomy": list(tax_agg.values()),
        "Trend_RCA": trend,
        "RCI_RCA": rci,
        "Volume_RCA": vol,
        "Trigger_RCA": trig[:80],
        "Board_RCA": brd,
        "Execution_RCA": exe,
        "Exit_Attribution": exa,
        "Trades": trade_rows or [{"empty": True}],
        "Daily": list(pack.get("daily") or []),
        "Robustness": kv_rows(rob),
        "Baseline": kv_rows(
            {
                **{f"CURRENT_{k}": v for k, v in CURRENT_LOCKED.items()},
                **{f"C0_C14_{k}": v for k, v in C0_OVERLAY_LOCKED.items()},
                "current_replay_ok": cur_meta.get("ok"),
                "current_replay_net": cur_pack.get("net_pnl_yen_100") if isinstance(cur_pack, dict) else None,
            }
        ),
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
            }
        ),
    }
    write_artifacts(report, sheets)
    print(
        f"DONE verdict={req.get('VERDICT')} net={req.get('NET')} pf={req.get('PF')} "
        f"trades={req.get('TRADE_N')} def={req.get('PRIMARY_DEFICIENCY')} ni={ni_ok} out={V1_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
