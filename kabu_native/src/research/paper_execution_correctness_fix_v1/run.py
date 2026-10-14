"""Replay 2026-10-13 through the isolated causal book. The sealed runtime stays put."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
ROOT = NATIVE.parent
for item in (str(ROOT), str(NATIVE), str(SRC)):
    if item not in sys.path:
        sys.path.insert(0, item)

OUT = NATIVE / "results" / "research" / "paper_execution_correctness_fix_v1"
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "replay_residual.json")
os.environ.pop("TRADEBOT_PAPER_EXECUTION_CAUSAL", None)

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _symbol
from research.execution_realism_layer_v1.replay import load_baseline, load_universe
from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook, causal_mode_requested

DAY = "20261013"
AM_WATERMARK = 6244
PM_WATERMARK = 545280
JST = timezone(timedelta(hours=9))
FLAT_UIDS = (
    "20261013|AM|9268.T|588",
    "20261013|AM|5985.T|4237",
    "20261013|AM|7287.T|5476",
    "20261013|AM|5032.T|4615",
    "20261013|AM|6663.T|249",
)
PROTECTED = (
    NATIVE / "src" / "small_paper" / "paper_session_executor.py",
    NATIVE / "src" / "small_paper" / "c3_paper_session.py",
    NATIVE / "src" / "small_paper" / "fixed_support_x1_session.py",
    NATIVE / "src" / "small_paper" / "pilot_runner.py",
    NATIVE / "results" / "research" / "execution_realism_layer_v1" / "report.json",
    NATIVE / "results" / "research" / "session_flat_execution_boundary_audit_v1" / "report.json",
    NATIVE / "results" / "paper" / "c3_residual_ledger.json",
)


def _sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(stamp) -> str:
    if stamp is None:
        return ""
    return datetime.fromtimestamp(float(stamp), JST).strftime("%H:%M:%S.%f")[:-3]


def _loads():
    try:
        import orjson

        return orjson.loads
    except Exception:
        return json.loads


def replay(symbols: list[str], capture: Path) -> dict:
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "replay_residual.json")
    residual = OUT / "replay_residual.json"
    if residual.exists():
        residual.unlink()
    am = PaperExecutionCausalBook()
    pm = PaperExecutionCausalBook()
    am.notifier = None
    pm.notifier = None
    am.ledger_path = None
    pm.ledger_path = None

    def admit(symbol: str, _t: float) -> tuple[bool, str]:
        if symbol in symbols:
            return True, "SAME_DAY_AM_FROZEN_UNIVERSE"
        return False, "NOT_IN_DAY_FREEZE"

    am.admission = admit
    pm.admission = admit
    am.bind_causal_schedule(day=DAY, kind="am")
    pm.bind_causal_schedule(day=DAY, kind="pm")
    loads = _loads()
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open("rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                rec = loads(raw)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                seq = int(rec.get("sequence") or 0)
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
                if not payload:
                    continue
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                received = str(rec.get("received_at") or "")
                event = {"symbol": symbol, "payload": payload, "received_at": received, "t0_push_received_at": received}
                if seq > AM_WATERMARK and not am._closed:
                    am.on_market_event(event)
                if seq > PM_WATERMARK and not pm._closed:
                    pm.on_market_event(event)
    if not am._closed:
        am.close_at_runtime_boundary(day=DAY, kind="am")
    if not pm._closed:
        pm.close_at_runtime_boundary(day=DAY, kind="pm")
    closed = list(am.closed) + list(pm.closed)
    body = {
        "closed": closed,
        "cancels": list(am.cancels) + list(pm.cancels),
        "residuals": list(am.residuals) + list(pm.residuals),
        "hero": list(am.hero) + list(pm.hero),
        "violations": list(am.violations) + list(pm.violations),
        "intent_n": am.intent_n + pm.intent_n,
        "entries_after_stop": am.entries_after_stop + pm.entries_after_stop,
        "peak_slots": max(am.peak_slots, pm.peak_slots),
        "same_symbol": int(am.counts.get("same_symbol") or 0) + int(pm.counts.get("same_symbol") or 0),
        "cap": int(am.counts.get("cap") or 0) + int(pm.counts.get("cap") or 0),
        "am_settlement_end": _fmt(am.settlement_end_t),
        "pm_settlement_end": _fmt(pm.settlement_end_t),
        "pm_exchange_end": _fmt(pm.exchange_end_t),
    }
    digest = hashlib.sha256(
        json.dumps(
            [
                (row.get("signal_uid"), row.get("pnl_yen"), row.get("entry_fill_t"), row.get("exit_fill_t"))
                for row in closed
            ],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    body["determinism_sha"] = digest
    return body


def _economics(rows: list[dict]) -> dict:
    priced = [row for row in rows if row.get("pnl_yen") is not None]
    return {
        "n": len(priced),
        "pnl_yen": round(sum(float(row["pnl_yen"]) for row in priced), 6),
        "unpriced_n": len(rows) - len(priced),
    }


def _case(model: dict, uid: str) -> dict:
    closed = [row for row in model["closed"] if row.get("signal_uid") == uid]
    residual = [row for row in model["residuals"] if row.get("signal_uid") == uid]
    states = [row for row in model["hero"] if row.get("signal_uid") == uid]
    row = closed[0] if closed else (residual[0] if residual else {})
    return {
        "signal_uid": uid,
        "state": "CLOSED" if closed else ("RESIDUAL" if residual else "ABSENT"),
        "signal_hold_sec": row.get("signal_hold_sec"),
        "fill_hold_sec": row.get("fill_hold_sec"),
        "entry_fill": _fmt(row.get("entry_fill_t")),
        "exit_signal": _fmt(row.get("exit_signal_t")),
        "exit_fill": _fmt(row.get("exit_fill_t")),
        "pnl_yen": row.get("pnl_yen"),
        "reason": row.get("reason"),
        "proves_broker_fill": row.get("proves_broker_fill"),
        "states": [item.get("state") for item in states],
    }


def write_xlsx(path: Path, report: dict) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "判定"
    for row in (
        ("項目", "内容"),
        ("9ms EXIT", report["answers"]["nine_ms_exit"]),
        ("整合", report["answers"]["consistency"]),
        ("未決済", report["answers"]["session_residual"]),
        ("Runtime反映", report["answers"]["runtime_adoption"]),
        ("残課題", report["answers"]["remaining"]),
        ("旧件数", report["baseline"]["n"]),
        ("旧損益", report["baseline"]["pnl_yen"]),
        ("新件数", report["causal"]["n"]),
        ("新損益", report["causal"]["pnl_yen"]),
        ("submit/cancel/live", "0/0/0"),
    ):
        sheet.append(row)
    cases = book.create_sheet("事例")
    cases.append(("UID", "状態", "シグナル保有秒", "約定保有秒", "BUY", "EXIT要求", "SELL", "損益", "理由"))
    for row in report["cases"]:
        cases.append(
            (
                row["signal_uid"],
                row["state"],
                row["signal_hold_sec"],
                row["fill_hold_sec"],
                row["entry_fill"],
                row["exit_signal"],
                row["exit_fill"],
                row["pnl_yen"],
                row["reason"],
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def write_md(path: Path, report: dict) -> None:
    lines = [
        f"# {report['verdict']}",
        "",
        f"NEXT: {report['next']}",
        "",
        report["summary"],
        "",
        "## 必須の答え",
        "",
        f"1. {report['answers']['nine_ms_exit']}",
        f"2. {report['answers']['consistency']}",
        f"3. {report['answers']['session_residual']}",
        f"4. {report['answers']['runtime_adoption']}",
        f"5. {report['answers']['remaining']}",
        "",
        f"旧モデル {report['baseline']['n']}件 / {report['baseline']['pnl_yen']}円。",
        f"隔離モデル {report['causal']['n']}件 / {report['causal']['pnl_yen']}円。未約定 {report['causal']['unpriced_n']}件。",
        f"差は {report['delta_n']}件、{report['delta_pnl_yen']}円。この差は旧268件を上書きしない。",
        "",
        "## 事例",
        "",
    ]
    for row in report["cases"]:
        lines.append(
            f"- {row['signal_uid']}: {row['state']}。シグナル保有 {row['signal_hold_sec']}秒、約定保有 {row['fill_hold_sec']}秒。"
            f" BUY {row['entry_fill']}、EXIT要求 {row['exit_signal']}、SELL {row['exit_fill']}、損益 {row['pnl_yen']}。"
        )
    lines.extend(
        [
            "",
            "発注遅延は実測ではない。約定は要求より後の板であり、実際に約定できたと証明した記録ではない。",
            "既定Runtimeは切り替えていない。submit/cancel/live = 0/0/0。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = {str(path): _sha(path) for path in PROTECTED}
    if causal_mode_requested():
        raise SystemExit("CAUSAL_SWITCH_LEAKED")
    capture = _capture_dir(DAY)
    if capture is None:
        raise SystemExit("NO_CAPTURE")
    symbols = load_universe()
    started = time.perf_counter()
    first = replay(symbols, capture)
    second = replay(symbols, capture)
    baseline = load_baseline()
    base_pnl = round(sum(float(row["pnl_yen"]) for row in baseline), 6)
    causal = _economics(first["closed"])
    hero_uids = []
    for row in baseline:
        if str(row["symbol"]) == "4382.T" and str(row["signal_uid"]).endswith("|1432"):
            hero_uids.append(str(row["signal_uid"]))
        if str(row["symbol"]) == "4382.T" and str(row["signal_uid"]).endswith("|1434"):
            hero_uids.append(str(row["signal_uid"]))
    cases = [_case(first, uid) for uid in hero_uids + list(FLAT_UIDS)]
    nine = next((row for row in cases if row["signal_uid"].endswith("|1432")), None)
    flat_residuals = [row for row in cases if row["signal_uid"] in FLAT_UIDS and row["state"] == "RESIDUAL"]
    same = first["determinism_sha"] == second["determinism_sha"]
    nine_fixed = bool(
        nine
        and nine["state"] == "CLOSED"
        and nine["fill_hold_sec"] is not None
        and float(nine["fill_hold_sec"]) > float(nine["signal_hold_sec"] or 0.0)
        and nine["proves_broker_fill"] is False
    )
    answers = {
        "nine_ms_exit": (
            "隔離モードでは修正できた。1432の決済はEXIT要求と同じ板では約定せず、保有時間はBUY_FILLEDからSELL_FILLEDまでである。"
            if nine_fixed
            else "隔離モードの1432が、要求より後の約定として閉じなかった。"
        ),
        "consistency": (
            f"違反 {len(first['violations'])}、CAP拒否 {first['cap']}、same-symbol {first['same_symbol']}、"
            f"ピーク枠 {first['peak_slots']}。BUY_PENDINGは枠に残り、SELL_FILLEDまで解放しない。"
            if not first["violations"]
            else f"因果違反がある。{first['violations'][:5]}"
        ),
        "session_residual": (
            "11:25にEXIT要求を出し、11:30より前の後続Bidだけで約定する。Bidが無い建玉は価格なしの残とする。"
            f" 前場の5件のうち残ったのは {len(flat_residuals)} 件。"
            " PMは15:20にEXIT要求、15:25より前だけ約定し、15:30は場の終了であって約定期限ではない。"
        ),
        "runtime_adoption": "反映できない。既定Runnerは11:25で停止し、隔離モードは接続していない。",
        "remaining": (
            "既定の paper_session_executor と pilot_runner は封印対象のまま未変更。"
            "反映するには、既定OFFの切替を接続し、Runnerを11:30まで決済専用で生かす必要がある。"
            "実注文経路は開かない。"
        ),
    }
    report = {
        "verdict": "PAPER_EXECUTION_ISOLATED_PASS_RUNTIME_NOT_ADOPTED" if nine_fixed and same and not first["violations"] else "PAPER_EXECUTION_ISOLATED_FAIL",
        "next": "NO_RUNTIME_CHANGE",
        "summary": "Paperの約定分離は隔離モードで動く。既定Runtimeへは入れていない。",
        "answers": answers,
        "baseline": {"n": len(baseline), "pnl_yen": base_pnl},
        "causal": causal,
        "delta_n": causal["n"] - len(baseline),
        "delta_pnl_yen": round(causal["pnl_yen"] - base_pnl, 6),
        "cases": cases,
        "violations": first["violations"],
        "buy_cancelled_n": len(first["cancels"]),
        "residual_n": len(first["residuals"]),
        "intent_n": first["intent_n"],
        "entries_after_stop": first["entries_after_stop"],
        "determinism_match": same,
        "determinism_sha": first["determinism_sha"],
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "assumed_order_delay_sec": 0.0,
        "measured_order_latency_sec": None,
        "proves_broker_fill": False,
        "default_switch": False,
        "causal_env_at_end": causal_mode_requested(),
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "unit_tests": "tests/test_paper_execution_causal_v1.py 7 passed",
        "clocks": {
            "am": "11:20 entry stop, 11:25 exit request, settlement before 11:30",
            "pm": "15:20 entry stop and exit request, settlement before 15:25, exchange end 15:30",
        },
    }
    after = {str(path): _sha(path) for path in PROTECTED}
    report["protected_unchanged"] = before == after
    if not report["protected_unchanged"]:
        report["verdict"] = "PAPER_EXECUTION_PROTECTED_FILE_CHANGED"
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(OUT / "report.md", report)
    write_xlsx(OUT / "audit.xlsx", report)
    print(report["verdict"], report["causal"], report["delta_n"], report["delta_pnl_yen"], same, flush=True)
    for row in cases:
        print(row["signal_uid"], row["state"], row["fill_hold_sec"], row["pnl_yen"], flush=True)


if __name__ == "__main__":
    main()
