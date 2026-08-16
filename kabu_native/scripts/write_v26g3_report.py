#!/usr/bin/env python
"""Write V26-G3 report.json / report.md / audit.xlsx. Does not Formal-freeze."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "research" / "v26g3_runtime_fix_candidate2_preflight"
PROSP = NATIVE / "results" / "research" / "v1r_exit_v2_prospective_activation"

VERDICT = "V1R_V26G3_CANDIDATE2_FULL_RUNTIME_PREFLIGHT_FAIL"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cert = _load(OUT / "paper_runtime_full_day_certification.json")
    c1 = _load(PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1.json")
    c2 = _load(PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2.json")
    c3 = _load(PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3.json")
    c4 = _load(PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4.json")
    v25_sel = _load(PROSP / "active_v1r_activation.json")

    report = {
        "verdict": VERDICT,
        "v26_freeze_eligible": False,
        "formal_v26_freeze": False,
        "formal_paper_allowed": False,
        "submit_cancel_live": "0/0/0",
        "created_at": datetime.now(JST).isoformat(),
        "official_cert_verdict": cert.get("verdict"),
        "official_failed_tests": cert.get("failed_tests"),
        "timeout_124_count": 0,
        "orchestrator_forced_kill_used_as_pass": False,
        "candidate1": {
            "id": c1.get("candidate_id"),
            "sha256": c1.get("sha256"),
            "status": "FAILED",
            "audit_only": True,
            "immutable": True,
            "bytes_rewritten": False,
            "promoted_to_pass": False,
            "reused": False,
        },
        "v25": {
            "activation_id": v25_sel.get("activation_id"),
            "selector_unchanged": v25_sel.get("activation_id")
            == "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25",
        },
        "snapshots": {
            "V26G3_2": {
                "id": c2.get("candidate_id"),
                "sha256": c2.get("sha256"),
                "status": "UNCERTIFIED",
                "immutable": True,
                "outcome": "PREFLIGHT_ABORTED_48X_CLOCK_RACE",
                "bytes_rewritten": False,
            },
            "V26G3_3": {
                "id": c3.get("candidate_id"),
                "sha256": c3.get("sha256"),
                "status": "UNCERTIFIED",
                "immutable": True,
                "outcome": "PREFLIGHT_ABORTED_ARM_FILE_TEAR",
                "bytes_rewritten": False,
            },
            "V26G3_4": {
                "id": c4.get("candidate_id"),
                "sha256": c4.get("sha256"),
                "status": "UNCERTIFIED",
                "immutable": True,
                "inventory_n": len(c4.get("runtime_file_sha256") or {}),
                "runtime_inventory_digest": c4.get("runtime_inventory_digest"),
                "candidate_source_digest": c4.get("candidate_source_digest"),
                "formal_paper_allowed": False,
                "preflight_identity": True,
            },
        },
        "targeted_regression": {
            "pytest_n": 162,
            "failed_tests": [],
        },
        "defects": {
            "A_nameerror": {
                "callsite": "pilot_runner.py run_live_dry_run Discord session_end / archive / seal used log.warning with no module logger",
                "fix": "logging.getLogger(__name__); no swallow; session_end not skipped",
                "recurrence_on_c4": "NOT_RECURRED",
                "seal_written": True,
            },
            "B_canonical_summary": {
                "sot_when_live_primary": "v1r_primary EXIT_EXECUTED exclusive",
                "window_a_g2_mismatch": "trade_count=0 vs EXIT_EXECUTED=9",
                "c4_full_day_am_canonical_trade_count": 0,
                "c4_full_day_am_primary_exits": 0,
                "parity_on_c4_full_day": "N/A_no_executed_v1r_exit",
                "unit_tests": "PASS",
            },
            "C_48x_waiting_market": {
                "class": "certification_replay_clock_parity",
                "not": "do_not_change_production_entry_to_match_48x",
                "g2_full_day": "PUSH>0 dual_ticks=0 INVALID_NO_GATE WAITING_MARKET",
                "c4_full_day_am": {
                    "session": "live_session_002718",
                    "session_validity": "VALID_SESSION",
                    "gate_evaluations": 2533,
                    "dual_ticks": 15432,
                    "runtime_state": "RUNNING",
                    "stop_reason": "morning_session_close",
                    "primary_fills": 0,
                    "primary_exits": 0,
                },
                "residual": "Paper now_jst can still race STOP/session_end if ARM_FILE SoT is missed; Window A 1.0x STOP fired with gate=0 / WAITING_MARKET",
            },
            "D_clock_stop": {
                "window_a_stop_reason": "session_clock_stop",
                "window_a_exit_code": 2,
                "timeout_124": 0,
                "now_jst_not_frozen_at_stop": True,
            },
            "E_session_artifact_incomplete": {
                "independent_after_A_D": "partially_yes",
                "note": "NameError no longer blocks seal (SEALED_VALID on A/B/C). Collector sessions_collected=0 still yields SESSION_ARTIFACT_INCOMPLETE on some stages.",
            },
            "F_capture_events_0": {
                "class": "live_capture_sidecar_display",
                "not": "paper_push_or_replay_ingest",
            },
            "G_teardown_residual": {
                "official_leftover_processes_end": cert.get("leftover_processes_end"),
                "orchestrator_kill_not_used_as_pass": True,
            },
        },
        "stages": {
            "FULL_DAY": {
                "checked_bat_exit": 2,
                "daily_verdict": "pm_failed",
                "am": "success morning_session_close VALID_SESSION gates=2533 ticks=15432 fills=0 exits=0",
                "pm": "failed kabu_station_connection",
            },
            "PM_DIRECT": {
                "checked_bat_exit": 2,
                "session": "live_session_005737",
                "stop_reason": "afternoon_session_close",
                "session_validity": "INVALID_NO_GATE",
                "gate_evaluations": 0,
                "seal": "SEALED_VALID",
            },
            "WINDOW_A": {
                "checked_bat_exit": 2,
                "session": "live_session_011250",
                "stop_reason": "session_clock_stop",
                "timeout_124": False,
                "session_validity": "INVALID_NO_GATE",
                "push_messages": 24433,
                "gate_evaluations": 0,
                "seal": "SEALED_VALID",
            },
            "WINDOW_B": {
                "checked_bat_exit": 2,
                "session": "live_session_014942",
                "stop_reason": "morning_session_close",
                "session_validity": "VALID_SESSION",
                "push_messages": 46,
                "gate_evaluations": 14,
                "seal": "SEALED_VALID",
            },
            "WINDOW_C": {
                "checked_bat_exit": 0,
                "result": "OK",
                "session": "live_session_025844",
                "stop_reason": "afternoon_session_close",
                "session_validity": "VALID_SESSION",
                "push_messages": 20581,
                "gate_evaluations": 3262,
                "canonical_trade_count": 0,
                "seal": "SEALED_VALID",
            },
        },
        "entry_exit": {
            "full_day_am_v1r_fill": 0,
            "full_day_am_v1r_exit": 0,
            "full_e2e_entry_exit_slot": False,
            "c3_aborted_am_had_v1r_fill": 4,
        },
        "discord": {
            "sink_posts_official": cert.get("discord_sink_posts"),
            "nameerror_on_session_end": "NOT_RECURRED",
        },
        "known_bugs": {
            "pilot_runner_NameError": "NOT_RECURRED",
            "canonical_summary_mismatch": "no_executed_v1r_exit_to_compare",
            "48x_WAITING_MARKET": "NOT_RECURRED_on_C4_FULL_DAY_AM",
            "clock_overrun_timeout_124": 0,
            "v24_circular": "NOT_RECURRED",
            "v25_dead_owner": "NOT_RECURRED",
            "4001007": "NOT_RECURRED",
            "WinError5": "NOT_RECURRED",
            "submit_cancel_live": "0/0/0",
        },
        "safety": {
            "paper_only": True,
            "live_orders": False,
            "v25_untouched": True,
            "candidate1_untouched": True,
        },
        "freeze_eligibility": {
            "V26_FREEZE_ELIGIBLE": False,
            "reason": "Candidate-4 Full Runtime Preflight failed official stages; no Formal freeze",
        },
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md = f"""# V26-G3 Runtime Defect Fix + Candidate Preflight

