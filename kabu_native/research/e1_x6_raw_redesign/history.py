"""Run-disposition history for the raw-feature redesign (append-only record)."""
from __future__ import annotations

SUPERSEDED_RUNS = {
    "e1x6r3_20260802_233645_144c3aab": {
        "disposition": "SUPERSEDED_PRE_ECONOMICS",
        "p1_sha256": "c100039605b74d6375b8fcf164bd635885c1e7e8c0be6bc0e1237574ffc3a347",
        "registry_sha256": "6ba0908ee823392d01ef2c418ce94b02b32c2ddc621810ff1691578857c706c9",
        "artifact_sha256": {
            "report.json": "17c877db5c1ee23682979e6614a7e8520301d4b8c9d49d2833f0ff9b21b4436d",
            "report.md": "b2550f65c2f21140206631327005375f885e731c32dfb178abb21bce5764d44b",
            "audit.xlsx": "45cda4e0ea0fcaecc74b62a9d0be8bfee09e3e728d096a28bc3322793fc80c71",
        },
        "reason": (
            "external audit before Phase B: field coverage equalled event-row missing "
            "rates instead of as-of grid coverage; fixed TICK=0.1; post-trigger "
            "confirmation ordered before TRIGGER in the state contract; PULL support "
            "condition (mid>=low_300s) tautological; BREAK 60s persistence only bound "
            "range_ratio; exit price basis / replay order / Phase B conventions "
            "under-specified. Superseded BEFORE any economics were generated."
        ),
        "artifacts_preserved_at": (
            r"C:\Users\yhach\e1x6_research_store\raw_feature_redesign"
            r"\e1x6r3_20260802_233645_144c3aab"
        ),
        "not_overwritten": True,
    },
    "e1x6r3r1_20260803_025749_8c66cd06": {
        "disposition": "SUPERSEDED_TICK_TABLE_BUG",
        "p1_sha256": "see run store p1_lock.json (BLOCKED run, preserved)",
        "reason": (
            "first A-R1 execution used the runtime narrow tick table that merges "
            "the official JPX 0.5-yen and 5-yen bands; 16 symbols with observed "
            "0.5/5-yen increments were falsely classified NO_TABLE_CONSISTENT. "
            "Superseded by a rerun with the official TOPIX500 fine table. "
            "No economics were generated; artifacts preserved."
        ),
        "artifacts_preserved_at": (
            r"C:\Users\yhach\e1x6_research_store\raw_feature_redesign"
            r"\e1x6r3r1_20260803_025749_8c66cd06"
        ),
        "not_overwritten": True,
    },
    "e1x6r3r1_20260803_031244_a7d98591": {
        "disposition": "VALID_BLOCK_EVIDENCE_R1",
        "verdict": "E1_X6_RAW_REDESIGN_P1_R1_BLOCKED",
        "p1_sha256": "4ff63a47866a08b8e4f78eda442a281fc13e74be5a9db2ee0289970714010dfc",
        "registry_sha256": "e71d1f1590bbb0541079ee45fe68d0f4432ecf872afe03bbdde2dbc1b92e0cff",
        "artifact_sha256": {
            "report.json": "99c8cb5a590d26d21c6f32ecfc38d9ff2f5c339c209c88800889d48ccb025f02",
            "report.md": "fe72b561687c8e8465a25a3c84769762f336de7920fa4e9417a34db0f7def6a3",
            "audit.xlsx": "e48a00376839a2b9d40e931c58c2bc463de386771e25344667188ab6288d9e4e",
        },
        "reason": (
            "kept unchanged as VALID evidence that FULL-grid as-of quote coverage "
            "(min 0.752485) fails the 0.90 gate. Phase A-R2 separates decision "
            "opportunities (symbol-PUSH-due grids) from full-grid state coverage; "
            "R1 full-grid numbers remain saved as diagnostics."
        ),
        "artifacts_preserved_at": (
            r"C:\Users\yhach\e1x6_research_store\raw_feature_redesign"
            r"\e1x6r3r1_20260803_031244_a7d98591"
        ),
        "not_overwritten": True,
    },
    "e1x6r3r2_20260803_040009_4d87ffa4": {
        "disposition": "VALID_BLOCK_EVIDENCE_R2",
        "verdict": "E1_X6_RAW_REDESIGN_P1_R2_BLOCKED",
        "p1_sha256": "8523de37b3a42a87490000311a99e6f7841a57eba6afb5e61e84712bfc2afb83",
        "registry_sha256": "b931258daad8a55b0d55985ccd66cf17efca83b9f6012b984cbed7c774fea66a",
        "artifact_sha256": {
            "report.json": "18c8b7497ddcb43395b51a2a5eaac788c5d8491b040591c74bafd228a23ec654",
            "report.md": "a4edc5c7ff55d98ffe1d94d8373814e93de771e24e959edd579d5994d2b460db",
            "audit.xlsx": "76112b9882524f9eb43f8d6f44742581ee4db90ff73ff6a721e762f5e6cd6ac4",
        },
        "reason": (
            "kept unchanged as VALID R2 block evidence: decision_quote_coverage "
            "mixed quote quality with spread<=50bps (failing 20260724_AM/"
            "20260729_AM), and 4 new Growth listings missing from the dated "
            "all_symbols.csv master. Phase A-R3 separates structural quote "
            "coverage from spread tradeability and freezes official JPX tick "
            "evidence for those symbols."
        ),
        "artifacts_preserved_at": (
            r"C:\Users\yhach\e1x6_research_store\raw_feature_redesign"
            r"\e1x6r3r2_20260803_040009_4d87ffa4"
        ),
        "not_overwritten": True,
    },
}
