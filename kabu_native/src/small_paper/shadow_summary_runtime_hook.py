"""Phase687W10A — RESEARCH_SHADOW summary hook on real AM/PM finalize.

Ownership: RESEARCH only (not Checked Runner / not trade-notify).
Does not change Shadow calculations, canonical summary, or actual PnL.
Fail-open: never blocks Paper finalize.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from notify.discord_notification_formatter import drop_none_lines, format_shadow_summary
from notify.discord_notification_model import (
    ActualOrShadow,
    NotificationCategory,
    Severity,
    build_envelope,
    trading_date_jst,
)

log = logging.getLogger("kabu_native.shadow_summary_runtime_hook")

OWNERSHIP = "RESEARCH"
SHADOW_NAME_COMPOSITE = "forward_shadow_bundle"


def session_kind_am_pm(summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session") or {}
    kind = str(am_pm.get("kind") or "").lower()
    if kind in ("am", "pm"):
        return kind
    # fallbacks used by some session configs
    label = str(summary.get("session_label") or summary.get("session_am_pm") or "").lower()
    if label.startswith("am") or label == "morning":
        return "am"
    if label.startswith("pm") or label == "afternoon":
        return "pm"
    return ""


def resolve_forward_days(summary: Mapping[str, Any]) -> int:
    """Best-effort forward session/day count for NP Logger band display only."""
    for key in (
        "forward_sessions",
        "np_forward_days",
        "trade_overlap_days",
        "w4s_forward_sessions",
    ):
        v = summary.get(key)
        if v is not None:
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                pass
    for nest_key in ("sector_heat_forward_shadow", "risk_sizing_forward_shadow", "equity_dynamic_stop_shadow"):
        nest = summary.get(nest_key)
        if isinstance(nest, Mapping):
            for k in ("trade_overlap_days", "days", "day_count"):
                if nest.get(k) is not None:
                    try:
                        return max(0, int(nest.get(k)))
                    except (TypeError, ValueError):
                        pass
    return 0


def np_logger_band(forward_days: int) -> str:
    if forward_days < 5:
        return "DATA COLLECTION ONLY"
    if forward_days < 10:
        return "RULE DISCOVERY NOT ALLOWED"
    return "RULE DISCOVERY REVIEW ALLOWED"


def _artifact_hash(summary: Mapping[str, Any], *, am_pm: str) -> str:
    keys = sorted(
        k
        for k in summary.keys()
        if "shadow" in k.lower() or k.startswith("np_pre_entry") or k.startswith("ihc_")
    )
    payload = {k: summary.get(k) for k in keys}
    payload["_am_pm"] = am_pm
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def collect_shadow_sections(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only extraction from finalized summary (no recalculation)."""
    from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines

    lines = format_research_shadow_daily_summary_lines(summary, omit_operator_covered=False)
    ihc_lines = [ln for ln in lines if "IHC" in ln or ln.startswith("I∨H∨C") or "microsequence" in ln.lower()]
    # Prefer dedicated IHC formatters when present
    try:
        from small_paper.shadow_ihc_portfolio import format_ihc_shadow_discord_lines

        ihc_lines = format_ihc_shadow_discord_lines(summary) or ihc_lines
    except Exception:
        pass
    try:
        from small_paper.ihc_shadow_counterfactual import format_entry_shadow_discord_lines

        entry_shadow = format_entry_shadow_discord_lines(summary)
        if entry_shadow:
            ihc_lines = list(ihc_lines) + list(entry_shadow)
    except Exception:
        pass

    candidates = 0
    hyp_fills = 0
    hyp_pnl = None
    for k, v in summary.items():
        lk = str(k).lower()
        if "shadow" not in lk and not lk.startswith("ihc_") and not lk.startswith("np_"):
            continue
        if "count" in lk and isinstance(v, (int, float)):
            candidates += int(v) if "block" in lk or "candidate" in lk or "accept" in lk else 0
        if "fill" in lk and isinstance(v, (int, float)):
            hyp_fills += int(v)
        if ("pnl" in lk or "delta_yen" in lk) and "actual" not in lk:
            try:
                hyp_pnl = (hyp_pnl or 0) + float(v)
            except (TypeError, ValueError):
                pass

    np_enabled = bool(summary.get("np_pre_entry_feature_logger_enabled"))
    forward_days = resolve_forward_days(summary)
    return {
        "shadow_name": SHADOW_NAME_COMPOSITE,
        "candidates": candidates or summary.get("np_pre_entry_logger_accept_count") or 0,
        "hypothetical_fills": hyp_fills,
        "hypothetical_pnl": hyp_pnl,
        "hypothetical_pnl_yen_100": hyp_pnl,
        "actual_overlap": summary.get("ihc_overlap_count"),
        "data_completeness": (
            "OK"
            if (lines or ihc_lines or np_enabled or summary.get("ihc_union_shadow_block_count") is not None)
            else "MISSING"
        ),
        "forward_sessions": forward_days,
        "ihc_section": "\n".join(ihc_lines) if ihc_lines else "",
        "research_lines": lines,
        "np_logger_enabled": np_enabled,
        "np_band": np_logger_band(forward_days),
        "execution_policy_present": bool(
            summary.get("execution_policy_shadow_count")
            or summary.get("kabu_execution_policy_shadow")
            or summary.get("execution_policy_shadow")
        ),
    }


