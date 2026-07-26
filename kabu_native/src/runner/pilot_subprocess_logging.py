"""Phase641b: persist pilot subprocess stdout/stderr for daily runner diagnostics."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

PILOT_STDOUT_LOG = "pilot_stdout.log"
PILOT_STDERR_LOG = "pilot_stderr.log"
TAIL_LINE_COUNT = 20
TRACEBACK_MARKER = "Traceback (most recent call last):"

# Normal session-end reasons written by pilot_runner (not pilot crashes).
PILOT_SOFT_OK_STOP_REASONS = frozenset(
    {
        "completed",
        "session_end",
        "morning_session_close",
        "afternoon_session_close",
        "recovery_session_close",
    }
)

# Subprocess failures that occur after small_paper_summary.json is finalized.
_POST_SESSION_PRINT_PATH = "run_small_paper_pilot.py"
_POST_SESSION_PRINT_CALL = "print(json.dumps"


def tail_lines(text: Optional[str], *, n: int = TAIL_LINE_COUNT) -> list[str]:
    if not text or not str(text).strip():
        return []
    lines = str(text).splitlines()
    return lines[-n:] if len(lines) > n else lines


def parse_traceback_fields(stderr: Optional[str]) -> dict[str, str]:
    """Extract first exception / traceback / error line from stderr text."""
    out = {"first_exception": "", "first_traceback": "", "first_error_line": ""}
    if not stderr or not str(stderr).strip():
        return out
    text = str(stderr)
    idx = text.find(TRACEBACK_MARKER)
    if idx >= 0:
        tb = text[idx:]
        out["first_traceback"] = tb[:8000]
        lines = tb.splitlines()
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("File "):
                continue
            if re.search(r"\w+(Error|Exception):", stripped):
                out["first_exception"] = stripped
                break
        for line in lines:
            stripped = line.strip()
            if re.search(r"\w+(Error|Exception):", stripped):
                out["first_error_line"] = stripped
                break
    else:
        nonempty = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if nonempty:
            out["first_error_line"] = nonempty[-1]
    return out


def format_pilot_exit_display(
    *,
    exit_code: Optional[int],
    pilot_verdict: Optional[str],
) -> str:
    if exit_code in (None, 0):
        return "0"
    if pilot_verdict == "completed_with_warnings":
        return "1 (warning)"
    return "1 (failed)"


def persist_pilot_subprocess_logs(
    session_dir: Path,
    *,
    stdout: Optional[str],
    stderr: Optional[str],
) -> dict[str, Any]:
    """Write full stdout/stderr to session dir; return tail lines + traceback parse."""
    session_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = session_dir / PILOT_STDOUT_LOG
    stderr_path = session_dir / PILOT_STDERR_LOG
    stdout_text = stdout if stdout is not None else ""
    stderr_text = stderr if stderr is not None else ""
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    tb = parse_traceback_fields(stderr_text)
    return {
        "pilot_stdout_path": str(stdout_path),
        "pilot_stderr_path": str(stderr_path),
        "pilot_stdout_log": PILOT_STDOUT_LOG,
        "pilot_stderr_log": PILOT_STDERR_LOG,
        "stdout_last_20_lines": tail_lines(stdout_text),
        "stderr_last_20_lines": tail_lines(stderr_text),
        "stdout_byte_count": len(stdout_text.encode("utf-8")),
        "stderr_byte_count": len(stderr_text.encode("utf-8")),
        **tb,
    }


def patch_session_summary_subprocess_meta(
    session_dir: Path,
    *,
    exit_code: Optional[int],
    pilot_verdict: Optional[str],
    stdout_path: str = "",
    stderr_path: str = "",
) -> bool:
    """Append subprocess logging fields to existing small_paper_summary.json (logging only)."""
    summary_fp = session_dir / "small_paper_summary.json"
    if not summary_fp.is_file():
        return False
    try:
        data = json.loads(summary_fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    data["pilot_exit_code"] = exit_code
    data["pilot_subprocess_verdict"] = pilot_verdict
    data["pilot_exit_display"] = format_pilot_exit_display(
        exit_code=exit_code,
        pilot_verdict=pilot_verdict,
    )
    if stdout_path:
        data["pilot_stdout_path"] = stdout_path
    if stderr_path:
        data["pilot_stderr_path"] = stderr_path
    summary_fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def pilot_subprocess_daily_summary_fields(
    prefix: str, live: Optional[Mapping[str, Any]], *, repo_root: Optional[Path] = None
) -> dict[str, Any]:
    if not live:
        return {}
    stdout_path = live.get("pilot_stdout_path")
    stderr_path = live.get("pilot_stderr_path")
    if repo_root is not None:
        rel = _rel_if_under_repo(repo_root, stdout_path)
        if rel:
            stdout_path = rel
        rel = _rel_if_under_repo(repo_root, stderr_path)
        if rel:
            stderr_path = rel
    return {
        f"{prefix}_pilot_exit_code": live.get("exit_code"),
        f"{prefix}_pilot_stdout_path": stdout_path,
        f"{prefix}_pilot_stderr_path": stderr_path,
        f"{prefix}_stdout_last_20_lines": live.get("stdout_last_20_lines") or [],
        f"{prefix}_stderr_last_20_lines": live.get("stderr_last_20_lines") or [],
        f"{prefix}_pilot_verdict": live.get("pilot_verdict"),
        f"{prefix}_first_exception": live.get("first_exception") or "",
        f"{prefix}_first_traceback": live.get("first_traceback") or "",
        f"{prefix}_first_error_line": live.get("first_error_line") or "",
    }


def is_post_session_subprocess_failure(live: Mapping[str, Any]) -> bool:
    """True when stderr indicates failure in pilot main() summary print (post-session)."""
    exc = str(live.get("first_exception") or live.get("first_error_line") or "")
    if not exc:
        return False
    if not any(
        token in exc
        for token in ("UnicodeEncodeError", "BrokenPipeError", "OSError")
    ):
        return False
    tb = str(live.get("first_traceback") or "")
    stderr = "\n".join(live.get("stderr_last_20_lines") or [])
    blob = f"{tb}\n{stderr}"
    return _POST_SESSION_PRINT_PATH in blob and _POST_SESSION_PRINT_CALL in blob


def session_stop_reason_soft_ok(stop_reason: str) -> bool:
    return str(stop_reason or "") in PILOT_SOFT_OK_STOP_REASONS


def build_warning_log_notes(live: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    exit_code = live.get("exit_code")
    if exit_code not in (None, 0):
        notes.append(f"pilot_exit_code={exit_code}")
    if is_post_session_subprocess_failure(live):
        notes.append("post_session_summary_print_failed (session artifacts OK)")
    stderr_lines: Sequence[str] = live.get("stderr_last_20_lines") or []
    stdout_lines: Sequence[str] = live.get("stdout_last_20_lines") or []
    if stderr_lines:
        notes.append("stderr_summary: " + " | ".join(stderr_lines[-5:])[:400])
    if stdout_lines:
        notes.append("stdout_summary: " + " | ".join(stdout_lines[-5:])[:400])
    return notes


def _rel_if_under_repo(repo_root: Path, path: Any) -> str:
    if not path:
        return ""
    try:
        p = Path(str(path))
        return str(p.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)
