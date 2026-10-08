#!/usr/bin/env python3
"""Install reviewed host files only. No network, service restart or MCU flashing."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- klipper-driver-monitor:begin -->'
END = '<!-- klipper-driver-monitor:end -->'
LYX_FILES = ('lyx.py', 'lyx_uart.py', 'lyx9231.py')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path, optional=False):
    if path.is_symlink():
        raise ValueError('Refusing symlink: %s' % path)
    if not path.exists():
        if optional:
            return None
        raise ValueError('Missing file: %s' % path)
    if not path.is_file():
        raise ValueError('Not a regular file: %s' % path)
    return path.read_bytes()


def api_url(value, origin_only=False):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or value.endswith('/')
            or any(char.isspace() for char in value)):
        raise ValueError('Use a canonical HTTP(S) origin without a trailing slash: %s' % value)
    # Accessing port also validates a malformed/out-of-range port.
    parsed.port
    if value != parsed.scheme + '://' + parsed.netloc:
        raise ValueError('Noncanonical origin: %s' % value)
    return value


def target_config(hostname, origins, endpoints):
    if not hostname or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', hostname):
        raise ValueError('Set the actual hostname returned by /printer/info')
    origins = list(dict.fromkeys(api_url(item, True) for item in origins))
    endpoints = list(dict.fromkeys(api_url(item) for item in (endpoints or origins)))
    if not origins:
        raise ValueError('At least one --origin is required')
    return {'hostname': hostname, 'pageOrigins': origins,
            'apiEndpoints': [{'apiUrl': item, 'printerId': hashlib.md5(item.encode()).hexdigest()}
                             for item in endpoints]}


def make_assets(config):
    result = {}
    for name in ('driver-monitor.mjs', 'driver-monitor-core.mjs'):
        text = regular(ROOT / 'frontend' / name).decode('utf-8')
        text = re.sub(r"(['\"]\./[^'\"]+)\.mjs(['\"])", r'\1.js\2', text)
        result[name.replace('.mjs', '.js')] = text.encode('utf-8')
    result['driver-monitor.css'] = regular(ROOT / 'frontend/driver-monitor.css')
    result['driver-monitor-config.js'] = (
        '// Generated target identity; contains no credentials.\nexport const TARGET_CONFIG = '
        + json.dumps(config, ensure_ascii=True, indent=2) + ';\n').encode('utf-8')
    return result


def patch_index(data, asset_dir):
    text = data.decode('utf-8')
    pattern = re.escape(BEGIN) + r'.*?' + re.escape(END)
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise ValueError('Malformed or duplicated monitor markers in index.html')
    previous = re.findall(pattern, text, re.S)
    unmarked = re.sub(pattern, '', text, flags=re.S)
    class ScriptFinder(HTMLParser):
        found = False

        def handle_starttag(self, tag, attrs):
            if tag == 'script':
                self.found = self.found or any(
                    key == 'src' and value and 'driver-monitor' in value for key, value in attrs)

    finder = ScriptFinder()
    finder.feed(unmarked)
    if finder.found:
        raise ValueError('Existing unmanaged driver-monitor script: migrate its index tag first')
    block = BEGIN + '\n<script type="module" src="/' + asset_dir + '/driver-monitor.js"></script>\n' + END
    if previous:
        return re.sub(pattern, lambda unused: block, text, flags=re.S).encode('utf-8')
    matches = list(re.finditer(r'</body\s*>', text, re.I))
    if len(matches) != 1:
        raise ValueError('Expected one closing body tag in Fluidd index.html')
    pos = matches[0].start()
    return (text[:pos] + block + '\n' + text[pos:]).encode('utf-8')


def build_plan(args):
    # Default to the original all-components/LYX workflow for old callers.
    components = getattr(args, 'components', 'all')
    drivers = getattr(args, 'drivers', 'lyx')
    with_lyx = getattr(args, 'with_lyx', False)
    if components not in ('all', 'backend', 'fluidd'):
        raise ValueError('Unsupported --components: %s' % components)
    if drivers == 'tmc':
        raise ValueError('TMC-only installation is no longer offered: this monitor displays LYX only. '
                         'Existing TMC configurations are unchanged; old receipts still support rollback.')
    if drivers != 'lyx':
        raise ValueError('Unsupported --drivers: %s' % drivers)
    if with_lyx and components == 'fluidd':
        raise ValueError('--with-lyx requires backend or all components')
    backend = components in ('all', 'backend')
    web = components in ('all', 'fluidd')
    paths = {'klipper': None, 'fluidd': None}
    config = asset_dir = None
    changes = []

    def add(path, after):
        before = regular(path, optional=True)
        if before != after:
            changes.append({'path': path, 'before': before, 'after': after})

    if backend:
        if not getattr(args, 'klipper', None):
            raise ValueError('--klipper is required for backend or all components')
        klipper = Path(args.klipper).expanduser().resolve(strict=True)
        extras = klipper / 'klippy/extras'
        if not extras.is_dir() or extras.is_symlink():
            raise ValueError('Expected klippy/extras under --klipper')
        paths['klipper'] = str(klipper)
        # Only LYX dependency validation belongs to this monitor.
        if drivers == 'lyx':
            provenance = json.loads(regular(ROOT / 'vendor/lyx/PROVENANCE.json'))
            for name in LYX_FILES:
                source = regular(ROOT / 'vendor/lyx' / name)
                if digest(source) != provenance['patched_sha256'][name]:
                    raise ValueError('Packaged LYX source hash mismatch: ' + name)
                installed = regular(extras / name, optional=True)
                if with_lyx:
                    allowed = {provenance['upstream_sha256'][name], digest(source)}
                    if installed is not None and digest(installed) not in allowed:
                        raise ValueError('Unknown LYX edits; compare manually before replacing: ' + name)
                    add(extras / name, source)
                elif installed != source:
                    raise ValueError('A matching patched LYX host module is required: %s; '
                                     'review --with-lyx or compare manually' % name)
        add(extras / 'driver_monitor.py', regular(ROOT / 'backend/driver_monitor.py'))

    if web:
        if not getattr(args, 'fluidd', None):
            raise ValueError('--fluidd is required for fluidd or all components')
        fluidd = Path(args.fluidd).expanduser().resolve(strict=True)
        index = fluidd / 'index.html'
        before_index = regular(index)
        config = target_config(getattr(args, 'hostname', None),
                               getattr(args, 'origin', None) or [],
                               getattr(args, 'api_url', None))
        paths['fluidd'] = str(fluidd)
        assets = make_assets(config)
        version = digest(b''.join(name.encode() + assets[name] for name in sorted(assets)))[:16]
        asset_dir = 'driver-monitor-' + version
        for name, data in assets.items():
            destination = fluidd / asset_dir / name
            if destination.parent.is_symlink():
                raise ValueError('Refusing symlink asset directory')
            add(destination, data)
        # Switch the web entry only after every asset is in place.
        add(index, patch_index(before_index, asset_dir))
    return {'components': components, 'drivers': drivers, 'paths': paths,
            'config': config, 'asset_dir': asset_dir, 'changes': changes}


def atomic_write(path, data, metadata=None):
    # New versioned web assets must be readable by nginx even under root umask
    # 077. Never chmod an existing user directory.
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=False)
        if path.parent.name.startswith('driver-monitor-'):
            path.parent.chmod(0o755)
    fd, temp = tempfile.mkstemp(prefix='.driver-monitor-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, metadata['mode'] if metadata else 0o644)
        if metadata and hasattr(os, 'chown'):
            os.chown(temp, metadata['uid'], metadata['gid'])
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def check_current(item, expected):
    data = regular(Path(item['path']), optional=True)
    actual = None if data is None else digest(data)
    if actual != expected:
        raise ValueError('File changed since planning; not overwriting: %s' % item['path'])


def apply_plan(plan, backup_root):
    changes = plan['changes']
    if not changes:
        return {'status': 'unchanged', 'files': 0,
                'components': plan.get('components', 'all'),
                'drivers': plan.get('drivers', 'lyx'), 'paths': plan.get('paths')}
    for item in changes:
        check_current(item, None if item['before'] is None else digest(item['before']))
    backup_root = Path(backup_root).expanduser().resolve()
    backup = backup_root / (time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    records = []
    for i, item in enumerate(changes):
        path = item['path']
        metadata = None
        if item['before'] is not None:
            st = path.stat()
            metadata = {'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid}
            (backup / ('%03d.before' % i)).write_bytes(item['before'])
        records.append({'path': str(path), 'before_sha256': None if item['before'] is None else digest(item['before']),
                        'after_sha256': digest(item['after']), 'backup_file': '%03d.before' % i,
                        'metadata': metadata, 'written': False})
    receipt = {'format': 1, 'status': 'installing', 'asset_dir': plan['asset_dir'],
               'components': plan.get('components', 'all'),
               'drivers': plan.get('drivers', 'lyx'), 'paths': plan.get('paths'),
               'target': plan.get('config'),
               'service_restarted': False, 'firmware_flashed': False, 'files': records}
    state_file = backup / 'receipt.json'

    def save():
        atomic_write(state_file, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())

    save()
    try:
        for item, record in zip(changes, records):
            check_current(record, record['before_sha256'])
            # Write-ahead receipt: rollback also handles a crash between replace
            # and receipt update, or a failed replace that left original bytes.
            record['written'] = True
            save()
            atomic_write(item['path'], item['after'], record['metadata'])
        receipt['status'] = 'installed'
    except Exception:
        receipt['status'] = 'partial; inspect receipt and use rollback'
        raise
    finally:
        save()
        print('Backup and rollback receipt: %s' % state_file)
    return {'status': 'installed', 'files': len(records), 'receipt': str(state_file),
            'components': receipt['components'], 'drivers': receipt['drivers'],
            'paths': receipt['paths']}


def rollback(receipt_path, apply=False):
    receipt_path = Path(receipt_path).expanduser().resolve(strict=True)
    receipt = json.loads(regular(receipt_path))
    if receipt.get('format') != 1 or not isinstance(receipt.get('files'), list):
        raise ValueError('Unsupported receipt')
    selected = []
    unchanged = []
    for item in receipt['files']:
        if not item['written']:
            continue
        current = regular(Path(item['path']), optional=True)
        if (None if current is None else digest(current)) == item['before_sha256']:
            unchanged.append(item)
            continue
        check_current(item, item['after_sha256'])
        data = None
        if item['before_sha256'] is not None:
            name = item['backup_file']
            if Path(name).name != name:
                raise ValueError('Invalid backup filename')
            data = regular(receipt_path.parent / name)
            if digest(data) != item['before_sha256']:
                raise ValueError('Backup hash mismatch')
        selected.append((item, data))
    if apply:
        for item in unchanged:
            item['written'] = False
        for item, data in reversed(selected):
            check_current(item, item['after_sha256'])
            path = Path(item['path'])
            if data is None:
                path.unlink()
            else:
                atomic_write(path, data, item['metadata'])
            item['written'] = False
            atomic_write(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
        receipt['status'] = 'rolled_back'
        atomic_write(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
    return {'status': 'rolled_back' if apply else 'rollback_plan', 'files': len(selected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('plan', 'install'):
        p = sub.add_parser(command)
        p.add_argument('--components', choices=('all', 'backend', 'fluidd'), default='all',
                       help='Components to install (default: all)')
        p.add_argument('--drivers', choices=('lyx', 'tmc'), default='lyx',
                       help='LYX host dependencies (default: lyx); legacy tmc selection is rejected with migration guidance')
        p.add_argument('--klipper', help='Klipper checkout root; required for backend/all')
        p.add_argument('--fluidd', help='Actual Fluidd webroot; required for fluidd/all')
        p.add_argument('--hostname', help='Exact /printer/info hostname; required for fluidd/all')
        p.add_argument('--origin', action='append', help='Allowed Fluidd page origin; required for fluidd/all, repeatable')
        p.add_argument('--api-url', action='append', help='Exact API URL saved in Fluidd, repeatable; defaults to origin')
        p.add_argument('--with-lyx', action='store_true', help='Also install reviewed matching LYX host modules')
        p.add_argument('--backup-dir', default=str(ROOT / '.install-backups'))
    p = sub.add_parser('rollback')
    p.add_argument('--receipt', required=True)
    p.add_argument('--apply', action='store_true', help='Otherwise only show rollback plan')
    args = parser.parse_args()
    try:
        if args.command == 'rollback':
            result = rollback(args.receipt, args.apply)
        else:
            plan = build_plan(args)
            result = {'status': 'plan', 'target': plan['config'], 'asset_dir': plan['asset_dir'],
                      'components': plan['components'], 'drivers': plan['drivers'],
                      'paths': plan['paths'],
                      'files': [{'path': str(x['path']), 'action': 'create' if x['before'] is None else 'replace'}
                                for x in plan['changes']]}
            if args.command == 'install':
                result = apply_plan(plan, args.backup_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print('No config include, restart, enable, motion, heat or firmware command was performed.')
    except (ValueError, OSError) as error:
        parser.exit(2, 'Error: %s\n' % error)


if __name__ == '__main__':
    main()
