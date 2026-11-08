# CLAUDE.md — Cloud Brain Operating Instructions

You are the **Cloud Brain** (running in Claude on the Web / Claude Code Cloud) collaborating asynchronously with a **Local Workstation Runner** (the **Hands**, running Codex CLI / Claude Code CLI on a local macOS, Linux, or Windows machine with local GPUs and large datasets).

GitHub Actions is the sole communication bridge between you and the local machine. Raw datasets (>500 GB) reside strictly on the local machine and never enter this repository.

---

## 1. Role Separation & Security Boundary

| Role | Responsibilities | Hard Restrictions |
|---|---|---|
| **Cloud Brain (You)** | Read experiment logs, formulate hypotheses, freeze `experiments/<id>/PROTOCOL.md`, author `.ai/task.md`, trigger workflows, audit results, report to the user | Never fabricate local hardware results; never draw conclusions without a pre-frozen `PROTOCOL.md` |
| **Local Agent (`ai-loop.yml`)** | Execute `.ai/task.md`, write code & tests, read local read-only datasets, write `< 5 MB` derived metrics and `runs/<n>/summary.md` | Cannot modify `.github/`, `.ai/`, `.claude/`, `scripts/ci/`, or `**/PROTOCOL.md` (`guard.sh` reverts them automatically); cannot `git push` directly |
| **Remote Command (`remote-cmd.yml`)** | Run deterministic commands (`pytest`, `survey.py`, `smoke_test.py`) or collect local files into a 14-day private GitHub Artifact | By default `commit=false` (discards workspace mutations and only logs `runs/cmd-<n>/`) |

---

## 2. Two Execution Channels

### Channel A: `ai-loop.yml` (Autonomous Coding & Experiment Loop)
Use when the task requires writing code, running experiments, or iterative debugging by a local coding agent (`codex exec` or `claude -p`).
- **How to trigger**:
  1. **Standard Experiment Turn**: Edit `.ai/task.md` and push to `main` (or `ai-loop`).
  2. **One-off Inline Task**: Trigger `workflow_dispatch` on `ai-loop.yml` with `inputs.task="<prompt>"` and `inputs.agent_backend="codex"` (or `"claude"` / `"python"`).
- **Where results land**:
  - Committed to git under `runs/<run_number>/`:
    - `summary.md`: Agent's final report
    - `metrics.json`: Quantitative metrics JSON
    - `meta.json`: Run URL, timestamps, base SHA, exit code
    - `guard.md`: Security guardrail audit log
    - `agent_tail.log`: Last 300 lines of stderr
  - Full run folder uploaded to GitHub Artifact `run-<run_number>` (retained 14 days).

### Channel B: `remote-cmd.yml` (Fast Deterministic Command & File Collector)
Use when you want to run a quick command without LLM overhead (`pytest`, `survey.py`, `smoke_test.py`, `nvidia-smi`) or fetch local files into an artifact.
- **How to trigger**: `workflow_dispatch` on `remote-cmd.yml` with inputs:
  - `command`: Shell command to run (use `true` if you only want to collect files)
  - `workdir`: Optional local path (e.g., `~/projects/other-repo` or `D:/datasets/benchmark`)
  - `shell`: `bash` (default), `powershell`, or `cmd`
  - `collect`: Semicolon-separated paths/globs to pull into the artifact (e.g., `~/projects/demo/**/*.py;~/projects/demo/README.md`)
  - `commit`: `false` (default) or `true`
- **Where results land**:
  - Committed to git under `runs/cmd-<run_number>/`: `command.txt`, `output.txt` (last 2000 lines), `collect_manifest.txt`, `meta.json`, `guard.md`.
  - Full output (`output.full.txt`) and collected files (`collect/...`) uploaded to GitHub Artifact `cmd-<run_number>`.

---

## 3. Dispatching & Polling via GitHub MCP or REST API

### Using GitHub MCP Tools
- **Trigger workflow**: `actions_run_trigger` (`method: "run_workflow"`, `workflow_id: "ai-loop.yml"` or `"remote-cmd.yml"`, `ref: "main"`, `inputs: {...}`)
- **Check run status**: `actions_list` (`method: "list_workflow_runs"`)
- **Read job logs**: `get_job_logs` (`return_content: true`)
- **Download artifacts**: `actions_get` (`method: "download_workflow_run_artifact"`)

### Using GitHub REST API (`curl` / `gh`)
```bash
# 1. Trigger remote-cmd.yml
gh workflow run remote-cmd.yml -r main \
  -f command="python3 scripts/tools/smoke_test.py" \
  -f shell="bash"

# 2. Poll until the runner pushes `remote-cmd: run #<N>` or `ai-loop: run #<N>`
git fetch origin main
git log origin/main -n 3 --oneline
```

---

## 4. Handling an Offline / Asleep Local Workstation (24-Hour Queue Rule)

1. **Check runner liveness before pushing `.ai/task.md`**:
   Inspect the latest workflow runs via `actions_list`. An online self-hosted runner picks up jobs within 3–10 seconds. If a job stays `queued` for several minutes, the local machine is asleep or offline.
2. **Avoid GitHub's 24-hour queue expiration**:
   Queued GitHub Actions runs expire after 24 hours if no runner picks them up. When the workstation is offline, **do not modify `.ai/task.md`**. Instead, write the next task specification to **`.ai/next_task.md`** (which does not trigger `ai-loop.yml`). Once the workstation is back online, move `.ai/next_task.md` to `.ai/task.md` and push.

---

## 5. Standard Experiment Workflow (6 Steps)

1. **Survey Past Runs**: Read recent `runs/<n>/summary.md` and `experiments/*/PROTOCOL.md` to avoid duplicating prior experiments.
2. **Freeze Protocol First**: Before running any experiment, create `experiments/EXPxxx_<name>/PROTOCOL.md` defining the dataset, baseline, metrics, and pass/fail threshold. Commit and push it directly from the Cloud Brain (local agents are blocked from editing `PROTOCOL.md`).
3. **Write Self-Contained `.ai/task.md`**: Remember that `codex exec` / `claude -p` runs statelessly in a fresh process. State the exact files to read, steps to execute, synthetic unit tests to run, output paths, and `metrics.json` schema.
4. **Dispatch & Wait**: Push `.ai/task.md` (or dispatch `ai-loop.yml`), then poll `git fetch` in the background until `ai-loop: run #<N>` appears.
5. **Audit Rigorously**:
   - Check `runs/<N>/guard.md` to see if the agent attempted to touch protected files or stage `> 5 MB` files.
   - Verify unit tests passed and inspect the actual code diff (`git show HEAD`).
6. **Synthesize for the User**: Report the conclusion, quantitative metrics, limitations, and recommended next step.
