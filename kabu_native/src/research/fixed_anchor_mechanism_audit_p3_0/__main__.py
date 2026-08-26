"""P3-0 runner. Reused-history mechanism diagnostic only. No new strategy. No Runtime change."""
from __future__ import annotations

import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.dynamic_anchor_p2_1.inventory import build_inventory
from research.fixed_anchor_mechanism_audit_p3_0 import (
    CLOCK_OFFSETS_SEC,
    FULL14,
    MAX_WORKERS,
    VERDICT_BLOCKED,
)
from research.fixed_anchor_mechanism_audit_p3_0.grid import common_support_fixed_grid
from research.fixed_anchor_mechanism_audit_p3_0.publish import (
    P1_REPORT,
    OUT,
    build_report,
    write_artifacts,
)
from research.fixed_anchor_mechanism_audit_p3_0.replay import replay_p3_day

CACHE = OUT / "_work_cache"


def _payload(row: dict, **extra) -> dict:
    p = {
        "date": row["date"],
        "capture_path": row["capture_path"],
        "universe": row["universe_symbols"],
        "universe_source": row["universe_source"],
    }
    p.update(extra)
    return p


def _cache_path(p: dict) -> Path:
    return CACHE / f"{p['date']}__{p.get('variant')}__{p.get('offset_sec', 0)}.json"


def _load_cache(p: dict) -> dict | None:
    fp = _cache_path(p)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == p["date"]:
        return body
    return None


def _save_cache(p: dict, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(p).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def run_jobs(jobs: list[dict]) -> tuple[list[dict], list[str]]:
    results: list[dict] = []
    failed: list[str] = []
    pending = []
    for j in jobs:
        cached = _load_cache(j)
        if cached is not None:
            print(
                f"CACHE {j['date']} {j.get('variant')} off={j.get('offset_sec')} "
                f"trades={cached.get('trade_n')} pnl={cached.get('pnl')}",
                flush=True,
            )
            results.append(cached)
        else:
            pending.append(j)
    if pending:
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(replay_p3_day, j): j for j in pending}
            for fut in as_completed(futs):
                job = futs[fut]
                day = job["date"]
                try:
                    out = fut.result()
                except Exception as exc:
                    print(f"FAIL {day} {job.get('variant')} {exc!r}", flush=True)
                    failed.append(f"{day}:{job.get('variant')}:{job.get('offset_sec')}")
                    continue
                if not out.get("ok"):
                    print(
                        f"FAIL {day} {job.get('variant')} {out.get('blocker')}",
                        flush=True,
                    )
                    failed.append(f"{day}:{job.get('variant')}:{out.get('blocker')}")
                    continue
                _save_cache(job, out)
                print(
                    f"OK {day} {job.get('variant')} off={job.get('offset_sec')} "
                    f"fires={out.get('anchor_fires')} trades={out.get('trade_n')} "
                    f"pnl={out.get('pnl')} leak={out.get('snapshot_future_leak')} "
                    f"sec={out.get('elapsed_sec')}",
                    flush=True,
                )
                results.append(out)
    results.sort(key=lambda d: (str(d.get("variant")), int(d.get("offset_sec") or 0), str(d.get("date"))))
    return results, failed


def _write_blocked(inv, support, baseline, cs, shifts, failed, p1, leak, reason: str) -> int:
    rep = build_report(
        inventory=inv,
        support=support,
        baseline_days=baseline,
        cs_days=cs,
        shift_days=shifts,
        failed=failed,
        p1=p1,
        leak=leak,
        blocked=True,
        blocked_reason=reason,
    )
    rep["verdict"] = VERDICT_BLOCKED
    paths = write_artifacts(rep)
    print("P3_0_BLOCKED", reason, paths["report_json"], flush=True)
    return 1


