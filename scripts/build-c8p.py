#!/usr/bin/env python3
"""Build FLY C8 Pro H723 firmware in a new, isolated source copy.

Python 3.8+, standard library only. This script never flashes an MCU, changes
services or installs dependencies. Supplied Klipper sources are trusted build
code; isolation protects existing files, it is not an execution sandbox.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHA = '895b6dcba659e4585097de958d5d90db51c4862ba1d24013334809b1a1f7526c'
PATCHED_SHA = '80eb1d99f7553c4f95bb9404af28e402f1c659da84ce292ec85cd045a49cb818'
PATCH_SHA = 'bd508336c32d3f853336bbd581cff88299bada0c75504e98ac03f9dab57632f0'
SOURCE_ROOTS = ('Makefile', 'COPYING', 'src', 'lib', 'scripts', 'klippy')
REQUIRED = ('Makefile', 'COPYING', 'src/Kconfig', 'src/Makefile',
            'src/stm32/Kconfig', 'src/stm32/Makefile', 'src/tmcuart.c',
            'src/spicmds.c', 'scripts/buildcommands.py',
            'lib/kconfiglib/olddefconfig.py', 'lib/kconfiglib/genconfig.py')
IGNORED_SUFFIXES = ('.cfg', '.log', '.pem', '.key', '.sqlite', '.db', '.pyc')
MODBUS_KCONFIG = ('config WANT_MODBUSUART\n    bool\n'
                  '    depends on HAVE_GPIO\n    default y\n',
                  'config WANT_MODBUSUART\n'
                  '    bool "Support Modbus stepper motor driver UART communication"\n'
                  '    depends on HAVE_GPIO\n')
TMC_KCONFIG = ('config WANT_TMCUART\n    bool\n'
               '    depends on HAVE_GPIO\n    default y\n',
               'config WANT_TMCUART\n'
               '    bool "Support Trinamic stepper motor driver UART communication"\n'
               '    depends on HAVE_GPIO\n')
MODBUS_MAKE = 'src-$(CONFIG_WANT_MODBUSUART) += modbus_uart.c'
TMC_MAKE = 'src-$(CONFIG_WANT_TMCUART) += tmcuart.c'
COMMANDS = (
    'config_modbus_uart oid=%c rx_pin=%u pull_up=%c tx_pin=%u bit_time=%u',
    'modbus_uart_send oid=%c write=%*s read=%c',
    'config_tmcuart oid=%c rx_pin=%u pull_up=%c tx_pin=%u bit_time=%u',
    'tmcuart_send oid=%c write=%*s read=%c',
    'config_spi oid=%c pin=%u cs_active_high=%c',
    'spi_set_bus oid=%c spi_bus=%u mode=%u rate=%u',
    'spi_send oid=%c data=%*s', 'spi_transfer oid=%c data=%*s')
RESPONSES = ('modbus_uart_response oid=%c read=%*s',
             'tmcuart_response oid=%c read=%*s',
             'spi_transfer_response oid=%c response=%*s')
FLASH_START = 0x08020000


class BuildError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    return sha256(Path(path).read_bytes())


def _mode(mode):
    if mode not in ('usb', 'canbridge'):
        raise BuildError('Unknown build mode: ' + str(mode))


def _no_links(path):
    """Reject symlinks in existing path components, including parent aliases."""
    path = Path(os.path.abspath(str(path)))
    for part in (path,) + tuple(path.parents):
        if part.is_symlink():
            raise BuildError('Symlink path is not accepted: ' + str(part))
    return path


def _safe_make_path(value):
    # Klipper's Makefiles interpolate paths in shell recipes without quoting.
    if re.search(r'[\s$`\\;\'"<>&|(){}*!?#:]', str(value)):
        raise BuildError('Build/tool path contains shell or make metacharacters: '
                         + str(value))


def source_inventory(source):
    """Only copy known build source roots, never .git, out or printer configs."""
    source = _no_links(source)
    if not source.is_dir():
        raise BuildError('Source directory does not exist: ' + str(source))
    files = {}
    for root_name in SOURCE_ROOTS:
        base = source / root_name
        if not base.exists() and not base.is_symlink():
            raise BuildError('Missing source root: ' + root_name)
        pending = [base]
        while pending:
            path = pending.pop()
            rel = path.relative_to(source)
            if path.is_symlink():
                raise BuildError('Source symlink is not accepted: ' + str(rel))
            kind = path.lstat().st_mode
            if stat.S_ISDIR(kind):
                if path.name.startswith('.') or path.name == '__pycache__':
                    continue
                pending.extend(sorted(path.iterdir(), reverse=True))
            elif stat.S_ISREG(kind):
                if path.name.startswith('.') or path.name.endswith(IGNORED_SUFFIXES):
                    continue
                files[rel.as_posix()] = file_sha(path)
            else:
                raise BuildError('Source special file is not accepted: ' + str(rel))
    for name in REQUIRED:
        if name not in files:
            raise BuildError('Incomplete Klipper source: missing ' + name)
    return dict(sorted(files.items()))


def _apply_patch(original, patch):
    """Apply the bundled exact-context unified diff, without fuzzy matching."""
    old = original.decode('utf-8').splitlines(keepends=True)
    lines = patch.decode('utf-8').splitlines(keepends=True)
    if lines[:2] != ['--- a/src/modbus_uart.c\n', '+++ b/src/modbus_uart.c\n']:
        raise BuildError('Unexpected bundled patch headers')
    output, cursor, pos = [], 0, 2
    while pos < len(lines):
        match = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n', lines[pos])
        if not match:
            raise BuildError('Unexpected bundled patch hunk')
        start, old_count, new_start, new_count = map(int, match.groups())
        start -= 1
        if start < cursor:
            raise BuildError('Overlapping patch hunks')
        output.extend(old[cursor:start])
        cursor, removed, added = start, 0, 0
        pos += 1
        while pos < len(lines) and not lines[pos].startswith('@@ '):
            line = lines[pos]
            if line[0] in ' -':
                if cursor >= len(old) or old[cursor] != line[1:]:
                    raise BuildError('Bundled patch does not match source')
                cursor += 1
                removed += 1
            if line[0] in ' +':
                output.append(line[1:])
                added += 1
            if line[0] not in ' +-':
                raise BuildError('Unexpected patch line')
            pos += 1
        if (removed, added) != (old_count, new_count):
            raise BuildError('Invalid patch hunk lengths')
    output.extend(old[cursor:])
    return ''.join(output).encode('utf-8')


def integrate_modbus(tree):
    tree = Path(tree)
    bundled = (ROOT / 'firmware/modbus_uart.c').read_bytes()
    patch = (ROOT / 'firmware/modbus_uart-r3.patch').read_bytes()
    if sha256(bundled) != ORIGINAL_SHA or sha256(patch) != PATCH_SHA:
        raise BuildError('Bundled Modbus source/patch checksum mismatch')
    result = _apply_patch(bundled, patch)
    if sha256(result) != PATCHED_SHA:
        raise BuildError('Patched Modbus checksum mismatch')
    target = tree / 'src/modbus_uart.c'
    before = file_sha(target) if target.exists() else None
    if before not in (None, ORIGINAL_SHA, PATCHED_SHA):
        raise BuildError('Unknown existing src/modbus_uart.c; refusing to overwrite')
    kpath = tree / 'src/Kconfig'
    kconfig = kpath.read_text()
    definitions = re.findall(r'^config WANT_MODBUSUART\n(?:[ \t].*\n|\n)*',
                             kconfig, flags=re.M)
    if definitions:
        # The fixed author commit and the verified FLYOS port differ only
        # in this visible menu label; accept these two exact definitions.
        known_menu = {MODBUS_KCONFIG[1].rstrip(), MODBUS_KCONFIG[1].replace(
            'Modbus stepper motor driver UART',
            'Generic software Modbus RTU UART').rstrip()}
        if (len(definitions) != 2
                or definitions[0].rstrip() != MODBUS_KCONFIG[0].rstrip()
                or definitions[1].rstrip() not in known_menu):
            raise BuildError('Unknown/partial WANT_MODBUSUART Kconfig definitions')
    else:
        for anchor, addition in zip(TMC_KCONFIG, MODBUS_KCONFIG):
            if kconfig.count(anchor) != 1:
                raise BuildError('Cannot identify both standard TMC Kconfig anchors')
            kconfig = kconfig.replace(anchor, anchor + addition, 1)
    # Do not accept other uses of the symbol, e.g. a hidden select/override.
    if kconfig.count('WANT_MODBUSUART') != 2:
        raise BuildError('Unexpected additional WANT_MODBUSUART Kconfig use')
    mpath = tree / 'src/Makefile'
    makefile = mpath.read_text()
    mentions = [line for line in makefile.splitlines()
                if 'modbus_uart' in line or 'WANT_MODBUSUART' in line]
    if mentions and mentions != [MODBUS_MAKE]:
        raise BuildError('Unknown/duplicate Modbus Makefile integration')
    if not mentions:
        if makefile.splitlines().count(TMC_MAKE) != 1:
            raise BuildError('Cannot identify standard TMC Makefile anchor')
        if not makefile.endswith('\n'):
            makefile += '\n'
        makefile = makefile.replace(TMC_MAKE + '\n',
                                    TMC_MAKE + '\n' + MODBUS_MAKE + '\n', 1)
    target.write_bytes(result)
    kpath.write_text(kconfig)
    mpath.write_text(makefile)
    return {'previous_sha256': before, 'result_sha256': PATCHED_SHA,
            'patch_sha256': PATCH_SHA}


def patch_version(tree):
    tree = Path(tree)
    path = tree / 'Makefile'
    content = path.read_text()
    lines = content.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines)
            if './scripts/buildcommands.py -d $(OUT)klipper.dict' in line]
    if len(hits) != 1:
        raise BuildError('Cannot identify the single buildcommands recipe')
    i = hits[0]
    base = '\t$(Q)$(PYTHON) ./scripts/buildcommands.py -d $(OUT)klipper.dict'
    tail = (' -t "$(CC);$(AS);$(LD);$(OBJCOPY);$(OBJDUMP);$(STRIP)"'
            ' $(OUT)compile_time_request.txt $(OUT)compile_time_request.c\n')
    expected = base + ' -e "$(EXTRAVERSION)"' + tail
    if lines[i] == base + tail:
        lines[i] = expected
    elif lines[i] != expected:
        raise BuildError('Unknown buildcommands version recipe; refusing to replace')
    helper = (tree / 'scripts/buildcommands.py').read_text()
    if not re.search(r'add_option\([\'\"]-e[\'\"]', helper):
        raise BuildError('buildcommands.py does not support the -e version argument')
    path.write_text(''.join(lines))


def parse_config(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r'(CONFIG_[A-Z0-9_]+)=(.*)', line)
        if match:
            if match[1] in result:
                raise BuildError('Duplicate configuration key: ' + match[1])
            result[match[1]] = match[2]
        else:
            match = re.fullmatch(r'# (CONFIG_[A-Z0-9_]+) is not set', line)
            if match:
                if match[1] in result:
                    raise BuildError('Duplicate configuration key: ' + match[1])
                result[match[1]] = 'n'
    return result


def validate_config(config, mode):
    _mode(mode)
    required = {'CONFIG_LOW_LEVEL_OPTIONS': 'y', 'CONFIG_MACH_STM32': 'y',
                'CONFIG_MACH_STM32H723': 'y', 'CONFIG_MCU': '"stm32h723xx"',
                'CONFIG_STM32_FLASH_START_20000': 'y',
                'CONFIG_STM32_CLOCK_REF_25M': 'y', 'CONFIG_WANT_MODBUSUART': 'y',
                'CONFIG_WANT_TMCUART': 'y', 'CONFIG_WANT_SPI': 'y',
                'CONFIG_WANT_SOFTWARE_SPI': 'y', 'CONFIG_USB': 'y',
                'CONFIG_INITIAL_PINS': '""'}
    if mode == 'usb':
        required['CONFIG_STM32_USB_PA11_PA12'] = 'y'
        for key in ('CONFIG_USBCANBUS', 'CONFIG_CANBUS',
                    'CONFIG_STM32_USBCANBUS_PA11_PA12'):
            if config.get(key, 'n') != 'n':
                raise BuildError('Unexpected CAN option in USB mode: ' + key)
    else:
        required.update({'CONFIG_STM32_USBCANBUS_PA11_PA12': 'y',
                         'CONFIG_STM32_CMENU_CANBUS_PB8_PB9': 'y',
                         'CONFIG_STM32_CANBUS_PB8_PB9': 'y',
                         'CONFIG_USBCANBUS': 'y', 'CONFIG_CANBUS': 'y',
                         'CONFIG_CANBUS_FREQUENCY': '1000000'})
        if config.get('CONFIG_STM32_USB_PA11_PA12', 'n') != 'n':
            raise BuildError('USB serial option enabled in CAN bridge mode')
    for key, value in required.items():
        if config.get(key) != value:
            raise BuildError('Resolved config mismatch: {} must equal {}'.format(key, value))
    for key, value in (('CONFIG_FLASH_APPLICATION_ADDRESS', FLASH_START),
                       ('CONFIG_FLASH_BOOT_ADDRESS', 0x08000000),
                       ('CONFIG_CLOCK_REF_FREQ', 25000000)):
        try:
            actual = int(config[key], 0)
        except (KeyError, ValueError):
            raise BuildError('Missing/invalid resolved configuration: ' + key)
        if actual != value:
            raise BuildError('Unexpected resolved value for ' + key)
    if config.get('CONFIG_STARTUP_PIN_STATE', '0') != '0':
        raise BuildError('Startup pin state must be unset/zero')


def prepare_source(source, work_dir, mode):
    _mode(mode)
    source, work_dir = _no_links(source), _no_links(work_dir)
    _safe_make_path(work_dir)
    if (source == work_dir or source in work_dir.parents
            or work_dir in source.parents):
        raise BuildError('Source and work directory may not overlap')
    if work_dir.exists():
        raise BuildError('Work directory already exists; choose a new path')
    inventory = source_inventory(source)
    work_dir.mkdir(parents=True, exist_ok=False)
    tree = work_dir / 'source'
    tree.mkdir()
    (work_dir / 'artifacts').mkdir()
    for name, expected in inventory.items():
        origin, dest = source / name, tree / name
        _no_links(origin)
        if not origin.is_file() or file_sha(origin) != expected:
            raise BuildError('Source changed before copying: ' + name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, dest)
        dest.chmod(origin.stat().st_mode & 0o777)
        if file_sha(dest) != expected:
            raise BuildError('Source changed during copying: ' + name)
    if source_inventory(source) != inventory:
        raise BuildError('Source changed during snapshot; retry with stable sources')
    integration = integrate_modbus(tree)
    patch_version(tree)
    seed = (ROOT / ('firmware/c8p-' + mode + '.config')).read_text()
    # FLYOS adds startup-pin selections absent in upstream. Keep this inert.
    if 'config STARTUP_PIN_NOT_SET' in (tree / 'src/Kconfig').read_text():
        seed += 'CONFIG_STARTUP_PIN_NOT_SET=y\n'
    (tree / '.config').write_text(seed)
    manifest = {
        'schema_version': 1, 'status': 'prepared-not-built', 'mode': mode,
        'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source_path': str(source), 'work_dir': str(work_dir),
        'source_files_sha256': inventory,
        'source_snapshot_sha256': sha256(json.dumps(inventory, sort_keys=True).encode()),
        'modbus': integration, 'seed_sha256': sha256(seed.encode()),
        'bootloader_offset': 131072, 'crystal_hz': 25000000,
        'can_bitrate': 1000000 if mode == 'canbridge' else None,
        'flashed': False, 'device_tested': False,
        'note': 'Build support only. Host LYX modules and matching CFG are required.'}
    _write_manifest(work_dir, manifest)
    return manifest


def _write_manifest(work_dir, manifest):
    (Path(work_dir) / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n')


def validate_artifacts(tree, mode):
    tree = Path(tree)
    config = parse_config((tree / '.config').read_text())
    validate_config(config, mode)
    output = tree / 'out'
    for filename in ('klipper.bin', 'klipper.elf', 'klipper.dict'):
        if not (output / filename).is_file() or (output / filename).is_symlink():
            raise BuildError('Missing/invalid artifact: ' + filename)
    try:
        dictionary = json.loads((output / 'klipper.dict').read_text())
    except (ValueError, UnicodeError) as exc:
        raise BuildError('Invalid protocol dictionary: ' + str(exc))
    if not isinstance(dictionary, dict):
        raise BuildError('Protocol dictionary root must be an object')
    for group, expected in (('commands', COMMANDS), ('responses', RESPONSES)):
        if not isinstance(dictionary.get(group), dict):
            raise BuildError('Invalid protocol dictionary group: ' + group)
        for command in expected:
            if command not in dictionary[group]:
                raise BuildError('Missing exact firmware protocol: ' + command)
    dconfig = dictionary.get('config', {})
    if not isinstance(dconfig, dict):
        raise BuildError('Protocol dictionary config must be an object')
    if dconfig.get('MCU') != 'stm32h723xx' or dconfig.get('RESERVE_PINS_USB') != 'PA11,PA12':
        raise BuildError('Protocol dictionary MCU/USB pin mismatch')
    if mode == 'canbridge':
        if dconfig.get('CANBUS_BRIDGE') != 1 or dconfig.get('RESERVE_PINS_CAN') != 'PB8,PB9':
            raise BuildError('Protocol dictionary CAN bridge/pin mismatch')
    elif dconfig.get('CANBUS_BRIDGE') or dconfig.get('RESERVE_PINS_CAN'):
        raise BuildError('Unexpected CAN bridge in USB protocol dictionary')
    binary = (output / 'klipper.bin').read_bytes()
    try:
        flash_end = int(config['CONFIG_FLASH_BOOT_ADDRESS'], 0) + int(config['CONFIG_FLASH_SIZE'], 0)
        ram_start, ram_size = (int(config[key], 0) for key in ('CONFIG_RAM_START', 'CONFIG_RAM_SIZE'))
    except (ValueError, KeyError):
        raise BuildError('Missing/invalid flash or RAM limits')
    if not 8 <= len(binary) <= flash_end - FLASH_START:
        raise BuildError('Binary exceeds configured application flash range')
    stack, reset = struct.unpack_from('<II', binary)
    if not ram_start < stack <= ram_start + ram_size or stack % 8:
        raise BuildError('Invalid initial stack vector')
    if not reset & 1 or not FLASH_START <= (reset & ~1) < FLASH_START + len(binary):
        raise BuildError('Invalid reset vector/application address')
    elf = (output / 'klipper.elf').read_bytes()
    if len(elf) < 52 or elf[:7] != b'\x7fELF\x01\x01\x01':
        raise BuildError('Expected ELF32 little-endian ARM firmware')
    header = struct.unpack_from('<HHIIIIIHHHHHH', elf, 16)
    elf_type, machine, version, entry, phoff = header[:5]
    phentsize, phnum = header[8:10]
    # Klipper's linker script can leave e_entry at the vector table base.
    # Cortex-M boot uses the separately verified reset vector, not e_entry.
    if (elf_type != 2 or machine != 40 or version != 1
            or entry not in (FLASH_START, reset)):
        raise BuildError('ELF architecture/entry does not match Klipper firmware')
    if phentsize != 32 or not phnum or phoff + phnum * phentsize > len(elf):
        raise BuildError('Invalid ELF program headers')
    segments = []
    for index in range(phnum):
        ptype, offset, vaddr, paddr, filesz, memsz, flags, align = struct.unpack_from(
            '<IIIIIIII', elf, phoff + index * phentsize)
        if ptype != 1 or not filesz:
            continue
        if (filesz > memsz or offset + filesz > len(elf)
                or paddr < FLASH_START or paddr + filesz > FLASH_START + len(binary)):
            raise BuildError('ELF load segment outside application binary')
        rel = paddr - FLASH_START
        if elf[offset:offset + filesz] != binary[rel:rel + filesz]:
            raise BuildError('ELF load data differs from binary')
        segments.append({'physical_address': paddr, 'size': filesz})
    if (not segments or min(x['physical_address'] for x in segments) != FLASH_START
            or max(x['physical_address'] + x['size'] for x in segments) != FLASH_START + len(binary)):
        raise BuildError('ELF/bin application size or base mismatch')
    return {'config': config, 'dictionary_version': dictionary.get('version'),
            'required_commands': list(COMMANDS), 'required_responses': list(RESPONSES),
            'build_versions': dictionary.get('build_versions'),
            'flash_start': FLASH_START, 'flash_end': flash_end,
            'initial_stack': stack, 'reset_vector': reset, 'binary_size': len(binary),
            'elf_load_segments': segments}


def reset_build_output(tree):
    """Discard only generated configuration output in the isolated tree.

    make olddefconfig may generate board-link while the seed has no derived
    BOARD_DIRECTORY. On filesystems/make versions with second-resolution
    mtimes, the resolved config may not make that stale link look older.
    """
    tree = _no_links(tree)
    output = tree / 'out'
    if output.is_symlink() or output.resolve().parent != tree:
        raise BuildError('Refusing to clean output outside the isolated tree')
    if output.exists():
        if not output.is_dir():
            raise BuildError('Generated output path is not a directory')
        shutil.rmtree(output)


def _run(command, cwd, log):
    env = os.environ.copy()
    for key in list(env):
        if key in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONPYCACHEPREFIX') or key.startswith('KCONFIG_'):
            env.pop(key)
    with Path(log).open('a') as stream:
        stream.write('$ ' + json.dumps(command) + '\n')
        stream.flush()
        subprocess.run(command, cwd=str(cwd), env=env, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('usb', 'canbridge'))
    parser.add_argument('--source', required=True, type=Path,
                        help='Complete trusted Klipper source tree; never modified')
    parser.add_argument('--work-dir', type=Path,
                        help='New isolated directory (default: temporary directory)')
    parser.add_argument('--prepare', action='store_true',
                        help='Copy/integrate source and seed only; no make or compilation')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 1, 4))
    parser.add_argument('--cross-prefix', default='arm-none-eabi-',
                        help='ARM compiler prefix, optionally an absolute path')
    args = parser.parse_args(argv)
    work_dir, manifest = None, None
    try:
        if not 1 <= args.jobs <= 256:
            raise BuildError('--jobs must be between 1 and 256')
        _safe_make_path(args.cross_prefix)
        _safe_make_path(sys.executable)
        if not args.prepare:
            missing = [tool for tool in ('make', 'readelf') + tuple(args.cross_prefix + suffix
                       for suffix in ('gcc', 'cpp', 'as', 'ld', 'objcopy', 'objdump', 'strip'))
                       if shutil.which(tool) is None]
            if missing:
                raise BuildError('Install build dependencies first; missing: ' + ', '.join(missing))
        work_dir = args.work_dir
        if work_dir is None:
            # Keep a unique parent; prepare_source must create the new child itself.
            parent = Path(tempfile.mkdtemp(prefix='c8p-' + args.mode + '-')).resolve()
            work_dir = parent / 'build'
        work_dir = Path(os.path.abspath(str(work_dir)))
        manifest = prepare_source(args.source, work_dir, args.mode)
        tree, log = work_dir / 'source', work_dir / 'build.log'
        log.write_text('Source prepared; no MCU has been flashed.\n')
        print('Work directory: ' + str(work_dir), flush=True)
        if args.prepare:
            print('prepared-not-built: configuration seed only; compilation NOT performed.')
            return 0
        version = '-c8p-lyx-r3-' + args.mode
        common = ['make', 'OUT=out/', 'KCONFIG_CONFIG=' + str(tree / '.config'),
                  'PYTHON=' + sys.executable,
                  'CROSS_PREFIX=' + args.cross_prefix,
                  'CPP=' + args.cross_prefix + 'cpp', 'EXTRAVERSION=' + version]
        _run(common + ['olddefconfig'], tree, log)
        config = parse_config((tree / '.config').read_text())
        validate_config(config, args.mode)
        reset_build_output(tree)
        _run(common + ['-j' + str(args.jobs), 'all'], tree, log)
        verified = validate_artifacts(tree, args.mode)
        if version not in str(verified['dictionary_version']):
            raise BuildError('Compiled dictionary lacks this build version suffix')
        if source_inventory(args.source) != manifest['source_files_sha256']:
            raise BuildError('Original source changed during build; provenance is unstable')
        artifacts = work_dir / 'artifacts'
        hashes = {}
        for name in ('klipper.bin', 'klipper.elf', 'klipper.dict'):
            shutil.copyfile(tree / 'out' / name, artifacts / name)
            hashes[name] = file_sha(artifacts / name)
        shutil.copyfile(tree / '.config', artifacts / '.config')
        hashes['.config'] = file_sha(artifacts / '.config')
        (artifacts / 'SHA256SUMS').write_text(''.join(
            digest + '  ' + name + '\n' for name, digest in sorted(hashes.items())))
        manifest.update(status='built-not-flashed', validation=verified,
                        artifact_sha256=hashes, version_suffix=version,
                        compiler_prefix=args.cross_prefix)
        _write_manifest(work_dir, manifest)
        print('built-not-flashed: verified artifacts in ' + str(artifacts))
        return 0
    except (BuildError, OSError, subprocess.CalledProcessError) as exc:
        if manifest is not None:
            manifest.update(status='failed-not-flashed', error=str(exc))
            _write_manifest(work_dir, manifest)
        print('ERROR: ' + str(exc), file=sys.stderr)
        if work_dir is not None:
            print('Inspect retained build directory: ' + str(work_dir), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
