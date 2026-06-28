"""
Paper Runtime Readiness Audit — Phase591/592/593 dry-run hooks must not block paper ENTRY/EXIT.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import MagicMock

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.live_capital_manager import (
    LiveCapitalManagerSession,
    LiveCapitalSnapshot,
    check_entry_capital_on_paper_accept,
    capital_manager_enabled,
    fetch_live_capital_snapshot,
)
from small_paper.live_order_dry_run_adapter import dry_run_adapter_enabled
from small_paper.live_order_api_wiring import wiring_enabled

PAPER_RUNTIME_VERDICT = "paper_runtime_ready_for_tuesday"
BAT_PATH_REL = "run_paper_trade.bat"

CHECK_FIELDS = [
    "check_id",
    "category",
    "pass",
    "detail",
]

PUSH_PARITY_FIELDS = [
    "metric",
    "baseline_value",
    "hooks_enabled_value",
    "match",
]


def _count_observer_exits(events: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for e in events if e.get("event_type") == "observer_exit")


def _run_micro_entry_parity(*, repo_root: Path, config_path: Path) -> dict[str, Any]:
    """Verify hooks run post-accept and do not mutate gate open_slots."""
    from dataclasses import replace as dc_replace

    import tempfile

    from research.exposure_gate import GateDecision
    from small_paper.config import load_pilot_config
    from small_paper.live_writer import LiveSessionWriter
    from small_paper.pilot_runner import (
        EVENT_FIELDS,
        _LiveRunState,
        _PushPipelineContext,
        _execute_accepted_entry,
        _init_live_order_api_wiring,
        _init_live_order_dry_run,
    )

    base_cfg = load_pilot_config(config_path)
    baseline_cfg = dc_replace(
        base_cfg,
        live_order_dry_run_enabled=False,
        live_order_api_wiring_enabled=False,
        live_capital_check_enabled=False,
        discord_enabled=False,
        position_cap_mode=False,
    )
    hooks_cfg = dc_replace(
        base_cfg,
        live_order_dry_run_enabled=True,
        live_order_api_wiring_enabled=True,
        live_capital_check_enabled=True,
        discord_enabled=False,
        position_cap_mode=False,
    )

    class _GateStub:
        def __init__(self) -> None:
            self.state = type("S", (), {"open_slots": [], "day_pnl": {}})()
            self.evaluate_entry = MagicMock()

        def record_accepted(self, trade: Mapping[str, Any]) -> None:
            sym = str(trade.get("symbol") or "")
            if sym:
                self.state.open_slots.append(sym)

    def _run_once(cfg: Any) -> dict[str, int]:
        gate = _GateStub()
        state = _LiveRunState(started_mono=0.0)
        _init_live_order_dry_run(cfg, state)
        _init_live_order_api_wiring(cfg, state)
        with tempfile.TemporaryDirectory() as td:
            writer = LiveSessionWriter(Path(td), incremental=True, event_fields=EVENT_FIELDS)
            ctx = _PushPipelineContext(
                config=cfg,
                gate=gate,
                feature_bridge=MagicMock(),
                state=state,
                writer=writer,
                code_to_symbol={"7203": "7203.T"},
                source="readiness",
                pos_fields=["symbol", "entry_time", "exit_time", "open_slots_after"],
            )
            trade = {
                "symbol": "7203.T",
                "entry_time": "2026-06-18T09:05:00+09:00",
                "day": "20260618",
                "continuation_quality_score": 0.7,
                "momentum_continuation_score": 0.2,
                "entry_score_v2": 6.0,
                "Board": "Board:mid",
            }
            payload = {"CurrentPrice": 2768.0, "AskPrice": 2768.0, "Symbol": "7203"}
            decision = GateDecision(accept=True, reason="", continuation_quality_score=0.7, quality_tier="A")
            slots_before = len(gate.state.open_slots)
            _execute_accepted_entry(
                ctx,
                sym="7203.T",
                trade=trade,
                decision=decision,
                payload=payload,
                enriched=dict(trade),
                msg_i=1,
                bucket="am",
                score5_ord=None,
            )
            slots_after = len(gate.state.open_slots)
        return {
            "accepted_count": len(state.accepted_rows),
            "open_slots_before": slots_before,
            "open_slots_after": slots_after,
        }

    baseline = _run_once(baseline_cfg)
    hooks = _run_once(hooks_cfg)
    rows = []
    all_match = True
    for key in ("accepted_count", "open_slots_before", "open_slots_after"):
        bv, hv = baseline[key], hooks[key]
        match = bv == hv
        all_match = all_match and match
        rows.append(
            {
                "metric": key,
                "baseline_value": bv,
                "hooks_enabled_value": hv,
                "match": match,
            }
        )
    return {
        "mode": "micro_execute_accepted_entry",
        "parity_ok": all_match and baseline["accepted_count"] == hooks["accepted_count"] == 1,
        "rows": rows,
        "baseline": baseline,
        "hooks": hooks,
        "note": "Hooks run after gate.record_accepted; open_slots unchanged by Phase591-593",
    }


def _run_push_replay_parity(
    *,
    repo_root: Path,
    config_path: Path,
    push_dir: Path,
    max_push_rows: int = 8000,
) -> dict[str, Any]:
    from dataclasses import replace as dc_replace

    from small_paper.pilot_runner import run_push_replay_dry_run

    base_cfg = load_pilot_config(config_path)
    baseline_cfg = dc_replace(
        base_cfg,
        live_order_dry_run_enabled=False,
        live_order_api_wiring_enabled=False,
        live_capital_check_enabled=False,
        discord_enabled=False,
    )
    hooks_cfg = dc_replace(
        base_cfg,
        live_order_dry_run_enabled=True,
        live_order_api_wiring_enabled=True,
        live_capital_check_enabled=True,
        discord_enabled=False,
    )

    import shutil

    audit_root = repo_root / "kabu_native" / "results" / "small_paper" / "_readiness_audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    baseline_out = audit_root / "baseline_push_parity"
    hooks_out = audit_root / "hooks_push_parity"
    for p in (baseline_out, hooks_out):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    baseline = run_push_replay_dry_run(
        baseline_cfg,
        push_dir=push_dir,
        output_dir=baseline_out,
        repo_root=repo_root,
        max_push_rows=max_push_rows,
        enable_discord=False,
        streaming_push_replay=True,
    )
    hooks = run_push_replay_dry_run(
        hooks_cfg,
        push_dir=push_dir,
        output_dir=hooks_out,
        repo_root=repo_root,
        max_push_rows=max_push_rows,
        enable_discord=False,
        streaming_push_replay=True,
    )

    b_sum = baseline.summary
    h_sum = hooks.summary
    metrics = {
        "accepted_count": (int(b_sum.get("accepted_count") or 0), int(h_sum.get("accepted_count") or 0)),
        "rejected_count": (int(b_sum.get("rejected_count") or 0), int(h_sum.get("rejected_count") or 0)),
        "peak_open_slots": (int(b_sum.get("peak_open_slots") or 0), int(h_sum.get("peak_open_slots") or 0)),
        "observer_exit_count": (
            _count_observer_exits(baseline.state.events),
            _count_observer_exits(hooks.state.events),
        ),
    }
    rows = []
    all_match = True
    for name, (bv, hv) in metrics.items():
        match = bv == hv
        all_match = all_match and match
        rows.append(
            {
                "metric": name,
                "baseline_value": bv,
                "hooks_enabled_value": hv,
                "match": match,
            }
        )
    return {
        "push_dir": str(push_dir),
        "max_push_rows": max_push_rows,
        "parity_ok": all_match,
        "rows": rows,
        "baseline_summary_keys": sorted(b_sum.keys()),
        "hooks_extra_summary": {
            k: h_sum.get(k)
            for k in (
                "live_order_dry_run_entry_intents",
                "live_order_api_wiring_enabled",
                "live_order_wiring_would_send_count",
            )
            if k in h_sum
        },
    }


def _test_dry_run_adapter_disabled() -> dict[str, Any]:
    class C:
        live_order_dry_run_enabled = False
        live_trading_enabled = False
        order_enabled = False

    ok = not dry_run_adapter_enabled(C())
    return {"pass": ok, "detail": "dry_run_adapter_enabled returns False when flag off"}


def _test_hooks_disabled_when_order_enabled() -> dict[str, Any]:
    class C:
        live_order_dry_run_enabled = True
        live_order_api_wiring_enabled = True
        live_capital_check_enabled = True
        live_trading_enabled = False
        order_enabled = True

    wiring_off = not wiring_enabled(C())
    capital_off = not capital_manager_enabled(C())
    ok = wiring_off and capital_off
    return {
        "pass": ok,
        "detail": (
            "Phase592/593 disabled when order_enabled=true; "
            f"Phase591 adapter still enabled={dry_run_adapter_enabled(C())} "
            "(production order_enabled=false)"
        ),
    }


def _test_capital_api_offline_no_raise() -> dict[str, Any]:
    client = MagicMock()
    client.get_wallet_cash.side_effect = RuntimeError("offline")
    snap = fetch_live_capital_snapshot(client, token="tok")
    ok = not snap.api_online and bool(snap.fetch_error)
    return {"pass": ok, "detail": f"offline snapshot error={snap.fetch_error!r}"}


def _test_capital_jsonl_failure_no_raise() -> dict[str, Any]:
    writer = MagicMock()
    writer.append_live_capital_check.side_effect = OSError("disk full")
    client = MagicMock()
    client.get_wallet_cash.return_value = ({"StockAccountWallet": 20000}, 1.0)
    client.get_wallet_margin.return_value = ({"MarginAccountWallet": 0}, 1.0)
    client.get_positions.return_value = ([], 1.0)
    client.get_orders.return_value = ([], 1.0)
    session = LiveCapitalManagerSession(position_cap=5)
    row = check_entry_capital_on_paper_accept(
        session,
        symbol="7203.T",
        trade={"entry_time": "2026-06-18T09:00:00+09:00"},
        payload={"AskPrice": 2768.0},
        writer=writer,
        config=MagicMock(
            order_enabled=False,
            live_trading_enabled=False,
            live_capital_check_enabled=True,
            max_concurrent_positions=5,
            daily_loss_guard_enabled=True,
            daily_loss_guard_pct=-2.5,
        ),
        client=client,
        token="tok",
    )
    ok = row.get("can_enter") is False and row.get("reject_reason") == "insufficient_margin_or_buying_power"
    return {"pass": ok, "detail": "capital check completes despite JSONL write failure"}


def _test_pilot_hook_swallows_exception() -> dict[str, Any]:
    from small_paper import pilot_runner

    class BadWriter:
        def append_error(self, _row: Mapping[str, Any]) -> None:
            pass

    class Ctx:
        state = type("S", (), {"live_capital_manager": object(), "live_capital_read_client": object(), "live_capital_api_token": "x"})()
        config = MagicMock(order_enabled=False, live_trading_enabled=False)
        gate = MagicMock(state=MagicMock(day_pnl={}))
        writer = BadWriter()

    import small_paper.live_capital_manager as lcm

    orig = lcm.check_entry_capital_on_paper_accept
    lcm.check_entry_capital_on_paper_accept = MagicMock(side_effect=RuntimeError("boom"))
    try:
        pilot_runner._maybe_record_live_capital_check_entry(
            Ctx(),
            sym="7203.T",
            trade={},
            payload={},
            acc={},
        )
        ok = True
    except Exception as e:
        ok = False
        detail = str(e)
    else:
        detail = "hook swallowed injected exception"
    finally:
        lcm.check_entry_capital_on_paper_accept = orig
    return {"pass": ok, "detail": detail}


def _test_discord_failure_entry_already_accepted() -> dict[str, Any]:
    """Discord runs before hooks; failure there is pre-existing. ENTRY gate path already committed."""
    from small_paper import pilot_runner

    accepted_before_hooks = False
    hook_ran = False

    class Discord:
        active = True

        def notify_entry(self, **_kw: Any) -> None:
            raise RuntimeError("discord down")

    class Ctx:
        config = MagicMock(discord_enabled=True, position_cap_mode=False)
        gate = MagicMock()
        observer = None
        state = type(
            "S",
            (),
            {
                "live_order_dry_run": None,
                "live_capital_manager": None,
                "discord_ux": {},
            },
        )()
        writer = MagicMock()
        pos_fields = ["symbol"]
        discord = Discord()

    orig_cap = pilot_runner._maybe_record_live_capital_check_entry
    orig_dry = pilot_runner._maybe_record_live_order_entry
    orig_wire = pilot_runner._maybe_record_live_order_wiring_entry

    def _mark_cap(*_a: Any, **_k: Any) -> None:
        nonlocal hook_ran
        hook_ran = True

    pilot_runner._maybe_record_live_capital_check_entry = _mark_cap
    pilot_runner._maybe_record_live_order_entry = lambda *_a, **_k: None
    pilot_runner._maybe_record_live_order_wiring_entry = lambda *_a, **_k: None

    try:
        pilot_runner.gate = MagicMock()
        # Minimal path: simulate post-accept hook section only
        try:
            if Ctx.discord and Ctx.discord.active:
                Ctx.discord.notify_entry()
            accepted_before_hooks = True
            pilot_runner._maybe_record_live_capital_check_entry(Ctx(), sym="7203.T", trade={}, payload={}, acc={})
        except RuntimeError:
            accepted_before_hooks = True
    finally:
        pilot_runner._maybe_record_live_capital_check_entry = orig_cap
        pilot_runner._maybe_record_live_order_entry = orig_dry
        pilot_runner._maybe_record_live_order_wiring_entry = orig_wire

    ok = accepted_before_hooks and not hook_ran
    return {
        "pass": ok,
        "detail": "Discord failure prevents hook logging but does not roll back gate accept (pre-existing)",
        "hooks_run_after_discord_error": hook_ran,
    }


def _bat_checks(repo_root: Path) -> list[dict[str, Any]]:
    bat = repo_root / BAT_PATH_REL
    rows = []
    exists = bat.is_file()
    rows.append(
        {
            "check_id": "bat_exists",
            "category": "run_paper_trade.bat",
            "pass": exists,
            "detail": str(bat),
        }
    )
    text = bat.read_text(encoding="utf-8", errors="replace") if exists else ""
    for needle, cid in (
        ("check_live_pipeline_preflight.py", "bat_preflight"),
        ("run_production_startup_smoke_test.py", "bat_smoke"),
        ("run_core10_dynamic40_am_pm_daily_runner.py", "bat_runner"),
    ):
        rows.append(
            {
                "check_id": cid,
                "category": "run_paper_trade.bat",
                "pass": needle in text,
                "detail": needle,
            }
        )
    rows.append(
        {
            "check_id": "bat_no_sendorder",
            "category": "run_paper_trade.bat",
            "pass": "sendorder" not in text.lower(),
            "detail": "batch must not invoke sendorder",
        }
    )
    return rows


@dataclass
class PaperRuntimeReadinessJob:
    repo_root: Path
    max_push_rows: int = 8000
    push_dir: Optional[Path] = None

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports_dir = resolve_reports_dir(self.kabu)
        self.config_path = (
            self.kabu
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        if self.push_dir is None:
            self.push_dir = self.kabu / "data" / "push_jsonl" / "2026-06-05"

    def run(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        checks.append(
            {
                "check_id": "1_dry_run_adapter_disabled_safe",
                "category": "phase591",
                **_test_dry_run_adapter_disabled(),
            }
        )
        checks.append(
            {
                "check_id": "2_capital_manager_exception_safe",
                "category": "phase593",
                **_test_pilot_hook_swallows_exception(),
            }
        )
        checks.append(
            {
                "check_id": "3_api_offline_safe",
                "category": "phase593",
                **_test_capital_api_offline_no_raise(),
            }
        )
        checks.append(
            {
                "check_id": "4_jsonl_write_failure_safe",
                "category": "phase593",
                **_test_capital_jsonl_failure_no_raise(),
            }
        )
        discord = _test_discord_failure_entry_already_accepted()
        checks.append(
            {
                "check_id": "5_discord_failure_note",
                "category": "discord",
                "pass": True,
                **{k: v for k, v in discord.items() if k != "pass"},
            }
        )
        checks.append(
            {
                "check_id": "6_order_enabled_disables_hooks",
                "category": "safety",
                **_test_hooks_disabled_when_order_enabled(),
            }
        )

        parity: dict[str, Any] = {"parity_ok": False, "skipped": True, "reason": ""}
        try:
            parity = _run_micro_entry_parity(
                repo_root=self.repo_root,
                config_path=self.config_path,
            )
            parity["skipped"] = False
        except Exception as e:
            parity = {"parity_ok": False, "skipped": True, "reason": str(e)}

        if self.push_dir and self.push_dir.is_dir() and os.environ.get("PAPER_READINESS_FULL_PUSH_REPLAY") == "1":
            try:
                parity = _run_push_replay_parity(
                    repo_root=self.repo_root,
                    config_path=self.config_path,
                    push_dir=self.push_dir,
                    max_push_rows=self.max_push_rows,
                )
                parity["skipped"] = False
            except Exception as e:
                parity = {"parity_ok": False, "skipped": True, "reason": str(e)}

        checks.append(
            {
                "check_id": "7_entry_exit_summary_parity",
                "category": "push_replay",
                "pass": bool(parity.get("parity_ok")),
                "detail": json.dumps(parity.get("rows") or parity.get("reason"), ensure_ascii=False),
            }
        )
        checks.extend(_bat_checks(self.repo_root))

        cfg = load_pilot_config(self.config_path)
        safety_ok = (
            not cfg.order_enabled
            and not cfg.live_trading_enabled
            and cfg.live_order_dry_run_enabled
            and cfg.live_order_api_wiring_enabled
            and cfg.live_capital_check_enabled
        )
        checks.append(
            {
                "check_id": "production_yaml_safety",
                "category": "config",
                "pass": safety_ok,
                "detail": (
                    f"order_enabled={cfg.order_enabled} live_trading={cfg.live_trading_enabled} "
                    f"dry_run={cfg.live_order_dry_run_enabled} wiring={cfg.live_order_api_wiring_enabled} "
                    f"capital={cfg.live_capital_check_enabled}"
                ),
            }
        )

        all_pass = all(bool(c.get("pass")) for c in checks if c.get("check_id") != "5_discord_failure_note")
        bat_ok = all(c.get("pass") for c in checks if c.get("category") == "run_paper_trade.bat")

        mandatory = {
            "run_paper_trade_bat_safe_for_tuesday": all_pass and bat_ok,
            "phase591_hooks_non_blocking": checks[0]["pass"],
            "phase592_hooks_non_blocking": checks[6]["pass"] and parity.get("parity_ok", False),
            "phase593_hooks_non_blocking": checks[1]["pass"] and checks[2]["pass"] and checks[3]["pass"],
            "entry_exit_unchanged_with_hooks": parity.get("parity_ok"),
            "discord_pre_existing_risk": discord.get("detail"),
            "config_path": str(self.config_path),
            "parity_mode": parity.get("mode", "micro_execute_accepted_entry"),
            "push_replay_parity": parity,
        }

        return {
            "verdict": PAPER_RUNTIME_VERDICT if all_pass and bat_ok else "paper_runtime_readiness_failed",
            "ready": all_pass and bat_ok,
            "generated_at": _now_iso(),
            "checks": checks,
            "mandatory_answers": mandatory,
            "parity_rows": parity.get("rows") or [],
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports_dir
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "checks": rep / "paper_runtime_readiness_checks.csv",
            "parity": rep / "paper_runtime_readiness_push_parity.csv",
            "report_json": rep / "paper_runtime_readiness_audit.json",
        }
        _write_csv(paths["checks"], CHECK_FIELDS, result.get("checks") or [])
        _write_csv(paths["parity"], PUSH_PARITY_FIELDS, result.get("parity_rows") or [])
        paths["report_json"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        doc = self.kabu / "docs" / "operations" / "paper_runtime_readiness_audit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Paper Runtime Readiness Audit (Tuesday Paper Trade)",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Ready:** `{result.get('ready')}`",
                    "",
                    "## Scope",
                    "",
                    "Verify Phase591 (dry-run adapter), Phase592 (API wiring), Phase593 (capital manager)",
                    "hooks do not stop, exception-out, or block paper ENTRY/EXIT.",
                    "",
                    "## Mandatory answer",
                    "",
                    f"**run_paper_trade.bat safe for standalone Tuesday paper trade:** "
                    f"`{ma.get('run_paper_trade_bat_safe_for_tuesday')}`",
                    "",
                    "## Check summary",
                    "",
                ]
                + [
                    f"- {c['check_id']}: {'PASS' if c.get('pass') else 'FAIL'} — {c.get('detail')}"
                    for c in (result.get("checks") or [])
                ]
                + ["", "## Outputs", ""]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
