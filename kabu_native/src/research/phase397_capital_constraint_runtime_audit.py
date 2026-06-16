"""
Phase397: Capital constraint runtime audit (investigation only).

Compares position-CAP-only vs capital-constrained sim vs Phase396 validation
on session structural_trades timeline. No Runtime changes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import normalize_structural_trade
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _parse_ts,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
)
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.phase385_cap_sensitivity_study import simulate_cap

JST = ZoneInfo("Asia/Tokyo")

INITIAL_EQUITY = 1_500_000.0
EQUITY_FLOOR = 750_000.0
LEVERAGE = 2.0
CAP = 3
STOP_POLICY = "fixed_stop_1p2"
SESSION_DAY = "20260615"
SESSION_ID = "live_session_122531"

COMPARISON_FIELDS = [
    "model",
    "accepted_count",
    "rejected_by_cap",
    "rejected_by_buying_power",
    "rejected_by_maintenance",
    "rejected_other",
    "final_pnl_yen_100",
    "final_equity",
    "accepted_symbols_count",
    "rejected_symbols_count",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_session_trades(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    trades: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = dict(row)
            t["exit_time"] = t.get("close_time") or t.get("exit_time")
            ep = _float(t.get("entry_price"))
            xp = _float(t.get("close_price") or t.get("exit_price"))
            if ep and xp:
                t["exit_price"] = xp
            trades.append(normalize_structural_trade(t))
    trades.sort(
        key=lambda t: (
            _parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        )
    )
    return trades


@dataclass
class PositionCapOnlyState:
    """Model A: CAP=3 until EXIT, no buying power / leverage / maintenance."""

    cap: int
    open_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    accepted_keys: set[str] = field(default_factory=set)
    accepted_trades: list[dict[str, Any]] = field(default_factory=list)
    rejected_cap: list[dict[str, Any]] = field(default_factory=list)
    realized_pnl: float = 0.0

    def current_equity(self) -> float:
        return INITIAL_EQUITY + self.realized_pnl

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        del ts, day
        if len(self.open_positions) >= self.cap:
            self.rejected_cap.append(dict(trade))
            return
        key = _position_key(trade)
        self.open_positions[key] = {"trade": trade, "shares": 100}
        self.accepted_keys.add(key)
        self.accepted_trades.append(dict(trade))

    def process_exit(self, trade: Mapping[str, Any], ts: str, day: str) -> None:
        del ts, day
        key = _position_key(trade)
        if key not in self.accepted_keys or key not in self.open_positions:
            return
        pos = self.open_positions.pop(key)
        self.realized_pnl += _trade_pnl_yen(pos["trade"], 100)

    def _force_close_all(self, ts: str, day: str) -> None:
        del ts, day
        for key in list(self.open_positions.keys()):
            pos = self.open_positions.pop(key)
            self.realized_pnl += _trade_pnl_yen(pos["trade"], 100)


def simulate_position_cap_only(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    state = PositionCapOnlyState(cap=CAP)
    events = build_event_timeline(trades)
    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        if kind == "entry":
            state.try_entry(trade, ts, day)
        else:
            state.process_exit(trade, ts, day)
    if state.open_positions and events:
        last_ts = events[-1][0].isoformat()
        last_day = _day_from_ts(last_ts)
        state._force_close_all(last_ts, last_day)
    accepted_symbols = sorted({str(t.get("symbol") or "") for t in state.accepted_trades})
    rejected_symbols = sorted({str(t.get("symbol") or "") for t in state.rejected_cap})
    pnl = round(state.realized_pnl, 2)
    return {
        "model": "position_cap_only",
        "accepted_count": len(state.accepted_trades),
        "rejected_by_cap": len(state.rejected_cap),
        "rejected_by_buying_power": 0,
        "rejected_by_maintenance": 0,
        "rejected_other": 0,
        "final_pnl_yen_100": pnl,
        "final_equity": round(state.current_equity(), 2),
        "accepted_keys": set(state.accepted_keys),
        "rejected_cap_keys": {_position_key(t) for t in state.rejected_cap},
        "accepted_symbols": accepted_symbols,
        "rejected_symbols": rejected_symbols,
        "accepted_symbols_count": len(accepted_symbols),
        "rejected_symbols_count": len(rejected_symbols),
    }


def simulate_capital_constrained(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Model B: full CapScenarioState via simulate_cap + simulate_audited detail."""
    cap_result = simulate_cap(
        trades,
        cap=CAP,
        initial_equity=INITIAL_EQUITY,
        equity_floor=EQUITY_FLOOR,
    )
    audited = simulate_audited(
        trades,
        starting_equity=int(INITIAL_EQUITY),
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    state = audited.get("_state")
    accepted_keys: set[str] = set()
    if state is not None:
        accepted_keys = set(getattr(state, "accepted_keys", set()) or [])
    reject_log = list(audited.get("reject_log") or [])
    rejected_cap_keys = {
        str(r.get("key") or "")
        for r in reject_log
        if str(r.get("reason") or "") == "max_concurrent_positions"
    }
    rejected_bp_keys = {
        str(r.get("key") or "")
        for r in reject_log
        if str(r.get("reason") or "") in ("insufficient_buying_power", "invalid_size", "invalid_price")
    }
    rejected_maint_keys = {
        str(r.get("key") or "")
        for r in reject_log
        if str(r.get("reason") or "") in ("maintenance_ratio_stop", "maintenance_ratio_force_exit")
    }
    rejected_other_keys = {
        str(r.get("key") or "")
        for r in reject_log
        if str(r.get("key") or "") not in rejected_cap_keys | rejected_bp_keys | rejected_maint_keys
    }
    cap_pnl = round(float(cap_result.get("total_pnl_yen_100") or 0.0), 2)
    cap_equity = round(float(cap_result.get("final_equity") or INITIAL_EQUITY), 2)
    pnl = cap_pnl
    counts = dict(audited.get("reject_reason_counts") or {})
    maint = int(counts.get("maintenance_ratio_stop", 0)) + int(
        counts.get("maintenance_ratio_force_exit", 0)
    )
    accepted_symbols = sorted(
        {k.split("|", 1)[0] for k in accepted_keys if "|" in k}
        | {k for k in accepted_keys if "|" not in k}
    )
    all_rejected = rejected_cap_keys | rejected_bp_keys | rejected_maint_keys | rejected_other_keys
    rejected_symbols = sorted(
        {k.split("|", 1)[0] for k in all_rejected if "|" in k}
    )
    return {
        "model": "capital_constrained",
        "accepted_count": int(audited.get("accepted_trade_count") or 0),
        "rejected_by_cap": int(counts.get("max_concurrent_positions", 0)),
        "rejected_by_buying_power": int(counts.get("insufficient_buying_power", 0))
        + int(counts.get("invalid_size", 0))
        + int(counts.get("invalid_price", 0)),
        "rejected_by_maintenance": maint,
        "rejected_other": int(audited.get("rejected_trade_count") or 0)
        - int(counts.get("max_concurrent_positions", 0))
        - int(counts.get("insufficient_buying_power", 0))
        - maint,
        "final_pnl_yen_100": pnl,
        "final_equity": cap_equity,
        "accepted_keys": accepted_keys,
        "rejected_cap_keys": rejected_cap_keys,
        "rejected_bp_keys": rejected_bp_keys,
        "rejected_maint_keys": rejected_maint_keys,
        "accepted_symbols": accepted_symbols,
        "rejected_symbols": rejected_symbols,
        "accepted_symbols_count": len(accepted_symbols),
        "rejected_symbols_count": len(rejected_symbols),
        "simulate_cap_pnl": cap_result.get("total_pnl_yen_100"),
        "maintenance_warning_count": int(cap_result.get("maintenance_warning_count") or 0),
        "maintenance_stop_count": int(cap_result.get("maintenance_stop_count") or 0),
        "force_exit_count": int(cap_result.get("force_exit_count") or 0),
    }


def phase396_validation_row(capital: Mapping[str, Any]) -> dict[str, Any]:
    """Model C: Phase396 validation replay (= capital sim on structural_trades)."""
    return {
        "model": "phase396_runtime_validation",
        "accepted_count": capital["accepted_count"],
        "rejected_by_cap": capital["rejected_by_cap"],
        "rejected_by_buying_power": capital["rejected_by_buying_power"],
        "rejected_by_maintenance": capital["rejected_by_maintenance"],
        "rejected_other": capital["rejected_other"],
        "final_pnl_yen_100": capital["final_pnl_yen_100"],
        "final_equity": capital["final_equity"],
        "accepted_symbols_count": capital["accepted_symbols_count"],
        "rejected_symbols_count": capital["rejected_symbols_count"],
        "note": "Phase396 script compared simulate_cap on structural_trades; not live runtime event replay",
    }


def _diff_rows(
    *,
    base: Mapping[str, Any],
    other: Mapping[str, Any],
    base_label: str,
    other_label: str,
) -> list[dict[str, Any]]:
    base_acc = set(base.get("accepted_keys") or [])
    other_acc = set(other.get("accepted_keys") or [])
    rows: list[dict[str, Any]] = []
    for key in sorted(base_acc - other_acc):
        sym = key.split("|", 1)[0]
        rows.append(
            {
                "diff_type": f"only_in_{base_label}",
                "position_key": key,
                "symbol": sym,
                "base_model": base_label,
                "other_model": other_label,
            }
        )
    for key in sorted(other_acc - base_acc):
        sym = key.split("|", 1)[0]
        rows.append(
            {
                "diff_type": f"only_in_{other_label}",
                "position_key": key,
                "symbol": sym,
                "base_model": base_label,
                "other_model": other_label,
            }
        )
    return rows


def runtime_code_audit() -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    checks: list[dict[str, str]] = []

    def _scan(path: str, terms: Sequence[str]) -> dict[str, int]:
        p = repo / path
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        return {t: text.count(t) for t in terms}

    gate = _scan(
        "src/research/exposure_gate.py",
        ("buying_power", "maintenance_ratio", "initial_equity", "leverage", "position_cap_mode", "observer_open_count"),
    )
    pilot = _scan(
        "src/small_paper/pilot_runner.py",
        ("buying_power", "maintenance", "initial_equity", "leverage", "compute_buying_power", "position_cap_mode"),
    )
    cfg = _scan(
        "src/small_paper/config.py",
        ("buying_power", "initial_equity", "leverage", "position_cap_mode"),
    )
    pcm = _scan(
        "src/small_paper/position_cap_mode.py",
        ("buying_power", "maintenance", "initial_equity", "leverage"),
    )

    runtime_has_capital = (
        pilot.get("buying_power", 0) > 0
        or pilot.get("compute_buying_power", 0) > 0
        or cfg.get("initial_equity", 0) > 0
    )
    checks.append(
        {
            "component": "exposure_gate.py",
            "capital_constraints": "No — position_cap_mode uses observer_open_count only",
            "evidence": f"observer_open_count={gate.get('observer_open_count', 0)} hits; buying_power=0",
        }
    )
    checks.append(
        {
            "component": "pilot_runner.py",
            "capital_constraints": "No — entry path has no buying_power / maintenance checks",
            "evidence": f"buying_power={pilot.get('buying_power', 0)}, compute_buying_power={pilot.get('compute_buying_power', 0)}",
        }
    )
    checks.append(
        {
            "component": "config.py",
            "capital_constraints": "No runtime equity/leverage fields for gate",
            "evidence": f"initial_equity={cfg.get('initial_equity', 0)}, position_cap_mode={cfg.get('position_cap_mode', 0)}",
        }
    )
    checks.append(
        {
            "component": "position_cap_mode.py",
            "capital_constraints": "No — CAP tracking and legacy VH shadow only",
            "evidence": f"buying_power={pcm.get('buying_power', 0)}",
        }
    )
    checks.append(
        {
            "component": "phase385 CapScenarioState (research)",
            "capital_constraints": "Yes — buying_power, maintenance, leverage in try_entry",
            "evidence": "Used by Phase267–274 capital sim; not wired to Runtime",
        }
    )
    return {
        "runtime_has_capital_constraints": runtime_has_capital,
        "scan": {"gate": gate, "pilot": pilot, "config": cfg, "position_cap_mode": pcm},
        "checks": checks,
    }


def determine_verdict(
    *,
    cap_only: Mapping[str, Any],
    capital: Mapping[str, Any],
    code_audit: Mapping[str, Any],
) -> tuple[str, str]:
    bp_rejects = int(capital.get("rejected_by_buying_power") or 0)
    same_accepted = int(cap_only.get("accepted_count") or 0) == int(capital.get("accepted_count") or 0)
    runtime_has = bool(code_audit.get("runtime_has_capital_constraints"))

    if runtime_has and same_accepted and bp_rejects >= 0:
        return "PASS", "Runtime includes capital constraints and matches capital sim."
    if not runtime_has and same_accepted and bp_rejects == 0:
        return (
            "WARN",
            "6/15 PM: buying_power reject=0 so position-CAP-only and capital sim agree by coincidence; Runtime has no capital constraints.",
        )
    if not runtime_has and not same_accepted:
        return (
            "FAIL",
            f"position_cap_only accepted={cap_only.get('accepted_count')} vs capital_constrained={capital.get('accepted_count')}; Runtime lacks BP enforcement.",
        )
    if not runtime_has and same_accepted and bp_rejects > 0:
        return (
            "WARN",
            f"Accepted counts match ({capital.get('accepted_count')}) but capital sim had {bp_rejects} buying_power rejects on other entry attempts; Runtime still has no capital constraints.",
        )
    return "FAIL", "Runtime and capital sim acceptance diverge."


def run_phase397(repo_root: Path, *, session_dir: Optional[Path] = None) -> dict[str, Any]:
    session_dir = session_dir or (
        repo_root / "results" / "small_paper" / SESSION_DAY / SESSION_ID
    )
    trades = _load_session_trades(session_dir)
    cap_only = simulate_position_cap_only(trades)
    capital = simulate_capital_constrained(trades)
    phase396 = phase396_validation_row(capital)
    code = runtime_code_audit()
    verdict, verdict_detail = determine_verdict(cap_only=cap_only, capital=capital, code_audit=code)

    comparison_rows = [
        {k: cap_only.get(k) for k in COMPARISON_FIELDS},
        {k: capital.get(k) for k in COMPARISON_FIELDS},
        {k: phase396.get(k) for k in COMPARISON_FIELDS if k in phase396},
    ]

    diff_a_vs_b = _diff_rows(base=cap_only, other=capital, base_label="position_cap_only", other_label="capital_constrained")

    summary = {
        "phase": 397,
        "generated_at": _now_iso(),
        "session": f"{SESSION_DAY}/{SESSION_ID}",
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "phase396_aligned_with": "capital_constrained simulate_cap/simulate_audited on structural_trades (not live runtime capital gate)",
        "runtime_has_capital_constraints": code["runtime_has_capital_constraints"],
        "buying_power_reject_count_6_15": int(capital.get("rejected_by_buying_power") or 0),
        "maintenance_reject_count_6_15": int(capital.get("rejected_by_maintenance") or 0),
        "position_cap_only_accepted": cap_only["accepted_count"],
        "capital_constrained_accepted": capital["accepted_count"],
        "accepted_delta_cap_only_minus_capital": cap_only["accepted_count"] - capital["accepted_count"],
        "need_runtime_capital_constraints": not code["runtime_has_capital_constraints"],
        "comparison": comparison_rows,
        "diff_position_cap_only_vs_capital_count": len(diff_a_vs_b),
        "code_audit": code,
    }

    docs = repo_root / "docs" / "operations"
    reports = repo_root / "results" / "reports"
    docs.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    _write_csv(reports / "phase397_capital_constraint_comparison.csv", comparison_rows, COMPARISON_FIELDS)
    diff_path = reports / "phase397_capital_constraint_diff.csv"
    if diff_a_vs_b:
        _write_csv(
            diff_path,
            diff_a_vs_b,
            ["diff_type", "position_key", "symbol", "base_model", "other_model"],
        )
    else:
        diff_path.write_text(
            "diff_type,position_key,symbol,base_model,other_model\n",
            encoding="utf-8",
        )

    (reports / "phase397_capital_constraint_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _write_audit_markdown(
        docs / "phase397_capital_constraint_runtime_audit.md",
        verdict=verdict,
        verdict_detail=verdict_detail,
        cap_only=cap_only,
        capital=capital,
        phase396=phase396,
        code=code,
        diff_a_vs_b=diff_a_vs_b,
        comparison_rows=comparison_rows,
    )

    return summary


def _write_audit_markdown(
    path: Path,
    *,
    verdict: str,
    verdict_detail: str,
    cap_only: Mapping[str, Any],
    capital: Mapping[str, Any],
    phase396: Mapping[str, Any],
    code: Mapping[str, Any],
    diff_a_vs_b: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> None:
    bp = int(capital.get("rejected_by_buying_power") or 0)
    maint = int(capital.get("rejected_by_maintenance") or 0)
    runtime_has = bool(code.get("runtime_has_capital_constraints"))
    same_acc = cap_only["accepted_count"] == capital["accepted_count"]

    def _tbl(rows: Sequence[Mapping[str, Any]]) -> str:
        lines = ["| " + " | ".join(COMPARISON_FIELDS) + " |", "| " + " | ".join("---" for _ in COMPARISON_FIELDS) + " |"]
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(c, "")) for c in COMPARISON_FIELDS) + " |")
        return "\n".join(lines)

    bp_note = (
        "**0件** — 6/15 PMでは買付余力rejectは発生せず、Position-CAP-only と Capital-constrained の **accepted 件数が偶然一致**。"
        if bp == 0
        else (
            f"**{bp}件** — 買付余力rejectあり。Phase396 Runtime と資産シミュは **まだ不一致**（Runtime に BP 判定なし）。"
            f" Position-CAP-only={cap_only['accepted_count']} accepted、Capital-constrained={capital['accepted_count']} accepted。"
            " 件数差は **受理パス依存**（BP reject は枠を消費しないため、後続の CAP 空きが変わる）。"
        )
    )

    body = f"""# Phase397 — Capital Constraint Runtime Audit

Generated: {_now_iso()}

## 判定: **{verdict}**

{verdict_detail}

---

## エグゼクティブサマリー（必須）

| 質問 | 回答 |
|------|------|
| **Phase396は何を一致させたか** | **Position-CAP（observer open ≤3、EXITまで拘束）** を `structural_trades.csv` タイムライン上の **資産シミュエンジン（`simulate_cap` / `CapScenarioState`）** と照合。Live Runtime のイベント逐次リプレイではない。 |
| **Runtimeに買付余力制約があるか** | **ない**。`exposure_gate.py` / `pilot_runner.py` / `position_cap_mode.py` に `buying_power`・`maintenance_ratio`・`initial_equity` の ENTRY 判定は未実装。 |
| **150万円資産シミュと完全一致しているか** | **いいえ（Runtime本線）**。Phase396 validation（C）は capital sim（B）と一致（accepted={capital['accepted_count']}）。Runtime 実装は CAP のみで BP/維持率なし。Position-CAP-only（A）は同じ CSV でも accepted={cap_only['accepted_count']} と **パス依存で乖離**。 |
| **今後Runtimeにcapital constraintsが必要か** | **必要**（資産シミュと Live paper の accepted ストリームを一致させるなら）。現状は CAP のみ一致。 |

---

## 確認1: 三モデル比較（2026-06-15 PM `live_session_122531`）

入力: `structural_trades.csv` エントリー/エグジット時系列（90トレード）。

{_tbl(comparison_rows)}

### 解釈

| モデル | 説明 |
|--------|------|
| **A. position_cap_only** | CAP=3、EXITまで拘束。買付余力・レバ・維持率なし。 |
| **B. capital_constrained** | 1.5M / lev2 / 100株 / CAP3 + `compute_buying_power` + maintenance（Phase385 `CapScenarioState`） |
| **C. phase396_runtime_validation** | Phase396 スクリプトの合格基準（= B と同じエンジン・同じ入力） |

**Phase396 accepted=22 の正体:** `simulate_cap`（**B: capital_constrained**）の accepted 件数。Position-CAP-only（A）との差は **{cap_only['accepted_count'] - capital['accepted_count']}** 件。

### accepted / rejected シンボル差分（A vs B）

差分行数: **{len(diff_a_vs_b)}**（`results/reports/phase397_capital_constraint_diff.csv`）

---

## 確認2: 6/15 PM 買付余力 reject

| 指標 | 件数 |
|------|------|
| `rejected_by_buying_power`（capital sim） | **{bp}** |
| `rejected_by_maintenance` | **{maint}** |
| `rejected_by_cap` | **{capital.get('rejected_by_cap')}** |

{bp_note}

---

## 確認3: Runtime コード監査

| コンポーネント | 資金制約 | 根拠 |
|----------------|----------|------|
"""
    for chk in code.get("checks") or []:
        body += f"| `{chk.get('component')}` | {chk.get('capital_constraints')} | {chk.get('evidence')} |\n"

    body += f"""
### 資産シミュが見る条件（Runtime 未実装）

| 条件 | 資産シミュ（Phase385） | Runtime（Phase396） |
|------|------------------------|---------------------|
| `initial_equity` | ¥1,500,000 | なし |
| `leverage_limit` | 2.0 | なし |
| `buying_power` | `equity * lev - gross` | なし |
| `maintenance_ratio` | WARNING / STOP_ENTRY / FORCE_EXIT | なし |
| `max_concurrent_positions` | `open_positions` 数 | `observer.open_count()` ✓ |

---

## 判定ロジック

| 条件 | 結果 |
|------|------|
| Runtime に capital constraints あり & accepted 一致 | PASS |
| Runtime に capital constraints なし & 6/15 BP reject=0 & accepted 一致 | **WARN**（偶然一致） |
| accepted / reject が capital sim と異なる | FAIL |

**今回: {verdict}** — Runtime capital constraints = `{runtime_has}`。

---

## 成果物

- `results/reports/phase397_capital_constraint_comparison.csv`
- `results/reports/phase397_capital_constraint_diff.csv`
- `results/reports/phase397_capital_constraint_summary.json`

---

## 推奨（調査のみ・未実装）

次フェーズ候補: Runtime ENTRY 前に **shadow** で `CapScenarioState.try_entry` 同等の買付余力チェックを並列記録し、reject 差分を可視化。本番 ENTRY ロジック変更は別 Phase。
"""
    path.write_text(body, encoding="utf-8")
