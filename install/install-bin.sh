#!/usr/bin/env bash
set -euo pipefail

TARGET="$HOME/.local/bin/ccollab"
RUNTIME_ROOT="${CCOLLAB_RUNTIME_ROOT:-}"

if [[ -z "$RUNTIME_ROOT" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    RUNTIME_ROOT="$HOME/Library/Application Support/cc_collab/install"
  else
    RUNTIME_ROOT="$HOME/.local/share/cc_collab/install"
  fi
fi

quoted_runtime_root="$(printf '%q' "$RUNTIME_ROOT")"
quoted_launcher="$(printf '%q' "$RUNTIME_ROOT/bin/ccollab")"

mkdir -p "$(dirname "$TARGET")"
cat >"$TARGET" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export CCOLLAB_RUNTIME_ROOT=$quoted_runtime_root
exec $quoted_launcher "\$@"
EOF
chmod +x "$TARGET"
