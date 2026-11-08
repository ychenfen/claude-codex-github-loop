# Experiment EXP001 — Hardware & Local Dataset Smoke Verification

## Objective
Run the baseline workstation health and local path verification defined in `experiments/EXP001_example_benchmark/PROTOCOL.md`:
1. Execute `python3 scripts/tools/smoke_test.py --out "$RUN_DIR/metrics.json"`.
2. Run the unit test suite (`python3 -m pytest tests/ -q`) and confirm 100% pass rate.
3. Write a concise summary in `$RUN_DIR/summary.md` reporting:
   - Host OS, CPU architecture, and Python version
   - PyTorch CUDA / Apple Silicon MPS accelerator availability
   - Status of configured local dataset roots from `config/local_paths.example.json`
   - Unit test pass count

## Constraints
- Do NOT modify any `PROTOCOL.md`, `.github/`, `.ai/`, or `scripts/ci/` files.
- Keep all generated files under 5 MB.
