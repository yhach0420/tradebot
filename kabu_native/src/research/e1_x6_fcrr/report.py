"""Publish report.json / report.md / audit.xlsx for FCRR final run."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_file

JST = ZoneInfo("Asia/Tokyo")
OUT_DIR = (
    Path(__file__).resolve().parents[3]
    / "results" / "research" / "e1_x6_redesign_20260721_20260731"
)


def render_md(report: dict[str, Any], sha: str) -> str:
    lines = [
        f"# E1_X6_FCRR — {report['verdict']}",
        "",
        f"- plan: `{report['plan_document_id']}` {report['plan_version']}",
        f"- spec: `{report['document_id']}` {report['document_version']}",
        f"- precommit_at_jst: {report['precommit']['precommit_at_jst']}",
        f"- precommit_sha256: `{report['precommit']['precommit_sha256']}`",
        f"- final_run_id: `{report['final_run_id']}`",
        f"- report.json sha: `{sha}`",
        f"- submit/cancel/live: 0/0/0",
        f"- mainline_changed: false",
        "",
        "## Gate 0",
        f"- ok: {report['gate0']['ok']}",
        f"- notes: {report['gate0'].get('notes')}",
        "",
        "## Candidates",
    ]
    for cid, row in report.get("candidate_results", {}).items():
        m = row["metrics"]
        lines.append(
            f"- `{cid}` n={m['n']} pnl={m['pnl']:.2f} pf={m['pf']} "
            f"failed={row['gates']['failed']}"
        )
    lines += ["", "## Verdict", report["verdict"], "", "STOP — no EXIT redesign / Shadow / Forward."]
    return "\n".join(lines)


def render_xlsx(report: dict[str, Any], sha: str, fp: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in (
        ("verdict", report["verdict"]),
        ("final_run_id", report["final_run_id"]),
        ("report_json_sha256", sha),
        ("precommit_sha256", report["precommit"]["precommit_sha256"]),
    ):
        ws.append(list(row))

    def sheet(name: str, headers: list, rows: list) -> None:
        w = wb.create_sheet(name)
        w.append(headers)
        for r in rows:
            w.append(r)

    sheet("Precommit", ["key", "value"],
          [[k, json.dumps(v, ensure_ascii=False)[:32000] if isinstance(v, (dict, list)) else v]
           for k, v in report["precommit"].items()])
    sheet("Safety", ["key", "value"],
          [["submit", 0], ["cancel", 0], ["live", 0],
           ["mainline_changed", False], ["paper_touched", False]])
    sheet("Tests", ["test", "outcome"],
          [[t["test"], t["outcome"]] for t in report.get("tests", {}).get("rows", [])])
    sheet("Candidates",
          ["id", "n", "pnl", "pf", "ex722_pnl", "max_dd", "stop_loss", "failed"],
          [[cid, r["metrics"]["n"], r["metrics"]["pnl"], r["metrics"]["pf"],
            r["metrics"]["ex722_pnl"], r["metrics"]["max_dd"],
            r["metrics"]["stop_loss_total"], json.dumps(r["gates"]["failed"])]
           for cid, r in report.get("candidate_results", {}).items()])
    tr_rows = []
    for cid, r in report.get("candidate_results", {}).items():
        for t in r.get("trades", [])[:5000]:
            tr_rows.append([cid, t.get("day"), t.get("symbol"), t.get("exit_reason"),
                            t.get("net_pnl_yen_100"), t.get("entry_time"), t.get("exit_time")])
    sheet("Trades", ["candidate", "day", "symbol", "exit", "pnl", "entry", "exit_t"], tr_rows)
    sheet("WalkForward", ["fold", "selected", "confirm", "confirm_pnl"],
          [[f["fold"], f["selected"], f["confirm"], f["confirm_pnl"]]
           for f in (report.get("rolling_origin") or {}).get("folds", [])])
    sheet("DayDeletion", ["held_out", "remaining_pnl", "pass"],
          [[r["held_out_day"], r["remaining_pnl"], r["pass"]]
           for r in (report.get("day_deletion") or {}).get("rows", [])])
    sheet("ChangeLog", ["item", "note"],
          [["FCRR_v1.0", "research-only independent ENTRY; X5 EXIT frozen"],
           ["CORE_VALID", str((report.get("gate0") or {}).get("core_valid"))]])
    # funnel
    fun_rows = []
    for cid, r in report.get("candidate_results", {}).items():
        for f in r.get("funnels", []):
            fun_rows.append([cid, f.get("day"), f.get("obs"), f.get("ENTRY_EMITTED"),
                             f.get("RECLAIM_CROSSED"), f.get("RETENTION_CONFIRMED")])
    sheet("FCRR_Funnel", ["candidate", "day", "obs", "entry", "reclaim", "retention"], fun_rows)
    sheet("FCRR_StateTransitions", ["note"], [["see report.json candidate_results.transitions_sha"]])
    sheet("FCRR_Episodes", ["candidate", "cap_blocked", "episode_reentry"],
          [[cid, r.get("cap_blocked"), r.get("episode_reentry")]
           for cid, r in report.get("candidate_results", {}).items()])
    sheet("FCRR_Ablation", ["id", "status"],
          [["A0-A3", "DIAGNOSTIC_ONLY_NOT_RUN_AS_CANDIDATES"],
           ["A4", "equals FCRR"]])
    sheet("NoiseAudit", ["note"], [["x5_accept not parallel-scored in this research path; BOTH_REJECT-heavy"]])
    wb.save(fp)


def atomic_publish(report: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / f"_publish_tmp_{datetime.now().strftime('%H%M%S')}"
    tmp.mkdir(parents=True, exist_ok=True)
    fp_json = tmp / "report.json"
    fp_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
    sha = sha256_file(fp_json)
    (tmp / "report.md").write_text(render_md(report, sha), encoding="utf-8")
    render_xlsx(report, sha, tmp / "audit.xlsx")
    shas = {}
    for name in ("report.json", "report.md", "audit.xlsx"):
        dst = OUT_DIR / name
        os.replace(tmp / name, dst)
        shas[name] = sha256_file(dst)
    tmp.rmdir()
    assert shas["report.json"] == sha
    return shas
