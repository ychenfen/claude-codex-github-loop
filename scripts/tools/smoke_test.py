#!/usr/bin/env python3
"""Verify self-hosted runner identity, OS/arch, GPU accelerators (CUDA / Apple MPS),
filesystem read/write probe, and local dataset paths declared in config/local_paths*.json.

Usage:
    python3 scripts/tools/smoke_test.py [--config config/local_paths.example.json] [--out runs/smoke.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import platform
import shutil
import socket
import tempfile
from pathlib import Path


def detect_accelerators() -> dict:
    info = {
        "torch_installed": False,
        "torch_version": None,
        "cuda_available": False,
        "cuda_device_count": 0,
        "cuda_devices": [],
        "mps_available": False,
    }
    try:
        import torch  # type: ignore

        info["torch_installed"] = True
        info["torch_version"] = getattr(torch, "__version__", "unknown")
        if hasattr(torch, "cuda") and torch.cuda.is_available():
            info["cuda_available"] = True
            count = torch.cuda.device_count()
            info["cuda_device_count"] = count
            info["cuda_devices"] = [torch.cuda.get_device_name(i) for i in range(count)]
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["mps_available"] = True
    except ImportError:
        pass
    return info


def run_file_probe() -> str:
    with tempfile.TemporaryDirectory(prefix="runner-smoke-") as tmp:
        probe = Path(tmp) / "file-probe.txt"
        probe.write_text("created", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "created"
        probe.write_text("updated", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "updated"
    return "passed"


def check_configured_paths(config_path: Path) -> list[dict]:
    if not config_path.is_file():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    roots = data.get("roots", {})
    results = []
    for key, spec in roots.items():
        raw_path = spec.get("path", "")
        mode = spec.get("mode", "ro")
        p = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        results.append(
            {
                "key": key,
                "path": raw_path,
                "mode": mode,
                "exists": p.exists(),
                "is_dir": p.is_dir(),
                "description": spec.get("description", ""),
            }
        )
    return results


def collect_smoke_report(config_path: Path | None = None) -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    if config_path is None:
        local_cfg = repo_root / "config" / "local_paths.json"
        example_cfg = repo_root / "config" / "local_paths.example.json"
        config_path = local_cfg if local_cfg.is_file() else example_cfg

    disk = shutil.disk_usage(Path.cwd())
    return {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
        "system": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "run_id": os.getenv("GITHUB_RUN_ID") or os.getenv("RUN_ID"),
        "runner_name": os.getenv("RUNNER_NAME"),
        "commit_sha": os.getenv("GITHUB_SHA") or os.getenv("COMMIT_SHA"),
        "file_probe": run_file_probe(),
        "disk_free_gb": round(disk.free / (1024**3), 2),
        "accelerators": detect_accelerators(),
        "configured_paths": check_configured_paths(config_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to local_paths.json (defaults to config/local_paths.json or config/local_paths.example.json)",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON output file path")
    args = parser.parse_args(argv)

    report = collect_smoke_report(args.config)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("# Self-Hosted Runner Smoke Test")
    print()
    print(f"- **Host**: `{report['hostname']}` (`{report['system']}` / `{report['architecture']}`)")
    print(f"- **User**: `{report['user']}`")
    print(f"- **Python**: `{report['python_version']}`")
    print(f"- **File Read/Write Probe**: `{report['file_probe']}`")
    print(f"- **Free Disk Space**: `{report['disk_free_gb']} GB`")
    acc = report["accelerators"]
    print(
        f"- **Accelerators**: PyTorch={acc['torch_installed']} "
        f"(CUDA={acc['cuda_available']}, MPS={acc['mps_available']})"
    )
    print()
    print("```json")
    print(json.dumps(report, indent=2))
    print("```")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
