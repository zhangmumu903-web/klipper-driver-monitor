"""Installer tests operate only on disposable directories, never a printer."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.klipper = self.base / 'klipper'
        self.extras = self.klipper / 'klippy/extras'
        self.extras.mkdir(parents=True)
        self.fluidd = self.base / 'fluidd'
        self.fluidd.mkdir()
        self.index = self.fluidd / 'index.html'
        self.original = b'<!doctype html><html><body><div id="app"></div></body></html>\n'
        self.index.write_bytes(self.original)
        for name in installer.LYX_FILES:
            shutil.copy2(ROOT / 'vendor/lyx' / name, self.extras / name)
        self.args = argparse.Namespace(klipper=str(self.klipper), fluidd=str(self.fluidd),
            hostname='printer-demo', origin=['http://192.0.2.10'], api_url=['http://192.0.2.10:7125'],
            with_lyx=False)
        self.backups = self.base / 'backups'

    def install(self, plan=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return installer.apply_plan(plan or installer.build_plan(self.args), self.backups)

    def test_plan_has_no_side_effects(self):
        plan = installer.build_plan(self.args)
        self.assertEqual(6, len(plan['changes']))
        self.assertEqual(self.original, self.index.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertFalse(self.backups.exists())

    def test_install_and_exact_rollback(self):
        result = self.install()
        self.assertEqual('installed', result['status'])
        self.assertIn(installer.BEGIN.encode(), self.index.read_bytes())
        self.assertEqual([], installer.build_plan(self.args)['changes'])
        preview = installer.rollback(result['receipt'])
        self.assertEqual(6, preview['files'])
        self.assertNotEqual(self.original, self.index.read_bytes())
        installer.rollback(result['receipt'], apply=True)
        self.assertEqual(self.original, self.index.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())

    def test_existing_backend_and_permissions_restored(self):
        path = self.extras / 'driver_monitor.py'
        path.write_bytes(b'# old reviewed version\n')
        path.chmod(0o640)
        result = self.install()
        self.assertEqual(0o640, path.stat().st_mode & 0o777)
        installer.rollback(result['receipt'], apply=True)
        self.assertEqual(b'# old reviewed version\n', path.read_bytes())
        self.assertEqual(0o640, path.stat().st_mode & 0o777)

    def test_configured_multiple_browser_origins(self):
        self.args.origin.append('https://printer.example')
        self.args.api_url.append('https://printer.example')
        plan = installer.build_plan(self.args)
        data = next(x['after'] for x in plan['changes'] if x['path'].name == 'driver-monitor-config.js')
        self.assertIn(b'https://printer.example', data)
        self.assertIn(b'printer-demo', data)
        self.assertEqual(32, len(plan['config']['apiEndpoints'][0]['printerId']))

    def test_generated_js_imports_match_files(self):
        plan = installer.build_plan(self.args)
        self.install(plan)
        directory = self.fluidd / plan['asset_dir']
        for name in ('driver-monitor.js', 'driver-monitor-core.js'):
            text = (directory / name).read_text()
            self.assertNotIn('.mjs', text)
            self.assertIn('./driver-monitor-config.js', text)

    def test_unknown_native_source_rejected(self):
        (self.extras / 'lyx.py').write_text('# unknown modifications\n')
        with self.assertRaises(ValueError):
            installer.build_plan(self.args)
        self.args.with_lyx = True
        with self.assertRaises(ValueError):
            installer.build_plan(self.args)

    def test_missing_lyx_requires_explicit_install(self):
        for name in installer.LYX_FILES:
            (self.extras / name).unlink()
        with self.assertRaises(ValueError):
            installer.build_plan(self.args)
        self.args.with_lyx = True
        result = self.install()
        self.assertEqual(9, result['files'])
        installer.rollback(result['receipt'], apply=True)
        for name in installer.LYX_FILES:
            self.assertFalse((self.extras / name).exists())

    def test_concurrent_change_prevents_any_install(self):
        plan = installer.build_plan(self.args)
        self.index.write_text('other editor')
        with self.assertRaises(ValueError):
            self.install(plan)
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())

    def test_rollback_does_not_overwrite_user_change(self):
        result = self.install()
        path = self.extras / 'driver_monitor.py'
        path.write_text('user edit')
        index_after = self.index.read_bytes()
        with self.assertRaises(ValueError):
            installer.rollback(result['receipt'], apply=True)
        self.assertEqual('user edit', path.read_text())
        self.assertEqual(index_after, self.index.read_bytes())

    def test_partial_install_has_recoverable_write_ahead_receipt(self):
        original_write = installer.atomic_write
        def fail_on_core(path, data, metadata=None):
            if path.name == 'driver-monitor-core.js':
                raise OSError('simulated disk write failure')
            original_write(path, data, metadata)
        with patch.object(installer, 'atomic_write', fail_on_core):
            with self.assertRaises(OSError):
                self.install()
        receipt = next(self.backups.glob('*/receipt.json'))
        self.assertTrue((self.extras / 'driver_monitor.py').exists())
        installer.rollback(receipt, apply=True)
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_symlink_target_rejected(self):
        path = self.extras / 'driver_monitor.py'
        path.symlink_to(self.index)
        with self.assertRaises(ValueError):
            installer.build_plan(self.args)
        self.assertEqual(self.original, self.index.read_bytes())

    def test_unmanaged_old_install_refused(self):
        for src in ('"/old/driver-monitor.js"', '/old/driver-monitor.js'):
            self.index.write_text('<body><script type=module src=%s></script></body>' % src)
            with self.assertRaisesRegex(ValueError, 'unmanaged'):
                installer.build_plan(self.args)

    def test_restrictive_umask_keeps_new_web_assets_readable(self):
        previous = os.umask(0o077)
        try:
            plan = installer.build_plan(self.args)
            self.install(plan)
        finally:
            os.umask(previous)
        directory = self.fluidd / plan['asset_dir']
        self.assertEqual(0o755, directory.stat().st_mode & 0o777)
        self.assertEqual(0o644, (directory / 'driver-monitor.js').stat().st_mode & 0o777)

    def test_bad_markers_and_body_refused(self):
        for text in ('<html/>', '<body></body></body>', '<body>'+installer.BEGIN+'</body>'):
            self.index.write_text(text)
            with self.assertRaises(ValueError):
                installer.build_plan(self.args)

    def test_invalid_or_credentialed_endpoint_refused(self):
        for url in ('http://name:secret@example.test', 'http://example.test/path',
                    'http://example.test/', 'javascript:alert(1)', 'http://example.test?token=x'):
            with self.assertRaises(ValueError):
                installer.target_config('printer-demo', [url], None)
        with self.assertRaises(ValueError):
            installer.target_config('', ['http://example.test'], None)

    def test_backup_tampering_refused(self):
        result = self.install()
        receipt_path = Path(result['receipt'])
        receipt = json.loads(receipt_path.read_text())
        old_index = next(x for x in receipt['files'] if x['path'] == str(self.index.resolve()))
        (receipt_path.parent / old_index['backup_file']).write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            installer.rollback(receipt_path, apply=True)

    def select_components(self, components, drivers='lyx'):
        self.args.components = components
        self.args.drivers = drivers
        if components == 'backend':
            self.args.fluidd = None
            self.args.hostname = None
            self.args.origin = None
            self.args.api_url = None
        elif components == 'fluidd':
            self.args.klipper = None

    def test_backend_only_without_web_identity_is_idempotent_and_reversible(self):
        self.select_components('backend')
        plan = installer.build_plan(self.args)
        self.assertEqual([self.extras.resolve() / 'driver_monitor.py'],
                         [change['path'] for change in plan['changes']])
        result = self.install(plan)
        self.assertEqual(1, result['files'])
        self.assertEqual([], installer.build_plan(self.args)['changes'])
        self.assertEqual(self.original, self.index.read_bytes())
        installer.rollback(result['receipt'], apply=True)
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_backend_only_can_explicitly_install_missing_lyx_modules(self):
        self.select_components('backend')
        self.args.with_lyx = True
        for name in installer.LYX_FILES:
            (self.extras / name).unlink()
        result = self.install()
        self.assertEqual(4, result['files'])
        self.assertEqual(self.original, self.index.read_bytes())
        installer.rollback(result['receipt'], apply=True)
        for name in ('driver_monitor.py',) + installer.LYX_FILES:
            self.assertFalse((self.extras / name).exists())

    def test_fluidd_only_never_reads_backend_or_lyx_and_rolls_back_exactly(self):
        self.select_components('fluidd')
        real_regular = installer.regular
        def only_web_files(path, optional=False):
            path = Path(path)
            self.assertNotIn(path.name, installer.LYX_FILES)
            self.assertNotEqual('PROVENANCE.json', path.name)
            self.assertNotEqual('driver_monitor.py', path.name)
            return real_regular(path, optional)
        with patch.object(installer, 'regular', side_effect=only_web_files):
            plan = installer.build_plan(self.args)
            self.assertEqual(5, len(plan['changes']))
            self.assertTrue(all(self.fluidd.resolve() in change['path'].parents
                                for change in plan['changes']))
            result = self.install(plan)
            self.assertEqual([], installer.build_plan(self.args)['changes'])
            installer.rollback(result['receipt'], apply=True)
        self.assertEqual(self.original, self.index.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())

    def test_tmc_install_ignores_unknown_missing_and_symlinked_lyx_files(self):
        self.select_components('all', 'tmc')
        unknown = self.extras / 'lyx.py'
        unknown.write_text('# preserve local unknown LYX implementation\n')
        linked = self.extras / 'lyx_uart.py'
        linked.unlink()
        linked.symlink_to(unknown)
        absent = self.extras / 'lyx9231.py'
        absent.unlink()
        real_regular = installer.regular
        def no_lyx_reads(path, optional=False):
            self.assertNotIn(Path(path).name, installer.LYX_FILES)
            self.assertNotEqual('PROVENANCE.json', Path(path).name)
            return real_regular(path, optional)
        with patch.object(installer, 'regular', side_effect=no_lyx_reads):
            result = self.install()
            self.assertEqual(6, result['files'])
            self.assertEqual([], installer.build_plan(self.args)['changes'])
            installer.rollback(result['receipt'], apply=True)
        self.assertEqual('# preserve local unknown LYX implementation\n', unknown.read_text())
        self.assertTrue(linked.is_symlink())
        self.assertEqual(str(unknown), os.readlink(linked))
        self.assertFalse(absent.exists())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_tmc_backend_only_does_not_require_lyx_modules(self):
        self.select_components('backend', 'tmc')
        for name in installer.LYX_FILES:
            (self.extras / name).unlink()
        result = self.install()
        self.assertEqual(1, result['files'])
        self.assertEqual([], installer.build_plan(self.args)['changes'])
        installer.rollback(result['receipt'], apply=True)
        self.assertFalse((self.extras / 'driver_monitor.py').exists())

    def test_invalid_component_or_driver_selection_is_rejected_without_writes(self):
        for components, drivers in (('mainsail', 'lyx'), ('all', 'unknown'),
                                    ('', 'lyx'), ('backend', '')):
            with self.subTest(components=components, drivers=drivers):
                self.args.components = components
                self.args.drivers = drivers
                with self.assertRaises(ValueError):
                    installer.build_plan(self.args)
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_incompatible_lyx_options_are_rejected_before_install(self):
        self.args.with_lyx = True
        for components, drivers in (('all', 'tmc'), ('backend', 'tmc'),
                                    ('fluidd', 'lyx'), ('fluidd', 'tmc')):
            with self.subTest(components=components, drivers=drivers):
                self.args.components = components
                self.args.drivers = drivers
                with self.assertRaises(ValueError):
                    installer.build_plan(self.args)
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_backend_lyx_validation_failure_does_not_change_web(self):
        self.select_components('backend')
        (self.extras / 'lyx.py').write_text('# unknown local change\n')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_fluidd_validation_failure_does_not_change_existing_backend(self):
        self.select_components('fluidd')
        backend = self.extras / 'driver_monitor.py'
        backend.write_text('# preserve existing backend\n')
        self.index.write_text('<html>no body end</html>')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.backups.exists())
        self.assertEqual('# preserve existing backend\n', backend.read_text())

    def test_backend_rollback_does_not_undo_separately_installed_web(self):
        self.select_components('backend')
        backend_result = self.install()
        self.args.components = 'fluidd'
        self.args.fluidd = str(self.fluidd)
        self.args.hostname = 'printer-demo'
        self.args.origin = ['http://192.0.2.10']
        self.args.api_url = None
        web_result = self.install()
        installed_index = self.index.read_bytes()
        installer.rollback(backend_result['receipt'], apply=True)
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(installed_index, self.index.read_bytes())
        installer.rollback(web_result['receipt'], apply=True)
        self.assertEqual(self.original, self.index.read_bytes())

    def test_fluidd_rollback_does_not_undo_separately_installed_backend(self):
        self.select_components('fluidd')
        web_result = self.install()
        self.args.components = 'backend'
        self.args.klipper = str(self.klipper)
        backend_result = self.install()
        backend = self.extras / 'driver_monitor.py'
        installed_backend = backend.read_bytes()
        installer.rollback(web_result['receipt'], apply=True)
        self.assertEqual(self.original, self.index.read_bytes())
        self.assertEqual(installed_backend, backend.read_bytes())
        installer.rollback(backend_result['receipt'], apply=True)
        self.assertFalse(backend.exists())

    def test_component_specific_cli_accepts_only_its_required_paths(self):
        cases = [
            ['--components', 'backend', '--drivers', 'tmc', '--klipper', str(self.klipper)],
            ['--components', 'fluidd', '--fluidd', str(self.fluidd),
             '--hostname', 'printer-demo', '--origin', 'http://192.0.2.10'],
        ]
        for options in cases:
            with self.subTest(options=options), \
                    patch.object(sys, 'argv', ['install.py', 'plan'] + options), \
                    contextlib.redirect_stdout(io.StringIO()):
                installer.main()
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(self.original, self.index.read_bytes())

    def test_component_specific_cli_rejects_missing_required_target(self):
        cases = [
            ['--components', 'backend'],
            ['--components', 'fluidd', '--hostname', 'printer-demo', '--origin', 'http://192.0.2.10'],
            ['--components', 'fluidd', '--fluidd', str(self.fluidd), '--origin', 'http://192.0.2.10'],
            ['--components', 'fluidd', '--fluidd', str(self.fluidd), '--hostname', 'printer-demo'],
        ]
        for options in cases:
            with self.subTest(options=options), \
                    patch.object(sys, 'argv', ['install.py', 'plan'] + options), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                installer.main()
            self.assertEqual(2, exc.exception.code)
        self.assertFalse(self.backups.exists())


if __name__ == '__main__':
    unittest.main()
