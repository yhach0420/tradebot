#!/usr/bin/env python3
"""
Phase 15: kabu_native shadow 実行前の安全チェック。

例::
    python kabu_native/scripts/check_shadow_safety.py
    python kabu_native/scripts/check_shadow_safety.py --skip-api --skip-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _paths() -> tuple[Path, Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root, native_root / "src"


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    for p in (src_root, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    from api.rest_client import load_kabu_env

    load_kabu_env(repo_root=repo_root)
    return repo_root, native_root


def _bool_from_raw(raw: dict[str, Any], *keys: str, default: bool = False) -> bool:
    for k in keys:
        if k in raw:
            return bool(raw[k])
    return default


def check_safety_flags(raw: dict[str, Any]) -> CheckResult:
    safety = raw.get("safety") or {}
    if not isinstance(safety, dict):
        return CheckResult("safety_flags", False, "safety section missing or invalid")

    discord = _bool_from_raw(
        safety,
        "discord_enabled",
        "discord_notify",
        default=True,
    )
    orders = _bool_from_raw(
        safety,
        "order_enabled",
        "place_orders",
        "orders_enabled",
        default=True,
    )
    legacy = _bool_from_raw(
        safety,
        "legacy_yahoo_watch_enabled",
        "connect_yahoo_watch",
        "yahoo_watch_enabled",
        default=True,
    )

    ok = not discord and not orders and not legacy
    return CheckResult(
        "safety_flags",
        ok,
        "all safety flags false" if ok else "unsafe safety flag detected",
        {
            "discord_enabled": discord,
            "order_enabled": orders,
            "legacy_yahoo_watch_enabled": legacy,
            "expected": {
                "discord_enabled": False,
                "order_enabled": False,
                "legacy_yahoo_watch_enabled": False,
            },
        },
    )


def _find_no_entry_until(obj: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if "no_entry" in k.lower() or k in ("opening_gate", "gate_0930"):
                found.append(p)
            found.extend(_find_no_entry_until(v, p))
    return found


def check_no_entry_until(raw: dict[str, Any]) -> CheckResult:
    paths = _find_no_entry_until(raw)
    return CheckResult(
        "no_entry_until_absent",
        len(paths) == 0,
        "no deprecated no_entry_until keys" if not paths else f"deprecated keys: {paths}",
        {"deprecated_paths": paths, "use": "market_session_control in rules"},
    )


def check_market_session_control(raw: dict[str, Any]) -> CheckResult:
    rules = raw.get("rules") or {}
    enabled = bool(rules.get("market_session_control", False)) if isinstance(rules, dict) else False
    return CheckResult(
        "market_session_control",
        enabled,
        "market_session_control=true (Phase 13 adopted)" if enabled else "market_session_control should be true",
        {"market_session_control": enabled},
    )


def check_output_layout(native_root: Path) -> CheckResult:
    day = datetime.now().strftime("%Y%m%d")
    out_dir = native_root / "results" / "shadow" / day
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return CheckResult("output_paths", False, f"cannot create output dir: {e}")

    csv_p = out_dir / "shadow_events.csv"
    jsonl_p = out_dir / "shadow_events.jsonl"
    return CheckResult(
        "output_paths",
        True,
        f"output dir writable: {out_dir}",
        {
            "shadow_dir": str(out_dir),
            "shadow_events_csv": str(csv_p),
            "shadow_events_jsonl": str(jsonl_p),
        },
    )


def check_watchlist_dry_run(
    repo_root: Path,
    native_root: Path,
    raw: dict[str, Any],
) -> list[CheckResult]:
    from shadow.config import load_shadow_config
    from shadow.watchlist import build_watchlist

    cfg_path = native_root / "configs" / "shadow.yaml"
    config = load_shadow_config(cfg_path)
    wl_raw = raw.get("watchlist") or {}
    results: list[CheckResult] = []

    for source in ("morning_screen", "universe"):
        try:
            symbols = build_watchlist(
                source=source,
                native_root=native_root,
                repo_root=repo_root,
                path=None,
                universe_path=config.watchlist.universe_path,
                top_n=config.watchlist.top_n,
                passed_only=config.watchlist.passed_only,
            )
            results.append(
                CheckResult(
                    f"watchlist_{source}",
                    len(symbols) > 0,
                    f"{source}: {len(symbols)} symbols",
                    {"symbols": [s.symbol for s in symbols[:20]], "count": len(symbols)},
                )
            )
        except Exception as e:
            results.append(
                CheckResult(
                    f"watchlist_{source}",
                    False,
                    f"{source} failed: {e!r}",
                    {"error": repr(e)},
                )
            )

    configured = str(wl_raw.get("source", "morning_screen"))
    results.append(
        CheckResult(
            "watchlist_configured_source",
            True,
            f"configured source={configured}",
            {"source": configured},
        )
    )
    return results


def check_api(repo_root: Path, native_root: Path, *, symbol: str) -> CheckResult:
    from api.rest_client import KabuNativeRestClient, default_base_url, require_kabu_password, redact_secrets

    reports_before = set((native_root / "results" / "reports").glob("**/token*")) if (native_root / "results" / "reports").exists() else set()

    try:
        client = KabuNativeRestClient(base_url=default_base_url())
        password = require_kabu_password()
        token = client.issue_token(password)
        if not token or len(token) < 8:
            return CheckResult("api", False, "token issue returned empty token")

        code = symbol.replace(".T", "").strip()
        board = client.get_board(f"{code}@1", token=token)
        price = board.get("CurrentPrice") or board.get("CalcPrice")
        if price is None:
            return CheckResult(
                "api",
                False,
                "board missing CurrentPrice",
                {"board_keys_sample": list(board.keys())[:15]},
            )

        token_repr = redact_secrets(token)
        reports_after = set((native_root / "results" / "reports").glob("**/token*")) if (native_root / "results" / "reports").exists() else set()
        new_token_files = reports_after - reports_before

        return CheckResult(
            "api",
            True,
            "token + board OK (token not persisted by this check)",
            {
                "symbol": symbol,
                "current_price": price,
                "token_redacted": token_repr,
                "token_persisted_files": [str(p) for p in new_token_files],
                "token_saved_to_disk": len(new_token_files) > 0,
            },
        )
    except Exception as e:
        return CheckResult("api", False, f"API check failed: {e!r}", {"error": repr(e)})


def check_shadow_run(
    repo_root: Path,
    native_root: Path,
) -> CheckResult:
    from shadow.config import load_shadow_config
    from shadow.runner import ShadowRunner
    from shadow.watchlist import build_watchlist

    config = load_shadow_config(native_root / "configs" / "shadow.yaml")
    config.runtime.max_polls = 1
    config.runtime.continue_on_error = True

    watchlist = build_watchlist(
        source=config.watchlist.source,
        native_root=native_root,
        repo_root=repo_root,
        path=None,
        universe_path=config.watchlist.universe_path,
        top_n=config.watchlist.top_n,
        passed_only=config.watchlist.passed_only,
    )
    if not watchlist:
        return CheckResult("shadow_run", False, "watchlist empty, cannot run poll")

    day = datetime.now().strftime("%Y%m%d")
    out_dir = native_root / "results" / "shadow" / day
    csv_before = (out_dir / "shadow_events.csv").exists()
    jsonl_before = (out_dir / "shadow_events.jsonl").exists()

    errors: list[str] = []
    try:
        runner = ShadowRunner(
            repo_root=repo_root,
            native_root=native_root,
            config=config,
            watchlist=watchlist,
        )
        runner.run_loop()
    except Exception as e:
        errors.append(repr(e))
        if not config.runtime.continue_on_error:
            return CheckResult("shadow_run", False, f"run failed: {e!r}")

    csv_path = out_dir / "shadow_events.csv"
    jsonl_path = out_dir / "shadow_events.jsonl"
    csv_ok = csv_path.is_file() and csv_path.stat().st_size > 0
    jsonl_ok = jsonl_path.is_file() and jsonl_path.stat().st_size > 0

    if not csv_ok and not csv_before:
        errors.append("shadow_events.csv missing or empty after run")
    if not jsonl_ok and not jsonl_before:
        errors.append("shadow_events.jsonl missing or empty after run")

    row_count = 0
    if jsonl_ok:
        with jsonl_path.open(encoding="utf-8") as f:
            row_count = sum(1 for _ in f)

    passed = csv_ok and jsonl_ok and not errors
    return CheckResult(
        "shadow_run",
        passed,
        "one poll completed; logs written" if passed else "; ".join(errors) or "output files invalid",
        {
            "max_polls": 1,
            "continue_on_error": config.runtime.continue_on_error,
            "csv_bytes": csv_path.stat().st_size if csv_ok else 0,
            "jsonl_bytes": jsonl_path.stat().st_size if jsonl_ok else 0,
            "jsonl_lines": row_count,
            "errors": errors,
        },
    )


def check_no_yahoo_shadow_import() -> CheckResult:
    """Ensure check does not import legacy kabu_signal_shadow paper_trade bridge."""
    legacy = "kabu_signal_shadow" in sys.modules
    yahoo_watch = "yahoo_kabu_watch" in sys.modules
    ok = not legacy and not yahoo_watch
    return CheckResult(
        "no_legacy_modules_loaded",
        ok,
        "legacy shadow/yahoo modules not imported" if ok else "legacy module loaded in this process",
        {"kabu_signal_shadow": legacy, "yahoo_kabu_watch": yahoo_watch},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="kabu_native shadow safety check")
    parser.add_argument("--skip-api", action="store_true", help="skip kabu API token/board test")
    parser.add_argument("--skip-run", action="store_true", help="skip live shadow --max-polls 1")
    parser.add_argument("--symbol", default="9984", help="API board test symbol code")
    parser.add_argument("--report-date", default=None)
    args = parser.parse_args()

    repo_root, native_root = _bootstrap()
    cfg_path = native_root / "configs" / "shadow.yaml"
    raw: dict[str, Any] = {}
    import yaml

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    checks: list[CheckResult] = []
    checks.append(check_no_yahoo_shadow_import())
    checks.append(check_safety_flags(raw))
    checks.append(check_no_entry_until(raw))
    checks.append(check_market_session_control(raw))
    checks.append(check_output_layout(native_root))
    checks.extend(check_watchlist_dry_run(repo_root, native_root, raw))

    if not args.skip_api:
        api_result = check_api(repo_root, native_root, symbol=args.symbol)
        if api_result.details.get("token_saved_to_disk"):
            api_result.passed = False
            api_result.message += " (token file appeared on disk)"
        checks.append(api_result)
    else:
        checks.append(CheckResult("api", True, "skipped (--skip-api)", {"skipped": True}))

    if not args.skip_run:
        checks.append(check_shadow_run(repo_root, native_root))
    else:
        checks.append(CheckResult("shadow_run", True, "skipped (--skip-run)", {"skipped": True}))

    warnings: list[str] = []
    for c in checks:
        if c.check_id == "no_entry_until_absent" and not c.passed:
            warnings.append(c.message)

    failed = [c for c in checks if not c.passed]
    overall = len(failed) == 0

    report_date = args.report_date or datetime.now().strftime("%Y%m%d")
    reports_dir = native_root / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"safety_report_{report_date}.json"

    payload = {
        "component": "kabu_native.check_shadow_safety",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall_pass": overall,
        "ready_for_weekday_shadow": overall,
        "config_path": str(cfg_path),
        "report_path": str(report_path),
        "checks": [
            {
                "check_id": c.check_id,
                "passed": c.passed,
                "message": c.message,
                "details": c.details,
            }
            for c in checks
        ],
        "warnings": warnings,
        "failed_check_ids": [c.check_id for c in failed],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote {report_path}", file=sys.stderr)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
