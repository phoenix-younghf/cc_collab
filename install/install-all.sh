#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/install/install-skill.sh"
"$ROOT/install/install-bin.sh"
python3 -m runtime.cli doctor

