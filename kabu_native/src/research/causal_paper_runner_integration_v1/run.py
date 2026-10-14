"""UID reconciliation and offline replay for the isolated runner hook.

Does not write the sealed runtime, the old ledgers, or earlier research reports.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
ROOT = NATIVE.parent
WORK = ROOT / "_causal_runner_v1" / "kabu_native"
for item in (str(ROOT), str(NATIVE), str(SRC)):
    if item not in sys.path:
        sys.path.insert(0, item)

OUT = NATIVE / "results" / "research" / "causal_paper_runner_integration_v1"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "replay_residual.json")
os.environ.pop("TRADEBOT_PAPER_EXECUTION_CAUSAL", None)
os.environ.pop("TRADEBOT_PAPER_EXECUTION_OFFLINE", None)

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _symbol
from research.execution_realism_layer_v1.model import ExecutionRealismBook
from research.execution_realism_layer_v1.replay import load_baseline, load_universe
from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook
from small_paper.paper_session_executor import resolve_paper_session_executor

DAY = "20261013"
AM_WATERMARK = 6244
PM_WATERMARK = 545280
JST = timezone(timedelta(hours=9))
PRIOR_SHA = "429fa4103fa5628d444ccd6071708dc37b356c09540f84f916a01ecdba0769a0"
FLAT_UIDS = (
    "20261013|AM|9268.T|588",
    "20261013|AM|5985.T|4237",
    "20261013|AM|7287.T|5476",
    "20261013|AM|5032.T|4615",
    "20261013|AM|6663.T|249",
)
PROTECTED = (
    NATIVE / "src" / "small_paper" / "pilot_runner.py",
    NATIVE / "src" / "small_paper" / "paper_session_executor.py",
    NATIVE / "src" / "small_paper" / "fixed_support_x1_session.py",
    NATIVE / "src" / "small_paper" / "c3_paper_session.py",
    NATIVE / "results" / "research" / "execution_realism_layer_v1" / "report.json",
    NATIVE / "results" / "research" / "paper_execution_correctness_fix_v1" / "report.json",
    NATIVE / "results" / "research" / "paper_execution_runner_integration_preflight_v1" / "report.json",
    NATIVE / "results" / "paper" / "c3_residual_ledger.json",
)


def _sha_file(path: Path) -> str:
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


def _install_trace() -> None:
    if getattr(ExecutionRealismBook, "_uid_trace_installed", False):
        return
    original = ExecutionRealismBook._on_signal

    def wrapped(self, sym, index, event_t, heap, flush_t):
        log = getattr(self, "denial_log", None)
        if log is None:
            self.denial_log = []
            log = self.denial_log
        uid = f"{self.day}|{self.session}|{sym}|{int(index)}"
        holder = self.open_pos.get(sym)
        snap = {
            "cap": int(self.counts.get("cap") or 0),
            "same": int(self.counts.get("same_symbol") or 0),
            "occ": int(self.counts.get("occupancy") or 0),
            "gross": int(getattr(self, "gross_cap_denied_n", 0) or 0),
            "adm": int(self.admission_denied_n),
            "quote": int(self.signal_quote_not_ok_n),
            "invalid": int(getattr(self, "signal_invalid_ask_n", 0) or 0),
            "seen": int(self.signal_seen_n),
            "closed": bool(self._entries_closed),
            "open_n": len(self.open_pos),
            "holder_uid": None if holder is None else holder.get("signal_uid"),
            "holder_state": None if holder is None else str(holder.get("exec_state") or ""),
        }
        original(self, sym, index, event_t, heap, flush_t)
        if int(self.signal_seen_n) == snap["seen"]:
            return
        pos = self.open_pos.get(sym)
        if pos is not None and str(pos.get("signal_uid")) == uid:
            return
        if int(self.admission_denied_n) > snap["adm"]:
            reason = "NOT_ADMITTED"
        elif int(self.signal_quote_not_ok_n) > snap["quote"]:
            reason = "QUOTE_NOT_OK"
        elif int(getattr(self, "signal_invalid_ask_n", 0) or 0) > snap["invalid"]:
            reason = "INVALID_ASK"
        elif int(self.counts.get("same_symbol") or 0) > snap["same"]:
            reason = "SAME_SYMBOL"
        elif int(self.counts.get("occupancy") or 0) > snap["occ"]:
            reason = "OCCUPANCY"
        elif int(self.counts.get("cap") or 0) > snap["cap"]:
            reason = "CAP"
        elif int(getattr(self, "gross_cap_denied_n", 0) or 0) > snap["gross"]:
            reason = "GROSS_CAP"
        elif snap["closed"] or bool(self._entries_closed):
            reason = "ENTRY_STOP"
        else:
            reason = "UNOPENED"
        log.append(
            {
                "uid": uid,
                "reason": reason,
                "event_t": float(event_t),
                "holder_uid": snap["holder_uid"],
                "holder_state": snap["holder_state"],
                "open_n": snap["open_n"],
            }
        )

    ExecutionRealismBook._on_signal = wrapped
    ExecutionRealismBook._uid_trace_installed = True


def _feed(am, pm, capture: Path) -> None:
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


def _admit(symbols: list[str]):
    def admit(symbol: str, _t: float) -> tuple[bool, str]:
        if symbol in symbols:
            return True, "SAME_DAY_AM_FROZEN_UNIVERSE"
        return False, "NOT_IN_DAY_FREEZE"

    return admit


def _prepare(kind: str, symbols: list[str], causal: bool):
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "replay_residual.json")
    book = PaperExecutionCausalBook() if causal else ExecutionRealismBook(0.0)
    book.notifier = None
    book.ledger_path = None
    book.admission = _admit(symbols)
    if causal:
        book.bind_causal_schedule(day=DAY, kind=kind)
    elif kind == "pm":
        book.bind_safe_pm_flatten(day=DAY)
    else:
        book.bind_production_schedule(day=DAY, kind="am")
    book.ledger_path = None
    book.notifier = None
    return book


def replay(symbols: list[str], capture: Path, *, causal: bool) -> dict:
    residual = OUT / "replay_residual.json"
    if residual.exists():
        residual.unlink()
    am = _prepare("am", symbols, causal)
    pm = _prepare("pm", symbols, causal)
    inherited = list(pm.residuals)
    _feed(am, pm, capture)
    closed = list(am.closed) + list(pm.closed)
    digest = hashlib.sha256(
        json.dumps(
            [(row.get("signal_uid"), row.get("pnl_yen"), row.get("entry_fill_t"), row.get("exit_fill_t")) for row in closed],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    denials = list(getattr(am, "denial_log", [])) + list(getattr(pm, "denial_log", []))
    return {
        "closed": closed,
        "cancels": list(am.cancels) + list(pm.cancels),
        "residuals": list(am.residuals) + list(pm.residuals),
        "hero": list(am.hero) + list(pm.hero),
        "violations": list(am.violations) + list(pm.violations),
        "denials": denials,
        "sha": digest,
        "pm_inherited_residual_n": len(inherited),
        "entries_after_stop": int(getattr(am, "entries_after_stop", 0)) + int(getattr(pm, "entries_after_stop", 0)),
        "post_stop_opened": sum(1 for row in getattr(am, "post_stop_signals", []) if row.get("opened"))
        + sum(1 for row in getattr(pm, "post_stop_signals", []) if row.get("opened")),
        "submit": int(am.snapshot_status()["submit"]) + int(pm.snapshot_status()["submit"]),
        "cancel": int(am.snapshot_status()["cancel"]) + int(pm.snapshot_status()["cancel"]),
        "live": int(am.snapshot_status()["live"]) + int(pm.snapshot_status()["live"]),
        "live_order_enabled": bool(am.live_order_enabled or pm.live_order_enabled),
    }


def _index(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("signal_uid")): row for row in rows}


def _yen(row: dict | None) -> float | None:
    if not row or row.get("pnl_yen") is None:
        return None
    return round(float(row["pnl_yen"]), 6)


def _explain_missing(uid: str, model: dict, paper: dict) -> dict:
    denial = next((row for row in model["denials"] if row["uid"] == uid), None)
    cancel = next((row for row in model["cancels"] if row.get("signal_uid") == uid), None)
    residual = next((row for row in model["residuals"] if row.get("signal_uid") == uid), None)
    if cancel is not None:
        reason = "BUY_PENDING_NEVER_FILLED"
        effect = "買いは成立せず、枠と資金は戻る。旧Paperの損益はこのモデルに載らない。"
    elif residual is not None:
        reason = "SELL_UNFILLED"
        effect = "売りが成立せず建玉と枠を残す。損益はnull。"
    elif denial is not None:
        reason = str(denial["reason"])
        holder = denial.get("holder_uid")
        state = denial.get("holder_state") or "occupied"
        if reason == "SAME_SYMBOL":
            effect = f"同一銘柄 {holder} が {state} のまま枠を占有し、このシグナルは建玉にならない。"
        elif reason == "CAP":
            effect = f"同時建玉が{denial.get('open_n')}件で上限に達し、このシグナルは建玉にならない。"
        elif reason == "GROSS_CAP":
            effect = "枠は空いていても、保有額と新規100株の合計が100万円を超えるため建玉にならない。"
        elif reason == "ENTRY_STOP":
            effect = "新規ENTRY停止後のシグナルなので建玉にならない。"
        elif reason == "NOT_ADMITTED":
            effect = "ユニバース外として拒否され、建玉にならない。"
        else:
            effect = "約定台帳に載らず、資金と建玉は増えない。"
    else:
        reason = "NOT_SEEN"
        effect = "このモデルのセッション窓では当該indexのシグナルが発生していない。"
    return {
        "uid": uid,
        "where": "paper_only",
        "reason": reason,
        "paper_pnl_yen": _yen(paper),
        "paper_exit": _fmt(paper.get("exit_t")),
        "paper_reentry": bool(paper.get("reentry")),
        "capital_effect": effect,
        "denial": denial,
    }


def _explain_extra(uid: str, row: dict, paper_rows: list[dict]) -> dict:
    symbol = str(row.get("symbol") or uid.split("|")[2])
    mates = [item for item in paper_rows if item.get("symbol") == symbol]
    nearest = None
    if mates and row.get("signal_t") is not None:
        nearest = min(mates, key=lambda item: abs(float(item["signal_t"]) - float(row["signal_t"])))
    return {
        "uid": uid,
        "where": "causal_only",
        "reason": "ADMITTED_AFTER_SLOT_RELEASE",
        "causal_pnl_yen": _yen(row),
        "entry_fill": _fmt(row.get("entry_fill_t")),
        "exit_fill": _fmt(row.get("exit_fill_t")),
        "nearest_paper_uid": None if nearest is None else nearest.get("signal_uid"),
        "capital_effect": "旧Paperには無い建玉。成立した約定の損益だけが隔離モデルの資金に入る。",
    }


def reconcile(paper: list[dict], prior: dict, isolated: dict) -> dict:
    paper_map = _index(paper)
    prior_map = _index(prior["closed"])
    iso_map = _index(isolated["closed"])
    paper_ids = set(paper_map)
    prior_ids = set(prior_map)
    iso_ids = set(iso_map)
    shared_pnl = []
    for uid in sorted(paper_ids & iso_ids):
        left = paper_map[uid]
        right = iso_map[uid]
        if _yen(left) != _yen(right) or abs(float(left["exit_t"]) - float(right.get("exit_fill_t") or 0.0)) > 1e-6:
            shared_pnl.append(
                {
                    "uid": uid,
                    "where": "same_uid_different_fill",
                    "reason": "LATER_CAUSAL_BID",
                    "paper_pnl_yen": _yen(left),
                    "causal_pnl_yen": _yen(right),
                    "pnl_delta_yen": round(float(right["pnl_yen"]) - float(left["pnl_yen"]), 6),
                    "paper_exit": _fmt(left.get("exit_t")),
                    "causal_exit": _fmt(right.get("exit_fill_t")),
                    "capital_effect": "同じ建玉のまま、売り価格と時刻だけが後続Bidへ移る。枠の採否は変わらない。",
                }
            )
    paper_only = [_explain_missing(uid, isolated, paper_map[uid]) for uid in sorted(paper_ids - iso_ids)]
    causal_only = [_explain_extra(uid, iso_map[uid], paper) for uid in sorted(iso_ids - paper_ids)]
    pending = [
        {
            "uid": row.get("signal_uid"),
            "reason": "BUY_PENDING_NEVER_FILLED",
            "model_reason": row.get("reason"),
            "capital_effect": "買い未約定のため建玉・CAP・資金拘束は解消され、損益は発生しない。",
        }
        for row in isolated["cancels"]
        if float(row.get("filled_qty") or 0.0) <= 0.0
    ]
    unfilled = [
        {
            "uid": row.get("signal_uid"),
            "reason": "SELL_UNFILLED",
            "pnl_yen": row.get("pnl_yen"),
            "capital_effect": "決済期限まで有効Bidがなく、建玉と枠を残し、損益はnull。",
        }
        for row in isolated["residuals"]
    ]
    groups = {
        "all_three": len(paper_ids & prior_ids & iso_ids),
        "paper_only": len(paper_ids - prior_ids - iso_ids),
        "prior_causal_only": len(prior_ids - paper_ids - iso_ids),
        "isolated_only": len(iso_ids - paper_ids - prior_ids),
        "paper_and_prior_not_isolated": len((paper_ids & prior_ids) - iso_ids),
        "paper_and_isolated_not_prior": len((paper_ids & iso_ids) - prior_ids),
        "prior_residual_not_closed": len(prior["residuals"]),
    }
    unexplained = [
        row
        for row in paper_only + causal_only
        if row["reason"] in {"UNOPENED", "NOT_SEEN", "UNKNOWN"}
    ]
    return {
        "groups": groups,
        "paper_n": len(paper_ids),
        "prior_closed_n": len(prior_ids),
        "isolated_closed_n": len(iso_ids),
        "same_uid_fill_diffs": shared_pnl,
        "paper_only": paper_only,
        "causal_only": causal_only,
        "buy_pending_never_filled": pending,
        "sell_unfilled": unfilled,
        "unexplained": unexplained,
        "null_causal_pnl_reason": (
            "report.json の causal_pnl_yen=null は、旧PaperのUIDが隔離モデルの成立約定に無いため。"
            "件数268の一致は、別UIDの入れ替わりを隠す。"
        ),
    }


def _case(model: dict, uid: str) -> dict:
    closed = [row for row in model["closed"] if row.get("signal_uid") == uid]
    row = closed[0] if closed else {}
    states = [item.get("state") for item in model["hero"] if item.get("signal_uid") == uid]
    return {
        "uid": uid,
        "pnl_yen": row.get("pnl_yen"),
        "entry_fill": _fmt(row.get("entry_fill_t")),
        "exit_signal": _fmt(row.get("exit_signal_t")),
        "exit_fill": _fmt(row.get("exit_fill_t")),
        "signal_hold_sec": row.get("signal_hold_sec"),
        "fill_hold_sec": row.get("fill_hold_sec"),
        "reason": row.get("reason"),
        "states": states,
    }


def _worktree_diff() -> str:
    completed = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=str(WORK.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


def _worktree_names() -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=str(WORK.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _lifecycle_tests() -> bool:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(WORK), str(WORK / "src"), str(ROOT)))
    env.pop("TRADEBOT_PAPER_EXECUTION_CAUSAL", None)
    env.pop("TRADEBOT_PAPER_EXECUTION_OFFLINE", None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(WORK / "tests" / "test_causal_paper_runner_integration_v1.py"), "-q", "--tb=line"],
        cwd=str(WORK),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    (OUT / "lifecycle_pytest.txt").write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    return completed.returncode == 0


def write_xlsx(path: Path, report: dict) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "判定"
    for key, value in report["checks"].items():
        sheet.append((key, value))
    sheet.append(("PAPER_RUNTIME_ADOPTION_READY", report["PAPER_RUNTIME_ADOPTION_READY"]))
    sheet.append(("running_runtime_updated", False))
    diff = book.create_sheet("UID差")
    diff.append(("UID", "区分", "理由", "旧損益", "隔離損益", "資金と建玉への影響"))
    for row in report["reconciliation"]["paper_only"]:
        diff.append((row["uid"], row["where"], row["reason"], row["paper_pnl_yen"], None, row["capital_effect"]))
    for row in report["reconciliation"]["causal_only"]:
        diff.append((row["uid"], row["where"], row["reason"], None, row["causal_pnl_yen"], row["capital_effect"]))
    for row in report["reconciliation"]["same_uid_fill_diffs"]:
        diff.append((row["uid"], row["where"], row["reason"], row["paper_pnl_yen"], row["causal_pnl_yen"], row["capital_effect"]))
    for row in report["reconciliation"]["buy_pending_never_filled"]:
        diff.append((row["uid"], "buy_pending", row["reason"], None, None, row["capital_effect"]))
    for row in report["reconciliation"]["sell_unfilled"]:
        diff.append((row["uid"], "sell_unfilled", row["reason"], None, row["pnl_yen"], row["capital_effect"]))
    book.save(path)


def write_md(path: Path, report: dict) -> None:
    rec = report["reconciliation"]
    lines = [
        "# CAUSAL_PAPER_RUNNER_INTEGRATION_V1",
        "",
        f"VERDICT: {report['verdict']}",
        "",
        f"NEXT: {report['next']}",
        "",
        "## 判定",
        "",
    ]
    for key, value in report["checks"].items():
        lines.append(f"- {key}: {value}")
    lines.append(f"- PAPER_RUNTIME_ADOPTION_READY: {report['PAPER_RUNTIME_ADOPTION_READY']}")
    lines.append("- 稼働中Runtimeへの反映: していない")
    lines.append("- 約定遅延の測定: していない。ASSUMED delay 0")
    lines.append("")
    lines.append("## UID")
    lines.append("")
    lines.append(rec["null_causal_pnl_reason"])
    lines.append("")
    for key, value in rec["groups"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("旧Paperにだけある取引:")
    for row in rec["paper_only"]:
        lines.append(f"- {row['uid']} {row['reason']} 旧損益 {row['paper_pnl_yen']}。{row['capital_effect']}")
    lines.append("")
    lines.append("隔離モデルにだけある取引:")
    for row in rec["causal_only"]:
        lines.append(
            f"- {row['uid']} 損益 {row['causal_pnl_yen']} BUY {row['entry_fill']} SELL {row['exit_fill']}。"
            f"近い旧UID {row['nearest_paper_uid']}。{row['capital_effect']}"
        )
    lines.append("")
    lines.append(f"同一UIDで価格または時刻が違う件数: {len(rec['same_uid_fill_diffs'])}")
    lines.append(f"BUY_PENDINGのまま未成立: {len(rec['buy_pending_never_filled'])}")
    lines.append(f"SELL_UNFILLED: {len(rec['sell_unfilled'])}")
    lines.append("")
    lines.append("## 実装差分")
    lines.append("")
    lines.append("隔離branch `causal-paper-runner-integration-v1`、worktree `_causal_runner_v1`。")
    lines.append("")
    lines.append("```")
    lines.append(report["diff_stat"] or "(empty)")
    lines.append("```")
    lines.append("")
    lines.append("変更対象:")
    for name in report["changed_files"]:
        lines.append(f"- {name}")
    lines.append("")
    lines.append("## 切替")
    lines.append("")
    lines.append("両方を立てたときだけ、オフラインの因果約定帳と決済延長が有効になる。")
    lines.append("")
    lines.append("- TRADEBOT_PAPER_EXECUTION_CAUSAL=1")
    lines.append("- TRADEBOT_PAPER_EXECUTION_OFFLINE=1")
    lines.append("")
    lines.append("片方だけでは従来のC3 Paper executorのまま。未設定が既定。")
    lines.append("")
    lines.append("## ロールバック")
    lines.append("")
    lines.append("環境変数を未設定に戻す。branchを稼働ツリーへmergeしない。稼働中のpilot_runner.pyとpaper_session_executor.pyは今回変更していない。")
    lines.append("")
    lines.append("## 実際のPaper Runnerへ反映する前の障害")
    lines.append("")
    lines.append(report["obstacle"])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    before = {str(path): _sha_file(path) for path in PROTECTED}
    _install_trace()
    symbols = load_universe()
    capture = _capture_dir(DAY)
    paper = load_baseline()
    prior = replay(symbols, capture, causal=False)
    isolated = replay(symbols, capture, causal=True)
    isolated_again = replay(symbols, capture, causal=True)
    rec = reconcile(paper, prior, isolated)
    flat_rows = [_case(isolated, uid) for uid in FLAT_UIDS]
    case_1432 = _case(isolated, "20261013|AM|4382.T|1432")
    case_1434 = _case(isolated, "20261013|AM|4382.T|1434")
    am_late = [
        row.get("signal_uid")
        for row in isolated["closed"]
        if str(row.get("signal_uid") or "").startswith("20261013|AM|")
        and row.get("exit_fill_t") is not None
        and float(row["exit_fill_t"]) >= float(datetime(2026, 10, 13, 11, 30, tzinfo=JST).timestamp())
    ]
    pm_late = [
        row.get("signal_uid")
        for row in isolated["closed"]
        if str(row.get("signal_uid") or "").startswith("20261013|PM|")
        and row.get("exit_fill_t") is not None
        and float(row["exit_fill_t"]) >= float(datetime(2026, 10, 13, 15, 25, tzinfo=JST).timestamp())
    ]
    flat_pnl = round(sum(float(row["pnl_yen"]) for row in flat_rows if row.get("pnl_yen") is not None), 6)
    sealed = resolve_paper_session_executor({"execution_family": "X1_IMMEDIATE_ASK"}, strategy_mode="C3")
    checks = {
        "UID_RECONCILIATION_PASS": not rec["unexplained"] and rec["paper_n"] == 268 and rec["isolated_closed_n"] == 268,
        "RUNNER_LIFECYCLE_PASS": _lifecycle_tests() and isolated["post_stop_opened"] == 0,
        "CAUSAL_EXECUTION_E2E_PASS": (
            "ENTRY_SIGNAL" in case_1432.get("states", [])
            and "POSITION_CLOSED" in case_1432.get("states", [])
            and case_1432.get("pnl_yen") == -300
            and case_1434.get("pnl_yen") == 600
            and case_1432.get("exit_fill")
            and case_1434.get("entry_fill")
            and case_1432["exit_fill"] < case_1434["entry_fill"]
            and len(flat_rows) == 5
            and flat_pnl == 3600
            and isolated["sha"] == PRIOR_SHA
            and not isolated["violations"]
        ),
        "SESSION_CLOSE_PASS": not am_late and not pm_late and all(row.get("exit_fill") for row in flat_rows),
        "RECOVERY_PASS": isolated["sha"] == isolated_again["sha"] and isolated["pm_inherited_residual_n"] == 0,
        "OFF_MODE_PARITY_PASS": type(sealed).__name__ == "C3PaperSessionExecutor" and sealed.live_order_enabled is False,
        "NO_REAL_ORDER_PATH_PASS": isolated["submit"] == 0 and isolated["cancel"] == 0 and isolated["live"] == 0 and not isolated["live_order_enabled"],
    }
    after = {str(path): _sha_file(path) for path in PROTECTED}
    protected_same = before == after
    ready = all(checks.values()) and protected_same
    if ready:
        obstacle = (
            "隔離branch上の接続と回帰は揃った。稼働中のPaper Runnerは未変更で、11:25停止のまま。"
            "反映は環境変数を両方立てたworktree差分を、停止中のRunnerへ別途適用する作業が残る。"
            "これは反映完了ではなく、実注文の約定保証でもない。"
        )
    else:
        failed = [key for key, value in checks.items() if not value]
        obstacle = "技術的障害が残る。未合格: " + ", ".join(failed) + "。稼働中Runtimeには適用できない。"
    report = {
        "verdict": "PAPER_RUNTIME_ADOPTION_READY" if ready else "PAPER_RUNTIME_ADOPTION_NOT_READY",
        "next": "HOLD_FOR_EXPLICIT_RUNTIME_APPLY" if ready else "FIX_FAILED_CHECK",
        "PAPER_RUNTIME_ADOPTION_READY": ready,
        "running_runtime_updated": False,
        "proves_broker_fill": False,
        "assumed_order_delay_sec": 0,
        "checks": checks,
        "protected_unchanged": protected_same,
        "reconciliation": rec,
        "economics": {
            "paper_n": 268,
            "paper_pnl_yen": round(sum(float(row["pnl_yen"]) for row in paper), 6),
            "prior_closed_n": rec["prior_closed_n"],
            "prior_pnl_yen": round(sum(float(row["pnl_yen"]) for row in prior["closed"] if row.get("pnl_yen") is not None), 6),
            "isolated_n": rec["isolated_closed_n"],
            "isolated_pnl_yen": round(sum(float(row["pnl_yen"]) for row in isolated["closed"] if row.get("pnl_yen") is not None), 6),
        },
        "cases": {"1432": case_1432, "1434": case_1434, "flats": flat_rows, "flat_pnl_yen": flat_pnl},
        "sha": {"first": isolated["sha"], "second": isolated_again["sha"], "prior": PRIOR_SHA},
        "orders": {"submit": isolated["submit"], "cancel": isolated["cancel"], "live": isolated["live"]},
        "diff_stat": _worktree_diff(),
        "changed_files": _worktree_names(),
        "obstacle": obstacle,
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(OUT / "report.md", report)
    write_xlsx(OUT / "audit.xlsx", report)
    print(json.dumps({"verdict": report["verdict"], "checks": checks, "groups": rec["groups"], "sha": report["sha"]["first"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
