#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
TARGET="$CODEX_HOME_DIR/skills/delegate-to-claude-code"

mkdir -p "$(dirname "$TARGET")"
ln -sfn "$ROOT/skill/delegate-to-claude-code" "$TARGET"

