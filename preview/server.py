"""Offline UI fixture only: no network client and no access to a printer."""
import copy
import argparse
import json
import math
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DYNAMIC = ['ALARM_CODE', 'MOTOR_SPEED', 'ERROR_ANGLE']
STATIC = ['CHIP_MODEL', 'RUN_CURRENT']
REGISTERS = STATIC + DYNAMIC
CYCLE_INTERVAL = 1.0  # Wait after the complete alarm -> speed -> angle round.
READ_GAP = .2  # Minimum delay after a simulated read ends, including manual reads.
HISTORY_LIMIT = 600
lock = threading.RLock()
state = {'scenario': 'invalid', 'seq': 0, 'last': None, 'readings': {},
         'history': {'MOTOR_SPEED': [], 'ERROR_ANGLE': []},
         'session_id': 'offline-preview-%s' % time.time_ns(),
         'stats': {'attempts': 0, 'ok': 0, 'invalid': 0, 'error': 0},
         'posts': [], 'reads': 0, 'samples': [], 'auto_enabled': True,
         'cycle_due': 0, 'next_read_at': 0, 'active': False,
         'cycle_active': False, 'cycle_status': 'idle', 'boot_complete': False,
         'lyx_steppers': ['stepper_x'], 'tmc_objects': ['tmc2209 extruder'],
         'other_drivers': {}, 'driver_outcomes': {}, 'get_counts': {},
         'layout_mode': 'cards'}

PROFILES = {
    'stepper_x': {'CHIP_MODEL': 2316, 'RUN_CURRENT': 960, 'ALARM_CODE': 0,
                  'MOTOR_SPEED': 120, 'ERROR_ANGLE': 12},
    'stepper_y': {'CHIP_MODEL': 2316, 'RUN_CURRENT': 640, 'ALARM_CODE': 2,
                  'MOTOR_SPEED': 840, 'ERROR_ANGLE': 84},
    'stepper_z': {'CHIP_MODEL': 2316, 'RUN_CURRENT': 320, 'ALARM_CODE': 7,
                  'MOTOR_SPEED': 360, 'ERROR_ANGLE': 36},
    **{'stepper_z%d' % index: {'CHIP_MODEL': 2316, 'RUN_CURRENT': 832,
       'ALARM_CODE': 0, 'MOTOR_SPEED': 100 + index * 20, 'ERROR_ANGLE': index * 3}
       for index in (1, 2, 3)},
}
TMC_CACHES = {
    'tmc2209 extruder': {'run_current': .7955, 'hold_current': .7955,
                        'drv_status': None, 'temperature': None},
    'tmc5160 stepper_z1': {'run_current': 1.2, 'hold_current': .6,
                         'drv_status': {'otpw': True, 'ot': False},
                         'temperature': 48.5},
}


def driver_state(stepper):
    if stepper == 'stepper_x':
        return state
    return state['other_drivers'].setdefault(stepper, {
        'readings': {}, 'history': {'MOTOR_SPEED': [], 'ERROR_ANGLE': []},
        'stats': {'attempts': 0, 'ok': 0, 'invalid': 0, 'error': 0}})


def sample(register, source, stepper='stepper_x'):
    driver = driver_state(stepper)
    start = time.monotonic()
    state['seq'] += 1
    state['reads'] += 1
    outcome = 'ok' if state['scenario'] in ('ok', 'alarm', 'trend', 'multiple', 'four_z') else 'invalid'
    outcome = state['driver_outcomes'].get(stepper, outcome)
    values = {'CHIP_MODEL': 2316, 'RUN_CURRENT': 960,
              'ALARM_CODE': 2 if state['scenario'] == 'alarm' else 0,
              'MOTOR_SPEED': 0, 'ERROR_ANGLE': 0}
    if state['scenario'] == 'trend':
        values['MOTOR_SPEED'] = int(750 + 350 * math.sin(start / 42))
        values['ERROR_ANGLE'] = int(70 + 55 * math.sin(start / 28))
    elif state['scenario'] in ('multiple', 'four_z'):
        values = PROFILES[stepper]
    end = time.monotonic()
    result = {'seq': state['seq'], 'stepper': stepper, 'register': register,
              'value': values[register] if outcome == 'ok' else None,
              'outcome': outcome, 'started': start, 'ended': end,
              'duration': end-start}
    state['last'] = result
    driver['readings'][register] = result
    driver['stats']['attempts'] += 1
    driver['stats'][outcome] += 1
    state['samples'].append({'source': source, **result})
    state['samples'] = state['samples'][-300:]
    if register in driver['history']:
        points = driver['history'][register]
        points.append(result)
        driver['history'][register] = [p for p in points if end - p['ended'] <= 600][-HISTORY_LIMIT:]


