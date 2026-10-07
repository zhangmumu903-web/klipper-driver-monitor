"""Installer tests operate only on disposable directories, never a printer."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
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


if __name__ == '__main__':
    unittest.main()
