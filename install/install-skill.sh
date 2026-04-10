#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
TARGET="$CODEX_HOME_DIR/skills/delegate-to-claude-code"
RUNTIME_ROOT="${CCOLLAB_RUNTIME_ROOT:-}"

if [[ -z "$RUNTIME_ROOT" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    RUNTIME_ROOT="$HOME/Library/Application Support/cc_collab/install"
  else
    RUNTIME_ROOT="$HOME/.local/share/cc_collab/install"
  fi
fi

mkdir -p "$(dirname "$TARGET")"
ln -sfn "$RUNTIME_ROOT/skill/delegate-to-claude-code" "$TARGET"