def seed_trend(stepper='stepper_x'):
    # Explicit offline demonstration only, never interpreted as device history.
    now = time.monotonic()
    cadence = CYCLE_INTERVAL + (len(DYNAMIC)-1) * READ_GAP
    count = min(HISTORY_LIMIT, int(300/cadence) + 1)
    driver = driver_state(stepper)
    for register in driver['history']:
        points = []
        for index in range(count):
            ended = now - (count-1-index)*cadence
            value = (int(750 + 350 * math.sin(ended / 42)) if register == 'MOTOR_SPEED'
                     else int(70 + 55 * math.sin(ended / 28)))
            if state['scenario'] in ('multiple', 'four_z'):
                value = PROFILES[stepper][register] + int(5 * math.sin(index / 8))
            state['seq'] += 1
            points.append({'seq': state['seq'], 'stepper': stepper,
                           'register': register, 'value': None if index == count*2//3 else value,
                           'outcome': 'invalid' if index == count*2//3 else 'ok',
                           'started': ended, 'ended': ended, 'duration': 0})
        driver['history'][register] = points
        driver['readings'][register] = points[-1]


def set_scenario(data):
    scenario = data.get('scenario', state['scenario'])
    if scenario not in ('invalid', 'ok', 'alarm', 'trend', 'missing', 'offline', 'multiple', 'four_z', 'tmc_only'):
        raise ValueError('Unknown offline scenario')
    default_steppers = ([] if scenario == 'tmc_only' else
                        ['stepper_z', 'stepper_z1', 'stepper_z2', 'stepper_z3'] if scenario == 'four_z' else
                        ['stepper_x', 'stepper_y'] if scenario == 'multiple' else ['stepper_x'])
    steppers = data.get('lyx_steppers', default_steppers)
    tmcs = data.get('tmc_objects', list(TMC_CACHES)
                    if scenario in ('multiple', 'four_z', 'tmc_only') else ['tmc2209 extruder'])
    outcomes = data.get('driver_outcomes', {})
    if (not isinstance(steppers, list) or not isinstance(tmcs, list)
            or any(s not in PROFILES for s in steppers)
            or any(s not in TMC_CACHES for s in tmcs)
            or not isinstance(outcomes, dict)
            or any(s not in PROFILES or value not in ('ok', 'invalid')
                   for s, value in outcomes.items())):
        raise ValueError('Unsupported offline driver fixture')
    state.update(scenario=scenario, lyx_steppers=list(dict.fromkeys(steppers)),
                 tmc_objects=list(dict.fromkeys(tmcs)), driver_outcomes=outcomes)
    if 'auto_enabled' in data:
        if type(data['auto_enabled']) is not bool:
            raise ValueError('auto_enabled must be boolean')
        state['auto_enabled'] = data['auto_enabled']
    for stepper in state['lyx_steppers']:
        driver_state(stepper)
        if scenario in ('trend', 'multiple', 'four_z'):
            seed_trend(stepper)
            if scenario in ('multiple', 'four_z'):
                for register in STATIC + ['ALARM_CODE']:
                    sample(register, 'fixture_seed', stepper)
    state['cycle_due'] = time.monotonic()