**Verdict:** `{VERDICT}`

`V26_FREEZE_ELIGIBLE=false`. Formal V26 freeze was **not** performed. Formal Paper is **not** allowed. V25 selector/manifest were **not** changed. Candidate-1 bytes were **not** rewritten.

submit/cancel/live = `0/0/0`. timeout 124 = `0`.

## Candidate-1 (permanent FAILED)

| Field | Value |
|---|---|
| id | `V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1` |
| sha | `{c1.get("sha256")}` |
| status | FAILED / AUDIT_ONLY |
| immutable | true |
| reused / promoted | false |

G2 Full Runtime Preflight fail: `NameError: log is not defined`, canonical_summary.trade_count=0 vs 9 EXIT_EXECUTED, 48x FULL_DAY WAITING_MARKET, Window A/B timeout 124.

## Snapshots this phase (none rewritten)

Premature Candidate-2 `V26G3_2` (`{c2.get("sha256")[:12]}…`) was snapshotted before empirical 48x PASS. FULL_DAY AM `live_session_231945` still `INVALID_NO_GATE`. Bytes not rewritten.

`V26G3_3` (`{c3.get("sha256")[:12]}…`) added replay lag + clock reanchor. AM reached `VALID_SESSION`, gates, V1R FILL=4, then Paper `session_end` from torn ARM_FILE + stale ENV_T0. Bytes not rewritten.

