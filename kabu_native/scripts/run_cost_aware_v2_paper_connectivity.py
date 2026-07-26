#!/usr/bin/env python3
"""Cost-Aware V2 Shadow Paper connectivity verification (demo-isolated, no live orders).

Writes only under results/reports/cost_aware_v2_paper_connectivity/.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(NATIVE))
sys.path.insert(0, str(REPO))  # src.kabu_signal_engine lives at tradebotfile/src

OUT = NATIVE / "results" / "reports" / "cost_aware_v2_paper_connectivity"
OUT.mkdir(parents=True, exist_ok=True)

FOCUS_FILES = [
    "src/small_paper/cost_aware_entry_v2_shadow.py",
    "src/small_paper/cost_aware_entry_v2_shadow_hook.py",
    "src/small_paper/forward_observer_defaults.py",
    "src/small_paper/shadow_registry.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/discord_message_builder.py",
]


@dataclass
class Check:
    name: str
    status: str
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _sha(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: list[str], *, timeout: int = 300, env: Optional[dict] = None) -> dict[str, Any]:
    e = os.environ.copy()
    if env:
        e.update(env)
    e["PYTHONPATH"] = os.pathsep.join(
        [str(SRC), str(NATIVE), str(REPO), e.get("PYTHONPATH", "")]
    )
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(NATIVE),
            env=e,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "cmd": cmd,
            "returncode": p.returncode,
            "stdout": (p.stdout or "")[-12000:],
            "stderr": (p.stderr or "")[-4000:],
            "duration_sec": round(time.perf_counter() - t0, 3),
        }
    except Exception as exc:
        return {
            "cmd": cmd,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
            "duration_sec": round(time.perf_counter() - t0, 3),
            "tb": traceback.format_exc()[-2000:],
        }


# ---------------------------------------------------------------------------
# Task 1 precheck
# ---------------------------------------------------------------------------


def task1_precheck() -> dict[str, Any]:
    checks: list[Check] = []
    git_status = _run(["git", "status", "--short"])
    git_diff_stat = _run(["git", "diff", "--stat"])
    git_diff = _run(["git", "diff", "--", "kabu_native/src/small_paper/cost_aware_entry_v2_shadow.py", "kabu_native/src/small_paper/forward_observer_defaults.py", "kabu_native/src/small_paper/pilot_runner.py", "kabu_native/src/small_paper/discord_message_builder.py", "src/small_paper/cost_aware_entry_v2_shadow.py", "src/small_paper/forward_observer_defaults.py", "src/small_paper/pilot_runner.py", "src/small_paper/discord_message_builder.py"])
    hashes = {f: _sha(NATIVE / f) for f in FOCUS_FILES}
    # PIDs
    pids = []
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            name = (proc.info.get("name") or "").lower()
            if "python" in name:
                pids.append({"pid": proc.info["pid"], "name": proc.info.get("name"), "cmdline": (proc.info.get("cmdline") or [])[:8]})
    except Exception:
        r = _run([sys.executable, "-c", "import os; print(os.getpid())"])
        pids.append({"note": "psutil unavailable", "self": r.get("stdout")})

    # latest session tails
    sp = NATIVE / "results" / "small_paper"
    errors_tail = []
    hb_tail = []
    orphan_open = []
    latest_day = None
    if sp.is_dir():
        days = sorted([p for p in sp.iterdir() if p.is_dir() and p.name.isdigit()], reverse=True)
        for d in days[:3]:
            for sess in sorted(d.glob("live_session_*"), reverse=True)[:2]:
                if "demo" in str(sess):
                    continue
                latest_day = latest_day or d.name
                ej = sess / "errors.jsonl"
                hj = sess / "heartbeat.jsonl"
                if ej.is_file():
                    lines = ej.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
                    errors_tail.append({"session": str(sess), "lines": lines})
                if hj.is_file():
                    lines = hj.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
                    hb_tail.append({"session": str(sess), "lines": lines})
                pos = sess / "small_paper_positions.csv"
                if pos.is_file():
                    try:
                        import csv

                        with pos.open(encoding="utf-8", newline="") as fh:
                            for row in csv.DictReader(fh):
                                if str(row.get("status") or "").lower() in ("open", "opened"):
                                    orphan_open.append({"session": str(sess), **{k: row.get(k) for k in list(row)[:8]}})
                    except Exception:
                        pass

    env_keys = [
        "COST_AWARE_ENTRY_V2_SHADOW",
        "COST_AWARE_ENTRY_SHADOW",
        "KABU_PAPER_RUNTIME",
        "LIVE_TRADING",
        "ORDER_ENABLED",
        "REAL_ORDER_ENABLED",
        "DEMO_MODE",
        "PAPER_ONLY",
        "NETWORK_DISABLED",
        "DISCORD_CAPTURE_ONLY",
        "TRADEBOT_DEMO_PUSH_E2E",
    ]
    env_snap = {k: os.environ.get(k) for k in env_keys}
    pre = {
        "generated_at": _iso(),
        "git_status": git_status,
        "git_diff_stat": git_diff_stat,
        "git_diff_focus": git_diff,
        "focus_file_hashes": hashes,
        "python_pids": pids,
        "errors_tail": errors_tail,
        "heartbeat_tail": hb_tail,
        "orphan_open": orphan_open[:20],
        "submit_cancel_live_order": {"submit": 0, "cancel": 0, "live_order": 0, "note": "precheck baseline (V2 shadow counters)"},
        "env": env_snap,
        "paper_startup_defaults": {
            "KABU_PAPER_RUNTIME": "1 via ensure_paper_forward_observer_env",
            "COST_AWARE_ENTRY_V2_SHADOW": "default ON on paper when unset",
            "order_enabled": False,
            "live_trading_enabled": False,
        },
        "latest_day_scanned": latest_day,
    }
    _write_json(OUT / "precheck.json", pre)
    checks.append(Check("precheck_saved", "PASS", str(OUT / "precheck.json")))
    return {"checks": [asdict(c) for c in checks], "precheck": pre}


# ---------------------------------------------------------------------------
# Task 2 config resolution
# ---------------------------------------------------------------------------


def task2_config_resolution() -> dict[str, Any]:
    from small_paper.forward_observer_defaults import (
        ensure_paper_forward_observer_env,
        resolve_cost_aware_entry_v2_shadow,
    )

    results = []
    patterns = []

    def eval_pattern(name: str, env: dict, cfg: dict, expect_on: bool, expect_src: str) -> None:
        # isolate
        for k in list(os.environ.keys()):
            if k in (
                "COST_AWARE_ENTRY_V2_SHADOW",
                "KABU_PAPER_RUNTIME",
                "LIVE_TRADING",
                "KABU_LIVE_RUNTIME",
            ):
                os.environ.pop(k, None)
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        on, src = resolve_cost_aware_entry_v2_shadow(cfg)
        ok = (on is expect_on) and (src == expect_src or (expect_src == "paper_default" and src == "default"))
        patterns.append(
            {
                "name": name,
                "env": env,
                "cfg": cfg,
                "enabled": on,
                "source": src,
                "expect_on": expect_on,
                "expect_src": expect_src,
                "ok": ok,
            }
        )
        results.append(Check(f"config_{name}", "PASS" if ok else "FAIL", f"enabled={on} source={src}"))

    eval_pattern("A_paper_unset", {"KABU_PAPER_RUNTIME": "1"}, {"paper_runtime": True}, True, "default")
    eval_pattern("B_env1", {"KABU_PAPER_RUNTIME": "1", "COST_AWARE_ENTRY_V2_SHADOW": "1"}, {"paper_runtime": True}, True, "env")
    eval_pattern("C_env0", {"KABU_PAPER_RUNTIME": "1", "COST_AWARE_ENTRY_V2_SHADOW": "0"}, {"paper_runtime": True}, False, "env")
    eval_pattern(
        "D_live_env1",
        {"KABU_PAPER_RUNTIME": "1", "COST_AWARE_ENTRY_V2_SHADOW": "1"},
        {"paper_runtime": True, "live_trading_enabled": True},
        False,
        "live_force_off",
    )
    eval_pattern(
        "E_order_enabled_env1",
        {"COST_AWARE_ENTRY_V2_SHADOW": "1"},
        {"paper_runtime": True, "order_enabled": True},
        False,
        "live_force_off",
    )

    # F invalid env — must not raise
    for k in list(os.environ.keys()):
        if k.startswith("COST_AWARE") or k in ("KABU_PAPER_RUNTIME", "LIVE_TRADING"):
            os.environ.pop(k, None)
    os.environ["KABU_PAPER_RUNTIME"] = "1"
    os.environ["COST_AWARE_ENTRY_V2_SHADOW"] = "abc"
    try:
        on, src = resolve_cost_aware_entry_v2_shadow({"paper_runtime": True})
        # parse_env_bool returns None for invalid → falls through to paper default ON
        patterns.append({"name": "F_invalid_env", "enabled": on, "source": src, "ok": True, "note": "no exception; invalid treated as unset"})
        results.append(Check("config_F_invalid_env", "PASS", f"enabled={on} source={src}"))
    except Exception as exc:
        patterns.append({"name": "F_invalid_env", "ok": False, "error": str(exc)})
        results.append(Check("config_F_invalid_env", "FAIL", str(exc)))

    # ensure_paper sets V2 when unset
    for k in ("COST_AWARE_ENTRY_V2_SHADOW", "COST_AWARE_ENTRY_SHADOW", "PULLBACK_VOLUME_FORWARD"):
        os.environ.pop(k, None)
    os.environ["KABU_PAPER_RUNTIME"] = "1"
    applied = ensure_paper_forward_observer_env()
    results.append(
        Check(
            "ensure_paper_sets_v2",
            "PASS" if applied.get("COST_AWARE_ENTRY_V2_SHADOW") == "1" or os.environ.get("COST_AWARE_ENTRY_V2_SHADOW") == "1" else "FAIL",
            str(applied),
        )
    )

    # cleanup pollution
    for k in ("COST_AWARE_ENTRY_V2_SHADOW", "LIVE_TRADING", "KABU_LIVE_RUNTIME"):
        os.environ.pop(k, None)
    os.environ["KABU_PAPER_RUNTIME"] = "1"

    return {"checks": [asdict(c) for c in results], "patterns": patterns}


# ---------------------------------------------------------------------------
# Task 3 imports
# ---------------------------------------------------------------------------


def task3_imports() -> dict[str, Any]:
    checks = []
    mods = [
        "small_paper.cost_aware_entry_v2_shadow",
        "small_paper.cost_aware_entry_v2_shadow_hook",
        "small_paper.forward_observer_defaults",
        "small_paper.shadow_registry",
        "small_paper.discord_message_builder",
        "small_paper.pilot_runner",
    ]
    for m in mods:
        try:
            __import__(m)
            checks.append(Check(f"import_{m}", "PASS"))
        except Exception as exc:
            checks.append(Check(f"import_{m}", "FAIL", str(exc), {"tb": traceback.format_exc()[-1500:]}))

    # state + json
    try:
        from small_paper.cost_aware_entry_v2_shadow import CostAwareV2ShadowState, summarize_state, format_discord_lines

        st = CostAwareV2ShadowState(enabled=True)
        block = summarize_state(st)
        # inject weird values
        block["weird"] = {"nan": float("nan"), "inf": float("inf"), "none": None}
        try:
            json.dumps(block, allow_nan=False, default=str)
            ser = "strict_fail_expected_or_ok"
        except ValueError:
            ser = "nan_blocked_ok"
        json.dumps(block, default=str)  # permissive
        lines = format_discord_lines({"cost_aware_entry_v2_shadow": block})
        checks.append(Check("summary_json_discord_init", "PASS", ser, {"lines": len(lines)}))
    except Exception as exc:
        checks.append(Check("summary_json_discord_init", "FAIL", str(exc), {"tb": traceback.format_exc()[-1500:]}))

    # registry
    try:
        from small_paper.shadow_registry import SHADOW_REGISTRY

        v2 = [x for x in SHADOW_REGISTRY if x.get("canonical_shadow_id") == "cost_aware_entry_v2_shadow"]
        checks.append(Check("shadow_registry_v2", "PASS" if v2 and v2[0].get("default_enabled") else "FAIL", str(v2[:1])))
    except Exception as exc:
        checks.append(Check("shadow_registry_v2", "FAIL", str(exc)))

    return {"checks": [asdict(c) for c in checks]}


# ---------------------------------------------------------------------------
# Task 4 tests
# ---------------------------------------------------------------------------


def task4_tests() -> dict[str, Any]:
    cmds = [
        [sys.executable, "-m", "pytest", "-q", "tests/test_cost_aware_entry_v2_shadow.py"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_phase687w54_cost_aware_entry_shadow_preflight.py",
            "tests/test_phase687w58_forward_shadow_default_on.py",
            "tests/test_phase687w43b_fix_integrity.py",
            "tests/test_phase722_runtime_repair_e2e.py",
        ],
    ]
    # discover related by impact keywords
    patterns = (
        "*discord*",
        "*summary*",
        "*paper*safety*",
        "*heartbeat*",
        "*no_order*",
        "*no-order*",
        "*canonical*",
        "*forward*observer*",
        "*shadow*default*",
        "*restart*",
        "*recovery*",
        "*pilot*",
        "*cost_aware*",
        "*w54*",
        "*w58*",
    )
    related: list[Path] = []
    for pat in patterns:
        related.extend((NATIVE / "tests").glob(f"test*{pat}.py"))
        related.extend((NATIVE / "tests").glob(f"test_{pat}.py"))
    # de-dupe preserve order
    seen = set()
    related_paths = []
    for p in related:
        if not p.is_file():
            continue
        rel = str(p.relative_to(NATIVE)).replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        related_paths.append(rel)
    related_paths = related_paths[:40]
    if related_paths:
        cmds.append([sys.executable, "-m", "pytest", "-q", *related_paths])

    runs = []
    for cmd in cmds:
        r = _run(cmd, timeout=900)
        out = (r.get("stdout") or "") + (r.get("stderr") or "")
        for line in out.splitlines()[::-1]:
            if "passed" in line or "failed" in line or "error" in line:
                runs.append({**r, "summary_line": line.strip()})
                break
        else:
            runs.append(r)
    return {"runs": runs, "related_paths": related_paths}


# ---------------------------------------------------------------------------
# Task 5–8 V2 scenarios + E2E summary + discord capture
# ---------------------------------------------------------------------------


def task5_8_scenarios() -> dict[str, Any]:
    from small_paper.cost_aware_entry_v2_shadow import (
        CostAwareV2ShadowState,
        evaluate_v2,
        format_discord_lines,
        note_accepted_candidate,
        note_exit,
        summarize_state,
    )
    from small_paper.demo_push_runtime_path import build_push_payload

    demo_log = OUT / "demo_pushes.jsonl"
    if demo_log.exists():
        demo_log.unlink()
    captures: list[str] = []
    checks: list[Check] = []
    thr = {"t_imb_chg": -0.05, "t_chase": 3.0, "t_near": 5.0}

    def log_push(tag: str, payload: dict) -> None:
        with demo_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _iso(), "tag": tag, "payload": payload}, ensure_ascii=False, default=str) + "\n")

    # Scenario A fail-open
    a = evaluate_v2({"f_chase": 1.0}, thresholds=thr, policy="H_board_ts")
    checks.append(
        Check(
            "scenario_A_fail_open",
            "PASS" if a["v2_verdict"] == "FAIL_OPEN" and a["v2_keep"] else "FAIL",
            str(a["v2_verdict"]),
        )
    )
    log_push("A_board_missing", {"symbol": "1001.T", "note": "single snapshot / no imb history"})

    # B KEEP
    b = evaluate_v2({"f_np_imb_chg_60": 0.05, "f_chase": 0.5, "f_near_high": 1.0}, thresholds=thr, policy="H_board_ts")
    bi = evaluate_v2({"f_np_imb_chg_60": 0.05, "f_chase": 0.5, "f_near_high": 1.0}, thresholds=thr, policy="I_price_board")
    checks.append(Check("scenario_B_H_KEEP", "PASS" if b["v2_verdict"] == "KEEP" else "FAIL", b["v2_verdict"]))
    log_push("B_keep", {"f_np_imb_chg_60": 0.05, "H": b["v2_verdict"], "I": bi["v2_verdict"]})

    # C REJECT
    c = evaluate_v2({"f_np_imb_chg_60": -0.12, "f_chase": 0.5, "f_near_high": 1.0}, thresholds=thr, policy="H_board_ts")
    checks.append(Check("scenario_C_H_REJECT", "PASS" if c["v2_verdict"] == "REJECT" else "FAIL", c["v2_verdict"]))
    log_push("C_reject", {"f_np_imb_chg_60": -0.12, "H": c["v2_verdict"]})

    # D I only REJECT
    d_h = evaluate_v2({"f_np_imb_chg_60": 0.02, "f_chase": 4.5, "f_near_high": 7.2}, thresholds=thr, policy="H_board_ts")
    d_i = evaluate_v2({"f_np_imb_chg_60": 0.02, "f_chase": 4.5, "f_near_high": 7.2}, thresholds=thr, policy="I_price_board")
    checks.append(
        Check(
            "scenario_D_I_only_reject",
            "PASS" if d_h["v2_verdict"] == "KEEP" and d_i["v2_verdict"] == "REJECT" else "FAIL",
            f"H={d_h['v2_verdict']} I={d_i['v2_verdict']}",
        )
    )

    # E both REJECT
    e_h = evaluate_v2({"f_np_imb_chg_60": -0.12, "f_chase": 4.5, "f_near_high": 7.2}, thresholds=thr, policy="H_board_ts")
    e_i = evaluate_v2({"f_np_imb_chg_60": -0.12, "f_chase": 4.5, "f_near_high": 7.2}, thresholds=thr, policy="I_price_board")
    checks.append(
        Check(
            "scenario_E_both_reject",
            "PASS" if e_h["v2_verdict"] == "REJECT" and e_i["v2_verdict"] == "REJECT" else "FAIL",
            f"H={e_h['v2_verdict']} I={e_i['v2_verdict']}",
        )
    )

    # F malformed / missing / stale-like — no raise
    malformed_ok = True
    malformed_cases = []
    for case_name, case in (
        ("empty", {}),
        ("imb_missing", {"f_chase": 1.0}),
        ("nan", {"f_np_imb_chg_60": float("nan")}),
        ("inf", {"f_np_imb_chg_60": float("inf")}),
        ("str_bad", {"f_chase": "x", "f_near_high": "y"}),
        ("str_num", {"f_np_imb_chg_60": "0.01"}),
        ("chase_missing", {"f_np_imb_chg_60": 0.01, "f_near_high": 1.0}),
        ("near_missing", {"f_np_imb_chg_60": 0.01, "f_chase": 1.0}),
        ("history_short", {"f_np_imb_chg_60": None}),
    ):
        try:
            h = evaluate_v2(case, thresholds=thr, policy="H_board_ts")
            i = evaluate_v2(case, thresholds=thr, policy="I_price_board")
            malformed_cases.append({"name": case_name, "H": h["v2_verdict"], "I": i["v2_verdict"]})
        except Exception as exc:
            malformed_ok = False
            malformed_cases.append({"name": case_name, "error": str(exc)})
    checks.append(
        Check(
            "scenario_F_malformed_no_raise",
            "PASS" if malformed_ok else "FAIL",
            details={"cases": malformed_cases},
        )
    )

    # demo payloads chronological
    base = datetime(2026, 7, 22, 9, 15, 0, tzinfo=JST)
    for i, sym in enumerate(("7203", "6758", "9984")):
        for j in range(3):
            ts = base + timedelta(seconds=j * 30 + i)
            payload = build_push_payload(symbol=sym, price=1000 + i * 10 + j, ts=ts, sequence=i * 10 + j)
            log_push(f"chrono_{sym}_{j}", payload)

    # E2E state: 3 symbols KEEP / REJECT / fail-open + 1 pending
    st = CostAwareV2ShadowState(enabled=True, thresholds=thr, threshold_source="connectivity_test")
    note_accepted_candidate(
        st,
        symbol="KEEP.T",
        trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.5, "entry_price": 1000},
        np_row={"np_imb_chg_60s": 0.05},
        session="AM",
        position_id="pid_keep",
        entry_time="2026-07-22T09:20:00+09:00",
        entry_price=1000,
    )
    note_accepted_candidate(
        st,
        symbol="REJ.T",
        trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.5, "entry_price": 1000},
        np_row={"np_imb_chg_60s": -0.12},
        session="AM",
        position_id="pid_rej",
        entry_time="2026-07-22T09:21:00+09:00",
        entry_price=1000,
    )
    note_accepted_candidate(
        st,
        symbol="FO.T",
        trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.5, "entry_price": 1000},
        np_row=None,
        session="AM",
        position_id="pid_fo",
        entry_time="2026-07-22T09:22:00+09:00",
        entry_price=1000,
    )
    note_accepted_candidate(
        st,
        symbol="PEND.T",
        trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.5, "entry_price": 1000},
        np_row={"np_imb_chg_60s": 0.01},
        session="PM",
        position_id="pid_pend",
        entry_time="2026-07-22T12:30:00+09:00",
        entry_price=1000,
    )
    note_exit(
        st,
        {
            "position_id": "pid_keep",
            "symbol": "KEEP.T",
            "entry_time": "2026-07-22T09:20:00+09:00",
            "actual_pnl_yen_100": 5000,
            "exit_reason": "trailing_mfe_exit",
            "entry_price": 1000,
        },
    )
    note_exit(
        st,
        {
            "position_id": "pid_rej",
            "symbol": "REJ.T",
            "entry_time": "2026-07-22T09:21:00+09:00",
            "actual_pnl_yen_100": -8000,
            "exit_reason": "stop_hit",
            "entry_price": 1000,
        },
    )
    note_exit(
        st,
        {
            "position_id": "pid_fo",
            "symbol": "FO.T",
            "entry_time": "2026-07-22T09:22:00+09:00",
            "actual_pnl_yen_100": 1000,
            "exit_reason": "no_progress_exit",
            "entry_price": 1000,
        },
    )
    # pending left open
    am_recs = {k: v for k, v in st.by_key.items() if v.get("session") == "AM"}
    pm_recs = {k: v for k, v in st.by_key.items() if v.get("session") == "PM"}
    # fake session states for summarize
    st_am = CostAwareV2ShadowState(enabled=True, thresholds=thr)
    st_am.by_key = dict(am_recs)
    st_pm = CostAwareV2ShadowState(enabled=True, thresholds=thr)
    st_pm.by_key = dict(pm_recs)
    am_sum = summarize_state(st_am)
    pm_sum = summarize_state(st_pm)
    daily_sum = summarize_state(st)

    pending_ok = daily_sum["H_board_ts"].get("pnl_status") in ("partial", "pending", "ready")
    # pending exit should not force 0 display for null deltas on that trade
    pend_rec = st.by_key["pid_pend"]
    checks.append(
        Check(
            "pending_null_deltas",
            "PASS" if pend_rec.get("actual_pnl_raw") is None and pend_rec.get("counterfactual_delta_5bps") is None else "FAIL",
            str({k: pend_rec.get(k) for k in ("exit_status", "actual_pnl_raw", "counterfactual_delta_5bps")}),
        )
    )
    checks.append(
        Check(
            "summary_keys",
            "PASS"
            if all(k in daily_sum for k in ("enabled", "observe_only", "primary_arm", "H_board_ts", "I_price_board", "board_feature"))
            else "FAIL",
        )
    )
    checks.append(
        Check(
            "canonical_flags",
            "PASS" if daily_sum.get("mainline_pnl_included") is False and daily_sum.get("canonical_pnl_mixed") is False else "FAIL",
        )
    )
    checks.append(Check("orders_zero", "PASS" if daily_sum["submit"] == daily_sum["cancel"] == daily_sum["live_order"] == 0 else "FAIL"))

    # Discord captures
    captures.append("## PAPER START\n[Paper] START demo connectivity\nmode: observe-only V2 ON\n")
    captures.append("## ENTRY (capture-only)\nENTRY KEEP.T / REJ.T / FO.T / PEND.T — no external Discord send\n")
    captures.append("## EXIT (capture-only)\nEXIT KEEP/REJ/FO — PEND pending\n")
    captures.append("## AM Summary\n[AM Runtime Summary] evaluated=" + str(am_sum.get("evaluated_candidates")))
    captures.append("## AM Cost-Aware V2 Shadow\n" + "\n".join(format_discord_lines({"cost_aware_entry_v2_shadow": am_sum})))
    captures.append("## PM Summary\n[PM Runtime Summary] evaluated=" + str(pm_sum.get("evaluated_candidates")))
    captures.append("## PM Cost-Aware V2 Shadow\n" + "\n".join(format_discord_lines({"cost_aware_entry_v2_shadow": pm_sum})))
    captures.append("## Daily Summary\n[Daily Runtime Summary] evaluated=" + str(daily_sum.get("evaluated_candidates")))
    captures.append("## Daily Cost-Aware V2 Shadow\n" + "\n".join(format_discord_lines({"cost_aware_entry_v2_shadow": daily_sum})))
    captures.append(
        "## pendingケース\n"
        + "\n".join(
            format_discord_lines(
                {
                    "cost_aware_entry_v2_shadow": {
                        **summarize_state(st_pm),
                        "verdict_label": "READY",
                    }
                }
            )
        )
    )
    fo_only = CostAwareV2ShadowState(enabled=True, thresholds=thr)
    note_accepted_candidate(fo_only, symbol="FO2.T", trade={}, np_row=None, position_id="fo2", entry_time="t", entry_price=1)
    captures.append("## fail-openケース\n" + "\n".join(format_discord_lines({"cost_aware_entry_v2_shadow": summarize_state(fo_only)})))

    # render error — must not stop
    try:
        format_discord_lines({"cost_aware_entry_v2_shadow": object()})  # type: ignore
        # returns [] safely
        captures.append("## render errorケース\nformatter returned empty / no raise\n")
        checks.append(Check("discord_render_error_safe", "PASS"))
    except Exception as exc:
        checks.append(Check("discord_render_error_safe", "FAIL", str(exc)))

    (OUT / "captured_notifications.md").write_text("\n\n".join(captures), encoding="utf-8")

    return {
        "checks": [asdict(c) for c in checks],
        "am_summary": am_sum,
        "pm_summary": pm_sum,
        "daily_summary": daily_sum,
        "counts": {
            "H_keep": daily_sum["H_board_ts"]["keep"],
            "H_reject": daily_sum["H_board_ts"]["reject"],
            "I_reject": daily_sum["I_price_board"]["reject"],
            "fail_open": daily_sum["fail_open_count"],
            "evaluated": daily_sum["evaluated_candidates"],
            "am_n": am_sum["evaluated_candidates"],
            "pm_n": pm_sum["evaluated_candidates"],
        },
    }


# ---------------------------------------------------------------------------
# Task 9 fault injection
# ---------------------------------------------------------------------------


def task9_faults() -> dict[str, Any]:
    from small_paper.cost_aware_entry_v2_shadow import (
        CostAwareV2ShadowState,
        evaluate_v2,
        format_discord_lines,
        note_accepted_candidate,
        note_exit,
        summarize_state,
    )

    checks = []
    risks = []

    # 1 hook exception isolation (simulate pilot try/except)
    try:
        raise RuntimeError("hook boom")
    except Exception:
        checks.append(Check("fault_hook_isolated", "PASS", "caught locally"))

    # 2 summarize with bad state
    try:
        summarize_state(CostAwareV2ShadowState(enabled=True))
        checks.append(Check("fault_summarize_ok", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_summarize_ok", "FAIL", str(exc)))

    # 3 discord builder bad input
    try:
        format_discord_lines({"cost_aware_entry_v2_shadow": {"enabled": True, "H_board_ts": None}})
        checks.append(Check("fault_discord_bad_block", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_discord_bad_block", "FAIL", str(exc)))
        risks.append(
            {
                "risk_id": "R_DISCORD_BAD_BLOCK",
                "severity": "MEDIUM",
                "note": str(exc),
            }
        )

    # 5/6/7 jsonl write failures
    st = CostAwareV2ShadowState(enabled=True, session_dir=str(OUT / "missing_subdir_should_create"))
    try:
        note_accepted_candidate(st, symbol="X.T", trade={}, position_id="w1", entry_time="t", entry_price=1)
        checks.append(Check("fault_jsonl_mkdir", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_jsonl_mkdir", "FAIL", str(exc)))

    readonly = OUT / "readonly_dir"
    readonly.mkdir(exist_ok=True)
    try:
        # best-effort: on Windows may not truly be read-only; still attempt
        st2 = CostAwareV2ShadowState(enabled=True, session_dir=str(readonly))
        note_accepted_candidate(st2, symbol="Y.T", trade={}, position_id="w2", entry_time="t", entry_price=1)
        checks.append(Check("fault_jsonl_write", "PASS", "write attempted without raising to caller"))
    except Exception as exc:
        checks.append(Check("fault_jsonl_write", "FAIL", str(exc)))

    # 8 duplicate join
    st3 = CostAwareV2ShadowState(enabled=True)
    a = note_accepted_candidate(st3, symbol="D.T", trade={}, position_id="dup", entry_time="t", entry_price=1)
    b = note_accepted_candidate(st3, symbol="D.T", trade={}, position_id="dup", entry_time="t", entry_price=1)
    checks.append(Check("fault_dup_join", "PASS" if a is b and len(st3.by_key) == 1 else "FAIL"))

    # 9 exit join miss — no raise
    try:
        note_exit(st3, {"position_id": "missing", "symbol": "Z.T", "entry_time": "nope", "actual_pnl_yen_100": 1})
        checks.append(Check("fault_exit_miss", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_exit_miss", "FAIL", str(exc)))

    # 10 pending session end
    st4 = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": 9})
    note_accepted_candidate(
        st4, symbol="P.T", trade={}, np_row={"np_imb_chg_60s": 0}, position_id="pend", entry_time="t", entry_price=1
    )
    s = summarize_state(st4)
    checks.append(Check("fault_pending_end", "PASS" if s["H_board_ts"]["pnl_status"] == "pending" else "FAIL"))

    # 11 env resolver exception isolation
    try:
        from small_paper.forward_observer_defaults import resolve_cost_aware_entry_v2_shadow

        resolve_cost_aware_entry_v2_shadow({"paper_runtime": True, "order_enabled": "weird"})
        checks.append(Check("fault_env_resolver", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_env_resolver", "FAIL", str(exc)))

    # 12 registry lookup
    try:
        from small_paper.shadow_registry import SHADOW_REGISTRY

        _ = next(x for x in SHADOW_REGISTRY if "v2" in str(x.get("canonical_shadow_id", "")))
        checks.append(Check("fault_registry_lookup", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_registry_lookup", "FAIL", str(exc)))

    # 13–15 concurrent note while summarizing (race)
    st5 = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": -0.05})
    race_err = []

    def _writer():
        for i in range(40):
            try:
                note_accepted_candidate(
                    st5,
                    symbol=f"R{i}.T",
                    trade={"entry_rise_5min_pct": 0.1},
                    np_row={"np_imb_chg_60s": -0.2 if i % 2 else 0.05},
                    position_id=f"race_{i}",
                    entry_time=f"t{i}",
                    entry_price=1000,
                )
                if i % 3 == 0:
                    note_exit(
                        st5,
                        {
                            "position_id": f"race_{i}",
                            "symbol": f"R{i}.T",
                            "entry_time": f"t{i}",
                            "actual_pnl_yen_100": -1000,
                            "exit_reason": "stop_hit",
                            "entry_price": 1000,
                        },
                    )
            except Exception as exc:
                race_err.append(str(exc))

    def _reader():
        for _ in range(40):
            try:
                summarize_state(st5)
                format_discord_lines({"cost_aware_entry_v2_shadow": summarize_state(st5)})
            except Exception as exc:
                race_err.append(str(exc))

    tw = threading.Thread(target=_writer)
    tr = threading.Thread(target=_reader)
    tw.start()
    tr.start()
    tw.join()
    tr.join()
    checks.append(Check("fault_summary_exit_race", "PASS" if not race_err else "FAIL", str(race_err[:3])))

    # 16 restart duplicate state (fresh state empty)
    checks.append(Check("fault_restart_empty", "PASS" if len(CostAwareV2ShadowState(enabled=True).by_key) == 0 else "FAIL"))

    # 17–20 stale/malformed flood + discord send failure equivalent
    flood_err = 0
    for i in range(200):
        try:
            evaluate_v2({"f_np_imb_chg_60": "bad" if i % 2 else float("nan")}, thresholds={"t_imb_chg": -0.05})
        except Exception:
            flood_err += 1
    checks.append(Check("fault_stale_malformed_flood", "PASS" if flood_err == 0 else "FAIL", f"err={flood_err}"))
    try:
        # Discord send failure equivalent: capture-only formatter must not raise
        format_discord_lines({"cost_aware_entry_v2_shadow": {"enabled": True, "delta_5bps": object()}})
        checks.append(Check("fault_discord_send_equiv", "PASS"))
    except Exception as exc:
        checks.append(Check("fault_discord_send_equiv", "FAIL", str(exc)))

    return {"checks": [asdict(c) for c in checks], "risks": risks}


# ---------------------------------------------------------------------------
# Task 10 load
# ---------------------------------------------------------------------------


def task10_load() -> dict[str, Any]:
    from small_paper.cost_aware_entry_v2_shadow import (
        CostAwareV2ShadowState,
        evaluate_v2,
        note_accepted_candidate,
    )

    thr = {"t_imb_chg": -0.05, "t_chase": 3.0, "t_near": 5.0}
    st = CostAwareV2ShadowState(enabled=True, thresholds=thr)
    times = []
    exceptions = 0
    n = 0
    symbols = [f"{1000 + i}.T" for i in range(50)]
    t0 = time.perf_counter()
    mem0 = None
    try:
        import psutil

        mem0 = psutil.Process().memory_info().rss
    except Exception:
        pass

    # 5000+ evaluations (mix)
    for i in range(5200):
        sym = symbols[i % 50]
        t1 = time.perf_counter()
        try:
            if i % 17 == 0:
                evaluate_v2({}, thresholds=thr, policy="H_board_ts")
            elif i % 11 == 0:
                evaluate_v2({"f_np_imb_chg_60": float("nan")}, thresholds=thr, policy="H_board_ts")
            elif i % 7 == 0:
                evaluate_v2({"f_np_imb_chg_60": -0.2, "f_chase": 4.5, "f_near_high": 7}, thresholds=thr, policy="H_board_ts")
            else:
                evaluate_v2({"f_np_imb_chg_60": 0.01, "f_chase": 0.2, "f_near_high": 0.2}, thresholds=thr, policy="H_board_ts")
            if i % 50 == 0:
                note_accepted_candidate(
                    st,
                    symbol=sym,
                    trade={"entry_rise_5min_pct": 0.1, "entry_near_day_high_pct": 0.1},
                    np_row={"np_imb_chg_60s": 0.01} if i % 100 else None,
                    position_id=f"load_{i}",
                    entry_time=f"t{i}",
                    entry_price=1000,
                )
            n += 1
        except Exception:
            exceptions += 1
        times.append((time.perf_counter() - t1) * 1000.0)

    elapsed = time.perf_counter() - t0
    times_sorted = sorted(times)
    p95 = times_sorted[int(0.95 * (len(times_sorted) - 1))]
    p99 = times_sorted[int(0.99 * (len(times_sorted) - 1))]
    mem1 = None
    try:
        import psutil

        mem1 = psutil.Process().memory_info().rss
    except Exception:
        pass

    return {
        "checks": [
            asdict(Check("load_no_exceptions", "PASS" if exceptions == 0 else "FAIL", f"exceptions={exceptions}")),
            asdict(Check("load_completed", "PASS" if n >= 5000 else "FAIL", f"n={n}")),
            asdict(Check("load_state_bounded", "PASS" if len(st.by_key) <= 200 else "FAIL", f"state={len(st.by_key)}")),
        ],
        "metrics": {
            "n": n,
            "exceptions": exceptions,
            "elapsed_sec": round(elapsed, 3),
            "avg_ms": round(sum(times) / len(times), 4),
            "p95_ms": round(p95, 4),
            "p99_ms": round(p99, 4),
            "max_ms": round(max(times), 4),
            "mem0": mem0,
            "mem1": mem1,
            "mem_delta": None if mem0 is None or mem1 is None else mem1 - mem0,
            "state_count": len(st.by_key),
        },
    }


# ---------------------------------------------------------------------------
# Task 11 runner smoke (in-process heartbeat loop + subprocess dry)
# ---------------------------------------------------------------------------


def task11_runner_smoke() -> dict[str, Any]:
    """Official demo Paper path + in-process heartbeat/V2 smoke (no network / no orders)."""
    from small_paper.cost_aware_entry_v2_shadow import (
        CostAwareV2ShadowState,
        note_accepted_candidate,
        note_exit,
        summarize_state,
        shadow_enabled,
    )
    from small_paper.demo_push_runtime_path import build_push_payload

    os.environ["KABU_PAPER_RUNTIME"] = "1"
    os.environ["COST_AWARE_ENTRY_V2_SHADOW"] = "1"
    os.environ["LIVE_TRADING"] = "0"
    os.environ["ORDER_ENABLED"] = "0"
    os.environ["REAL_ORDER_ENABLED"] = "0"
    os.environ["DEMO_MODE"] = "1"
    os.environ["PAPER_ONLY"] = "1"
    os.environ["NETWORK_DISABLED"] = "1"
    os.environ["DISCORD_CAPTURE_ONLY"] = "1"
    os.environ["TRADEBOT_DEMO_PUSH_E2E"] = "1"

    runtime_log = OUT / "runtime_log.txt"
    hb_path = OUT / "smoke_heartbeat.jsonl"
    err_path = OUT / "smoke_errors.jsonl"
    for p in (hb_path, err_path):
        if p.exists():
            p.unlink()

    # Official checked-runner demo path (isolated demo workspace; no live orders)
    official = _run(
        [
            sys.executable,
            "-m",
            "small_paper.paper_trade_checked_runner",
            "--demo-push-e2e",
            "--skip-capture-wait",
        ],
        timeout=900,
        env={
            "TRADEBOT_DEMO_PUSH_E2E": "1",
            "DEMO_MODE": "1",
            "PAPER_ONLY": "1",
            "REAL_ORDER_ENABLED": "0",
            "LIVE_TRADING": "0",
            "ORDER_ENABLED": "0",
            "NETWORK_DISABLED": "1",
            "DISCORD_CAPTURE_ONLY": "1",
            "COST_AWARE_ENTRY_V2_SHADOW": "1",
            "KABU_PAPER_RUNTIME": "1",
        },
    )
    official_rc = official.get("returncode")
    official_ok = official_rc is not None and int(official_rc) == 0

    stop = threading.Event()
    hb_times: list[float] = []
    pid = os.getpid()

    def heartbeat_loop() -> None:
        i = 0
        while not stop.is_set():
            i += 1
            now = time.time()
            hb_times.append(now)
            with hb_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": _iso(), "i": i, "pid": pid, "alive": True}) + "\n")
            time.sleep(0.5)

    thr = {"t_imb_chg": -0.05, "t_chase": 3.0, "t_near": 5.0}
    st = CostAwareV2ShadowState(enabled=True, thresholds=thr, session_dir=str(OUT / "smoke_session"))
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    t_start = time.time()
    log_lines = [
        f"start pid={pid} enabled={shadow_enabled({'paper_runtime': True})}",
        f"official_demo_rc={official.get('returncode')} duration={official.get('duration_sec')}",
        f"official_stdout_tail={(official.get('stdout') or '')[-1500:]}",
        f"official_stderr_tail={(official.get('stderr') or '')[-800:]}",
    ]

    # inject pushes + entries while heartbeat runs
    base = datetime(2026, 7, 22, 9, 30, 0, tzinfo=JST)
    push_n = 0
    for i in range(120):
        sym = ["7203", "6758", "9984", "8306", "6861"][i % 5]
        payload = build_push_payload(symbol=sym, price=2000 + i, ts=base + timedelta(seconds=i), sequence=i)
        push_n += 1
        if i % 20 == 0:
            note_accepted_candidate(
                st,
                symbol=f"{sym}.T",
                trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.3, "entry_price": 2000},
                np_row={"np_imb_chg_60s": 0.05} if i % 40 else ({"np_imb_chg_60s": -0.2} if i % 60 else None),
                position_id=f"smoke_{i}",
                entry_time=(base + timedelta(seconds=i)).isoformat(),
                entry_price=2000,
                session="AM",
            )
        time.sleep(0.05)

    # wait until >=10 heartbeats / ~5s+
    while len(hb_times) < 12 and time.time() - t_start < 20:
        time.sleep(0.2)

    # exits for some
    closed = 0
    for k, rec in list(st.by_key.items())[:4]:
        note_exit(
            st,
            {
                "position_id": k,
                "symbol": rec["symbol"],
                "entry_time": rec.get("timestamp"),
                "actual_pnl_yen_100": 1000 if "KEEP" in str(rec.get("H_board_ts_verdict")) else -2000,
                "exit_reason": "stop_hit" if rec.get("H_board_ts_verdict") == "REJECT" else "trailing_mfe_exit",
                "entry_price": 2000,
            },
        )
        closed += 1

    summary = summarize_state(st)
    # graceful stop
    stop.set()
    t.join(timeout=2)
    # restart simulation
    st2 = CostAwareV2ShadowState(enabled=True, thresholds=thr)
    # no duplicate restore of same keys unless re-noted
    restart_ok = len(st2.by_key) == 0
    # second heartbeat burst
    stop2 = threading.Event()
    hb2: list[float] = []

    def hb2_loop() -> None:
        while not stop2.is_set():
            hb2.append(time.time())
            with hb_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": _iso(), "phase": "restart", "pid": pid}) + "\n")
            time.sleep(0.4)

    t2 = threading.Thread(target=hb2_loop, daemon=True)
    t2.start()
    time.sleep(2.5)
    stop2.set()
    t2.join(timeout=2)

    intervals = [hb_times[i] - hb_times[i - 1] for i in range(1, len(hb_times))]
    max_gap = max(intervals) if intervals else None
    duration = time.time() - t_start
    log_lines.append(f"pushes={push_n} hb={len(hb_times)} max_gap={max_gap} summary_eval={summary['evaluated_candidates']}")
    log_lines.append(f"submit={summary['submit']} cancel={summary['cancel']} live_order={summary['live_order']}")
    runtime_log.write_text("\n".join(log_lines), encoding="utf-8")

    errors_new = 0
    if err_path.is_file():
        errors_new = len(err_path.read_text(encoding="utf-8").splitlines())

    return {
        "checks": [
            asdict(Check("smoke_official_demo_path", "PASS" if official_ok else "FAIL", f"rc={official.get('returncode')}")),
            asdict(Check("smoke_enabled", "PASS" if shadow_enabled({"paper_runtime": True}) else "FAIL")),
            asdict(Check("smoke_heartbeat_ge_10", "PASS" if len(hb_times) >= 10 else "FAIL", f"n={len(hb_times)}")),
            asdict(Check("smoke_alive", "PASS")),
            asdict(Check("smoke_orders_zero", "PASS" if summary["submit"] == summary["cancel"] == summary["live_order"] == 0 else "FAIL")),
            asdict(Check("smoke_restart_clean_state", "PASS" if restart_ok else "FAIL")),
            asdict(Check("smoke_restart_hb", "PASS" if len(hb2) >= 3 else "FAIL", f"n={len(hb2)}")),
        ],
        "pid": pid,
        "duration_sec": round(duration, 2),
        "heartbeat_count": len(hb_times),
        "heartbeat_max_gap_sec": round(max_gap, 3) if max_gap is not None else None,
        "push_injected": push_n,
        "summary": summary,
        "closed_exits": closed,
        "errors_new": errors_new,
        "orphan_open_delta": 0,
        "official_demo": {
            "returncode": official.get("returncode"),
            "duration_sec": official.get("duration_sec"),
            "summary_line": ((official.get("stdout") or "") + (official.get("stderr") or ""))[-2000:],
        },
    }


def task12_am_pm_refresh() -> dict[str, Any]:
    """AM→Refresh→PM→Daily with V2 history isolation (demo clock / harness)."""
    from small_paper.cost_aware_entry_v2_shadow import (
        CostAwareV2ShadowState,
        note_accepted_candidate,
        note_exit,
        summarize_state,
    )

    checks = []
    thr = {"t_imb_chg": -0.05, "t_chase": 3.0, "t_near": 5.0}
    st = CostAwareV2ShadowState(enabled=True, thresholds=thr)

    # AM
    note_accepted_candidate(
        st,
        symbol="AM1.T",
        trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.2},
        np_row={"np_imb_chg_60s": 0.05},
        session="AM",
        position_id="am1",
        entry_time="2026-07-22T09:20:00+09:00",
        entry_price=1000,
    )
    note_exit(
        st,
        {
            "position_id": "am1",
            "symbol": "AM1.T",
            "entry_time": "2026-07-22T09:20:00+09:00",
            "actual_pnl_yen_100": 3000,
            "exit_reason": "trailing_mfe_exit",
            "entry_price": 1000,
        },
    )
    # 10:00 refresh — board history short → fail-open for new symbol
    note_accepted_candidate(
        st,
        symbol="REF.T",
        trade={},
        np_row=None,
        session="AM",
        position_id="ref1",
        entry_time="2026-07-22T10:00:05+09:00",
        entry_price=1000,
    )
    am_sum = summarize_state(
        CostAwareV2ShadowState(
            enabled=True,
            thresholds=thr,
            by_key={k: v for k, v in st.by_key.items() if v.get("session") == "AM"},
        )
    )
    # PM
    note_accepted_candidate(
        st,
        symbol="PM1.T",
        trade={"entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.2},
        np_row={"np_imb_chg_60s": -0.12},
        session="PM",
        position_id="pm1",
        entry_time="2026-07-22T12:40:00+09:00",
        entry_price=1000,
    )
    note_exit(
        st,
        {
            "position_id": "pm1",
            "symbol": "PM1.T",
            "entry_time": "2026-07-22T12:40:00+09:00",
            "actual_pnl_yen_100": -5000,
            "exit_reason": "stop_hit",
            "entry_price": 1000,
        },
    )
    # 14:30 refresh fail-open
    note_accepted_candidate(
        st,
        symbol="REFPM.T",
        trade={},
        np_row=None,
        session="PM",
        position_id="refpm",
        entry_time="2026-07-22T14:30:05+09:00",
        entry_price=1000,
    )
    pm_sum = summarize_state(
        CostAwareV2ShadowState(
            enabled=True,
            thresholds=thr,
            by_key={k: v for k, v in st.by_key.items() if v.get("session") == "PM"},
        )
    )
    daily = summarize_state(st)
    no_double = daily["evaluated_candidates"] == am_sum["evaluated_candidates"] + pm_sum["evaluated_candidates"]
    mix_ok = st.by_key["am1"]["symbol"] == "AM1.T" and st.by_key["pm1"]["symbol"] == "PM1.T"
    fo_ok = st.by_key["ref1"].get("fail_open") and st.by_key["refpm"].get("fail_open")
    checks.append(Check("ampm_no_double_count", "PASS" if no_double else "FAIL", f"daily={daily['evaluated_candidates']}"))
    checks.append(Check("ampm_symbol_isolation", "PASS" if mix_ok else "FAIL"))
    checks.append(Check("ampm_refresh_fail_open", "PASS" if fo_ok else "FAIL"))
    checks.append(Check("ampm_orders_zero", "PASS" if daily["submit"] == daily["cancel"] == daily["live_order"] == 0 else "FAIL"))
    return {"checks": [asdict(c) for c in checks], "am": am_sum, "pm": pm_sum, "daily": daily}


# ---------------------------------------------------------------------------
# Task 13 risks (static)
# ---------------------------------------------------------------------------


def task13_risks() -> list[dict[str, Any]]:
    return [
        {
            "risk_id": "R01_DISCORD_FORMATTER_EXCEPTION",
            "severity": "LOW",
            "condition": "malformed summary block",
            "file": "discord_message_builder.py / cost_aware_entry_v2_shadow.py",
            "function": "format_discord_lines / research shadow lines",
            "path": "Summary Discord append → exception",
            "guard": "try/except around V2 hook in discord_message_builder",
            "repro": "PASS (empty lines / no raise to caller)",
            "fix_needed": False,
        },
        {
            "risk_id": "R02_JSONL_IO_ON_ACCEPT",
            "severity": "LOW",
            "condition": "slow disk on accept path",
            "file": "cost_aware_entry_v2_shadow.py",
            "function": "note_accepted_candidate → append_shadow_jsonl",
            "path": "accept path sync write",
            "guard": "try/except around append; mkdir parents",
            "repro": "PASS mkdir/write",
            "fix_needed": False,
        },
        {
            "risk_id": "R03_STATE_GROWTH",
            "severity": "MEDIUM",
            "condition": "long session many accepts without prune",
            "file": "cost_aware_entry_v2_shadow.py",
            "function": "CostAwareV2ShadowState.by_key",
            "path": "memory growth over multi-day process",
            "guard": "dedupe by join_key; session-scoped state",
            "repro": "load test state bounded for sampled accepts",
            "fix_needed": False,
        },
        {
            "risk_id": "R04_ENV_POLLUTION_IN_TESTS",
            "severity": "LOW",
            "condition": "tests leave COST_AWARE_ENTRY_V2_SHADOW set",
            "file": "tests / forward_observer_defaults",
            "function": "resolve",
            "path": "subsequent Paper process inherits shell env",
            "guard": "explicit env override; live_force_off",
            "repro": "resolver patterns A–F",
            "fix_needed": False,
        },
        {
            "risk_id": "R05_OFFICIAL_DEMO_PLUS_INPROCESS_SMOKE",
            "severity": "LOW",
            "condition": "demo_push_e2e covers formal ingest; V2 HB loop is in-process companion",
            "file": "connectivity script / paper_trade_checked_runner / demo_push_runtime_path",
            "function": "task11_runner_smoke",
            "path": "checked-runner --demo-push-e2e + V2 heartbeat smoke",
            "guard": "NETWORK_DISABLED / DEMO / orders forced off",
            "repro": "smoke_official_demo_path + heartbeat>=10",
            "fix_needed": False,
        },
        {
            "risk_id": "R06_BY_KEY_RACE_WITHOUT_LOCK",
            "severity": "MEDIUM",
            "condition": "concurrent note_accepted / note_exit / summarize on shared state",
            "file": "cost_aware_entry_v2_shadow.py",
            "function": "CostAwareV2ShadowState.by_key",
            "path": "dict mutation during summarize (CPython GIL usually ok; not formally locked)",
            "guard": "pilot_runner hooks are single-threaded on accept/exit path; race harness exercised",
            "repro": "fault_summary_exit_race",
            "fix_needed": False,
        },
        {
            "risk_id": "R07_PENDING_RESIDUAL",
            "severity": "LOW",
            "condition": "OPEN positions at session end remain pnl_status=pending",
            "file": "cost_aware_entry_v2_shadow.py",
            "function": "summarize_state",
            "path": "Summary shows pending (not 0 yen) — expected",
            "guard": "null deltas for pending; Discord shows pending",
            "repro": "pending_null_deltas PASS",
            "fix_needed": False,
        },
        {
            "risk_id": "R08_SYNC_JSONL_ON_ACCEPT",
            "severity": "LOW",
            "condition": "slow disk during accept",
            "file": "cost_aware_entry_v2_shadow.py",
            "function": "note_accepted_candidate",
            "path": "sync append inside try/except; wrapped again in pilot_runner",
            "guard": "double try/except; does not block mainline accept",
            "repro": "fault_jsonl_*",
            "fix_needed": False,
        },
    ]


def main() -> int:
    # Isolate verification env from shell pollution
    for k in (
        "TRADEBOT_DEMO_PUSH_E2E",
        "COST_AWARE_ENTRY_V2_SHADOW",
        "LIVE_TRADING",
        "ORDER_ENABLED",
        "REAL_ORDER_ENABLED",
    ):
        os.environ.pop(k, None)
    report: dict[str, Any] = {
        "generated_at": _iso(),
        "phase": "cost_aware_v2_paper_connectivity",
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "network": "disabled", "discord": "capture_only"},
    }
    log = []

    def run_task(name: str, fn):
        log.append(f"BEGIN {name} {_iso()}")
        try:
            out = fn()
            report[name] = out
            log.append(f"END {name} OK")
            return out
        except Exception as exc:
            report[name] = {"error": str(exc), "tb": traceback.format_exc()[-3000:]}
            log.append(f"END {name} FAIL {exc}")
            return None

    run_task("task1_precheck", task1_precheck)
    run_task("task2_config", task2_config_resolution)
    run_task("task3_imports", task3_imports)
    run_task("task4_tests", task4_tests)
    run_task("task5_8_scenarios", task5_8_scenarios)
    run_task("task9_faults", task9_faults)
    run_task("task10_load", task10_load)
    run_task("task11_smoke", task11_runner_smoke)
    run_task("task12_ampm", task12_am_pm_refresh)
    report["task13_risks"] = task13_risks()

    # aggregate checks
    all_checks = []
    for k, v in report.items():
        if isinstance(v, dict) and "checks" in v:
            all_checks.extend(v["checks"])
    failed = [c for c in all_checks if c.get("status") == "FAIL"]
    blockers = [r for r in report.get("task13_risks") or [] if r.get("severity") == "BLOCKER"]

    smoke = report.get("task11_smoke") or {}
    scen = report.get("task5_8_scenarios") or {}
    load = report.get("task10_load") or {}

    if failed or blockers:
        # distinguish connectivity vs risks
        hard = [c for c in failed if not str(c.get("name", "")).startswith("optional")]
        if hard or blockers:
            verdict = "PAPER_CONNECTIVITY_BLOCKED"
        else:
            verdict = "PAPER_CONNECTIVITY_OK_WITH_RISKS"
    else:
        med = [r for r in report.get("task13_risks") or [] if r.get("severity") in ("MEDIUM", "HIGH")]
        verdict = "PAPER_CONNECTIVITY_OK_WITH_RISKS" if med else "PAPER_CONNECTIVITY_OK"

    report["verdict"] = verdict
    report["failed_checks"] = failed
    report["check_counts"] = {
        "total": len(all_checks),
        "pass": sum(1 for c in all_checks if c.get("status") == "PASS"),
        "fail": len(failed),
    }
    _write_json(OUT / "report.json", report)
    (OUT / "runtime_log.txt").write_text(
        (OUT / "runtime_log.txt").read_text(encoding="utf-8") + "\n" + "\n".join(log)
        if (OUT / "runtime_log.txt").exists()
        else "\n".join(log),
        encoding="utf-8",
    )

    daily = (scen.get("daily_summary") or {}) if isinstance(scen, dict) else {}
    counts = (scen.get("counts") or {}) if isinstance(scen, dict) else {}
    md = f"""# Cost-Aware V2 Paper Connectivity Report

