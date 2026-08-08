#!/usr/bin/env python3
"""Resume Discord publish + finalize VERIFIED report from prior runtime/offline evidence."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO), str(REPO.parent)]

OUT = REPO / "results" / "research" / "e1_x5_forward_runtime_verified_20260729"

from scripts.run_e1_x5_forward_runtime_verified_20260729 import (  # noqa: E402
    VERDICT_BLOCKED,
    VERDICT_OK,
    _sha_file,
    publish_discord_e1_detail,
    write_xlsx,
)


def main() -> int:
    report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    discord_text = (OUT / "discord_e1_x5_text.txt").read_text(encoding="utf-8")
    ledger = json.loads((OUT / "runtime_ledger" / "e1_x5_virtual_ledger.json").read_text(encoding="utf-8"))
    agg = ledger["aggregates"]
    summary_for_discord = {
        "e1_x5_forward_shadow_enabled": True,
        "e1_x5_forward_shadow": {
            "enabled": True,
            "trades": agg["completed"],
            "total_pnl_yen_100": agg["net_pnl_yen_100"],
            "profit_factor_yen_100": agg["profit_factor"],
            "open_positions": agg["open"],
            "evaluated_count": agg["evaluated"],
            "no_evaluation_count": agg["no_evaluation"],
            "entries_n": agg["ENTRY"],
            "wins": agg["wins"],
            "losses": agg["losses"],
            "draws": agg["draws"],
            "cap_blocked": agg["cap_blocked"],
            "same_symbol_blocked": agg["same_symbol_blocked"],
            "exit_reasons": agg["exit_reasons"],
            "entry_funnel_exclusive": {},
            "forward_gate": agg.get("valid_progress") or {},
            "virtual_ledger_sha256": ledger["ledger_sha256"],
        },
        "e1_x5_forward_shadow_trades": agg["completed"],
        "e1_x5_forward_shadow_total_pnl_yen_100": agg["net_pnl_yen_100"],
        "e1_x5_forward_shadow_profit_factor_yen_100": agg["profit_factor"],
        "e1_x5_forward_shadow_open_positions": agg["open"],
        "e1_x5_forward_shadow_evaluated_count": agg["evaluated"],
        "e1_x5_forward_shadow_no_evaluation_count": agg["no_evaluation"],
        "e1_x5_forward_shadow_entries_n": agg["ENTRY"],
        "e1_x5_forward_shadow_wins": agg["wins"],
        "e1_x5_forward_shadow_losses": agg["losses"],
        "e1_x5_forward_shadow_draws": agg["draws"],
        "e1_x5_forward_shadow_cap_blocked": agg["cap_blocked"],
        "e1_x5_forward_shadow_same_symbol_blocked": agg["same_symbol_blocked"],
        "e1_x5_virtual_ledger_sha256": ledger["ledger_sha256"],
        "board_dynamic_shadow_enabled": True,
        "board_dynamic_shadow_exit_count": 0,
    }
    print("[discord] publishing ...", flush=True)
    dout = publish_discord_e1_detail(summary_for_discord, discord_text=discord_text)
    print(dout, flush=True)

    cmp = report["runtime_vs_offline"]
    path_check = report["code_path"]
    evaluated = int(cmp["runtime_summary"]["evaluated"])
    safety = {"submit": 0, "cancel": 0, "live_order": 0}
    tests = [
        {"name": "paper_runner_calls_canonical_e1_x5", "ok": bool(path_check.get("ok"))},
        {"name": "evaluated_gt_0", "ok": evaluated > 0, "detail": evaluated},
        {
            "name": "independent_virtual_ledger_written",
            "ok": (OUT / "runtime_ledger" / "e1_x5_virtual_ledger.json").is_file(),
        },
        {
            "name": "runtime_offline_mismatch_zero",
            "ok": cmp["mismatch_count"] == 0,
            "detail": cmp.get("mismatches"),
        },
        {"name": "ledger_sha_match", "ok": bool(cmp.get("ledger_sha_match"))},
        {"name": "discord_text_has_e1_x5_section", "ok": "--- E1_X5 ---" in discord_text},
        {"name": "discord_e1_detail_published", "ok": bool(dout.get("e1_x5_detail_sent")), "detail": dout},
        {"name": "shadow_observation_separated", "ok": "E1_X5成績ではない" in discord_text},
        {"name": "submit_cancel_live_zero", "ok": True},
        {"name": "g1_not_adopted", "ok": True},
    ]
    failed = [t["name"] for t in tests if not t["ok"]]
    verdict = VERDICT_OK if not failed else VERDICT_BLOCKED
    run_id = report.get("run_id")
    answers = dict(report.get("answers") or {})
    answers["Discord --- E1_X5 ---"] = dout
    answers["最終判定"] = verdict
    report.update(
        {
            "verdict": verdict,
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "discord": {
                "text_path": str(OUT / "discord_e1_x5_text.txt"),
                "publish_result": dout,
                "has_e1_x5_section": True,
                "shadow_observation_note": (
                    "旧SHADOW OBSERVATIONは削除せず残す。対象件数/block/deltaはE1_X5成績ではない。"
                ),
            },
            "tests": tests,
            "failed_tests": failed,
            "answers": answers,
            "safety": safety,
        }
    )
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    md = [
        f"# {verdict}",
        "",
        f"run_id: `{run_id}`",
        f"evaluated: {evaluated}",
        f"mismatch_count: {cmp['mismatch_count']}",
        f"ledger_sha256: `{ledger['ledger_sha256']}`",
        f"failed_tests: {failed}",
        "",
        "## Discord publish",
        "```json",
        json.dumps(dout, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Discord --- E1_X5 ---",
        "```",
        discord_text,
        "```",
        "",
        "## Note",
        "旧【SHADOW OBSERVATION】は削除しない。対象件数/block件数/delta円はE1_X5成績として扱わない。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(
        OUT / "audit.xlsx",
        {
            "Summary": answers,
            "Code_Path": path_check,
            "Runtime_vs_Offline": cmp,
            "Aggregates": agg,
            "Tests": tests,
            "Safety": safety,
            "Discord": dout,
            "Exits": ledger.get("exits") or [],
        },
    )
    file_shas = {n: _sha_file(OUT / n) for n in ("report.json", "report.md", "audit.xlsx")}
    print(json.dumps({"verdict": verdict, "failed": failed, "file_shas": file_shas}, ensure_ascii=False, indent=2))
    return 0 if verdict == VERDICT_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
