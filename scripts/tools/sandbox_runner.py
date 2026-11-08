#!/usr/bin/env python3
"""Optional isolated task runner with process-group timeout and macOS `sandbox-exec` support.

On macOS (`Darwin` with `sandbox-exec`), restricts filesystem access under `/Users`
to only the task directory (read-only), output directory (read/write), temporary
directory (read/write), and explicitly granted paths from `config/local_paths.json`.
On Linux/Windows, enforces the clean environment allowlist, per-task output isolation,
and hard process-group timeout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TASK_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,60}$")
MAX_TIMEOUT = 3600
MAX_LOG_BYTES = 5 * 1024 * 1024
MACOS_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny file-read* file-write* (subpath "/Users"))
(allow file-read-metadata)
(allow file-read* (subpath (param "TASK_DIR")))
(allow file-read* file-write* (subpath (param "OUT_DIR")))
(allow file-read* file-write* (subpath (param "TMP_DIR")))
"""


def run_isolated_script(script_path: Path, out_dir: Path, timeout_seconds: int = 120) -> dict:
    script_path = script_path.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "task.log"

    with tempfile.TemporaryDirectory(prefix="sandbox-task-") as tmp_str:
        tmp = Path(tmp_str).resolve()
        (tmp / "home").mkdir(parents=True, exist_ok=True)

        env = {
            "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(tmp / "home"),
            "TMPDIR": str(tmp),
            "LANG": "en_US.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TASK_OUTPUT_DIR": str(out_dir),
        }

        use_macos_sandbox = (
            platform.system() == "Darwin"
            and shutil.which("sandbox-exec") is not None
            and os.environ.get("DISABLE_MACOS_SANDBOX") != "1"
        )
        if use_macos_sandbox:
            profile = tmp / "task.sb"
            profile.write_text(MACOS_SANDBOX_PROFILE, encoding="utf-8")
            cmd = [
                "sandbox-exec",
                "-f",
                str(profile),
                "-D",
                f"TASK_DIR={script_path.parent}",
                "-D",
                f"OUT_DIR={out_dir}",
                "-D",
                f"TMP_DIR={tmp}",
                sys.executable,
                "-s",
                str(script_path),
            ]
            sandbox_desc = "macos-sandbox-exec"
        else:
            cmd = [sys.executable, str(script_path)]
            sandbox_desc = "env-and-timeout-isolation"

        started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        t0 = time.monotonic()
        timed_out = False
        with open(log_path, "wb") as log_fp:
            proc = subprocess.Popen(
                cmd,
                cwd=str(script_path.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait()
                code = 124

        duration = round(time.monotonic() - t0, 3)
        finished_at = dt.datetime.now(dt.timezone.utc).isoformat()

    size = log_path.stat().st_size if log_path.is_file() else 0
    if size > MAX_LOG_BYTES:
        with open(log_path, "r+b") as log_fp:
            log_fp.truncate(MAX_LOG_BYTES)
            log_fp.seek(0, os.SEEK_END)
            log_fp.write(b"\n[runner] log truncated\n")

    result = {
        "script": str(script_path),
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "sandbox": sandbox_desc,
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "exit_code": code,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
        "log_bytes": size,
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path, help="Path to Python script to execute")
    parser.add_argument("--out-dir", type=Path, default=Path("runs/sandbox"), help="Output directory")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds (1..3600)")
    args = parser.parse_args(argv)

    if not 1 <= args.timeout <= MAX_TIMEOUT:
        raise SystemExit(f"timeout must be between 1 and {MAX_TIMEOUT}")
    if not args.script.is_file():
        raise SystemExit(f"script not found: {args.script}")

    res = run_isolated_script(args.script, args.out_dir, timeout_seconds=args.timeout)
    print(json.dumps(res, indent=2))
    return res["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
