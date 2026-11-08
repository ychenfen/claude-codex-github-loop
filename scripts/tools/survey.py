#!/usr/bin/env python3
"""Cross-platform workspace & directory inventory tool for remote Cloud Brain inspection.

Reads only filesystem metadata and project marker filenames (never file contents).
For each target path, outputs:
- Directory tree up to `--depth`: file count, total size, latest modification time, and stack tags
- Detected software projects: git branch/HEAD/dirty status, language/stack markers, test directories
- Top 20 most recently modified files

Usage:
    python3 scripts/tools/survey.py /path/to/workspace [more_paths...] [--depth 2]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
    ".ruff_cache",
}
MARKERS = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "requirements.txt": "Python",
    "environment.yml": "Python/conda",
    "package.json": "Node",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "CMakeLists.txt": "C/C++",
    "Makefile": "make",
    "pytest.ini": "pytest",
    "tox.ini": "tox",
    "Dockerfile": "Docker",
}
TEST_HINTS = ("tests", "test", "__tests__", "spec")


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def walk_stats(root: Path) -> tuple[int, int, float, list[tuple[float, str, int]]]:
    """Recursively count files, total bytes, latest mtime, and top-20 recent files."""
    files, size, latest, recent = 0, 0, 0.0, []
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            try:
                st = os.stat(os.path.join(dirpath, name), follow_symlinks=False)
            except OSError:
                continue
            files += 1
            size += st.st_size
            latest = max(latest, st.st_mtime)
            recent.append((st.st_mtime, os.path.join(dirpath, name), st.st_size))
    recent.sort(reverse=True)
    return files, size, latest, recent[:20]


def git_info(path: Path) -> dict | None:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    if not (path / ".git").exists():
        return None
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    last = run("log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d")
    dirty = len([l for l in run("status", "--porcelain").splitlines() if l.strip()])
    remote = run("remote", "get-url", "origin")
    return dict(branch=branch, last=last, dirty=dirty, remote=remote)


def project_info(path: Path) -> dict | None:
    try:
        names = {p.name for p in path.iterdir()}
    except OSError:
        return None
    stack = sorted(
        {v for k, v in MARKERS.items() if k in names}
        | ({"Python"} if any(n.endswith(".py") for n in names) else set())
    )
    tests = sorted(n for n in names if n.lower() in TEST_HINTS)
    git = git_info(path)
    if not (stack or tests or git):
        return None
    return dict(stack=stack, tests=tests, git=git)


def tree(root: Path, depth: int, prefix: str = "") -> list[str]:
    lines: list[str] = []
    try:
        entries = sorted((p for p in root.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return [f"{prefix}(unreadable: {type(e).__name__})"]
    dirs = [p for p in entries if p.is_dir() and p.name not in SKIP_DIRS and not p.is_symlink()]
    loose = [p for p in entries if p.is_file()]
    for d in dirs:
        n, s, t, _ = walk_stats(d)
        proj = project_info(d)
        tag = ""
        if proj:
            bits = proj["stack"] + (["git"] if proj["git"] else []) + (["tests"] if proj["tests"] else [])
            tag = f"  **[{', '.join(bits)}]**"
        lines.append(f"{prefix}- `{d.name}/`  {n} files, {fmt_size(s)}, modified {fmt_time(t)}{tag}")
        if depth > 1:
            lines += tree(d, depth - 1, prefix + "  ")
    if loose:
        total = sum(p.stat().st_size for p in loose if p.exists())
        shown = ", ".join(f"`{p.name}`" for p in loose[:15]) + (" …" if len(loose) > 15 else "")
        lines.append(f"{prefix}- ({len(loose)} files in dir, {fmt_size(total)}) {shown}")
    return lines


def survey(root: Path, depth: int) -> list[str]:
    out = [f"## `{root}`", ""]
    if not root.exists():
        return out + ["Directory does not exist.", ""]
    n, s, t, recent = walk_stats(root)
    out += [f"Total: {n} files, {fmt_size(s)}, latest modification {fmt_time(t)}.", "", "### Directory Tree", ""]
    out += tree(root, depth) + ["", "### Detected Projects", ""]
    found = []
    for dirpath, dirnames, _ in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        p = Path(dirpath)
        if len(p.relative_to(root).parts) > depth:
            dirnames[:] = []
            continue
        info = project_info(p)
        if info and (info["git"] or info["tests"] or set(info["stack"]) - {"Python"}):
            found.append((p, info))
            if info["git"]:
                dirnames[:] = []  # Do not recurse inside a detected git repository
    if not found:
        out.append("(No git repositories, test suites, or build manifests detected)")
    for p, info in found:
        g = info["git"]
        gtxt = (
            f"git: branch `{g['branch']}`, last commit {g['last'] or '-'}, dirty {g['dirty']}"
            + (f", remote {g['remote']}" if g["remote"] else "")
            if g
            else "not a git repo"
        )
        out.append(
            f"- `{p}`: {', '.join(info['stack']) or 'unknown stack'}; "
            f"{'tests: ' + ', '.join(info['tests']) if info['tests'] else 'no test dir'}; {gtxt}"
        )
    out += ["", "### Top 20 Recently Modified Files", ""]
    out += [f"- {fmt_time(m)}  {fmt_size(sz):>9}  `{f}`" for m, f, sz in recent]
    return out + [""]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="One or more directory paths to inspect")
    ap.add_argument("--depth", type=int, default=2, help="Directory tree traversal depth (default: 2)")
    args = ap.parse_args(argv)
    lines = [f"# Workspace Inventory ({dt.datetime.now().strftime('%Y-%m-%d %H:%M')}, depth {args.depth})", ""]
    for p in args.paths:
        lines += survey(Path(p), args.depth)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
