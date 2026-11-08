"""Comprehensive unit tests for hard security guardrails (guard.py & guard.sh),
safe file collector (collect.sh), workspace inventory (survey.py), and runner smoke test (smoke_test.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ci"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "tools"))

import guard  # noqa: E402
import sandbox_runner  # noqa: E402
import smoke_test  # noqa: E402
import survey  # noqa: E402


def _init_temp_git_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a realistic temporary git repository with protected paths and a frozen PROTOCOL.md."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test-user"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    # Create protected directories and files
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ai-loop.yml").write_text("name: ai-loop\n", encoding="utf-8")
    (repo / ".ai").mkdir(parents=True)
    (repo / ".ai" / "task.md").write_text("# Original Task\n", encoding="utf-8")
    (repo / "scripts" / "ci").mkdir(parents=True)
    (repo / "scripts" / "ci" / "guard.sh").write_text("#!/usr/bin/env bash\n# original\n", encoding="utf-8")
    (repo / "experiments" / "EXP001").mkdir(parents=True)
    (repo / "experiments" / "EXP001" / "PROTOCOL.md").write_text("# Frozen Protocol v1\n", encoding="utf-8")
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "baseline.py").write_text("print('hello')\n", encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base_sha


def _simulate_rogue_agent_changes(repo: Path) -> None:
    """Simulate an agent that:
    1) Modifies protected files (.github/, .ai/task.md, scripts/ci/guard.sh, experiments/EXP001/PROTOCOL.md)
    2) Adds a new untracked file under a protected directory (.claude/settings.json)
    3) Writes a valid derived metric & source code file (src/eval.py, runs/1/summary.md)
    4) Writes a >1 MB oversized dataset dump (data/raw_dump.bin)
    5) Writes suspected secret files (.env.local, api_secret_key.json, id_rsa_backup)
    6) Makes its own rogue `git commit` before the workflow guardrail runs!
    """
    # Protected modifications
    (repo / ".github" / "workflows" / "ai-loop.yml").write_text("name: hacked\n", encoding="utf-8")
    (repo / ".ai" / "task.md").write_text("# Tampered Task\n", encoding="utf-8")
    (repo / "scripts" / "ci" / "guard.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (repo / "experiments" / "EXP001" / "PROTOCOL.md").write_text("# Tampered Protocol\n", encoding="utf-8")

    # Untracked file in protected dir
    (repo / ".claude").mkdir(parents=True, exist_ok=True)
    (repo / ".claude" / "config.json").write_text('{"bypass": true}\n', encoding="utf-8")

    # Valid agent outputs
    (repo / "src" / "eval.py").write_text("def score():\n    return 0.95\n", encoding="utf-8")
    (repo / "runs" / "1").mkdir(parents=True, exist_ok=True)
    (repo / "runs" / "1" / "summary.md").write_text("# Run 1 Summary\nAccuracy: 95.0%\n", encoding="utf-8")

    # Rogue commit by the agent
    subprocess.run(["git", "add", "src/eval.py", ".ai/task.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "agent premature commit"], cwd=repo, check=True)

    # Oversized file (2 MB; we will test with max_mb=1)
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "raw_dump.bin").write_bytes(b"X" * (2 * 1024 * 1024))

    # Suspected credential / secret files
    (repo / ".env.local").write_text("OPENAI_API_KEY=sk-123456\n", encoding="utf-8")
    (repo / "api_secret_token.json").write_text('{"token": "secret"}\n', encoding="utf-8")
    (repo / "id_rsa_test").write_text("PRIVATE KEY\n", encoding="utf-8")


def _staged_files(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return {f.strip() for f in out if f.strip()}


def test_guard_py_enforces_all_security_invariants(tmp_path: Path) -> None:
    repo, base_sha = _init_temp_git_repo(tmp_path)
    _simulate_rogue_agent_changes(repo)

    res = guard.enforce_guard(base_sha, "runs/1", max_mb=1.0, repo_root=repo)

    # 1. HEAD was soft-reset back to base_sha
    head_now = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_now == base_sha

    # 2. Protected files are restored to exact BASE_SHA content
    assert (repo / ".github" / "workflows" / "ai-loop.yml").read_text(encoding="utf-8") == "name: ai-loop\n"
    assert (repo / ".ai" / "task.md").read_text(encoding="utf-8") == "# Original Task\n"
    assert (repo / "experiments" / "EXP001" / "PROTOCOL.md").read_text(encoding="utf-8") == "# Frozen Protocol v1\n"
    assert not (repo / ".claude" / "config.json").exists()

    # 3. Check staged files: ONLY valid outputs + guard.md are staged!
    staged = _staged_files(repo)
    assert staged == {"src/eval.py", "runs/1/summary.md", "runs/1/guard.md"}

    # 4. Oversized file still exists on local disk (not deleted!), but was unstaged
    assert (repo / "data" / "raw_dump.bin").is_file()
    assert len(res["blocked_size"]) == 1
    assert "data/raw_dump.bin" in res["blocked_size"][0]

    # 5. Secret files were blocked and unstaged
    assert len(res["blocked_secrets"]) == 3
    guard_text = (repo / "runs" / "1" / "guard.md").read_text(encoding="utf-8")
    assert "Protected Path Modifications Reverted" in guard_text
    assert "Oversized Files Blocked" in guard_text
    assert "Suspected Credential Files Blocked" in guard_text


def test_guard_sh_enforces_all_security_invariants(tmp_path: Path) -> None:
    repo, base_sha = _init_temp_git_repo(tmp_path)
    _simulate_rogue_agent_changes(repo)

    # Copy guard.sh to a frozen temp path (mirroring $RUNNER_TEMP/ci/guard.sh)
    frozen_guard = tmp_path / "frozen_guard.sh"
    shutil.copy(REPO_ROOT / "scripts" / "ci" / "guard.sh", frozen_guard)

    env = os.environ.copy()
    env["MAX_MB"] = "1"
    subprocess.run(
        ["bash", str(frozen_guard), base_sha, "runs/1"],
        cwd=repo,
        env=env,
        check=True,
    )

    head_now = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_now == base_sha
    assert (repo / "experiments" / "EXP001" / "PROTOCOL.md").read_text(encoding="utf-8") == "# Frozen Protocol v1\n"
    assert not (repo / ".claude" / "config.json").exists()

    staged = _staged_files(repo)
    assert staged == {"src/eval.py", "runs/1/summary.md", "runs/1/guard.md"}


def test_collect_sh_filters_secrets_and_caches(tmp_path: Path) -> None:
    src = tmp_path / "workspace"
    (src / "sub").mkdir(parents=True)
    (src / ".git").mkdir(parents=True)
    (src / "node_modules").mkdir(parents=True)

    (src / "README.md").write_text("# Hello\n", encoding="utf-8")
    (src / "sub" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (src / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (src / "my_secret_key.pem").write_text("KEY\n", encoding="utf-8")
    (src / ".git" / "config").write_text("git\n", encoding="utf-8")
    (src / "node_modules" / "pkg.js").write_text("js\n", encoding="utf-8")

    dest = tmp_path / "collected"
    manifest = tmp_path / "manifest.txt"
    collect_sh = REPO_ROOT / "scripts" / "ci" / "collect.sh"

    subprocess.run(
        ["bash", str(collect_sh), f"{src};{tmp_path}/nonexistent_file.txt", str(dest), str(manifest)],
        check=True,
    )

    manifest_text = manifest.read_text(encoding="utf-8")
    assert "OK\t" in manifest_text
    assert "README.md" in manifest_text
    assert "app.py" in manifest_text
    assert "SKIP\t-\t" in manifest_text
    assert "suspected secret/credential" in manifest_text
    assert "MISS\t-\t" in manifest_text


def test_survey_tool_outputs_inventory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proj = tmp_path / "sample_proj"
    (proj / "tests").mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (proj / "main.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "tests" / "test_main.py").write_text("def test_x(): pass\n", encoding="utf-8")

    rc = survey.main([str(tmp_path), "--depth", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Workspace Inventory" in out
    assert "sample_proj" in out
    assert "Python" in out


def test_smoke_test_runs_and_writes_json(tmp_path: Path) -> None:
    out_json = tmp_path / "smoke.json"
    rc = smoke_test.main(["--out", str(out_json)])
    assert rc == 0
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["file_probe"] == "passed"
    assert "accelerators" in data
    assert "configured_paths" in data


def test_sandbox_runner_executes_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISABLE_MACOS_SANDBOX", "1")
    script = tmp_path / "demo_task.py"
    script.write_text(
        "import os, pathlib\n"
        "out = pathlib.Path(os.environ['TASK_OUTPUT_DIR']) / 'done.txt'\n"
        "out.write_text('ok', encoding='utf-8')\n"
        "print('hello from task')\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    res = sandbox_runner.run_isolated_script(script, out_dir, timeout_seconds=10)
    assert res["exit_code"] == 0
    assert res["timed_out"] is False
    assert (out_dir / "done.txt").read_text(encoding="utf-8") == "ok"
    assert "hello from task" in (out_dir / "task.log").read_text(encoding="utf-8")

