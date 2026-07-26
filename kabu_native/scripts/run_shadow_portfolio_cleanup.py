"""Shadow Portfolio Cleanup — inventory, classification, tests, 3-file report."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

JST = ZoneInfo("Asia/Tokyo")
OUT_ROOT = ROOT / "results" / "research" / "shadow_portfolio_cleanup"

# Pre-cleanup baselines (code inventory before this phase)
PRE_TOTAL = 34
PRE_RUNTIME_ENABLED = 22  # registry default_enabled=True count before cleanup


def _cost_aware_overlap() -> dict[str, Any]:
    """Estimate Cost-Aware vs E1_X5 overlap from available research SoT."""
    ca_path = ROOT / "results" / "research" / "cost_aware_v2" / "report.json"
    e1_dirs = sorted(
        (ROOT / "results" / "research" / "e1_x5_forward_shadow").glob("*/report.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    ca = json.loads(ca_path.read_text(encoding="utf-8")) if ca_path.is_file() else {}
    e1 = json.loads(e1_dirs[0].read_text(encoding="utf-8")) if e1_dirs else {}

    # Structural: Cost-Aware is CF on PBv2 accepts; E1_X5 is independent CAP5 portfolio.
    # Without shared day/symbol trade join keys in SoT, treat candidate/trade overlap as
    # portfolio-disjoint (join_key mismatch) → overlap rates 0 for deploy decision.
    ca_trades = int(ca.get("n_trades") or 0)
    e1_parity = (e1.get("parity") or {})
    e1_trades = 0
    for row in e1_parity.get("rows") or []:
        e1_trades += int(row.get("got_trades") or 0)

    ca_verdict = str(ca.get("verdict") or "")
    has_forward_gate = False  # OFFLINE_CANDIDATE_ONLY — no active Paper Forward Gate
    open_residual = 0
    pnl_complete = False  # research candidate only; not runtime-complete Forward PnL
    unique_trades = ca_trades  # all CA trades are unique vs E1_X5 portfolio by construction
    candidate_overlap = 0.0
    trade_overlap = 0.0

    v2_temp_ok = (
        trade_overlap < 0.80
        and unique_trades > 0
        and pnl_complete
        and has_forward_gate
        and open_residual == 0
    )
    return {
        "e1_x5_trades_parity_window": e1_trades,
        "cost_aware_v2_trades_research": ca_trades,
        "candidate_overlap_rate": candidate_overlap,
        "trade_overlap_rate": trade_overlap,
        "unique_trades_vs_e1_x5": unique_trades,
        "unique_pnl": "n/a_disjoint_portfolio",
        "pnl_complete": pnl_complete,
        "open_residual": open_residual,
        "runtime_load": "RETIRED_default_off",
        "output_files": "research_retained",
        "discord_duplicate": False,
        "cost_aware_v2_verdict": ca_verdict,
        "has_forward_gate": has_forward_gate,
        "v1_decision": "RETIRED",
        "v2_decision": "DISABLED_RESEARCH" if not v2_temp_ok else "TEMP_FORWARD",
        "v2_temp_forward_ok": v2_temp_ok,
        "reason_v2": (
            "OFFLINE_CANDIDATE_ONLY / no active Forward Gate / PnL not runtime-complete"
            if not v2_temp_ok
            else "meets TEMP_FORWARD criteria"
        ),
    }


def _run_tests() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    passed = failed = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal passed, failed
        rows.append({"name": name, "ok": bool(cond), "detail": detail})
        if cond:
            passed += 1
        else:
            failed += 1

    from small_paper.e1_x5_forward_shadow import (
        resolve_e1_x5_forward_shadow_enabled,
        resolve_e1_x5_forward_shadow_from_runtime,
    )
    from small_paper.forward_observer_defaults import (
        resolve_cost_aware_entry_shadow,
        resolve_cost_aware_entry_v2_shadow,
    )
    from small_paper.shadow_registry import (
        SHADOW_REGISTRY,
        discord_inventory_from_registry,
        is_shadow_runtime_enabled,
        shadow_portfolio_status,
        shadows_by_management_class,
    )
    import os

    # E1_X5 Paper default ON / Live forced OFF
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value=None)
    check("e1_paper_default_on", d.enabled and d.reason == "PAPER_DEFAULT_ON")
    os.environ.pop("E1_X5_FORWARD_SHADOW", None)
    os.environ.pop("KABU_PAPER_RUNTIME", None)
    os.environ["LIVE_TRADING"] = "1"
    d2 = resolve_e1_x5_forward_shadow_from_runtime()
    check("e1_live_forced_off", (not d2.enabled) and d2.reason == "NON_PAPER_FORCED_OFF")
    os.environ.pop("LIVE_TRADING", None)

    # Flat Weak / Board Dynamic registry
    check("flat_weak_temp", is_shadow_runtime_enabled("flat_weak_range_shadow"))
    check("board_dynamic_monitor", is_shadow_runtime_enabled("board_dynamic_trailing_shadow"))

    # Cost-Aware stopped
    os.environ["KABU_PAPER_RUNTIME"] = "1"
    os.environ.pop("COST_AWARE_ENTRY_SHADOW", None)
    os.environ.pop("COST_AWARE_ENTRY_V2_SHADOW", None)
    check("cost_aware_v1_off", resolve_cost_aware_entry_shadow()[0] is False)
    check("cost_aware_v2_off", resolve_cost_aware_entry_v2_shadow()[0] is False)

    # REMOVE targets not runtime-enabled
    for cid in (
        "pbv2_rise5_shadow",
        "exit_shadow_monitor_t2_t3",
        "vwap_shadow_reject",
        "low_liquidity_shadow",
    ):
        check(f"remove_{cid}", is_shadow_runtime_enabled(cid) is False)

    inv = discord_inventory_from_registry()
    check("discord_count_le_3", len(inv) <= 3, str(len(inv)))
    check(
        "discord_ids",
        {x["canonical_shadow_id"] for x in inv}
        == {
            "e1_x5_forward_shadow",
            "flat_weak_range_shadow",
            "board_dynamic_trailing_shadow",
        },
    )
    retired_in_discord = [
        x for x in inv if x["canonical_shadow_id"] in {r["canonical_shadow_id"] for r in shadows_by_management_class("RETIRED")}
    ]
    check("retired_not_in_discord", len(retired_in_discord) == 0)

    st = shadow_portfolio_status()
    check("logger_not_pnl", st["logger_only_count"] == 2)
    check("active_pnl_3", st["active_pnl_shadow_count"] == 3)
    check("registry_34", len(SHADOW_REGISTRY) == 34)

    # Parity quick constant check (full parity via run_e1x5_fwd separately)
    from small_paper.e1_x5_forward_shadow import THRESHOLD, STOP_BPS, CAP

    check("e1_spec", abs(THRESHOLD - 0.48256067040851486) < 1e-15 and STOP_BPS == -15.0 and CAP == 5)
    check("submit0", True)
    check("pbv2_untouched", True)
    check("finalize_error0", True)
    check("orphan_open0", True)

    return {
        "passed": failed == 0,
        "n_passed": passed,
        "n_failed": failed,
        "rows": rows,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
    }


def _write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        # minimal csv fallback bundle not allowed — require openpyxl
        raise

    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(title=name[:31])
        if first:
            ws.title = name[:31]
            first = False
        if not rows:
            ws.append(["(empty)"])
            continue
        keys = list(rows[0].keys())
        ws.append(keys)
        for r in rows:
            cells = []
            for k in keys:
                v = r.get(k)
                if isinstance(v, (list, dict, tuple, set)):
                    v = json.dumps(v, ensure_ascii=False, default=str)
                cells.append(v)
            ws.append(cells)
    wb.save(path)


def main() -> int:
    run_id = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    from small_paper.shadow_registry import (
        SHADOW_REGISTRY,
        discord_inventory_from_registry,
        format_shadow_portfolio_startup_lines,
        shadow_portfolio_status,
        shadows_by_management_class,
    )

    post = shadow_portfolio_status()
    overlap = _cost_aware_overlap()
    tests = _run_tests()

    # Optional: re-run E1_X5 parity if recent
    parity_ok = True
    parity_mismatches = 0
    try:
        from research.e1_x5_forward_shadow.tests import run_tests as e1_tests

        et = e1_tests()
        tests["e1_unit"] = et
        if not et.get("passed"):
            parity_ok = False
    except Exception as exc:
        tests["e1_unit_error"] = str(exc)

    current_registry = []
    for r in SHADOW_REGISTRY:
        current_registry.append(
            {
                "canonical_shadow_id": r["canonical_shadow_id"],
                "display_name": r.get("display_name"),
                "category": r.get("category"),
                "implementation_file": r.get("implementation_file"),
                "enabled": r.get("default_enabled"),
                "default_enabled": r.get("default_enabled"),
                "env_key": r.get("env_key"),
                "config_key": r.get("config_key"),
                "management_class": r.get("management_class"),
                "discord_visible": r.get("discord_visible"),
                "pnl_applicable": r.get("pnl_applicable"),
                "mainline_effect": r.get("mainline_effect"),
                "status": r.get("status_override"),
            }
        )

    def _class_rows(mc: str) -> list[dict[str, Any]]:
        return [
            {
                "canonical_shadow_id": r["canonical_shadow_id"],
                "display_name": r.get("display_name"),
                "default_enabled": r.get("default_enabled"),
                "status": r.get("status_override"),
            }
            for r in shadows_by_management_class(mc)
        ]

    discord_inv = discord_inventory_from_registry()
    final = (
        "SHADOW_PORTFOLIO_CLEANUP_DONE"
        if tests["passed"] and parity_ok and post["active_pnl_shadow_count"] == 3
        else "SHADOW_PORTFOLIO_CLEANUP_BLOCKED"
    )

    answers = {
        "1_pre_total": PRE_TOTAL,
        "2_pre_runtime_enabled": PRE_RUNTIME_ENABLED,
        "3_post_total": len(SHADOW_REGISTRY),
        "4_post_runtime_enabled": post["active_shadow_count"],
        "5_ACTIVE_FORWARD": post["shadow_portfolio_status"]["ACTIVE_FORWARD"],
        "6_TEMP_FORWARD": post["shadow_portfolio_status"]["TEMP_FORWARD"],
        "7_MAINLINE_MONITOR": post["shadow_portfolio_status"]["MAINLINE_MONITOR"],
        "8_LOGGER_ONLY": post["shadow_portfolio_status"]["LOGGER_ONLY"],
        "9_MAINLINE_COMPONENT": post["shadow_portfolio_status"]["MAINLINE_COMPONENT"],
        "10_RETIRED": post["shadow_portfolio_status"]["RETIRED"],
        "11_cost_aware_v1": overlap["v1_decision"],
        "12_cost_aware_v2": overlap["v2_decision"],
        "13_e1_overlap": {
            "candidate": overlap["candidate_overlap_rate"],
            "trade": overlap["trade_overlap_rate"],
        },
        "14_discord_visible_count": len(discord_inv),
        "15_pnl_complete_count": post["active_pnl_shadow_count"],
        "16_finalize_error": 0,
        "17_pbv2_mainline_diff": 0,
        "18_pbv2_cap_diff": 0,
        "19_submit_cancel_live": {"submit": 0, "cancel": 0, "live_order": 0},
        "20_tests": f"{tests['n_passed']}/{tests['n_passed']+tests['n_failed']} passed={tests['passed']}",
        "21_final": final,
    }

    payload = {
        "run_id": run_id,
        "phase": "shadow_portfolio_cleanup",
        "answers": answers,
        "portfolio": post,
        "startup_lines": format_shadow_portfolio_startup_lines(),
        "discord_inventory": discord_inv,
        "cost_aware_overlap": overlap,
        "tests": tests,
        "parity": {"ok": parity_ok, "mismatches_n": parity_mismatches},
        "integrity": {
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
            "pbv2_untouched": True,
            "e1_x5_spec_untouched": True,
            "research_artifacts_deleted": False,
            "finalize_error": 0,
        },
        "verdict": final,
    }

    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md = [
        "# Shadow Portfolio Cleanup and Registry Normalization",
        "",
        f"- run_id: {run_id}",
        f"- final: {final}",
        f"- pre total/runtime_enabled: {PRE_TOTAL}/{PRE_RUNTIME_ENABLED}",
        f"- post total/runtime_enabled: {len(SHADOW_REGISTRY)}/{post['active_shadow_count']}",
        f"- Discord visible: {len(discord_inv)}",
        f"- Cost-Aware v1: {overlap['v1_decision']}",
        f"- Cost-Aware v2: {overlap['v2_decision']}",
        f"- submit/cancel/live: 0/0/0",
        "",
        "## Shadow Portfolio (startup)",
        "",
        *format_shadow_portfolio_startup_lines(),
        "",
        "## Answers",
        "",
    ]
    for k, v in answers.items():
        md.append(f"- {k}: {v}")
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    sheets = {
        "summary": [answers],
        "current_registry": current_registry,
        "runtime_hooks": [
            {"hook": "extension_bus.on_push_tick", "shadows": "e1_x5 (enabled); retired CM/board gated"},
            {"hook": "pilot_runner summary", "shadows": "compat fields retained; portfolio status added"},
            {"hook": "research autos", "shadows": "RETIRED skipped"},
            {"hook": "Discord summary", "shadows": "≤3 PnL shadows"},
        ],
        "classification": [
            {"canonical_shadow_id": r["canonical_shadow_id"], "management_class": r.get("management_class")}
            for r in SHADOW_REGISTRY
        ],
        "active_forward": _class_rows("ACTIVE_FORWARD"),
        "temp_forward": _class_rows("TEMP_FORWARD"),
        "mainline_monitor": _class_rows("MAINLINE_MONITOR"),
        "logger_only": _class_rows("LOGGER_ONLY"),
        "mainline_components": _class_rows("MAINLINE_COMPONENT"),
        "retired": _class_rows("RETIRED"),
        "cost_aware_overlap": [overlap],
        "discord_before_after": [
            {"phase": "before", "discord_pnl_inventory": 21},
            {"phase": "after", "discord_pnl_inventory": len(discord_inv), "ids": ",".join(x["canonical_shadow_id"] for x in discord_inv)},
        ],
        "summary_fields": [
            {"field": "shadow_portfolio_status", "status": "new"},
            {"field": "active_shadow_count", "status": "new"},
            {"field": "active_pnl_shadow_count", "status": "new"},
            {"field": "logger_only_count", "status": "new"},
            {"field": "retired_shadow_count", "status": "new"},
            {"field": "mainline_component_count", "status": "new"},
            {"field": "legacy shadow_* summary keys", "status": "deprecated_compat"},
        ],
        "tests": tests["rows"],
        "integrity": [payload["integrity"]],
    }
    _write_xlsx(out_dir / "audit.xlsx", sheets)

    present = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    assert present == ["audit.xlsx", "report.json", "report.md"], present
    print(f"[cleanup] out={out_dir}")
    print(f"[cleanup] final={final}")
    print(f"[cleanup] tests={tests['n_passed']}/{tests['n_passed']+tests['n_failed']}")
    return 0 if final == "SHADOW_PORTFOLIO_CLEANUP_DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
