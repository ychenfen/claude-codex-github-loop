# Workflow Operations Manual (English)

> 中文版：[WORKFLOW.md](WORKFLOW.md)

This repository implements an asynchronous **Cloud Brain (Claude on the Web / Claude Code Cloud) + Local Hands (Codex CLI / Claude Code CLI)** engineering loop over a **GitHub Actions Self-Hosted Runner**.
Large local datasets (e.g., 500GB+ medical imaging corpora, video datasets, local LLM benchmarks) remain strictly on your local workstation (macOS / Linux / Windows) and never enter Git.

---

## 1. Roles & Security Boundaries

| Role | What It Does | What It Never Does |
|---|---|---|
| **Cloud Brain (Claude Web)** | Reads experiment history, proposes hypotheses, freezes `PROTOCOL.md`, writes `.ai/task.md`, dispatches runs, audits code & metrics | Never touches raw data directly; never claims experimental conclusions without a pre-frozen `PROTOCOL.md` |
| **Local Agent (`ai-loop.yml`)** | Executes `.ai/task.md`, writes code & unit tests, reads local read-only datasets, produces `< 5 MB` metrics and `summary.md` | Cannot modify `.github/`, `.ai/`, `.claude/`, `scripts/ci/`, or `**/PROTOCOL.md` (reverted by `guard.sh`); never pushes directly |
| **Remote Command (`remote-cmd.yml`)** | Runs deterministic commands (`pytest`, `survey.py`, `smoke_test.py`) or collects local files into a 14-day private artifact | Defaults to `commit=false` (discards workspace edits); collected files only enter ephemeral Artifacts |

---

## 2. Two Execution Channels

| Workflow | When to Use | How to Trigger | Output Locations |
|---|---|---|---|
| `ai-loop.yml` | Multi-step coding, evaluation, or autonomous debugging tasks | 1) Push edits to `.ai/task.md` on `main`/`ai-loop`<br>2) `workflow_dispatch` with optional inline `task` and `agent_backend` (`codex`/`claude`/`python`) | Committed to `runs/<n>/`: `summary.md`, `metrics.json`, `meta.json`, `guard.md`, `task.md`, `agent_tail.log`; full folder in Artifact `run-<n>` |
| `remote-cmd.yml` | Deterministic commands: running tests, workspace inventory (`survey.py`), GPU checks, or fetching files | `workflow_dispatch` with `command`, optional `workdir`, `shell` (`bash`/`powershell`/`cmd`), `collect`, `commit` (`false` default) | Committed to `runs/cmd-<n>/`: `command.txt`, `output.txt` (last 2000 lines), `collect_manifest.txt`, `meta.json`, `guard.md`; full output & files in Artifact `cmd-<n>` |

Both workflows share `concurrency: local-workstation-loop` so jobs run strictly sequentially on your workstation.

---

## 3. Five-Layer Hard Guardrail (`scripts/ci/guard.sh` & `guard.py`)

Before the local agent executes, the workflow copies `scripts/ci/` to `$RUNNER_TEMP/ci` (**Pre-Freeze Pattern**). Even if the agent modifies `scripts/ci/` inside the workspace during its run, the uncorrupted copy in `$RUNNER_TEMP/ci` runs before `git push`:

1. **Commit Soft-Reset**: If the agent ran `git commit` on its own, `git reset --soft "$BASE_SHA"` collapses all commits back into staged changes so every single file is inspected.
2. **Protected Path Reversion**: Any modification or addition to `.github/`, `.ai/`, `.claude/`, `scripts/ci/`, or `**/PROTOCOL.md` is automatically reverted.
3. **Oversized File Gate (> 5 MB)**: Any staged file exceeding `MAX_MB` (default 5 MB) is unstaged (`git rm --cached`) while remaining intact on the local disk.
4. **Credential & Secret Gate**: Files matching `.env*`, `*.pem`, `*.key`, `id_rsa*`, `*secret*`, `*token*`, `*credential*`, or `*password*` are automatically unstaged, and token patterns (`sk-*`, `ghp_*`, `github_pat_*`, `AKIA*`) are redacted from logs.
5. **Audit Trail (`guard.md`)**: Writes a transparent report to `runs/<n>/guard.md` and surfaces warnings in the GitHub Actions summary.

---

## 4. Offline Runner Queueing Strategy (Avoiding the 24-Hour Timeout)

GitHub Actions automatically cancels queued jobs if a self-hosted runner does not pick them up within **24 hours**.
- When your local workstation is asleep or offline, do **not** push changes to `.ai/task.md`.
- Instead, save your next experiment specification to **`.ai/next_task.md`** (which does not trigger `ai-loop.yml`).
- Once your workstation is awake, rename `.ai/next_task.md` to `.ai/task.md` and push.
