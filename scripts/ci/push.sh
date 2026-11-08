#!/usr/bin/env bash
# Commit staged changes and push back to TARGET_BRANCH (defaults to current branch or main).
# Retries up to 4 times with exponential backoff and `git pull --rebase --autostash`
# in case another workflow or user pushed in the meantime.
#
# Usage: push.sh "<commit-message>"
set -euo pipefail

BRANCH="${TARGET_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
if [ "$BRANCH" = "HEAD" ]; then
  BRANCH="main"
fi

git config user.name  "${GIT_AUTHOR_NAME:-ai-loop-runner}"
git config user.email "${GIT_AUTHOR_EMAIL:-ai-loop-runner@users.noreply.github.com}"

git diff --cached --quiet && { echo "No staged changes to commit."; exit 0; }
git commit -q -m "$1"

for i in 1 2 3 4; do
  if git pull -q --rebase --autostash origin "$BRANCH" && git push -q origin "HEAD:$BRANCH"; then
    git log -1 --format="Pushed %h %s -> $BRANCH"
    exit 0
  fi
  echo "Push attempt $i failed; retrying in $((i * 5))s..."
  sleep $((i * 5))
done

echo "::error::Failed to push to $BRANCH after 4 attempts; outputs remain in the workflow artifact."
exit 1
