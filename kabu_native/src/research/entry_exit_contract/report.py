"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(str(name)[:31])
        if not rows:
            ws.append(["empty"])
            continue
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.iterdir():
        if p.is_file() and p.name not in ("report.md", "report.json", "audit.xlsx"):
            p.unlink()
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    st = p.get("strategies") or {}
    ports = ((p.get("cap5") or {}).get("portfolios") or {})
    lines = [
        "# Entry–Exit Contract Strategy Study",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final_verdict: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        "## 結論",
        "",
        v.get("summary") or "",
        "",
        f"- warmup: {p.get('warmup_day')} / OOS: {p.get('oos_days')}",
        f"- entry counts OOS: {p.get('entry_counts_oos')}",
        f"- coverage: {p.get('coverage')}",
        "",
        "## Strategy M0/M1/M2",
        "",
    ]
    for sid in ("EC1", "EC2", "EC3"):
        s = st.get(sid) or {}
        lines.append(f"### {sid} pairing={s.get('pairing')}")
        for mode in ("M0", "M1", "M2"):
            m = s.get(mode) or {}
            lines.append(
                f"- {mode}: n={m.get('n')} pnl5={m.get('total_pnl_5bps')} PF={m.get('PF_5bps')} "
                f"success={m.get('contract_success_rate')} capture={m.get('mean_mfe_capture')}"
            )
        lines.append("")
    lines += ["## CAP=5 Portfolios", ""]
    for pid in ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        c = ports.get(pid) or {}
        lines.append(
            f"- {pid}: accepted={c.get('accepted')} pnl5={c.get('pnl_5bps')} PF={c.get('PF_5bps')} "
            f"cap_blocked={c.get('cap_blocked')} tradeDD={c.get('max_dd_trade_sequence')}"
        )
    lines += [
        "",
        "## Safety",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')}",
        f"- PBv2+X6 = DIAGNOSTIC ONLY",
        "",
        "## 本線採用しない理由",
        "",
        v.get("no_production_reason") or "",
        "OOS < 10営業日のため ENTRY_EXIT_CONTRACT_INSUFFICIENT_OOS。",
        "",
    ]
    return "\n".join(lines) + "\n"


