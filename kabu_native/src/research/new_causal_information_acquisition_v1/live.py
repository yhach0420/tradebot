"""NEW_INFO live capture. 07:55-11:30 only. Fixed 12-step start. No sendorder."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1 import (
    CASE_FIRST_FULL,
    CASE_FUTURES_RESOLVE_FAIL,
    CASE_LIVE_REG_FAIL,
    CASE_PARTIAL,
    CASE_TRANSPORT_FAIL,
    NEXT_ACCUMULATE,
    WINDOW_END_HM,
)
from research.new_causal_information_acquisition_v1.completeness import evaluate_day
from research.new_causal_information_acquisition_v1.exclusive import ExclusiveBlocked, require_exclusive
from research.new_causal_information_acquisition_v1.futures_resolve import FuturesResolveError, resolve_both
from research.new_causal_information_acquisition_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import in_acquisition_window, live_order_counts
from research.new_causal_information_acquisition_v1.prepare import verify_prepared_for_live
from research.new_causal_information_acquisition_v1.register_plan import apply_registration_if_exclusive, build_plan
from research.new_causal_information_acquisition_v1.spec import refuse_legacy_futures_backfill
from research.new_causal_information_acquisition_v1.universe import build_new_info_universe
from research.new_causal_information_acquisition_v1.writer import ContextCaptureSession, day_layout

JST = ZoneInfo("Asia/Tokyo")

LIVE_START_SEQUENCE = (
    "1_trading_date_verify",
    "2_window_0755_1130_verify",
    "3_prepared_manifest_verify",
    "4_source_universe_sha_verify",
    "5_competitor_process_verify",
    "6_standard_paper_opval_cert_absent",
    "7_NK225mini_derivmonth_0_resolve",
    "8_TOPIX_derivmonth_0_resolve",
    "9_resolved_symbol_board_rest_preflight",
    "10_exact_registration_pack_48_plus_2",
    "11_registration_mutation",
    "12_websocket_capture_start",
)


class LiveStartError(RuntimeError):
    def __init__(self, verdict: str, message: str) -> None:
        super().__init__(message)
        self.verdict = verdict


def _write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _issue_token_as_sole_station_client(*, native_root: Path, trading_date: str, session_id: str) -> tuple[Any, Any, str]:
    """Issue token only after exclusive. This process is the sole Station client, not Paper Ingress."""
    from api.push_client import KabuNativePushClient
    from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env
    from small_paper.kabu_token_authority import owner_issue_context, publish_owned_token

    load_kabu_env(repo_root=Path(native_root).parent)
    load_kabu_env(repo_root=Path(native_root))
    rest = KabuNativeRestClient(default_base_url())
    with owner_issue_context(
        native_root=Path(native_root),
        trading_date=str(trading_date),
        pid=os.getpid(),
        session_id=str(session_id),
        caller="new_info_context_capture",
    ):
        token = rest.issue_token_from_env()
    publish_owned_token(
        token,
        native_root=Path(native_root),
        trading_date=str(trading_date),
        caller="new_info_context_capture",
    )
    push = KabuNativePushClient(rest, token)
    return rest, push, token


def run_live_capture(
    *,
    native_root: Optional[Path] = None,
    trading_date: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fixed 12-step live start. Forbidden before 07:55. No sendorder."""
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    clock = now or datetime.now(JST)
    steps: list[dict[str, Any]] = []

    def mark(name: str, **extra: Any) -> None:
        if name not in LIVE_START_SEQUENCE:
            raise LiveStartError(CASE_LIVE_REG_FAIL, f"unknown live step {name}")
        expected = LIVE_START_SEQUENCE[len(steps)]
        if name != expected:
            raise LiveStartError(CASE_LIVE_REG_FAIL, f"live sequence violation: got {name} expected {expected}")
        steps.append({"step": name, **extra})
        extra_s = " ".join(f"{k}={v}" for k, v in extra.items() if k != "competitors")
        print(f"LIVE_STEP {name} {extra_s}", flush=True)

    refuse_legacy_futures_backfill(day)
    today = datetime.now(JST).strftime("%Y%m%d")
    if day != today:
        raise LiveStartError(CASE_LIVE_REG_FAIL, f"trading_date {day} != today {today}")
    mark("1_trading_date_verify", trading_date=day)

    if not in_acquisition_window(clock):
        raise LiveStartError(CASE_LIVE_REG_FAIL, "NEW_INFO --live is 07:55-11:30 JST only")
    mark("2_window_0755_1130_verify", window_ok=True)

    prepared = verify_prepared_for_live(native_root=root, trading_date=day)
    mark("3_prepared_manifest_verify", path=str(day_layout(day, native_root=root)["prepared_manifest"]))
    mark("4_source_universe_sha_verify", sha=prepared.get("source_universe_sha256"))

    try:
        exclusive = require_exclusive(native_root=root, trading_date=day)
    except ExclusiveBlocked as exc:
        raise LiveStartError(CASE_LIVE_REG_FAIL, str(exc)) from exc
    mark("5_competitor_process_verify", competitors=exclusive.get("competitors") or [])
    if exclusive.get("paper_simultaneous") or exclusive.get("opval_simultaneous") or exclusive.get("certification_mode"):
        raise LiveStartError(CASE_LIVE_REG_FAIL, "Paper/OPVAL/Cert present")
    mark(
        "6_standard_paper_opval_cert_absent",
        paper=False,
        opval=False,
        cert=bool(exclusive.get("certification_mode")),
        owner_class=(exclusive.get("registration_owner_class") or {}).get("class"),
    )

    session_id = f"new_info_{day}_{os.getpid()}"
    layout = day_layout(day, native_root=root)
    layout["root"].mkdir(parents=True, exist_ok=True)
    layout["new_info_pid"].write_text(str(os.getpid()), encoding="utf-8")

    try:
        rest, push, _token = _issue_token_as_sole_station_client(
            native_root=root, trading_date=day, session_id=session_id
        )
        resolved = resolve_both(rest, token=_token)
    except FuturesResolveError as exc:
        raise LiveStartError(CASE_FUTURES_RESOLVE_FAIL, str(exc)) from exc
    except Exception as exc:
        raise LiveStartError(CASE_FUTURES_RESOLVE_FAIL, f"{type(exc).__name__}:{exc}") from exc

    nk = resolved.get("nk225mini") or {}
    tx = resolved.get("topix") or {}
    mark("7_NK225mini_derivmonth_0_resolve", symbol=nk.get("resolved_symbol"), deriv_month=0)
    mark("8_TOPIX_derivmonth_0_resolve", symbol=tx.get("resolved_symbol"), deriv_month=0)
    mark(
        "9_resolved_symbol_board_rest_preflight",
        nk=bool((nk.get("board_preflight") or {}).get("ok")),
        topix=bool((tx.get("board_preflight") or {}).get("ok")),
    )

    uni = build_new_info_universe(Path(prepared["source_universe_path"]))
    plan = build_plan(stock_symbols=uni["stock_symbols"], contracts=resolved["contracts"])
    if int(plan["total_n"]) != 50:
        raise LiveStartError(CASE_LIVE_REG_FAIL, f"pack n={plan['total_n']}")
    mark("10_exact_registration_pack_48_plus_2", total_n=plan["total_n"], stock_n=48, futures_n=2)

    try:
        applied = apply_registration_if_exclusive(
            push,
            plan,
            native_root=root,
            trading_date=day,
            isolated_state_root=layout["station_state"],
        )
    except ExclusiveBlocked as exc:
        raise LiveStartError(CASE_LIVE_REG_FAIL, str(exc)) from exc
    except Exception as exc:
        raise LiveStartError(CASE_LIVE_REG_FAIL, f"{type(exc).__name__}:{exc}") from exc
    if not applied.get("ok"):
        raise LiveStartError(CASE_LIVE_REG_FAIL, f"registration failed: {applied}")
    mark("11_registration_mutation", regist_n=applied.get("symbol_count"), unregistered_competitor=False)

    session = ContextCaptureSession(native_root=root, trading_date=day, session_id=session_id)
    session.bind_contracts(resolved["by_code"])
    live_manifest = {
        "mode": "NEW_INFO_DEV_CONSTRUCTION_ONLY",
        "trading_date": day,
        "prepared_sha": prepared.get("source_universe_sha256"),
        "futures": resolved["contracts"],
        "registration_specs": plan["specs"],
        "live_start_sequence": [s["step"] for s in steps] + ["12_websocket_capture_start"],
        "orders": live_order_counts(),
        "started_at": datetime.now(JST).isoformat(timespec="milliseconds"),
    }
    _write_json(layout["live_manifest"], live_manifest)
    session.start()
    mark("12_websocket_capture_start", websocket=True)
    assert [s["step"] for s in steps] == list(LIVE_START_SEQUENCE)
    _write_json(
        layout["status"],
        {
            "at": datetime.now(JST).isoformat(timespec="milliseconds"),
            "phase": "websocket_started",
            "stats": {"stock_n": 0, "nk_n": 0, "topix_n": 0, "push_n": 0},
            "orders": live_order_counts(),
            "nk_symbol": nk.get("resolved_symbol"),
            "topix_symbol": tx.get("resolved_symbol"),
            "window_end": "11:30",
        },
    )
    print("LIVE_CAPTURE_RUNNING until 11:30 JST", flush=True)

    stats = {"stock_n": 0, "nk_n": 0, "topix_n": 0, "push_n": 0}
    try:
        stats = asyncio.run(
            _consume_until_1130(
                push=push,
                session=session,
                resolved=resolved,
                layout=layout,
                stats=stats,
            )
        )
    except Exception as exc:
        session.stop()
        _finalize(root, day, session, push, registered=True, transport_error=str(exc))
        raise LiveStartError(CASE_TRANSPORT_FAIL, str(exc)) from exc

    return _finalize(root, day, session, push, registered=True, transport_error="", stats=stats, steps=steps)


