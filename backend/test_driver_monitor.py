"""Offline contract tests. No Klipper installation or device connection."""

import hashlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'driver_monitor', Path(__file__).with_name('driver_monitor.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# Load the pinned host implementation shipped with this repository. Only UART
# transport and sleep clock are faked; native retries, locking and the actual
# LYX_READ_REG command implementation remain real in the integration tests.
NATIVE_DIR = Path(__file__).resolve().parents[1] / 'vendor' / 'lyx'
NATIVE_SHA256 = {
    'lyx.py': 'c2d0eb148237b851836fbebcfdf4c85ab003466d73ad3b53bf6761e26ddf8ded',
    'lyx_uart.py': '27289a755fdb413f34ebfc290c16d1e5969308540a5da9a5138b47fd32827abc',
    'lyx9231.py': '8e2fdf3fa4e5a547beb5953a56716010546347b2096d906cb92f849765d073bc',
}
for native_filename, expected_sha256 in NATIVE_SHA256.items():
    native_path = NATIVE_DIR / native_filename
    if hashlib.sha256(native_path.read_bytes()).hexdigest() != expected_sha256:
        raise RuntimeError('Native test fixture hash mismatch: %s' % native_filename)
native_package = types.ModuleType('_driver_monitor_native_extras')
native_package.__path__ = [str(NATIVE_DIR)]
sys.modules[native_package.__name__] = native_package
native_uart = importlib.import_module(native_package.__name__ + '.lyx_uart')
native_lyx9231 = importlib.import_module(native_package.__name__ + '.lyx9231')


class CommandError(Exception):
    pass


class FakeReactor:
    NEVER = 1e16

    def __init__(self):
        self.now = 100.0
        self.timers = []
        self.on_pause = None
        self.pauses = []

    def monotonic(self):
        return self.now

    def register_timer(self, callback, when=NEVER):
        timer = {'callback': callback, 'when': when}
        self.timers.append(timer)
        return timer

    def update_timer(self, timer, when):
        timer['when'] = when

    def pause(self, when):
        self.pauses.append((self.now, when))
        if self.on_pause is not None:
            self.on_pause()
        self.now = max(self.now, when)
        return self.now

    def fire(self):
        timer = min(self.timers, key=lambda t: t['when'])
        if timer['when'] == self.NEVER:
            return False
        self.now = max(self.now, timer['when'])
        timer['when'] = timer['callback'](self.now)
        return True


class FakeMutex:
    def __init__(self):
        self.locked = False
        self.acquisitions = 0

    def test(self):
        return self.locked

    def __enter__(self):
        if self.locked:
            raise AssertionError('Must reject busy mutex without waiting')
        self.locked = True
        self.acquisitions += 1

    def __exit__(self, exc_type, exc, tb):
        self.locked = False


class FakeUART:
    def __init__(self, reactor):
        self.reactor = reactor
        self.mutex = FakeMutex()
        self.calls = []
        self.answer = {'data': 960}
        self.failure = None
        self.on_read = None

    def reg_read(self, address, register):
        if not self.mutex.locked:
            raise AssertionError('Read submitted without existing bus mutex')
        self.calls.append((address, register, self.reactor.now))
        self.reactor.now += 0.008
        if self.on_read is not None:
            self.on_read()
        if self.failure is not None:
            raise self.failure
        return self.answer

    def reg_write(self, *args):
        raise AssertionError('Monitoring must never write registers')


class FakeDriver:
    name_to_reg = {
        'CHIP_MODEL': 3, 'RUN_CURRENT': 19, 'ALARM_CODE': 8,
        'MOTOR_SPEED': 14, 'ERROR_ANGLE': 16}

    def __init__(self, uart, address):
        self.mcu_lyx = self
        self.mcu_uart = uart
        self.addr = address
        self.register_calls = []

    def get_register(self, register):
        # Lightweight contract stub for scheduler tests. Retry behavior is
        # separately exercised with the actual device MCU_LYX_uart class.
        self.register_calls.append(register)
        with self.mcu_uart.mutex:
            return self.mcu_uart.reg_read(self.addr, self.name_to_reg[register])['data']


class FakeStatus:
    def __init__(self, **status):
        self.status = status

    def get_status(self, eventtime):
        return self.status


class FakeEnableTracking:
    def __init__(self, owner, name, reactor):
        self.owner, self.name, self.reactor = owner, name, reactor
        self.callbacks = []
        self.mcu_offset = 0.0
        self.stepper = types.SimpleNamespace(get_mcu=lambda: self)

    def estimated_print_time(self, eventtime):
        return eventtime + self.mcu_offset

    def register_state_callback(self, callback):
        self.callbacks.append(callback)

    def is_motor_enabled(self):
        return self.owner.status['steppers'][self.name]

    def set_enabled(self, enabled, print_time=None):
        if enabled == self.is_motor_enabled():
            return
        if print_time is None:
            print_time = self.estimated_print_time(self.reactor.now)
        # Real EnableTracking notifies before updating its boolean state.
        for callback in self.callbacks:
            callback(print_time, enabled)
        self.owner.status['steppers'][self.name] = enabled


class FakeStepperEnable(FakeStatus):
    def __init__(self, reactor):
        super().__init__(steppers={'stepper_x': True, 'stepper_y': False})
        self.trackers = {name: FakeEnableTracking(self, name, reactor)
                         for name in self.status['steppers']}

    def lookup_enable(self, name):
        return self.trackers[name]


class FakeToolhead:
    def __init__(self, reactor):
        self.reactor = reactor
        self.last_move_time = None

    def get_last_move_time(self):
        return (self.reactor.now if self.last_move_time is None
                else self.last_move_time)


class FakeGcode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, handler, desc=None):
        self.commands[name] = handler


class FakePrinter:
    command_error = CommandError

    def __init__(self):
        self.reactor = FakeReactor()
        self.uart = FakeUART(self.reactor)
        self.events = {}
        self.shutdown_messages = []
        self.objects = {
            'gcode': FakeGcode(),
            'webhooks': FakeStatus(state='ready'),
            'print_stats': FakeStatus(state='standby'),
            'pause_resume': FakeStatus(is_paused=False),
            # Enabled motors are permitted; no monitor action changes them.
            'stepper_enable': FakeStepperEnable(self.reactor),
            'toolhead': FakeToolhead(self.reactor),
            'lyx9231 stepper_x': FakeDriver(self.uart, 1),
            'lyx9231 stepper_y': FakeDriver(self.uart, 2),
        }

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {}

    def register_event_handler(self, name, handler):
        self.events[name] = handler

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self, prefix):
        return [(name, obj) for name, obj in self.objects.items()
                if name.split()[0] == prefix]

    def invoke_shutdown(self, message):
        self.shutdown_messages.append(message)
        self.objects['webhooks'].status['state'] = 'shutdown'
        self.events['klippy:shutdown']()


