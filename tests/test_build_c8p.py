"""Offline C8P build checks; no printer, compiler, network, or flash device used.

Temporary source trees and fake build outputs exercise validation and isolation.
They do not count as an ARM cross-compilation or hardware compatibility test.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('c8p_builder', ROOT / 'scripts/build-c8p.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildC8PTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'input-source'
        self.work = self.base / 'isolated-build'

    def fixture_source(self):
        for name in builder.SOURCE_ROOTS:
            if name not in ('Makefile', 'COPYING'):
                (self.source / name).mkdir(parents=True, exist_ok=True)
        for name in builder.REQUIRED:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# minimal trusted build fixture\n')
        recipe = ('\t$(Q)$(PYTHON) ./scripts/buildcommands.py -d $(OUT)klipper.dict'
                  ' -t "$(CC);$(AS);$(LD);$(OBJCOPY);$(OBJDUMP);$(STRIP)"'
                  ' $(OUT)compile_time_request.txt $(OUT)compile_time_request.c\n')
        (self.source / 'Makefile').write_text('all:\n' + recipe)
        (self.source / 'scripts/buildcommands.py').write_text("parser.add_option('-e')\n")
        (self.source / 'src/Kconfig').write_text('\n'.join(builder.TMC_KCONFIG))
        (self.source / 'src/Makefile').write_text(builder.TMC_MAKE + '\n')
        return self.source

    def resolved_config(self, mode='usb'):
        config = builder.parse_config((ROOT / ('firmware/c8p-%s.config' % mode)).read_text())
        config.update(CONFIG_MCU='"stm32h723xx"', CONFIG_USB='y',
                      CONFIG_FLASH_APPLICATION_ADDRESS='0x8020000',
                      CONFIG_FLASH_BOOT_ADDRESS='0x8000000', CONFIG_FLASH_SIZE='0x40000',
                      CONFIG_CLOCK_REF_FREQ='25000000', CONFIG_RAM_START='0x20000000',
                      CONFIG_RAM_SIZE='0x20000')
        if mode == 'canbridge':
            config.update(CONFIG_CANBUS='y', CONFIG_USBCANBUS='y',
                          CONFIG_STM32_CANBUS_PB8_PB9='y')
        return config

    def write_config(self, tree, mode='usb'):
        config = self.resolved_config(mode)
        (tree / '.config').write_text(''.join(key + '=' + value + '\n'
                                             for key, value in config.items()))

    def fake_outputs(self, tree, mode='usb'):
        """Minimal internally consistent ELF/bin/dictionary, never executable here."""
        self.write_config(tree, mode)
        output = tree / 'out'
        output.mkdir(exist_ok=True)
        binary = struct.pack('<II', 0x20020000, 0x08020009) + bytes(24)
        (output / 'klipper.bin').write_bytes(binary)
        elf = b'\x7fELF\x01\x01\x01' + bytes(9)
        elf += struct.pack('<HHIIIIIHHHHHH', 2, 40, 1, 0x08020009,
                           52, 0, 0, 52, 32, 1, 0, 0, 0)
        elf += struct.pack('<IIIIIIII', 1, 84, 0x08020000, 0x08020000,
                           len(binary), len(binary), 5, 4)
        (output / 'klipper.elf').write_bytes(elf + binary)
        dconfig = {'MCU': 'stm32h723xx', 'RESERVE_PINS_USB': 'PA11,PA12'}
        if mode == 'canbridge':
            dconfig.update(CANBUS_BRIDGE=1, RESERVE_PINS_CAN='PB8,PB9')
        dictionary = {'version': 'test-c8p-lyx-r3-' + mode,
                      'commands': {key: index for index, key in enumerate(builder.COMMANDS)},
                      'responses': {key: index for index, key in enumerate(builder.RESPONSES)},
                      'config': dconfig}
        (output / 'klipper.dict').write_text(json.dumps(dictionary))
        return dictionary

    def test_bundled_source_and_exact_r3_patch_match_provenance(self):
        provenance = json.loads((ROOT / 'firmware/PROVENANCE.json').read_text())
        original = (ROOT / 'firmware/modbus_uart.c').read_bytes()
        self.assertEqual(provenance['original_sha256'], hashlib.sha256(original).hexdigest())
        patched = builder._apply_patch(original, (ROOT / 'firmware/modbus_uart-r3.patch').read_bytes())
        self.assertEqual(provenance['patched_sha256'], hashlib.sha256(patched).hexdigest())

    def test_profiles_keep_driver_interfaces_enabled(self):
        for mode in ('usb', 'canbridge'):
            with self.subTest(mode=mode):
                config = builder.parse_config((ROOT / ('firmware/c8p-%s.config' % mode)).read_text())
                for key in ('CONFIG_MACH_STM32H723', 'CONFIG_STM32_FLASH_START_20000',
                            'CONFIG_STM32_CLOCK_REF_25M', 'CONFIG_WANT_MODBUSUART',
                            'CONFIG_WANT_TMCUART', 'CONFIG_WANT_SPI',
                            'CONFIG_WANT_SOFTWARE_SPI'):
                    self.assertEqual('y', config[key])
                self.assertEqual('""', config['CONFIG_INITIAL_PINS'])

    def test_profiles_select_distinct_host_transports(self):
        usb = builder.parse_config((ROOT / 'firmware/c8p-usb.config').read_text())
        can = builder.parse_config((ROOT / 'firmware/c8p-canbridge.config').read_text())
        self.assertEqual('y', usb['CONFIG_STM32_USB_PA11_PA12'])
        self.assertNotEqual('y', usb.get('CONFIG_STM32_USBCANBUS_PA11_PA12'))
        self.assertEqual('y', can['CONFIG_STM32_USBCANBUS_PA11_PA12'])
        self.assertEqual('y', can['CONFIG_STM32_CMENU_CANBUS_PB8_PB9'])
        self.assertEqual('1000000', can['CONFIG_CANBUS_FREQUENCY'])
        self.assertNotEqual('y', can.get('CONFIG_STM32_USB_PA11_PA12'))

    def test_parse_config_refuses_conflicting_assignments(self):
        for text in ('CONFIG_USB=y\nCONFIG_USB=n\n',
                     'CONFIG_USB=y\n# CONFIG_USB is not set\n'):
            with self.subTest(text=text), self.assertRaises(builder.BuildError):
                builder.parse_config(text)

    def test_resolved_config_rejects_wrong_board_clock_offset_or_interfaces(self):
        for key, value in (('CONFIG_MCU', '"stm32h743xx"'),
                           ('CONFIG_FLASH_APPLICATION_ADDRESS', '0x8000000'),
                           ('CONFIG_CLOCK_REF_FREQ', '8000000'),
                           ('CONFIG_WANT_MODBUSUART', 'n'), ('CONFIG_WANT_TMCUART', 'n'),
                           ('CONFIG_WANT_SPI', 'n'), ('CONFIG_INITIAL_PINS', '"PA6"'),
                           ('CONFIG_STARTUP_PIN_STATE', '1')):
            with self.subTest(key=key):
                config = self.resolved_config()
                config[key] = value
                with self.assertRaises(builder.BuildError):
                    builder.validate_config(config, 'usb')

    def test_resolved_transport_cannot_silently_fall_back(self):
        for mode, key, value in (('usb', 'CONFIG_USBCANBUS', 'y'),
                                 ('canbridge', 'CONFIG_CANBUS_FREQUENCY', '500000'),
                                 ('canbridge', 'CONFIG_STM32_USB_PA11_PA12', 'y'),
                                 ('canbridge', 'CONFIG_STM32_CANBUS_PB8_PB9', 'n')):
            with self.subTest(mode=mode, key=key):
                config = self.resolved_config(mode)
                config[key] = value
                with self.assertRaises(builder.BuildError):
                    builder.validate_config(config, mode)

    def test_prepare_keeps_input_untouched_and_ignores_old_build_config_and_secrets(self):
        self.fixture_source()
        originals = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        for name in ('.config', 'out/klipper.bin', '.git/config', 'printer_data/config/printer.cfg',
                     'scripts/debug.log', 'klippy/auth.key'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('must not be copied\n')
        result = builder.prepare_source(self.source, self.work, 'usb')
        tree = self.work / 'source'
        self.assertEqual('prepared-not-built', result['status'])
        self.assertFalse(result['flashed'])
        self.assertFalse(result['device_tested'])
        self.assertEqual(builder.PATCHED_SHA, builder.file_sha(tree / 'src/modbus_uart.c'))
        self.assertEqual((ROOT / 'firmware/c8p-usb.config').read_text(),
                         (tree / '.config').read_text())
        self.assertFalse((tree / 'out').exists())
        for name in ('.git', 'printer_data', 'scripts/debug.log', 'klippy/auth.key'):
            self.assertFalse((tree / name).exists(), name)
        for path, original in originals.items():
            self.assertEqual(original, path.read_bytes(), str(path))
        self.assertFalse((self.source / 'src/modbus_uart.c').exists())
        self.assertEqual([], list((self.work / 'artifacts').iterdir()))

    def test_existing_output_is_never_replaced(self):
        self.fixture_source()
        self.work.mkdir()
        sentinel = self.work / 'important'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(builder.BuildError, 'already exists'):
            builder.prepare_source(self.source, self.work, 'usb')
        self.assertEqual('keep', sentinel.read_text())

    def test_source_and_output_overlap_rejected(self):
        self.fixture_source()
        for path in (self.source, self.source / 'build', self.base):
            with self.subTest(path=path), self.assertRaisesRegex(builder.BuildError, 'overlap'):
                builder.prepare_source(self.source, path, 'usb')
        self.assertFalse((self.source / 'build').exists())

    def test_missing_source_rejected_before_creating_output(self):
        with self.assertRaises(builder.BuildError):
            builder.prepare_source(self.source, self.work, 'usb')
        self.assertFalse(self.work.exists())

    def test_source_symlink_cannot_pull_external_files_into_snapshot(self):
        self.fixture_source()
        external = self.base / 'outside.txt'
        external.write_text('private data')
        (self.source / 'scripts/external.py').symlink_to(external)
        with self.assertRaisesRegex(builder.BuildError, 'symlink'):
            builder.prepare_source(self.source, self.work, 'usb')
        self.assertFalse(self.work.exists())
        self.assertEqual('private data', external.read_text())

    def test_output_parent_symlink_rejected(self):
        self.fixture_source()
        alias = self.base / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(builder.BuildError, 'Symlink'):
            builder.prepare_source(self.source, alias / 'build', 'usb')
        self.assertFalse((self.source / 'build').exists())

    def test_make_unsafe_output_paths_rejected(self):
        self.fixture_source()
        for name in ('space here', 'semi;colon', 'dollar$(touch bad)', 'line\nbreak'):
            with self.subTest(name=name), self.assertRaises(builder.BuildError):
                builder.prepare_source(self.source, self.base / name, 'usb')
            self.assertFalse((self.base / name).exists())

    def test_unknown_existing_modbus_source_refused_without_overwrite(self):
        self.fixture_source()
        target = self.source / 'src/modbus_uart.c'
        target.write_text('user modification')
        with self.assertRaisesRegex(builder.BuildError, 'Unknown existing'):
            builder.integrate_modbus(self.source)
        self.assertEqual('user modification', target.read_text())

    def test_known_original_and_patched_sources_integrate_idempotently(self):
        self.fixture_source()
        target = self.source / 'src/modbus_uart.c'
        shutil.copyfile(ROOT / 'firmware/modbus_uart.c', target)
        first = builder.integrate_modbus(self.source)
        snapshots = {name: (self.source / name).read_bytes()
                     for name in ('src/modbus_uart.c', 'src/Kconfig', 'src/Makefile')}
        second = builder.integrate_modbus(self.source)
        self.assertEqual(builder.ORIGINAL_SHA, first['previous_sha256'])
        self.assertEqual(builder.PATCHED_SHA, second['previous_sha256'])
        for name, value in snapshots.items():
            self.assertEqual(value, (self.source / name).read_bytes())

    def test_partial_modbus_kconfig_and_duplicate_make_integration_refused(self):
        self.fixture_source()
        kpath = self.source / 'src/Kconfig'
        original = kpath.read_text()
        kpath.write_text(original + builder.MODBUS_KCONFIG[0])
        with self.assertRaisesRegex(builder.BuildError, 'partial'):
            builder.integrate_modbus(self.source)
        self.assertFalse((self.source / 'src/modbus_uart.c').exists())
        kpath.write_text(original)
        mpath = self.source / 'src/Makefile'
        mpath.write_text(builder.TMC_MAKE + '\n' + (builder.MODBUS_MAKE + '\n') * 2)
        with self.assertRaisesRegex(builder.BuildError, 'duplicate'):
            builder.integrate_modbus(self.source)
        self.assertFalse((self.source / 'src/modbus_uart.c').exists())

    def test_make_anchor_without_final_newline_cannot_silently_skip_integration(self):
        self.fixture_source()
        path = self.source / 'src/Makefile'
        path.write_text(builder.TMC_MAKE)
        try:
            builder.integrate_modbus(self.source)
        except builder.BuildError:
            # Refusal is safe; successful preparation must include the rule.
            self.assertFalse((self.source / 'src/modbus_uart.c').exists())
        else:
            self.assertEqual(1, path.read_text().splitlines().count(builder.MODBUS_MAKE))

    def test_unknown_version_recipe_is_not_replaced(self):
        self.fixture_source()
        path = self.source / 'Makefile'
        original = path.read_text().replace(' -t ', ' --custom-option -t ')
        path.write_text(original)
        with self.assertRaisesRegex(builder.BuildError, 'Unknown buildcommands'):
            builder.patch_version(self.source)
        self.assertEqual(original, path.read_text())

    def test_both_artifact_profiles_validate_consistent_offline_fixtures(self):
        self.source.mkdir()
        for mode in ('usb', 'canbridge'):
            with self.subTest(mode=mode):
                self.fake_outputs(self.source, mode)
                result = builder.validate_artifacts(self.source, mode)
                self.assertEqual(32, result['binary_size'])
                self.assertEqual(0x08020000, result['flash_start'])

    def test_dictionary_requires_full_protocol_schema(self):
        self.source.mkdir()
        dictionary = self.fake_outputs(self.source)
        expected = builder.COMMANDS[0]
        del dictionary['commands'][expected]
        dictionary['commands'][expected.split()[0] + ' wrong=%c'] = 0
        (self.source / 'out/klipper.dict').write_text(json.dumps(dictionary))
        with self.assertRaisesRegex(builder.BuildError, 'Missing exact'):
            builder.validate_artifacts(self.source, 'usb')

    def test_malformed_dictionary_shape_is_reported_as_build_failure(self):
        self.source.mkdir()
        for variant in ('root-list', 'config-list', 'null'):
            with self.subTest(variant=variant):
                dictionary = self.fake_outputs(self.source)
                if variant == 'root-list':
                    dictionary = []
                elif variant == 'config-list':
                    dictionary['config'] = []
                else:
                    dictionary = None
                (self.source / 'out/klipper.dict').write_text(json.dumps(dictionary))
                with self.assertRaises(builder.BuildError):
                    builder.validate_artifacts(self.source, 'usb')

    def test_dictionary_rejects_wrong_can_pin_or_missing_bridge(self):
        self.source.mkdir()
        for key, value in (('CANBUS_BRIDGE', 0), ('RESERVE_PINS_CAN', 'PD0,PD1')):
            with self.subTest(key=key):
                dictionary = self.fake_outputs(self.source, 'canbridge')
                dictionary['config'][key] = value
                (self.source / 'out/klipper.dict').write_text(json.dumps(dictionary))
                with self.assertRaisesRegex(builder.BuildError, 'CAN bridge/pin'):
                    builder.validate_artifacts(self.source, 'canbridge')

    def test_binary_vector_and_elf_data_must_agree(self):
        self.source.mkdir()
        for change, error in (('stack', 'stack'), ('reset', 'reset'), ('elf', 'load data')):
            with self.subTest(change=change):
                self.fake_outputs(self.source)
                path = self.source / ('out/klipper.elf' if change == 'elf' else 'out/klipper.bin')
                data = bytearray(path.read_bytes())
                if change == 'stack':
                    struct.pack_into('<I', data, 0, 0)
                elif change == 'reset':
                    struct.pack_into('<I', data, 4, 0x08000001)
                else:
                    data[-1] ^= 1
                path.write_bytes(data)
                with self.assertRaisesRegex(builder.BuildError, error):
                    builder.validate_artifacts(self.source, 'usb')

    def test_missing_or_symlinked_artifact_refused(self):
        self.source.mkdir()
        self.fake_outputs(self.source)
        path = self.source / 'out/klipper.bin'
        path.unlink()
        with self.assertRaisesRegex(builder.BuildError, 'Missing/invalid artifact'):
            builder.validate_artifacts(self.source, 'usb')
        external = self.base / 'outside.bin'
        external.write_bytes(b'not the build')
        path.symlink_to(external)
        with self.assertRaisesRegex(builder.BuildError, 'Missing/invalid artifact'):
            builder.validate_artifacts(self.source, 'usb')

    def test_prepare_cli_never_invokes_external_build_or_device_commands(self):
        self.fixture_source()
        with patch.object(builder, '_run') as run, \
                patch.object(builder.shutil, 'which') as which, \
                contextlib.redirect_stdout(io.StringIO()):
            code = builder.main(['--mode', 'usb', '--source', str(self.source),
                                 '--work-dir', str(self.work), '--prepare'])
        self.assertEqual(0, code)
        run.assert_not_called()
        which.assert_not_called()
        self.assertFalse((self.work / 'source/out').exists())

    def test_named_wrappers_cannot_be_overridden_to_the_opposite_profile(self):
        self.fixture_source()
        for mode, opposite in (('usb', 'canbridge'), ('canbridge', 'usb')):
            with self.subTest(mode=mode):
                work = self.base / ('wrapper-' + mode)
                result = subprocess.run(['sh', str(ROOT / ('scripts/build-c8p-%s.sh' % mode)),
                                         '--mode', opposite, '--source', str(self.source),
                                         '--work-dir', str(work), '--prepare'],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.returncode == 0:
                    manifest = json.loads((work / 'manifest.json').read_text())
                    self.assertEqual(mode, manifest['mode'])
                    self.assertEqual('prepared-not-built', manifest['status'])
                else:
                    self.assertFalse((work / 'manifest.json').exists())

    def test_mocked_build_runs_only_configure_and_compile_and_records_verified_outputs(self):
        self.fixture_source()
        calls = []
        def run(command, tree, log):
            calls.append(command)
            if 'olddefconfig' in command:
                self.write_config(tree, 'canbridge')
                # GNU make with second-resolution timestamps can mistake the
                # unresolved seed's board link for current generated output.
                stale = tree / 'out'
                stale.mkdir()
                (stale / 'board').symlink_to(tree / 'src', target_is_directory=True)
                (stale / 'board-link').write_text('seed used empty BOARD_DIRECTORY\n')
                same_second = 1700000000
                os.utime(tree / '.config', (same_second, same_second))
                os.utime(stale / 'board-link', (same_second, same_second))
            elif 'all' in command:
                self.assertFalse((tree / 'out').exists(),
                                 'stale configure output must be removed before compilation')
                self.fake_outputs(tree, 'canbridge')
            else:
                self.fail('Unexpected external command: ' + repr(command))
        with patch.object(builder, '_run', side_effect=run), \
                patch.object(builder.shutil, 'which', return_value='/mock/tool'), \
                contextlib.redirect_stdout(io.StringIO()):
            code = builder.main(['--mode', 'canbridge', '--source', str(self.source),
                                 '--work-dir', str(self.work), '--jobs', '2'])
        self.assertEqual(0, code)
        self.assertEqual(2, len(calls))
        self.assertTrue(all(command[0] == 'make' for command in calls))
        self.assertEqual('olddefconfig', calls[0][-1])
        self.assertEqual('all', calls[1][-1])
        for command in calls:
            self.assertIn('CPP=arm-none-eabi-cpp', command)
            self.assertIn('OUT=out/', command)
            self.assertIn('KCONFIG_CONFIG=' + str(self.work / 'source/.config'), command)
        self.assertFalse(any(word in ('flash', 'restart', 'sudo')
                             for command in calls for word in command))
        manifest = json.loads((self.work / 'manifest.json').read_text())
        self.assertEqual('built-not-flashed', manifest['status'])
        self.assertFalse(manifest['flashed'])
        self.assertFalse(manifest['device_tested'])
        for name, digest in manifest['artifact_sha256'].items():
            self.assertEqual(digest, builder.file_sha(self.work / 'artifacts' / name))

    def test_clean_generated_output_does_not_follow_nested_board_symlink(self):
        self.fixture_source()
        output = self.source / 'out'
        output.mkdir()
        external = self.base / 'external'
        external.mkdir()
        sentinel = external / 'important'
        sentinel.write_text('retain external source')
        (output / 'board').symlink_to(external, target_is_directory=True)
        (output / 'board-link').write_text('stale')
        builder.reset_build_output(self.source)
        self.assertFalse(output.exists())
        self.assertEqual('retain external source', sentinel.read_text())
        self.assertTrue((self.source / 'src/Kconfig').exists())

    def test_clean_rejects_external_output_symlink_without_deleting_target(self):
        self.fixture_source()
        external = self.base / 'external'
        external.mkdir()
        sentinel = external / 'important'
        sentinel.write_text('retain external output')
        output = self.source / 'out'
        output.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(builder.BuildError, 'outside'):
            builder.reset_build_output(self.source)
        self.assertTrue(output.is_symlink())
        self.assertEqual('retain external output', sentinel.read_text())

    def test_clean_rejects_regular_file_instead_of_output_directory(self):
        self.fixture_source()
        output = self.source / 'out'
        output.write_text('retain user file')
        with self.assertRaisesRegex(builder.BuildError, 'not a directory'):
            builder.reset_build_output(self.source)
        self.assertEqual('retain user file', output.read_text())

    def test_failed_config_stops_before_compile_and_retains_failure_manifest(self):
        self.fixture_source()
        calls = []
        def run(command, tree, log):
            calls.append(command)
            self.write_config(tree)
            with (tree / '.config').open('a') as stream:
                stream.write('CONFIG_USBCANBUS=y\n')
        with patch.object(builder, '_run', side_effect=run), \
                patch.object(builder.shutil, 'which', return_value='/mock/tool'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = builder.main(['--mode', 'usb', '--source', str(self.source),
                                 '--work-dir', str(self.work)])
        self.assertEqual(1, code)
        self.assertEqual(1, len(calls))
        manifest = json.loads((self.work / 'manifest.json').read_text())
        self.assertEqual('failed-not-flashed', manifest['status'])
        self.assertEqual([], list((self.work / 'artifacts').iterdir()))

    def test_subprocess_uses_argument_list_and_scrubs_external_make_config(self):
        polluted = {'MAKEFLAGS': '--eval=bad', 'MFLAGS': '-e', 'MAKEOVERRIDES': 'OUT=/elsewhere',
                    'KCONFIG_CONFIG': '/outside/config', 'PYTHONPATH': '/outside/python'}
        with patch.dict(os.environ, polluted), patch.object(builder.subprocess, 'run') as run:
            builder._run(['make', 'olddefconfig'], self.base, self.base / 'build.log')
        args, kwargs = run.call_args
        self.assertEqual(['make', 'olddefconfig'], args[0])
        self.assertFalse(kwargs.get('shell', False))
        self.assertTrue(kwargs['check'])
        for key in polluted:
            self.assertNotIn(key, kwargs['env'])


if __name__ == '__main__':
    unittest.main()
