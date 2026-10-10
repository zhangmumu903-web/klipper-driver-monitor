# Background monitoring and optional alarm shutdown for existing LYX9231 objects.
# License: GNU GPLv3

import copy
from collections import deque
import json
import logging
import time
import uuid


SCHEMA_VERSION = 1
STATIC_REGISTERS = ('CHIP_MODEL', 'RUN_CURRENT')
DYNAMIC_REGISTERS = ('ALARM_CODE', 'MOTOR_SPEED', 'ERROR_ANGLE')
READ_REGISTERS = STATIC_REGISTERS + DYNAMIC_REGISTERS
HISTORY_REGISTERS = ('MOTOR_SPEED', 'ERROR_ANGLE')
HISTORY_SECONDS = 600.0
HISTORY_LIMIT = 600
ALARM_REASONS = {
    1: '过流', 2: '电机未连接', 3: '线圈异常', 4: '超差', 5: '堵转',
}


class DriverMonitor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.auto_enabled = config.getboolean('auto_start', True)
        self.shutdown_on_alarm = config.getboolean('shutdown_on_alarm', False)
        if self.shutdown_on_alarm and not self.auto_enabled:
            raise config.error('shutdown_on_alarm requires auto_start: true')
        self.alarm_shutdown = None
        self.enable_trackers = {}
        self.enable_states = {}
        self.cycle_interval = config.getfloat('cycle_interval', 1.0, minval=0.5)
        self.read_gap = config.getfloat('read_gap', 0.2, minval=0.01)
        self.next_read_at = 0.0
        self.ready = False
        self.active = False
        self.cycle_active = False
        self.cycle_status = 'idle'
        self.next_cycle_at = None
        self.cycle_due = 0.0
        self.drivers = {}
        self.readings = {}
        self.history = {}
        self.session_id = None
        self.startup_pending = {}
        self.sample_stats = {}
        self.last_alarm_values = {}
        self.seq = 0
        self.last_result = None
        self.timer = self.reactor.register_timer(
            self._auto_timer, self.reactor.NEVER)
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:shutdown', self._not_ready)
        self.printer.register_event_handler('klippy:disconnect', self._not_ready)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'DRIVER_MONITOR_READ', self.cmd_DRIVER_MONITOR_READ,
            desc='Read one LYX register using its native read transaction')
        gcode.register_command(
            'DRIVER_MONITOR_REFRESH', self.cmd_DRIVER_MONITOR_REFRESH,
            desc='Read LYX alarm, speed, and angle error in native transactions')
        gcode.register_command(
            'DRIVER_MONITOR_AUTO', self.cmd_DRIVER_MONITOR_AUTO,
            desc='Enable or pause background LYX monitoring')

    def _handle_ready(self):
        # Reference only: no driver construction, MCU objects, or pin setup.
        self.drivers = {
            name.split(' ', 1)[1]: driver
            for name, driver in self.printer.lookup_objects('lyx9231')
            if name.startswith('lyx9231 ')
        }
        self.readings = {name: {} for name in self.drivers}
        self.history = {
            name: {register: deque(maxlen=HISTORY_LIMIT)
                   for register in HISTORY_REGISTERS}
            for name in self.drivers
        }
        self.session_id = uuid.uuid4().hex
        self.startup_pending = {
            name: set(STATIC_REGISTERS) for name in self.drivers}
        self.last_result = None
        self.last_alarm_values = {}
        # This clears only the host session latch, never the driver's alarm.
        self.alarm_shutdown = None
        if self.shutdown_on_alarm:
            self._bind_enable_tracking()
        for stepper in self.drivers:
            self.sample_stats.setdefault(stepper, {
                'attempts': 0, 'ok': 0, 'invalid': 0, 'error': 0})
        self.ready = True
        self.cycle_status = 'idle'
        self.cycle_due = self.reactor.monotonic()
        if self.auto_enabled:
            self._schedule(self.cycle_due)

    def _schedule(self, when):
        self.next_cycle_at = when
        self.reactor.update_timer(
            self.timer, self.reactor.NEVER if when is None else when)

    def _bind_enable_tracking(self):
        enables = self.printer.lookup_object('stepper_enable')
        for stepper in self.drivers:
            tracker = enables.lookup_enable(stepper)
            previous = self.enable_states.get(stepper, {})
            self.enable_states[stepper] = {
                'epoch': previous.get('epoch', 0) + 1,
                'started_at': self.reactor.monotonic(),
                'enable_print_time': None,
                'enable_time_source': 'disabled',
            }
            if self.enable_trackers.get(stepper) is not tracker:
                tracker.register_state_callback(
                    lambda print_time, enabled, name=stepper:
                    self._enable_changed(name, print_time, enabled))
                self.enable_trackers[stepper] = tracker
            if tracker.is_motor_enabled():
                # Earlier enable events precede the end of already queued moves.
                # This is a conservative protection gate, not their original time.
                boundary = self.printer.lookup_object(
                    'toolhead').get_last_move_time()
                state = self.enable_states[stepper]
                state['enable_print_time'] = boundary
                state['enable_time_source'] = 'ready_queue_boundary'

    def _enable_changed(self, stepper, print_time, enabled):
        state = self.enable_states.get(stepper)
        if state is None:
            return
        state['epoch'] += 1
        state['started_at'] = self.reactor.monotonic()
        state['enable_print_time'] = print_time if enabled else None
        state['enable_time_source'] = 'state_callback' if enabled else 'disabled'
        # Called before EnableTracking updates is_enabled. No UART or motor
        # operations here; the later timer observes the completed state change.
        if enabled and self.shutdown_on_alarm and self.ready and self.auto_enabled:
            self.cycle_due = min(self.cycle_due, state['started_at'])
            if not self.cycle_active:
                self._schedule(state['started_at'])

    def _protection_state(self, stepper, eventtime):
        tracker = self.enable_trackers[stepper]
        state = self.enable_states[stepper]
        enabled = tracker.is_motor_enabled()
        print_time = state['enable_print_time']
        reached = (print_time is not None and
                   tracker.stepper.get_mcu().estimated_print_time(eventtime)
                   >= print_time)
        return {
            'enabled': enabled,
            'armed': (self.shutdown_on_alarm and self.ready
                      and self.alarm_shutdown is None and enabled and reached),
            'epoch': state['epoch'],
            'enable_print_time': print_time,
            'enable_time_source': state['enable_time_source'],
        }

    def _not_ready(self):
        self.ready = False
        self.cycle_status = 'stopped'
        self._schedule(None)

    def _is_ready(self):
        webhooks = self.printer.lookup_object('webhooks', None)
        return (self.ready and self.alarm_shutdown is None and webhooks is not None
                and webhooks.get_status(self.reactor.monotonic()).get('state')
                == 'ready')

    def _check_manual(self, gcmd, stepper):
        if not self._is_ready():
            raise gcmd.error('Driver monitor: Klipper is not ready')
        if stepper not in self.drivers:
            raise gcmd.error('Driver monitor: unknown LYX stepper %s' % stepper)
        if self.active:
            raise gcmd.error('Driver monitor: a read or cycle is in progress')

    def _log_result(self, previous, result):
        stepper, register = result['stepper'], result['register']
        wall_time, monotonic = time.time(), result['ended']
        failed_before = previous is not None and previous['outcome'] != 'ok'
        if result['outcome'] != 'ok':
            if not failed_before:
                logging.warning(
                    'Driver monitor stepper=%s register=%s no_valid_response '
                    'outcome=%s error_type=%s detail=%s unix=%.3f monotonic=%.6f',
                    stepper, register, result['outcome'],
                    result.get('error_type', 'none'),
                    result.get('error_message', 'no validated reply'),
                    wall_time, monotonic)
            return
        value = result['value']
        if failed_before:
            logging.info('Driver monitor stepper=%s register=%s recovered '
                         'value=%d (0x%04X) unix=%.3f monotonic=%.6f',
                         stepper, register, value, value, wall_time, monotonic)
        if register == 'ALARM_CODE':
            old = self.last_alarm_values.get(stepper)
            if old != value:
                old_text = 'unknown' if old is None else '%d (0x%04X)' % (old, old)
                logging.info('Driver monitor stepper=%s register=ALARM_CODE '
                             'changed old=%s new=%d (0x%04X) '
                             'unix=%.3f monotonic=%.6f',
                             stepper, old_text, value, value,
                             wall_time, monotonic)
            self.last_alarm_values[stepper] = value

    def _read_once(self, stepper, register, driver):
        # One logical transaction; get_register() owns the UART mutex and its
        # existing retry policy. Do not acquire that mutex a second time here.
        self.seq += 1
        result = {
            'seq': self.seq, 'stepper': stepper, 'register': register,
            'value': None, 'outcome': 'error',
            'started': self.reactor.monotonic(),
            'ended': None, 'duration': None,
        }
        previous = self.readings[stepper].get(register)
        protection_start = (self._protection_state(stepper, result['started'])
                            if self.shutdown_on_alarm else None)
        stats = self.sample_stats[stepper]
        stats['attempts'] += 1  # Logical transactions, not low-level wire reads.
        # Mark attempted startup reads once, including invalid/error results.
        # A busy mutex is rejected before this method and is not an attempt.
        self.startup_pending[stepper].discard(register)
        try:
            # Same complete read operation as LYX_READ_REG. Wait for its final
            # value or exception before caching a sample or starting the next.
            value = driver.get_register(register)
            if value is None:
                # None means no valid reply, not necessarily no received bytes.
                result['outcome'] = 'invalid'
            elif type(value) is int and 0 <= value <= 0xffff:
                result['value'] = value
                result['outcome'] = 'ok'
            else:
                raise ValueError('Unexpected LYX register value')
        except Exception as exc:
            # Cache an error, stop this batch, and log only a state transition.
            # Do not emit a repeated traceback on every periodic failed read.
            result['error_type'] = type(exc).__name__
            result['error_message'] = ' '.join(str(exc).split())[:160]
        finally:
            result['ended'] = self.reactor.monotonic()
            # Shared by all monitor entry points and drivers, including errors.
            self.next_read_at = result['ended'] + self.read_gap
            result['duration'] = result['ended'] - result['started']
            stats[result['outcome']] += 1
            self.last_result = result
            self.readings[stepper][register] = result
            if register in HISTORY_REGISTERS:
                samples = self.history[stepper][register]
                while (samples and result['ended'] - samples[0]['ended']
                       > HISTORY_SECONDS):
                    samples.popleft()
                # Failed reads are real samples too, with value=None. Their
                # presence lets clients draw a gap rather than invent a value.
                samples.append(result)
        self._log_result(previous, result)
        # Keep shutdown outside the read-error handler. A safety transition
        # must never be mistaken for a failed UART sample or retried.
        self._check_alarm_shutdown(result, protection_start)
        return result

    def _check_alarm_shutdown(self, result, protection_start):
        if (not self.shutdown_on_alarm or self.alarm_shutdown is not None
                or result['register'] != 'ALARM_CODE'
                or result['outcome'] != 'ok'
                or type(result['value']) is not int
                or not 1 <= result['value'] <= 0xffff
                or not protection_start['armed']):
            return
        stepper = result['stepper']
        protection_end = self._protection_state(stepper, result['ended'])
        if (not protection_end['armed']
                or protection_end['epoch'] != protection_start['epoch']
                or result['started'] < self.enable_states[stepper]['started_at']):
            return
        value = result['value']
        reason = ALARM_REASONS.get(value, '未知报警码')
        wall_time = time.time()
        message = ('LYX 驱动报警停机：轴 %s，原因：%s，ALARM_CODE=%d (0x%04X)，'
                   'unix=%.3f monotonic=%.6f。驱动报警需断电重新上电清除；'
                   'Klipper 重启不代表驱动报警已清除。'
                   % (stepper, reason, value, value, wall_time, result['ended']))
        self.alarm_shutdown = {
            'stepper': stepper, 'register': 'ALARM_CODE', 'value': value,
            'seq': result['seq'], 'ended': result['ended'], 'unix': wall_time,
            'reason': reason, 'message': message,
            'enable_epoch': protection_end['epoch'],
        }
        logging.error('%s', message)
        self.printer.invoke_shutdown(message)

    def _read_batch(self, stepper, registers, automatic=False):
        driver = self.drivers[stepper].mcu_lyx
        mutex = driver.mcu_uart.mutex
        results = []
        for register in registers:
            if not self._is_ready() or (automatic and not self.auto_enabled):
                return 'stopped', results
            if mutex.test():
                return 'busy', results
            # Keep monitor ownership (active), but release the UART mutex while
            # yielding so other Klipper commands can use the bus or stop us.
            while self.reactor.monotonic() < self.next_read_at:
                self.reactor.pause(self.next_read_at)
                if not self._is_ready() or (automatic and not self.auto_enabled):
                    return 'stopped', results
            # pause() may have allowed another command to acquire the bus.
            # After test(), no monitor code yields before native get_register()
            # acquires its own mutex. Never double-lock the non-reentrant bus.
            if mutex.test():
                return 'busy', results
            result = self._read_once(stepper, register, driver)
            results.append(result)
            if self.alarm_shutdown is not None or not self._is_ready():
                return 'stopped', results
            if result['outcome'] == 'error':
                return 'error', results
        return 'complete', results

    def _manual_batch(self, gcmd, stepper, registers):
        self._check_manual(gcmd, stepper)
        self.active = True
        try:
            outcome, results = self._read_batch(stepper, registers)
        finally:
            self.active = False
        if outcome != 'complete':
            raise gcmd.error('Driver monitor: %s; completed results cached, '
                             'no additional transaction retry performed' % outcome)
        gcmd.respond_info('Driver monitor: ' + json.dumps(results, sort_keys=True))

    def cmd_DRIVER_MONITOR_READ(self, gcmd):
        register = gcmd.get('REGISTER').upper()
        if register not in READ_REGISTERS:
            raise gcmd.error('Driver monitor: unsupported register %s' % register)
        self._manual_batch(gcmd, gcmd.get('STEPPER'), (register,))

    def cmd_DRIVER_MONITOR_REFRESH(self, gcmd):
        self._manual_batch(gcmd, gcmd.get('STEPPER'), DYNAMIC_REGISTERS)

    def cmd_DRIVER_MONITOR_AUTO(self, gcmd):
        enabled = bool(gcmd.get_int('ENABLE', minval=0, maxval=1))
        if self.shutdown_on_alarm and not enabled:
            raise gcmd.error('使能后报警停机保护已开启，不能暂停后台监测')
        self.auto_enabled = enabled
        if not enabled:
            self._schedule(None)
            if not self.cycle_active:
                self.cycle_status = 'stopped'
        elif self._is_ready() and not self.cycle_active:
            self._schedule(max(self.reactor.monotonic(), self.cycle_due))
        # While a cycle is in flight, its callback owns subsequent scheduling.
        # Disable allows the current native transaction to finish, then stops.
        gcmd.respond_info('Driver monitor: background monitoring %s'
                         % ('enabled' if enabled else 'paused'))

    def _next_auto_time(self):
        if not self.auto_enabled or not self._is_ready() or not self.drivers:
            self.next_cycle_at = None
            return self.reactor.NEVER
        self.next_cycle_at = self.cycle_due
        return self.next_cycle_at

    def _auto_timer(self, eventtime):
        self.next_cycle_at = None
        if not self.auto_enabled or not self._is_ready():
            self.cycle_status = 'stopped'
            return self.reactor.NEVER
        now = self.reactor.monotonic()
        if now < self.cycle_due:
            return self._next_auto_time()
        # One ordered round per callback; never catch up missed rounds.
        if self.active:
            self.cycle_status = 'busy'
            self.cycle_due = now + self.cycle_interval
            return self._next_auto_time()
        self.active = self.cycle_active = True
        self.cycle_status = 'reading'
        try:
            cycle_outcome = 'complete'
            for stepper in sorted(self.drivers):
                startup = tuple(r for r in STATIC_REGISTERS
                                if r in self.startup_pending[stepper])
                registers = (DYNAMIC_REGISTERS + startup if self.shutdown_on_alarm
                             else startup + DYNAMIC_REGISTERS)
                outcome, unused = self._read_batch(
                    stepper, registers, automatic=True)
                if outcome == 'stopped':
                    cycle_outcome = 'stopped'
                    break
                if outcome == 'error':
                    cycle_outcome = 'error'
                elif outcome == 'busy' and cycle_outcome == 'complete':
                    cycle_outcome = 'busy'
                # Busy/error only ends this driver's batch. Other configured
                # drivers must not be starved by a failing earlier driver.
            self.cycle_status = cycle_outcome
        finally:
            self.active = self.cycle_active = False
            ended = self.reactor.monotonic()
            # Begin the next wait only after the entire round has completed.
            # Busy/error batches are skipped, without immediate retries.
            self.cycle_due = ended + self.cycle_interval
        return self._next_auto_time()

    def get_status(self, eventtime):
        # Browser queries/subscriptions are cache-only; no UART or scheduling.
        return {
            'schema_version': SCHEMA_VERSION,
            'min_interval': 0.0,
            'next_allowed_at': 0.0,
            'cooldown_remaining': 0.0,
            'ready': self.ready,
            'active': self.active,
            'drivers': [
                {'stepper': name, 'type': 'lyx9231',
                 'registers': list(READ_REGISTERS)}
                for name in sorted(self.drivers)
            ],
            'last_result': copy.deepcopy(self.last_result),
            'readings': copy.deepcopy(self.readings),
            'session_id': self.session_id,
            'history': {
                stepper: {
                    register: [copy.deepcopy(sample) for sample in samples
                               if eventtime - sample['ended'] <= HISTORY_SECONDS]
                    for register, samples in series.items()
                }
                for stepper, series in self.history.items()
            },
            'stats': copy.deepcopy(self.sample_stats),
            'stats_unit': 'logical_transactions',
            'auto_enabled': self.auto_enabled,
            'shutdown_on_alarm': self.shutdown_on_alarm,
            'alarm_shutdown': copy.deepcopy(self.alarm_shutdown),
            'protection': {
                stepper: self._protection_state(stepper, eventtime)
                for stepper in self.drivers if stepper in self.enable_trackers
            },
            'cycle_interval': self.cycle_interval,
            'read_order': list(DYNAMIC_REGISTERS),
            'read_gap': self.read_gap,
            'cycle_active': self.cycle_active,
            'cycle_status': self.cycle_status,
            'next_cycle_in': (None if self.next_cycle_at is None else
                              max(0.0, self.next_cycle_at - eventtime)),
        }


def load_config(config):
    return DriverMonitor(config)