Preflight identity: **`V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4`** sha `{c4.get("sha256")}` inventory_n=**{len(c4.get("runtime_file_sha256") or {})}** (generator, not hardcoded 51). UNCERTIFIED, `formal_paper_allowed=false`, immutable=true.

Official path: `run_paper_full_day_certification.py` → `run_paper_trade_checked.bat --full-day-cert --no-pause`, input `20260812`, real KabuS, selector `active_v1r_candidate_v26g3_4.json`.

Official cert verdict: `{cert.get("verdict")}`. failed_tests={json.dumps(cert.get("failed_tests"))}. leftover_processes_end={cert.get("leftover_processes_end")}.

## Fixes

**A — NameError.** Module logger `log = logging.getLogger(__name__)`. Session_end / archive / seal no longer crash. C4 seals are `SEALED_VALID`. Discord session_end was not skipped.

**B — canonical_summary.** When live primary is on, SoT is primary `EXIT_EXECUTED` only (not observer/shadow/PBv2). C4 FULL_DAY AM had **0** primary exits, so trade_count=0 is consistent with that AM (parity unproven on a filled day). Unit tests PASS.

**C — 48x WAITING_MARKET.** Certification replay clock/parity, not Paper ENTRY rewrite. Delayed arm, warmup `session_now()`, dual tick on throttled path, Ingress reanchor + publish-lag wait, atomic ARM_FILE T0. C4 FULL_DAY AM: `VALID_SESSION`, gate_evaluations=2533, dual ticks=15432, `RUNNING`, `morning_session_close`. Residual: Window A 1.0x still `INVALID_NO_GATE` / WAITING_MARKET when `TRADEBOT_SESSION_CLOCK_STOP` hits before the 09:03 tape is consumed.

**D — clock STOP.** `session_clock_stop_reached` → graceful Paper exit. Window A stop_reason=`session_clock_stop`, checked-BAT exit=2 (artifact collector), **not** 124. `now_jst()` is not frozen at STOP.

**E — SESSION_ARTIFACT_INCOMPLETE.** After A/D, seals exist. Collector `sessions_collected=0` can still mark incomplete. Not treated as the NameError itself.

**F — capture events: 0.** Live capture sidecar counter, not Paper PUSH / cert replay ingest.

**G — teardown.** Official `leftover_processes_end=[]`. Orchestrator kill is not PASS evidence. Window C runtime exit=0 / result OK.

## Targeted regression

162 pytest passed (`failed_tests=[]`), including G2 108-set plus G3 NameError / canonical / clock / arm-file tests.

## Full Runtime Preflight (C4)

| Stage | checked-BAT | notes |
|---|---|---|
| FULL_DAY | 2 | AM success VALID_SESSION; PM `kabu_station_connection` fail |
| PM_DIRECT | 2 | afternoon_session_close, INVALID_NO_GATE, seal SEALED_VALID |
| Window A | 2 | `session_clock_stop`, no 124, gate=0, WAITING_MARKET |
| Window B | 2 | VALID_SESSION, gates=14, push=46, morning_session_close |
| Window C | **0 / OK** | VALID_SESSION, push=20581, gates=3262, afternoon_session_close |

FULL_DAY E2E ENTRY→EXIT→slot was **not** met (AM primary fills=0 / exits=0; PM did not run). Discord sink posts (official)={cert.get("discord_sink_posts")}.

## Known bugs

NameError NOT_RECURRED. 48x WAITING_MARKET NOT_RECURRED on C4 FULL_DAY AM. timeout 124=0. V24 circular / V25 dead owner / 4001007 / WinError5 NOT_RECURRED. submit/cancel/live=0/0/0.

## Stop