def run_batch(registers, source, automatic=False, stepper='stepper_x', steps=None):
    # This is an offline fixture. It has no UART/network client; samples are
    # generated here. Release the lock during gaps so GET and AUTO remain usable.
    with lock:
        if state['cycle_active'] or (automatic and not state['auto_enabled']):
            return False
        state['cycle_active'] = True
        state['cycle_status'] = 'reading'
    completed = False
    try:
        tasks = steps if steps is not None else [(stepper, r) for r in registers]
        for stepper, register in tasks:
            while True:
                with lock:
                    if stepper not in state['lyx_steppers'] or (automatic and not state['auto_enabled']):
                        return True
                    delay = state['next_read_at'] - time.monotonic()
                    if delay <= 0:
                        state['active'] = True
                        sample(register, source, stepper)
                        state['active'] = False
                        state['next_read_at'] = state['last']['ended'] + READ_GAP
                        break
                time.sleep(min(delay, .05))
        completed = True
        return True
    finally:
        with lock:
            state['active'] = False
            state['cycle_active'] = False
            state['cycle_status'] = 'complete' if completed else 'stopped'
            state['boot_complete'] = all(register in driver_state(s)['readings']
                                         for s in state['lyx_steppers'] for register in STATIC)
            if automatic:
                state['cycle_due'] = time.monotonic() + CYCLE_INTERVAL


def auto_worker():
    while True:
        with lock:
            due = state['auto_enabled'] and not state['cycle_active'] and time.monotonic() >= state['cycle_due']
            tasks = [(s, r) for s in state['lyx_steppers']
                     for r in ([r for r in STATIC if r not in driver_state(s)['readings']] + DYNAMIC)]
        if due:
            run_batch([], 'auto_cycle', automatic=True, steps=tasks)
        time.sleep(.05)


