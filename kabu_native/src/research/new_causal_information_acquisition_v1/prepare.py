"""NEW_INFO prepare-only. Same-day AM universe via standard prebuild. No registration."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1 import (
    CASE_PREPARED,
    FUTURE_CODES,
    LIVE_OPERATOR_COMMAND,
    NEXT_LIVE_COMMAND,
    TOTAL_REGISTRATION_N,
)
from research.new_causal_information_acquisition_v1.exclusive import probe_exclusive
from research.new_causal_information_acquisition_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts
from research.new_causal_information_acquisition_v1.spec import file_sha256, refuse_legacy_futures_backfill, standard_config_unchanged
from research.new_causal_information_acquisition_v1.universe import build_new_info_universe
from research.new_causal_information_acquisition_v1.writer import day_layout
from small_paper.universe_prebuild import am_universe_path, run_universe_prebuild, validate_universe_sot, write_prebuild_artifact

JST = ZoneInfo("Asia/Tokyo")
PREPARE_LIVE_REGISTRATION_ATTEMPTED = False
PREPARE_UNREGISTER_N = 0


class PrepareError(RuntimeError):
    pass


def _repo_root(native_root: Path) -> Path:
    return Path(native_root).resolve().parent


def ensure_same_day_am_universe(*, native_root: Path, trading_date: str) -> dict[str, Any]:
    refuse_legacy_futures_backfill(trading_date)
    std = standard_config_unchanged()
    if not std.get("ok"):
        raise PrepareError(f"standard Paper universe config drifted: {std}")
    from small_paper.paper_trade_checked_runner import default_pythonpath

    repo = _repo_root(native_root)
    src = str(Path(native_root) / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    os.environ["PYTHONPATH"] = default_pythonpath()
    result = run_universe_prebuild(
        repo_root=_repo_root(native_root),
        native_root=Path(native_root),
        trading_date=str(trading_date),
        allow_synthetic=False,
        force_rebuild=False,
        enable_intraday_refresh=False,
    )
    try:
        write_prebuild_artifact(Path(native_root), str(trading_date), result)
    except OSError:
        pass
    path = am_universe_path(native_root, trading_date)
    val = validate_universe_sot(path, trading_date=str(trading_date), require_session="am")
    if not val.get("ok"):
        raise PrepareError(
            f"same-day AM universe FAIL CLOSED verdict={result.get('verdict')} "
            f"reason={result.get('error_reason') or val.get('reason')} path={path} "
            f"failed={val.get('failed_checks')}"
        )
    existing = "existing" if str(result.get("verdict") or "") == "existing_valid" else str(result.get("existing_or_generated") or "generated")
    if existing not in {"existing", "generated"}:
        existing = "generated" if path.is_file() else existing
    if existing not in {"existing", "generated"}:
        raise PrepareError("prior-day fallback is forbidden")
    return {
        "prebuild": result,
        "validation": val,
        "path": path,
        "existing_or_generated": existing,
        "verdict": result.get("verdict") or "am_sot_valid",
    }


def build_prepared_manifest(
    *,
    native_root: Path,
    trading_date: str,
    prebuild: Mapping[str, Any] | dict[str, Any],
) -> dict[str, Any]:
    path = Path(prebuild["path"])
    uni = build_new_info_universe(path)
    dropped = list(uni.get("dropped") or [])
    sha = file_sha256(path)
    exclusive = probe_exclusive(native_root=native_root, trading_date=trading_date)
    orders = live_order_counts()
    body = {
        "trading_date": str(trading_date),
        "source_universe_path": str(path),
        "source_universe_sha256": sha,
        "universe_prebuild_verdict": prebuild.get("verdict"),
        "existing_or_generated": prebuild.get("existing_or_generated"),
        "prior_day_fallback": False,
        "manual_selection": False,
        "core10": list(uni["core_symbols"]),
        "dynamic40": list(uni["dynamic40_symbols"]),
        "dynamic38": list(uni["dynamic38_symbols"]),
        "dropped_symbols": list(uni["dropped_dynamic_tail"]),
        "dropped_ranks": list(uni["dropped_ranks"]),
        "dropped": dropped,
        "stock_n": int(uni["stock_n"]),
        "core_n": int(uni["core_n"]),
        "dynamic40_n": int(uni["dynamic40_n"]),
        "dynamic38_n": int(uni["dynamic_n"]),
        "future_codes": list(FUTURE_CODES),
        "expected_registration_n": int(TOTAL_REGISTRATION_N),
        "duplicate_symbol_n": 0,
        "prepared_at": datetime.now(JST).isoformat(timespec="milliseconds"),
        "live_registration_attempted": False,
        "unregister_n": 0,
        "submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "sendorder_n": int(orders["sendorder_call_n"]),
        "competitor_audit": exclusive,
        "live_command": LIVE_OPERATOR_COMMAND,
    }
    dup = len(uni["stock_symbols"]) - len(set(uni["stock_symbols"]))
    body["duplicate_symbol_n"] = int(dup)
    return body


def write_prepared_manifest(*, native_root: Path, trading_date: str, body: dict[str, Any]) -> Path:
    layout = day_layout(str(trading_date), native_root=native_root)
    layout["root"].mkdir(parents=True, exist_ok=True)
    path = layout["prepared_manifest"]
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def prepare_pass_conditions(body: dict[str, Any], *, prebuild: dict[str, Any]) -> dict[str, Any]:
    val = dict(prebuild.get("validation") or {})
    checks = {
        "same_day_AM_CSV": Path(body.get("source_universe_path") or "").is_file(),
        "source_row_N_50": int(val.get("symbol_count") or 0) == 50,
        "Core_N_10": int(body.get("core_n") or 0) == 10,
        "Dynamic40_N_40": int(body.get("dynamic40_n") or 0) == 40,
        "Dynamic38_N_38": int(body.get("dynamic38_n") or 0) == 38,
        "stock_N_48": int(body.get("stock_n") or 0) == 48,
        "future_intended_N_2": list(body.get("future_codes") or []) == list(FUTURE_CODES),
        "total_intended_registration_N_50": int(body.get("expected_registration_n") or 0) == 50,
        "duplicate_symbol_N_0": int(body.get("duplicate_symbol_n") or 0) == 0,
        "manual_selection_false": body.get("manual_selection") is False,
        "prior_day_fallback_false": body.get("prior_day_fallback") is False,
        "live_registration_attempted_false": body.get("live_registration_attempted") is False,
        "unregister_N_0": int(body.get("unregister_n") or 0) == 0,
        "sendorder_N_0": int(body.get("sendorder_n") or 0) == 0,
    }
    return {"ok": all(checks.values()), "checks": checks}


def run_prepare_only(*, native_root: Optional[Path] = None, trading_date: str) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    if PREPARE_LIVE_REGISTRATION_ATTEMPTED or PREPARE_UNREGISTER_N:
        raise PrepareError("prepare-only mutated registration")
    pre = ensure_same_day_am_universe(native_root=root, trading_date=trading_date)
    body = build_prepared_manifest(native_root=root, trading_date=trading_date, prebuild=pre)
    path = write_prepared_manifest(native_root=root, trading_date=trading_date, body=body)
    gates = prepare_pass_conditions(body, prebuild=pre)
    dropped = list(body.get("dropped") or [])
    rank49 = next((d for d in dropped if int(d.get("rank") or 0) == 49), {})
    rank50 = next((d for d in dropped if int(d.get("rank") or 0) == 50), {})
    verdict = CASE_PREPARED if gates.get("ok") else "NEW_INFO_FIRST_CAPTURE_PREPARE_FAIL_V1"
    nxt = NEXT_LIVE_COMMAND if gates.get("ok") else "FIX_NEW_INFO_PREPARE_V1"
    answers = {
        "1_same_day_AM_CSV_generated_or_existing": bool(gates["checks"]["same_day_AM_CSV"]),
        "2_path": body.get("source_universe_path"),
        "3_SHA": body.get("source_universe_sha256"),
        "4_row_N": int((pre.get("validation") or {}).get("symbol_count") or 0),
        "5_Core_N": body.get("core_n"),
        "6_Dynamic40_N": body.get("dynamic40_n"),
        "7_today_dropped_rank49_symbol": rank49.get("symbol"),
        "8_today_dropped_rank50_symbol": rank50.get("symbol"),
        "9_Dynamic38_N": body.get("dynamic38_n"),
        "10_intended_total_registration_N": body.get("expected_registration_n"),
        "11_prepared_manifest_path": str(path),
        "12_prepare_only_executed": True,
        "13_live_registration_attempted": False,
        "14_unregister_N": 0,
        "15_submit_cancel_live": body.get("submit_cancel_live"),
        "16_0755_live_command_exact": LIVE_OPERATOR_COMMAND,
        "17_VERDICT": verdict,
        "18_NEXT": nxt,
    }
    return {
        "ok": bool(gates.get("ok")),
        "VERDICT": verdict,
        "NEXT": nxt,
        "answers": answers,
        "prepared_manifest_path": str(path),
        "prepared_manifest": body,
        "gates": gates,
        "prebuild": {
            "verdict": pre.get("verdict"),
            "existing_or_generated": pre.get("existing_or_generated"),
            "path": str(pre.get("path")),
        },
        "live_registration_attempted": False,
        "orders": live_order_counts(),
    }


def load_prepared_manifest(*, native_root: Path, trading_date: str) -> dict[str, Any]:
    path = day_layout(str(trading_date), native_root=native_root)["prepared_manifest"]
    if not path.is_file():
        raise PrepareError(f"prepared_manifest missing: {path}")
    body = json.loads(path.read_text(encoding="utf-8"))
    if str(body.get("trading_date") or "") != str(trading_date):
        raise PrepareError("prepared_manifest trading_date mismatch")
    return body


def verify_prepared_for_live(*, native_root: Path, trading_date: str) -> dict[str, Any]:
    body = load_prepared_manifest(native_root=native_root, trading_date=trading_date)
    src = Path(body.get("source_universe_path") or "")
    if not src.is_file():
        raise PrepareError("prepared source universe missing")
    sha = file_sha256(src)
    if sha != str(body.get("source_universe_sha256") or ""):
        raise PrepareError("source universe SHA mismatch vs prepared_manifest")
    if int(body.get("expected_registration_n") or 0) != 50:
        raise PrepareError("prepared expected_registration_n != 50")
    if body.get("live_registration_attempted") is True:
        raise PrepareError("prepared_manifest already marked live")
    return body
