#!/usr/bin/env bash
# Development install for the Forge monorepo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  echo "error: no interpreter at $PYTHON" >&2
  echo "Create one first:  python3.12 -m venv .venv" >&2
  exit 1
fi

echo "==> Python: $("$PYTHON" --version)"

echo "==> Installing third-party dependencies"
"$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PYTHON" -m pip install --quiet --disable-pip-version-check -r "$ROOT/requirements-dev.txt"

echo "==> Verifying workspace imports"
# shellcheck source=scripts/env.sh
. "$ROOT/scripts/env.sh"
"$PYTHON" "$ROOT/scripts/verify_imports.py"

echo "==> Development install complete."
echo "    Run 'source scripts/env.sh' in your shell, or use the Makefile targets."
