"""Registration profile tests. No live register call and no Alpha code edits."""
from __future__ import annotations

from pathlib import Path

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_state_alpha_shadow.identity import schema_sha, source_sha
from research.causal_driver_pb1.sector_state_alpha_shadow.isolation import OUT as SHADOW_PKG
from research.causal_driver_pb1.sector_state_alpha_shadow_registration import (
    ACTIVATION_FLAG,
    EXPECTED_DRIVER_SHA256,
    EXPECTED_IMPLEMENTATION_SHA256,
    EXPECTED_SCHEMA_SHA256,
    EXPECTED_STATE_SHA256,
    M3_SHA256,
    M3_TARGET_SHA256,
    REGISTER_LIMIT,
)
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.dry_run import LIVE_REGISTER_CALLS, dry_run
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.guard import (
    activation_requested,
    ack_register,
    owner_guard,
    precheck,
    restore_register,
    snapshot_register,
)
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.profiles import (
    build_candidates,
    freeze_profile,
    measure,
    plan_sha,
    reject_rotation,
    select_profile,
)
from research.causal_driver_pb1.sector_state_alpha_shadow import M3_TARGETS


def alpha_hashes() -> dict[str, str]:
    root = Path(SHADOW_PKG).resolve().parent.parent.parent.parent / "src" / "research" / "causal_driver_pb1" / "sector_state_alpha_shadow"
    if not (root / "driver.py").is_file():
        root = Path(__file__).resolve().parents[1] / "sector_state_alpha_shadow"
    names = ["config.py", "driver.py", "machine.py", "register_audit.py", "safety.py"]
    return {
        "implementation_sha256": source_sha([str(root / name) for name in names]),
        "driver_implementation_sha256": sha256_bytes((root / "driver.py").read_bytes()),
        "state_machine_sha256": sha256_bytes((root / "machine.py").read_bytes()),
        "schema_sha256": schema_sha(),
    }


def run_checks() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    plan = dry_run()
    ident = plan["identities"]
    candidates = plan["candidates"]

    def record(name: str, ok: bool, detail: str = "") -> None:
        rows.append({"test": name, "pass": bool(ok), "detail": detail})

    record("R1", len(ident["m3"]) == 30 and ident["m3_sha256"] == M3_SHA256)
    record("R2", ident["targets_subset"] and set(ident["targets"]) == set(M3_TARGETS) and len(ident["targets"]) == 5)
    record("R3", candidates["PROFILE_A"]["union_n"] == 30 and candidates["PROFILE_A"]["valid"] and candidates["PROFILE_A"]["union_n"] <= REGISTER_LIMIT)

    synthetic_c = {"PROFILE_C": {"valid": False, "union_n": 77, "truncated": False}, "PROFILE_B": {"valid": True, "union_n": 32, "truncated": False, "profile_id": "PROFILE_B"}, "PROFILE_A": {"valid": True, "union_n": 30, "truncated": False, "profile_id": "PROFILE_A"}}
    picked_b = select_profile(synthetic_c)["profile_id"] == "PROFILE_B"
    synthetic_a = {"PROFILE_C": {"valid": False, "union_n": 77, "truncated": False}, "PROFILE_B": {"valid": False, "union_n": 51, "truncated": False}, "PROFILE_A": {"valid": True, "union_n": 30, "truncated": False, "profile_id": "PROFILE_A"}}
    picked_a = select_profile(synthetic_a)["profile_id"] == "PROFILE_A"
    actual = plan["selected_profile"]
    rule = "PROFILE_C" if candidates["PROFILE_C"]["valid"] else "PROFILE_B" if candidates["PROFILE_B"]["valid"] else "PROFILE_A"
    record("R4", picked_b and picked_a and actual == rule)

    without_dynamic = build_candidates(m3=ident["m3"], core=[], futures=[])
    record("R5", without_dynamic["PROFILE_A"]["symbols"] == sorted(ident["m3"]) and plan["driver_read_sha256"] == M3_SHA256 and plan["dynamic38_block_included"] is False)

    blocked = owner_guard({"standard_paper_active": True, "dynamic38_continuity_active": False, "formal_certification_active": False})
    record("R6", blocked["status"] == "REGISTER_PROFILE_OWNER_CONFLICT" and blocked["mutated"] is False and blocked["register_changed"] is False)

    incomplete = ack_register(required_m3=ident["m3"], actual=ident["m3"][:-1])
    record("R7", incomplete["missing_M3_n"] == 1 and incomplete["alpha_enabled"] is False and incomplete["status"] == "M3_REGISTER_INCOMPLETE")

    snap = snapshot_register(symbols=ident["occupied"], owner=ident["previous_owner"])
    restored = restore_register(snap, list(snap["pre_shadow_registration_symbols"]))
    record("R8", restored["ok"] and restored["restored_sha256"] == snap["pre_shadow_registration_sha256"])

    too_big = freeze_profile("PROFILE_X", measure(m3=ident["m3"], core=ident["core"] + [f"X{i}" for i in range(30)], futures=ident["futures"]), m3=ident["m3"])
    record("R9", too_big["union_n"] > REGISTER_LIMIT and too_big["valid"] is False and too_big["truncated"] is False and len(too_big["symbols"]) == too_big["union_n"])

    rotated = False
    try:
        reject_rotation({"rotation": True, "m3_missing_n": 15, "symbols": ident["m3"][:15], "batches": [["a"], ["b"]]})
    except RuntimeError:
        rotated = True
    record("R10", rotated)

    hashes = alpha_hashes()
    hash_ok = (
        hashes["implementation_sha256"] == EXPECTED_IMPLEMENTATION_SHA256
        and hashes["driver_implementation_sha256"] == EXPECTED_DRIVER_SHA256
        and hashes["state_machine_sha256"] == EXPECTED_STATE_SHA256
        and hashes["schema_sha256"] == EXPECTED_SCHEMA_SHA256
    )
    record("ALPHA_HASH", hash_ok, "" if hash_ok else str(hashes))

    root = Path(__file__).resolve().parent
    banned_hits = []
    for path in root.glob("*.py"):
        if path.name == "checks.py":
            continue
        text = path.read_text(encoding="utf-8")
        for token in ("register_symbols_cleared(", "sendorder", "submit_order"):
            if token in text:
                banned_hits.append(f"{path.name}:{token}")
    flag_absent = not activation_requested(["run_paper", "--live"])
    flag_present = activation_requested(["run_tool", ACTIVATION_FLAG])
    stopped = precheck([True, True, False])
    record(
        "DRY_RUN",
        plan["pass"]
        and plan["would_mutate_live_register"] is False
        and LIVE_REGISTER_CALLS == 0
        and not banned_hits
        and flag_absent
        and flag_present
        and stopped["alpha_enabled"] is False
        and plan["M3_missing_n"] == 0
        and ident["m3_sha256"] == M3_SHA256,
    )
    _ = M3_TARGET_SHA256
    return {"pass": all(bool(r["pass"]) for r in rows), "rows": rows, "plan": plan, "hashes": hashes, "banned_hits": banned_hits}
