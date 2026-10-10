"""Lifecycle tests use disposable trees only; no service or hardware commands."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('manage', ROOT / 'scripts/manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.klipper = self.base / 'klipper'
        self.extras = self.klipper / 'klippy/extras'
        self.extras.mkdir(parents=True)
        for name in manage.installer.LYX_FILES:
            shutil.copy2(ROOT / 'vendor/lyx' / name, self.extras / name)
        self.fluidd = self.base / 'fluidd'
        self.fluidd.mkdir()
        self.index = self.fluidd / 'index.html'
        self.original_index = b'<html><body><main>Fluidd v1</main></body></html>\n'
        self.index.write_bytes(self.original_index)
        self.config = self.base / 'config/printer.cfg'
        self.config.parent.mkdir()
        self.original_cfg = b'[include axes.cfg]\n#*# <---------------------- SAVE_CONFIG ---------------------->\n#*# [bed_mesh default]\n#*# version = 1\n'
        self.config.write_bytes(self.original_cfg)
        (self.config.parent / 'axes.cfg').write_text('[lyx9231 stepper_z]\nuart_pin: PA0\nrun_current: 1.3\n')
        self.state_dir = self.base / 'private-state'
        self.initial = ['--klipper', str(self.klipper), '--fluidd', str(self.fluidd),
                        '--config', str(self.config), '--hostname', 'test-printer',
                        '--origin', 'http://printer.test', '--state-dir', str(self.state_dir)]
        self.testroot = self.base / 'package'
        (self.testroot / 'frontend/customization').mkdir(parents=True)
        for name, text in (('layout.json', '{"schema_version":1,"mode":"cards"}'),
                           ('custom.css', '/* Customize card styles here. */'),
                           ('custom-renderer.mjs.example', 'export function render() { return null; }')):
            (self.testroot / 'frontend/customization' / name).write_text(text)
        # ROOT is only used for source reads by manage; installer asset generation
        # continues using the real package so the fixture follows new modules.
        shutil.copytree(ROOT / 'backend', self.testroot / 'backend')
        shutil.copytree(ROOT / 'vendor', self.testroot / 'vendor')
        self.rootpatch = patch.object(manage, 'ROOT', self.testroot)
        self.rootpatch.start()
        self.addCleanup(self.rootpatch.stop)

    def args(self, command='install', *extra):
        base = self.initial if not (self.state_dir / 'manifest.json').exists() else ['--state-dir', str(self.state_dir)]
        return manage.parser().parse_args([command] + base + list(extra))

    def plan(self, command='install', *extra):
        return manage.new_plan(self.args(command, *extra), self.state_dir)

    def install(self):
        return manage.apply(self.plan())

    def state(self):
        return manage.load_state(self.state_dir)

    def test_plan_is_readonly_and_monitor_only(self):
        plan = self.plan()
        self.assertFalse(self.state_dir.exists())
        paths = [x['path'] for x in plan['changes']]
        self.assertIn(str(self.extras / 'driver_monitor.py'), paths)
        self.assertFalse(any(Path(x).name in manage.installer.LYX_FILES for x in paths))
        self.assertEqual(self.original_cfg, self.config.read_bytes())
        self.assertTrue(plan['restart_required'])

    def test_install_repeat_update_uninstall_leaves_lyx_and_customization(self):
        result = self.install()
        self.assertEqual('installed', result['status'])
        self.assertTrue(result['restart_required'])
        self.assertFalse(result['runtime_verified'])
        text = self.config.read_text()
        self.assertLess(text.index(manage.CFG_BEGIN), text.index('SAVE_CONFIG'))
        self.assertEqual([str(self.config.parent / manage.CONFIG_NAME)], manage.scan_config(self.config)['monitor'])
        self.assertEqual('unchanged', manage.apply(self.plan('install'))['status'])
        self.assertEqual('unchanged', manage.apply(self.plan('update'))['status'])
        self.assertIn(b'[driver_monitor]', (self.config.parent / manage.CONFIG_NAME).read_bytes())
        self.assertIn(b'shutdown_on_alarm: false', (self.config.parent / manage.CONFIG_NAME).read_bytes())
        user = self.fluidd / 'driver-monitor-user/custom.css'
        user.write_text('body {color:red}')
        self.index.write_bytes(self.index.read_bytes().replace(b'Fluidd v1', b'Fluidd v2'))
        self.config.write_bytes(self.config.read_bytes() + b'\n[user-added section]\nvalue:123\n')
        result = manage.apply(self.plan('uninstall'))
        self.assertEqual('uninstalled', result['status'])
        self.assertIn(b'Fluidd v2', self.index.read_bytes())
        self.assertNotIn(manage.installer.BEGIN.encode(), self.index.read_bytes())
        self.assertIn(b'[user-added section]', self.config.read_bytes())
        self.assertIn(b'#*# [bed_mesh default]', self.config.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertTrue((self.extras / 'lyx9231.py').exists())
        self.assertEqual('body {color:red}', user.read_text())
        self.assertTrue((self.state_dir / 'history').exists())

    def test_frontend_uninstall_retains_backend_and_protection(self):
        self.install()
        config = self.config.parent / manage.CONFIG_NAME
        config.write_bytes(config.read_bytes().replace(b'shutdown_on_alarm: false', b'shutdown_on_alarm: true'))
        result = manage.apply(self.plan('uninstall', '--frontend-only'))
        self.assertFalse(result['restart_required'])
        self.assertTrue((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(['backend'], self.state()['components'])
        self.assertIn(b'shutdown_on_alarm: true', config.read_bytes())

    def test_user_owned_config_preserved_and_full_uninstall_preflight(self):
        axes = self.config.parent / 'axes.cfg'
        axes.write_bytes(axes.read_bytes() + b'\n[driver_monitor]\nshutdown_on_alarm: true\n')
        self.install()
        self.assertFalse((self.config.parent / manage.CONFIG_NAME).exists())
        self.assertEqual(self.original_cfg, self.config.read_bytes())
        index = self.index.read_bytes()
        with self.assertRaisesRegex(ValueError, 'user-owned'):
            self.plan('uninstall')
        self.assertEqual(index, self.index.read_bytes())
        self.assertTrue((self.extras / 'driver_monitor.py').exists())
        manage.apply(self.plan('uninstall', '--frontend-only'))
        self.assertIn(b'shutdown_on_alarm: true', axes.read_bytes())

    def test_existing_cfg_via_wildcard_and_duplicate_guard(self):
        self.config.write_text('[include *.inc]\n')
        (self.config.parent / 'monitor.inc').write_text('[driver_monitor]\nshutdown_on_alarm: true\n')
        self.install()
        self.assertFalse((self.config.parent / manage.CONFIG_NAME).exists())
        (self.config.parent / 'double.inc').write_text('[driver_monitor]\n')
        with self.assertRaisesRegex(ValueError, 'More than one'):
            self.plan('update')

    def test_wildcard_would_auto_load_new_cfg_rejected_without_writes(self):
        self.config.write_text('[include *.inc]\n')
        (self.config.parent / 'other.inc').write_text('[include driver-*.cfg]\n')
        with self.assertRaisesRegex(ValueError, 'wildcard'):
            self.plan()
        self.assertFalse((self.config.parent / manage.CONFIG_NAME).exists())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())

    def test_unknown_dependency_and_backend_edits_refused(self):
        lyx = self.extras / 'lyx9231.py'
        lyx.write_text('unknown fork')
        with self.assertRaisesRegex(ValueError, 'Compatible LYX'):
            self.plan()
        self.assertEqual('unknown fork', lyx.read_text())
        shutil.copy2(ROOT / 'vendor/lyx/lyx9231.py', lyx)
        self.install()
        backend = self.extras / 'driver_monitor.py'
        backend.write_text('local edit')
        for action in ('update', 'repair', 'uninstall'):
            with self.assertRaisesRegex(ValueError, 'Unknown local edit'):
                self.plan(action)

    def test_preserve_user_modified_generated_monitor_cfg_on_update(self):
        self.install()
        config = self.config.parent / manage.CONFIG_NAME
        config.write_text('[driver_monitor]\nshutdown_on_alarm: true\nread_gap:0.9\n')
        manage.apply(self.plan('update'))
        self.assertIn('read_gap:0.9', config.read_text())
        with self.assertRaisesRegex(ValueError, 'Unknown local edit'):
            self.plan('uninstall')

    def test_repair_preserves_upgraded_fluidd_and_custom_assets(self):
        self.install()
        entry = self.state()['entries']['frontend']
        directory = self.fluidd / entry['asset_dir']
        shutil.rmtree(directory)
        upgraded = b'<html><body><main>Fresh Fluidd</main></body></html>\n'
        self.index.write_bytes(upgraded)
        custom = self.fluidd / 'driver-monitor-user/layout.json'
        custom.write_text('{"mode":"compact"}')
        result = manage.apply(self.plan('repair', '--frontend-only'))
        self.assertFalse(result['restart_required'])
        self.assertTrue((directory / 'driver-monitor.js').is_file())
        self.assertIn(b'Fresh Fluidd', self.index.read_bytes())
        self.assertEqual('{"mode":"compact"}', custom.read_text())
        manage.apply(self.plan('uninstall', '--frontend-only'))
        self.assertIn(b'Fresh Fluidd', self.index.read_bytes())

    def test_repair_restores_missing_owned_config(self):
        self.install()
        owned = self.config.parent / manage.CONFIG_NAME
        owned.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing Klipper include'):
            manage.scan_config(self.config)
        plan = self.plan('repair', '--backend-only')
        self.assertEqual([str(owned)], [item['path'] for item in plan['changes']])
        result = manage.apply(plan)
        self.assertTrue(result['restart_required'])
        self.assertEqual(manage.DEFAULT_CONFIG, owned.read_bytes())
        self.assertEqual([str(owned)], manage.scan_config(self.config)['monitor'])

    def test_uninstall_removes_owned_include_when_owned_cfg_is_missing(self):
        self.install()
        (self.config.parent / manage.CONFIG_NAME).unlink()
        result = manage.apply(self.plan('uninstall'))
        self.assertTrue(result['restart_required'])
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertNotIn(manage.CFG_BEGIN.encode(), self.config.read_bytes())
        self.assertEqual([], manage.scan_config(self.config)['monitor'])

    def test_missing_unowned_include_still_blocks_repair_and_uninstall(self):
        self.install()
        (self.config.parent / manage.CONFIG_NAME).unlink()
        (self.config.parent / 'axes.cfg').unlink()
        index = self.index.read_bytes()
        for command in ('repair', 'uninstall'):
            with self.assertRaisesRegex(ValueError, 'Missing Klipper include.*axes.cfg'):
                self.plan(command)
        self.assertEqual(index, self.index.read_bytes())
        self.assertTrue((self.extras / 'driver_monitor.py').exists())

    def test_missing_unowned_monitor_cfg_cannot_be_adopted_as_recoverable(self):
        self.config.write_text('[include driver-monitor.cfg]\n')
        with self.assertRaisesRegex(ValueError, 'Missing Klipper include'):
            self.plan('install', '--adopt-existing')
        self.assertFalse(self.state_dir.exists())

    def test_missing_owned_cfg_repair_does_not_duplicate_replacement_section(self):
        self.install()
        (self.config.parent / manage.CONFIG_NAME).unlink()
        axes = self.config.parent / 'axes.cfg'
        axes.write_bytes(axes.read_bytes() + b'\n[driver_monitor]\nshutdown_on_alarm:true\n')
        with self.assertRaisesRegex(ValueError, 'different .*driver_monitor'):
            self.plan('repair', '--backend-only')
        self.assertFalse((self.config.parent / manage.CONFIG_NAME).exists())

    def test_reset_layout_preserves_custom_renderer_and_can_rollback(self):
        self.install()
        root = self.fluidd / 'driver-monitor-user'
        (root / 'layout.json').write_text('{"mode":"compact"}')
        (root / 'custom.css').write_text('.card {color:yellow}')
        (root / 'custom-renderer.js').write_text('export const myRenderer = true;')
        manage.apply(self.plan('update', '--frontend-only', '--reset-layout'))
        self.assertIn('cards', (root / 'layout.json').read_text())
        self.assertEqual('export const myRenderer = true;', (root / 'custom-renderer.js').read_text())
        manage.apply_rollback(manage.rollback_plan(self.state_dir))
        self.assertEqual('{"mode":"compact"}', (root / 'layout.json').read_text())
        self.assertEqual('.card {color:yellow}', (root / 'custom.css').read_text())

    def test_adopt_identical_legacy_without_removing_drivers(self):
        legacy_args = type('Legacy', (), {'klipper': str(self.klipper), 'fluidd': str(self.fluidd),
                      'hostname': 'test-printer', 'origin': ['http://printer.test'], 'api_url': None,
                      'with_lyx': False})()
        legacy_plan = manage.installer.build_plan(legacy_args)
        manage.installer.apply_plan(legacy_plan, self.base / 'old-backup')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            self.plan()
        result = manage.apply(self.plan('install', '--adopt-existing'))
        self.assertEqual('installed', result['status'])
        manage.apply(self.plan('uninstall'))
        self.assertTrue((self.extras / 'lyx9231.py').is_file())
        self.assertTrue((self.base / 'old-backup').is_dir())

    def test_adopt_rejects_modified_legacy_assets(self):
        self.test_adopt_identical_legacy_without_removing_drivers()
        # Build another unmanaged valid-index install, then damage its asset.
        legacy_args = type('Legacy', (), {'klipper': str(self.klipper), 'fluidd': str(self.fluidd),
                      'hostname': 'test-printer', 'origin': ['http://printer.test'], 'api_url': None,
                      'with_lyx': False, 'components': 'fluidd'})()
        plan = manage.installer.build_plan(legacy_args)
        manage.installer.apply_plan(plan, self.base / 'old-backup')
        (self.fluidd / plan['asset_dir'] / 'driver-monitor.js').write_text('bad')
        with self.assertRaisesRegex(ValueError, 'content hash'):
            self.plan('install', '--frontend-only', '--adopt-existing')

    def test_rollback_initial_and_upgrade_preserve_unrelated_changes(self):
        self.install()
        self.config.write_bytes(self.config.read_bytes() + b'\n# new user note\n')
        self.index.write_bytes(self.index.read_bytes().replace(b'Fluidd v1', b'Fluidd v3'))
        manage.apply_rollback(manage.rollback_plan(self.state_dir))
        self.assertIn(b'Fluidd v3', self.index.read_bytes())
        self.assertIn(b'# new user note', self.config.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertTrue((self.fluidd / 'driver-monitor-user/layout.json').exists())
        self.assertIsNone(self.state())

    def test_uninstall_rollback_restores_monitor_without_restoring_whole_index(self):
        self.install()
        manage.apply(self.plan('uninstall'))
        self.index.write_bytes(self.index.read_bytes().replace(b'Fluidd v1', b'Fluidd v9'))
        manage.apply_rollback(manage.rollback_plan(self.state_dir))
        self.assertIn(b'Fluidd v9', self.index.read_bytes())
        self.assertIn(manage.installer.BEGIN.encode(), self.index.read_bytes())
        text = self.index.read_text()
        self.assertLess(text.index(manage.installer.BEGIN), text.index('</body>'))
        self.assertTrue((self.extras / 'driver_monitor.py').exists())
        self.assertEqual(['backend', 'frontend'], self.state()['components'])

    def test_real_package_upgrade_and_rollback(self):
        self.install()
        old_state = self.state()
        backend_path = self.extras / 'driver_monitor.py'
        old_backend = backend_path.read_bytes()
        (self.testroot / 'backend/driver_monitor.py').write_bytes(old_backend + b'\n# new release\n')
        make_assets = manage.installer.make_assets
        def upgraded(config):
            result = make_assets(config)
            result['driver-monitor.js'] += b'\n// new release\n'
            return result
        user = self.fluidd / 'driver-monitor-user/custom.css'
        user.write_text('.card {color: blue}')
        with patch.object(manage.installer, 'make_assets', side_effect=upgraded):
            result = manage.apply(self.plan('update'))
        self.assertTrue(result['restart_required'])
        self.assertNotEqual(old_backend, backend_path.read_bytes())
        self.assertNotEqual(old_state['entries']['frontend']['asset_dir'], self.state()['entries']['frontend']['asset_dir'])
        manage.apply_rollback(manage.rollback_plan(self.state_dir))
        self.assertEqual(old_state, self.state())
        self.assertEqual(old_backend, backend_path.read_bytes())
        self.assertEqual('.card {color: blue}', user.read_text())

    def test_rollback_retry_after_partial_rollback_keeps_unrelated_edits(self):
        self.install()
        self.index.write_bytes(self.index.read_bytes().replace(b'Fluidd v1', b'Fluidd v8'))
        real_write = manage.write_file
        def fail(path, data, metadata=None):
            if path == self.extras / 'driver_monitor.py':
                raise OSError('disk error late in rollback')
            return real_write(path, data, metadata)
        with patch.object(manage, 'write_file', side_effect=fail):
            with self.assertRaises(OSError):
                manage.apply_rollback(manage.rollback_plan(self.state_dir))
        manage.apply_rollback(manage.rollback_plan(self.state_dir))
        self.assertIn(b'Fluidd v8', self.index.read_bytes())
        self.assertNotIn(manage.installer.BEGIN.encode(), self.index.read_bytes())

    def test_partial_transaction_recovers_from_pending(self):
        real_write = manage.write_file
        def fail(path, data, metadata=None):
            if path == self.index:
                raise OSError('out of disk')
            return real_write(path, data, metadata)
        with patch.object(manage, 'write_file', side_effect=fail):
            with self.assertRaises(OSError):
                self.install()
        self.assertTrue((self.state_dir / 'pending.json').exists())
        manage.apply_rollback(manage.rollback_plan(self.state_dir))
        self.assertEqual(self.original_cfg, self.config.read_bytes())
        self.assertEqual(self.original_index, self.index.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertFalse((self.state_dir / 'pending.json').exists())

    def test_concurrent_change_prevents_any_writes(self):
        plan = self.plan()
        self.config.write_bytes(self.config.read_bytes() + b'# concurrent edit\n')
        with self.assertRaisesRegex(ValueError, 'changed since planning'):
            manage.apply(plan)
        self.assertFalse(self.state_dir.exists())
        self.assertEqual(self.original_index, self.index.read_bytes())

    def test_symlink_custom_or_asset_path_rejected(self):
        (self.fluidd / 'driver-monitor-user').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.plan()
        self.assertFalse((self.base / 'layout.json').exists())

    def test_swap_asset_parent_to_symlink_after_plan_rejected(self):
        plan = self.plan()
        asset = next(x for x in plan['changes'] if Path(x['path']).name == 'driver-monitor.js')
        Path(asset['path']).parent.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            manage.apply(plan)
        self.assertFalse((self.base / 'driver-monitor.js').exists())

    def test_state_dir_must_be_outside_webroot(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            manage.new_plan(self.args(), self.fluidd / 'public-backups')

    def test_state_cannot_be_retargeted_to_another_instance(self):
        self.install()
        with self.assertRaisesRegex(ValueError, 'one instance'):
            self.plan('update', '--klipper', str(self.base / 'other-klipper'))

    def test_frontend_only_does_not_require_lyx_or_printer_cfg(self):
        for path in self.extras.iterdir():
            path.unlink()
        args = manage.parser().parse_args(['install', '--frontend-only', '--fluidd', str(self.fluidd),
               '--hostname', 'printer', '--origin', 'http://printer.test'])
        plan = manage.new_plan(args, self.state_dir)
        self.assertFalse(plan['restart_required'])
        self.assertFalse(any(x['component'] == 'backend' for x in plan['changes']))
        manage.apply(plan)

    def test_doctor_distinguishes_files_from_runtime(self):
        self.install()
        with patch.object(manage, 'runtime_status', return_value={'monitor': {'status': 'pending', 'error': 'HTTP 401'}}):
            result = manage.doctor(self.args('doctor'), self.state_dir)
        self.assertEqual('files_ok', result['status'])
        self.assertFalse(result['monitor_loaded'])
        with patch.object(manage, 'runtime_status', return_value={'monitor': {'status': {'driver_monitor': {'drivers': {}}}}}):
            result = manage.doctor(self.args('doctor'), self.state_dir)
        self.assertTrue(result['monitor_loaded'])
        with patch.object(manage, 'runtime_status', return_value={
                'printer_info': {'hostname': 'another-printer'},
                'monitor': {'status': {'driver_monitor': {'drivers': {}}}}}):
            result = manage.doctor(self.args('doctor'), self.state_dir)
        self.assertFalse(result['monitor_loaded'])
        self.assertEqual('wrong_moonraker_instance', result['findings'][0]['status'])


if __name__ == '__main__':
    unittest.main()
