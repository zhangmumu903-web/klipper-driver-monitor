#!/usr/bin/env bash
set -euo pipefail
KDM_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "${1-}" == "--local" ]]; then
  shift
  exec python3 "$KDM_SCRIPT_DIR/scripts/manage.py" update "$@"
fi
exec python3 "$KDM_SCRIPT_DIR/scripts/distribution.py" update "$@"
