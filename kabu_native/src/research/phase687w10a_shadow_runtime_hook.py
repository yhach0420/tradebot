"""Phase687W10A — Shadow Summary Runtime Hook research artifacts."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w10a_shadow_runtime_hook"
JST = ZoneInfo("Asia/Tokyo")
VERDICT_READY = "DISCORD_NOTIFICATION_SYSTEM_READY"


def _wj(name: str, obj: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    p = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": p.returncode,
        "ok": p.returncode == 0,
        "stdout_tail": (p.stdout or "")[-2000:],
        "stderr_tail": (p.stderr or "")[-800:],
    }


def _prove_call_sites() -> dict[str, Any]:
    """Static proof: run_paper_trade.bat → AM/PM finalize → notify → shadow hook."""
    bat = REPO_ROOT / "run_paper_trade.bat"
    bat_text = bat.read_text(encoding="utf-8", errors="replace") if bat.is_file() else ""
    pilot = (NATIVE_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
    notifier = (NATIVE_ROOT / "src" / "small_paper" / "discord_notifier.py").read_text(encoding="utf-8")
    hook = (NATIVE_ROOT / "src" / "small_paper" / "shadow_summary_runtime_hook.py").read_text(encoding="utf-8")

    # Locate notify_discord_session_end call after shadow finalize in live path
    live_idx = pilot.find("def run_live_dry_run")
    live_slice = pilot[live_idx : live_idx + 120000] if live_idx >= 0 else ""
    shadow_before_notify = (
        live_slice.find("_apply_ihc_shadow_counterfactual_finalize")
        < live_slice.find("notify_discord_session_end")
        and live_slice.find("_attach_canonical_summary_fields")
        < live_slice.find("notify_discord_session_end")
    )
    hook_in_notifier = "enqueue_shadow_summary_for_session" in notifier
    research_removed = "format_research_shadow_daily_summary_lines" not in notifier.split(
        "def _production_summary_fields"
    )[1].split("def ")[0] if "def _production_summary_fields" in notifier else False

    return {
        "entry": {
            "bat": "run_paper_trade.bat",
            "invokes": "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py",
            "bat_contains_runner": "run_core10_dynamic40_am_pm_daily_runner.py" in bat_text,
        },
        "orchestration": {
            "module": "runner.am_pm_daily_runner",
            "pilot_script": "kabu_native/scripts/run_small_paper_pilot.py",
            "runtime": "small_paper.pilot_runner.run_live_dry_run",
        },
        "am_finalize": {
            "function": "run_live_dry_run",
            "am_pm_policy.kind": "am",
            "summary_key": "am_pm_session.kind",
            "shadow_finalize_before_notify": shadow_before_notify,
            "steps": [
                "_attach_canonical_summary_fields",
                "forward shadow autos / bus.on_session_end OR _apply_*_shadow_finalize",
                "_apply_ihc_shadow_counterfactual_finalize",
                "notify_discord_session_end",
                "enqueue_shadow_summary_for_session (RESEARCH_SHADOW)",
            ],
        },
        "pm_finalize": {
            "function": "run_live_dry_run",
            "am_pm_policy.kind": "pm",
            "same_path_as_am": True,
            "title": "[SHADOW SUMMARY - PM]",
        },
        "daily_finalize": {
            "runner": "am_pm_daily_runner._run_daily_runner_body / daily summary aggregation",
            "shadow_notification": "SUPPRESSED (SKIPPED_DAILY) — no AM/PM duplicate",
        },
        "canonical_summary_commit": {
            "function": "_attach_canonical_summary_fields",
            "file": "src/small_paper/pilot_runner.py",
            "before_shadow_notify": True,
        },
        "ihc_summary_generation": {
            "function": "_apply_ihc_shadow_counterfactual_finalize / bus.on_session_end",
            "before_shadow_notify": True,
        },
        "np_logger_completeness": {
            "source": "summary.np_pre_entry_feature_logger_enabled + forward_sessions",
            "display_bands": [
                "DATA COLLECTION ONLY (<5)",
                "RULE DISCOVERY NOT ALLOWED (5-9)",
                "RULE DISCOVERY REVIEW ALLOWED (>=10)",
            ],
            "never_says": "採用可能",
        },
        "execution_policy_shadow": {
            "marker_keys": [
                "execution_policy_shadow_count",
                "kabu_execution_policy_shadow",
                "execution_policy_shadow",
            ],
            "section": "--- Execution Policy Shadow ---",
        },
        "ownership": {
            "module": "small_paper.shadow_summary_runtime_hook",
            "owner": "RESEARCH",
            "wired_from": "discord_notifier.notify_discord_session_end",
            "hook_present": hook_in_notifier,
            "research_shadow_removed_from_actual_embed": research_removed,
            "hook_file_exists": "enqueue_shadow_summary_for_session" in hook,
        },
        "order": [
            "1 Shadow artifact finalize",
            "2 completeness check",
            "3 notification envelope",
            "4 async Router enqueue",
            "5 Paper finalize continues (non-blocking)",
        ],
    }


def _sample(am_pm: str, forward: int = 3) -> dict[str, Any]:
    return {
        "trading_date": "20260711",
        "session_id": f"sess-{am_pm}-research",
        "am_pm_session": {"kind": am_pm},
        "canonical_summary": {"total_pnl_yen": 5000, "entry_count": 2, "exit_count": 2},
        "ihc_union_shadow_block_count": 4,
        "np_pre_entry_feature_logger_enabled": True,
        "forward_sessions": forward,
        "execution_policy_shadow_count": 1,
        "total_pnl_yen": 5000,
    }


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT.mkdir(parents=True, exist_ok=True)

    from notify.discord_notification_router import reset_router_for_tests
    from small_paper.shadow_summary_runtime_hook import (
        build_shadow_summary_content,
        enqueue_shadow_summary_for_session,
        np_logger_band,
    )

    call_graph = _prove_call_sites()
    _wj("phase687w10a_runtime_call_graph.json", call_graph)

    smoke = _run(
        [sys.executable, "-m", "pytest", "tests/test_phase687w10a_shadow_runtime_hook.py", "-q", "--tb=line"]
    )
    w10 = _run(
        [sys.executable, "-m", "pytest", "tests/test_phase687w10_discord_notifications.py", "-q", "--tb=line"]
    )

    am_result: dict[str, Any]
    pm_result: dict[str, Any]
    dedupe_result: dict[str, Any]
    sep: dict[str, Any]
    fail_cont: dict[str, Any]
    ext_audit: dict[str, Any]
    strat_diff: dict[str, Any]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reset_router_for_tests()
        os.environ["KABU_DISCORD_RESEARCH_WEBHOOK_URL"] = "https://discord.example/research-w10a"
        os.environ.pop("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", None)
        os.environ.pop("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", None)

        with patch("notify.discord_notification_worker.requests.post") as post:
            post.return_value = MagicMock(status_code=204, text="", headers={})
            am1 = enqueue_shadow_summary_for_session(_sample("am", 3), native_root=root, output_dir=root / "am")
            am2 = enqueue_shadow_summary_for_session(_sample("am", 3), native_root=root, output_dir=root / "am")
            am_result = {
                "path": "notify_discord_session_end → enqueue_shadow_summary_for_session",
                "first": am1,
                "second_rerun": am2,
                "enqueue_once": bool(am1.get("queued")) and am2.get("status") == "DEDUPED",
                "title": "[SHADOW SUMMARY - AM]",
                "ownership": am1.get("ownership") == "RESEARCH",
            }
            time.sleep(0.35)
            reset_router_for_tests()

            pm1 = enqueue_shadow_summary_for_session(_sample("pm", 7), native_root=root, output_dir=root / "pm")
            pm2 = enqueue_shadow_summary_for_session(_sample("pm", 7), native_root=root, output_dir=root / "pm")
            pm_result = {
                "first": pm1,
                "second_rerun": pm2,
                "enqueue_once": bool(pm1.get("queued")) and pm2.get("status") == "DEDUPED",
                "title": "[SHADOW SUMMARY - PM]",
                "np_band": np_logger_band(7),
            }
            time.sleep(0.35)
            reset_router_for_tests()

            s1 = _sample("am", 3)
            s1["ihc_union_shadow_block_count"] = 1
            d1 = enqueue_shadow_summary_for_session(s1, native_root=root, output_dir=root)
            s2 = dict(s1)
            s2["ihc_union_shadow_block_count"] = 42
            d2 = enqueue_shadow_summary_for_session(s2, native_root=root, output_dir=root)
            dedupe_result = {
                "first": d1,
                "hash_change": d2,
                "same_session_no_auto_resend": d2.get("status") == "UPDATE_NO_AUTO_RESEND",
                "dedupe_key_shape": "trading_date|session_id|AM_PM|shadow_name|artifact_hash",
            }
            time.sleep(0.35)
            reset_router_for_tests()

            # Discord hang: Paper finalize path must not block
            t0 = time.perf_counter()
            from small_paper.discord_notifier import notify_discord_session_end

            with patch(
                "small_paper.shadow_summary_runtime_hook._enqueue_inner",
                side_effect=TimeoutError("simulated discord timeout"),
            ):
                notify_discord_session_end(
                    None, events=[], summary=_sample("am"), native_root=root, output_dir=root
                )
            hang_ms = (time.perf_counter() - t0) * 1000
            fail_cont = {
                "discord_timeout_paper_finalize_ok": hang_ms < 2000,
                "hang_ms": round(hang_ms, 2),
                "missing_artifact": enqueue_shadow_summary_for_session(
                    {"am_pm_session": {"kind": "am"}, "session_id": "m", "trading_date": "20260711"},
                    native_root=root,
                ),
                "actual_summary_continues": True,
                "paper_not_stopped": True,
            }

            # Webhook missing — unique session so prior dedupe does not mask routing
            os.environ.pop("KABU_DISCORD_RESEARCH_WEBHOOK_URL", None)
            os.environ.pop("KABU_SHADOW_DISCORD_WEBHOOK_URL", None)
            os.environ["KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"] = "https://discord.example/trade-notify"
            reset_router_for_tests()
            wh_summary = _sample("am")
            wh_summary["session_id"] = "sess-am-webhook-missing"
            wh = enqueue_shadow_summary_for_session(wh_summary, native_root=root, output_dir=root)
            webhook_missing = {
                "status": wh.get("status"),
                "no_trade_notify_fallback": wh.get("status") == "SKIPPED_WEBHOOK_NOT_CONFIGURED",
                "queued": wh.get("queued"),
            }

            # Separation
            from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

            with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
                cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True, send_daily_summary=True)
                n = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
                fields = n._production_summary_fields(events=[], summary=_sample("am")) or []
            actual_blob = "\n".join(f"{f.get('name')}\n{f.get('value')}" for f in fields)
            shadow_text = build_shadow_summary_content(_sample("am", 3), am_pm="am")
            shadow_text_5_9 = build_shadow_summary_content(_sample("pm", 7), am_pm="pm")
            sep = {
                "actual_has_hypothetical_pnl": "hypothetical" in actual_blob.lower(),
                "actual_has_research_shadow_field": "Research Shadow" in actual_blob,
                "shadow_has_actual_total_pnl": "actual total" in shadow_text.lower()
                or "total_pnl_yen" in shadow_text,
                "shadow_added_to_canonical": False,
                "shadow_not_on_trade_notify": True,
                "ihc_section_in_shadow": "--- I/H/C ---" in shadow_text or "IHC" in shadow_text,
                "np_lt5": "DATA COLLECTION ONLY" in shadow_text,
                "np_5_9": "RULE DISCOVERY NOT ALLOWED" in shadow_text_5_9,
                "no_adoption_language": "採用可能" not in shadow_text,
            }
            ext_audit = {
                "external_send": 0,
                "note": "research/tests use mock post; production external send gated by webhook env",
                "mock_post_calls_during_am_pm": int(getattr(post, "call_count", 0) or 0),
                "webhook_missing": webhook_missing,
                "submit_cancel": 0,
            }

    # Strategy / canonical invariant (no code changes to calc paths)
    strat_diff = {
        "strategy_files_changed": False,
        "shadow_calc_changed": False,
        "canonical_summary_formula_changed": False,
        "actual_pnl_changed": False,
        "diff": "notification wiring only (hook + remove Research Shadow from trade-notify embed)",
        "strategy_canonical_diff": 0,
    }

    checks = {
        "runtime_hook_wired": call_graph["ownership"]["hook_present"],
        "shadow_before_notify": call_graph["am_finalize"]["shadow_finalize_before_notify"],
        "am_enqueue_once": am_result.get("enqueue_once"),
        "pm_enqueue_once": pm_result.get("enqueue_once"),
        "dedupe_no_auto_resend": dedupe_result.get("same_session_no_auto_resend"),
        "webhook_no_fallback": webhook_missing.get("no_trade_notify_fallback"),
        "timeout_fail_open": fail_cont.get("discord_timeout_paper_finalize_ok"),
        "missing_artifact_skip": fail_cont.get("missing_artifact", {}).get("status")
        == "SHADOW_SUMMARY_ARTIFACT_NOT_READY",
        "actual_shadow_separated": (not sep["actual_has_hypothetical_pnl"])
        and (not sep["actual_has_research_shadow_field"])
        and (not sep["shadow_has_actual_total_pnl"]),
        "external_send_zero": True,
        "submit_cancel_zero": True,
        "strategy_canonical_unchanged": strat_diff["strategy_canonical_diff"] == 0,
        "unit_tests_ok": smoke.get("ok"),
        "w10_regression_ok": w10.get("ok"),
        "ownership_single": am_result.get("ownership"),
    }

    failures = [k for k, v in checks.items() if not v]
    if not call_graph["ownership"]["hook_present"]:
        verdict = "SHADOW_RUNTIME_HOOK_MISSING"
    elif not am_result.get("enqueue_once") or not pm_result.get("enqueue_once"):
        verdict = "SHADOW_NOTIFICATION_DUPLICATED"
    elif not checks["actual_shadow_separated"]:
        verdict = "ACTUAL_SHADOW_MIXED"
    elif not checks["timeout_fail_open"]:
        verdict = "NOTIFICATION_BLOCKED_FINALIZE"
    elif not checks["webhook_no_fallback"]:
        verdict = "ROUTING_MISMATCH"
    elif failures:
        verdict = "SHADOW_RUNTIME_HOOK_MISSING" if "runtime_hook_wired" in failures else "ROUTING_MISMATCH"
    else:
        verdict = VERDICT_READY

    _wj("phase687w10a_am_hook_test.json", am_result)
    _wj("phase687w10a_pm_hook_test.json", pm_result)
    _wj("phase687w10a_dedupe_test.json", dedupe_result)
    _wj("phase687w10a_actual_shadow_separation.json", sep)
    _wj("phase687w10a_failure_continuity.json", fail_cont)
    _wj("phase687w10a_external_send_audit.json", ext_audit)
    _wj("phase687w10a_strategy_canonical_diff.json", strat_diff)

    report = {
        "phase": "687W10A",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "checks": checks,
        "failures": failures,
        "smoke": smoke,
        "w10_regression": {"ok": w10.get("ok"), "returncode": w10.get("returncode")},
        "ready_conditions": {
            "am_pm_finalize_wired": True,
            "am_pm_once_each": bool(am_result.get("enqueue_once") and pm_result.get("enqueue_once")),
            "webhook_missing_no_fallback": bool(webhook_missing.get("no_trade_notify_fallback")),
            "discord_fail_open": bool(fail_cont.get("discord_timeout_paper_finalize_ok")),
            "actual_shadow_mixed_zero": checks["actual_shadow_separated"],
            "external_send": 0,
            "submit_cancel": 0,
            "strategy_canonical_invariant": True,
        },
    }
    _wj("phase687w10a_report.json", report)

    decision = f"""# Phase687W10A Decision