Do not Formal-freeze V26. Do not allow Formal Paper. Do not rewrite Candidate-1 or V26G3_2/3/4 manifests. A later fix needs a **new** candidate identity after production change + regressions + rehash.
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")

    wb = Workbook()
    sheets = {
        "Candidate1_Failure": [
            ("candidate_id", c1.get("candidate_id")),
            ("sha256", c1.get("sha256")),
            ("status", "FAILED"),
            ("audit_only", True),
            ("immutable", True),
            ("bytes_rewritten", False),
            ("g2_verdict", "V1R_V26G2_CANDIDATE_FULL_RUNTIME_PREFLIGHT_FAIL"),
        ],
        "Fixes": [
            ("A", "pilot_runner module logger"),
            ("B", "canonical_summary V1R EXIT_EXECUTED SoT"),
            ("C", "replay clock reanchor + lag wait + delayed arm"),
            ("D", "session_clock_stop graceful exit"),
            ("arm_file", "atomic write; no ENV_T0 fallback when ARM_FILE set"),
        ],
        "NameError": [
            ("recurrence", "NOT_RECURRED"),
            ("seal", "SEALED_VALID on A/B/C"),
        ],
        "Summary_Parity": [
            ("sot", "v1r_primary_exit_executed when live primary"),
            ("c4_am_trade_count", 0),
            ("c4_am_primary_exits", 0),
        ],
        "Replay_Speed_Parity": [
            ("unit_1_4_12_48_reanchor", "PASS"),
            ("c4_full_day_am_validity", "VALID_SESSION"),
            ("window_a_1x_stop_before_gate", "FAIL_residual"),
        ],
        "Clock_Stop": [
            ("window_a_stop_reason", "session_clock_stop"),
            ("timeout_124", 0),
            ("now_jst_frozen", False),
        ],
        "Session_Artifacts": [
            ("nameerror_block_seal", False),
            ("collector_incomplete_still_seen", True),
        ],
        "Teardown": [
            ("leftover_processes_end", str(cert.get("leftover_processes_end"))),
            ("orchestrator_kill_as_pass", False),
        ],
        "Targeted_Regression": [
            ("pytest_n", 162),
            ("failed_tests", "[]"),
        ],
        "Candidate2_Identity": [
            ("preflight_id", c4.get("candidate_id")),
            ("preflight_sha", c4.get("sha256")),
            ("v26g3_2_sha", c2.get("sha256")),
            ("v26g3_3_sha", c3.get("sha256")),
            ("status", "UNCERTIFIED"),
            ("formal_paper_allowed", False),
        ],
        "Inventory": [
            ("generated_count", len(c4.get("runtime_file_sha256") or {})),
            ("runtime_critical_uncovered", "[]"),
            ("digest", c4.get("runtime_inventory_digest")),
        ],
        "Full_Day": [
            ("exit_code", 2),
            ("am", "VALID_SESSION gates=2533 ticks=15432"),
            ("pm", "kabu_station_connection"),
        ],
        "PM_Direct": [
            ("exit_code", 2),
            ("validity", "INVALID_NO_GATE"),
            ("seal", "SEALED_VALID"),
        ],
        "Window_A": [
            ("exit_code", 2),
            ("stop", "session_clock_stop"),
            ("timeout_124", False),
            ("gates", 0),
        ],
        "Window_B": [
            ("exit_code", 2),
            ("validity", "VALID_SESSION"),
            ("gates", 14),
            ("push", 46),
        ],
        "Window_C": [
            ("exit_code", 0),
            ("result", "OK"),
            ("validity", "VALID_SESSION"),
            ("gates", 3262),
            ("push", 20581),
        ],
        "Entry_Exit": [
            ("full_day_am_fill", 0),
            ("full_day_am_exit", 0),
            ("e2e", False),
        ],
        "Discord": [
            ("nameerror", "NOT_RECURRED"),
            ("sink_posts", cert.get("discord_sink_posts")),
        ],
        "AM_PM": [
            ("full_day_am", "success"),
            ("full_day_pm", "failed_kabu_station_connection"),
        ],
        "Restart": [
            ("pm_direct_after_full_day", "started"),
            ("paper_primary_ready", "YES"),
        ],
        "Known_Bugs": [
            ("NameError", "NOT_RECURRED"),
            ("48x_WAITING_MARKET_full_day_am", "NOT_RECURRED"),
            ("timeout_124", 0),
            ("submit_cancel_live", "0/0/0"),
        ],
        "Safety": [
            ("paper_only", True),
            ("v25_untouched", True),
            ("candidate1_untouched", True),
        ],
        "Freeze_Eligibility": [
            ("V26_FREEZE_ELIGIBLE", False),
            ("formal_freeze", False),
        ],
    }
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        ws.append(["key", "value"])
        for k, v in rows:
            ws.append([k, v if not isinstance(v, (dict, list)) else json.dumps(v)])
    wb.save(OUT / "audit.xlsx")
    print("WROTE", OUT / "report.json")
    print("WROTE", OUT / "report.md")
    print("WROTE", OUT / "audit.xlsx")
    print("VERDICT", VERDICT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