def shadow_artifacts_ready(summary: Mapping[str, Any], *, am_pm: str) -> tuple[bool, str]:
    if am_pm not in ("am", "pm"):
        return False, "not_am_pm_session"
    if summary.get("summary_integrity_error"):
        return False, "summary_integrity_error"
    data = collect_shadow_sections(summary)
    if data["data_completeness"] == "MISSING" and not data["research_lines"] and not data["np_logger_enabled"]:
        return False, "SHADOW_SUMMARY_ARTIFACT_NOT_READY"
    # finalized marker: canonical present means session summary built
    if not isinstance(summary.get("canonical_summary"), Mapping) and not data["research_lines"]:
        return False, "SHADOW_SUMMARY_ARTIFACT_NOT_READY"
    return True, "ok"


def build_shadow_summary_content(
    summary: Mapping[str, Any],
    *,
    am_pm: str,
    artifact_path: str = "",
    artifact_hash: str = "",
) -> str:
    data = collect_shadow_sections(summary)
    title = f"[SHADOW SUMMARY - {am_pm.upper()}]"
    base = format_shadow_summary(
        {
            "shadow_name": data["shadow_name"],
            "candidates": data["candidates"],
            "hypothetical_fills": data["hypothetical_fills"],
            "hypothetical_pnl": data.get("hypothetical_pnl_yen_100")
            if data.get("hypothetical_pnl_yen_100") is not None
            else 0,
            "actual_overlap": data["actual_overlap"]
            if data.get("actual_overlap") is not None
            else 0,
            "data_completeness": data["data_completeness"],
            "forward_sessions": data["forward_sessions"],
        }
    )
    # Replace generic title with AM/PM-specific
    lines = [title] + base.splitlines()[1:]
    lines.append(f"source artifact: {artifact_path or '(session summary)'}")
    if artifact_hash:
        lines.append(f"artifact_hash: {artifact_hash}")
    if data["ihc_section"]:
        lines.append("--- I/H/C ---")
        lines.append(data["ihc_section"])
    if data["np_logger_enabled"] or data["forward_sessions"] is not None:
        lines.append("--- Phase687 NP Logger ---")
        lines.append(data["np_band"])
    if data["execution_policy_present"]:
        lines.append("--- Execution Policy Shadow ---")
        lines.append("present=true")
    text = "\n".join(drop_none_lines(lines))
    assert "採用可能" not in text
    return text