def main() -> int:
    support = common_support_fixed_grid()
    print(
        f"COMMON_SUPPORT original={support['original_anchor_count']} "
        f"kept={support['common_support_anchor_count']} "
        f"excluded={[e['anchor_time'] for e in support['excluded']]}",
        flush=True,
    )
    inv = build_inventory()
    p1 = json.loads(P1_REPORT.read_text(encoding="utf-8")) if P1_REPORT.is_file() else {}
    if not P1_REPORT.is_file():
        return _write_blocked(inv, support, [], [], {}, ["NO_P1_REPORT"], p1, False, "NO_P1_REPORT")

    by_inv = {r["date"]: r for r in inv}
    rows = []
    for day in FULL14:
        r = by_inv.get(day)
        if not r or not r.get("replay_eligible") or not r.get("universe_symbols") or not r.get("capture_path"):
            return _write_blocked(
                inv, support, [], [], {}, [f"MISSING_{day}"], p1, False, f"MISSING_OR_INELIGIBLE_{day}"
            )
        rows.append(r)

    kept_hm = [(int(h), int(m)) for h, m in support["kept_hm"]]
    print(f"P3-0 FULL14={len(rows)} workers={MAX_WORKERS} phase=baseline", flush=True)

    base_jobs = [
        _payload(
            r,
            variant="baseline",
            offset_sec=0,
            allowed_hm=None,
            fire_mode="production",
            with_diagnostics=True,
        )
        for r in rows
    ]
    baseline, fail1 = run_jobs(base_jobs)
    leak = any(bool(d.get("snapshot_future_leak")) for d in baseline)
    if fail1 or len(baseline) != 14:
        return _write_blocked(
            inv, support, baseline, [], {}, fail1, p1, leak, "BASELINE_REPLAY_FAILED"
        )

    probe = build_report(
        inventory=inv,
        support=support,
        baseline_days=baseline,
        cs_days=[],
        shift_days={},
        failed=[],
        p1=p1,
        leak=leak,
        blocked=False,
    )
    if probe.get("BASELINE_RECONCILE") != "PASS":
        return _write_blocked(
            inv, support, baseline, [], {}, ["BASELINE_RECONCILE_FAIL"], p1, leak, "BASELINE_RECONCILE_FAIL"
        )
    print("BASELINE_RECONCILE PASS", flush=True)

    cs_jobs = [
        _payload(
            r,
            variant="common_support",
            offset_sec=0,
            allowed_hm=kept_hm,
            fire_mode="production",
            with_diagnostics=False,
        )
        for r in rows
    ]
    shift_jobs = []
    for off in CLOCK_OFFSETS_SEC:
        for r in rows:
            shift_jobs.append(
                _payload(
                    r,
                    variant="shift",
                    offset_sec=int(off),
                    allowed_hm=kept_hm,
                    fire_mode="shifted_grid",
                    with_diagnostics=False,
                )
            )
    print(f"phase=clock jobs={len(cs_jobs) + len(shift_jobs)}", flush=True)
    cs_days, fail_cs = run_jobs(cs_jobs)
    sh_days, fail_sh = run_jobs(shift_jobs)
    failed = fail_cs + fail_sh
    shift_map: dict[int, list] = {int(o): [] for o in CLOCK_OFFSETS_SEC}
    for d in sh_days:
        shift_map.setdefault(int(d.get("offset_sec") or 0), []).append(d)

    if failed or len(cs_days) != 14 or any(len(shift_map[o]) != 14 for o in CLOCK_OFFSETS_SEC):
        return _write_blocked(
            inv,
            support,
            baseline,
            cs_days,
            shift_map,
            failed,
            p1,
            leak,
            "CLOCK_SHIFT_REPLAY_FAILED",
        )

    leak = leak or any(bool(d.get("snapshot_future_leak")) for d in baseline)
    rep = build_report(
        inventory=inv,
        support=support,
        baseline_days=baseline,
        cs_days=cs_days,
        shift_days=shift_map,
        failed=failed,
        p1=p1,
        leak=leak,
        blocked=False,
    )
    paths = write_artifacts(rep)
    if CACHE.is_dir():
        shutil.rmtree(CACHE, ignore_errors=True)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"CLOCK={rep['CLOCK_RESULT']} SEL={rep['SELECTION_RESULT']} "
        f"MECH={rep['FIXED_ANCHOR_MECHANISM']}",
        flush=True,
    )
    return 0 if not str(rep["verdict"]).endswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