async def _consume_until_1130(*, push: Any, session: Any, resolved: dict[str, Any], layout: dict[str, Path], stats: dict[str, int]) -> dict[str, int]:
    end = time(*WINDOW_END_HM)
    nk_sym = str((resolved.get("nk225mini") or {}).get("resolved_symbol") or "")
    tx_sym = str((resolved.get("topix") or {}).get("resolved_symbol") or "")
    first: dict[str, Any] = {}
    async for payload in push.iter_messages(recv_poll_sec=5.0):
        now = datetime.now(JST)
        if now.time() > end:
            break
        received_at = now.isoformat(timespec="milliseconds")
        kind = session.ingest_push(payload, received_at=received_at)
        stats["push_n"] = int(stats.get("push_n") or 0) + 1
        raw = str((payload or {}).get("Symbol") or "").split("@", 1)[0]
        bucket = None
        if kind == "stock":
            stats["stock_n"] = int(stats.get("stock_n") or 0) + 1
            bucket = "stock"
        elif kind == "futures":
            if raw == nk_sym:
                stats["nk_n"] = int(stats.get("nk_n") or 0) + 1
                bucket = "nk"
            elif raw == tx_sym:
                stats["topix_n"] = int(stats.get("topix_n") or 0) + 1
                bucket = "topix"
        if bucket and bucket not in first:
            buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else {}
            sell = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else {}
            rec = {
                "received_at": received_at,
                "Symbol": raw,
                "CurrentPrice": payload.get("CurrentPrice"),
                "Bid1": buy.get("Price") if isinstance(buy, dict) else None,
                "Ask1": sell.get("Price") if isinstance(sell, dict) else None,
                "TradingVolume": payload.get("TradingVolume"),
            }
            first[bucket] = rec
            print(f"FIRST_PUSH {bucket} {json.dumps(rec, ensure_ascii=False, default=str)}", flush=True)
            _write_json(layout["root"] / "first_push.json", first)
        if stats["push_n"] == 1 or stats["push_n"] % 20 == 0:
            _write_json(
                layout["status"],
                {
                    "at": received_at,
                    "stats": stats,
                    "orders": live_order_counts(),
                    "first": {k: v.get("received_at") for k, v in first.items()},
                    "window_end": "11:30",
                },
            )
    return stats


