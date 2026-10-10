#!/bin/sh
# Build in an isolated directory; never flash or restart the printer.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/build-c8p.py" "$@" --mode usb
