#!/usr/bin/env python3
"""Manage only Driver Monitor and Fluidd cards; never flash, move, heat or restart."""
import argparse
import copy
import fcntl
import fnmatch
import glob
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('monitor_install', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)
CFG_BEGIN = '# klipper-driver-monitor:begin'
CFG_END = '# klipper-driver-monitor:end'
CONFIG_NAME = 'driver-monitor.cfg'
DEFAULT_CONFIG = ("# Driver Monitor: existing motor/LYX settings are not changed.\n"
                  "# Set shutdown_on_alarm: true to stop on a valid alarm after enable.\n"
                  "[driver_monitor]\nauto_start: true\nshutdown_on_alarm: false\n"
                  "cycle_interval: 1.0\nread_gap: 0.2\n").encode()
DEFAULT_STATE = Path.home() / '.local/share/klipper-driver-monitor/state'
COMPONENTS = ('backend', 'frontend')


def serial(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()


def sha(data):
    return None if data is None else installer.digest(data)


def checked_path(path, root=None):
    """Resolve an explicit root, but never follow a symlink inside its tree."""
    path = Path(path).expanduser().absolute()
    if root is None:
        # Existing parent may be a system alias (/var on macOS). Do not accept
        # the actual output itself as a symlink.
        if path.is_symlink():
            raise ValueError('Refusing symlink: %s' % path)
        return path.parent.resolve() / path.name
    root = Path(root).resolve()
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise ValueError('Path escapes selected root: %s' % path)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('Refusing symlink inside selected root: %s' % cursor)
    return path


def read(path, optional=False, root=None):
    return installer.regular(checked_path(path, root), optional)


def root_directory(value, marker):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir() or not (path / marker).exists():
        raise ValueError('Expected %s under %s' % (marker, path))
    checked_path(path / marker, path)
    return path


def markers(data, begin, end):
    text = data.decode('utf-8')
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise ValueError('Malformed or duplicated managed markers')
    pattern = re.escape(begin) + r'.*?' + re.escape(end)
    found = re.findall(pattern, text, re.S)
    return found[0] if found else None


def swap_block(data, begin, end, expected, desired):
    current = markers(data, begin, end)
    if current != expected:
        raise ValueError('Managed entry was changed; inspect before replacing it')
    text = data.decode('utf-8')
    if current is not None:
        text = text.replace(current, desired or '', 1)
    elif desired:
        # Klipper splits the file at SAVE_CONFIG. An include appended below
        # that marker looks valid to a human but is never loaded at runtime.
        autosave = re.search(r'^#\*# .*SAVE_CONFIG.*$', text, re.M) if begin == CFG_BEGIN else None
        if autosave:
            prefix, suffix = text[:autosave.start()], text[autosave.start():]
            text = prefix + ('' if prefix.endswith('\n') else '\n') + desired + '\n' + suffix
        elif begin == installer.BEGIN:
            closing = list(re.finditer(r'</body\s*>', text, re.I))
            if len(closing) != 1:
                raise ValueError('Expected one closing body tag before restoring Fluidd entry')
            pos = closing[0].start()
            text = text[:pos] + desired + '\n' + text[pos:]
        else:
            text += ('' if text.endswith('\n') else '\n') + desired + '\n'
    return text.encode('utf-8')


def scan_config(main, allow_missing=()):
    """Read Klipper includes (including globs); never infer from unrelated CFGs."""
    main = checked_path(main)
    allowed = {checked_path(path) for path in allow_missing}
    sections, files, stack = [], {}, []

    def visit(path):
        path = checked_path(path)
        if path in stack:
            raise ValueError('Recursive Klipper include: %s' % path)
        data = read(path, optional=path in allowed and path != main)
        files[str(path)] = data
        if data is None:
            return
        stack.append(path)
        # Klipper reserves #*# SAVE_CONFIG lines; these are not active includes.
        for line in data.decode('utf-8').splitlines():
            if re.match(r'^#\*# .*SAVE_CONFIG', line):
                break
            line = re.split(r'[#;]', line, maxsplit=1)[0].strip()
            match = re.fullmatch(r'\[([^\]]+)\]', line)
            if not match:
                continue
            section = match.group(1).strip()
            if section.startswith('include '):
                pattern = str(path.parent / section[8:].strip())
                matches = sorted(glob.glob(pattern))
                if not matches and not glob.has_magic(pattern):
                    missing = checked_path(pattern)
                    if missing not in allowed:
                        raise ValueError('Missing Klipper include: %s' % pattern)
                    visit(missing)
                for included in matches:
                    visit(Path(included))
            else:
                sections.append((section, str(path)))
        stack.pop()

    visit(main)
    monitor = [path for section, path in sections if section == 'driver_monitor']
    if len(monitor) > 1:
        raise ValueError('More than one active [driver_monitor] section; fix duplicate includes first')
    return {'monitor': monitor, 'lyx': [section for section, _ in sections
            if section.startswith('lyx9231 ')], 'files': files}


def owned_config_paths(state, main):
    """Only our recorded auxiliary CFG may be absent during recovery/removal."""
    entry = state['entries'].get('config')
    if entry is None:
        return ()
    path = Path(entry['config_path'])
    record = state['files'].get(str(path))
    if (entry['path'] != str(main) or path != main.parent / CONFIG_NAME
            or not record or record.get('component') != 'backend'):
        raise ValueError('Inconsistent owned monitor CFG record; inspect lifecycle manifest')
    return (path,)


def load_state(directory):
    path = checked_path(directory) / 'manifest.json'
    data = read(path, optional=True, root=path.parent)
    if data is None:
        return None
    state = json.loads(data)
    if state.get('format') != 2:
        raise ValueError('Unsupported lifecycle state format; do not reuse a legacy receipt as state')
    return state


def options(args, state):
    result = copy.deepcopy(state.get('options', {}) if state else {})
    for name in ('klipper', 'fluidd', 'config', 'hostname', 'origin', 'api_url', 'moonraker_url'):
        value = getattr(args, name, None)
        if value is not None:
            if state and name in ('klipper', 'fluidd', 'config') and result.get(name):
                if Path(value).expanduser().resolve() != Path(result[name]):
                    raise ValueError('A state directory belongs to one instance; use a separate --state-dir')
            result[name] = value
    result.setdefault('moonraker_url', 'http://127.0.0.1:7125')
    installer.api_url(result['moonraker_url'])
    return result


def selected(args, state=None):
    if args.frontend_only:
        return ['frontend']
    if args.backend_only:
        return ['backend']
    if state and args.command in ('update', 'repair', 'uninstall', 'doctor'):
        return list(state['components'])
    return list(COMPONENTS)


def complete_options(args, opts, components, interactive):
    needed = []
    if 'backend' in components:
        needed += ['klipper', 'config']
    if 'frontend' in components:
        needed += ['fluidd', 'hostname', 'origin']
    for key in needed:
        if opts.get(key):
            continue
        if not interactive:
            raise ValueError('Missing --%s; use an interactive terminal or pass this instance explicitly' % key.replace('_', '-'))
        candidates = []
        if key in ('klipper', 'fluidd'):
            for base in (Path.home(), Path('/data'), Path('/opt'), Path('/srv')):
                candidate = base / key
                marker = 'klippy/extras' if key == 'klipper' else 'index.html'
                if (candidate / marker).exists():
                    candidates.append(str(candidate))
            if key == 'fluidd' and Path('/var/www/fluidd/index.html').exists():
                candidates.append('/var/www/fluidd')
        elif key == 'config':
            candidates = [str(path) for path in (Path.home() / 'printer_data/config/printer.cfg',
                          Path('/usr/share/printer_data/config/printer.cfg')) if path.is_file()]
        print('Select %s for this Klipper instance%s' % (key, ': ' + ', '.join(candidates) if candidates else ''))
        default = candidates[0] if len(candidates) == 1 else ''
        value = input('%s%s: ' % (key, ' [' + default + ']' if default else '')).strip() or default
        if not value:
            raise ValueError('An explicit %s is required' % key)
        opts[key] = value.split() if key == 'origin' else value
    if 'backend' in components:
        opts['klipper'] = str(root_directory(opts['klipper'], 'klippy/extras'))
        opts['config'] = str(checked_path(opts['config']))
        read(Path(opts['config']))
    if 'frontend' in components:
        opts['fluidd'] = str(root_directory(opts['fluidd'], 'index.html'))
        installer.target_config(opts['hostname'], opts['origin'], opts.get('api_url'))
    return opts


def validate_dependencies(klipper):
    provenance = json.loads(read(ROOT / 'vendor/lyx/PROVENANCE.json'))
    missing = []
    for name in installer.LYX_FILES:
        path = klipper / 'klippy/extras' / name
        data = read(path, optional=True, root=klipper)
        if sha(data) != provenance['patched_sha256'][name]:
            missing.append(name)
    if missing:
        raise ValueError('Compatible LYX host dependencies are required: %s. '
                         'Monitor-only installation will not replace them or flash MCU firmware; '
                         'see docs/INSTALLATION.md.' % ', '.join(missing))


def legacy_frontend(fluidd, block):
    match = re.fullmatch(re.escape(installer.BEGIN) + r'\n<script type="module" src="/(driver-monitor-[0-9a-f]{16})/driver-monitor.js"></script>\n' + re.escape(installer.END), block)
    if not match:
        raise ValueError('Unknown legacy monitor entry; compare manually before adoption')
    folder = fluidd / match.group(1)
    contents = {}
    for item in sorted(folder.iterdir()):
        if item.suffix not in ('.js', '.css'):
            raise ValueError('Unknown legacy asset: %s' % item)
        contents[item.name] = read(item, root=fluidd)
    version = installer.digest(b''.join(name.encode() + contents[name] for name in sorted(contents)))[:16]
    if match.group(1) != 'driver-monitor-' + version:
        raise ValueError('Legacy assets differ from their content hash; inspect before adoption')
    return {str(folder / name): {'sha256': sha(data), 'component': 'frontend'} for name, data in contents.items()}


def new_plan(args, state_dir, interactive=False):
    state = load_state(state_dir)
    if not state and args.command not in ('install', 'doctor'):
        raise ValueError('No lifecycle manifest found; use install --adopt-existing for a reviewed legacy installation')
    comps = selected(args, state)
    opts = complete_options(args, options(args, state), comps, interactive)
    if opts.get('fluidd'):
        web = Path(opts['fluidd'])
        if state_dir == web or web in state_dir.parents or state_dir in web.parents:
            raise ValueError('--state-dir must be outside the Fluidd webroot')
    after_state = copy.deepcopy(state) if state else {
        'format': 2, 'components': [], 'files': {}, 'entries': {}, 'options': opts,
        'firmware_flashed': False, 'service_restarted': False}
    after_state['options'] = opts
    after_state['package_root'] = str(ROOT)
    changes, notes = [], []
    reads = {}

    def source(path, optional=False, root=None):
        path = checked_path(path, root)
        data = read(path, optional, root)
        reads[str(path)] = sha(data)
        return data

    def change(path, before, after, component, embedded=None):
        if before != after:
            item = {'path': str(path), 'before': before, 'after': after, 'component': component}
            if embedded:
                item['embedded'] = embedded
            changes.append(item)

    def managed(path, data, component, root):
        path = checked_path(path, root)
        current = source(path, True, root)
        record = after_state['files'].get(str(path))
        if record and current is not None and sha(current) != record['sha256']:
            raise ValueError('Unknown local edit; not overwriting managed file: %s' % path)
        if not record and current is not None:
            if not args.adopt_existing or current != data:
                raise ValueError('Existing unmanaged file: %s. Use --adopt-existing only for identical reviewed files.' % path)
        change(path, current, data, component)
        after_state['files'][str(path)] = {'sha256': sha(data), 'component': component}

    if args.command in ('install', 'update', 'repair'):
        if 'backend' in comps:
            klipper = Path(opts['klipper'])
            validate_dependencies(klipper)
            managed(klipper / 'klippy/extras/driver_monitor.py', read(ROOT / 'backend/driver_monitor.py'), 'backend', klipper)
            main = Path(opts['config'])
            tree = scan_config(main, allow_missing=owned_config_paths(after_state, main))
            for name, data in tree['files'].items():
                reads[name] = sha(data)
            if not tree['lyx']:
                notes.append('No active [lyx9231 ...] section was found; cards appear only after compatible drivers are configured.')
            key = 'config'
            entry = after_state['entries'].get(key)
            current = source(main)
            block = markers(current, CFG_BEGIN, CFG_END)
            if entry:
                if entry['path'] != str(main) or (block is not None and block != entry['block']):
                    raise ValueError('Managed CFG include was changed; inspect before repair')
                # Existing owned file may be edited by user: updates preserve it.
                config_path = Path(entry['config_path'])
                data = source(config_path, True, main.parent)
                if data is None:
                    if tree['monitor']:
                        raise ValueError('A different [driver_monitor] exists; not restoring a duplicate managed section')
                    managed(config_path, DEFAULT_CONFIG, 'backend', main.parent)
                if block is None:
                    if tree['monitor']:
                        raise ValueError('A different [driver_monitor] exists; not adding a duplicate managed include')
                    change(main, current, swap_block(current, CFG_BEGIN, CFG_END, None, entry['block']), 'backend',
                           {'begin': CFG_BEGIN, 'end': CFG_END, 'before': None, 'after': entry['block']})
            elif not tree['monitor']:
                if block:
                    raise ValueError('Unowned CFG marker found; inspect legacy configuration manually')
                config_path = main.parent / CONFIG_NAME
                managed(config_path, DEFAULT_CONFIG, 'backend', main.parent)
                block = CFG_BEGIN + '\n[include ' + CONFIG_NAME + ']\n' + CFG_END
                # A wildcard include can already pick up the newly-created CFG.
                # Avoid adding an explicit duplicate: test the planned content
                # lexically against every active include pattern.
                for filename, data in tree['files'].items():
                    for match in re.finditer(r'^\s*\[include ([^\]]+)\]', data.decode(), re.M):
                        pattern = str(Path(filename).parent / match.group(1).strip())
                        if fnmatch.fnmatch(str(config_path), pattern):
                            raise ValueError('A wildcard include would already load driver-monitor.cfg; add [driver_monitor] to your own included CFG and retry')
                change(main, current, swap_block(current, CFG_BEGIN, CFG_END, None, block), 'backend',
                       {'begin': CFG_BEGIN, 'end': CFG_END, 'before': None, 'after': block})
                after_state['entries'][key] = {'path': str(main), 'config_path': str(config_path), 'block': block}
            else:
                notes.append('Existing [driver_monitor] retained: %s' % tree['monitor'][0])
            if 'backend' not in after_state['components']:
                after_state['components'].append('backend')
        if 'frontend' in comps:
            fluidd = Path(opts['fluidd'])
            index = fluidd / 'index.html'
            old_index = source(index, root=fluidd)
            existing_block = markers(old_index, installer.BEGIN, installer.END)
            owned = after_state['entries'].get('frontend')
            if existing_block and not owned:
                if not args.adopt_existing:
                    raise ValueError('Existing legacy monitor entry; use --adopt-existing to validate and take ownership')
                after_state['files'].update(legacy_frontend(fluidd, existing_block))
            elif owned and existing_block is not None and existing_block != owned['block']:
                raise ValueError('Managed Fluidd entry was changed; inspect before replacing')
            config = installer.target_config(opts['hostname'], opts['origin'], opts.get('api_url'))
            assets = installer.make_assets(config)
            version = installer.digest(b''.join(name.encode() + assets[name] for name in sorted(assets)))[:16]
            directory = 'driver-monitor-' + version
            for name, data in assets.items():
                managed(fluidd / directory / name, data, 'frontend', fluidd)
            patched = installer.patch_index(old_index, directory)
            new_block = markers(patched, installer.BEGIN, installer.END)
            change(index, old_index, patched, 'frontend',
                   {'begin': installer.BEGIN, 'end': installer.END, 'before': existing_block, 'after': new_block})
            after_state['entries']['frontend'] = {'path': str(index), 'block': new_block, 'asset_dir': directory}
            # User customization is never tracked as an owned file or removed.
            defaults = ROOT / 'frontend/customization'
            for name in ('layout.json', 'custom.css', 'custom-renderer.mjs.example'):
                template = defaults / name
                if not template.exists():
                    raise ValueError('Missing packaged customization template: %s' % template)
                target = fluidd / 'driver-monitor-user' / ('custom-renderer.js' if name.endswith('.mjs.example') else name)
                current = source(target, True, fluidd)
                reset = args.reset_layout and name in ('layout.json', 'custom.css')
                if current is None or reset:
                    change(target, current, read(template), 'customization')
            if 'frontend' not in after_state['components']:
                after_state['components'].append('frontend')
        after_state['status'] = 'installed'
    elif args.command == 'uninstall':
        # All preconditions are evaluated before any mutation in apply().
        if 'backend' in comps:
            main = Path(opts['config'])
            tree = scan_config(main, allow_missing=owned_config_paths(after_state, main))
            for name, data in tree['files'].items():
                reads[name] = sha(data)
            entry = after_state['entries'].get('config')
            if tree['monitor'] and (not entry or tree['monitor'] != [entry['config_path']]):
                raise ValueError('A user-owned [driver_monitor] remains active in %s. Remove that section manually before backend uninstall, or use --frontend-only.' % ', '.join(tree['monitor']))
            if entry:
                current = source(main)
                block = markers(current, CFG_BEGIN, CFG_END)
                if block is not None:
                    change(main, current, swap_block(current, CFG_BEGIN, CFG_END, entry['block'], None), 'backend',
                           {'begin': CFG_BEGIN, 'end': CFG_END, 'before': entry['block'], 'after': None})
                elif tree['monitor']:
                    raise ValueError('An unowned include still loads the monitor; remove it before uninstall')
                after_state['entries'].pop('config')
        if 'frontend' in comps:
            entry = after_state['entries'].get('frontend')
            if entry:
                index = Path(entry['path'])
                current = source(index, root=Path(opts['fluidd']))
                block = markers(current, installer.BEGIN, installer.END)
                if block is not None:
                    change(index, current, swap_block(current, installer.BEGIN, installer.END, entry['block'], None), 'frontend',
                           {'begin': installer.BEGIN, 'end': installer.END, 'before': entry['block'], 'after': None})
                after_state['entries'].pop('frontend')
        for path, record in list(after_state['files'].items()):
            if record['component'] not in comps:
                continue
            root = Path(opts['fluidd']) if record['component'] == 'frontend' else (
                Path(opts['config']).parent if Path(path).suffix == '.cfg' else Path(opts['klipper']))
            current = source(Path(path), True, root)
            if current is not None and sha(current) != record['sha256']:
                raise ValueError('Unknown local edit; not deleting %s. Preserve/resolve it before uninstall.' % path)
            change(Path(path), current, None, record['component'])
            del after_state['files'][path]
        after_state['components'] = [c for c in after_state['components'] if c not in comps]
        after_state['status'] = 'installed' if after_state['components'] else 'uninstalled'
        if 'backend' in comps:
            notes.append('Full backend removal stops this component\'s alarm protection after Klipper reload.')
    else:
        raise ValueError('Unsupported planning command')
    return {'command': args.command, 'changes': changes, 'state_before': state, 'state_after': after_state,
            'reads': reads, 'state_dir': str(state_dir), 'notes': notes,
            'restart_required': any(item['component'] == 'backend' for item in changes)}


def verify_reads(reads):
    for path, expected in reads.items():
        if sha(read(Path(path), optional=True)) != expected:
            raise ValueError('File changed since planning; no writes performed: %s' % path)


def record_root(path, component, opts):
    if component in ('frontend', 'customization'):
        return Path(opts['fluidd'])
    if path == Path(opts['config']) or path == Path(opts['config']).parent / CONFIG_NAME:
        return Path(opts['config']).parent
    return Path(opts['klipper'])


def verify_destinations(plan):
    opts = plan['state_after']['options']
    for item in plan['changes']:
        path = Path(item['path'])
        checked_path(path, record_root(path, item['component'], opts))


def write_file(path, data, metadata=None):
    if data is None:
        if path.exists():
            path.unlink()
    else:
        installer.atomic_write(path, data, metadata)


def apply(plan):
    directory = Path(plan['state_dir'])
    verify_destinations(plan)
    verify_reads(plan['reads'])
    if load_state(directory) != plan['state_before']:
        raise ValueError('Lifecycle manifest changed since planning')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('Refusing symlink state directory')
    lock = directory / 'lock'
    checked_path(lock, directory)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_destinations(plan)
        verify_reads(plan['reads'])
        if load_state(directory) != plan['state_before']:
            raise ValueError('Lifecycle manifest changed since planning')
        changes = plan['changes']
        comparable = copy.deepcopy(plan['state_after'])
        if not changes and comparable == plan['state_before']:
            return {'status': 'unchanged', 'restart_required': False, 'notes': plan['notes']}
        txid = time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        history = directory / 'history' / txid
        checked_path(history, directory)
        history.mkdir(parents=True, mode=0o700)
        receipt = {'format': 2, 'id': txid, 'command': plan['command'], 'status': 'applying',
                   'state_before': plan['state_before'], 'state_after': comparable, 'files': [],
                   'restart_required': plan['restart_required']}
        for i, item in enumerate(changes):
            path = Path(item['path'])
            metadata = None
            if item['before'] is not None:
                st = path.stat()
                metadata = {'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid}
                (history / ('%03d.before' % i)).write_bytes(item['before'])
            record = {k: v for k, v in item.items() if k not in ('before', 'after')}
            record.update(before_sha256=sha(item['before']), after_sha256=sha(item['after']),
                          backup='%03d.before' % i, metadata=metadata, attempted=False)
            receipt['files'].append(record)
        receipt_path = history / 'receipt.json'
        installer.atomic_write(receipt_path, serial(receipt))
        # Pending pointer survives a crash before the final manifest is saved.
        pending = directory / 'pending.json'
        installer.atomic_write(pending, serial({'receipt': str(receipt_path)}))
        try:
            for item, record in zip(changes, receipt['files']):
                checked_path(Path(item['path']), record_root(Path(item['path']), item['component'], comparable['options']))
                if sha(read(Path(item['path']), True)) != sha(item['before']):
                    raise ValueError('Concurrent file edit: %s' % item['path'])
                record['attempted'] = True
                installer.atomic_write(receipt_path, serial(receipt))
                write_file(Path(item['path']), item['after'], record['metadata'])
            comparable['last_transaction'] = str(receipt_path)
            installer.atomic_write(directory / 'manifest.json', serial(comparable))
            receipt['status'] = 'complete'
            receipt['state_after'] = comparable
            installer.atomic_write(receipt_path, serial(receipt))
            pending.unlink()
        except Exception:
            receipt['status'] = 'incomplete; run rollback with this state directory'
            installer.atomic_write(receipt_path, serial(receipt))
            raise
        return {'status': 'installed' if comparable['components'] else 'uninstalled', 'files': len(changes),
                'state': str(directory / 'manifest.json'), 'receipt': str(receipt_path),
                'restart_required': plan['restart_required'], 'runtime_verified': False,
                'tools_dir': str(ROOT),
                'notes': plan['notes']}


def rollback_plan(directory):
    state = load_state(directory)
    pending = read(directory / 'pending.json', True, directory)
    receipt_name = json.loads(pending)['receipt'] if pending else state.get('last_transaction') if state else None
    if not receipt_name:
        raise ValueError('No transaction available for rollback')
    receipt_path = checked_path(Path(receipt_name), directory)
    receipt = json.loads(read(receipt_path, root=directory))
    if receipt.get('format') != 2 or receipt.get('status') == 'rolled_back':
        raise ValueError('Unsupported or already rolled-back transaction')
    changes, reads = [], {str(receipt_path): sha(read(receipt_path, root=directory))}
    for record in reversed(receipt['files']):
        if not record['attempted']:
            continue
        path = checked_path(Path(record['path']), record_root(Path(record['path']), record['component'], receipt['state_after']['options']))
        current = read(path, True)
        reads[str(path)] = sha(current)
        if record['component'] == 'customization' and record['before_sha256'] is None:
            # Once created, customization belongs to the user, including when
            # rolling back the initial installation.
            continue
        if sha(current) == record['before_sha256']:
            continue
        if record.get('embedded') and current is not None:
            info = record['embedded']
            if markers(current, info['begin'], info['end']) == info['before']:
                # A previous rollback attempt may already have restored only
                # our block while preserving unrelated edits around it.
                continue
        data = None
        if record['before_sha256'] is not None:
            if Path(record['backup']).name != record['backup']:
                raise ValueError('Invalid backup path')
            data = read(receipt_path.parent / record['backup'], root=directory)
            if sha(data) != record['before_sha256']:
                raise ValueError('Backup hash mismatch')
        if record.get('embedded') and sha(current) != record['after_sha256']:
            info = record['embedded']
            if current is None:
                raise ValueError('The containing CFG/index is missing; restore it before rollback')
            data = swap_block(current, info['begin'], info['end'], info['after'], info['before'])
        elif sha(current) != record['after_sha256']:
            raise ValueError('Unknown edit; rollback will not overwrite %s' % path)
        changes.append({'path': str(path), 'before': current, 'after': data, 'component': record['component'],
                        'metadata': record.get('metadata')})
    return {'receipt_path': receipt_path, 'receipt': receipt, 'changes': changes, 'reads': reads,
            'state_before': state, 'state_dir': str(directory)}


def apply_rollback(plan):
    directory = Path(plan['state_dir'])
    verify_reads(plan['reads'])
    if load_state(directory) != plan['state_before']:
        raise ValueError('Lifecycle state changed during rollback planning')
    with (directory / 'lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_reads(plan['reads'])
        if load_state(directory) != plan['state_before']:
            raise ValueError('Lifecycle state changed during rollback planning')
        for item in plan['changes']:
            checked_path(Path(item['path']), record_root(Path(item['path']), item['component'], plan['receipt']['state_after']['options']))
            write_file(Path(item['path']), item['after'], item.get('metadata'))
        before = plan['receipt']['state_before']
        manifest = directory / 'manifest.json'
        if before is None:
            if manifest.exists():
                manifest.unlink()
        else:
            installer.atomic_write(manifest, serial(before))
        plan['receipt']['status'] = 'rolled_back'
        installer.atomic_write(plan['receipt_path'], serial(plan['receipt']))
        pending = directory / 'pending.json'
        if pending.exists():
            pending.unlink()
    return {'status': 'rolled_back', 'files': len(plan['changes']),
            'restart_required': plan['receipt']['restart_required'], 'runtime_verified': False}


def runtime_status(url):
    result = {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for name, path in (('printer_info', '/printer/info'), ('monitor', '/printer/objects/query?driver_monitor=ready,drivers,auto_enabled,shutdown_on_alarm,stats,last_result')):
        try:
            with opener.open(url + path, timeout=3) as response:
                result[name] = json.load(response).get('result', {})
        except (OSError, ValueError, urllib.error.URLError) as error:
            result[name] = {'status': 'pending', 'error': str(error)}
    return result


def doctor(args, directory):
    state = load_state(directory)
    if not state:
        return {'status': 'not_installed', 'state_dir': str(directory),
                'note': 'Legacy installs need install --adopt-existing after review'}
    findings = []
    for name, record in state['files'].items():
        try:
            data = read(Path(name), True)
            status = 'ok' if sha(data) == record['sha256'] else 'missing' if data is None else 'modified'
        except (OSError, ValueError) as error:
            status = str(error)
        if status != 'ok':
            findings.append({'path': name, 'status': status})
    for name, entry in state['entries'].items():
        begin, end = (CFG_BEGIN, CFG_END) if name == 'config' else (installer.BEGIN, installer.END)
        try:
            data = read(Path(entry['path']))
            if markers(data, begin, end) != entry['block']:
                findings.append({'path': entry['path'], 'status': 'entry_missing_or_modified'})
        except (ValueError, OSError) as error:
            findings.append({'path': entry['path'], 'status': str(error)})
    runtime = runtime_status(options(args, state)['moonraker_url'])
    status = runtime.get('monitor', {}).get('status', {})
    loaded = isinstance(status, dict) and isinstance(status.get('driver_monitor'), dict)
    expected_host = state['options'].get('hostname')
    actual_host = runtime.get('printer_info', {}).get('hostname')
    if expected_host and actual_host and actual_host != expected_host:
        findings.append({'status': 'wrong_moonraker_instance', 'expected_hostname': expected_host,
                         'actual_hostname': actual_host})
        loaded = False
    return {'status': 'needs_attention' if findings else 'files_ok', 'findings': findings,
            'components': state['components'], 'runtime': runtime, 'monitor_loaded': loaded,
            'activation': 'loaded' if loaded else 'pending_reload_or_connection',
            'firmware_verified': False}


def public_plan(plan):
    return {'status': 'plan', 'files': [{'path': item['path'], 'action': 'delete' if item['after'] is None else
             'create' if item['before'] is None else 'replace', 'component': item['component']} for item in plan['changes']],
            'restart_required': plan.get('restart_required', plan.get('receipt', {}).get('restart_required', False)),
            'notes': plan.get('notes', [])}


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'update', 'uninstall', 'doctor', 'repair', 'rollback'))
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--frontend-only', action='store_true')
    group.add_argument('--backend-only', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--yes', action='store_true', help='Apply the printed plan without an interactive confirmation')
    parser.add_argument('--state-dir', default=str(DEFAULT_STATE))
    for name in ('klipper', 'fluidd', 'config', 'hostname', 'moonraker-url'):
        parser.add_argument('--' + name)
    parser.add_argument('--origin', action='append')
    parser.add_argument('--api-url', action='append')
    parser.add_argument('--adopt-existing', action='store_true', help='Adopt identical backend files and checksum-verified legacy assets')
    parser.add_argument('--reset-layout', action='store_true', help='Reset layout.json and custom.css; custom renderer is retained')
    return parser


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        directory = checked_path(args.state_dir)
        if args.command == 'doctor':
            print(json.dumps(doctor(args, directory), ensure_ascii=False, indent=2))
            return 0
        if args.command != 'rollback' and read(directory / 'pending.json', True, directory):
            raise ValueError('An unfinished transaction exists; run rollback before another operation')
        if args.command == 'rollback':
            plan = rollback_plan(directory)
        else:
            plan = new_plan(args, directory, interactive=sys.stdin.isatty())
        print(json.dumps(public_plan(plan), ensure_ascii=False, indent=2))
        if args.plan:
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError('Use --plan to preview or --yes to apply in a non-interactive terminal')
            if input('Apply this plan? Type yes: ').strip() != 'yes':
                print('Cancelled; nothing changed.')
                return 0
        result = apply_rollback(plan) if args.command == 'rollback' else apply(plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('restart_required'):
            print('Files saved; runtime activation is pending. Inspect local service hooks, then reload Klipper when idle. No restart was performed.')
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        cli.exit(2, 'Error: %s\n' % error)


if __name__ == '__main__':
    sys.exit(main())
