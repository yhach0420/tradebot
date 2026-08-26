from pathlib import Path

p = Path("scripts/_v26g8_validate_duallane_repair.py")
s = p.read_text(encoding="utf-8")

if "occ_match_ms" not in s:
    s = s.replace(
        "    occ_closed = False",
        "    occ_closed = False\n"
        "    occ_match_ms: list[float] = []\n"
        "    occ_n_match = 0\n"
        '    skip_full = str(os.environ.get("V26G8_OCC_ONLY") or "").strip().lower() in ("1", "true", "yes")',
        1,
    )

if "occ_match_ms.append" not in s:
    s = s.replace(
        "                    match_ms.append(dt)",
        "                    match_ms.append(dt)\n"
        "                    if not occ_closed:\n"
        "                        occ_match_ms.append(dt)\n"
        "                        occ_n_match += 1",
        1,
    )

if "OCC_ONLY_STOP" not in s:
    s = s.replace(
        "                    occ_closed = True",
        "                    occ_closed = True\n"
        "                    if skip_full:\n"
        '                        print("OCC_ONLY_STOP", flush=True)\n'
        "                        stop_all = True\n"
        "                        break",
        1,
    )

if "stop_all = False" not in s:
    s = s.replace(
        "    for part in iter_parts():",
        "    stop_all = False\n    for part in iter_parts():\n        if stop_all:\n            break",
        1,
    )

s = s.replace(
    "    occ_match = match_ms  # matching ticks inside occupancy are the first n_match collected in window",
    "    occ_match = occ_match_ms",
    1,
)

p.write_text(s, encoding="utf-8")
print("ok", "occ_match_ms" in s, "OCC_ONLY_STOP" in s, "skip_full" in s)
