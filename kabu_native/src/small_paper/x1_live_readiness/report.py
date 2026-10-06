"""Write the live-readiness artifacts. Does not start Paper and does not send orders."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from small_paper.x1_live_readiness import (
    REAL_BROKER_CANCEL_CALLS,
    REAL_BROKER_LIVE_CALLS,
    REAL_BROKER_SUBMIT_CALLS,
)
from small_paper.x1_live_readiness.fault_matrix import run_all
from small_paper.x1_live_readiness.replay import run_observed_latency_replay
from small_paper.x1_live_readiness.shadow import v12_observed_shadow

OUT = Path(__file__).resolve().parents[3] / "results" / "operations" / "live_trading_readiness_v1"


def build_report(rows: list[dict] | None = None) -> dict:
    matrix = rows if rows is not None else run_all()
    failed = [row["scenario"] for row in matrix if not row["passed"]]
    shadow = v12_observed_shadow()
    replay = run_observed_latency_replay([])
    safety_pass = not failed
    economics_proven = replay["status"] == "COMPLETED"
    full_day = "NOT_RUN"
    if not safety_pass or full_day != "PASS":
        verdict = "LIVE_TRADING_READINESS_BLOCKED_V1"
        nxt = "NEXT_UNSEEN_FULL_DAY_V12_PAPER"
    elif not economics_proven:
        verdict = "LIVE_EXECUTION_ECONOMICS_NOT_PROVEN_V1"
        nxt = "COLLECT_V12_EXECUTION_SHADOW_THEN_REPLAY"
    else:
        verdict = "LIVE_SMALL_EXECUTION_VALIDATION_READY_V1"
        nxt = "WAIT_FOR_OPERATOR_EXPLICIT_LIVE_SMALL_APPROVAL"
    return {
        "verdict": verdict,
        "next": nxt,
        "live_authorized": False,
        "activation_id": "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V12",
        "v12_strategy_modified": False,
        "prospective_full_day": {
            "trading_date_20260929": "NOT_PROSPECTIVE_DAY1",
            "v12_unseen_full_day": full_day,
            "reason": "No unseen complete V12 paper session has been run. This phase did not start Paper.",
        },
        "order_state_machine_implemented": True,
        "real_broker_submit_calls": REAL_BROKER_SUBMIT_CALLS,
        "real_broker_cancel_calls": REAL_BROKER_CANCEL_CALLS,
        "real_broker_live_calls": REAL_BROKER_LIVE_CALLS,
        "submit_cancel_live": [REAL_BROKER_SUBMIT_CALLS, REAL_BROKER_CANCEL_CALLS, REAL_BROKER_LIVE_CALLS],
        "fault_matrix": {
            "n": len(matrix),
            "passed": sum(1 for row in matrix if row["passed"]),
            "failed": failed,
            "rows": matrix,
        },
        "execution_shadow": shadow,
        "execution_replay": replay,
        "emergency_liquidation_implemented": False,
    }


def write_report(rows: list[dict] | None = None) -> dict:
    body = build_report(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(body), encoding="utf-8")
    _xlsx(OUT / "audit.xlsx", body)
    return body


def _markdown(body: dict) -> str:
    matrix = body["fault_matrix"]
    replay = body["execution_replay"]
    lines = [
        "# X1 V12 live trading readiness V1",
        "",
        f"VERDICT: {body['verdict']}",
        f"NEXT: {body['next']}",
        "",
        "V12 strategy, activation, and candidate hashes were not modified.",
        "20260929 remains NOT_PROSPECTIVE_DAY1.",
        "Real order calls were not enabled. submit/cancel/live = 0/0/0.",
        "live authorized: false",
        "",
        "## Prospective full day",
        "",
        "V12 unseen full-day paper: NOT_RUN.",
        "This phase did not launch the checked runner.",
        "",
        "## Fault matrix",
        "",
        f"passed {matrix['passed']} / {matrix['n']}",
        "",
        "| scenario | group | qty | submits | entry blocked | parity | deterministic |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for row in matrix["rows"]:
        lines.append(
            f"| {row['scenario']} | {row['group']} | {row['confirmed_qty']} | {row['submit_calls']} | {row['entry_blocked']} | {row['parity']} | {row['deterministic']} |"
        )
    lines.extend(
        [
            "",
            "## Execution shadow",
            "",
            "n = 0. No V12 paper session recorded signal-to-ready latency.",
            "",
            "## Execution replay",
            "",
            f"status: {replay['status']}",
            f"reason: {replay['reason']}",
            "Control and treatment economics were not computed. Baseline PnL was not haircut.",
            "",
            "STOP.",
            "",
        ]
    )
    return "\n".join(lines)


def _xlsx(path: Path, body: dict) -> None:
    rows = [["scenario", "group", "passed", "confirmed_qty", "submit_calls", "entry_blocked", "parity", "deterministic", "state", "mode"]]
    for row in body["fault_matrix"]["rows"]:
        rows.append(
            [
                row["scenario"],
                row["group"],
                row["passed"],
                row["confirmed_qty"],
                row["submit_calls"],
                row["entry_blocked"],
                row["parity"],
                row["deterministic"],
                row["state"],
                row["mode"],
            ]
        )
    verdict_rows = [
        ["field", "value"],
        ["verdict", body["verdict"]],
        ["next", body["next"]],
        ["live_authorized", body["live_authorized"]],
        ["v12_unseen_full_day", body["prospective_full_day"]["v12_unseen_full_day"]],
        ["trading_date_20260929", body["prospective_full_day"]["trading_date_20260929"]],
        ["submit", body["submit_cancel_live"][0]],
        ["cancel", body["submit_cancel_live"][1]],
        ["live", body["submit_cancel_live"][2]],
        ["shadow_n", body["execution_shadow"]["n"]],
        ["replay_status", body["execution_replay"]["status"]],
    ]
    sheet1 = _sheet_xml(rows)
    sheet2 = _sheet_xml(verdict_rows)
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="fault_matrix" sheetId="1" r:id="rId1"/>
<sheet name="verdict" sheetId="2" r:id="rId2"/>
</sheets>
</workbook>"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>"""
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)


def _sheet_xml(rows: list[list]) -> str:
    body = ["<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
            "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>"]
    for r_idx, row in enumerate(rows, start=1):
        body.append(f"<row r=\"{r_idx}\">")
        for c_idx, value in enumerate(row, start=1):
            ref = f"{_col(c_idx)}{r_idx}"
            text = _xml(value)
            body.append(f"<c r=\"{ref}\" t=\"inlineStr\"><is><t>{text}</t></is></c>")
        body.append("</row>")
    body.append("</sheetData></worksheet>")
    return "".join(body)


def _col(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xml(value) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
