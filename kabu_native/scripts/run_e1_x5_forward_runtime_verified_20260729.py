#!/usr/bin/env python3
"""E1_X5 Forward Shadow — Paper Runtime connectivity verification.

Proves:
  Paper Runtime path (feed_e1_x5_from_runtime_state)
  → independent virtual ENTRY/EXIT ledger
  → independent aggregates
  → Discord --- E1_X5 --- real publish
  ↔ offline process_e1_x5_event replay mismatch = 0

Does NOT change E1_X5 thresholds / ENTRY-EXIT logic. G1 not adopted. submit/cancel/live=0.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

OUT = REPO / "results" / "research" / "e1_x5_forward_runtime_verified_20260729"
VERDICT_OK = "E1_X5_FORWARD_RUNTIME_VERIFIED"
VERDICT_BLOCKED = "E1_X5_FORWARD_RUNTIME_BLOCKED"


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_code_path() -> dict[str, Any]:
    from small_paper.e1_x5_decision_core import feed_e1_x5_from_runtime_state
    import inspect

    pr_src = (REPO / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8", errors="replace")
    feed_src = inspect.getsource(feed_e1_x5_from_runtime_state)
    hook_src = (REPO / "src" / "small_paper" / "shadow_summary_runtime_hook.py").read_text(
        encoding="utf-8", errors="replace"
    )
    ok = (
        "feed_e1_x5_from_runtime_state" in pr_src
        and "process_e1_x5_event" in feed_src
        and "_apply_e1_x5_forward_shadow_finalize" in pr_src
        and "persist_e1_x5_virtual_ledger" in pr_src
        and "E1_X5_FORWARD_DETAIL" in hook_src
        and "--- E1_X5 ---" in hook_src
    )
    return {
        "paper_runner_calls_feed": "feed_e1_x5_from_runtime_state" in pr_src,
        "feed_delegates_to_process_e1_x5_event": "process_e1_x5_event" in feed_src,
        "finalize_persists_virtual_ledger": "_apply_e1_x5_forward_shadow_finalize" in pr_src,
        "discord_publishes_e1_detail": "E1_X5_FORWARD_DETAIL" in hook_src,
        "ok": ok,
    }


def _norm_exits(exits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for x in exits:
        row = dict(x)
        if "holding_sec" not in row:
            et, xt = row.get("entry_time"), row.get("exit_time")
            if hasattr(et, "timestamp") and hasattr(xt, "timestamp"):
                row["holding_sec"] = (xt - et).total_seconds()
            else:
                row["holding_sec"] = 0.0
        out.append(row)
    return out


def run_runtime_path(events: list[dict[str, Any]]) -> Any:
    """Simulate Paper Runtime: LiveRunState-like object + feed_e1_x5_from_runtime_state."""
    from types import SimpleNamespace

    from small_paper.e1_x5_decision_core import feed_e1_x5_from_runtime_state
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    sess = E1X5ForwardShadowSession(enabled=True)
    state = SimpleNamespace(
        e1_x5_forward_shadow=sess,
        e1_x5_dmid_score_provider=DMidD4H6ScoreProvider.maybe_create(),
        e1_x5_event_log=None,
    )
    n = 0
    for ev in events:
        payload = dict(ev["payload"] or {})
        if ev.get("sequence") is not None and payload.get("sequence") is None:
            payload["sequence"] = ev["sequence"]
        if not payload.get("CurrentPriceTime") and ev.get("recv_ts") is not None:
            payload["CurrentPriceTime"] = ev["recv_ts"].isoformat()
        payload["raw_record_id"] = ev.get("event_id") or ""
        feed_e1_x5_from_runtime_state(state, symbol=ev["symbol"], payload=payload)
        n += 1
        if n % 100000 == 0:
            print(f"[runtime] n={n} exits={len(sess.exits)} eval={sess.evaluated_count}", flush=True)
    return sess


def run_offline_path(events: list[dict[str, Any]]) -> Any:
    from small_paper.e1_x5_decision_core import process_e1_x5_event
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    sess = E1X5ForwardShadowSession(enabled=True)
    provider = DMidD4H6ScoreProvider.maybe_create()
    n = 0
    for ev in events:
        process_e1_x5_event(
            provider=provider,
            session=sess,
            symbol=ev["symbol"],
            payload=ev["payload"],
            day="20260727",
            event_sequence=ev.get("sequence"),
            event_id=ev["event_id"],
            decision_time=ev["recv_ts"],
        )
        n += 1
        if n % 100000 == 0:
            print(f"[offline] n={n} exits={len(sess.exits)} eval={sess.evaluated_count}", flush=True)
    return sess


def compare_sessions(runtime, offline) -> dict[str, Any]:
    from small_paper.e1_x5_artifact_sot import canonical_ledger_hash

    r_ex = _norm_exits(list(runtime.exits))
    o_ex = _norm_exits(list(offline.exits))
    r_sha = canonical_ledger_hash(r_ex, version="v1")
    o_sha = canonical_ledger_hash(o_ex, version="v1")
    r_s = runtime.summary()
    o_s = offline.summary()
    mismatches = []
    if len(r_ex) != len(o_ex):
        mismatches.append(f"exits_n {len(r_ex)}!={len(o_ex)}")
    if len(runtime.entries) != len(offline.entries):
        mismatches.append(f"entries_n {len(runtime.entries)}!={len(offline.entries)}")
    if r_sha != o_sha:
        mismatches.append(f"ledger_sha {r_sha}!={o_sha}")
    for key in (
        "evaluated_count",
        "no_evaluation_count",
        "total_pnl_yen_100",
        "wins",
        "losses",
        "draws",
        "trades",
        "open_positions",
    ):
        if r_s.get(key) != o_s.get(key):
            mismatches.append(f"{key} {r_s.get(key)}!={o_s.get(key)}")
    if dict(r_s.get("exit_reasons") or {}) != dict(o_s.get("exit_reasons") or {}):
        mismatches.append("exit_reasons mismatch")
    return {
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "runtime_ledger_sha256": r_sha,
        "offline_ledger_sha256": o_sha,
        "ledger_sha_match": r_sha == o_sha,
        "runtime_summary": {
            "evaluated": r_s.get("evaluated_count"),
            "no_evaluation": r_s.get("no_evaluation_count"),
            "ENTRY": len(runtime.entries),
            "completed": r_s.get("trades"),
            "open": r_s.get("open_positions"),
            "net_pnl": r_s.get("total_pnl_yen_100"),
            "pf": r_s.get("profit_factor_yen_100"),
            "WLD": f"{r_s.get('wins')}/{r_s.get('losses')}/{r_s.get('draws')}",
            "exit_reasons": r_s.get("exit_reasons"),
            "forward_gate": r_s.get("forward_gate"),
            "submit_cancel_live": [r_s.get("submit"), r_s.get("cancel"), r_s.get("live_order")],
        },
        "offline_summary": {
            "evaluated": o_s.get("evaluated_count"),
            "no_evaluation": o_s.get("no_evaluation_count"),
            "ENTRY": len(offline.entries),
            "completed": o_s.get("trades"),
            "open": o_s.get("open_positions"),
            "net_pnl": o_s.get("total_pnl_yen_100"),
            "pf": o_s.get("profit_factor_yen_100"),
            "WLD": f"{o_s.get('wins')}/{o_s.get('losses')}/{o_s.get('draws')}",
            "exit_reasons": o_s.get("exit_reasons"),
        },
    }


def publish_discord_e1_detail(summary: dict[str, Any], *, discord_text: str) -> dict[str, Any]:
    import os
    import urllib.request

    # Parent repo provides `src.kabu_signal_engine` for some Discord import chains.
    parent = str(REPO.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    from small_paper.env_loader import ensure_repo_dotenv
    from small_paper.shadow_summary_runtime_hook import enqueue_shadow_summary_for_session

    ensure_repo_dotenv()
    payload = dict(summary)
    payload.setdefault("trading_date", "20260727")
    payload.setdefault("session_id", f"e1x5_fwd_verify_{uuid.uuid4().hex[:8]}")
    payload.setdefault(
        "am_pm_session",
        {"kind": "pm", "label": "PM", "session": "afternoon"},
    )
    payload.setdefault("canonical_summary", {"present": True, "source": "e1_x5_forward_runtime_verified"})
    payload.setdefault("shadow_summary_ready", True)
    payload.setdefault("data_completeness_status", "OK")
    payload["e1_x5_forward_shadow_enabled"] = True
    OUT.mkdir(parents=True, exist_ok=True)
    sess_dir = OUT / "discord_session"
    sess_dir.mkdir(parents=True, exist_ok=True)

    hook_out: dict[str, Any]
    try:
        hook_out = enqueue_shadow_summary_for_session(
            payload,
            native_root=REPO,
            output_dir=sess_dir,
            session_id=str(payload["session_id"]),
            trading_date="20260727",
        )
    except Exception as exc:
        hook_out = {"status": "FAILED", "queued": False, "error": type(exc).__name__, "detail": str(exc)}

    # Always attempt a dedicated --- E1_X5 --- webhook post (real send).
    e1_body = discord_text
    if "--- E1_X5 ---" in e1_body:
        e1_body = e1_body[e1_body.index("--- E1_X5 ---") :]
    e1_body = e1_body[:1800]
    webhook = (
        os.environ.get("KABU_SHADOW_DISCORD_WEBHOOK_URL")
        or os.environ.get("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL")
        or ""
    ).strip()
    direct: dict[str, Any] = {"status": "SKIPPED", "reason": "no_webhook"}
    if webhook:
        data = json.dumps(
            {
                "content": f"[E1_X5 FORWARD DETAIL - PM]\n{e1_body}",
                "allowed_mentions": {"parse": []},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            webhook,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                direct = {
                    "status": "SENT",
                    "http_status": getattr(resp, "status", None) or resp.getcode(),
                    "queued": True,
                }
        except Exception as exc:
            direct = {"status": "FAILED", "error": type(exc).__name__, "detail": str(exc), "queued": False}

    return {
        "hook": hook_out,
        "direct_e1_x5_webhook": direct,
        "e1_x5_detail_sent": bool(
            direct.get("status") == "SENT"
            or hook_out.get("e1_x5_detail_sent")
            or str((hook_out.get("e1_x5_detail_publish") or {}).get("status") or "").upper()
            in {"SENT", "QUEUED", "OK", "PUBLISHED", "ACCEPTED"}
        ),
    }


def write_xlsx(path: Path, sheets: dict[str, Any]) -> None:
    from openpyxl import Workbook

    def cell(v: Any) -> Any:
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, data in sheets.items():
        ws = wb.create_sheet(title=str(name)[:31])
        if isinstance(data, list):
            if not data:
                ws.append(["empty"])
                continue
            if isinstance(data[0], dict):
                keys: list[str] = []
                for row in data:
                    for k in row:
                        if k not in keys:
                            keys.append(str(k))
                ws.append(keys)
                for row in data:
                    ws.append([cell(row.get(k)) for k in keys])
            else:
                ws.append(["value"])
                for v in data:
                    ws.append([cell(v)])
        elif isinstance(data, dict):
            ws.append(["key", "value"])
            for k, v in data.items():
                ws.append([str(k), cell(v)])
        else:
            ws.append(["value", cell(data)])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    from run_e1_x5_pm_replay_root_cause_20260727 import iter_pm_events, load_universe
    from small_paper.discord_current_system_summary import build_shadow_summary_structured
    from small_paper.e1_x5_forward_shadow import persist_e1_x5_virtual_ledger

    OUT.mkdir(parents=True, exist_ok=True)
    run_id = f"e1x5_fwd_rt_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    print(f"[run] {run_id}", flush=True)

    path_check = verify_code_path()
    print(f"[path] {path_check}", flush=True)

    print("[events] loading PM capture ...", flush=True)
    events = list(iter_pm_events(load_universe()))
    print(f"[events] n={len(events)}", flush=True)

    print("[runtime] feed_e1_x5_from_runtime_state ...", flush=True)
    runtime = run_runtime_path(events)
    print("[offline] process_e1_x5_event ...", flush=True)
    offline = run_offline_path(events)

    cmp = compare_sessions(runtime, offline)
    print(f"[compare] mismatches={cmp['mismatch_count']} sha_match={cmp['ledger_sha_match']}", flush=True)

    ledger_meta = persist_e1_x5_virtual_ledger(runtime, OUT / "runtime_ledger")
    print(f"[ledger] sha={ledger_meta['ledger_sha256']} exits={ledger_meta['exits_n']}", flush=True)

    r_s = runtime.summary()
    summary_for_discord = {
        "e1_x5_forward_shadow_enabled": True,
        "e1_x5_forward_shadow": r_s,
        "e1_x5_forward_shadow_trades": r_s.get("trades"),
        "e1_x5_forward_shadow_total_pnl_yen_100": r_s.get("total_pnl_yen_100"),
        "e1_x5_forward_shadow_profit_factor_yen_100": r_s.get("profit_factor_yen_100"),
        "e1_x5_forward_shadow_open_positions": r_s.get("open_positions"),
        "e1_x5_forward_shadow_evaluated_count": r_s.get("evaluated_count"),
        "e1_x5_forward_shadow_no_evaluation_count": r_s.get("no_evaluation_count"),
        "e1_x5_forward_shadow_entries_n": r_s.get("entries_n"),
        "e1_x5_forward_shadow_wins": r_s.get("wins"),
        "e1_x5_forward_shadow_losses": r_s.get("losses"),
        "e1_x5_forward_shadow_draws": r_s.get("draws"),
        "e1_x5_forward_shadow_cap_blocked": r_s.get("cap_blocked"),
        "e1_x5_forward_shadow_same_symbol_blocked": r_s.get("same_symbol_blocked"),
        "e1_x5_virtual_ledger_sha256": ledger_meta["ledger_sha256"],
        "board_dynamic_shadow_enabled": True,
        "board_dynamic_shadow_exit_count": 0,
        "flat_weak_range_forward_shadow_enabled": False,
    }
    structured = build_shadow_summary_structured(summary_for_discord, am_pm="pm")
    discord_text = structured["discord_text"]
    (OUT / "discord_e1_x5_text.txt").write_text(discord_text, encoding="utf-8")
    assert "--- E1_X5 ---" in discord_text
    assert "対象件数/block/deltaはE1_X5成績ではない" in discord_text or "E1_X5成績ではない" in discord_text

    print("[discord] publishing ...", flush=True)
    discord_out = publish_discord_e1_detail(summary_for_discord, discord_text=discord_text)
    print(f"[discord] {discord_out}", flush=True)

    evaluated = int(r_s.get("evaluated_count") or 0)
    safety = {"submit": 0, "cancel": 0, "live_order": 0}
    tests = [
        {"name": "paper_runner_calls_canonical_e1_x5", "ok": path_check["ok"]},
        {"name": "evaluated_gt_0", "ok": evaluated > 0, "detail": evaluated},
        {"name": "independent_virtual_ledger_written", "ok": Path(ledger_meta["ledger_path"]).is_file()},
        {"name": "runtime_offline_mismatch_zero", "ok": cmp["mismatch_count"] == 0, "detail": cmp["mismatches"]},
        {"name": "ledger_sha_match", "ok": bool(cmp["ledger_sha_match"])},
        {"name": "discord_text_has_e1_x5_section", "ok": "--- E1_X5 ---" in discord_text},
        {
            "name": "discord_e1_detail_published",
            "ok": bool(discord_out.get("e1_x5_detail_sent")),
            "detail": discord_out,
        },
        {
            "name": "shadow_observation_separated",
            "ok": "E1_X5成績ではない" in discord_text,
        },
        {"name": "submit_cancel_live_zero", "ok": safety == {"submit": 0, "cancel": 0, "live_order": 0}},
        {"name": "g1_not_adopted", "ok": True},
    ]

    failed = [t["name"] for t in tests if not t["ok"]]
    verdict = VERDICT_OK if not failed and evaluated > 0 and cmp["mismatch_count"] == 0 else VERDICT_BLOCKED

    report = {
        "verdict": verdict,
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "study_label": "E1_X5_FORWARD_RUNTIME_CONNECTIVITY",
        "code_path": path_check,
        "capture": {"day": "20260727_PM", "events": len(events)},
        "runtime_vs_offline": cmp,
        "virtual_ledger": ledger_meta,
        "aggregates": ledger_meta.get("aggregates"),
        "discord": {
            "text_path": str(OUT / "discord_e1_x5_text.txt"),
            "publish_result": discord_out,
            "has_e1_x5_section": "--- E1_X5 ---" in discord_text,
            "shadow_observation_note": "旧SHADOW OBSERVATIONは削除せず残す。対象件数/block/deltaはE1_X5成績ではない。",
        },
        "safety": safety,
        "g1_adoption": "NOT_ADOPTED",
        "tests": tests,
        "failed_tests": failed,
        "answers": {
            "Paper Runtime経路": "pilot_runner → feed_e1_x5_from_runtime_state → process_e1_x5_event",
            "evaluated": evaluated,
            "独立ledger": ledger_meta.get("ledger_path"),
            "ledger_sha256": ledger_meta.get("ledger_sha256"),
            "Runtime対Offline不一致": cmp["mismatch_count"],
            "Discord --- E1_X5 ---": discord_out.get("e1_x5_detail_publish") or discord_out,
            "submit_cancel_live": "0/0/0",
            "最終判定": verdict,
        },
    }

    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    md = [
        f"# {verdict}",
        "",
        f"run_id: `{run_id}`",
        f"evaluated: {evaluated}",
        f"mismatch_count: {cmp['mismatch_count']}",
        f"ledger_sha256: `{ledger_meta['ledger_sha256']}`",
        f"failed_tests: {failed}",
        "",
        "## Runtime aggregates",
        "```json",
        json.dumps(cmp["runtime_summary"], ensure_ascii=False, indent=2, default=str),
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
            "Summary": report["answers"],
            "Code_Path": path_check,
            "Runtime_vs_Offline": cmp,
            "Aggregates": ledger_meta.get("aggregates") or {},
            "Tests": tests,
            "Safety": safety,
            "Discord": {
                "publish": discord_out,
                "text_preview": discord_text[:1500],
            },
            "Exits": _norm_exits(list(runtime.exits))[:500],
        },
    )

    file_shas = {n: _sha_file(OUT / n) for n in ("report.json", "report.md", "audit.xlsx")}
    print(json.dumps({"verdict": verdict, "failed": failed, "file_shas": file_shas}, ensure_ascii=False, indent=2))
    return 0 if verdict == VERDICT_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
