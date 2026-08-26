from pathlib import Path

src = Path("scripts/write_v26g7_candidate7_snapshot.py").read_text(encoding="utf-8")
src = src.replace("V26-G7 Candidate-7", "V26-G8 Candidate-8")
src = src.replace(
    "Does not mutate Formal V25 or Candidate-6. Does not Formal-freeze V26.",
    "Does not mutate Formal V25, Candidate-6, or Candidate-7. Does not Formal-freeze V26.",
)
src = src.replace(
    "OPVAL selector is rewritten separately.",
    "OPVAL current-trading-day identity is not rewritten.",
)
src = src.replace(
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G7_7",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G8_8",
)
src = src.replace("active_v1r_candidate_v26g7_7.json", "active_v1r_candidate_v26g8_8.json")
src = src.replace("candidate-7 snapshot already exists", "candidate-8 snapshot already exists")
src = src.replace(
    "Identity-only UNCERTIFIED candidate-7 selector; not the active Formal selector. ",
    "Identity-only UNCERTIFIED candidate-8 selector; not the active Formal selector. ",
)
src = src.replace(
    "Not Candidate-6. OPVAL binds via OPVAL_CURRENT_TRADING_DAY identity.",
    "Not Candidate-7. OPVAL current-trading-day identity is not rewritten.",
)
src = src.replace(
    "V26G7_UNCERTIFIED_CURRENT_RUNTIME_OPVAL_CANDIDATE7",
    "V26G8_DUALLANE_THROUGHPUT_REPAIR_RUNTIME_ONLY",
)
src = src.replace("V1R_V26G7_INVENTORY_COVERAGE_FAIL", "V1R_V26G8_INVENTORY_COVERAGE_FAIL")

old = '''C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
PRIOR = (
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5",
    C6_ID,
)
'''
new = '''C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
C7_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G7_7"
C7_SHA = "bc0b47e01f6bce592fa374bc555d3e9f26dbd353848356a890bdb73452602960"
C7_SELECTOR = OUT / "active_v1r_candidate_v26g7_7.json"
PRIOR = (
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5",
    C6_ID,
    C7_ID,
)
'''
if old not in src:
    raise SystemExit("BLOCK_NOT_FOUND C6_SHA/PRIOR")
src = src.replace(old, new, 1)

a = '''    if prior_shas[C6_ID] != C6_SHA:
        print("REFUSE: Candidate-6 manifest mutated")
        return 2
    c6 = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
'''
b = '''    if prior_shas[C6_ID] != C6_SHA:
        print("REFUSE: Candidate-6 manifest mutated")
        return 2
    if prior_shas[C7_ID] != C7_SHA:
        print("REFUSE: Candidate-7 manifest mutated")
        return 2
    if C7_SELECTOR.is_file():
        c7s = json.loads(C7_SELECTOR.read_text(encoding="utf-8"))
        if c7s.get("activation_id") != C7_ID or c7s.get("activation_sha") != C7_SHA:
            print("REFUSE: Candidate-7 identity selector mutated")
            return 2
    c6 = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    c7 = json.loads((OUT / f"{C7_ID}.json").read_text(encoding="utf-8"))
'''
if a not in src:
    raise SystemExit("BLOCK_NOT_FOUND c6 load")
src = src.replace(a, b, 1)

src = src.replace(
    '            "parent_candidate6_id": C6_ID,\n            "parent_candidate6_sha": C6_SHA,',
    '            "parent_candidate6_id": C6_ID,\n            "parent_candidate6_sha": C6_SHA,\n            "parent_candidate7_id": C7_ID,\n            "parent_candidate7_sha": C7_SHA,',
    1,
)
src = src.replace(
    '            "parent_runtime_candidate_id": C6_ID,\n            "parent_runtime_candidate_sha": C6_SHA,',
    '            "parent_runtime_candidate_id": C7_ID,\n            "parent_runtime_candidate_sha": C7_SHA,',
    1,
)

c = '''    c6_after = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during candidate write")
        return 2
    if c6_after.get("sha256") != C6_SHA:
        print("REFUSE: Candidate-6 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
'''
d = '''    c6_after = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    c7_after = json.loads((OUT / f"{C7_ID}.json").read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during candidate write")
        return 2
    if c6_after.get("sha256") != C6_SHA:
        print("REFUSE: Candidate-6 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    if c7_after.get("sha256") != C7_SHA:
        print("REFUSE: Candidate-7 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
'''
if c not in src:
    raise SystemExit("BLOCK_NOT_FOUND after-check")
src = src.replace(c, d, 1)

src = src.replace(
    '    print("CANDIDATE6_UNCHANGED=true")\n    print("PRIOR_CANDIDATES_UNCHANGED=true")',
    '    print("CANDIDATE6_UNCHANGED=true")\n    print("CANDIDATE7_UNCHANGED=true")\n    print("RUNTIME_CHANGED=true")\n    print("STRATEGY_AFFECTING_DIFF_G=0")\n    print("PRIOR_CANDIDATES_UNCHANGED=true")',
    1,
)
src = src.replace(
    'str(v25.get("strategy_sha") or "") == str(c6.get("strategy_sha") or "")',
    'str(v25.get("strategy_sha") or "") == str(c7.get("strategy_sha") or "") == str(c6.get("strategy_sha") or "")',
    1,
)

# DualLane-only inventory drift vs C7
guard = '''    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2
'''
guard2 = '''    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2
    c7_inv = c7.get("runtime_file_sha256") or {}
    drifted = [rel for rel in RUNTIME_DEPENDENCY_RELS if "v1r_live_dual_lane.py" not in rel and str(c7_inv.get(rel) or "") != str(inv.get(rel) or "")]
    if drifted:
        print("REFUSE: strategy-affecting inventory drift G=", len(drifted), drifted[:20])
        return 2
'''
if guard not in src:
    raise SystemExit("BLOCK_NOT_FOUND inventory collect")
src = src.replace(guard, guard2, 1)

Path("scripts/write_v26g8_candidate8_snapshot.py").write_text(src, encoding="utf-8")
print("WROTE", len(src.splitlines()))
