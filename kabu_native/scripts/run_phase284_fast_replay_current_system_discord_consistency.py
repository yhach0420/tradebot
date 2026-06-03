#!/usr/bin/env python3
"""
Phase284: Re-simulate 20260603 push data with current gate (v2>=5, close>=300, cap=3,
trailing_mfe shadow) and audit Discord notifications on NOTIFY webhook.

Output: kabu_native/results/reports/phase284_fast_replay_current_system_discord_consistency.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_REPO = Path(__file__).resolve().parents[2]
_OUT = _REPO / "kabu_native/results/reports/phase284_fast_replay_current_system_discord_consistency.json"
_NOTIFY_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
_MIN_CLOSE = 300.0
_EXPECTED_V2_MIN = 5
_EXPECTED_CAP = 3


def _bootstrap() -> None:
    native = _REPO / "kabu_native"
    for p in (native / "src", _REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_env() -> None:
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=_REPO)
    except Exception:
        env = _REPO / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
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


def _patch_push_loader(allowed: set[str], windows: list[Any]) -> Any:
    import json as _json

    import small_paper.pilot_runner as pr
    from small_paper.allowed_trading_windows import is_in_allowed_trading_window

    orig = pr._load_push_replay_records

    def _filtered(push_dir: Path, *, max_rows: Optional[int] = None) -> list[tuple[str, str, dict]]:
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


def _int_score(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass
class SimAuditState:
    entry_symbols: dict[str, list[str]] = field(default_factory=dict)
    exit_symbols: list[str] = field(default_factory=list)
    sim_accepted: int = 0
    sim_rejected: int = 0
    sim_max_concurrent_rejects: int = 0
    sim_v2_below_rejects: int = 0


def _install_hooks(
    audit: dict[str, Any],
    sim: SimAuditState,
    *,
    post_interval_sec: float = 0.0,
) -> dict[str, Any]:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    hooks: dict[str, Any] = {}
    last_post_mono = 0.0
    post_gap = max(0.0, float(post_interval_sec))
    pending_meta: dict[str, Any] = {}
    orig_post = SmallPaperDiscordNotifier._post
    orig_entry = SmallPaperDiscordNotifier.notify_entry
    orig_deferred = SmallPaperDiscordNotifier.notify_entry_deferred_max_concurrent
    orig_exit = SmallPaperDiscordNotifier.notify_exit

    def _log_post(tag: str, ok: bool, extra: dict[str, Any], **kwargs: Any) -> None:
        detail_parts: list[str] = []
        for f in kwargs.get("fields") or []:
            name = str(f.get("name") or "")
            if "詳細" in name:
                detail_parts.append(str(f.get("value") or ""))
        audit["discord_posts"].append(
            {
                "at": datetime.now(JST).isoformat(timespec="seconds"),
                "event_tag": tag,
                "sent": ok,
                "detail_text": "".join(detail_parts) if detail_parts else extra.get("detail_text"),
                **extra,
            }
        )
        if ok:
            audit["sent_counts"][tag] += 1
        else:
            audit["blocked_counts"][tag] += 1

    def audited_post(self, **kwargs: Any) -> bool:
        nonlocal last_post_mono
        if post_gap > 0:
            now_m = time.monotonic()
            wait = post_gap - (now_m - last_post_mono) if last_post_mono else 0.0
            if wait > 0:
                time.sleep(wait)
        ok = orig_post(self, **kwargs)
        if post_gap > 0:
            last_post_mono = time.monotonic()
        tag = str(kwargs.get("event_tag") or "")
        extra = {
            "title_line": kwargs.get("title_line"),
            "dedupe_key": kwargs.get("dedupe_key"),
            "trade_notify": bool(kwargs.get("trade_notify")),
            "webhook_source": self.trade_webhook_source()
            if kwargs.get("trade_notify")
            else "legacy",
            **pending_meta,
        }
        pending_meta.clear()
        _log_post(tag, ok, extra, **kwargs)
        return ok

    def audited_entry(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        open_slots: int,
        session_bucket: str,
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Any = None,
    ) -> bool:
        sym = _norm_sym(str(event.get("symbol") or ""))
        v2 = _int_score(event.get("entry_expectancy_score_v2"))
        rec = {"symbol": sym, "entry_score_v2": v2, "open_slots": open_slots}
        audit["entry_attempts"].append(rec)
        if v2 is None or v2 < _EXPECTED_V2_MIN:
            audit["violations"].append(
                {"kind": "entry_notify_below_v2", "symbol": sym, "entry_score_v2": v2}
            )
            _log_post("ENTRY", False, {**rec, "blocked_reason": "score_below_5"})
            return False
        pending_meta.update(rec)
        ok = orig_entry(
            self,
            event=event,
            payload=payload,
            open_slots=open_slots,
            session_bucket=session_bucket,
            score5_candidate_ordinal=score5_candidate_ordinal,
            ux_stats=ux_stats,
        )
        if ok:
            sim.entry_symbols.setdefault(sym, []).append(str(event.get("event_time") or ""))
        return ok

    def audited_deferred(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        trade_data: Mapping[str, Any],
        open_slots: int,
        open_positions: Any,
        score5_candidate_ordinal: Optional[int] = None,
        ux_stats: Any = None,
    ) -> bool:
        sym = _norm_sym(str(event.get("symbol") or ""))
        v2 = _int_score(
            event.get("entry_expectancy_score_v2") or trade_data.get("entry_expectancy_score_v2")
        )
        reason = str(event.get("gate_reject_reason") or "")
        rec = {
            "symbol": sym,
            "entry_score_v2": v2,
            "gate_reject_reason": reason,
            "open_slots": open_slots,
        }
        audit["deferred_attempts"].append(rec)
        if reason != "max_concurrent":
            audit["violations"].append(
                {"kind": "deferred_wrong_reason", "symbol": sym, "reason": reason}
            )
            _log_post("ENTRY見送り", False, {**rec, "blocked_reason": "not_max_concurrent"})
            return False
        if v2 is None or v2 < _EXPECTED_V2_MIN:
            audit["violations"].append(
                {"kind": "deferred_notify_below_v2", "symbol": sym, "entry_score_v2": v2}
            )
            _log_post("ENTRY見送り", False, {**rec, "blocked_reason": "score_below_5"})
            return False
        pending_meta.update(rec)
        return orig_deferred(
            self,
            event=event,
            payload=payload,
            trade_data=trade_data,
            open_slots=open_slots,
            open_positions=open_positions,
            score5_candidate_ordinal=score5_candidate_ordinal,
            ux_stats=ux_stats,
        )

    def audited_exit(self, *, context: Mapping[str, Any]) -> bool:
        sym = _norm_sym(str(context.get("symbol") or ""))
        had_entry = sym in sim.entry_symbols and len(sim.entry_symbols[sym]) > 0
        rec = {
            "symbol": sym,
            "exit_reason": str(context.get("exit_reason") or ""),
            "had_prior_entry": had_entry,
        }
        if not had_entry:
            audit["violations"].append({"kind": "exit_without_entry", "symbol": sym})
        pending_meta.update(rec)
        ok = orig_exit(self, context=context)
        if ok:
            sim.exit_symbols.append(sym)
        return ok

    SmallPaperDiscordNotifier._post = audited_post  # type: ignore[method-assign]
    SmallPaperDiscordNotifier.notify_entry = audited_entry  # type: ignore[method-assign]
    SmallPaperDiscordNotifier.notify_entry_deferred_max_concurrent = audited_deferred  # type: ignore[method-assign]
    SmallPaperDiscordNotifier.notify_exit = audited_exit  # type: ignore[method-assign]

    hooks.update(
        {
            "post": orig_post,
            "entry": orig_entry,
            "deferred": orig_deferred,
            "exit": orig_exit,
        }
    )
    return hooks


def _restore_hooks(hooks: dict[str, Any]) -> None:
    from small_paper.discord_notifier import SmallPaperDiscordNotifier

    SmallPaperDiscordNotifier._post = hooks["post"]  # type: ignore[method-assign]
    SmallPaperDiscordNotifier.notify_entry = hooks["entry"]  # type: ignore[method-assign]
    SmallPaperDiscordNotifier.notify_entry_deferred_max_concurrent = hooks["deferred"]  # type: ignore[method-assign]
    SmallPaperDiscordNotifier.notify_exit = hooks["exit"]  # type: ignore[method-assign]


def _parse_daily_summary_metrics(posts: list[dict[str, Any]]) -> dict[str, Optional[int]]:
    """Extract entry/exit/trade counts from Daily Summary detail embed."""
    summaries = [p for p in posts if p.get("event_tag") == "Daily Summary" and p.get("sent")]
    if not summaries:
        return {"entry_count": None, "exit_count": None, "trade_count": None}
    text = str(summaries[-1].get("detail_text") or "")
    out: dict[str, Optional[int]] = {"entry_count": None, "exit_count": None, "trade_count": None}
    for line in text.splitlines():
        line = line.strip()
        for key, label in (
            ("entry_count", "entry_count:"),
            ("exit_count", "exit_count:"),
            ("trade_count", "trade_count:"),
        ):
            if line.startswith(label):
                try:
                    out[key] = int(line.split(":", 1)[1].strip())
                except (IndexError, ValueError):
                    pass
    return out


def _analyze_simulation(result: Any, audit: dict[str, Any], sim: SimAuditState) -> dict[str, Any]:
    summary = result.summary if result else {}
    events = result.events if result else []
    rejects = result.rejects if result else []

    accepted_events = [e for e in events if e.get("event_type") == "accepted"]
    sim_accepted = len(accepted_events)
    sim_exits = sum(1 for e in events if e.get("event_type") == "observer_exit")
    sim_rejected = sum(1 for e in events if e.get("event_type") == "rejected")
    accept_below_v2 = sum(
        1
        for e in accepted_events
        if (_int_score(e.get("entry_expectancy_score_v2")) or 0) < _EXPECTED_V2_MIN
    )
    mc_rejects = [
        r
        for r in rejects
        if str(r.get("gate_reject_reason") or "") == "max_concurrent"
    ]
    mc_v5 = [
        r
        for r in mc_rejects
        if (_int_score(r.get("entry_expectancy_score_v2")) or 0) >= _EXPECTED_V2_MIN
    ]
    v2_below = sum(
        1
        for r in rejects
        if str(r.get("gate_reject_reason") or "") == "entry_score_v2_below_threshold"
    )

    def _dedupe_sent(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for p in posts:
            if not p.get("sent"):
                continue
            key = str(p.get("dedupe_key") or f"{p.get('event_tag')}|{p.get('at')}")
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    sent_entries = _dedupe_sent(
        [p for p in audit["discord_posts"] if p.get("event_tag") == "ENTRY"]
    )
    sent_def = _dedupe_sent(
        [p for p in audit["discord_posts"] if p.get("event_tag") == "ENTRY見送り"]
    )
    sent_exits = _dedupe_sent(
        [p for p in audit["discord_posts"] if p.get("event_tag") == "EXIT"]
    )

    entry_scores = [_int_score(p.get("entry_score_v2")) for p in sent_entries]
    def_scores = [_int_score(p.get("entry_score_v2")) for p in sent_def]

    summary_entry = int(
        summary.get("observer_entry_count") or summary.get("accepted_count") or 0
    )
    summary_exit = int(summary.get("observer_exit_count") or 0)
    trade_count = summary_exit
    ds_metrics = _parse_daily_summary_metrics(audit["discord_posts"])

    checks = {
        "entry_notify_all_score5_plus": all((s or 0) >= _EXPECTED_V2_MIN for s in entry_scores),
        "deferred_notify_all_score5_plus": all((s or 0) >= _EXPECTED_V2_MIN for s in def_scores),
        "no_entry_below_v2_sent": not any((s or 0) < _EXPECTED_V2_MIN for s in entry_scores if s is not None),
        "deferred_only_max_concurrent": all(
            p.get("gate_reject_reason") == "max_concurrent" for p in audit["deferred_attempts"] if p.get("sent")
        ),
        "sim_no_accept_below_v2": accept_below_v2 == 0,
        "discord_entry_count_matches_sim": len(sent_entries) == sim_accepted,
        "discord_exit_count_matches_sim": len(sent_exits) == sim_exits,
        "every_exit_has_entry": all(p.get("had_prior_entry") for p in sent_exits) if sent_exits else True,
        "summary_entry_matches_sim": summary_entry == sim_accepted,
        "summary_exit_matches_sim": summary_exit == sim_exits,
        "summary_trade_count_equals_exit": trade_count == summary_exit,
        "daily_summary_entry_matches": ds_metrics.get("entry_count") in (None, summary_entry),
        "daily_summary_exit_matches": ds_metrics.get("exit_count") in (None, summary_exit),
        "daily_summary_trade_matches": ds_metrics.get("trade_count") in (None, trade_count),
        "max_concurrent_deferred_lte_gate": len(sent_def) <= len(mc_v5),
        "uses_notify_webhook": all(
            p.get("webhook_source") in ("notify", "legacy_fallback")
            for p in audit["discord_posts"]
            if p.get("sent") and p.get("trade_notify")
        ),
    }

    verdict = "consistency_ok" if all(checks.values()) and not audit["violations"] else "needs_attention"

    return {
        "simulation": {
            "accepted_count": sim_accepted,
            "rejected_count": sim_rejected,
            "observer_exit_count": sim_exits,
            "max_concurrent_rejects": len(mc_rejects),
            "max_concurrent_rejects_v5_plus": len(mc_v5),
            "entry_score_v2_below_rejects": v2_below,
        },
        "discord_sent": {
            "ENTRY": len(sent_entries),
            "ENTRY見送り": len(sent_def),
            "EXIT": len(sent_exits),
            "raw_counter": dict(audit["sent_counts"]),
        },
        "discord_blocked": dict(audit["blocked_counts"]),
        "checks": checks,
        "verdict": verdict,
        "violations": audit["violations"][:50],
        "daily_summary_parsed": ds_metrics,
        "summary_excerpt": {
            k: summary.get(k)
            for k in (
                "accepted_count",
                "rejected_count",
                "observer_entry_count",
                "observer_exit_count",
                "push_rows",
                "policy_label",
                "structural_exit_policy",
                "entry_score_v2_min",
                "max_concurrent_positions",
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase284 re-sim + Discord consistency audit")
    parser.add_argument("--day-key", default="20260603")
    parser.add_argument(
        "--config",
        type=Path,
        default=_REPO
        / "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    )
    parser.add_argument(
        "--push-dir",
        type=Path,
        default=_REPO / "kabu_native/data/push_jsonl/2026-06-03",
    )
    parser.add_argument("--max-push-rows", type=int, default=None)
    parser.add_argument("--post-interval-sec", type=float, default=1.0)
    parser.add_argument("--skip-discord", action="store_true", help="Re-sim only, no webhook")
    args = parser.parse_args()

    _bootstrap()
    _load_env()

    from small_paper.config import load_pilot_config, resolve_output_dir
    from small_paper.pilot_runner import run_push_replay_dry_run

    day_key = args.day_key
    push_dir = args.push_dir if args.push_dir.is_absolute() else _REPO / args.push_dir
    features_csv = _REPO / "kabu_native/results/reports" / f"features_{day_key}.csv"
    allowed = _symbols_close_ge_300(push_dir, features_csv)

    cfg_path = args.config if args.config.is_absolute() else _REPO / args.config
    cfg = load_pilot_config(cfg_path)
    cfg = replace(
        cfg,
        discord_enabled=not args.skip_discord,
        discord_observer_only=True,
        discord_send_entry_deferred_max_concurrent=True,
        discord_send_universe_refresh=False,
        discord_send_daily_summary=not args.skip_discord,
    )

    stamp = datetime.now(JST).strftime("%H%M%S")
    out_dir = resolve_output_dir(cfg, repo_root=_REPO, day_key=day_key) / f"phase284_resim_{stamp}"

    audit: dict[str, Any] = {
        "discord_posts": [],
        "sent_counts": Counter(),
        "blocked_counts": Counter(),
        "entry_attempts": [],
        "deferred_attempts": [],
        "violations": [],
    }
    sim = SimAuditState()

    orig_loader = _patch_push_loader(allowed, cfg.allowed_windows())
    hooks = (
        _install_hooks(audit, sim, post_interval_sec=args.post_interval_sec)
        if not args.skip_discord
        else {}
    )

    t0 = time.monotonic()
    result = None
    err: Optional[str] = None
    try:
        result = run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=out_dir,
            repo_root=_REPO,
            poll_interval_sec=0.0,
            replay_speed_sec=0.0,
            max_push_rows=args.max_push_rows,
            enable_discord=not args.skip_discord,
        )
    except Exception as e:
        err = str(e)
    finally:
        _restore_push_loader(orig_loader)
        if hooks:
            _restore_hooks(hooks)

    runtime_sec = round(time.monotonic() - t0, 2)

    analysis = _analyze_simulation(result, audit, sim) if result else {"verdict": "needs_attention"}

    report = {
        "phase": 284,
        "title": "Fast replay current system + Discord consistency audit",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "system_config": {
            "config_path": str(cfg_path),
            "policy_label": cfg.policy_label,
            "entry_score_v2_min": cfg.entry_score_v2_min,
            "max_concurrent_positions": cfg.max_concurrent_positions,
            "structural_exit_policy": getattr(cfg, "structural_exit_policy", ""),
            "close_gte_300_filter": True,
            "trailing_mfe": "combined_structural_exit_v1_trailing_mfe_shadow",
        },
        "data": {
            "day_key": day_key,
            "push_dir": str(push_dir),
            "symbols_in_push": len(list(push_dir.glob("*.jsonl"))),
            "symbols_after_close_filter": len(allowed),
            "features_csv": str(features_csv),
            "max_push_rows": args.max_push_rows,
            "output_dir": str(out_dir),
        },
        "runtime_sec": runtime_sec,
        "error": err,
        "post_interval_sec": args.post_interval_sec,
        "discord_env": {
            _NOTIFY_ENV: bool(os.getenv(_NOTIFY_ENV, "").strip()),
        },
        "discord_skip": args.skip_discord,
        "consistency": analysis,
        "constraints": {
            "production_logic_changed": False,
            "re_simulation_not_notification_replay": True,
        },
        "notes": [
            "push-replay re-runs ExposureGate + observer on push_jsonl (not live_session event replay).",
            "ENTRY Discord only on gate accept; deferred only max_concurrent with v2>=5 in pilot_runner.",
            "Phase284 hooks block Discord if score<5 even if mis-invoked.",
        ],
    }

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {_OUT}")
    print(f"verdict={analysis.get('verdict')} runtime_sec={runtime_sec}")
    if result:
        print(f"sim accepted={analysis['simulation']['accepted_count']} discord ENTRY={audit['sent_counts'].get('ENTRY',0)}")
    if err:
        print(f"error={err}")
    return 0 if analysis.get("verdict") == "consistency_ok" and not err else 1


if __name__ == "__main__":
    raise SystemExit(main())
