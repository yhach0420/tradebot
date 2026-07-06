"""Quick parity: HEAD vs Phase629 on first N push rows (det mode)."""
import importlib
import shutil
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
WORKTREE = Path(r"c:\Users\yhach\Documents\tradebotfile-phase629-baseline")
DAY = "2026-06-25"
MAX_ROWS = 8000
POLL = 5.0
YAML = KABU / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


def _run(tag: str, src_root: Path) -> dict:
    for p in (src_root / "kabu_native" / "src", src_root):
        s = str(p)
        if s in sys.path:
            sys.path.remove(s)
    for p in (src_root / "kabu_native" / "src", src_root):
        sys.path.insert(0, str(p))
    for mod in list(sys.modules):
        if mod.startswith("small_paper") or mod.startswith("research"):
            del sys.modules[mod]
    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run

    cfg = replace(
        load_pilot_config(YAML),
        discord_enabled=False,
        entry_latency_trace_enabled=False,
    )
    out = KABU / "results" / "small_paper" / "_phase629" / tag / f"quick_{DAY.replace('-', '')}"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    run_push_replay_dry_run(
        cfg,
        push_dir=KABU / "data" / "push_jsonl" / DAY,
        output_dir=out,
        repo_root=REPO,
        poll_interval_sec=POLL,
        max_push_rows=MAX_ROWS,
        streaming_push_replay=True,
        enable_discord=False,
        write_board_shadow_reports=False,
    )
    import json

    ev = []
    p = out / "small_paper_events.jsonl"
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ev.append(json.loads(line))
    s = json.loads((out / "small_paper_summary.json").read_text(encoding="utf-8"))
    return {
        "tag": tag,
        "pilot": sys.modules["small_paper.pilot_runner"].__file__,
        "events": len(ev),
        "accepted": s.get("accepted_count"),
        "rejected": s.get("rejected_count"),
        "gate_eval": s.get("gate_evaluations"),
        "accept_syms": tuple(
            e.get("symbol") for e in ev if e.get("event_type") == "accepted"
        ),
        "reject_reasons": Counter(
            str(e.get("gate_reject_reason") or "")
            for e in ev
            if e.get("event_type") == "rejected"
        ),
    }


def main() -> int:
    a = _run("quick_head", WORKTREE)
    b = _run("quick_after", REPO)
    print("HEAD pilot:", a["pilot"])
    print("AFTER pilot:", b["pilot"])
    for k in ("events", "accepted", "rejected", "gate_eval"):
        print(f"{k}: HEAD={a[k]} AFTER={b[k]} MATCH={a[k]==b[k]}")
    print("accept_syms match:", a["accept_syms"] == b["accept_syms"])
    if a["accept_syms"] != b["accept_syms"]:
        print("HEAD accepts:", a["accept_syms"][:20])
        print("AFTER accepts:", b["accept_syms"][:20])
    diff = a["reject_reasons"] - b["reject_reasons"]
    if diff:
        print("reject reason diff (HEAD-AFTER):", dict(diff))
    keys = ("events", "accepted", "rejected", "gate_eval", "accept_syms", "reject_reasons")
    return 0 if all(a[k] == b[k] for k in keys) else 1


if __name__ == "__main__":
    raise SystemExit(main())
