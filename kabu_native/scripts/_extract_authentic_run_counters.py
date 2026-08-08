#!/usr/bin/env python3
"""Extract authentic first-run counters from terminal 611167 dump."""
from __future__ import annotations

import json
import re
from pathlib import Path

TERM = Path(
    r"C:\Users\yhach\.cursor\projects\c-Users-yhach-Documents-tradebotfile-kabu-native"
    r"\terminals\611167.txt"
)
OUT = Path("results/research/e1_x5_canonical_path_unify_20260728/_authentic_run_extract.json")


def main() -> None:
    text = TERM.read_text(encoding="utf-8", errors="replace")
    configs = []
    for m in re.finditer(
        r'"config":\s*"([^"]+)".*?"cap_blocked":\s*(\d+).*?"same_symbol_blocked":\s*(\d+)',
        text,
        re.S,
    ):
        configs.append(
            {
                "config": m.group(1),
                "cap_blocked": int(m.group(2)),
                "same_symbol_blocked": int(m.group(3)),
            }
        )
    # de-dupe keeping first occurrence order for G1構成別
    seen = set()
    uniq = []
    for c in configs:
        if c["config"] in seen:
            continue
        seen.add(c["config"])
        uniq.append(c)

    orphan_m = re.search(
        r'"orphan_open":\s*(\d+).*?"reasons":\s*(\{[^}]+\})',
        text,
        re.S,
    )
    base_cap = re.search(r'"全window_BASE合計"[\s\S]*?"cap_blocked":\s*(\d+)', text)
    base_same = re.search(r'"全window_BASE合計"[\s\S]*?"same_symbol_blocked":\s*(\d+)', text)

    pm_hash = re.search(r'"standalone_ledger":\s*"([0-9a-f]{64})"', text)
    hash_ok = re.search(r'"hash_match_expected":\s*(true|false)', text)
    expected = re.search(r'"expected_hash":\s*"([0-9a-f]{64})"', text)

    extract = {
        "verdict_line": "E1_X5_RETROSPECTIVE_VALID_WINDOW_REEVALUATION_BLOCKED"
        if "REEVALUATION_BLOCKED" in text
        else None,
        "base_cap_blocked": int(base_cap.group(1)) if base_cap else None,
        "base_same_symbol_blocked": int(base_same.group(1)) if base_same else None,
        "orphan_open_n": int(orphan_m.group(1)) if orphan_m else None,
        "orphan_reasons_raw": orphan_m.group(2) if orphan_m else None,
        "config_counters": uniq,
        "parity_standalone_ledger": pm_hash.group(1) if pm_hash else None,
        "parity_expected_hash": expected.group(1) if expected else None,
        "parity_hash_match_expected": (hash_ok.group(1) == "true") if hash_ok else None,
        "failed_tests": ["parity_pm_hash_match"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(extract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(extract, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
