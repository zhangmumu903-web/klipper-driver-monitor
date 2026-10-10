#!/usr/bin/env python3
"""Build deterministic public release assets from tracked repository files."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def bootstrap():
    prelude = '''#!/usr/bin/env bash
# Standalone public installer; a local checkout runs its own reviewed version.
set -euo pipefail
KDM_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
command -v python3 >/dev/null || { printf '需要 Python 3.8+。\\n' >&2; exit 2; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else "需要 Python 3.8+")'
if [[ -f "$KDM_SCRIPT_DIR/scripts/manage.py" && -f "$KDM_SCRIPT_DIR/VERSION" ]]; then
  exec python3 "$KDM_SCRIPT_DIR/scripts/manage.py" install "$@"
fi
# Keep stdin attached to the terminal so the downloaded manager can prompt.
exec python3 -c "$(cat <<'KDM_BOOTSTRAP_PY'
'''
    suffix = '''
KDM_BOOTSTRAP_PY
)" install "$@"
'''
    return (prelude + (ROOT / 'scripts/distribution.py').read_text() + suffix).encode()


def package(output):
    if (ROOT / 'install.sh').read_bytes() != bootstrap():
        raise ValueError('install.sh is stale; run package-release.py --write-bootstrap')
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=str(ROOT)).decode().split('\0')
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name in sorted(filter(None, names)):
                path = ROOT / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Only tracked regular files may ship: ' + name)
                data = path.read_bytes()
                entry = tarfile.TarInfo('klipper-driver-monitor/' + name)
                entry.size = len(data)
                entry.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                archive.addfile(entry, io.BytesIO(data))
    output.mkdir(parents=True, exist_ok=True)
    data = buffer.getvalue()
    (output / 'klipper-driver-monitor.tar.gz').write_bytes(data)
    (output / 'SHA256SUMS').write_text(hashlib.sha256(data).hexdigest() + '  klipper-driver-monitor.tar.gz\n')
    (output / 'install.sh').write_bytes(bootstrap())
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-bootstrap', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/release')
    options = parser.parse_args()
    if options.write_bootstrap:
        (ROOT / 'install.sh').write_bytes(bootstrap())
    else:
        package(options.output)