def _finalize(
    native_root: Path,
    day: str,
    session: ContextCaptureSession,
    push: Any,
    *,
    registered: bool,
    transport_error: str,
    stats: Optional[dict[str, int]] = None,
    steps: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    session.stop()
    unreg_n = 0
    if registered:
        try:
            push.unregister_all()
            unreg_n = 1
        except Exception:
            unreg_n = 0
    ev = evaluate_day(day, native_root=native_root)
    if ev.get("FULL"):
        verdict = CASE_FIRST_FULL
        nxt = NEXT_ACCUMULATE
    elif transport_error:
        verdict = CASE_TRANSPORT_FAIL
        nxt = "RETRY_NEW_INFO_LIVE_CAPTURE_NEXT_WINDOW_V1"
    else:
        verdict = CASE_PARTIAL
        nxt = "RETRY_OR_CONTINUE_TOWARD_FULL_NEW_INFO_DAY_V1"
    layout = day_layout(day, native_root=native_root)
    final = {
        "trading_date": day,
        "VERDICT": verdict,
        "NEXT": nxt,
        "FULL": bool(ev.get("FULL")),
        "capture": ev,
        "stats": stats or {},
        "steps": steps or [],
        "transport_error": transport_error,
        "unregister_on_stop": unreg_n,
        "switched_to_standard_paper": False,
        "orders": live_order_counts(),
        "finalized_at": datetime.now(JST).isoformat(timespec="milliseconds"),
    }
    _write_json(layout["root"] / "final_manifest.json", final)
    return final
