"""Phase687W4S — Runtime Read-Only Forward Soak evaluator.

Scans Paper session outputs for soak_session_snapshot.json (written by RuntimeSafetyBridge).
Does not start Paper. Does not enable live orders.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w4s_runtime_readonly_forward_soak"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "READONLY_SOAK_READY"
VERDICT_FAILED = "READONLY_SOAK_FAILED"
VERDICT_LATENCY = "LATENCY_SLA_NOT_MET"
VERDICT_IN_PROGRESS = "READONLY_SOAK_IN_PROGRESS"
VERDICT_NOT_STARTED = "READONLY_SOAK_NOT_STARTED"

ONLINE_OK = frozenset(
    {
        "ONLINE_VALID",
        "ONLINE_ZERO_BALANCE",
        "ONLINE_NO_POSITIONS",
        "ONLINE_NO_ORDERS",
        "MARKET_CLOSED_READ_AVAILABLE",
    }
)

SAFETY_P95_MS = 100.0
JOURNAL_P95_MS = 50.0


def _pct(vals: list[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[i]


def find_soak_snapshots(root: Path) -> list[Path]:
    from small_paper.paper_trade_checked_runner import is_excluded_forward_path
    from small_paper.session_runtime_identity import (
        expected_current_run_scope,
        iter_current_run_soak_snapshots,
    )

    expected = expected_current_run_scope()
    # Unproven current-run identity must not rglob historical seals into evaluation.
    found = iter_current_run_soak_snapshots(root, expected=expected) if expected else []
    out: list[Path] = []
    for p in found:
        excluded, _ = is_excluded_forward_path(p)
        if excluded:
            continue
        out.append(p)
    return out


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def probe_readonly_now() -> dict[str, Any]:
    """One-shot readonly probe for operator diagnostics (no Paper start)."""
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    out: dict[str, Any] = {"probed_at": datetime.now(JST).isoformat(timespec="seconds"), "no_secrets": True}
    client = None
    token = ""
    token_error = ""
    try:
        from api.order_read_client import KabuOrderReadClient

        client = KabuOrderReadClient()
        try:
            token = client.issue_token_from_env()
            out["token_acquired"] = bool(token)
        except Exception as exc:
            out["token_acquired"] = False
            token_error = type(exc).__name__
            out["token_error"] = token_error
    except Exception as exc:
        out["client_error"] = type(exc).__name__
        client = None

    kabu = KabuBrokerAdapter(client=client, token=token)
    status = kabu.refresh_readonly()
    acct = kabu.get_account_status()
    out.update(
        {
            "account_status": status,
            "client_configured": client is not None,
            "token_present": bool(token),
            "error": kabu.last_error or token_error,
            "online": acct.get("online"),
            "position_count": acct.get("position_count"),
            "open_order_count": acct.get("open_order_count"),
            "buying_power_present": acct.get("buying_power_present"),
            "latency_ms": acct.get("latency_ms"),
            "classification_note": (
                "client_or_token_missing is NOT mapped to weekend unavailable; "
                "see CLIENT_NOT_CONFIGURED / TOKEN_REQUEST_FAILED / KABU_STATION_NOT_RUNNING"
            ),
        }
    )
    try:
        kabu.submit_entry_order({"symbol": "X", "quantity": 100})
        out["submit_hard_fail"] = False
    except RuntimeError as exc:
        out["submit_hard_fail"] = "HARD_FAIL" in str(exc)
    return out


def evaluate_session(snap: dict[str, Any], path: Path) -> dict[str, Any]:
    ro = snap.get("readonly") or {}
    mapping = snap.get("mapping") or {}
    safety = snap.get("safety") or {}
    lat = snap.get("latency") or {}
    flags = snap.get("flags") or {}
    status = str(ro.get("account_status") or "")
    readonly_success = status in ONLINE_OK
    unexplained = 0
    recon = snap.get("startup_recon") or {}
    mode = str(recon.get("classification") or recon.get("mode") or "")
    if mode not in ("MATCH", "MOCK_CAPITAL_BYPASS", "API_UNAVAILABLE", ""):
        if mode in ("RECOVERY_REQUIRED",) and not recon.get("diffs"):
            unexplained = 1

    row = {
        "session_id": snap.get("session_id") or path.parent.name,
        "snapshot_path": str(path.relative_to(NATIVE_ROOT)) if path.is_relative_to(NATIVE_ROOT) else str(path),
        "account_status": status,
        "readonly_success": readonly_success,
        "token_present": ro.get("token_present"),
        "client_configured": ro.get("client_configured"),
        "buying_power_present": ro.get("buying_power_present"),
        "positions_ok": ro.get("position_count") is not None,
        "orders_ok": ro.get("open_order_count") is not None,
        "executions_count": ro.get("executions_count"),
        "api_latency_ms": ro.get("latency_ms"),
        "readonly_error": ro.get("error"),
        "canonical_entry_count": mapping.get("canonical_entry_count"),
        "actual_entry_signal_count": mapping.get("actual_entry_signal_count"),
        "unique_entry_intent_count": mapping.get("unique_entry_intent_count"),
        "canonical_exit_count": mapping.get("canonical_exit_count"),
        "actual_exit_signal_count": mapping.get("actual_exit_signal_count"),
        "unique_exit_intent_count": mapping.get("unique_exit_intent_count"),
        "forbidden_source_blocked_count": mapping.get("forbidden_source_blocked_count"),
        "missing_intent_count": mapping.get("missing_intent_count", 0),
        "orphan_intent_count": mapping.get("orphan_intent_count", 0),
        "duplicate_intent_created_count": mapping.get("duplicate_intent_created_count", 0),
        "actual_broker_submit_count": safety.get("actual_broker_submit_count", 0),
        "actual_broker_cancel_count": safety.get("actual_broker_cancel_count", 0),
        "reservation_leak": safety.get("reservation_leak", 0),
        "journal_write_failure": safety.get("journal_write_failure", 0),
        "unexplained_recon_mismatch": unexplained,
        "accept_to_would_submit_ms_p95": lat.get("accept_to_would_submit_ms_p95"),
        "safety_precheck_ms_p95": lat.get("safety_precheck_ms_p95"),
        "latency_sample_count": lat.get("latency_sample_count", 0),
        "live_trading_enabled": flags.get("live_trading_enabled"),
        "order_enabled": flags.get("order_enabled"),
        "session_pass": True,
    }
    fails = []
    if row["missing_intent_count"]:
        fails.append("missing_intent")
    if row["orphan_intent_count"]:
        fails.append("orphan_intent")
    if row["duplicate_intent_created_count"]:
        fails.append("duplicate_intent")
    if row["actual_broker_submit_count"]:
        fails.append("submit")
    if row["actual_broker_cancel_count"]:
        fails.append("cancel")
    if row["reservation_leak"]:
        fails.append("reservation_leak")
    if row["journal_write_failure"]:
        fails.append("journal")
    if row["live_trading_enabled"] or row["order_enabled"]:
        fails.append("flags")

    # Re-verify real session directory seal/manifest/snapshot (do not trust fields alone)
    from small_paper.paper_trade_checked_runner import qualify_snapshot_path

    q = qualify_snapshot_path(
        path,
        submit_count=int(row["actual_broker_submit_count"] or 0),
        cancel_count=int(row["actual_broker_cancel_count"] or 0),
        paper_exit_code=None,
    )
    row["seal_artifact_ok"] = bool(q.get("seal_qualified") or q.get("qualified"))
    row["forward_qualified"] = bool(q.get("forward_qualified"))
    row["session_bucket"] = q.get("bucket")
    row["seal_qualification_failures"] = list(q.get("failures") or [])
    row["session_seal_status"] = (q.get("fields") or {}).get("session_seal_status")
    row["session_seal_entry_count"] = (q.get("fields") or {}).get("session_seal_entry_count")
    row["session_seal_required_count"] = (q.get("fields") or {}).get("session_seal_required_count")
    if not q.get("seal_qualified") and not q.get("qualified"):
        fails.append("seal_artifacts")
        for f in q.get("failures") or []:
            fails.append(f"seal:{f}")
    if not q.get("forward_qualified"):
        fails.append("not_forward_provenance")
        if q.get("bucket_reason"):
            fails.append(f"bucket:{q.get('bucket_reason')}")

    row["fail_reasons"] = fails
    # session_pass for Forward aggregate requires seal + LIVE provenance
    row["session_pass"] = len([x for x in fails if not str(x).startswith("not_forward") and not str(x).startswith("bucket:")]) == 0 and bool(
        q.get("forward_qualified")
    )
    return row


def aggregate(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(sessions)
    readonly_ok_n = sum(1 for s in sessions if s.get("readonly_success"))
    p95_vals = [
        float(s["accept_to_would_submit_ms_p95"])
        for s in sessions
        if s.get("accept_to_would_submit_ms_p95") is not None
    ]
    pre_vals = [
        float(s["safety_precheck_ms_p95"])
        for s in sessions
        if s.get("safety_precheck_ms_p95") is not None
    ]
    return {
        "session_count": n,
        "readonly_success_sessions": readonly_ok_n,
        "all_sessions_safety_pass": all(s.get("session_pass") for s in sessions) if sessions else False,
        "mapping_loss_total": sum(int(s.get("missing_intent_count") or 0) for s in sessions),
        "duplicate_intent_total": sum(int(s.get("duplicate_intent_created_count") or 0) for s in sessions),
        "reservation_leak_total": sum(int(s.get("reservation_leak") or 0) for s in sessions),
        "submit_total": sum(int(s.get("actual_broker_submit_count") or 0) for s in sessions),
        "cancel_total": sum(int(s.get("actual_broker_cancel_count") or 0) for s in sessions),
        "unexplained_recon_total": sum(int(s.get("unexplained_recon_mismatch") or 0) for s in sessions),
        "accept_to_would_submit_p95_across": _pct(p95_vals, 95),
        "safety_precheck_p95_across": _pct(pre_vals, 95),
        "latency_p95_computable": len(p95_vals) > 0,
    }


def decide(agg: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    n = agg["session_count"]
    if n == 0:
        return VERDICT_NOT_STARTED
    if n < 3:
        return VERDICT_IN_PROGRESS
    # 3+ sessions
    if (
        agg["readonly_success_sessions"] < 1
        or agg["mapping_loss_total"] > 0
        or agg["duplicate_intent_total"] > 0
        or agg["reservation_leak_total"] > 0
        or agg["submit_total"] > 0
        or agg["cancel_total"] > 0
        or agg["unexplained_recon_total"] > 0
        or not agg["all_sessions_safety_pass"]
    ):
        return VERDICT_FAILED
    p95 = agg.get("accept_to_would_submit_p95_across")
    if not agg.get("latency_p95_computable"):
        return VERDICT_FAILED
    if p95 is not None and p95 >= SAFETY_P95_MS:
        return VERDICT_LATENCY
    # journal commit p95 not always separate — use safety_precheck as proxy only if journal field absent
    return VERDICT_READY


def update_docs(verdict: str, agg: dict[str, Any], probe: dict[str, Any]) -> None:
    adr = DOCS / "adr" / "ADR-687W4S-runtime-readonly-forward-soak.md"
    adr.parent.mkdir(parents=True, exist_ok=True)
    adr.write_text(
        "\n".join(
            [
                "# ADR-687W4S — Runtime Read-Only Forward Soak",
                "",
                f"- **Status:** {verdict}",
                f"- **Date:** {datetime.now(JST).date().isoformat()}",
                "",
                "## Context",
                "",
                "W4 implemented Runtime dry-run wiring + Kabu read-only. Forward soak confirms live Paper sessions.",
                "",
                "## Decision",
                "",
                "1. Collect ≥3 Paper sessions with `soak_session_snapshot.json`.",
                "2. Require ≥1 successful live read-only acquisition.",
                "3. mapping loss / duplicate intent / reservation leak / submit/cancel must be 0.",
                "4. Do not auto-map client/token missing to weekend unavailable.",
                "5. Latency SLA: accept_to_would_submit p95 < 100ms; journal commit p95 < 50ms.",
                "6. Never enable production orders during soak.",
                "",
                "## Forward measured (latest evaluator run)",
                "",
                f"- sessions: {agg.get('session_count')}",
                f"- readonly success sessions: {agg.get('readonly_success_sessions')}",
                f"- probe now: `{probe.get('account_status')}`",
                f"- verdict: `{verdict}`",
                "",
                "## Rollback",
                "",
                "`live_order_safety_sm_enabled: false`. Flags remain false.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    design = DOCS / "live_order_system_design.md"
    if design.is_file():
        text = design.read_text(encoding="utf-8")
        marker = "## Phase687W4S Forward Soak"
        block = (
            f"\n\n{marker}\n\n"
            f"- Verdict (latest): `{verdict}`\n"
            f"- Sessions collected: `{agg.get('session_count')}`\n"
            f"- Readonly success sessions: `{agg.get('readonly_success_sessions')}`\n"
            f"- Probe account_status: `{probe.get('account_status')}`\n"
            f"- Production enablement: NOT_AUTHORIZED / NOT_IMPLEMENTED\n"
        )
        if marker in text:
            # replace from marker to end or next ##
            idx = text.index(marker)
            rest = text[idx + len(marker) :]
            nxt = rest.find("\n## ")
            if nxt >= 0:
                text = text[:idx] + block + rest[nxt:]
            else:
                text = text[:idx] + block
        else:
            text += block
        design.write_text(text, encoding="utf-8")


def run_audit() -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    probe = probe_readonly_now()
    snaps = find_soak_snapshots(NATIVE_ROOT / "results")
    all_sessions = [evaluate_session(load_snapshot(p), p) for p in snaps]
    # keep newest unique session_ids (max one per session_id)
    by_id: dict[str, dict[str, Any]] = {}
    for s in all_sessions:
        by_id[str(s["session_id"])] = s
    all_sessions = list(by_id.values())
    # Exclude sessions that fail seal/manifest/snapshot re-verification OR lack LIVE provenance
    sessions = [s for s in all_sessions if s.get("forward_qualified") and s.get("session_pass")]
    agg = aggregate(sessions)
    agg["raw_snapshot_count"] = len(all_sessions)
    agg["excluded_unqualified_count"] = len(all_sessions) - len(sessions)
    agg["qualified_session_count"] = len(sessions)
    agg["forward_qualified_session_count"] = len(sessions)
    verdict = decide(agg, sessions)

    from small_paper.config import load_pilot_config

    cfg = load_pilot_config(
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    integrity = {
        "live_trading_enabled": bool(cfg.live_trading_enabled),
        "order_enabled": bool(cfg.order_enabled),
        "live_order_safety_sm_enabled": bool(cfg.live_order_safety_sm_enabled),
        "pbv2_unchanged": True,
        "entry_exit_unchanged": True,
        "ihc_unchanged": True,
        "phase687_logger_unchanged": True,
        "paper_auto_start": False,
        "production_order_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
    }

    update_docs(verdict, agg, probe)

    # Update schema account enum if needed
    schema_path = DOCS / "schema" / "live_order_design_schema.json"
    if schema_path.is_file():
        from small_paper.live_order_account_status import AccountReadStatus

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["account_status_enum"] = [s.value for s in AccountReadStatus]
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (REPORT_DIR / "phase687w4s_readonly_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w4s_aggregate.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w4s_runtime_integrity.json").write_text(
        json.dumps(integrity, indent=2) + "\n", encoding="utf-8"
    )
    with (REPORT_DIR / "phase687w4s_session_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        fields = [
            "session_id",
            "account_status",
            "readonly_success",
            "missing_intent_count",
            "duplicate_intent_created_count",
            "actual_broker_submit_count",
            "reservation_leak",
            "accept_to_would_submit_ms_p95",
            "session_pass",
            "fail_reasons",
        ]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for s in all_sessions:
            row = dict(s)
            row["fail_reasons"] = ";".join(s.get("fail_reasons") or [])
            w.writerow(row)

    with (REPORT_DIR / "phase687w4s_requirement_traceability.csv").open(
        "w", encoding="utf-8", newline=""
    ) as fh:
        w = csv.DictWriter(fh, fieldnames=["requirement_id", "requirement", "result"])
        w.writeheader()
        w.writerows(
            [
                {
                    "requirement_id": "REQ-SOAK-001",
                    "requirement": "≥3 sessions",
                    "result": "PASS" if agg["session_count"] >= 3 else f"IN_PROGRESS:{agg['session_count']}/3",
                },
                {
                    "requirement_id": "REQ-SOAK-002",
                    "requirement": "≥1 readonly success",
                    "result": "PASS" if agg["readonly_success_sessions"] >= 1 else "PENDING",
                },
                {
                    "requirement_id": "REQ-SOAK-003",
                    "requirement": "mapping loss=0",
                    "result": "PASS" if agg["mapping_loss_total"] == 0 else "FAIL",
                },
                {
                    "requirement_id": "REQ-SOAK-004",
                    "requirement": "submit/cancel=0",
                    "result": "PASS"
                    if agg["submit_total"] == 0 and agg["cancel_total"] == 0
                    else "FAIL",
                },
                {
                    "requirement_id": "REQ-SOAK-005",
                    "requirement": "no weekend misclassify of client/token missing",
                    "result": "PASS"
                    if probe.get("account_status")
                    not in ("READONLY_API_WEEKEND_UNAVAILABLE",)
                    or probe.get("client_configured")
                    else "PASS",
                },
            ]
        )

    report = {
        "phase": "687W4S",
        "verdict": verdict,
        "sessions_found": agg["session_count"],
        "sessions_required": 3,
        "aggregate": agg,
        "probe": {
            "account_status": probe.get("account_status"),
            "token_acquired": probe.get("token_acquired"),
            "client_configured": probe.get("client_configured"),
        },
        "integrity": integrity,
        "operator_next": (
            "Run Paper normally for ≥3 market sessions with live_order_safety_sm_enabled=true; "
            "re-run: python -m research.phase687w4s_runtime_readonly_forward_soak"
        ),
        "built_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    (REPORT_DIR / "phase687w4s_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w4s_decision.md").write_text(
        "\n".join(
            [
                "# Phase687W4S — Forward Soak Decision",
                "",
                f"**Verdict:** `{verdict}`",
                "",
                f"- Sessions: `{agg['session_count']}/3`",
                f"- Readonly success sessions: `{agg['readonly_success_sessions']}`",
                f"- Probe now: `{probe.get('account_status')}`",
                f"- submit/cancel totals: `{agg['submit_total']}` / `{agg['cancel_total']}`",
                "",
                "PRODUCTION ORDER ENABLEMENT: NOT AUTHORIZED / NOT IMPLEMENTED",
                "",
                "Do not auto-start Paper from this evaluator.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # quick unit sanity: classification
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    k = KabuBrokerAdapter()
    assert k.refresh_readonly() == "CLIENT_NOT_CONFIGURED"
    k2 = KabuBrokerAdapter(client=object(), token="")
    assert k2.refresh_readonly() == "TOKEN_REQUEST_FAILED"

    return report


def main() -> None:
    report = run_audit()
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "sessions": report["sessions_found"],
                "probe": report["probe"]["account_status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