def enqueue_shadow_summary_for_session(
    summary: Mapping[str, Any],
    *,
    native_root: Path,
    output_dir: Optional[Path] = None,
    session_id: str = "",
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    """
    Call after Shadow finalize + completeness. Enqueues RESEARCH_SHADOW only for AM/PM.
    Never raises into Paper finalize.
    """
    try:
        return _enqueue_inner(
            summary,
            native_root=native_root,
            output_dir=output_dir,
            session_id=session_id,
            trading_date=trading_date,
        )
    except Exception as exc:
        log.warning("shadow summary hook failed (fail-open): %s", exc)
        try:
            from notify.discord_notification_router import get_router

            get_router(native_root).audit.record_event(
                {
                    "status": "SKIPPED",
                    "category": NotificationCategory.RESEARCH_SHADOW.value,
                    "error_category": type(exc).__name__,
                    "reason": "SHADOW_SUMMARY_HOOK_EXCEPTION",
                }
            )
        except Exception:
            pass
        return {"status": "FAILED", "queued": False, "error": type(exc).__name__}


def _enqueue_inner(
    summary: Mapping[str, Any],
    *,
    native_root: Path,
    output_dir: Optional[Path],
    session_id: str,
    trading_date: Optional[str],
) -> dict[str, Any]:
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass
    from notify.discord_notification_router import get_router

    am_pm = session_kind_am_pm(summary)
    router = get_router(Path(native_root))
    day = trading_date or str(summary.get("trading_date") or trading_date_jst())
    sid = session_id or str(summary.get("session_id") or "")

    if am_pm not in ("am", "pm"):
        # Daily: do not send (no AM/PM duplicate)
        router.audit.record_event(
            {
                "status": "SKIPPED",
                "category": NotificationCategory.RESEARCH_SHADOW.value,
                "reason": "DAILY_SHADOW_SUMMARY_SUPPRESSED",
                "ownership": OWNERSHIP,
            }
        )
        return {"status": "SKIPPED_DAILY", "queued": False, "am_pm": am_pm or "daily"}

    ready, reason = shadow_artifacts_ready(summary, am_pm=am_pm)
    if not ready:
        router.audit.record_event(
            {
                "status": "SKIPPED",
                "category": NotificationCategory.RESEARCH_SHADOW.value,
                "reason": "SHADOW_SUMMARY_ARTIFACT_NOT_READY",
                "detail": reason,
                "ownership": OWNERSHIP,
                "am_pm": am_pm,
            }
        )
        return {"status": "SHADOW_SUMMARY_ARTIFACT_NOT_READY", "queued": False, "am_pm": am_pm}

    art_hash = _artifact_hash(summary, am_pm=am_pm)
    art_path = str(output_dir) if output_dir else ""
    content = build_shadow_summary_content(
        summary, am_pm=am_pm, artifact_path=art_path, artifact_hash=art_hash
    )
    # Guard: no actual total PnL language
    low = content.lower()
    if "actual total" in low or "canonical total" in low:
        content = content.replace("actual total", "[redacted]").replace("Actual total", "[redacted]")

    # Phase687W25C-R2: only enabled shadows with today's count > 0
    from small_paper.discord_message_builder import (
        audit_discord_shadow_inventory,
        build_shadow_observation_embed_payload,
        collect_active_shadow_observations,
        embed_to_discord_payload,
    )

    active = collect_active_shadow_observations(summary)
    inventory_audit = audit_discord_shadow_inventory(summary)
    if not active:
        router.audit.record_event(
            {
                "status": "SKIPPED",
                "category": NotificationCategory.RESEARCH_SHADOW.value,
                "reason": "NO_ACTIVE_SHADOW_FOR_DISCORD",
                "ownership": OWNERSHIP,
                "am_pm": am_pm,
                "inventory": inventory_audit,
            }
        )
        return {
            "status": "SKIPPED_NO_ACTIVE_SHADOW",
            "queued": False,
            "am_pm": am_pm,
            "inventory": inventory_audit,
        }

    shadow_embed = build_shadow_observation_embed_payload(
        {
            "shadow_name": ", ".join(r["name"] for r in active),
            "blocks": sum(int(r.get("count") or 0) for r in active),
            "delta_yen": " / ".join(f"{r['name']}={r['delta']}" for r in active),
            "active_shadows": active,
        },
        am_pm=am_pm,
    )
    embed_body = embed_to_discord_payload(shadow_embed, content="")
    discord_embeds = list(embed_body.get("embeds") or [])

    # Stable once-per AM/PM identity; full key includes hash for UPDATE detection.
    stable_key = f"{day}|{sid}|{am_pm.upper()}|{SHADOW_NAME_COMPOSITE}"
    dedupe_key = f"{stable_key}|{art_hash}"
    prior = router.dedupe.check(stable_key)
    if not prior.get("allow"):
        prev = prior.get("previous") or {}
        prev_hash = str(prev.get("payload_hash") or "")
        if prev_hash and prev_hash != art_hash:
            router.audit.record_event(
                {
                    "status": "UPDATE_NO_AUTO_RESEND",
                    "category": NotificationCategory.RESEARCH_SHADOW.value,
                    "dedupe_key": dedupe_key,
                    "stable_key": stable_key,
                    "previous_hash": prev_hash,
                    "artifact_hash": art_hash,
                    "ownership": OWNERSHIP,
                    "am_pm": am_pm,
                    "reason": "artifact_hash_changed_operator_resend_only",
                }
            )
            return {
                "status": "UPDATE_NO_AUTO_RESEND",
                "queued": False,
                "am_pm": am_pm,
                "dedupe_key": dedupe_key,
                "ownership": OWNERSHIP,
            }
        router.audit.record_event(
            {
                "status": "DEDUPED",
                "category": NotificationCategory.RESEARCH_SHADOW.value,
                "dedupe_key": dedupe_key,
                "stable_key": stable_key,
                "ownership": OWNERSHIP,
                "am_pm": am_pm,
            }
        )
        return {
            "status": "DEDUPED",
            "queued": False,
            "am_pm": am_pm,
            "dedupe_key": dedupe_key,
            "ownership": OWNERSHIP,
        }

    env = build_envelope(
        category=NotificationCategory.RESEARCH_SHADOW,
        severity=Severity.INFO,
        event_type=f"SHADOW_SUMMARY_{am_pm.upper()}",
        title=str(shadow_embed.get("title") or "[SHADOW OBSERVATION]"),
        content="",
        embeds=discord_embeds,
        trading_date=day,
        session_id=sid,
        am_pm=am_pm.upper(),
        dedupe_key=stable_key,
        actual_or_shadow=ActualOrShadow.SHADOW,
        source_module="shadow_summary_runtime_hook",
        ownership=OWNERSHIP,
        artifact_path=art_path,
        state_version=art_hash,
        extra={
            "artifact_hash": art_hash,
            "full_dedupe_key": dedupe_key,
            "auto_resend": False,
            "shadow_text_audit": content[:500],
            "active_shadows": active,
            "inventory": inventory_audit,
        },
    )
    # Ensure payload_hash used by dedupe.record is the artifact hash
    env.payload_hash = art_hash
    outcome = router.publish(env)
    outcome["am_pm"] = am_pm
    outcome["dedupe_key"] = dedupe_key
    outcome["stable_key"] = stable_key
    outcome["ownership"] = OWNERSHIP
    return outcome
