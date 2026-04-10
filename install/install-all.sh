#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_install_root() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    printf '%s\n' "$HOME/Library/Application Support/cc_collab/install"
    return
  fi
  printf '%s\n' "$HOME/.local/share/cc_collab/install"
}

python_works() {
  local candidate="$1"
  command -v "$candidate" >/dev/null 2>&1 || return 1
  "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 0) else 1)" \
    >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in python3 python; do
    if python_works "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_python() {
  if command -v brew >/dev/null 2>&1; then
    echo "Attempting to install Python via brew..."
    if brew install python; then
      return 0
    fi
    echo "brew install python failed." >&2
  fi
  echo "Install Python 3 and re-run ./install/install-all.sh." >&2
  echo "If you use Homebrew, run: brew install python" >&2
  return 1
}

copy_payload() {
  local install_root="$1"
  rm -rf "$install_root"
  mkdir -p "$install_root"
  local relative
  for relative in bin runtime skill install examples; do
    cp -R "$ROOT/$relative" "$install_root/$relative"
  done
  cp "$ROOT/README.md" "$install_root/README.md"
  cp "$ROOT/AGENTS.md" "$install_root/AGENTS.md"
}

refresh_session_path() {
  local bin_dir="$HOME/.local/bin"
  mkdir -p "$bin_dir"
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) export PATH="$bin_dir:$PATH" ;;
  esac
  echo "Current session PATH includes: $bin_dir"
}

run_doctor() {
  local launcher="$HOME/.local/bin/ccollab"
  set +e
  local output
  output="$("$launcher" doctor 2>&1)"
  local status=$?
  set -e
  printf '%s\n' "$output"
  if [[ $status -ne 0 ]]; then
    echo "ccollab installed, but runtime readiness still needs attention." >&2
  fi
}

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
  install_python
  PYTHON="$(find_python || true)"
fi
if [[ -z "$PYTHON" ]]; then
  exit 1
fi

INSTALL_ROOT="$(resolve_install_root)"
export CCOLLAB_RUNTIME_ROOT="$INSTALL_ROOT"
copy_payload "$INSTALL_ROOT"
refresh_session_path
"$ROOT/install/install-skill.sh"
"$ROOT/install/install-bin.sh"
run_doctor
