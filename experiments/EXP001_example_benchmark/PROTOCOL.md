# Frozen Experiment Protocol: EXP001 (Baseline Workstation & Dataset Audit)

> **IMMUTABILITY RULE**: Every `**/PROTOCOL.md` file is hardware-protected by `scripts/ci/guard.sh` and `scripts/ci/guard.py`.
> The Cloud Brain freezes `PROTOCOL.md` *before* dispatching the local agent. Any attempt by the local agent to modify a frozen `PROTOCOL.md` during execution is automatically reverted before commit.

## 1. Hypothesis & Goal
Verify that the local workstation runner (macOS Apple Silicon / Linux CUDA / Windows) can execute deterministic evaluation scripts against read-only local datasets without leaking raw data or credentials into Git.

## 2. Dataset & Environment Specification
- **Config File**: `config/local_paths.json` (fallback: `config/local_paths.example.json`)
- **Read-Only Roots**: Local dataset directories must never be modified or copied into the repository.
- **Derived Outputs**: Aggregated JSON/CSV metrics under `data/derived/exp001/` or `runs/<run_number>/metrics.json` (must be `< 5 MB`).

## 3. Acceptance Criteria
1. `scripts/tools/smoke_test.py` exits with code `0` and `"file_probe": "passed"`.
2. `pytest tests/` passes 100% of guardrail and tool checks.
3. `guard.md` reports `0` unapproved protected-path mutations.