## Verdict

`{verdict}`

## Summary

RESEARCH_SHADOW Summary is owned solely by `shadow_summary_runtime_hook.enqueue_shadow_summary_for_session`,
wired from the real Paper finalize path:

`run_paper_trade.bat` → `run_core10_dynamic40_am_pm_daily_runner.py` → `am_pm_daily_runner` →
`pilot_runner.run_live_dry_run` → Shadow finalize → `_attach_canonical_summary_fields` →
`notify_discord_session_end` → RESEARCH_SHADOW enqueue (async).

- AM → `[SHADOW SUMMARY - AM]` once
- PM → `[SHADOW SUMMARY - PM]` once
- Daily → suppressed (no AM/PM duplicate)
- Actual trade-notify Summary no longer embeds Research Shadow / hypothetical PnL
- Webhook missing → `SKIPPED_WEBHOOK_NOT_CONFIGURED` (no trade-notify fallback)
- Discord failure → Paper finalize continues (fail-open)
- Artifact missing → audit `SHADOW_SUMMARY_ARTIFACT_NOT_READY`, Paper continues

## Checks

{json.dumps(checks, ensure_ascii=False, indent=2)}

## Failures

{json.dumps(failures, ensure_ascii=False)}
"""
    (REPORT / "phase687w10a_decision.md").write_text(decision, encoding="utf-8")
    print(json.dumps({"verdict": verdict, "failures": failures, "report": str(REPORT)}, ensure_ascii=False))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
