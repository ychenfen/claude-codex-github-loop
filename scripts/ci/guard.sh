#!/usr/bin/env bash
# Pre-push hard security guardrail:
# 1) Soft-resets any agent commits back to <BASE_SHA>
# 2) Reverts modifications (and deletes untracked additions) to protected paths:
#    .github/, .ai/, .claude/, scripts/ci/, and any **/PROTOCOL.md
# 3) Stages remaining changes and unstages files > MAX_MB (default 5 MB)
# 4) Unstages suspected credential/secret files (.env*, *.pem, *.key, *secret*, *token*, *credential*, etc.)
# 5) Writes a transparent audit log to <RUN_DIR>/guard.md
#
# Usage: guard.sh <BASE_SHA> <RUN_DIR>
# Note: The workflow copies scripts/ci/ to $RUNNER_TEMP/ci BEFORE executing the agent,
# so even if the agent modifies scripts/ci/ in the workspace, the frozen copy runs.
set -euo pipefail

BASE="$1"
RUN_DIR="${2%/}"
OUT="$RUN_DIR/guard.md"
MAX_MB="${MAX_MB:-5}"
MAX_BYTES=$((MAX_MB * 1024 * 1024))

# Protected directories & frozen protocol files
PROTECTED=(.github .ai .claude scripts/ci)
while IFS= read -r p; do
  [ -n "$p" ] && PROTECTED+=("$p")
done < <({ git ls-tree -r --name-only "$BASE"; git ls-files --others --exclude-standard; } | grep -E '(^|/)PROTOCOL\.md$' | sort -u || true)

# 1. Soft-reset any commits made by the agent back to BASE_SHA
if [ "$(git rev-parse HEAD)" != "$BASE" ]; then
  git reset --soft "$BASE"
fi

# 2. Revert modifications or remove untracked files in protected paths
reverted=()
for p in "${PROTECTED[@]}"; do
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if git cat-file -e "$BASE:$f" 2>/dev/null; then
      git checkout "$BASE" -- "$f"
      reverted+=("\`$f\` (modification reverted)")
    else
      git rm -q --cached --ignore-unmatch -- "$f" >/dev/null 2>&1 || true
      rm -f -- "$f"
      reverted+=("\`$f\` (untracked addition removed)")
    fi
  done < <({ git diff --name-only "$BASE" -- "$p"; git ls-files --others --exclude-standard -- "$p"; } 2>/dev/null | sort -u)
done

# 3. Stage all remaining workspace changes
git add -A

# Helper: check if basename matches suspected credential patterns
is_secret_file() {
  local base
  base=$(basename "$1" | tr '[:upper:]' '[:lower:]')
  case "$base" in
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*|*.kdbx|*credential*|*secret*|*password*|*token*|cookies*|*.cookie)
      return 0 ;;
    *)
      return 1 ;;
  esac
}

# 4. Block oversized files (> MAX_MB) and suspected secret files
blocked_size=()
blocked_secrets=()
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  [ "$f" = "$OUT" ] && continue
  if is_secret_file "$f"; then
    git rm -q --cached -- "$f"
    blocked_secrets+=("\`$f\` (suspected credential/secret file, unstaged)")
    continue
  fi
  size=$(wc -c < "$f" | tr -d '[:space:]')
  if [ "$size" -gt "$MAX_BYTES" ]; then
    git rm -q --cached -- "$f"
    blocked_size+=("\`$f\` ($((size / 1024 / 1024)) MB > ${MAX_MB} MB limit, unstaged & kept on runner)")
  fi
done < <(git diff --cached --name-only -z)

# 5. Write transparent audit log
mkdir -p "$RUN_DIR"
{
  echo "# Guardrail Audit Report (\`guard.md\`)"
  echo
  echo "- **Base Commit**: \`$BASE\`"
  echo "- **Max File Size**: \`${MAX_MB} MB\`"
  echo
  if [ ${#reverted[@]} -eq 0 ] && [ ${#blocked_size[@]} -eq 0 ] && [ ${#blocked_secrets[@]} -eq 0 ]; then
    echo "All checks passed: no protected paths modified, no oversized files, and no suspected credential files."
  fi
  if [ ${#reverted[@]} -gt 0 ]; then
    echo "## Protected Path Modifications Reverted"
    printf -- '- %s\n' "${reverted[@]}"
    echo
  fi
  if [ ${#blocked_size[@]} -gt 0 ]; then
    echo "## Oversized Files Blocked (> ${MAX_MB} MB)"
    printf -- '- %s\n' "${blocked_size[@]}"
    echo
  fi
  if [ ${#blocked_secrets[@]} -gt 0 ]; then
    echo "## Suspected Credential Files Blocked"
    printf -- '- %s\n' "${blocked_secrets[@]}"
    echo
  fi
} > "$OUT"
git add -f -- "$OUT"

for r in ${reverted[@]+"${reverted[@]}"}; do echo "::warning::Guard reverted protected path: $r"; done
for b in ${blocked_size[@]+"${blocked_size[@]}"}; do echo "::warning::Guard blocked oversized file: $b"; done
for s in ${blocked_secrets[@]+"${blocked_secrets[@]}"}; do echo "::warning::Guard blocked secret file: $s"; done
echo "Guard summary: ${#reverted[@]} reverted, ${#blocked_size[@]} oversized blocked, ${#blocked_secrets[@]} secrets blocked"