def _mode_sheet(st: Mapping[str, Any], sid: str, mode: str) -> list[dict[str, Any]]:
    m = ((st.get(sid) or {}).get(mode) or {})
    base = [{k: m.get(k) for k in m if k != "sample_rows"}]
    samples = m.get("sample_rows") or []
    return base + [{**{"section": "sample"}, **{k: r.get(k) for k in (
        "day", "symbol", "entry_time", "exit_reason", "pnl_5bps", "classification", "hold_sec", "mfe_capture_ratio"
    )}} for r in samples[:30]]


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    st = p.get("strategies") or {}
    ports = ((p.get("cap5") or {}).get("portfolios") or {})
    samples = p.get("contract_samples") or {}
    cov = p.get("coverage") or {}

    def empty():
        return [{"status": "empty"}]

    consistency = []
    violations = []
    for sid in ("EC1", "EC2", "EC3"):
        for r in ((st.get(sid) or {}).get("M2") or {}).get("sample_rows") or []:
            consistency.append(
                {
                    "strategy_id": sid,
                    "setup_id": r.get("setup_id"),
                    "exit_contract_consistent": r.get("exit_contract_consistent"),
                    "classification": r.get("classification"),
                    "exit_reason": r.get("exit_reason"),
                }
            )
            if r.get("contract_violation_reason"):
                violations.append({"strategy_id": sid, "reason": r.get("contract_violation_reason"), "setup_id": r.get("setup_id")})

    exec_rows = []
    slip_rows = []
    for sid in ("EC1", "EC2", "EC3"):
        for r in ((st.get(sid) or {}).get("M2") or {}).get("sample_rows") or []:
            ex = r.get("execution") or {}
            exec_rows.append({"strategy_id": sid, "symbol": r.get("symbol"), **{k: ex.get(k) for k in (
                "status", "bid_at_decision", "bid_qty", "sellable_100", "spread_bps", "next_push_mode", "bid_500ms_mode"
            )}})
            slip_rows.append({"strategy_id": sid, "symbol": r.get("symbol"), **{k: ex.get(k) for k in (
                "pnl_1tick_slip", "pnl_2tick_slip", "pnl_500ms_delay", "pnl_1s_delay", "tick_size"
            )}})

    daily = []
    for sid in ("EC1", "EC2", "EC3"):
        dep = ((st.get(sid) or {}).get("M2") or {}).get("dependency") or {}
        daily.append({"strategy": sid, **dep})

    return {
        "README": [{"title": "Entry-Exit Contract", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "DATA_SOURCES": [{"role": k, "path": v} for k, v in (p.get("sot") or {}).items()] or empty(),
        "SOURCE_AUDIT": [p.get("discovery") or {"status": "empty"}],
        "CONTRACT_SPEC": [
            {"strategy": "EC1", "name": "Volume Breakout Contract"},
            {"strategy": "EC2", "name": "Pullback Reclaim Contract"},
            {"strategy": "EC3", "name": "Compression Breakout Contract"},
            {"strategy": "PBv2", "name": "DIAGNOSTIC_ONLY"},
        ],
        "FEATURE_LINEAGE": [
            {"feature": "micro_high_*", "rule": "excludes current bar", "source": "VCIE _features_fast"},
            {"feature": "range_high_*", "rule": "excludes current bar", "source": "VCIE _features_fast"},
            {"feature": "volume_impulse_*", "rule": "causal baseline windows", "source": "VCIE"},
            {"feature": "invalidation_level", "rule": "frozen at ENTRY", "source": "EntryContract.levels"},
        ],
        "FEATURE_COVERAGE": (
            [{"strategy": k, **v} for k, v in (cov.get("per_strategy") or {}).items()]
            or [{"status": "empty"}]
        ),
        "ENTRY_COHORTS": [{"strategy": k, "n_all": v, "n_oos": (p.get("entry_counts_oos") or {}).get(k)} for k, v in (p.get("entry_counts") or {}).items()],
        "ENTRY_CONTRACTS": (samples.get("EC1") or [])[:40] + (samples.get("EC2") or [])[:20] + (samples.get("EC3") or [])[:20] or empty(),
        "EXIT_CONTRACTS": [
            {"strategy": sid, "profit_exit": ((samples.get(sid) or [{}])[0] if samples.get(sid) else {}).get("profit_exit_definition")}
            for sid in ("EC1", "EC2", "EC3")
        ],
        "CONTRACT_CONSISTENCY": consistency or empty(),
        "CONTRACT_VIOLATIONS": violations or [{"status": "none"}],
        "EC1_VOLUME_BREAKOUT": [{"pairing": (st.get("EC1") or {}).get("pairing"), **{k: ((st.get("EC1") or {}).get("M2") or {}).get(k) for k in ("n", "contract_success_rate", "total_pnl_5bps", "PF_5bps")}}],
        "EC1_CURRENT_EXIT": _mode_sheet(st, "EC1", "M0"),
        "EC1_GENERIC_X6": _mode_sheet(st, "EC1", "M1"),
        "EC1_MATCHED_EXIT": _mode_sheet(st, "EC1", "M2"),
        "EC2_PULLBACK_RECLAIM": [{"pairing": (st.get("EC2") or {}).get("pairing"), **{k: ((st.get("EC2") or {}).get("M2") or {}).get(k) for k in ("n", "contract_success_rate", "total_pnl_5bps", "PF_5bps")}}],
        "EC2_CURRENT_EXIT": _mode_sheet(st, "EC2", "M0"),
        "EC2_GENERIC_X6": _mode_sheet(st, "EC2", "M1"),
        "EC2_MATCHED_EXIT": _mode_sheet(st, "EC2", "M2"),
        "EC3_COMPRESSION": [{"pairing": (st.get("EC3") or {}).get("pairing"), **{k: ((st.get("EC3") or {}).get("M2") or {}).get(k) for k in ("n", "contract_success_rate", "total_pnl_5bps", "PF_5bps")}}],
        "EC3_CURRENT_EXIT": _mode_sheet(st, "EC3", "M0"),
        "EC3_GENERIC_X6": _mode_sheet(st, "EC3", "M1"),
        "EC3_MATCHED_EXIT": _mode_sheet(st, "EC3", "M2"),
        "EXPECTED_PATH": [{"strategy": sid, "rate": ((st.get(sid) or {}).get("M2") or {}).get("expected_horizon_achieved_rate")} for sid in ("EC1", "EC2", "EC3")],
        "INVALIDATION": [{"strategy": sid, "rate": ((st.get(sid) or {}).get("M2") or {}).get("invalidation_rate"), "latency": ((st.get(sid) or {}).get("M2") or {}).get("mean_invalidation_to_exit_sec")} for sid in ("EC1", "EC2", "EC3")],
        "EXIT_LATENCY": [{"strategy": sid, "mean_inv_to_exit": ((st.get(sid) or {}).get("M2") or {}).get("mean_invalidation_to_exit_sec")} for sid in ("EC1", "EC2", "EC3")],
        "MFE_MAE": [{"strategy": sid, "mean_mfe": ((st.get(sid) or {}).get("M2") or {}).get("mean_mfe_5bps")} for sid in ("EC1", "EC2", "EC3")],
        "MFE_CAPTURE": [{"strategy": sid, "mean_capture": ((st.get(sid) or {}).get("M2") or {}).get("mean_mfe_capture")} for sid in ("EC1", "EC2", "EC3")],
        "FALSE_INVALIDATION": [{"strategy": sid, "n": ((st.get(sid) or {}).get("M2") or {}).get("false_invalidation_n")} for sid in ("EC1", "EC2", "EC3")],
        "LOST_WINNERS": [{"strategy": sid, "n": ((st.get(sid) or {}).get("M2") or {}).get("lost_winner_n")} for sid in ("EC1", "EC2", "EC3")],
        "SAME_EPISODE_REGRET": [{"strategy": sid, "mean": ((st.get(sid) or {}).get("M2") or {}).get("mean_same_episode_regret")} for sid in ("EC1", "EC2", "EC3")],
        "EXECUTION_REALISM": exec_rows or empty(),
        "SLIPPAGE": slip_rows or empty(),
        "CAP5_EVENT_LOG": (p.get("cap5") or {}).get("event_log") or empty(),
        "CAP5_RESULTS": [ports[k] for k in ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7") if k in ports] or empty(),
        "PORTFOLIO_COMPARISON": [{"portfolio": k, **ports[k]} for k in ports] or empty(),
        "DAILY_RESULTS": daily or empty(),
        "SYMBOL_DEPENDENCY": [{"strategy": sid, **(((st.get(sid) or {}).get("M2") or {}).get("dependency") or {})} for sid in ("EC1", "EC2", "EC3")],
        "DAY_DEPENDENCY": daily or empty(),
        "WALK_FORWARD": [{"warmup": p.get("warmup_day"), "oos": ",".join(p.get("oos_days") or []), "thresholds": p.get("thresholds")}],
        "VERDICT": [p.get("verdict") or {"final": "ENTRY_EXIT_CONTRACT_OFFLINE_ONLY"}],
    }
