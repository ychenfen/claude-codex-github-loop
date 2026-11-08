#!/usr/bin/env python3
"""Pre-push hard security guardrail for the GitHub Self-Hosted Runner Loop.

Usage:
    python3 scripts/ci/guard.py <BASE_SHA> <RUN_DIR> [--max-mb 5]

What this guardrail enforces before any agent output is committed & pushed:
1. Soft-resets any commits made by the agent back to <BASE_SHA> so all mutations
   pass through one unified inspection gate.
2. Reverts any modifications (and deletes untracked additions) to protected paths:
   - .github/, .ai/, .claude/, scripts/ci/
   - Any **/PROTOCOL.md file tracked in <BASE_SHA> or created in the workspace
3. Stages remaining files (`git add -A`).
4. Unstages (keeps on local disk, blocks from git) any file larger than MAX_MB (default 5 MB).
5. Unstages (keeps on local disk, blocks from git) any suspected credential or secret file
   (.env*, *.pem, *.key, *secret*, *token*, *credential*, id_rsa*, etc.).
6. Writes a transparent audit log to <RUN_DIR>/guard.md and stages it.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path

BASE_PROTECTED_PREFIXES = (".github", ".ai", ".claude", "scripts/ci")
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.kdbx",
    "*credential*",
    "*secret*",
    "*password*",
    "*token*",
    "cookies*",
    "*.cookie",
)


def run_git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def file_exists_in_commit(commit: str, rel_path: str, cwd: Path) -> bool:
    res = run_git(["cat-file", "-e", f"{commit}:{rel_path}"], cwd=cwd, check=False)
    return res.returncode == 0


def is_secret_filename(rel_path: str) -> bool:
    name = Path(rel_path).name.lower()
    # Allow source code / tests that happen to mention token/secret in test names if needed,
    # Wait: in a workspace, any newly staged file matching SECRET_PATTERNS (except guard/test scripts
    # which are protected or already tracked) should be checked. To be strict and predictable,
    # match the basename against SECRET_PATTERNS.
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS)


def is_protected_path(rel_path: str, base_protocols: set[str]) -> bool:
    norm = rel_path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    for prefix in BASE_PROTECTED_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    if norm in base_protocols or norm.endswith("/PROTOCOL.md") or norm == "PROTOCOL.md":
        return True
    return False


def enforce_guard(base_sha: str, run_dir: str, max_mb: float = 5.0, repo_root: Path | None = None) -> dict:
    cwd = (repo_root or Path.cwd()).resolve()
    max_bytes = int(max_mb * 1024 * 1024)

    # 1. Soft-reset any agent commits back to BASE_SHA
    head_sha = run_git(["rev-parse", "HEAD"], cwd=cwd).stdout.strip()
    if head_sha != base_sha:
        run_git(["reset", "--soft", base_sha], cwd=cwd)

    # Discover all **/PROTOCOL.md tracked at BASE_SHA
    ls_tree = run_git(["ls-tree", "-r", "--name-only", base_sha], cwd=cwd).stdout.splitlines()
    base_protocols = {
        p.strip() for p in ls_tree if p.strip().endswith("/PROTOCOL.md") or p.strip() == "PROTOCOL.md"
    }

    # 2. Identify all modified or untracked files touching protected paths
    diff_names = run_git(["diff", "--name-only", base_sha], cwd=cwd).stdout.splitlines()
    untracked_names = run_git(["ls-files", "--others", "--exclude-standard"], cwd=cwd).stdout.splitlines()
    candidates = sorted({f.strip() for f in (diff_names + untracked_names) if f.strip()})

    reverted: list[str] = []
    for rel in candidates:
        if is_protected_path(rel, base_protocols):
            if file_exists_in_commit(base_sha, rel, cwd):
                run_git(["checkout", base_sha, "--", rel], cwd=cwd)
                reverted.append(f"`{rel}` (modification reverted)")
            else:
                run_git(["rm", "-q", "--cached", "--ignore-unmatch", "--", rel], cwd=cwd, check=False)
                target = cwd / rel
                if target.is_file() or target.is_symlink():
                    target.unlink()
                reverted.append(f"`{rel}` (untracked addition removed)")

    # 3. Stage all remaining changes
    run_git(["add", "-A"], cwd=cwd)

    # 4. Inspect staged files for size > MAX_BYTES and suspected secrets
    staged_raw = run_git(["diff", "--cached", "--name-only", "-z"], cwd=cwd).stdout
    staged_files = [f for f in staged_raw.split("\0") if f]

    blocked_size: list[str] = []
    blocked_secrets: list[str] = []

    run_dir_norm = run_dir.replace("\\", "/").strip("/")
    guard_rel = f"{run_dir_norm}/guard.md" if run_dir_norm else "guard.md"

    for rel in staged_files:
        norm = rel.replace("\\", "/")
        if norm == guard_rel:
            continue
        full_path = cwd / rel
        if not full_path.is_file():
            continue

        # Check secret filename pattern
        if is_secret_filename(rel):
            run_git(["rm", "-q", "--cached", "--", rel], cwd=cwd, check=False)
            blocked_secrets.append(f"`{rel}` (suspected credential/secret file, unstaged)")
            continue

        # Check file size limit
        size = full_path.stat().st_size
        if size > max_bytes:
            run_git(["rm", "-q", "--cached", "--", rel], cwd=cwd, check=False)
            mb_str = f"{size / (1024 * 1024):.2f} MB"
            blocked_size.append(f"`{rel}` ({mb_str} > {max_mb:g} MB limit, unstaged & kept on runner)")

    # 5. Write audit report to <RUN_DIR>/guard.md
    out_path = cwd / guard_rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Guardrail Audit Report (`guard.md`)",
        "",
        f"- **Base Commit**: `{base_sha}`",
        f"- **Max File Size**: `{max_mb:g} MB`",
        "",
    ]
    if not reverted and not blocked_size and not blocked_secrets:
        lines.append("All checks passed: no protected paths modified, no oversized files, and no suspected credential files.")
    if reverted:
        lines.append("## Protected Path Modifications Reverted")
        lines.extend(f"- {item}" for item in reverted)
        lines.append("")
    if blocked_size:
        lines.append(f"## Oversized Files Blocked (> {max_mb:g} MB)")
        lines.extend(f"- {item}" for item in blocked_size)
        lines.append("")
    if blocked_secrets:
        lines.append("## Suspected Credential Files Blocked")
        lines.extend(f"- {item}" for item in blocked_secrets)
        lines.append("")

    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    run_git(["add", "-f", "--", guard_rel], cwd=cwd)

    for item in reverted:
        print(f"::warning::Guard reverted protected path: {item}")
    for item in blocked_size:
        print(f"::warning::Guard blocked oversized file: {item}")
    for item in blocked_secrets:
        print(f"::warning::Guard blocked secret file: {item}")
    print(
        f"Guard summary: {len(reverted)} reverted, "
        f"{len(blocked_size)} oversized blocked, "
        f"{len(blocked_secrets)} secrets blocked"
    )
    return {
        "base_sha": base_sha,
        "reverted": reverted,
        "blocked_size": blocked_size,
        "blocked_secrets": blocked_secrets,
        "guard_md": str(out_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_sha", help="Base commit SHA before the agent or command ran")
    parser.add_argument("run_dir", help="Run output directory (writes <run_dir>/guard.md)")
    parser.add_argument(
        "--max-mb",
        type=float,
        default=float(os.environ.get("MAX_MB", "5")),
        help="Maximum allowed file size in MB (default: 5)",
    )
    args = parser.parse_args(argv)
    enforce_guard(args.base_sha, args.run_dir, max_mb=args.max_mb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
