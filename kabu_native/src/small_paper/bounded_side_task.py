"""Bounded side tasks that must not block Paper session finalize / exit.

ThreadPoolExecutor + future.result(timeout=N) is NOT an effective hard timeout:
on TimeoutError, ``with ThreadPoolExecutor()`` still shutdown(wait=True).

Primary API: killable subprocess workers (terminate → kill, Windows process tree).
Daemon threads are retained only for proofs / non-I/O diagnostics — production
session-end Discord/archive/backup MUST use subprocess workers.

Rules:
- workers write only under ``<session>/_side_task_tmp/<task_id>/``
- never rewrite sealed artifacts after seal marker
- timeout → pending; safe retry on next start
- source delete forbidden; overwrite of sealed paths forbidden
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Global telemetry (process-local)
_lock = threading.RLock()
_active_workers = 0
_timeout_workers = 0
_max_active_workers = 8
_seen_task_ids: set[str] = set()
_sealed_sessions: set[str] = set()  # resolved session_dir strings


@dataclass
class SideTaskResult:
    ok: bool
    timed_out: bool = False
    abandoned: bool = False
    killed: bool = False
    error: Optional[str] = None
    value: Any = None
    elapsed_sec: float = 0.0
    code: str = "OK"
    pending: bool = False
    task_id: str = ""
    pid: Optional[int] = None
    residual_process: bool = False


def telemetry() -> dict[str, Any]:
    with _lock:
        return {
            "active_worker_count": _active_workers,
            "timeout_worker_count": _timeout_workers,
            "max_active_workers": _max_active_workers,
            "seen_task_ids": len(_seen_task_ids),
            "sealed_sessions": len(_sealed_sessions),
        }


def mark_session_sealed(session_dir: str | Path) -> None:
    with _lock:
        _sealed_sessions.add(str(Path(session_dir).resolve()))


def is_session_sealed(session_dir: str | Path) -> bool:
    with _lock:
        return str(Path(session_dir).resolve()) in _sealed_sessions


def side_task_tmp_dir(session_dir: str | Path, task_id: str) -> Path:
    d = Path(session_dir) / "_side_task_tmp" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def assert_writable_path(session_dir: str | Path, path: str | Path) -> None:
    """Raise if path is outside session temp or session is already sealed."""
    sess = Path(session_dir).resolve()
    p = Path(path).resolve()
    if is_session_sealed(sess):
        raise RuntimeError(f"LATE_WRITE_FORBIDDEN: session sealed: {sess}")
    tmp_root = (sess / "_side_task_tmp").resolve()
    try:
        p.relative_to(tmp_root)
    except ValueError as exc:
        raise RuntimeError(f"WRITE_PATH_FORBIDDEN: {p} not under {tmp_root}") from exc


def _inc_active() -> bool:
    global _active_workers
    with _lock:
        if _active_workers >= _max_active_workers:
            return False
        _active_workers += 1
        return True


def _dec_active() -> None:
    global _active_workers
    with _lock:
        _active_workers = max(0, _active_workers - 1)


def _inc_timeout() -> None:
    global _timeout_workers
    with _lock:
        _timeout_workers += 1


def _kill_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except Exception:
            pass
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    time.sleep(0.2)
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            out = (r.stdout or "").strip()
            return bool(out) and str(pid) in out and "No tasks" not in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_daemon_bounded(
    fn: Callable[[], Any],
    *,
    timeout_sec: float,
    name: str = "side_task",
) -> SideTaskResult:
    """Daemon-thread abandon (may leave residual threads). Prefer run_subprocess_bounded."""
    box: dict[str, Any] = {}
    done = threading.Event()
    task_id = f"{name}-{uuid.uuid4().hex[:8]}"

    def _target() -> None:
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as exc:
            box["ok"] = False
            box["error"] = f"{type(exc).__name__}:{exc}"
            box["tb"] = traceback.format_exc()[-1500:]
        finally:
            done.set()

    if not _inc_active():
        return SideTaskResult(
            ok=False,
            error="active_worker_limit",
            code="SIDE_TASK_LIMIT",
            pending=True,
            task_id=task_id,
        )
    t0 = time.perf_counter()
    th = threading.Thread(target=_target, name=f"bounded-{name}", daemon=True)
    try:
        th.start()
        finished = done.wait(timeout=float(timeout_sec))
        elapsed = time.perf_counter() - t0
        if finished:
            if box.get("ok"):
                return SideTaskResult(ok=True, value=box.get("value"), elapsed_sec=elapsed, task_id=task_id)
            return SideTaskResult(
                ok=False,
                error=str(box.get("error") or "unknown"),
                elapsed_sec=elapsed,
                code="SIDE_TASK_ERROR",
                pending=True,
                task_id=task_id,
            )
        _inc_timeout()
        log.warning("%s timed out after %.3fs; abandoning daemon worker", name, timeout_sec)
        return SideTaskResult(
            ok=False,
            timed_out=True,
            abandoned=True,
            error=f"{name}_timeout",
            elapsed_sec=elapsed,
            code="SIDE_TASK_TIMEOUT",
            pending=True,
            task_id=task_id,
        )
    finally:
        if done.is_set():
            _dec_active()
        else:
            # abandoned: count as timeout worker; do not block; dec when thread dies (best-effort)
            def _watch() -> None:
                th.join()
                _dec_active()

            threading.Thread(target=_watch, name=f"watch-{name}", daemon=True).start()


def run_subprocess_bounded(
    *,
    task: str,
    session_dir: str | Path,
    timeout_sec: float,
    name: str = "side_task",
    extra: Optional[dict[str, Any]] = None,
    kill_grace_sec: float = 2.0,
) -> SideTaskResult:
    """Killable subprocess worker. Writes result JSON under session `_side_task_tmp` only."""
    global _seen_task_ids
    session_dir = Path(session_dir)
    task_id = f"{name}-{uuid.uuid4().hex[:10]}"
    with _lock:
        if task_id in _seen_task_ids:
            return SideTaskResult(ok=False, error="duplicate_task_id", code="DUP_TASK", task_id=task_id, pending=True)
        _seen_task_ids.add(task_id)
        if is_session_sealed(session_dir):
            return SideTaskResult(
                ok=False,
                error="session_already_sealed",
                code="LATE_WRITE_FORBIDDEN",
                pending=True,
                task_id=task_id,
            )

    if not _inc_active():
        return SideTaskResult(
            ok=False,
            error="active_worker_limit",
            code="SIDE_TASK_LIMIT",
            pending=True,
            task_id=task_id,
        )

    tmp = side_task_tmp_dir(session_dir, task_id)
    result_path = tmp / "result.json"
    req_path = tmp / "request.json"
    req = {
        "task": task,
        "task_id": task_id,
        "session_dir": str(session_dir),
        "tmp_dir": str(tmp),
        "extra": extra or {},
        "name": name,
    }
    req_path.write_text(json.dumps(req, ensure_ascii=False, default=str), encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "small_paper.bounded_side_task",
        "--request",
        str(req_path),
        "--result",
        str(result_path),
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Ensure imports resolve
    src = str(Path(__file__).resolve().parents[1])
    native = str(Path(__file__).resolve().parents[2])
    repo = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = os.pathsep.join([src, native, repo, env.get("PYTHONPATH", "")])

    t0 = time.perf_counter()
    proc: Optional[subprocess.Popen[str]] = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=native,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = proc.communicate(timeout=float(timeout_sec))
        except subprocess.TimeoutExpired:
            _inc_timeout()
            pid = int(proc.pid or 0)
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=float(kill_grace_sec))
            except Exception:
                pass
            if proc.poll() is None:
                _kill_process_tree(pid)
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
            residual = _pid_alive(pid)
            elapsed = time.perf_counter() - t0
            # pending marker for next-start retry (temp only)
            pending_path = tmp / "pending.json"
            pending_path.write_text(
                json.dumps(
                    {
                        "task": task,
                        "task_id": task_id,
                        "pending": True,
                        "timed_out": True,
                        "killed": True,
                        "session_dir": str(session_dir),
                        "name": name,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return SideTaskResult(
                ok=False,
                timed_out=True,
                abandoned=True,
                killed=True,
                error=f"{name}_timeout_killed",
                elapsed_sec=elapsed,
                code="SIDE_TASK_TIMEOUT",
                pending=True,
                task_id=task_id,
                pid=pid,
                residual_process=residual,
            )

        elapsed = time.perf_counter() - t0
        payload: dict[str, Any] = {}
        if result_path.is_file():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception as exc:
                payload = {"ok": False, "error": f"result_parse:{exc}"}
        else:
            payload = {
                "ok": False,
                "error": f"no_result rc={proc.returncode} stderr={(stderr or '')[-500:]}",
            }
        if payload.get("ok"):
            return SideTaskResult(
                ok=True,
                value=payload.get("value"),
                elapsed_sec=elapsed,
                task_id=task_id,
                pid=proc.pid,
            )
        return SideTaskResult(
            ok=False,
            error=str(payload.get("error") or "task_failed"),
            elapsed_sec=elapsed,
            code=str(payload.get("code") or "SIDE_TASK_ERROR"),
            pending=bool(payload.get("pending", True)),
            task_id=task_id,
            pid=proc.pid,
            value=payload.get("value"),
        )
    except Exception as exc:
        return SideTaskResult(
            ok=False,
            error=f"{type(exc).__name__}:{exc}",
            code="SIDE_TASK_ERROR",
            pending=True,
            task_id=task_id,
            elapsed_sec=time.perf_counter() - t0,
        )
    finally:
        _dec_active()


def _execute_request(req: dict[str, Any]) -> dict[str, Any]:
    task = str(req.get("task") or "")
    session_dir = Path(str(req.get("session_dir") or ""))
    tmp_dir = Path(str(req.get("tmp_dir") or ""))
    extra = req.get("extra") if isinstance(req.get("extra"), dict) else {}
    task_id = str(req.get("task_id") or "")

    # Refuse if parent already sealed (race with finalize)
    if is_session_sealed(session_dir):
        return {"ok": False, "pending": True, "code": "LATE_WRITE_FORBIDDEN", "error": "session_sealed"}

    # All worker outputs must stay under tmp_dir
    assert_writable_path(session_dir, tmp_dir / ".touch")
    (tmp_dir / ".touch").write_text("ok", encoding="utf-8")

    if task == "sleep":
        time.sleep(float(extra.get("seconds") or 1.0))
        out = tmp_dir / "slept.txt"
        assert_writable_path(session_dir, out)
        out.write_text("slept", encoding="utf-8")
        return {"ok": True, "value": {"slept": True, "task_id": task_id}}

    if task == "late_write_probe":
        # Attempt to rewrite a sealed-path file (must fail when sealed)
        target = Path(str(extra.get("target") or (session_dir / "session_summary.json")))
        delay = float(extra.get("delay_sec") or 0.5)
        time.sleep(delay)
        if is_session_sealed(session_dir):
            return {"ok": False, "pending": True, "code": "LATE_WRITE_FORBIDDEN", "error": "blocked_after_seal"}
        # Even if not sealed yet, only allow tmp writes in this worker API
        try:
            assert_writable_path(session_dir, target)
        except Exception as exc:
            return {"ok": False, "pending": True, "code": "WRITE_PATH_FORBIDDEN", "error": str(exc)}
        return {"ok": False, "error": "should_not_reach"}

    if task == "discord_session_end":
        # Load finalized summary from disk and send session-end Discord.
        # Capture-only / no network when DISCORD_CAPTURE_ONLY=1 (handled inside notifier).
        marker = tmp_dir / "discord_done.json"
        assert_writable_path(session_dir, marker)
        capture_only = str(os.environ.get("DISCORD_CAPTURE_ONLY") or "").strip() in (
            "1",
            "true",
            "True",
            "YES",
            "yes",
        )
        try:
            from small_paper.discord_notifier import (
                SmallPaperDiscordConfig,
                SmallPaperDiscordNotifier,
            )

            summary_path = Path(
                str(extra.get("summary_path") or (session_dir / "small_paper_summary.json"))
            )
            if not summary_path.is_file():
                alt = session_dir / "summary.json"
                summary_path = alt if alt.is_file() else summary_path
            if not summary_path.is_file():
                raise FileNotFoundError(f"summary_missing:{summary_path}")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                raise ValueError("summary_not_object")

            cfg = SmallPaperDiscordConfig(
                enabled=True,
                send_daily_summary=True,
                # _post requires observer_only=True (paper notify path).
                observer_only=True,
            )
            discord = SmallPaperDiscordNotifier(
                cfg,
                profile=str(summary.get("profile") or "session_end"),
                entry_profile=str(summary.get("entry_profile") or summary.get("profile") or "session_end"),
                policy_label=str(summary.get("policy_label") or "paper"),
                min_continuation_quality=float(summary.get("min_continuation_quality") or 0.0),
            )
            # Prefer in-memory empty events — production embed uses canonical_summary only.
            events: list[dict[str, Any]] = []
            events_path = Path(str(extra.get("events_path") or ""))
            if events_path.is_file() and events_path.stat().st_size < 8_000_000:
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass

            if capture_only:
                # Demo / investigation: do not hit webhook; persist capture artifact.
                capture = tmp_dir / "discord_capture.json"
                assert_writable_path(session_dir, capture)
                capture.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "capture_only": True,
                            "task_id": task_id,
                            "dedupe_key": extra.get("dedupe_key"),
                            "stop_reason": summary.get("stop_reason"),
                            "trading_date": summary.get("trading_date"),
                            "accepted_count": summary.get("accepted_count"),
                            "canonical_total_pnl_yen_100": summary.get("canonical_total_pnl_yen_100"),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    encoding="utf-8",
                )
                marker.write_text(
                    json.dumps(
                        {"ok": True, "capture_only": True, "task_id": task_id, "capture": str(capture)},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "value": {"discord": "capture_only", "marker": str(marker), "capture": str(capture)},
                }

            native_root = Path(str(extra.get("native_root") or Path(__file__).resolve().parents[2]))
            from small_paper.session_end_discord_delivery import (
                DEFAULT_FLUSH_SEC,
                deliver_session_end_discord,
            )

            flush_sec = float(extra.get("flush_sec") or DEFAULT_FLUSH_SEC)
            delivery = deliver_session_end_discord(
                discord=discord,
                events=events,
                summary=summary,
                native_root=native_root,
                output_dir=session_dir,
                flush_sec=flush_sec,
                session_id=str(summary.get("session_id") or session_dir.name or ""),
            )
            discord_status = str(delivery.get("discord") or "failed")
            # HTTP-confirmed only — never mark enqueue as sent.
            marker_ok = bool(delivery.get("ok")) and discord_status == "sent"
            marker.write_text(
                json.dumps(
                    {
                        "ok": marker_ok,
                        "capture_only": False,
                        "task_id": task_id,
                        "dedupe_key": extra.get("dedupe_key"),
                        "summary_path": str(summary_path),
                        "discord": discord_status,
                        "delivery": {
                            "per_key": delivery.get("per_key"),
                            "flush": delivery.get("flush"),
                            "session_id": delivery.get("session_id"),
                        },
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
            return {
                "ok": marker_ok,
                "pending": not marker_ok,
                "value": {
                    "discord": discord_status,
                    "marker": str(marker),
                    "summary_path": str(summary_path),
                    "dedupe_key": extra.get("dedupe_key"),
                    "per_key": delivery.get("per_key"),
                    "flush": delivery.get("flush"),
                    "queue_remaining": delivery.get("queue_remaining"),
                    "worker_alive": delivery.get("worker_alive"),
                    "submit": 0,
                    "cancel": 0,
                    "live_order": 0,
                },
                "error": None if marker_ok else f"discord_{discord_status}",
            }
        except Exception as exc:
            marker.write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
            return {"ok": False, "pending": True, "error": str(exc)}

    if task == "archive_session_copy":
        from small_paper.data_retention_guard import archive_session_copy

        # Run archive but record status only in tmp; archive_session_copy itself writes archive tree
        # (allowed as non-seal path). Never rewrite session seal/summary from here after seal.
        if is_session_sealed(session_dir):
            return {"ok": False, "pending": True, "code": "LATE_WRITE_FORBIDDEN", "error": "session_sealed"}
        root = Path(str(extra.get("native_root") or Path(__file__).resolve().parents[2]))
        bak = archive_session_copy(session_dir, root=root)
        status = tmp_dir / "archive_status.json"
        assert_writable_path(session_dir, status)
        status.write_text(json.dumps(bak, ensure_ascii=False, default=str), encoding="utf-8")
        return {"ok": bool(bak.get("ok")), "value": bak, "pending": not bool(bak.get("ok"))}

    if task == "external_backup":
        from small_paper.external_backup import after_session_archive

        if is_session_sealed(session_dir):
            return {"ok": False, "pending": True, "code": "LATE_WRITE_FORBIDDEN", "error": "session_sealed"}
        native = Path(str(extra.get("native_root") or Path(__file__).resolve().parents[2]))
        ext = after_session_archive(session_dir, native=native)
        status = tmp_dir / "external_status.json"
        assert_writable_path(session_dir, status)
        status.write_text(json.dumps(ext, ensure_ascii=False, default=str), encoding="utf-8")
        return {"ok": bool(ext.get("ok") or ext.get("skipped")), "value": ext, "pending": bool(ext.get("pending"))}

    if task == "hang":
        time.sleep(float(extra.get("seconds") or 3600))
        return {"ok": True, "value": "never"}

    return {"ok": False, "error": f"unknown_task:{task}", "pending": True}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="bounded_side_task")
    p.add_argument("--request", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args(argv)
    req = json.loads(Path(args.request).read_text(encoding="utf-8"))
    try:
        out = _execute_request(req)
    except Exception as exc:
        out = {"ok": False, "pending": True, "error": f"{type(exc).__name__}:{exc}", "tb": traceback.format_exc()[-1500:]}
    Path(args.result).write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0 if out.get("ok") else 1


def prove_threadpool_context_hang(*, sleep_sec: float = 2.0, timeout_sec: float = 0.2) -> dict[str, Any]:
    def _sleep() -> str:
        time.sleep(sleep_sec)
        return "done"

    t0 = time.perf_counter()
    timeout_at = None
    exited_at = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_sleep)
            try:
                fut.result(timeout=timeout_sec)
            except concurrent.futures.TimeoutError:
                timeout_at = time.perf_counter() - t0
        exited_at = time.perf_counter() - t0
    except Exception as exc:
        return {"error": str(exc), "elapsed": time.perf_counter() - t0}
    total = time.perf_counter() - t0
    return {
        "pattern": "ThreadPoolExecutor_context_manager",
        "timeout_sec": timeout_sec,
        "sleep_sec": sleep_sec,
        "timeout_at_sec": timeout_at,
        "with_exit_at_sec": exited_at,
        "total_sec": round(total, 4),
        "verdict": "HARD_TIMEOUT_EFFECTIVE" if total <= 0.5 else "HARD_TIMEOUT_NOT_EFFECTIVE",
    }


def prove_subprocess_bounded(*, sleep_sec: float = 2.0, timeout_sec: float = 0.2) -> dict[str, Any]:
    import tempfile

    sess = Path(tempfile.mkdtemp(prefix="side_task_prove_"))
    t0 = time.perf_counter()
    res = run_subprocess_bounded(
        task="hang",
        session_dir=sess,
        timeout_sec=timeout_sec,
        name="prove_hang",
        extra={"seconds": sleep_sec},
        kill_grace_sec=1.0,
    )
    total = time.perf_counter() - t0
    time.sleep(0.3)
    residual = bool(res.pid and _pid_alive(int(res.pid)))
    return {
        "pattern": "run_subprocess_bounded",
        "timeout_sec": timeout_sec,
        "sleep_sec": sleep_sec,
        "result": asdict(res),
        "total_sec": round(total, 4),
        "residual_process": residual,
        "verdict": "HARD_TIMEOUT_EFFECTIVE" if total <= 0.5 and not residual else "HARD_TIMEOUT_NOT_EFFECTIVE",
    }


if __name__ == "__main__":
    raise SystemExit(main())
