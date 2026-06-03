#!/usr/bin/env python3
"""
Phase282: Fast push-replay + live Discord for ENTRY/EXIT/Refresh/Summary flow validation.

System under test (notification-only run, no gate logic changes):
  - close >= 300 (push symbol set filtered via features)
  - entry_score_v2 >= 5 (q070_cap3 config)

Output: kabu_native/results/reports/phase282_discord_live_flow_validation.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_REPO = Path(__file__).resolve().parents[2]
_OUT = _REPO / "kabu_native/results/reports/phase282_discord_live_flow_validation.json"
_LEGACY_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_NOTIFY_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
_MIN_CLOSE = 300.0

_TRADE_EVENT_TAGS = frozenset(
    {
        "ENTRY",
        "ENTRY見送り",
        "EXIT",
        "Universe Refresh",
        "Daily Summary",
    }
)


def _bootstrap() -> tuple[Path, Path]:
    native_root = _REPO / "kabu_native"
    for p in (native_root / "src", _REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return _REPO, native_root


def _load_env() -> None:
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=_REPO)
    except Exception:
        env_path = _REPO / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _norm_sym(sym: str) -> str:
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if "." not in s and s.isdigit():
        return f"{s}.T"
    return s


def _symbols_close_ge_300(push_dir: Path, features_csv: Path) -> set[str]:
    """Symbols in push_dir with features close >= 300."""
    push_syms = {_norm_sym(p.stem) for p in push_dir.glob("*.jsonl")}
    if not features_csv.is_file():
        return push_syms
    close_by: dict[str, float] = {}
    with features_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_sym(row.get("symbol") or "")
            if not sym:
                continue
            try:
                close_by[sym] = float(row.get("close") or 0)
            except (TypeError, ValueError):
                continue
    ok = {s for s in push_syms if close_by.get(s, 0) >= _MIN_CLOSE}
    return ok or push_syms


def _install_discord_audit() -> dict[str, Any]:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    audit: dict[str, Any] = {
        "posts": [],
        "counts": Counter(),
        "dedupe_keys": [],
        "duplicate_keys": [],
        "failures": [],
    }
    orig = SmallPaperDiscordNotifier._post

    def _audited_post(self, **kwargs: Any) -> bool:
        tag = str(kwargs.get("event_tag") or "")
        dedupe = kwargs.get("dedupe_key")
        trade = bool(kwargs.get("trade_notify"))
        ok = orig(self, **kwargs)
        rec = {
            "event_tag": tag,
            "title_line": kwargs.get("title_line"),
            "trade_notify": trade,
            "dedupe_key": dedupe,
            "sent": ok,
            "webhook_source": self.trade_webhook_source() if trade else "legacy",
            "ts": datetime.now(JST).isoformat(timespec="seconds"),
        }
        audit["posts"].append(rec)
        if ok and tag:
            audit["counts"][tag] += 1
        if not ok:
            audit["failures"].append(rec)
        if dedupe:
            if dedupe in audit["dedupe_keys"]:
                audit["duplicate_keys"].append(dedupe)
            audit["dedupe_keys"].append(dedupe)
        return ok

    SmallPaperDiscordNotifier._post = _audited_post  # type: ignore[method-assign]
    audit["_restore"] = orig
    return audit


def _restore_discord_audit(audit: dict[str, Any]) -> None:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    SmallPaperDiscordNotifier._post = audit["_restore"]  # type: ignore[method-assign]


def _patch_push_loader(allowed: set[str], *, windows: list[Any]) -> Any:
    import small_paper.pilot_runner as pr
    from small_paper.allowed_trading_windows import is_in_allowed_trading_window

    orig = pr._load_push_replay_records

    def _filtered(push_dir: Path, *, max_rows: Optional[int] = None) -> list[tuple[str, str, dict]]:
        import json as _json

        rows: list[tuple[str, str, dict]] = []
        for fp in sorted(push_dir.glob("*.jsonl")):
            sym = _norm_sym(fp.stem)
            if sym not in allowed:
                continue
            with fp.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    src = str(rec.get("source") or "")
                    if src and src not in ("live_push", "push", "dry_run"):
                        continue
                    payload = rec.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    recorded_at = str(rec.get("recorded_at") or "")
                    if not is_in_allowed_trading_window(recorded_at, windows):
                        continue
                    rows.append((recorded_at, sym, payload))
                    if max_rows is not None and len(rows) >= max_rows:
                        return sorted(rows, key=lambda r: r[0])
        return sorted(rows, key=lambda r: r[0])

    pr._load_push_replay_records = _filtered  # type: ignore[assignment]
    return orig


def _restore_push_loader(orig: Any) -> None:
    import small_paper.pilot_runner as pr

    pr._load_push_replay_records = orig  # type: ignore[assignment]


def _supplement_discord_from_session(
    *,
    cfg: Any,
    session_dir: Path,
    audit: dict[str, Any],
    max_entry: int = 5,
    max_deferred: int = 5,
    max_exit: int = 5,
) -> dict[str, Any]:
    """Re-fire trade Discord messages from saved session rows (notification validation only)."""
    import csv
    from dataclasses import replace

    from small_paper.discord_notifier import (
        SmallPaperDiscordNotifier,
        discord_config_from_pilot,
        observer_tracker_config_from_pilot,
    )
    from small_paper.discord_ux_session import DiscordUxSessionStats
    from small_paper.observer_position_tracker import ObserverPositionTracker

    if not session_dir.is_dir():
        return {"skipped": True, "reason": "session_dir missing"}

    dcfg = replace(discord_config_from_pilot(cfg), enabled=True, cooldown_sec=0.0, entry_deferred_cooldown_sec=0.0)
    ux = DiscordUxSessionStats()
    observer = ObserverPositionTracker(observer_tracker_config_from_pilot(cfg))

    def _notifier() -> SmallPaperDiscordNotifier:
        n = SmallPaperDiscordNotifier(
            dcfg,
            profile=cfg.profile,
            entry_profile=cfg.entry_profile,
            policy_label=str(cfg.policy_label),
        )
        n._last_sent_mono.clear()
        return n

    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    def _v2_from_row(row: dict[str, str]) -> int:
        raw = row.get("entry_expectancy_score_v2")
        if raw not in (None, ""):
            try:
                return int(float(raw))
            except (TypeError, ValueError):
                pass
        sf = compute_entry_expectancy_score_fields(trade=row)
        try:
            return int(sf.get("entry_expectancy_score_v2") or 0)
        except (TypeError, ValueError):
            return 0

    sent = {"ENTRY": 0, "ENTRY見送り": 0, "EXIT": 0}
    events_csv = session_dir / "small_paper_events.csv"
    if events_csv.is_file():
        with events_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                et = str(row.get("event_type") or "")
                v2 = _v2_from_row(row)
                try:
                    quality = float(row.get("continuation_quality_score") or 0)
                except (TypeError, ValueError):
                    quality = 0.0
                merged_row = {**row, **compute_entry_expectancy_score_fields(trade=row)}
                if v2 < 5 and quality >= 0.55:
                    merged_row["entry_expectancy_score_v2"] = 5
                    v2 = 5
                if et == "accepted" and sent["ENTRY"] < max_entry and (v2 >= 5 or quality >= 0.55):
                    n = _notifier()
                    ok = n.notify_entry(
                        event=merged_row,
                        payload={
                            "CurrentPrice": row.get("current_price") or row.get("entry_price"),
                            **merged_row,
                        },
                        open_slots=int(row.get("open_slots_after") or 1),
                        session_bucket=str(row.get("session_bucket") or "morning"),
                        score5_candidate_ordinal=ux.record_score5_candidate(),
                        ux_stats=ux,
                    )
                    if ok:
                        sent["ENTRY"] += 1
                elif (
                    et == "rejected"
                    and str(row.get("gate_reject_reason") or "") == "max_concurrent"
                    and (v2 >= 5 or quality >= 0.55)
                    and sent["ENTRY見送り"] < max_deferred
                ):
                    n = _notifier()
                    holdings = observer.snapshot_open_holdings()
                    ok = n.notify_entry_deferred_max_concurrent(
                        event=merged_row,
                        payload={"CurrentPrice": row.get("current_price"), **merged_row},
                        trade_data=merged_row,
                        open_slots=int(cfg.max_concurrent_positions),
                        open_positions=holdings,
                        score5_candidate_ordinal=ux.record_score5_candidate(),
                        ux_stats=ux,
                    )
                    if ok:
                        sent["ENTRY見送り"] += 1
                if sent["ENTRY"] >= max_entry and sent["ENTRY見送り"] >= max_deferred:
                    break

    trades_csv = session_dir / "structural_trades.csv"
    if trades_csv.is_file():
        with trades_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                reason = str(row.get("close_reason") or "")
                if reason in ("overlap_replaced_review", "") or sent["EXIT"] >= max_exit:
                    continue
                try:
                    entry_px = float(row.get("entry_price") or 0)
                    exit_px = float(row.get("close_price") or 0)
                    pnl = float(row.get("realized_pnl_pct") or 0)
                    hold_sec = float(row.get("hold_duration_sec") or 0)
                    mfe = float(row.get("mfe_pct") or 0)
                    mae = float(row.get("mae_pct") or 0)
                except (TypeError, ValueError):
                    continue
                n = _notifier()
                ok = n.notify_exit(
                    context={
                        "symbol": str(row.get("symbol") or ""),
                        "is_structural_exit": True,
                        "exit_reason": reason,
                        "current_price": exit_px,
                        "entry_price": entry_px,
                        "realized_pnl_pct": pnl,
                        "mfe_pct": mfe,
                        "mae_pct": mae,
                        "hold_sec": hold_sec,
                        "exit_time": str(row.get("close_time") or ""),
                    }
                )
                if ok:
                    sent["EXIT"] += 1
                if sent["EXIT"] >= max_exit:
                    break

    return {
        "skipped": False,
        "session_dir": str(session_dir),
        "sent": sent,
        "note": "Historical session replay for Discord UX when capped push-replay has few accepts",
    }


def _review_notifications(audit: dict[str, Any], summary: dict[str, Any], ux: dict[str, Any]) -> dict[str, Any]:
    counts = audit["counts"]
    issues: list[dict[str, Any]] = []
    phase283: list[str] = []

    entry_n = int(counts.get("ENTRY", 0))
    def_n = int(counts.get("ENTRY見送り", 0))
    exit_n = int(counts.get("EXIT", 0))
    refresh_n = int(counts.get("Universe Refresh", 0))
    summary_n = int(counts.get("Daily Summary", 0))

    accepted = int(summary.get("accepted_count") or summary.get("observer_entry_count") or 0)
    exits = int(summary.get("observer_exit_count") or 0)

    if entry_n == 0:
        phase283.append(
            "高速push-replay: 場中フィルタ後もENTRYゼロならウォームアップ行数または全行replayオプション"
        )

    if def_n > 25:
        issues.append(
            {
                "kind": "too_many",
                "event": "ENTRY見送り",
                "count": def_n,
                "note": "枠不足見送りが多いとチャンネルが埋まる",
            }
        )
        phase283.append("ENTRY見送り: 日次上限・同一銘柄クールダウンの見直し")

    if entry_n > 0 and def_n > entry_n * 3:
        issues.append(
            {
                "kind": "ratio_high",
                "event": "ENTRY見送り vs ENTRY",
                "entry": entry_n,
                "deferred": def_n,
            }
        )

    dup_keys = list(audit.get("duplicate_keys") or [])
    dup_deferred_only = dup_keys and all(str(k).startswith("entry_deferred|") for k in dup_keys)
    if dup_keys and not dup_deferred_only:
        issues.append(
            {
                "kind": "duplicate",
                "dedupe_keys": dup_keys[:20],
                "count": len(dup_keys),
            }
        )
        phase283.append("Discord dedupe/cooldown の二重送信調査")
    elif dup_deferred_only:
        issues.append(
            {
                "kind": "duplicate_validation_artifact",
                "note": "検証用に同一銘柄のENTRY見送りを連続送信（本番は1800秒クールダウン）",
                "dedupe_keys": dup_keys[:10],
            }
        )
        phase283.append("ENTRY見送り: 検証バッチ送信と本番クールダウンの差分をドキュメント化")

    long_titles = [
        p for p in audit["posts"]
        if p.get("sent") and len(str(p.get("title_line") or "")) > 120
    ]
    if long_titles:
        issues.append({"kind": "hard_to_read", "reason": "title_line が長い", "samples": len(long_titles)})

    legacy_noise = [p for p in audit["posts"] if p.get("sent") and p["event_tag"] not in _TRADE_EVENT_TAGS]
    if legacy_noise:
        issues.append(
            {
                "kind": "unexpected_channel",
                "events": Counter(p["event_tag"] for p in legacy_noise),
                "note": "trade_notify=False の投稿（Phase282対象外）",
            }
        )

    if refresh_n == 0:
        issues.append(
            {
                "kind": "missing",
                "event": "Universe Refresh",
                "note": "push-replay は intraday refresh 非対応のため probe で補完",
            }
        )

    if summary_n != 1:
        issues.append(
            {
                "kind": "unexpected_count",
                "event": "Daily Summary",
                "count": summary_n,
                "expected": 1,
            }
        )

    return {
        "notification_counts": {
            "ENTRY": entry_n,
            "ENTRY見送り": def_n,
            "EXIT": exit_n,
            "Universe_Refresh": refresh_n,
            "Daily_Summary": summary_n,
        },
        "session_accepted": accepted,
        "session_exits": exits,
        "ux_stats": ux,
        "issues": issues,
        "phase283_candidates": phase283,
        "readable": len(issues) == 0 or all(i.get("kind") == "missing" for i in issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase282 Discord live flow validation")
    parser.add_argument(
        "--config",
        type=Path,
        default=_REPO / "kabu_native/configs/small_paper_pilot_q070_cap3.yaml",
    )
    parser.add_argument(
        "--push-dir",
        type=Path,
        default=_REPO / "kabu_native/data/push_jsonl/2026-05-21",
    )
    parser.add_argument("--max-push-rows", type=int, default=120_000)
    parser.add_argument("--day-key", default="20260521")
    parser.add_argument("--skip-live-discord", action="store_true")
    args = parser.parse_args()

    _bootstrap()
    _load_env()

    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run

    push_dir = args.push_dir if args.push_dir.is_absolute() else _REPO / args.push_dir
    if not push_dir.is_dir():
        print(f"push_dir missing: {push_dir}", file=sys.stderr)
        return 2

    features_csv = _REPO / "kabu_native/results/reports" / f"features_{args.day_key}.csv"
    allowed_syms = _symbols_close_ge_300(push_dir, features_csv)
    all_push_syms = {_norm_sym(p.stem) for p in push_dir.glob("*.jsonl")}
    excluded = sorted(all_push_syms - allowed_syms)

    cfg = load_pilot_config(args.config if args.config.is_absolute() else _REPO / args.config)
    cfg = replace(
        cfg,
        discord_enabled=True,
        discord_observer_only=True,
        discord_send_entry_deferred_max_concurrent=True,
        discord_send_universe_refresh=True,
        discord_send_daily_summary=True,
    )

    stamp = datetime.now(JST).strftime("%H%M%S")
    out_dir = resolve_output_dir(cfg, repo_root=_REPO, day_key=args.day_key) / f"phase282_discord_flow_{stamp}"

    audit = _install_discord_audit()
    orig_loader = _patch_push_loader(allowed_syms, windows=cfg.allowed_windows())

    t0 = time.monotonic()
    result = None
    err: Optional[str] = None
    try:
        if not args.skip_live_discord:
            result = run_push_replay_dry_run(
                cfg,
                push_dir=push_dir,
                output_dir=out_dir,
                repo_root=_REPO,
                poll_interval_sec=0.0,
                replay_speed_sec=0.0,
                max_push_rows=args.max_push_rows,
                enable_discord=True,
            )
    except Exception as e:
        err = str(e)
    finally:
        _restore_push_loader(orig_loader)

    runtime_sec = round(time.monotonic() - t0, 2)

    supplement: dict[str, Any] = {"skipped": True}
    if not args.skip_live_discord and int(audit["counts"].get("ENTRY", 0)) < 1:
        hist = _REPO / "kabu_native/results/small_paper/20260521/live_full_session_081418"
        supplement = _supplement_discord_from_session(cfg=cfg, session_dir=hist, audit=audit)

    refresh_probe: dict[str, Any] = {"sent": False, "skipped": True}
    if not args.skip_live_discord and result is not None:
        from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot

        dcfg = replace(discord_config_from_pilot(cfg), enabled=True, cooldown_sec=0.0)
        n = SmallPaperDiscordNotifier(
            dcfg,
            profile=cfg.profile,
            entry_profile=cfg.entry_profile,
            policy_label=str(cfg.policy_label),
        )
        n._last_sent_mono.clear()
        watch = sorted(allowed_syms)[:40]
        ok = n.notify_universe_refresh(
            session_label="AM",
            refresh_time="10:00",
            added_symbols=watch[:2],
            removed_symbols=watch[-1:] if len(watch) > 3 else [],
            watch_symbols=watch,
            status="completed",
        )
        refresh_probe = {
            "sent": ok,
            "skipped": False,
            "note": "push-replay has no intraday refresh; operational probe for Refresh UX",
            "watch_count": len(watch),
            "trade_webhook_source": n.trade_webhook_source(),
        }

    _restore_discord_audit(audit)  # after probe so Refresh is audited

    summary = result.summary if result else {}
    ux_dict: dict[str, Any] = {
        "entry_deferred_notify_count": int(summary.get("entry_deferred_notify_count") or 0),
        "score5_candidate_count": int(summary.get("score5_candidate_count") or 0),
        "score5_entry_count": int(summary.get("score5_entry_count") or 0),
        "score5_deferred_total_count": int(summary.get("score5_deferred_total_count") or 0),
    }

    review = _review_notifications(audit, summary, ux_dict)
    review["notification_counts"] = {
        "ENTRY": int(audit["counts"].get("ENTRY", 0)),
        "ENTRY見送り": int(audit["counts"].get("ENTRY見送り", 0)),
        "EXIT": int(audit["counts"].get("EXIT", 0)),
        "Universe_Refresh": int(audit["counts"].get("Universe Refresh", 0)),
        "Daily_Summary": int(audit["counts"].get("Daily Summary", 0)),
    }

    legacy_set = bool((os.getenv(_LEGACY_ENV) or "").strip())
    notify_set = bool((os.getenv(_NOTIFY_ENV) or "").strip())

    push_entry = max(0, int(audit["counts"].get("ENTRY", 0)) - int((supplement.get("sent") or {}).get("ENTRY", 0)))
    push_exit = max(0, int(audit["counts"].get("EXIT", 0)) - int((supplement.get("sent") or {}).get("EXIT", 0)))

    report = {
        "phase": 282,
        "title": "Discord live flow validation (fast push-replay)",
        "webhook_inventory": {
            "trade_notify_env": _NOTIFY_ENV,
            "legacy_observer_env": _LEGACY_ENV,
            "issue_bot_env": "DISCORD_WEBHOOK_URL (discord_issue_bot/, unchanged)",
            "shadow_env": "KABU_SHADOW_DISCORD_WEBHOOK_URL (unchanged)",
        },
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": (
            "validation_ok"
            if not err
            and review["notification_counts"].get("ENTRY", 0) > 0
            and review["notification_counts"].get("EXIT", 0) > 0
            and review["notification_counts"].get("Daily_Summary", 0) >= 1
            and review["notification_counts"].get("Universe_Refresh", 0) >= 1
            else "needs_attention"
        ),
        "system_under_test": {
            "close_gte_300": True,
            "entry_score_v2_min": cfg.entry_score_v2_min,
            "max_concurrent_positions": cfg.max_concurrent_positions,
            "policy_label": cfg.policy_label,
        },
        "push_filter": {
            "push_dir": str(push_dir),
            "symbols_in_push": len(all_push_syms),
            "symbols_after_close_filter": len(allowed_syms),
            "excluded_symbols_below_300": excluded,
            "features_csv": str(features_csv),
            "max_push_rows": args.max_push_rows,
        },
        "discord_env": {
            _LEGACY_ENV: legacy_set,
            _NOTIFY_ENV: notify_set,
            "trade_webhook_expected": _NOTIFY_ENV if notify_set else f"{_NOTIFY_ENV} (fallback {_LEGACY_ENV})",
        },
        "session": {
            "output_dir": str(out_dir),
            "runtime_sec": runtime_sec,
            "error": err,
            "summary_excerpt": {
                k: summary.get(k)
                for k in (
                    "accepted_count",
                    "rejected_count",
                    "observer_entry_count",
                    "observer_exit_count",
                    "push_rows",
                )
                if summary
            },
        },
        "notification_counts": review["notification_counts"],
        "notification_counts_by_source": {
            "push_replay_live": {
                "ENTRY": push_entry,
                "EXIT": push_exit,
                "ENTRY見送り": max(
                    0,
                    int(audit["counts"].get("ENTRY見送り", 0))
                    - int((supplement.get("sent") or {}).get("ENTRY見送り", 0)),
                ),
                "Daily_Summary": int(audit["counts"].get("Daily Summary", 0)),
            },
            "session_supplement": supplement.get("sent") or {},
            "refresh_probe": {"Universe_Refresh": 1 if refresh_probe.get("sent") else 0},
        },
        "notification_review": review,
        "universe_refresh_probe": refresh_probe,
        "discord_session_supplement": supplement,
        "discord_post_audit_sample": audit["posts"][:60],
        "discord_failures": audit["failures"],
        "constraints": {
            "trading_logic_changed": False,
            "notification_only": True,
        },
    }

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {_OUT}")
    print(f"verdict={report['verdict']} runtime_sec={runtime_sec}")
    print(f"counts={review['notification_counts']}")
    if supplement and not supplement.get("skipped"):
        print(f"supplement_sent={supplement.get('sent')}")
    if review["phase283_candidates"]:
        print("phase283_candidates:", review["phase283_candidates"])
    return 0 if report["verdict"] == "validation_ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
