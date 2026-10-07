#!/usr/bin/env bash
# One host-side entry point; firmware builds and flashing are separate.
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v python3 >/dev/null 2>&1; then
  printf '需要 Python 3.8+；请先安装 python3。\n' >&2
  exit 2
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else "需要 Python 3.8+")'
case "${1-}" in
  plan|install|rollback) exec python3 "$SCRIPT_DIR/scripts/install.py" "$@" ;;
  *) exec python3 "$SCRIPT_DIR/scripts/setup.py" "$@" ;;
esac
