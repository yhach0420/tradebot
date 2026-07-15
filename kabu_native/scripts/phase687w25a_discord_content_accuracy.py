"""Phase687W25A — Discord content accuracy artifacts + split test counts."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[1]
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w25a_discord_content_accuracy"

DEDICATED = [
    "tests/test_phase687w25a_discord_content_accuracy.py",
]
RELATED = [
    "tests/test_phase687w25_discord_notification_refresh.py",
    "tests/test_phase316_exit_discord_100share_yen_notification.py",
    "tests/test_phase433_discord_symbol_name_exit_time.py",
    "tests/test_discord_cap_blocked_notify.py",
]


def _run_pytest(paths: list[str]) -> dict:
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{NATIVE_ROOT.parent}"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q", "--tb=line"],
        cwd=str(NATIVE_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    # pytest -q summary like "9 passed in 1.2s" or "2 failed, 7 passed"
    passed = failed = 0
    import re

    m = re.search(r"(\d+)\s+passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", out)
    if m:
        failed = int(m.group(1))
    return {
        "exit_code": proc.returncode,
        "passed": passed,
        "failed": failed,
        "stdout_tail": out[-1500:],
        "paths": paths,
    }


def main() -> int:
    sys.path[:0] = [str(NATIVE_ROOT / "src"), str(NATIVE_ROOT.parent)]
    from notify.discord_notification_formatter import format_communication_degraded
    from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier
    from small_paper.discord_message_builder import build_exit_detail

    REPORT.mkdir(parents=True, exist_ok=True)

    # Previews
    discord_txt = format_communication_degraded(
        {"target": "Discord webhook", "status": "SEND_FAILED", "last_push_age_sec": "N/A"}
    )
    kabu_txt = format_communication_degraded(
        {
            "target": "Kabu PUSH",
            "status": "DEGRADED_NO_PUSH",
            "last_push_age_sec": 45.0,
            "reconnect": "1回目",
        }
    )
    fanout_txt = format_communication_degraded(
        {"target": "Capture fan-out", "status": "INGEST_DOWN"}
    )
    (REPORT / "communication_preview.txt").write_text(
        "\n\n----\n\n".join([discord_txt, kabu_txt, fanout_txt]) + "\n",
        encoding="utf-8",
    )

    high = trailing_params_for_board_tier(80.0)
    low = trailing_params_for_board_tier(10.0)
    exit_high = build_exit_detail(
        symbol="4174.T",
        entry_price=925.0,
        exit_price=940.0,
        pnl_pct=1.62,
        mfe_pct=2.0,
        mae_pct=-0.2,
        hold_minutes=12.0,
        exit_reason="trailing_mfe_exit",
        pnl_yen_100=1500.0,
        board_dynamic_trailing_tier=high[2],
        board_dynamic_trailing_activate_pct=high[0],
        board_dynamic_trailing_giveback_frac=high[1],
        exit_time="2026-07-14T10:21:59+09:00",
    )
    exit_low = build_exit_detail(
        symbol="7203.T",
        entry_price=2800.0,
        exit_price=2815.0,
        pnl_pct=0.54,
        mfe_pct=0.9,
        mae_pct=-0.1,
        hold_minutes=9.0,
        exit_reason="trailing_mfe_exit",
        board_dynamic_trailing_tier=low[2],
        board_dynamic_trailing_activate_pct=low[0],
        board_dynamic_trailing_giveback_frac=low[1],
        exit_time="2026-07-14T11:00:00+09:00",
    )
    (REPORT / "trailing_exit_preview.txt").write_text(
        "=== board_high (runtime) ===\n"
        + exit_high
        + "\n\n=== board_low (runtime) ===\n"
        + exit_low
        + "\n",
        encoding="utf-8",
    )

    dedicated = _run_pytest(DEDICATED)
    related = _run_pytest(RELATED)
    total_passed = dedicated["passed"] + related["passed"]
    total_failed = dedicated["failed"] + related["failed"]
    total_run = total_passed + total_failed

    obsolete = False
    for p in (REPORT / "communication_preview.txt", REPORT / "trailing_exit_preview.txt"):
        t = p.read_text(encoding="utf-8")
        if "Discord webhook" in t and "ENTRY評価: 一時停止" in t.split("Discord webhook")[1].split("----")[0]:
            obsolete = True
        if "mid (activate 0.60%" in t or "giveback 35%" in t:
            obsolete = True

    verdict = (
        "NOTIFICATION_RUNTIME_VALUE_MISMATCH"
        if obsolete or dedicated["exit_code"] != 0 or related["exit_code"] != 0
        else "DISCORD_CONTENT_ACCURACY_FIXED"
    )

    report = {
        "phase": "687W25A",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "test_counts": {
            "dedicated_paths": DEDICATED,
            "dedicated_passed": dedicated["passed"],
            "dedicated_failed": dedicated["failed"],
            "related_paths": RELATED,
            "related_passed": related["passed"],
            "related_failed": related["failed"],
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_run": total_run,
        },
        "dedicated_result": dedicated,
        "related_result": related,
        "fixes": [
            "Discord webhook degraded → ENTRY評価: 継続 / Paper影響: NONE",
            "Kabu PUSH degraded → ENTRY評価: 一時停止",
            "Capture fan-out degraded → ENTRY評価: 継続",
            "trailing EXIT displays runtime board_high/board_low activate/giveback only",
            "removed mid/0.60%/35% legacy preview values",
        ],
        "notification_count_changed": False,
        "send_conditions_changed": False,
        "actual_submit_cancel": 0,
    }
    (REPORT / "phase687w25a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT / "regression_test_results.json").write_text(
        json.dumps(
            {
                "dedicated": dedicated,
                "related": related,
                "counts": report["test_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (REPORT / "code_change_manifest.json").write_text(
        json.dumps(
            {
                "files_changed": [
                    "src/notify/discord_notification_formatter.py",
                    "src/small_paper/discord_message_builder.py",
                    "tests/test_phase687w25a_discord_content_accuracy.py",
                    "scripts/phase687w25a_discord_content_accuracy.py",
                ],
                "send_conditions_unchanged": True,
                "notification_events_unchanged": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    decision = f"""# Phase687W25A Decision

## Verdict: `{verdict}`

### Fixes
1. Discord webhook障害 → ENTRY評価: **継続** / Paper本体への影響: **NONE**
2. Kabu PUSH障害 → ENTRY評価: **一時停止**
3. Capture fan-out障害 → ENTRY評価: **継続**
4. trailing_mfe EXIT → runtime の board tier / activation / giveback のみ表示（mid/0.60%/35% を排除）

### Test counts
| bucket | passed | failed | paths |
|--------|--------|--------|-------|
| W25A dedicated | {dedicated['passed']} | {dedicated['failed']} | {len(DEDICATED)} file(s) |
| related | {related['passed']} | {related['failed']} | {len(RELATED)} file(s) |
| **total** | **{total_passed}** | **{total_failed}** | **{total_run} run** |

Do not report inflated combined counts from unrelated suites.

### Constraints
- 通知数変更なし
- 送信条件変更なし
- submit/cancel=0
"""
    (REPORT / "phase687w25a_decision.md").write_text(decision, encoding="utf-8")
    print(decision)
    print(f"verdict={verdict}")
    print(
        f"counts dedicated={dedicated['passed']}/{dedicated['failed']} "
        f"related={related['passed']}/{related['failed']} "
        f"total_passed={total_passed} total_run={total_run}"
    )
    return 0 if verdict == "DISCORD_CONTENT_ACCURACY_FIXED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