class FakeConfig:
    def __init__(self, printer, **values):
        self.printer = printer
        self.values = values

    def get_printer(self):
        return self.printer

    def error(self, message):
        return ValueError(message)

    def getboolean(self, name, default):
        return self.values.get(name, default)

    def getfloat(self, name, default, minval):
        value = self.values.get(name, default)
        if value < minval:
            raise ValueError('Below config minimum')
        return value


class FakeCommand:
    def __init__(self, stepper='stepper_x', register='RUN_CURRENT', enable=None):
        self.params = {'STEPPER': stepper, 'REGISTER': register, 'ENABLE': enable}
        self.responses = []

    def get(self, name):
        return self.params[name]

    def get_int(self, name, minval, maxval):
        value = self.params[name]
        if type(value) is not int or not minval <= value <= maxval:
            raise CommandError('Invalid integer')
        return value

    def error(self, message):
        return CommandError(message)

    def respond_info(self, message):
        self.responses.append(message)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.monitor = module.load_config(FakeConfig(self.printer))
        self.uart = self.printer.uart
        self.reactor = self.printer.reactor
        self.log_warning = patch.object(module.logging, 'warning').start()
        self.log_info = patch.object(module.logging, 'info').start()
        self.log_error = patch.object(module.logging, 'error').start()
        self.addCleanup(patch.stopall)

    def ready(self):
        self.printer.events['klippy:ready']()

    def read(self, **kwargs):
        command = FakeCommand(**kwargs)
        self.monitor.cmd_DRIVER_MONITOR_READ(command)
        return command

    def status(self):
        return self.monitor.get_status(self.reactor.now)

    def native_driver(self, stepper='stepper_x', uart=None, address=1):
        uart = self.uart if uart is None else uart
        driver = native_uart.MCU_LYX_uart.__new__(native_uart.MCU_LYX_uart)
        driver.printer = self.printer
        driver.name = stepper
        driver.name_to_reg = native_lyx9231.Registers
        driver.addr = address
        driver.mcu_uart = uart
        driver.mutex = uart.mutex
        command_driver = native_lyx9231.LYX9231.__new__(native_lyx9231.LYX9231)
        command_driver.mcu_lyx = driver
        self.printer.objects['lyx9231 ' + stepper] = command_driver
        return command_driver

    def test_monitor_matches_native_command_after_invalid_then_valid_reply(self):
        command_driver = self.native_driver()
        def replay():
            values = iter((None, None, 0))
            self.uart.on_read = lambda: setattr(
                self.uart, 'answer', {'data': next(values)})
        with patch.object(native_uart.time, 'sleep'):
            replay()
            command = FakeCommand(register='ALARM_CODE')
            command_driver.cmd_LYX_READ_REG(command)
            self.assertEqual(command.responses, ['ALARM_CODE (0x08) = 0'])
            self.assertEqual(len(self.uart.calls), 3)
            self.uart.calls.clear()
            replay()
            self.ready()
            self.read(register='ALARM_CODE')
        result = self.status()['last_result']
        self.assertEqual((result['outcome'], result['value']), ('ok', 0))
        self.assertEqual(len(self.uart.calls), 3)
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 1, 'ok': 1, 'invalid': 0, 'error': 0})
        self.assertEqual(self.status()['stats_unit'], 'logical_transactions')
        self.assertEqual(self.uart.mutex.acquisitions, 2)  # Native + monitor.
        self.assertFalse(self.uart.mutex.locked)

    def test_native_exhaustion_is_one_error_transaction_and_stops_batch(self):
        self.native_driver()
        self.ready()
        self.uart.answer = {'data': None}
        def retry_delay(seconds):
            self.reactor.now += seconds
        with patch.object(native_uart.time, 'sleep', side_effect=retry_delay) as sleep:
            with self.assertRaises(CommandError):
                self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.assertEqual(len(self.uart.calls), 1000)
        self.assertEqual(set(r for a, r, t in self.uart.calls), {8})
        self.assertEqual(sleep.call_count, 1000)
        self.assertEqual(self.uart.mutex.acquisitions, 1)
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.status()['active'])
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 1, 'ok': 0, 'invalid': 0, 'error': 1})
        result = self.status()['last_result']
        self.assertIsNone(result['value'])
        self.assertEqual(result['error_type'], 'CommandError')
        self.assertIn("Unable to read lyx uart 'stepper_x' register ALARM_CODE",
                      result['error_message'])
        self.assertEqual(set(self.status()['readings']['stepper_x']), {'ALARM_CODE'})
        self.assertAlmostEqual(self.monitor.next_read_at, result['ended'] + .2)

    def test_native_retrying_transactions_finish_before_next_register(self):
        command_driver = self.native_driver()
        self.ready()
        counts = {}
        transactions = []
        native_get = command_driver.mcu_lyx.get_register
        def observed_get(register):
            self.assertFalse(self.uart.mutex.locked)  # Native owns the only lock.
            started = self.reactor.now
            try:
                return native_get(register)
            finally:
                transactions.append((register, started, self.reactor.now))
        def slow_reply():
            register = self.uart.calls[-1][1]
            counts[register] = counts.get(register, 0) + 1
            self.reactor.now += .35
            self.uart.answer = {'data': 0 if counts[register] == 3 else None}
            calls_before = len(self.uart.calls)
            self.monitor._auto_timer(self.reactor.now)
            self.assertEqual(len(self.uart.calls), calls_before)
        self.uart.on_read = slow_reply
        with patch.object(command_driver.mcu_lyx, 'get_register',
                          side_effect=observed_get) as logical_read:
            with patch.object(native_uart.time, 'sleep', side_effect=lambda s:
                              setattr(self.reactor, 'now', self.reactor.now + s)):
                self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.assertEqual(logical_read.call_count, 3)
        self.assertEqual([r for a, r, t in self.uart.calls], [8] * 3 + [14] * 3 + [16] * 3)
        self.assertEqual([r for r, start, end in transactions],
                         ['ALARM_CODE', 'MOTOR_SPEED', 'ERROR_ANGLE'])
        for previous, following in zip(transactions, transactions[1:]):
            self.assertGreaterEqual(following[1] - previous[2], .2 - 1e-10)
        self.assertEqual(self.uart.mutex.acquisitions, 3)
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 3, 'ok': 3, 'invalid': 0, 'error': 0})
        for register in module.DYNAMIC_REGISTERS:
            result = self.status()['readings']['stepper_x'][register]
            self.assertGreater(result['duration'], 1.0)
            self.assertEqual(result['value'], 0)
        self.assertEqual(len(self.status()['history']['stepper_x']['MOTOR_SPEED']), 1)

    def test_native_pause_finishes_current_transaction_before_stopping_batch(self):
        self.native_driver()
        self.ready()
        def reply_and_pause():
            count = len(self.uart.calls)
            self.uart.answer = {'data': 2316 if count == 3 else None}
            if count == 1:
                self.monitor.cmd_DRIVER_MONITOR_AUTO(FakeCommand(enable=0))
        self.uart.on_read = reply_and_pause
        with patch.object(native_uart.time, 'sleep'):
            self.reactor.fire()
        self.assertEqual([r for a, r, t in self.uart.calls], [3, 3, 3])
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 1, 'ok': 1, 'invalid': 0, 'error': 0})
        self.assertEqual(self.status()['stats']['stepper_y']['attempts'], 0)
        self.assertFalse(self.status()['auto_enabled'])
        self.assertFalse(self.status()['active'])
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.reactor.fire())

    def test_native_busy_bus_is_rejected_before_entering_transaction(self):
        command_driver = self.native_driver()
        self.ready()
        self.uart.mutex.locked = True
        with patch.object(command_driver.mcu_lyx, 'get_register',
                          wraps=command_driver.mcu_lyx.get_register) as logical_read:
            with self.assertRaises(CommandError):
                self.read()
        logical_read.assert_not_called()
        self.assertEqual(self.uart.calls, [])
        self.assertEqual(self.status()['stats']['stepper_x']['attempts'], 0)
        self.assertTrue(self.uart.mutex.locked)

    def test_discovery_and_status_have_no_io(self):
        self.assertEqual(self.status()['drivers'], [])
        self.ready()
        for _ in range(20):
            status = self.status()
        self.assertEqual([d['stepper'] for d in status['drivers']],
                         ['stepper_x', 'stepper_y'])
        self.assertIsNone(status['last_result'])
        self.assertEqual(status['schema_version'], 1)
        self.assertEqual(status['min_interval'], 0.0)
        self.assertEqual(self.uart.calls, [])

    def test_klipper_statistics_discovery_does_not_register_status_dictionary(self):
        # statistics.PrinterStats discovers any object with a `stats`
        # attribute, then calls it as stats(eventtime).  Status dictionaries
        # must not accidentally opt this object into that callback protocol.
        class ExistingStatsProvider:
            def stats(self, eventtime):
                return True, 'existing_stats=1'

        self.ready()
        objects = [('driver_monitor', self.monitor),
                   ('existing', ExistingStatsProvider())]
        callbacks = [obj.stats for name, obj in objects
                     if hasattr(obj, 'stats')]
        emitted = [callback(self.reactor.now) for callback in callbacks]
        self.assertEqual(emitted, [(True, 'existing_stats=1')])
        self.assertEqual(self.uart.calls, [])
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 0, 'ok': 0, 'invalid': 0, 'error': 0})

    def test_success_calls_one_native_transaction_and_allows_enabled_motor(self):
        self.ready()
        self.read(register='run_current')
        self.assertEqual(self.uart.calls, [(1, 19, 100.0)])
        self.assertEqual(self.printer.objects['lyx9231 stepper_x'].register_calls,
                         ['RUN_CURRENT'])
        result = self.status()['last_result']
        self.assertEqual((result['seq'], result['value'], result['outcome']),
                         (1, 960, 'ok'))
        self.assertAlmostEqual(result['duration'], .008)
        self.assertFalse(self.uart.mutex.locked)
        self.assertTrue(self.printer.objects['stepper_enable'].status[
            'steppers']['stepper_x'])

    def test_next_manual_read_is_accepted_and_waits_gap_without_cooldown_rejection(self):
        self.ready()
        self.read()
        ended = self.status()['last_result']['ended']
        self.assertEqual(self.status()['next_allowed_at'], 0.0)
        self.assertEqual(len(self.uart.calls), 1)
        self.assertEqual(self.status()['last_result']['seq'], 1)
        self.read()
        self.assertEqual(len(self.uart.calls), 2)
        self.assertEqual(self.uart.calls[-1], (1, 19, ended + .2))
        self.assertEqual(self.status()['last_result']['seq'], 2)
        self.read(stepper='stepper_y')
        self.assertEqual(len(self.uart.calls), 3)
        self.assertEqual(self.uart.calls[-1][0], 2)

    def test_defensive_none_result_is_not_requeried_and_replaces_old_value(self):
        self.ready()
        self.read()
        self.uart.answer = {'data': None}
        self.read()
        self.assertEqual(len(self.uart.calls), 2)
        result = self.status()['last_result']
        self.assertEqual(result['outcome'], 'invalid')
        self.assertIsNone(result['value'])
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 2, 'ok': 1, 'invalid': 1, 'error': 0})
        self.assertEqual(self.status()['cooldown_remaining'], 0.0)

    def test_exception_releases_mutex_without_transaction_retry_or_cooldown(self):
        self.ready()
        self.uart.failure = RuntimeError('simulated MCU transport timeout')
        with patch.object(module.logging, 'exception'):
            with self.assertRaises(CommandError):
                self.read()
        self.assertEqual(len(self.uart.calls), 1)
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.status()['active'])
        self.assertEqual(self.status()['last_result']['outcome'], 'error')
        self.assertIsNone(self.status()['last_result']['value'])
        self.assertEqual(self.status()['cooldown_remaining'], 0.0)
        self.uart.failure = None
        self.read()
        self.assertEqual(len(self.uart.calls), 2)
        self.assertEqual(self.status()['last_result']['outcome'], 'ok')

    def test_busy_mutex_rejected_without_io_or_result_change(self):
        self.ready()
        self.uart.mutex.locked = True
        with self.assertRaises(CommandError):
            self.read()
        self.assertTrue(self.uart.mutex.locked)
        self.assertEqual(self.uart.calls, [])
        self.assertIsNone(self.status()['last_result'])
        self.assertEqual(self.status()['next_allowed_at'], 0.0)

    def test_not_ready_and_shutdown_rejected_before_io(self):
        with self.assertRaises(CommandError):
            self.read()
        self.ready()
        self.printer.objects['webhooks'].status['state'] = 'shutdown'
        with self.assertRaises(CommandError):
            self.read()
        self.printer.objects['webhooks'].status['state'] = 'ready'
        self.printer.events['klippy:shutdown']()
        with self.assertRaises(CommandError):
            self.read()
        self.assertEqual(self.uart.calls, [])

    def test_printing_and_paused_reads_are_permitted(self):
        self.ready()
        for state in ('printing', 'paused'):
            self.printer.objects['print_stats'].status['state'] = state
            self.read()
        self.printer.objects['pause_resume'].status['is_paused'] = True
        self.read()
        self.assertEqual(len(self.uart.calls), 3)

    def test_unsupported_register_or_stepper_rejected_before_io(self):
        self.ready()
        for register in ('SAVE_PARAM', 'BAUDRATE', '0x13'):
            with self.assertRaises(CommandError):
                self.read(register=register)
        with self.assertRaises(CommandError):
            self.read(stepper='extruder')
        self.assertEqual(self.uart.calls, [])

    def test_status_is_detached_and_does_not_read_or_add_cooldown(self):
        self.ready()
        self.read()
        status = self.status()
        deadline = status['next_allowed_at']
        status['last_result']['value'] = 7
        status['stats']['stepper_x']['ok'] = 90
        status['drivers'][0]['registers'].clear()
        self.reactor.now += 3.0
        for _ in range(20):
            status = self.status()
        self.assertEqual(len(self.uart.calls), 1)
        self.assertEqual(status['last_result']['value'], 960)
        self.assertEqual(status['stats']['stepper_x']['ok'], 1)
        self.assertEqual(len(status['drivers'][0]['registers']), 5)
        self.assertEqual(status['next_allowed_at'], deadline)
        self.assertEqual(status['cooldown_remaining'], 0.0)

    def test_malformed_transport_value_is_not_published_as_valid(self):
        self.ready()
        self.uart.answer = {'data': True}
        with patch.object(module.logging, 'exception'):
            with self.assertRaises(CommandError):
                self.read()
        self.assertEqual(len(self.uart.calls), 1)
        self.assertEqual(self.status()['last_result']['outcome'], 'error')
        self.assertIsNone(self.status()['last_result']['value'])

    def test_startup_without_browser_reads_five_once_per_driver(self):
        self.assertEqual(len(self.reactor.timers), 1)
        self.assertFalse(self.reactor.fire())
        self.ready()
        self.assertEqual(self.uart.calls, [])
        self.assertTrue(self.reactor.fire())
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(a, r) for a in (1, 2) for r in (3, 19, 8, 14, 16)])
        self.assertEqual(set(self.status()['readings']['stepper_x']),
                         set(module.READ_REGISTERS))
        self.assertAlmostEqual(self.monitor.cycle_due, self.reactor.now + 1)

    def test_each_round_has_fixed_order_and_late_tick_never_catches_up(self):
        self.ready()
        self.reactor.fire()
        self.uart.calls.clear()
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(a, r) for a in (1, 2) for r in (8, 14, 16)])
        self.uart.calls.clear()
        # Skip several timer deadlines: one ordered round, no backlog.
        self.reactor.now = self.monitor.cycle_due + 50
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(a, r) for a in (1, 2) for r in (8, 14, 16)])
        self.assertFalse(self.status()['cycle_active'])
        self.assertEqual(self.status()['read_order'],
                         ['ALARM_CODE', 'MOTOR_SPEED', 'ERROR_ANGLE'])
        self.assertEqual(self.status()['cycle_interval'], 1)
        self.assertNotIn('alarm_interval', self.status())
        self.assertNotIn('telemetry_interval', self.status())
        self.assertAlmostEqual(self.monitor.cycle_due, self.reactor.now + 1)

    def test_slow_invalid_reply_finishes_before_next_register_or_round(self):
        self.ready()
        self.reactor.fire()
        self.uart.calls.clear()
        self.uart.answer = {'data': None}
        completed = []
        def slow_reply():
            self.reactor.now += .7
            count = len(self.uart.calls)
            # Simulate a due callback while the synchronous read is waiting.
            self.monitor._auto_timer(self.reactor.now)
            self.assertEqual(len(self.uart.calls), count)
            completed.append(self.reactor.now)
        self.uart.on_read = slow_reply
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(a, r) for a in (1, 2) for r in (8, 14, 16)])
        for ended, following in zip(completed, self.uart.calls[1:]):
            self.assertGreaterEqual(following[2] - ended, .2 - 1e-10)
        self.assertEqual(len(completed), 6)  # Invalid values are not retried.
        self.assertAlmostEqual(self.monitor.cycle_due, completed[-1] + 1)
        before = len(self.uart.calls)
        self.monitor._auto_timer(self.reactor.now)
        self.assertEqual(len(self.uart.calls), before)

    def test_ordered_round_busy_or_error_driver_does_not_starve_other_bus(self):
        other = FakeUART(self.reactor)
        self.printer.objects['lyx9231 stepper_y'] = FakeDriver(other, 2)
        self.ready()
        self.reactor.fire()
        self.uart.calls.clear()
        other.calls.clear()
        self.uart.mutex.locked = True
        self.reactor.fire()
        self.assertEqual(self.uart.calls, [])
        self.assertEqual([r for a, r, t in other.calls], [8, 14, 16])
        self.assertEqual(self.status()['cycle_status'], 'busy')
        self.uart.mutex.locked = False
        self.uart.failure = RuntimeError('transport timeout')
        other.calls.clear()
        self.reactor.fire()
        self.assertEqual([r for a, r, t in self.uart.calls], [8])
        self.assertEqual([r for a, r, t in other.calls], [8, 14, 16])
        self.assertEqual(self.status()['cycle_status'], 'error')
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(other.mutex.locked)

    def test_background_busy_skips_without_failure_and_keeps_startup_pending(self):
        self.ready()
        self.uart.mutex.locked = True
        self.reactor.fire()
        self.assertEqual(self.uart.calls, [])
        self.assertEqual(self.status()['cycle_status'], 'busy')
        self.assertEqual(self.status()['stats']['stepper_x']['attempts'], 0)
        self.assertEqual(self.log_warning.call_count, 0)
        self.uart.mutex.locked = False
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 10)

    def test_auto_pause_and_resume_do_not_read_from_command(self):
        self.ready()
        self.monitor.cmd_DRIVER_MONITOR_AUTO(FakeCommand(enable=0))
        self.assertFalse(self.status()['auto_enabled'])
        self.assertFalse(self.reactor.fire())
        self.assertEqual(self.uart.calls, [])
        self.monitor.cmd_DRIVER_MONITOR_AUTO(FakeCommand(enable=1))
        self.assertTrue(self.status()['auto_enabled'])
        self.assertEqual(self.uart.calls, [])
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 10)

    def test_pause_mid_cycle_finishes_current_read_and_does_not_submit_next(self):
        self.ready()
        self.uart.on_read = lambda: self.monitor.cmd_DRIVER_MONITOR_AUTO(
            FakeCommand(enable=0))
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 1)
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.status()['active'])
        self.assertFalse(self.reactor.fire())

    def test_disconnect_mid_cycle_stops_after_current_read(self):
        self.ready()
        self.uart.on_read = self.printer.events['klippy:disconnect']
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 1)
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.status()['ready'])
        self.assertFalse(self.reactor.fire())

    def test_manual_refresh_updates_exactly_three_and_cannot_overlap_auto(self):
        self.ready()
        self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(1, 8), (1, 14), (1, 16)])
        self.uart.calls.clear()
        rejections = []
        def during_read():
            with self.assertRaises(CommandError):
                self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
            rejections.append(True)
        self.uart.on_read = during_read
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 10)
        self.assertEqual(len(rejections), 10)

    def test_static_empty_transaction_is_not_requeried_and_dynamic_is_null(self):
        self.ready()
        self.uart.answer = {'data': None}
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 10)
        self.uart.calls.clear()
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(a, r) for a in (1, 2) for r in (8, 14, 16)])
        result = self.status()['readings']['stepper_x']['ALARM_CODE']
        self.assertIsNone(result['value'])
        self.assertEqual(result['outcome'], 'invalid')

    def test_error_stops_batch_releases_mutex_and_does_not_repeat_static(self):
        self.ready()
        self.uart.failure = RuntimeError('transport timeout')
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 2)
        self.assertFalse(self.uart.mutex.locked)
        self.assertEqual(self.status()['cycle_status'], 'error')
        self.uart.failure = None
        self.reactor.fire()
        self.assertEqual(len([c for c in self.uart.calls if c[:2] == (1, 3)]), 1)

    def test_first_driver_busy_does_not_starve_independent_driver(self):
        other_uart = FakeUART(self.reactor)
        self.printer.objects['lyx9231 stepper_y'] = FakeDriver(other_uart, 2)
        self.ready()
        self.uart.mutex.locked = True
        self.reactor.fire()
        self.assertEqual(self.uart.calls, [])
        self.assertEqual([r for a, r, t in other_uart.calls], [3, 19, 8, 14, 16])
        self.assertEqual(self.status()['stats']['stepper_x']['attempts'], 0)
        self.assertEqual(self.status()['stats']['stepper_y']['ok'], 5)
        self.assertEqual(self.status()['cycle_status'], 'busy')

    def test_first_driver_error_does_not_starve_independent_driver(self):
        other_uart = FakeUART(self.reactor)
        self.printer.objects['lyx9231 stepper_y'] = FakeDriver(other_uart, 2)
        self.ready()
        self.uart.failure = RuntimeError('transport timeout')
        self.reactor.fire()
        self.assertEqual(len(self.uart.calls), 1)
        self.assertFalse(self.uart.mutex.locked)
        self.assertEqual([r for a, r, t in other_uart.calls], [3, 19, 8, 14, 16])
        failed = self.status()['readings']['stepper_x']['CHIP_MODEL']
        self.assertEqual(failed['error_type'], 'RuntimeError')
        self.assertEqual(failed['error_message'], 'transport timeout')
        self.assertEqual(self.status()['cycle_status'], 'error')

    def test_alarm_change_failure_recovery_events_are_deduplicated(self):
        self.ready()
        for value in (2, 2, None, None, 2, 2, 3, 3):
            self.uart.answer = {'data': value}
            self.read(register='ALARM_CODE')
        self.assertEqual(len(self.uart.calls), 8)
        self.assertEqual(self.log_warning.call_count, 1)
        self.assertEqual(self.log_info.call_count, 3)  # initial, recovery, 2 -> 3
        messages = [args[0] % args[1:] for args, kwargs in self.log_info.call_args_list]
        self.assertTrue(any('old=2 (0x0002) new=3 (0x0003)' in m for m in messages))
        warning = self.log_warning.call_args[0]
        self.assertIn('register=ALARM_CODE no_valid_response', warning[0] % warning[1:])
        self.assertTrue(all('unix=' in m and 'monotonic=' in m for m in messages))
        self.assertIn('monotonic=', warning[0] % warning[1:])

    def test_repeated_communication_exception_logs_only_first_transition(self):
        self.ready()
        self.uart.failure = RuntimeError('transport timeout')
        for _ in range(2):
            with self.assertRaises(CommandError):
                self.read(register='ALARM_CODE')
        self.assertEqual(len(self.uart.calls), 2)
        self.assertEqual(self.log_warning.call_count, 1)
        self.assertEqual(self.status()['stats']['stepper_x']['error'], 2)

    def test_auto_start_false_and_config_minima(self):
        printer = FakePrinter()
        monitor = module.load_config(FakeConfig(printer, auto_start=False))
        printer.events['klippy:ready']()
        self.assertFalse(printer.reactor.fire())
        self.assertEqual(printer.uart.calls, [])
        self.assertFalse(monitor.get_status(100)['auto_enabled'])
        with self.assertRaises(ValueError):
            module.load_config(FakeConfig(FakePrinter(), cycle_interval=0.1))
        with self.assertRaises(ValueError):
            module.load_config(FakeConfig(FakePrinter(), read_gap=0))
        with self.assertRaises(ValueError):
            module.load_config(FakeConfig(FakePrinter(), read_gap=0.009))

    def test_gap_is_global_across_startup_auto_manual_batches_and_drivers(self):
        self.ready()
        self.read()
        self.reactor.fire()  # Remaining static entries plus dynamic startup.
        self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.reactor.fire()  # Next complete dynamic round.
        calls = self.uart.calls
        self.assertEqual(len(calls), 19)
        self.assertEqual(len([c for c in calls if c[:2] == (1, 19)]), 1)
        self.assertEqual(len([c for c in calls if c[:2] == (2, 19)]), 1)
        for previous, current in zip(calls, calls[1:]):
            self.assertGreaterEqual(current[2] - (previous[2] + .008),
                                    .2 - 1e-10)
        self.assertEqual(self.status()['read_gap'], .2)
        self.assertEqual(self.status()['min_interval'], 0)
        self.assertEqual(self.status()['cooldown_remaining'], 0)

    def test_gap_starts_at_read_completion_for_invalid_and_exception(self):
        self.ready()
        self.uart.answer = {'data': None}
        self.uart.on_read = lambda: setattr(self.reactor, 'now',
                                            self.reactor.now + .35)
        self.read()
        invalid_end = self.status()['last_result']['ended']
        self.uart.on_read = None
        self.uart.failure = RuntimeError('simulated error')
        with self.assertRaises(CommandError):
            self.read()
        self.assertAlmostEqual(self.uart.calls[-1][2], invalid_end + .2)
        error_end = self.status()['last_result']['ended']
        self.uart.failure = None
        self.uart.answer = {'data': 960}
        self.read()
        self.assertAlmostEqual(self.uart.calls[-1][2], error_end + .2)
        self.assertEqual(len(self.uart.calls), 3)

    def test_gap_yields_without_uart_mutex_but_keeps_monitor_exclusive(self):
        self.ready()
        self.read()
        observations = []
        def while_waiting():
            self.assertFalse(self.uart.mutex.locked)
            self.assertTrue(self.status()['active'])
            with self.uart.mutex:
                observations.append('other command acquired bus')
            with self.assertRaises(CommandError):
                self.read(stepper='stepper_y')
            before = len(self.uart.calls)
            self.monitor._auto_timer(self.reactor.now)
            self.assertEqual(len(self.uart.calls), before)
        self.reactor.on_pause = while_waiting
        self.read()
        self.assertEqual(observations, ['other command acquired bus'])
        self.assertEqual(len(self.uart.calls), 2)
        self.assertFalse(self.status()['active'])

    def test_shutdown_during_gap_cancels_remaining_manual_reads(self):
        self.ready()
        self.reactor.on_pause = self.printer.events['klippy:shutdown']
        with self.assertRaises(CommandError):
            self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.assertEqual([r for a, r, t in self.uart.calls], [8])
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.status()['active'])
        self.assertFalse(self.status()['ready'])

    def test_auto_pause_during_gap_cancels_remaining_startup_reads(self):
        self.ready()
        self.reactor.on_pause = lambda: self.monitor.cmd_DRIVER_MONITOR_AUTO(
            FakeCommand(enable=0))
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls], [(1, 3)])
        self.assertEqual(self.monitor.startup_pending['stepper_x'],
                         {'RUN_CURRENT'})
        self.assertFalse(self.status()['auto_enabled'])
        self.assertFalse(self.reactor.fire())
        self.assertFalse(self.uart.mutex.locked)

    def test_bus_acquired_during_gap_skips_driver_without_starving_other_bus(self):
        other = FakeUART(self.reactor)
        self.printer.objects['lyx9231 stepper_y'] = FakeDriver(other, 2)
        self.ready()
        self.reactor.on_pause = lambda: setattr(self.uart.mutex, 'locked', True)
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls], [(1, 3)])
        self.assertEqual([r for a, r, t in other.calls], [3, 19, 8, 14, 16])
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 1, 'ok': 1, 'invalid': 0, 'error': 0})
        calls = self.uart.calls + other.calls
        for previous, current in zip(calls, calls[1:]):
            self.assertGreaterEqual(current[2] - (previous[2] + .008),
                                    .2 - 1e-10)
        self.assertEqual(self.status()['cycle_status'], 'busy')
        self.assertTrue(self.uart.mutex.locked)  # Do not release another owner.
        self.assertFalse(other.mutex.locked)

    def test_configured_gap_and_round_wait_are_independent(self):
        printer = FakePrinter()
        monitor = module.load_config(FakeConfig(
            printer, read_gap=.4, cycle_interval=3))
        printer.events['klippy:ready']()
        monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.assertEqual(len(printer.uart.calls), 3)
        self.assertAlmostEqual(printer.uart.calls[1][2]
                               - printer.uart.calls[0][2], .408)
        self.assertEqual(monitor.cycle_interval, 3)
        printer.reactor.fire()
        self.assertAlmostEqual(monitor.cycle_due, printer.reactor.now + 3)

    def test_history_is_bounded_per_driver_and_register_without_extra_reads(self):
        self.assertEqual(module.HISTORY_SECONDS, 600.0)
        self.ready()
        for value in range(605):
            self.uart.answer = {'data': value}
            self.read(register='MOTOR_SPEED')
        self.read(register='ERROR_ANGLE')
        self.read(stepper='stepper_y', register='MOTOR_SPEED')
        self.read(register='ALARM_CODE')
        history = self.status()['history']
        self.assertEqual(len(self.uart.calls), 608)
        speed = history['stepper_x']['MOTOR_SPEED']
        self.assertEqual(len(speed), 600)
        self.assertEqual([p['value'] for p in speed], list(range(5, 605)))
        self.assertEqual(len(history['stepper_x']['ERROR_ANGLE']), 1)
        self.assertEqual(len(history['stepper_y']['MOTOR_SPEED']), 1)
        self.assertNotIn('ALARM_CODE', history['stepper_x'])

    def test_failed_history_samples_remain_null_and_are_not_retried(self):
        self.ready()
        self.read(register='MOTOR_SPEED')
        self.uart.answer = {'data': None}
        self.read(register='MOTOR_SPEED')
        self.uart.failure = RuntimeError('timeout')
        with self.assertRaises(CommandError):
            self.read(register='MOTOR_SPEED')
        self.assertEqual(len(self.uart.calls), 3)
        samples = self.status()['history']['stepper_x']['MOTOR_SPEED']
        self.assertEqual([p['outcome'] for p in samples], ['ok', 'invalid', 'error'])
        self.assertEqual([p['value'] for p in samples], [960, None, None])
        self.assertEqual(samples[-1], self.status()['readings']['stepper_x']['MOTOR_SPEED'])

    def test_history_expiry_and_queries_are_cache_only_and_detached(self):
        self.ready()
        self.read(register='ERROR_ANGLE')
        ended = self.status()['last_result']['ended']
        self.reactor.now = ended + 600.0
        self.assertEqual(len(self.status()['history']['stepper_x']['ERROR_ANGLE']), 1)
        self.reactor.now += .001
        self.assertEqual(self.status()['history']['stepper_x']['ERROR_ANGLE'], [])
        self.assertEqual(len(self.uart.calls), 1)
        self.read(register='ERROR_ANGLE')
        self.assertEqual(len(self.monitor.history['stepper_x']['ERROR_ANGLE']), 1)
        status = self.status()
        status['history']['stepper_x']['ERROR_ANGLE'][0]['value'] = 99
        for _ in range(20):
            self.assertEqual(self.status()['history']['stepper_x']['ERROR_ANGLE'][0]['value'], 960)
        self.assertEqual(len(self.uart.calls), 2)

    def test_ready_starts_new_history_session_without_reading(self):
        self.assertIsNone(self.status()['session_id'])
        self.ready()
        first = self.status()['session_id']
        self.assertIsInstance(first, str)
        self.read(register='MOTOR_SPEED')
        self.assertEqual(self.status()['session_id'], first)
        self.printer.events['klippy:disconnect']()
        self.ready()
        self.assertNotEqual(self.status()['session_id'], first)
        self.assertEqual(self.status()['history']['stepper_x']['MOTOR_SPEED'], [])
        self.assertEqual(len(self.uart.calls), 1)

    def protection_ready(self, initially_enabled=False):
        enables = self.printer.objects['stepper_enable']
        enables.status['steppers']['stepper_x'] = initially_enabled
        self.monitor = module.load_config(FakeConfig(
            self.printer, shutdown_on_alarm=True))
        self.ready()
        return enables.lookup_enable('stepper_x')

    def test_alarm_protection_configuration_keeps_legacy_default(self):
        self.assertFalse(self.status()['shutdown_on_alarm'])
        self.assertIsNone(self.status()['alarm_shutdown'])
        with self.assertRaisesRegex(ValueError, 'auto_start'):
            module.load_config(FakeConfig(
                FakePrinter(), shutdown_on_alarm=True, auto_start=False))

    def test_alarm_shutdown_requires_new_read_after_enable_and_stops_batch(self):
        tracker = self.protection_ready()
        self.uart.answer = {'data': 2}
        self.read(register='ALARM_CODE')  # Disabled alarm remains visible.
        self.assertFalse(self.status()['protection']['stepper_x']['armed'])
        self.assertEqual(self.printer.shutdown_messages, [])
        before = len(self.uart.calls)
        tracker.set_enabled(True)
        self.assertEqual(len(self.uart.calls), before)  # Callback is IO-free.
        self.assertTrue(self.status()['protection']['stepper_x']['armed'])
        self.assertIsNone(self.status()['alarm_shutdown'])  # Old cache is inert.
        with patch.object(module.logging, 'error') as log:
            with self.assertRaisesRegex(CommandError, 'stopped'):
                self.monitor.cmd_DRIVER_MONITOR_REFRESH(FakeCommand())
        self.assertEqual([r for a, r, t in self.uart.calls], [8, 8])
        self.assertFalse(self.uart.mutex.locked)
        self.assertFalse(self.status()['active'])
        self.assertFalse(self.status()['ready'])
        self.assertFalse(self.reactor.fire())
        self.assertEqual(len(self.printer.shutdown_messages), 1)
        fault = self.status()['alarm_shutdown']
        self.assertEqual((fault['stepper'], fault['value'], fault['reason']),
                         ('stepper_x', 2, '电机未连接'))
        self.assertIn('0x0002', fault['message'])
        self.assertIn('断电重新上电', fault['message'])
        self.assertIn('unix=', fault['message'])
        self.assertIn('monotonic=', fault['message'])
        log.assert_called_once_with('%s', fault['message'])
        self.assertEqual(self.status()['last_result']['outcome'], 'ok')
        self.assertEqual(self.status()['stats']['stepper_x']['attempts'], 2)

    def test_alarm_gate_uses_stepper_mcu_clock_at_both_read_boundaries(self):
        tracker = self.protection_ready()
        tracker.mcu_offset = -50
        tracker.set_enabled(True, print_time=51.0)
        self.uart.answer = {'data': 4}
        self.assertFalse(self.status()['protection']['stepper_x']['armed'])
        self.uart.on_read = lambda: setattr(self.reactor, 'now', 101.1)
        self.read(register='ALARM_CODE')  # Started before effective print_time.
        self.assertEqual(self.printer.shutdown_messages, [])
        self.assertTrue(self.status()['protection']['stepper_x']['armed'])
        self.uart.on_read = None
        with self.assertRaises(CommandError):
            self.read(register='ALARM_CODE')
        self.assertEqual(self.status()['alarm_shutdown']['reason'], '超差')

    def test_alarm_reply_spanning_enable_disable_or_reenable_never_trips(self):
        for transition in ('enable', 'disable', 'reenable'):
            with self.subTest(transition=transition):
                self.printer = FakePrinter()
                self.uart = self.printer.uart
                self.reactor = self.printer.reactor
                tracker = self.protection_ready()
                if transition != 'enable':
                    tracker.set_enabled(True)
                self.uart.answer = {'data': 1}
                def change():
                    if transition != 'enable':
                        tracker.set_enabled(False)
                    if transition != 'disable':
                        tracker.set_enabled(True)
                self.uart.on_read = change
                self.read(register='ALARM_CODE')
                self.assertEqual(self.printer.shutdown_messages, [])
                self.assertIsNone(self.status()['alarm_shutdown'])
                self.assertEqual(self.status()['last_result']['value'], 1)

    def test_ready_enabled_state_uses_queue_boundary_not_invented_enable_time(self):
        self.printer.objects['toolhead'].last_move_time = 102.0
        tracker = self.protection_ready(initially_enabled=True)
        state = self.status()['protection']['stepper_x']
        self.assertTrue(state['enabled'])
        self.assertFalse(state['armed'])
        self.assertEqual(state['enable_print_time'], 102.0)
        self.assertEqual(state['enable_time_source'], 'ready_queue_boundary')
        self.uart.answer = {'data': 5}
        self.read(register='ALARM_CODE')
        self.assertEqual(self.printer.shutdown_messages, [])
        self.reactor.now = 102.1
        self.assertTrue(self.status()['protection']['stepper_x']['armed'])
        with self.assertRaises(CommandError):
            self.read(register='ALARM_CODE')
        self.assertEqual(self.status()['alarm_shutdown']['reason'], '堵转')
        self.assertEqual(len(tracker.callbacks), 1)

    def test_startup_protection_reads_alarm_first_and_halts_other_drivers(self):
        tracker = self.protection_ready()
        tracker.set_enabled(True)
        self.uart.answer = {'data': 3}
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls], [(1, 8)])
        self.assertEqual(self.status()['stats']['stepper_y']['attempts'], 0)
        self.assertEqual(self.status()['alarm_shutdown']['reason'], '线圈异常')
        self.assertFalse(self.status()['cycle_active'])
        self.assertFalse(self.reactor.fire())

    def test_protected_zero_alarms_keep_order_gap_and_single_startup_reads(self):
        tracker = self.protection_ready()
        tracker.set_enabled(True)
        self.uart.answer = {'data': 0}
        self.reactor.fire()
        self.assertEqual([(a, r) for a, r, t in self.uart.calls],
                         [(a, r) for a in (1, 2) for r in (8, 14, 16, 3, 19)])
        for previous, following in zip(self.uart.calls, self.uart.calls[1:]):
            self.assertGreaterEqual(following[2] - previous[2] - .008, .2 - 1e-10)
        self.uart.calls.clear()
        self.reactor.fire()
        self.assertEqual([r for a, r, t in self.uart.calls], [8, 14, 16] * 2)
        self.assertEqual(self.printer.shutdown_messages, [])

    def test_alarm_zero_invalid_and_communication_error_are_not_alarms(self):
        tracker = self.protection_ready()
        tracker.set_enabled(True)
        for value in (0, None, True, -1, 65536, '2'):
            self.uart.answer = {'data': value}
            if value is not None and (type(value) is not int or value < 0 or value > 65535):
                with self.assertRaises(CommandError):
                    self.read(register='ALARM_CODE')
            else:
                self.read(register='ALARM_CODE')
        self.uart.failure = RuntimeError('transport timeout')
        with self.assertRaises(CommandError):
            self.read(register='ALARM_CODE')
        self.assertEqual(self.printer.shutdown_messages, [])
        self.assertIsNone(self.status()['alarm_shutdown'])
        self.assertEqual(self.status()['last_result']['error_type'], 'RuntimeError')

    def test_non_alarm_register_never_trips_even_with_protection_armed(self):
        self.protection_ready().set_enabled(True)
        self.uart.answer = {'data': 65535}
        for register in ('MOTOR_SPEED', 'ERROR_ANGLE', 'RUN_CURRENT', 'CHIP_MODEL'):
            self.read(register=register)
        self.assertEqual(self.printer.shutdown_messages, [])

    def test_alarm_native_validation_retries_invalid_frames_before_one_shutdown(self):
        uart = native_uart.MCU_LYX_uart_bitbang.__new__(native_uart.MCU_LYX_uart_bitbang)
        uart.mutex = FakeMutex()
        uart.oid = 12
        raw = bytearray([1, 3, 2, 0, 1])
        crc = uart._crc16(raw)
        good = bytes(raw + bytearray([crc & 255, crc >> 8]))
        replies = iter([good[:-1] + bytes([good[-1] ^ 1]),
                        bytes.fromhex('01830440f3ffff'), good])
        sends = []
        def send(args):
            sends.append(args)
            self.assertEqual(self.printer.shutdown_messages, [])
            self.reactor.now += .05
            return {'read': next(replies)}
        uart.send_cmd = types.SimpleNamespace(send=send)
        self.native_driver(uart=uart)
        self.protection_ready().set_enabled(True)
        with patch.object(native_uart.time, 'sleep'):
            with self.assertRaises(CommandError):
                self.read(register='ALARM_CODE')
        self.assertEqual(len(sends), 3)
        self.assertEqual(self.status()['stats']['stepper_x'],
                         {'attempts': 1, 'ok': 1, 'invalid': 0, 'error': 0})
        self.assertEqual(self.status()['alarm_shutdown']['reason'], '过流')
        self.assertFalse(uart.mutex.locked)
        self.assertEqual(uart.mutex.acquisitions, 1)

    def test_alarm_native_exhaustion_is_communication_error_without_shutdown(self):
        self.native_driver()
        self.protection_ready().set_enabled(True)
        self.uart.answer = {'data': None}
        with patch.object(native_uart.time, 'sleep'):
            with self.assertRaises(CommandError):
                self.read(register='ALARM_CODE')
        self.assertEqual(len(self.uart.calls), 1000)
        self.assertEqual(self.printer.shutdown_messages, [])
        self.assertIsNone(self.status()['alarm_shutdown'])
        self.assertEqual(self.status()['stats']['stepper_x']['error'], 1)

    def test_alarm_pause_is_rejected_and_enable_callback_only_schedules(self):
        tracker = self.protection_ready()
        scheduled = self.monitor.next_cycle_at
        with self.assertRaisesRegex(CommandError, '不能暂停'):
            self.monitor.cmd_DRIVER_MONITOR_AUTO(FakeCommand(enable=0))
        self.assertTrue(self.status()['auto_enabled'])
        self.assertEqual(self.monitor.next_cycle_at, scheduled)
        tracker.set_enabled(True, print_time=101.0)
        self.assertEqual(self.uart.calls, [])
        self.assertIsNone(self.status()['alarm_shutdown'])
        self.assertTrue(self.status()['protection']['stepper_x']['enabled'])
        self.assertFalse(self.status()['protection']['stepper_x']['armed'])

    def test_alarm_latch_survives_shutdown_disconnect_and_status_is_detached(self):
        tracker = self.protection_ready()
        tracker.set_enabled(True)
        self.uart.answer = {'data': 42}
        with self.assertRaises(CommandError):
            self.read(register='ALARM_CODE')
        fault = self.status()['alarm_shutdown']
        self.assertEqual(fault['reason'], '未知报警码')
        self.printer.events['klippy:disconnect']()
        for _ in range(10):
            status = self.status()
            self.assertEqual(status['alarm_shutdown'], fault)
            status['alarm_shutdown']['value'] = 0
            with self.assertRaises(CommandError):
                self.read(register='ALARM_CODE')
        self.assertEqual(len(self.uart.calls), 1)
        self.assertEqual(len(self.printer.shutdown_messages), 1)
        tracker.set_enabled(False)
        self.printer.objects['webhooks'].status['state'] = 'ready'
        self.ready()
        self.assertIsNone(self.status()['alarm_shutdown'])
        self.assertEqual(len(tracker.callbacks), 1)  # No duplicated callbacks.
        self.read(register='ALARM_CODE')  # Chip still reports the same alarm.
        self.assertEqual(self.status()['last_result']['value'], 42)
        self.assertEqual(len(self.printer.shutdown_messages), 1)
        tracker.set_enabled(True)
        with self.assertRaises(CommandError):
            self.read(register='ALARM_CODE')
        self.assertEqual(len(self.printer.shutdown_messages), 2)

    def test_shutdown_exception_is_not_swallowed_as_uart_error(self):
        self.protection_ready().set_enabled(True)
        self.uart.answer = {'data': 1}
        with patch.object(self.printer, 'invoke_shutdown',
                          side_effect=RuntimeError('shutdown handler failed')):
            with self.assertRaisesRegex(RuntimeError, 'shutdown handler failed'):
                self.read(register='ALARM_CODE')
        self.assertEqual(self.status()['last_result']['outcome'], 'ok')
        self.assertEqual(self.status()['stats']['stepper_x']['error'], 0)
        self.assertFalse(self.status()['active'])
        self.assertIsNotNone(self.status()['alarm_shutdown'])

    def test_armed_alarm_applies_in_idle_printing_and_paused_states(self):
        for print_state in ('standby', 'printing', 'paused'):
            with self.subTest(print_state=print_state):
                self.printer = FakePrinter()
                self.uart = self.printer.uart
                self.reactor = self.printer.reactor
                self.protection_ready().set_enabled(True)
                self.printer.objects['print_stats'].status['state'] = print_state
                self.uart.answer = {'data': 1}
                with self.assertRaises(CommandError):
                    self.read(register='ALARM_CODE')
                self.assertEqual(len(self.printer.shutdown_messages), 1)

    def test_already_enabled_without_queue_time_api_fails_ready_initialization(self):
        self.printer.objects['toolhead'] = None
        with self.assertRaises(AttributeError):
            self.protection_ready(initially_enabled=True)
        self.assertFalse(self.monitor.ready)
        self.assertEqual(self.uart.calls, [])
        self.assertFalse(self.reactor.fire())


if __name__ == '__main__':
    unittest.main()
