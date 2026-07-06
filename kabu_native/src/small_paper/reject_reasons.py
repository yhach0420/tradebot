"""Gate reject reason strings for Discord/formatting (mirror research.exposure_gate)."""

REJECT_MAX_CONCURRENT = "max_concurrent"
REJECT_SAME_SYMBOL_OPEN_OVERLAP = "REJECT_SAME_SYMBOL_OPEN_OVERLAP"
REJECT_MAX_ENTRIES_PER_SCAN = "max_entries_per_scan"
REJECT_OR_CAP_FULL = "or_cap_full"
REJECT_PBV2_CAP_FULL = "pbv2_cap_full"

# ENTRY qualified but blocked after gate — notify via trade-cap-blocked webhook only.
ENTRY_BLOCKED_DISCORD_NOTIFY_REASONS = frozenset(
    {
        REJECT_MAX_CONCURRENT,
        REJECT_SAME_SYMBOL_OPEN_OVERLAP,
        REJECT_MAX_ENTRIES_PER_SCAN,
        REJECT_OR_CAP_FULL,
        REJECT_PBV2_CAP_FULL,
    }
)

ENTRY_BLOCKED_DISCORD_LABEL_JA: dict[str, str] = {
    REJECT_MAX_CONCURRENT: "保有上限到達",
    REJECT_SAME_SYMBOL_OPEN_OVERLAP: "同一銘柄保有中（overlap）",
    REJECT_MAX_ENTRIES_PER_SCAN: "スキャン採用上限超過",
    REJECT_OR_CAP_FULL: "OR枠上限到達",
    REJECT_PBV2_CAP_FULL: "PBv2枠上限到達",
}


def is_entry_blocked_discord_notify_reason(reason: str) -> bool:
    return str(reason or "") in ENTRY_BLOCKED_DISCORD_NOTIFY_REASONS


def entry_blocked_discord_label(reason: str) -> str:
    key = str(reason or "")
    return ENTRY_BLOCKED_DISCORD_LABEL_JA.get(key, key or "ENTRY条件成立だが採用不可")
