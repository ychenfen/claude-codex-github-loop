#!/usr/bin/env bash
# Safely collect specified local files into an ephemeral artifact staging directory
# (never committed to git) so the Cloud Brain can download and inspect them.
#
# Usage: collect.sh "<semicolon-or-newline-separated-paths>" <DEST_DIR> <MANIFEST_FILE>
# Skips:
#   - .git, node_modules, __pycache__, .venv, venv, .mypy_cache, .pytest_cache
#   - Suspected credential/secret files (.env*, *.pem, *.key, *secret*, *token*, *credential*, etc.)
#   - Individual files > 20 MB
#   - Anything beyond 100 MB total or 3000 files total
set -uo pipefail
shopt -s nullglob dotglob 2>/dev/null || true
shopt -s globstar 2>/dev/null || true

LIST="$1"
DEST="$2"
MANIFEST="$3"
MAX_FILE=$((20 * 1024 * 1024))
MAX_TOTAL=$((100 * 1024 * 1024))
MAX_FILES=3000

mkdir -p "$DEST"
: > "$MANIFEST"
total=0
count=0

to_unix() {
  if command -v cygpath > /dev/null 2>&1; then
    cygpath -u "$1"
  else
    printf '%s' "$1"
  fi
}

normalize_rel_path() {
  local f="$1"
  local abs_dir abs_file
  abs_dir=$(cd "$(dirname "$f")" 2>/dev/null && pwd || dirname "$f")
  abs_file="$abs_dir/$(basename "$f")"
  printf '%s' "$abs_file" | sed -E 's#^/([a-zA-Z])/#\1/#; s#^/##'
}

skip_reason() {
  local f="$1"
  local base
  base=$(basename "$f" | tr '[:upper:]' '[:lower:]')
  case "/$f/" in
    */.git/*|*/node_modules/*|*/__pycache__/*|*/.venv/*|*/venv/*|*/.mypy_cache/*|*/.pytest_cache/*)
      echo "cache/dependency directory"
      return ;;
  esac
  case "$base" in
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*|*.kdbx|*credential*|*secret*|*password*|*token*|cookies*|*.cookie)
      echo "suspected secret/credential"
      return ;;
  esac
}

take() {
  local f="$1" size rel reason
  reason=$(skip_reason "$f")
  if [ -n "$reason" ]; then
    printf 'SKIP\t-\t%s\t%s\n' "$f" "$reason" >> "$MANIFEST"
    return
  fi
  size=$(wc -c < "$f" 2>/dev/null | tr -d '[:space:]') || {
    printf 'SKIP\t-\t%s\tunreadable\n' "$f" >> "$MANIFEST"
    return
  }
  if [ "$size" -gt "$MAX_FILE" ]; then
    printf 'SKIP\t%s\t%s\texceeds 20 MB per-file limit\n' "$size" "$f" >> "$MANIFEST"
    return
  fi
  if [ $((total + size)) -gt "$MAX_TOTAL" ] || [ "$count" -ge "$MAX_FILES" ]; then
    printf 'SKIP\t%s\t%s\texceeds 100 MB / 3000 files cap\n' "$size" "$f" >> "$MANIFEST"
    return
  fi
  rel=$(normalize_rel_path "$f")
  mkdir -p "$DEST/$(dirname "$rel")"
  if cp -p "$f" "$DEST/$rel" 2>/dev/null; then
    total=$((total + size))
    count=$((count + 1))
    printf 'OK\t%s\t%s\n' "$size" "$f" >> "$MANIFEST"
  else
    printf 'SKIP\t%s\t%s\tcopy failed\n' "$size" "$f" >> "$MANIFEST"
  fi
}

while IFS= read -r item; do
  item="${item#"${item%%[![:space:]]*}"}"
  item="${item%"${item##*[![:space:]]}"}"
  [ -n "$item" ] || continue
  p=$(to_unix "$item")
  IFS=''
  matches=( $p )
  unset IFS
  if [ ${#matches[@]} -eq 0 ]; then
    printf 'MISS\t-\t%s\tnot found or no glob match\n' "$item" >> "$MANIFEST"
    continue
  fi
  for m in "${matches[@]}"; do
    if [ -d "$m" ]; then
      while IFS= read -r -d '' f; do
        take "$f"
      done < <(find "$m" \( -name .git -o -name node_modules -o -name __pycache__ -o -name .venv -o -name venv \) -prune -o -type f -print0)
    elif [ -f "$m" ]; then
      take "$m"
    else
      printf 'MISS\t-\t%s\tnot found or no glob match\n' "$m" >> "$MANIFEST"
    fi
  done
done < <(printf '%s\n' "$LIST" | tr ';' '\n')

echo "Collected $count file(s), $((total / 1024)) KB total; manifest written to $MANIFEST"
