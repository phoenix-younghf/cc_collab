#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$HOME/.local/bin/ccollab"

mkdir -p "$(dirname "$TARGET")"
ln -sfn "$ROOT/bin/ccollab" "$TARGET"