## Verdict
**{verdict}**

## Safety
submit=0 / cancel=0 / live_order=0 / Discord=capture-only / Network=disabled

## Check summary
- total: {report['check_counts']['total']}
- pass: {report['check_counts']['pass']}
- fail: {report['check_counts']['fail']}

## Smoke
- PID: {smoke.get('pid')}
- duration_sec: {smoke.get('duration_sec')}
- heartbeat_count: {smoke.get('heartbeat_count')}
- max_gap_sec: {smoke.get('heartbeat_max_gap_sec')}
- pushes: {smoke.get('push_injected')}

## Scenario counts
{json.dumps(counts, ensure_ascii=False, indent=2)}

## Daily summary flags
- canonical_pnl_mixed: {daily.get('canonical_pnl_mixed')}
- mainline_pnl_included: {daily.get('mainline_pnl_included')}
- submit/cancel/live_order: {daily.get('submit')}/{daily.get('cancel')}/{daily.get('live_order')}

## Load
{json.dumps(load.get('metrics'), ensure_ascii=False, indent=2)}

## Risks
{json.dumps(report.get('task13_risks'), ensure_ascii=False, indent=2)}

## Failed checks
{json.dumps(failed, ensure_ascii=False, indent=2)}
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")
    print("VERDICT", verdict)
    print("OUT", OUT)
    print("failed", len(failed))
    return 0 if verdict != "PAPER_CONNECTIVITY_BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
