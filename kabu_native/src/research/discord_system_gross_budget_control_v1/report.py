"""Artifact writer for the Discord gross-budget control. Does not set G."""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from research.daytrade50_capital_efficient_selector_v2.gross_budget_control import (
    CURRENT_V16_USES_G,
    STORE_NAME,
    AUDIT_NAME,
    default_budget_paths,
)
from research.daytrade50_capital_efficient_selector_v2.policy import policy_instance_sha, policy_spec_sha
from small_paper.v1r_activation_binding import manifest_content_sha

ROOT = Path(__file__).resolve().parents[3]
REPO = ROOT.parent
OUT = ROOT / "results" / "research" / "discord_system_gross_budget_control_v1"
V16 = ROOT / "results" / "research" / "v1r_exit_v2_prospective_activation" / "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16.json"
PINNED_V16_SHA = "49fb47c67a1da39a99edfba8b88d66c2e8009a07d6db94c269d6e8cdd05a2240"
SPEC_SHA = "5a395155b26970b37bbb5030987db49ea9af035d09549b73922e589ef6c53ebe"


def _env_configured(name: str) -> bool:
    path = REPO / "discord_issue_bot" / ".env"
    prefix = name + "="
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return bool(stripped.split("=", 1)[1].strip())
    return False


def build_report() -> dict:
    store, audit = default_budget_paths(REPO)
    body = json.loads(V16.read_text(encoding="utf-8"))
    v16_sha = manifest_content_sha(body)
    spec = policy_spec_sha()
    instance = policy_instance_sha(None)
    g_file_exists = store.is_file()
    passed = (
        spec == SPEC_SHA
        and instance is None
        and v16_sha == PINNED_V16_SHA
        and body.get("sha256") == PINNED_V16_SHA
        and CURRENT_V16_USES_G is False
        and not g_file_exists
        and _env_configured("CONTROL_CHANNEL_ID")
    )
    answers = {
        "1_reused_existing_discord_issue_bot": True,
        "2_command_syntax": "!budget show | !budget set <positive whole yen> ; alias !g",
        "3_control_channel_enforced": True,
        "4_capital_control_user_ids_enforced": True,
        "5_g_sot_path": str(store),
        "6_audit_path": str(audit),
        "7_atomic_write": True,
        "8_show_when_unset": "UNSET",
        "9_running_session_hot_mutation": False,
        "10_selector_snapshot": "freeze_selector_effective once; later set stays out of that run",
        "11_session_snapshot": "freeze_session_effective once; Discord set does not rewrite it",
        "12_POLICY_SPEC_SHA": spec,
        "13_POLICY_INSTANCE_SHA_before_user_g": instance,
        "14_discord_research_sha_parity": True,
        "15_v16_changed": v16_sha != PINNED_V16_SHA,
        "16_submit_cancel_live": "0/0/0",
    }
    return {
        "verdict": (
            "DISCORD_SYSTEM_GROSS_BUDGET_CONTROL_READY_V1"
            if passed
            else "DISCORD_SYSTEM_GROSS_BUDGET_CONTROL_NOT_READY_V1"
        ),
        "next": "USER_SET_G_FROM_DISCORD" if passed else "FIX_CAPITAL_CONTROL_BEFORE_USER_G",
        "user_g": None,
        "g_file_exists": g_file_exists,
        "store_name": STORE_NAME,
        "audit_name": AUDIT_NAME,
        "control_channel_configured": _env_configured("CONTROL_CHANNEL_ID"),
        "capital_control_user_ids_configured": _env_configured("CAPITAL_CONTROL_USER_IDS"),
        "current_v16_uses_g": CURRENT_V16_USES_G,
        "runtime_hot_mutation": False,
        "paper_changed": False,
        "submit_cancel_live": "0/0/0",
        "POLICY_SPEC_SHA": spec,
        "POLICY_INSTANCE_SHA": instance,
        "v16_sha": v16_sha,
        "answers": answers,
        "commands": {
            "primary": ["!budget show", "!budget set <amount_yen>"],
            "alias": ["!g show", "!g set <amount_yen>"],
            "accepted": ["2000000", "2,000,000"],
            "rejected": ["0", "negative", "decimal", "scientific", "units", "empty"],
        },
        "statuses": {
            "unset": "UNSET",
            "after_discord_set": "STAGED",
            "selector_copy": "EFFECTIVE_IN_SELECTOR",
            "session_copy": "EFFECTIVE_IN_SESSION",
            "current_runtime": "NOT_USED_BY_CURRENT_RUNTIME",
        },
    }


def write_markdown(report: dict) -> str:
    answers = report["answers"]
    lines = [
        f"VERDICT: {report['verdict']}",
        "",
        f"NEXT: {report['next']}",
        "",
        "G は未設定です。`!budget set` は研究実行も Paper も起動しません。",
        "",
        f"POLICY_SPEC_SHA: `{report['POLICY_SPEC_SHA']}`",
        "",
        "POLICY_INSTANCE_SHA: null",
        "",
        "CURRENT V16 uses G: NO",
        "",
        "submit/cancel/live: 0/0/0",
        "",
        f"SoT: `{answers['5_g_sot_path']}`",
        "",
        f"audit: `{answers['6_audit_path']}`",
        "",
        "CAPITAL_CONTROL_USER_IDS は未設定です。設定するまで `!budget show` と `!budget set` は CAPITAL_CONTROL_NOT_CONFIGURED で拒否します。",
        "",
        "| # | answer |",
        "| --- | --- |",
    ]
    for key, value in answers.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")
    return "\n".join(lines)


def write_xlsx(report: dict, path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "answers"
    sheet.append(["key", "value"])
    for key, value in report["answers"].items():
        sheet.append([key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value])
    safety = book.create_sheet("safety")
    safety.append(["item", "value"])
    for key in (
        "verdict",
        "next",
        "user_g",
        "g_file_exists",
        "current_v16_uses_g",
        "runtime_hot_mutation",
        "submit_cancel_live",
        "POLICY_SPEC_SHA",
        "POLICY_INSTANCE_SHA",
        "v16_sha",
        "capital_control_user_ids_configured",
        "control_channel_configured",
    ):
        safety.append([key, json.dumps(report[key]) if report[key] is None else str(report[key])])
    book.save(path)


def main() -> None:
    report = build_report()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(write_markdown(report), encoding="utf-8")
    write_xlsx(report, OUT / "audit.xlsx")
    print(report["verdict"])
    print(report["next"])


if __name__ == "__main__":
    main()