def status():
    response = {
        'webhooks': {'state': 'ready', 'state_message': 'Printer is ready'},
        'print_stats': {'state': 'standby'}, 'pause_resume': {'is_paused': False},
        'driver_monitor': {
            'schema_version': 1, 'min_interval': 0.0, 'active': state['active'],
            'shutdown_on_alarm': True,
            'protection': {s: {'enabled': False, 'armed': False, 'generation': 1}
                                 for s in state['lyx_steppers']},
            'ready': True, 'next_allowed_at': 0.0, 'cooldown_remaining': 0.0,
            'auto_enabled': state['auto_enabled'], 'cycle_interval': CYCLE_INTERVAL,
            'read_gap': READ_GAP, 'read_order': list(DYNAMIC),
            'cycle_active': state['cycle_active'], 'cycle_status': state['cycle_status'],
            'next_cycle_in': (max(0, state['cycle_due']-time.monotonic())
                              if state['auto_enabled'] and not state['cycle_active'] else None),
            'drivers': [{'stepper': s, 'type': 'lyx9231', 'registers': REGISTERS}
                        for s in state['lyx_steppers']],
            'last_result': state['last'],
            'session_id': state['session_id'],
            'history': {s: {r: [p for p in points if time.monotonic()-p['ended'] <= 600]
                            for r, points in driver_state(s)['history'].items()}
                        for s in state['lyx_steppers']},
            'readings': {s: driver_state(s)['readings'] for s in state['lyx_steppers']},
            'stats': {s: driver_state(s)['stats'] for s in state['lyx_steppers']},
            'stats_unit': 'logical_transactions'}}
    for stepper in state['lyx_steppers']:
        response['lyx9231 ' + stepper] = {
            'run_current': PROFILES[stepper]['RUN_CURRENT']/640,
            'hold_current': PROFILES[stepper]['RUN_CURRENT']/1280, 'microstep': 16}
    response.update({name: TMC_CACHES[name] for name in state['tmc_objects']})
    return response


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, data, code=200, content_type='application/json'):
        body = json.dumps(data, ensure_ascii=False).encode() if content_type == 'application/json' else data
        self.send_response(code)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        uri = urlsplit(self.path)
        with lock:
            if uri.path.startswith('/printer/'):
                state['get_counts'][uri.path] = state['get_counts'].get(uri.path, 0) + 1
            if uri.path == '/printer/info':
                return self.reply({'result': {'hostname': 'driver-monitor-demo', 'state': 'ready'}})
            if uri.path == '/printer/objects/list':
                names = list(status())
                if state['scenario'] == 'missing':
                    names.remove('driver_monitor')
                return self.reply({'result': {'objects': names}})
            if uri.path == '/printer/objects/query':
                if state['scenario'] == 'offline':
                    return self.reply({'error': {'message': '模拟连接中断'}}, 503)
                data = status()
                if state['scenario'] == 'missing':
                    data.pop('driver_monitor')
                wanted = {unquote(x.split('=')[0]) for x in uri.query.split('&')}
                data = {k: v for k, v in data.items() if k in wanted}
                return self.reply({'result': {'eventtime': time.monotonic(), 'status': data}})
            if uri.path == '/driver-monitor-user/layout.json':
                layout = json.loads((ROOT / 'frontend/customization/layout.json').read_text())
                layout['mode'] = state['layout_mode']
                return self.reply(layout)
            if uri.path == '/__test':
                return self.reply(copy.deepcopy(state))
        if uri.path == '/driver-monitor-user/custom.css':
            return self.reply((ROOT / 'frontend/customization/custom.css').read_bytes(), content_type='text/css')
        target = ROOT / ('preview/index.html' if uri.path == '/' else uri.path.lstrip('/'))
        try:
            target = target.resolve()
            target.relative_to(ROOT)
            content = target.read_bytes()
        except (OSError, ValueError):
            return self.reply({'error': 'not found'}, 404)
        return self.reply(content, content_type=mimetypes.guess_type(str(target))[0] or 'text/plain')

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
        with lock:
            if self.path == '/__layout':
                if data.get('mode') not in ('cards', 'compact', 'z-overview'):
                    return self.reply({'error': 'Unknown preview layout'}, 400)
                state['layout_mode'] = data['mode']
                return self.reply({'mode': state['layout_mode']})
            if self.path == '/__scenario':
                try:
                    set_scenario(data)
                except ValueError as exc:
                    return self.reply({'error': str(exc)}, 400)
                return self.reply({'scenario': state['scenario']})
            if self.path != '/printer/gcode/script':
                return self.reply({'error': 'not found'}, 404)
            script = data.get('script', '')
            state['posts'].append({'at': time.monotonic(), 'script': script})
            parts = script.split()
            args = dict(x.split('=', 1) for x in parts[1:])
            if not parts:
                return self.reply({'error': {'message': 'missing command'}}, 400)
            if parts[0] == 'DRIVER_MONITOR_AUTO' and args.get('ENABLE') in ('0', '1'):
                state['auto_enabled'] = args['ENABLE'] == '1'
                if state['auto_enabled']:
                    state['cycle_due'] = time.monotonic()
                return self.reply({'result': 'ok'})
            if args.get('STEPPER') not in state['lyx_steppers']:
                return self.reply({'error': {'message': '未知驱动'}}, 400)
            if parts[0] == 'DRIVER_MONITOR_REFRESH':
                registers, source = DYNAMIC, 'manual_refresh'
            elif parts[0] == 'DRIVER_MONITOR_READ' and args.get('REGISTER') in REGISTERS:
                registers, source = [args['REGISTER']], 'manual_single'
            else:
                return self.reply({'error': {'message': '模拟服务器拒绝其他指令'}}, 400)
        if not run_batch(registers, source, stepper=args['STEPPER']):
            return self.reply({'error': {'message': '后台正在读取，请等本轮完成'}}, 400)
        return self.reply({'result': 'ok'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', default='invalid')
    parser.add_argument('--paused', action='store_true')
    parser.add_argument('--layout', choices=['cards', 'compact', 'z-overview'], default='cards')
    parser.add_argument('--port', type=int, default=18763)
    options = parser.parse_args()
    state['layout_mode'] = options.layout
    set_scenario({'scenario': options.scenario, 'auto_enabled': not options.paused})
    threading.Thread(target=auto_worker, daemon=True).start()
    print('Offline embedded preview: http://127.0.0.1:%d ' % options.port +
          '(no printer connection; alarm -> speed -> angle, cycle gap 1s, read gap .2s)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', options.port), Handler).serve_forever()
