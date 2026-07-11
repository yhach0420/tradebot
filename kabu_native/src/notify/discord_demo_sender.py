"""Phase687W10B — Discord full notification demo sender.

Explicit CLI only (`--send-demo-all`). Never touches Paper/Capture/canonical.
Uses a demo-only dedupe store so production Monday notifications are never DEDUPED.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

from notify.discord_notification_audit import mask_secrets_text
from notify.discord_notification_dedupe import DedupeStore
from notify.discord_notification_model import (
    ActualOrShadow,
    NotificationCategory,
    NotificationEnvelope,
    Severity,
    WEBHOOK_ENV_CAP,
    WEBHOOK_ENV_CAPTURE,
    WEBHOOK_ENV_CRITICAL,
    WEBHOOK_ENV_OPERATIONS,
    WEBHOOK_ENV_RESEARCH,
    WEBHOOK_ENV_TRADE,
    build_envelope,
    trading_date_jst,
)
from notify.discord_notification_router import resolve_webhook_url

JST = ZoneInfo("Asia/Tokyo")

DEMO_BANNER = "[DEMO - NO REAL TRADE]"
DEMO_BODY_MARKERS = (
    "デモ通知",
    "実際のENTRY/EXITではありません",
    "実注文は実行されていません",
    "サンプル値です",
)
DEMO_SYMBOL = "DEMO.T"
DEMO_DEDUPE_REL = Path("runtime") / "discord_notification_demo_dedupe.jsonl"

# Primary destination keys for reporting (no cross-category fallback).
DEMO_DESTINATION_KEYS: dict[str, str] = {
    NotificationCategory.TRADE_ACTUAL.value: WEBHOOK_ENV_TRADE,
    NotificationCategory.SESSION_SUMMARY.value: WEBHOOK_ENV_TRADE,
    NotificationCategory.CAP_BLOCKED.value: WEBHOOK_ENV_CAP,
    NotificationCategory.OPERATIONS.value: WEBHOOK_ENV_OPERATIONS,
    NotificationCategory.MARKET_CAPTURE.value: WEBHOOK_ENV_CAPTURE,
    NotificationCategory.RESEARCH_SHADOW.value: WEBHOOK_ENV_RESEARCH,
    NotificationCategory.CRITICAL_SAFETY.value: WEBHOOK_ENV_CRITICAL,
}

# Demo routing: same-category keys only; NEVER fall back CRITICAL→OPS or to trade-notify.
DEMO_CATEGORY_KEYS: dict[NotificationCategory, tuple[str, ...]] = {
    NotificationCategory.TRADE_ACTUAL: (WEBHOOK_ENV_TRADE,),
    NotificationCategory.SESSION_SUMMARY: (WEBHOOK_ENV_TRADE,),
    NotificationCategory.CAP_BLOCKED: (WEBHOOK_ENV_CAP,),
    NotificationCategory.OPERATIONS: (WEBHOOK_ENV_OPERATIONS,),
    NotificationCategory.MARKET_CAPTURE: (WEBHOOK_ENV_CAPTURE,),
    NotificationCategory.RESEARCH_SHADOW: (WEBHOOK_ENV_RESEARCH,),
    NotificationCategory.CRITICAL_SAFETY: (WEBHOOK_ENV_CRITICAL,),
}


def _now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d-%H%M%S")


def _unique_demo_run_id() -> str:
    import uuid

    return f"discord-demo-{_now_stamp()}-{uuid.uuid4().hex[:8]}"


def _time_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def wrap_demo_content(body: str) -> str:
    lines = [DEMO_BANNER, "", *DEMO_BODY_MARKERS, "", body.strip()]
    return "\n".join(lines)


def demo_disclaimer_ok(text: str) -> bool:
    return DEMO_BANNER in text and all(m in text for m in DEMO_BODY_MARKERS)


@dataclass
class DemoSpec:
    seq: int
    category: NotificationCategory
    event_type: str
    title: str
    body: str
    severity: Severity = Severity.INFO
    actual_or_shadow: ActualOrShadow = ActualOrShadow.NONE
    am_pm: str = ""
    symbol: str = ""


def build_demo_specs() -> list[DemoSpec]:
    """Exactly 17 demo notifications in required order."""
    specs: list[DemoSpec] = [
        DemoSpec(
            1,
            NotificationCategory.OPERATIONS,
            "PAPER_RUNNER_STARTED",
            "[DEMO] PAPER RUNNER STARTED",
            "checked runner started (demo)\nlive_trading_enabled=false\norder_enabled=false",
            Severity.INFO,
            ActualOrShadow.OPERATIONS,
        ),
        DemoSpec(
            2,
            NotificationCategory.OPERATIONS,
            "PAPER_SESSION_FINISHED",
            "[DEMO] PAPER SESSION FINISHED",
            "paper session finished (demo)\nactual submit/cancel: 0 / 0",
            Severity.INFO,
            ActualOrShadow.OPERATIONS,
        ),
        DemoSpec(
            3,
            NotificationCategory.OPERATIONS,
            "POST_SESSION_CHECK_FAILED",
            "[DEMO] POST SESSION CHECK FAILED",
            "post session check failed (demo)\nreason: DEMO_POST_CHECK\noperator action: デモ通知のため対応不要",
            Severity.WARNING,
            ActualOrShadow.OPERATIONS,
        ),
        DemoSpec(
            4,
            NotificationCategory.OPERATIONS,
            "PAPER_BLOCKED_CAPTURE_CONTINUES",
            "[DEMO] PAPER BLOCKED - CAPTURE CONTINUES",
            "failed step: DEMO_PREFLIGHT\nreason: DEMO_BLOCK\nnext action: デモ通知のため対応不要\n"
            "Capture status: ONLINE\ncapture continues: true",
            Severity.WARNING,
            ActualOrShadow.OPERATIONS,
        ),
        DemoSpec(
            5,
            NotificationCategory.OPERATIONS,
            "DAILY_RUNNER_FINISHED",
            "[DEMO] DAILY RUNNER FINISHED",
            "daily runner finished (demo)\nAM/PM sessions completed\nactual submit/cancel: 0 / 0",
            Severity.INFO,
            ActualOrShadow.OPERATIONS,
        ),
        DemoSpec(
            6,
            NotificationCategory.MARKET_CAPTURE,
            "MARKET_CAPTURE_STARTED",
            "[DEMO] MARKET CAPTURE STARTED",
            "Capture status: ONLINE\nevents: 0\nsymbols: 0\nreason: DEMO_START",
            Severity.INFO,
            ActualOrShadow.CAPTURE,
        ),
        DemoSpec(
            7,
            NotificationCategory.MARKET_CAPTURE,
            "MARKET_CAPTURE_DEGRADED",
            "[DEMO] MARKET CAPTURE DEGRADED",
            "reason: DEMO_WEBSOCKET_DISCONNECT\ndisconnect duration: 5秒\ndropped events: 0\n"
            "registration mismatch: 0\noperator action: デモ通知のため対応不要",
            Severity.WARNING,
            ActualOrShadow.CAPTURE,
        ),
        DemoSpec(
            8,
            NotificationCategory.MARKET_CAPTURE,
            "MARKET_CAPTURE_FINISHED",
            "[DEMO] MARKET CAPTURE FINISHED",
            "total events: 0\nsymbols seen: 0\ndisconnects: 1\ndrops: 0\ngaps: 0\n"
            "Capture status: FINISHED\nCapture Seal: DEMO_ONLY",
            Severity.INFO,
            ActualOrShadow.CAPTURE,
        ),
        DemoSpec(
            9,
            NotificationCategory.TRADE_ACTUAL,
            "ENTRY_ACTUAL",
            "[DEMO] ENTRY - ACTUAL",
            "銘柄: DEMO.T\n価格: 1,000円\n株数: 100株\n想定建玉金額: 100,000円\n"
            "ENTRY score: 3\n理由:\n・モメンタム条件成立\n・板の買い優勢\nCapture状態: ONLINE",
            Severity.INFO,
            ActualOrShadow.ACTUAL,
            symbol=DEMO_SYMBOL,
        ),
        DemoSpec(
            10,
            NotificationCategory.TRADE_ACTUAL,
            "EXIT_ACTUAL",
            "[DEMO] EXIT - ACTUAL",
            "銘柄: DEMO.T\nENTRY価格: 1,000円\nEXIT価格: 1,015円\n株数: 100株\n"
            "損益: +1,500円\n100株換算損益: +1,500円\n保有時間: 8分20秒\n"
            "理由: 利益保護\n最大含み益: +2.1%\n最大含み損: -0.3%",
            Severity.INFO,
            ActualOrShadow.ACTUAL,
            symbol=DEMO_SYMBOL,
        ),
        DemoSpec(
            11,
            NotificationCategory.CAP_BLOCKED,
            "CAP_BLOCKED",
            "[DEMO] CAP BLOCKED",
            "銘柄: DEMO.T\nactive_positions: 3\nposition_cap: 3\n理由: 保有上限到達",
            Severity.WARNING,
            ActualOrShadow.ACTUAL,
            symbol=DEMO_SYMBOL,
        ),
        DemoSpec(
            12,
            NotificationCategory.SESSION_SUMMARY,
            "PAPER_SUMMARY_AM",
            "[DEMO] PAPER SUMMARY - AM",
            "actual trades: 5\nwins / losses: 3 / 2\nwin rate: 60.0%\n"
            "total PnL yen_100: +4,500円\nprofit factor: 1.65\nactual submit/cancel: 0 / 0",
            Severity.INFO,
            ActualOrShadow.ACTUAL,
            am_pm="AM",
        ),
        DemoSpec(
            13,
            NotificationCategory.SESSION_SUMMARY,
            "PAPER_SUMMARY_PM",
            "[DEMO] PAPER SUMMARY - PM",
            "actual trades: 4\nwins / losses: 2 / 2\ntotal PnL yen_100: -1,200円\n"
            "actual submit/cancel: 0 / 0",
            Severity.INFO,
            ActualOrShadow.ACTUAL,
            am_pm="PM",
        ),
        DemoSpec(
            14,
            NotificationCategory.SESSION_SUMMARY,
            "PAPER_SUMMARY_DAILY",
            "[DEMO] PAPER SUMMARY - DAILY",
            "AM PnL: +4,500円\nPM PnL: -1,200円\nDaily total: +3,300円\n"
            "AM trades: 5\nPM trades: 4\nactual submit/cancel: 0 / 0",
            Severity.INFO,
            ActualOrShadow.ACTUAL,
            am_pm="DAILY",
        ),
        DemoSpec(
            15,
            NotificationCategory.RESEARCH_SHADOW,
            "SHADOW_SUMMARY_AM",
            "[DEMO] SHADOW SUMMARY - AM",
            "I candidates: 3\nH candidates: 2\nC candidates: 4\n"
            "hypothetical PnL: +2,100円\nADOPTION STATUS: NOT ADOPTED\nDATA COLLECTION ONLY",
            Severity.INFO,
            ActualOrShadow.SHADOW,
            am_pm="AM",
        ),
        DemoSpec(
            16,
            NotificationCategory.RESEARCH_SHADOW,
            "SHADOW_SUMMARY_PM",
            "[DEMO] SHADOW SUMMARY - PM",
            "I candidates: 3\nH candidates: 2\nC candidates: 4\n"
            "hypothetical PnL: +2,100円\nADOPTION STATUS: NOT ADOPTED\nDATA COLLECTION ONLY",
            Severity.INFO,
            ActualOrShadow.SHADOW,
            am_pm="PM",
        ),
        DemoSpec(
            17,
            NotificationCategory.CRITICAL_SAFETY,
            "CRITICAL_SAFETY",
            "[DEMO] CRITICAL SAFETY",
            "failure type: DEMO_RECONCILIATION_MISMATCH\nENTRY allowed: false\nEXIT allowed: true\n"
            "actual submit/cancel: 0 / 0\noperator action: デモ通知のため対応不要",
            Severity.CRITICAL,
            ActualOrShadow.NONE,
        ),
    ]
    assert len(specs) == 17
    return specs


@dataclass
class DemoSendResult:
    seq: int
    category: str
    event_type: str
    title: str
    destination_key: str
    status: str
    notification_id: str = ""
    payload_hash: str = ""
    http_status: Optional[int] = None
    retry_count: int = 0
    latency_ms: Optional[float] = None
    error: str = ""
    dedupe_key: str = ""


@dataclass
class DemoRunReport:
    demo_run_id: str
    session_id: str
    trading_date: str
    started_at: str
    ended_at: str = ""
    results: list[DemoSendResult] = field(default_factory=list)
    live_trading_enabled: bool = False
    order_enabled: bool = False
    submit: int = 0
    cancel: int = 0
    audit_json: str = ""
    audit_log: str = ""
    production_dedupe_untouched: bool = True
    kabu_api_calls: int = 0
    exit_code: int = 0

    def counts(self) -> dict[str, int]:
        sent = skipped = failed = 0
        for r in self.results:
            if r.status == "SENT":
                sent += 1
            elif r.status.startswith("SKIPPED") or r.status == "DEDUPED":
                skipped += 1
            else:
                failed += 1
        return {"total": len(self.results), "sent": sent, "skipped": skipped, "failed": failed}

    def by_category(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.results:
            bucket = out.setdefault(r.category, {"sent": 0, "skipped": 0, "failed": 0})
            if r.status == "SENT":
                bucket["sent"] += 1
            elif r.status.startswith("SKIPPED") or r.status == "DEDUPED":
                bucket["skipped"] += 1
            else:
                bucket["failed"] += 1
        return out

    def missing_webhooks(self) -> list[str]:
        return sorted(
            {
                r.destination_key or r.category
                for r in self.results
                if r.status == "SKIPPED_WEBHOOK_NOT_CONFIGURED"
            }
        )

    def failures(self) -> list[dict[str, str]]:
        return [
            {
                "category": r.category,
                "event_type": r.event_type,
                "status": r.status,
                "error": r.error,
            }
            for r in self.results
            if r.status == "FAILED"
        ]

    def to_dict(self) -> dict[str, Any]:
        c = self.counts()
        return {
            "demo_run_id": self.demo_run_id,
            "session_id": self.session_id,
            "trading_date": self.trading_date,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total": c["total"],
            "sent": c["sent"],
            "skipped": c["skipped"],
            "failed": c["failed"],
            "by_category": self.by_category(),
            "missing_webhooks": self.missing_webhooks(),
            "failures": self.failures(),
            "results": [r.__dict__ for r in self.results],
            "live_trading_enabled": self.live_trading_enabled,
            "order_enabled": self.order_enabled,
            "submit": self.submit,
            "cancel": self.cancel,
            "actual_submit_cancel": f"{self.submit} / {self.cancel}",
            "real_orders": "DISABLED",
            "production_dedupe_untouched": self.production_dedupe_untouched,
            "kabu_api_calls": self.kabu_api_calls,
            "audit_json": self.audit_json,
            "audit_log": self.audit_log,
            "exit_code": self.exit_code,
            "secrets_present": False,
        }


def _envelope_for_spec(
    spec: DemoSpec,
    *,
    demo_run_id: str,
    session_id: str,
    trading_date: str,
) -> NotificationEnvelope:
    content = wrap_demo_content(spec.body)
    assert demo_disclaimer_ok(content)
    assert "DEMO" in spec.title
    dedupe_key = f"demo|{demo_run_id}|{spec.seq}|{spec.category.value}|{spec.event_type}"
    return build_envelope(
        category=spec.category,
        severity=spec.severity,
        event_type=f"DEMO_{spec.event_type}",
        title=spec.title,
        content=content,
        trading_date=trading_date,
        session_id=session_id,
        am_pm=spec.am_pm,
        symbol=spec.symbol,
        source_module="discord_demo_sender",
        dedupe_key=dedupe_key,
        actual_or_shadow=spec.actual_or_shadow,
        ownership="DEMO",
        extra={"demo": True, "demo_run_id": demo_run_id, "demo_seq": spec.seq},
    )


def _send_one(
    envelope: NotificationEnvelope,
    *,
    url: str,
    destination_key: str,
    timeout_sec: float = 8.0,
    max_retries: int = 3,
    post_fn: Optional[Callable[..., Any]] = None,
) -> DemoSendResult:
    post = post_fn or requests.post
    last_err = ""
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    retries = 0
    payload = envelope.discord_payload()
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = post(url, json=payload, timeout=timeout_sec)
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            http_status = int(resp.status_code)
            if http_status == 429:
                retries = attempt
                retry_after = float(resp.headers.get("Retry-After") or 1.0)
                time.sleep(min(5.0, max(0.2, retry_after)))
                last_err = "HTTP_429"
                continue
            if http_status >= 400:
                retries = attempt
                last_err = f"HTTP_{http_status}"
                time.sleep(min(2.0, 0.2 * attempt))
                continue
            return DemoSendResult(
                seq=int((envelope.extra or {}).get("demo_seq") or 0),
                category=envelope.category,
                event_type=envelope.event_type,
                title=envelope.title,
                destination_key=destination_key,
                status="SENT",
                notification_id=envelope.notification_id,
                payload_hash=envelope.payload_hash,
                http_status=http_status,
                retry_count=max(0, attempt - 1),
                latency_ms=latency_ms,
                dedupe_key=envelope.dedupe_key,
            )
        except requests.Timeout:
            retries = attempt
            last_err = "TIMEOUT"
            time.sleep(min(2.0, 0.2 * attempt))
        except requests.ConnectionError:
            retries = attempt
            last_err = "CONNECTION"
            time.sleep(min(2.0, 0.2 * attempt))
        except Exception as exc:
            last_err = type(exc).__name__
            break
    return DemoSendResult(
        seq=int((envelope.extra or {}).get("demo_seq") or 0),
        category=envelope.category,
        event_type=envelope.event_type,
        title=envelope.title,
        destination_key=destination_key,
        status="FAILED",
        notification_id=envelope.notification_id,
        payload_hash=envelope.payload_hash,
        http_status=http_status,
        retry_count=retries,
        latency_ms=latency_ms,
        error=last_err,
        dedupe_key=envelope.dedupe_key,
    )


def _write_audit(native_root: Path, report: DemoRunReport) -> tuple[Path, Path]:
    day = report.trading_date
    stamp = _time_stamp()
    demo_dir = Path(native_root) / "results" / "notifications" / day / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    json_path = demo_dir / f"discord_demo_{stamp}.json"
    log_path = demo_dir / f"discord_demo_{stamp}.log"
    payload = report.to_dict()
    text = mask_secrets_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    json_path.write_text(text, encoding="utf-8")
    lines = [
        "[DISCORD DEMO RESULT]",
        f"demo_run_id: {report.demo_run_id}",
        f"total: {payload['total']}",
        f"sent: {payload['sent']}",
        f"skipped: {payload['skipped']}",
        f"failed: {payload['failed']}",
        "",
    ]
    for cat in (
        "TRADE_ACTUAL",
        "SESSION_SUMMARY",
        "CAP_BLOCKED",
        "OPERATIONS",
        "MARKET_CAPTURE",
        "RESEARCH_SHADOW",
        "CRITICAL_SAFETY",
    ):
        b = payload["by_category"].get(cat) or {"sent": 0, "skipped": 0, "failed": 0}
        lines.append(f"{cat}: sent={b['sent']} skipped={b['skipped']} failed={b['failed']}")
    lines.append("")
    lines.append(f"actual submit/cancel: {report.submit} / {report.cancel}")
    lines.append("real orders: DISABLED")
    if payload["missing_webhooks"]:
        lines.append("")
        lines.append(f"missing webhooks: {', '.join(payload['missing_webhooks'])}")
    if payload["failures"]:
        lines.append("")
        lines.append("failures:")
        for f in payload["failures"]:
            lines.append(
                f"  - {f['category']} {f['event_type']}: {f['status']} {f.get('error') or ''}"
            )
    for r in report.results:
        lines.append(
            f"{r.seq:02d} {r.category} {r.event_type} status={r.status} "
            f"dest={r.destination_key} http={r.http_status} latency_ms={r.latency_ms}"
        )
    log_path.write_text(mask_secrets_text("\n".join(lines) + "\n"), encoding="utf-8")
    return json_path, log_path


def format_console_result(report: DemoRunReport) -> str:
    d = report.to_dict()
    lines = [
        "[DISCORD DEMO RESULT]",
        "",
        f"demo_run_id: {d['demo_run_id']}",
        f"total: {d['total']}",
        f"sent: {d['sent']}",
        f"skipped: {d['skipped']}",
        f"failed: {d['failed']}",
        "",
    ]
    for cat in (
        "TRADE_ACTUAL",
        "SESSION_SUMMARY",
        "CAP_BLOCKED",
        "OPERATIONS",
        "MARKET_CAPTURE",
        "RESEARCH_SHADOW",
        "CRITICAL_SAFETY",
    ):
        b = d["by_category"].get(cat) or {"sent": 0, "skipped": 0, "failed": 0}
        lines.append(f"{cat}: sent={b['sent']} skipped={b['skipped']} failed={b['failed']}")
    lines.append("")
    lines.append(f"actual submit/cancel: {d['submit']} / {d['cancel']}")
    lines.append("real orders: DISABLED")
    if d["missing_webhooks"]:
        lines.append("")
        lines.append(f"missing: {', '.join(d['missing_webhooks'])}")
    if d["failures"]:
        lines.append("")
        lines.append("failures:")
        for f in d["failures"]:
            lines.append(
                f"  - {f['category']} / {f['event_type']}: {f.get('error') or f['status']}"
            )
    return "\n".join(lines)


def run_discord_demo_all(
    native_root: Path,
    *,
    post_fn: Optional[Callable[..., Any]] = None,
    dry_run_no_http: bool = False,
) -> DemoRunReport:
    """
    Send all 17 demo notifications. Does not use production dedupe store.
    dry_run_no_http: build + route only (tests); marks SENT when URL present without HTTP.
    """
    from small_paper.env_loader import ensure_repo_dotenv

    ensure_repo_dotenv()
    root = Path(native_root)
    demo_run_id = _unique_demo_run_id()
    session_id = demo_run_id
    trading_date = trading_date_jst()
    started = datetime.now(JST).isoformat(timespec="seconds")

    prod_dedupe_path = root / "runtime" / "discord_notification_dedupe.jsonl"
    prod_before = prod_dedupe_path.read_bytes() if prod_dedupe_path.is_file() else b""

    demo_dedupe = DedupeStore(root / DEMO_DEDUPE_REL)
    report = DemoRunReport(
        demo_run_id=demo_run_id,
        session_id=session_id,
        trading_date=trading_date,
        started_at=started,
    )

    for spec in build_demo_specs():
        env = _envelope_for_spec(
            spec,
            demo_run_id=demo_run_id,
            session_id=session_id,
            trading_date=trading_date,
        )
        assert env.extra.get("demo") is True
        keys = DEMO_CATEGORY_KEYS[spec.category]
        dest = DEMO_DESTINATION_KEYS[spec.category.value]
        url, key_used = resolve_webhook_url(keys)
        if not url:
            report.results.append(
                DemoSendResult(
                    seq=spec.seq,
                    category=spec.category.value,
                    event_type=env.event_type,
                    title=spec.title,
                    destination_key=dest,
                    status="SKIPPED_WEBHOOK_NOT_CONFIGURED",
                    notification_id=env.notification_id,
                    payload_hash=env.payload_hash,
                    dedupe_key=env.dedupe_key,
                )
            )
            continue

        chk = demo_dedupe.check(env.dedupe_key)
        if not chk.get("allow"):
            report.results.append(
                DemoSendResult(
                    seq=spec.seq,
                    category=spec.category.value,
                    event_type=env.event_type,
                    title=spec.title,
                    destination_key=key_used or dest,
                    status="DEDUPED",
                    notification_id=env.notification_id,
                    payload_hash=env.payload_hash,
                    dedupe_key=env.dedupe_key,
                    error="same demo_run duplicate",
                )
            )
            continue

        if dry_run_no_http:
            result = DemoSendResult(
                seq=spec.seq,
                category=spec.category.value,
                event_type=env.event_type,
                title=spec.title,
                destination_key=key_used or dest,
                status="SENT",
                notification_id=env.notification_id,
                payload_hash=env.payload_hash,
                http_status=204,
                retry_count=0,
                latency_ms=0.0,
                dedupe_key=env.dedupe_key,
            )
        else:
            env.webhook_env_key = key_used
            result = _send_one(env, url=url, destination_key=key_used or dest, post_fn=post_fn)

        if result.status == "SENT":
            demo_dedupe.record(
                dedupe_key=env.dedupe_key,
                status="SENT",
                notification_id=env.notification_id,
                payload_hash=env.payload_hash,
                severity=env.severity,
            )
        report.results.append(result)

    report.ended_at = datetime.now(JST).isoformat(timespec="seconds")
    prod_after = prod_dedupe_path.read_bytes() if prod_dedupe_path.is_file() else b""
    report.production_dedupe_untouched = prod_before == prod_after

    if report.counts()["failed"] > 0:
        report.exit_code = 1
    else:
        report.exit_code = 0

    json_path, log_path = _write_audit(root, report)
    report.audit_json = str(json_path)
    report.audit_log = str(log_path)
    return report


def assert_no_real_symbols(specs: Sequence[DemoSpec]) -> None:
    banned = ("7203", "6758", "9984", "8306", "6501")
    for s in specs:
        blob = s.title + "\n" + s.body + "\n" + s.symbol
        for b in banned:
            if b in blob:
                raise AssertionError(f"real-looking symbol/code leaked: {b}")
        if s.symbol and s.symbol != DEMO_SYMBOL:
            raise AssertionError(f"unexpected symbol: {s.symbol}")
