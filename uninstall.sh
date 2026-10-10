#!/usr/bin/env bash
set -euo pipefail
KDM_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$KDM_SCRIPT_DIR/scripts/manage.py" uninstall "$@"
