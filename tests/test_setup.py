"""Interactive installation is exercised on temporary directories only."""
import argparse
import contextlib
import importlib.util
import io
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('monitor_setup', ROOT / 'scripts/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.klipper = self.base / 'custom source with spaces'
        self.extras = self.klipper / 'klippy/extras'
        self.extras.mkdir(parents=True)
        for name in setup.installer.LYX_FILES:
            shutil.copy2(ROOT / 'vendor/lyx' / name, self.extras / name)
        self.fluidd = self.base / 'custom web with spaces'
        self.fluidd.mkdir()
        self.index = self.fluidd / 'index.html'
        self.original = b'<html><body><main>Existing Fluidd</main></body></html>\n'
        self.index.write_bytes(self.original)
        self.backups = self.base / 'private backups'
        self.messages = []
        self.prompts = []
        self.options = argparse.Namespace(
            command='install', components='all', drivers='lyx', with_lyx=False,
            klipper=str(self.klipper), fluidd=str(self.fluidd),
            hostname='printer-demo', origin=['http://printer.example'],
            api_url=None, backup_dir=str(self.backups))

    def answers(self, values):
        sequence = iter(values)
        def answer(prompt):
            self.prompts.append(prompt)
            return next(sequence)
        return answer

    def assert_unmodified(self):
        self.assertEqual(self.original, self.index.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertFalse(self.backups.exists())

    def test_discovery_is_bounded_and_deduplicates_actual_directories(self):
        home1, home2 = self.base / 'user1', self.base / 'user2'
        first = home1 / 'klipper'
        second = home2 / 'klipper'
        for path in (first, second):
            (path / 'klippy/extras').mkdir(parents=True)
        # A source in an unrelated nested folder must not be found by crawling.
        (home1 / 'projects/unrelated/klippy/extras').mkdir(parents=True)
        valid = setup.valid_directory
        checked = []
        def only_fixture_paths(kind, value):
            path = Path(value)
            checked.append(path)
            if self.base not in path.parents:
                raise OSError('not part of the test host')
            return valid(kind, value)
        with patch.object(setup, 'valid_directory', side_effect=only_fixture_paths):
            found = setup.discover_paths('klipper', homes=[home1, home2, home1])
        self.assertEqual(sorted([first.resolve(), second.resolve()], key=str), found)
        self.assertNotIn(home1 / 'projects/unrelated', checked)

    def test_default_discovery_never_enumerates_home_or_other_user_directories(self):
        current_home = self.base / 'current-user'
        source = current_home / 'klipper'
        (source / 'klippy/extras').mkdir(parents=True)
        valid = setup.valid_directory
        def only_current_fixture(kind, value):
            if Path(value) != source:
                raise OSError('outside the synthetic current-user installation')
            return valid(kind, value)
        with patch.object(setup.Path, 'home', return_value=current_home), \
                patch.object(setup.Path, 'iterdir', side_effect=AssertionError('must not enumerate')) as listing, \
                patch.object(setup, 'valid_directory', side_effect=only_current_fixture):
            found = setup.discover_paths('klipper')
        listing.assert_not_called()
        self.assertEqual([source.resolve()], found)

    def test_multiple_candidates_require_explicit_selection(self):
        second = self.base / 'second klipper'
        (second / 'klippy/extras').mkdir(parents=True)
        result = setup.choose_path('klipper', [self.klipper, second],
                                   self.answers(['', '2']), self.messages.append)
        self.assertEqual(str(second.resolve()), result)
        self.assertEqual(2, len(self.prompts))
        self.assertTrue(any('多个候选不会自动选择' in line for line in self.messages))

    def test_no_candidate_accepts_verified_custom_path_with_spaces(self):
        result = setup.choose_path('klipper', [],
                                   self.answers(['', str(self.base / 'missing'), str(self.klipper)]),
                                   self.messages.append)
        self.assertEqual(str(self.klipper.resolve()), result)
        self.assertEqual(3, len(self.prompts))
        self.assertTrue(any('路径不可用' in line for line in self.messages))

    def test_path_that_exists_without_component_marker_is_refused(self):
        unrelated = self.base / 'not-a-web-root'
        unrelated.mkdir()
        result = setup.choose_path('fluidd', [],
                                   self.answers([str(unrelated), str(self.fluidd)]),
                                   self.messages.append)
        self.assertEqual(str(self.fluidd.resolve()), result)
        self.assertEqual(2, len(self.prompts))

    def test_backend_lyx_options_do_not_request_web_identity(self):
        with patch.object(setup, 'discover_paths', return_value=[]) as discover:
            options = setup.collect_options(
                self.answers(['2', str(self.klipper), str(self.backups)]),
                self.messages.append)
        self.assertEqual('backend', options.components)
        self.assertEqual('lyx', options.drivers)
        self.assertTrue(options.with_lyx)
        self.assertIsNone(options.fluidd)
        self.assertIsNone(options.hostname)
        self.assertIsNone(options.origin)
        self.assertIsNone(options.api_url)
        self.assertEqual(str(self.backups), options.backup_dir)
        discover.assert_called_once_with('klipper')
        self.assertFalse(any('hostname' in prompt for prompt in self.prompts))

    def test_backend_lyx_selection_explicitly_requests_matching_modules(self):
        with patch.object(setup, 'discover_paths', return_value=[]):
            options = setup.collect_options(
                self.answers(['2', str(self.klipper), '']), self.messages.append)
        self.assertTrue(options.with_lyx)
        self.assertEqual('lyx', options.drivers)
        self.assertTrue(any('不会生成或刷写固件' in line for line in self.messages))
        self.assertFalse(any('驱动依赖' in prompt for prompt in self.prompts))
        self.assertTrue(any('仅显示 LYX' in line for line in self.messages))

    def test_fluidd_only_options_do_not_discover_klipper_or_install_lyx(self):
        with patch.object(setup, 'discover_paths', return_value=[]) as discover, \
                patch.object(setup.socket, 'gethostname', return_value='reference-host'):
            options = setup.collect_options(self.answers([
                '3', str(self.fluidd), 'printer-demo',
                'http://printer.example https://printer.example',
                'http://printer.example:7125', '']), self.messages.append)
        self.assertEqual('fluidd', options.components)
        self.assertIsNone(options.klipper)
        self.assertFalse(options.with_lyx)
        self.assertEqual('printer-demo', options.hostname)
        self.assertEqual(['http://printer.example', 'https://printer.example'], options.origin)
        self.assertEqual(['http://printer.example:7125'], options.api_url)
        discover.assert_called_once_with('fluidd')
        self.assertFalse(any('驱动依赖' in prompt for prompt in self.prompts))

    def test_plan_only_never_prompts_for_confirmation_or_creates_backups(self):
        with patch.object(setup, 'collect_options', return_value=self.options), \
                patch.object(setup.installer, 'apply_plan') as apply:
            result = setup.run_setup(plan_only=True,
                                     input_fn=self.answers([]), output=self.messages.append)
        self.assertEqual('plan', result['status'])
        self.assertEqual(7, len(result['files']))
        apply.assert_not_called()
        self.assertEqual([], self.prompts)
        self.assert_unmodified()

    def test_only_exact_yes_performs_install_and_receipt_can_roll_it_back(self):
        for answer in ('', 'y', 'Y', 'YES', 'true', 'yes please'):
            with self.subTest(answer=answer), \
                    patch.object(setup, 'collect_options', return_value=self.options):
                result = setup.run_setup(input_fn=self.answers([answer]), output=self.messages.append)
                self.assertEqual('cancelled', result['status'])
                self.assert_unmodified()
        with patch.object(setup, 'collect_options', return_value=self.options), \
                contextlib.redirect_stdout(io.StringIO()):
            result = setup.run_setup(input_fn=self.answers(['yes']), output=self.messages.append)
        self.assertEqual('installed', result['status'])
        self.assertEqual(7, result['files'])
        self.assertIn(setup.installer.BEGIN.encode(), self.index.read_bytes())
        setup.installer.rollback(result['receipt'], apply=True)
        self.assertEqual(self.original, self.index.read_bytes())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())

    def test_unchanged_install_does_not_ask_for_write_confirmation(self):
        plan = setup.installer.build_plan(self.options)
        with contextlib.redirect_stdout(io.StringIO()):
            setup.installer.apply_plan(plan, self.backups)
        with patch.object(setup, 'collect_options', return_value=self.options), \
                patch.object(setup.installer, 'apply_plan') as apply:
            result = setup.run_setup(input_fn=self.answers([]), output=self.messages.append)
        self.assertEqual('unchanged', result['status'])
        apply.assert_not_called()
        self.assertEqual([], self.prompts)

    def test_confirmation_applies_the_exact_reviewed_plan(self):
        plan = setup.installer.build_plan(self.options)
        with patch.object(setup, 'collect_options', return_value=self.options), \
                patch.object(setup.installer, 'build_plan', return_value=plan) as build, \
                patch.object(setup.installer, 'apply_plan', wraps=setup.installer.apply_plan) as apply, \
                contextlib.redirect_stdout(io.StringIO()):
            setup.run_setup(input_fn=self.answers(['yes']), output=self.messages.append)
        build.assert_called_once_with(self.options)
        self.assertIs(plan, apply.call_args.args[0])
        self.assertEqual(str(self.backups), apply.call_args.args[1])

    def test_change_during_confirmation_refuses_entire_plan(self):
        def concurrent_edit(prompt):
            self.index.write_text('another editor changed this file')
            return 'yes'
        with patch.object(setup, 'collect_options', return_value=self.options), \
                self.assertRaisesRegex(ValueError, 'File changed since planning'):
            setup.run_setup(input_fn=concurrent_edit, output=self.messages.append)
        self.assertEqual('another editor changed this file', self.index.read_text())
        self.assertFalse((self.extras / 'driver_monitor.py').exists())
        self.assertFalse(self.backups.exists())

    def test_invalid_target_does_not_reach_confirmation_or_install(self):
        self.options.hostname = ''
        with patch.object(setup, 'collect_options', return_value=self.options), \
                patch.object(setup.installer, 'apply_plan') as apply, \
                self.assertRaises(ValueError):
            setup.run_setup(input_fn=self.answers([]), output=self.messages.append)
        apply.assert_not_called()
        self.assert_unmodified()

    def test_noninteractive_input_is_refused_before_discovery(self):
        with patch.object(setup.sys.stdin, 'isatty', return_value=False), \
                patch.object(setup, 'run_setup') as run, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            setup.main(['--plan'])
        self.assertEqual(2, exc.exception.code)
        run.assert_not_called()
        self.assert_unmodified()

    def test_eof_and_keyboard_interrupt_do_not_turn_into_install_authorization(self):
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), \
                    patch.object(setup, 'collect_options', return_value=self.options), \
                    patch.object(setup.installer, 'apply_plan') as apply, \
                    self.assertRaises(type(error)):
                def interrupted(prompt):
                    raise error
                setup.run_setup(input_fn=interrupted, output=self.messages.append)
            apply.assert_not_called()
            self.assert_unmodified()


if __name__ == '__main__':
    unittest.main()
