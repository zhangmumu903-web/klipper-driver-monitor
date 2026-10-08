import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MonitorController, MoonrakerApi, ApiError, PREVIEW_HOSTNAME,
  REGISTER_INFO, DYNAMIC_REGISTERS, targetProblem as checkTargetProblem, buildRefreshCommand, buildAutoCommand,
  driversFrom, readingDisplay, resultTime, trendData,
  monitorScheduleLabel, alarmProtectionDisplay, alarmShutdownMessage} from './driver-monitor-core.mjs';
import {isDashboardLocation} from './driver-monitor.mjs';
import {TARGET_CONFIG} from './driver-monitor-config.mjs';

const TEST_HOSTNAME = 'printer-test';
const TEST_ENDPOINTS = ['http://192.0.2.10', 'http://192.0.2.10:7125']
  .map(apiUrl => ({apiUrl, printerId: createHash('md5').update(apiUrl).digest('hex')}));
const TEST_PRINTER_ID = TEST_ENDPOINTS[0].printerId;
const TEST_CONFIG = {hostname: TEST_HOSTNAME, pageOrigins: ['http://192.0.2.10'], apiEndpoints: TEST_ENDPOINTS};
const targetProblem = (info, url, options) => checkTargetProblem(info, url, options, TEST_CONFIG);

const pageUrl = `http://192.0.2.10/?printer=${TEST_PRINTER_ID}#/`;
const tick = () => new Promise(resolve => setImmediate(resolve));
function snapshot() {
  return {info: {hostname: TEST_HOSTNAME}, eventtime: 100,
    objects: ['webhooks', 'driver_monitor'], status: {
      webhooks: {state: 'ready'},
      driver_monitor: {schema_version: 1, ready: true, active: false, cycle_active: false,
        auto_enabled: true, cycle_interval: 1, read_gap: .2, read_order: [...DYNAMIC_REGISTERS],
        min_interval: 0, next_allowed_at: 0, cooldown_remaining: 0,
        drivers: [{stepper: 'stepper_x', type: 'lyx9231', registers: Object.keys(REGISTER_INFO)}],
        readings: {}, last_result: null,
        stats: {stepper_x: {attempts: 0, ok: 0, invalid: 0, error: 0}}},
    }};
}
function rig(controllerOptions = {}) {
  const data = snapshot(), posts = [], monitor = data.status.driver_monitor;
  let queries = 0, seq = 0;
  function update(register, value = 0, outcome = 'ok', stepper = 'stepper_x') {
    const reading = {seq: ++seq, stepper, register, value, outcome,
      started: 100 + seq, ended: 100 + seq + .004, duration: .004};
    (monitor.readings[stepper] ||= {})[register] = reading;
    data.eventtime = reading.ended; monitor.last_result = reading; return reading;
  }
  const api = {
    async snapshot() { queries++; return structuredClone(data); },
    async refreshDriver(stepper) {
      posts.push(buildRefreshCommand(stepper));
      for (const register of DYNAMIC_REGISTERS) update(register, 0, 'ok', stepper);
    },
    async setAuto(enabled) { posts.push(buildAutoCommand(enabled)); monitor.auto_enabled = enabled; },
  };
  const controller = new MonitorController({api, pageUrl, targetConfig: TEST_CONFIG,
    wallNow: () => 1700000000000, ...controllerOptions});
  return {controller, data, monitor, api, posts, update,
    get queries() { return queries; },
    async open() { controller.setOpen(true); await controller.refresh(); }};
}

test('only the dashboard route keeps a card active', () => {
  assert.equal(isDashboardLocation(pageUrl), true);
  assert.equal(isDashboardLocation('http://127.0.0.1:18763/'), true);
  for (const route of ['console', 'jobs', 'settings'])
    assert.equal(isDashboardLocation(`http://192.0.2.10/#/${route}`), false);
});

test('mount and repeated cache refresh never start automatic POST requests', async () => {
  const r = rig(); await r.open();
  for (let i = 0; i < 5; i++) await r.controller.refresh();
  assert.equal(r.queries, 6); assert.equal(r.posts.length, 0);
  assert.equal(r.controller.state.snapshot.status.driver_monitor.auto_enabled, true);
});

test('a successful cache update clears stale connection errors and prior action notices', async () => {
  const r = rig(); await r.open(); const normal = r.api.snapshot;
  r.api.snapshot = async () => { throw new ApiError('无法连接当前页面的 Moonraker。'); };
  await r.controller.refresh();
  assert.equal(r.controller.state.connected, false);
  assert.match(r.controller.state.message, /无法连接/);
  r.api.snapshot = normal; await r.controller.refresh();
  assert.equal(r.controller.state.connected, true);
  assert.equal(r.controller.state.message, ''); assert.equal(r.controller.state.tone, 'neutral');
  await r.controller.refreshDriver(); assert.match(r.controller.state.message, /三项状态已刷新/);
  await r.controller.refresh();
  assert.equal(r.controller.state.message, ''); assert.equal(r.controller.state.tone, 'neutral');
});

test('one explicit refresh is one batch command and completed actions have no cooldown', async () => {
  const r = rig(); await r.open();
  assert.equal(await r.controller.refreshDriver(), true);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_REFRESH STEPPER=stepper_x']);
  assert.equal(await r.controller.refreshDriver(), true);
  assert.equal(r.posts.length, 2);
  assert.equal(r.controller.state.busy, false);
});

test('hostname and current printer selection gate POST even after initial connection', async () => {
  assert.equal(targetProblem({hostname: TEST_HOSTNAME}, pageUrl), '');
  assert.equal(targetProblem({hostname: TEST_HOSTNAME}, 'http://192.0.2.10/'), '');
  assert.ok(targetProblem({hostname: 'other'}, pageUrl));
  assert.ok(targetProblem({hostname: TEST_HOSTNAME},
    `http://192.0.2.10/?printer=${TEST_PRINTER_ID}&printer=other#/`));
  const r = rig(); let currentUrl = pageUrl; r.controller.pageUrl = () => currentUrl;
  await r.open(); currentUrl = 'http://192.0.2.10/?printer=wrong#/';
  await r.controller.refreshDriver(); assert.equal(r.posts.length, 0);
});

test('a missing or old backend cannot invoke a legacy command', async () => {
  const r = rig(); r.data.objects = ['webhooks']; await r.open();
  await r.controller.refreshDriver(); assert.equal(r.posts.length, 0);
  r.data.objects.push('driver_monitor'); delete r.monitor.auto_enabled;
  await r.controller.refresh(); await r.controller.refreshDriver();
  assert.equal(r.posts.length, 0);
});

test('printing and paused states permit monitoring but not-ready blocks a fresh action', async () => {
  for (const state of ['printing', 'paused']) {
    const r = rig(); r.data.status.print_stats = {state}; r.data.status.pause_resume = {is_paused: true};
    await r.open(); await r.controller.refreshDriver(); assert.equal(r.posts.length, 1);
  }
  const r = rig(); await r.open(); r.data.status.webhooks.state = 'shutdown';
  await r.controller.refreshDriver(); assert.equal(r.posts.length, 0);
});

test('busy prevents double-clicks and concurrent auto commands before the first await finishes', async () => {
  const r = rig(); await r.open(); const normal = r.api.refreshDriver; let release;
  r.api.refreshDriver = async (...args) => { await normal(...args); await new Promise(resolve => { release = resolve; }); };
  const pending = r.controller.refreshDriver(); await tick();
  assert.equal(r.controller.state.busy, true);
  await r.controller.refreshDriver(); await r.controller.setAuto(false);
  assert.equal(r.posts.length, 1);
  release(); await pending; assert.equal(r.controller.state.busy, false);
});

test('background active blocks manual refresh but still accepts explicit pause', async () => {
  const r = rig(); r.monitor.active = true; r.monitor.cycle_active = true; await r.open();
  await r.controller.refreshDriver(); assert.equal(r.posts.length, 0);
  assert.equal(await r.controller.setAuto(false), true);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_AUTO ENABLE=0']);
  assert.equal(r.controller.state.snapshot.status.driver_monitor.auto_enabled, false);
});

test('alarm shutdown protection rejects pause before any POST while keeping manual refresh available', async () => {
  const r = rig(); r.monitor.shutdown_on_alarm = true; await r.open();
  assert.match(r.controller.actionProblem('auto'), /保护已开启.*不能暂停/);
  assert.equal(await r.controller.setAuto(false), false);
  assert.deepEqual(r.posts, []);
  assert.equal(r.monitor.auto_enabled, true);
  assert.match(r.controller.state.message, /保护已开启/);
  assert.equal(await r.controller.refreshDriver(), true);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_REFRESH STEPPER=stepper_x']);
});

test('a fresh snapshot enabling alarm protection prevents an already requested pause POST', async () => {
  const r = rig(); await r.open(); const normal = r.api.snapshot;
  r.api.snapshot = async () => { r.monitor.shutdown_on_alarm = true; return normal(); };
  assert.equal(await r.controller.setAuto(false), false);
  assert.equal(r.posts.length, 0);
  assert.equal(r.monitor.auto_enabled, true);
  assert.match(r.controller.state.message, /保护已开启/);
});

test('alarm protection does not block an explicit resume when monitoring is paused', async () => {
  const r = rig(); r.monitor.shutdown_on_alarm = true; r.monitor.auto_enabled = false; await r.open();
  assert.equal(r.controller.actionProblem('auto'), '');
  assert.equal(await r.controller.setAuto(true), true);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_AUTO ENABLE=1']);
});

test('old backends and explicitly disabled alarm protection retain the pause action', async () => {
  for (const enabled of [undefined, false]) {
    const r = rig();
    if (enabled !== undefined) r.monitor.shutdown_on_alarm = enabled;
    await r.open();
    assert.equal(await r.controller.setAuto(false), true);
    assert.deepEqual(r.posts, ['DRIVER_MONITOR_AUTO ENABLE=0']);
  }
});

test('protection presentation distinguishes each axis, pending enable and unknown state', () => {
  const monitor = {shutdown_on_alarm: true, protection: {
    stepper_x: {enabled: false, armed: false},
    stepper_y: {enabled: true, armed: true},
    stepper_z: {enabled: true, armed: false},
  }};
  assert.deepEqual(alarmProtectionDisplay(monitor, 'stepper_x'),
    {label: '使能后报警停机', state: '未使能待命', tone: 'neutral'});
  assert.deepEqual(alarmProtectionDisplay(monitor, 'stepper_y'),
    {label: '使能后报警停机', state: '已使能监测', tone: 'success'});
  assert.equal(alarmProtectionDisplay(monitor, 'stepper_z').state, '等待使能生效');
  assert.equal(alarmProtectionDisplay(monitor, 'missing').state, '使能状态未知');
  monitor.protection.stepper_x.armed = true;
  assert.equal(alarmProtectionDisplay(monitor, 'stepper_x').state, '未使能待命');
});

test('legacy and disabled protection add no new per-axis state claim', () => {
  for (const monitor of [undefined, {}, {shutdown_on_alarm: false}, {shutdown_on_alarm: 'true'}])
    assert.equal(alarmProtectionDisplay(monitor, 'stepper_x'), null);
});

test('latched alarm shutdown presents its Chinese cause even after the backend leaves ready', () => {
  const monitor = {ready: false, shutdown_on_alarm: true, alarm_shutdown: {
    stepper: 'stepper_x', value: 2, reason: '驱动报警', message: 'stepper_x 驱动报警原值 2，已触发停机。',
  }};
  assert.equal(alarmProtectionDisplay(monitor, 'stepper_x').state, '已触发报警停机');
  assert.equal(alarmProtectionDisplay(monitor, 'stepper_y').state, '等待 Klipper 就绪');
  assert.equal(alarmShutdownMessage(monitor), '报警停机：stepper_x 驱动报警原值 2，已触发停机。');
  delete monitor.alarm_shutdown.message;
  assert.equal(alarmShutdownMessage(monitor), '报警停机：驱动报警');
  monitor.alarm_shutdown.reason = null;
  assert.match(alarmShutdownMessage(monitor), /已触发驱动报警停机/);
  monitor.alarm_shutdown = null;
  assert.equal(alarmShutdownMessage(monitor), '');
  assert.equal(alarmProtectionDisplay(monitor, 'stepper_x').state, '等待 Klipper 就绪');
});

test('hiding or removing a card does not pause backend monitoring', async () => {
  const r = rig(); await r.open();
  r.controller.setOpen(false); r.controller.setVisible(false);
  await r.controller.refreshDriver(); await r.controller.setAuto(false);
  assert.equal(r.posts.length, 0); assert.equal(r.monitor.auto_enabled, true);
});

test('leaving during the preflight GET prevents the pending POST', async () => {
  const r = rig(); await r.open(); const normal = r.api.snapshot; let release;
  r.api.snapshot = async () => { await new Promise(resolve => { release = resolve; }); return normal(); };
  const pending = r.controller.refreshDriver(); await tick();
  r.controller.setOpen(false); release(); await pending;
  assert.equal(r.posts.length, 0);
});

test('POST timeout gets a cache confirmation but never retries a command', async () => {
  const r = rig(); await r.open();
  r.api.refreshDriver = async stepper => {
    r.posts.push(buildRefreshCommand(stepper)); throw new ApiError('timeout: execution unknown', 0, true);
  };
  const initial = r.queries; assert.equal(await r.controller.refreshDriver(), false);
  assert.equal(r.posts.length, 1); assert.equal(r.queries - initial, 2);
  await r.controller.refresh(); assert.equal(r.posts.length, 1);
});

test('a partial failing batch preserves per-field time and never claims all three were refreshed', async () => {
  const r = rig(); for (const register of DYNAMIC_REGISTERS) r.update(register, 42);
  const previousSpeed = {...r.monitor.readings.stepper_x.MOTOR_SPEED}; await r.open();
  r.api.refreshDriver = async stepper => {
    r.posts.push(buildRefreshCommand(stepper)); r.update('ALARM_CODE', null, 'error');
    throw new ApiError('communication error', 400);
  };
  assert.equal(await r.controller.refreshDriver(), false);
  const readings = r.controller.state.snapshot.status.driver_monitor.readings.stepper_x;
  assert.equal(readings.ALARM_CODE.value, null);
  assert.equal(readings.MOTOR_SPEED.ended, previousSpeed.ended);
  assert.match(r.controller.state.message, /communication error/);
  assert.equal(r.posts.length, 1);
});

test('unchanged cached readings are not reported as a fresh three-field success', async () => {
  const r = rig(); await r.open(); await r.controller.refreshDriver();
  r.api.refreshDriver = async stepper => { r.posts.push(buildRefreshCommand(stepper)); };
  assert.equal(await r.controller.refreshDriver(), false);
  assert.match(r.controller.state.message, /未确认本次完整刷新/);
});

test('invalid or malformed fields display failure and never turn a raw error code into an alarm value', async () => {
  const r = rig(); await r.open(); const normal = r.api.refreshDriver;
  r.api.refreshDriver = async stepper => { await normal(stepper); r.update('ALARM_CODE', null, 'invalid'); };
  assert.equal(await r.controller.refreshDriver(), true);
  const view = readingDisplay(r.monitor.readings.stepper_x.ALARM_CODE, r.data, 1700000000000);
  assert.equal(view.value, '—'); assert.equal(view.status, '读取失败');
  assert.equal(readingDisplay({outcome: 'invalid', value: 4}, r.data, 0).value, '—');
  assert.equal(readingDisplay({outcome: 'ok', value: 1.5}, r.data, 0).status, '读取失败');
  assert.equal(readingDisplay(undefined, r.data, 0).status, '未读');
});

test('raw zero and current register remain numeric raw values, with individual monotonic timestamps', () => {
  const r = rig(), alarm = r.update('ALARM_CODE', 0), current = r.update('RUN_CURRENT', 960);
  assert.equal(readingDisplay(alarm, r.data, 1700000000000).value, '0');
  assert.equal(readingDisplay(current, r.data, 1700000000000).value, '960');
  assert.deepEqual(resultTime({ended: 99}, {eventtime: 100}, 1700000000000),
    {wallTime: 1699999999000, label: '完成时间'});
});

test('static readings older than 24 hours retain their sample time across later GET responses', () => {
  const reading = {ended: 100, outcome: 'ok', value: 960};
  const first = readingDisplay(reading, {eventtime: 90100}, 1700000000000);
  const later = readingDisplay(reading, {eventtime: 90103}, 1700000003000);
  assert.equal(first.wallTime, 1699910000000);
  assert.equal(later.wallTime, first.wallTime);
  assert.deepEqual(resultTime({}, {eventtime: 100}, 1700000000000),
    {wallTime: null, label: '采样时间未知'});
  assert.equal(resultTime({ended: 101}, {eventtime: 100}, 1700000000000).wallTime, null);
});

test('TMC-only configurations have no cards or driver actions, with or without the backend', async () => {
  const r = rig();
  r.monitor.drivers = [];
  r.data.objects.push('tmc2209 extruder', 'tmc5160 stepper_z');
  r.data.status['tmc2209 extruder'] = {run_current: 1.5, drv_status: {otpw: true}};
  await r.open();
  assert.deepEqual(driversFrom(r.data), []);
  assert.deepEqual(r.controller.state.drivers, []);
  assert.equal(r.controller.state.selectedKey, '');
  assert.match(r.controller.actionProblem('auto'), /尚未发现可监测的 LYX/);
  assert.equal(await r.controller.refreshDriver('tmc2209 extruder'), false);
  assert.equal(await r.controller.setAuto(true), false);
  delete r.data.status.driver_monitor;
  r.data.objects = r.data.objects.filter(name => name !== 'driver_monitor');
  await r.controller.refresh();
  assert.deepEqual(r.controller.state.drivers, []);
  assert.equal(r.posts.length, 0);
});

test('mixed backend entries keep distinct valid LYX cards and ignore TMC and duplicates', () => {
  const data = snapshot();
  data.objects.push('tmc2209 extruder');
  data.status.driver_monitor.drivers.push(
    {stepper: 'stepper_y', type: 'lyx9231'},
    {stepper: 'stepper_x', type: 'lyx9231'},
    {stepper: 'extruder', type: 'tmc2209'},
    {stepper: 'bad\nM112', type: 'lyx9231'});
  assert.deepEqual(driversFrom(data).map(driver => driver.key), ['lyx:stepper_x', 'lyx:stepper_y']);
});

test('snapshot queries never request TMC status for mixed or TMC-only printers', async () => {
  for (const hasMonitor of [true, false]) {
    const data = snapshot(), calls = [];
    data.objects.push('tmc2209 extruder', 'tmc5160 stepper_z');
    if (!hasMonitor) data.objects = data.objects.filter(name => name !== 'driver_monitor');
    const api = new MoonrakerApi(async (path, options) => {
      calls.push({path, options});
      let result;
      if (path === '/printer/info') result = data.info;
      else if (path === '/printer/objects/list') result = {objects: data.objects};
      else {
        const names = [...new URL(path, 'http://localhost').searchParams.keys()];
        assert.deepEqual(names, hasMonitor ? ['webhooks', 'driver_monitor'] : ['webhooks']);
        result = {eventtime: data.eventtime,
          status: Object.fromEntries(names.map(name => [name, data.status[name]]))};
      }
      return {ok: true, status: 200, json: async () => ({result})};
    });
    const result = await api.snapshot();
    assert.equal(calls.length, 3);
    assert.ok(calls.every(call => call.options.method === 'GET'));
    assert.ok(calls.every(call => !/tmc/i.test(call.path)));
    assert.equal(driversFrom(result).length, hasMonitor ? 1 : 0);
  }
});

test('command builders reject injections and non-boolean auto settings', () => {
  for (const name of ['stepper_x\nM112', 'stepper_x\n', 'stepper_x VALUE=1', ''])
    assert.throws(() => buildRefreshCommand(name));
  for (const value of [0, 1, '0', '1', null]) assert.throws(() => buildAutoCommand(value));
  assert.equal(buildAutoCommand(true), 'DRIVER_MONITOR_AUTO ENABLE=1');
});

test('browser fetch retains global receiver and each command uses one same-origin POST', async () => {
  const calls = [];
  const api = new MoonrakerApi(function (path, options) {
    assert.equal(this, globalThis); calls.push({path, options});
    return Promise.resolve({ok: true, status: 200, json: async () => ({result: 'ok'})});
  });
  await api.refreshDriver('stepper_x'); await api.setAuto(false);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.redirect, 'error');
  assert.deepEqual(JSON.parse(calls[0].options.body), {script: 'DRIVER_MONITOR_REFRESH STEPPER=stepper_x'});
  assert.deepEqual(JSON.parse(calls[1].options.body), {script: 'DRIVER_MONITOR_AUTO ENABLE=0'});
});

test('authentication failures and timeouts do not retry fetch', async () => {
  let calls = 0;
  const unauthorized = new MoonrakerApi(async () => { calls++; return {status: 401, ok: false}; });
  await assert.rejects(unauthorized.refreshDriver('stepper_x'), /未授权/); assert.equal(calls, 1);
  const timeout = new MoonrakerApi((path, options) => {
    calls++; return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
      const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
    }));
  }, 5);
  await assert.rejects(timeout.refreshDriver('stepper_x'), /不会自动重发/); assert.equal(calls, 2);
});

function trendFixture() {
  const data = snapshot(); data.eventtime = 1000;
  const monitor = data.status.driver_monitor;
  monitor.cycle_interval = 10;
  monitor.session_id = 'session-a';
  monitor.history = {stepper_x: {MOTOR_SPEED: [], ERROR_ANGLE: []}};
  const add = (register, ended, value, outcome = 'ok') => {
    monitor.history.stepper_x[register].push({seq: ended, stepper: 'stepper_x', register,
      ended, started: ended - .004, duration: .004, value, outcome});
  };
  return {data, monitor, add, view: register => trendData(data, 'stepper_x', register, 1700000000000)};
}

test('trends show only the selected series in the shared last-five-minute window', () => {
  const r = trendFixture();
  for (const time of [699, 700, 710, 990, 1000, 1001]) r.add('MOTOR_SPEED', time, 12);
  r.monitor.history.stepper_x.MOTOR_SPEED.push({stepper: 'stepper_y', register: 'MOTOR_SPEED', ended: 995, value: 999, outcome: 'ok'});
  const view = r.view('MOTOR_SPEED');
  assert.deepEqual(view.points.map(point => point.ended), [700, 710, 990, 1000]);
  assert.equal(view.start, 700); assert.equal(view.end, 1000);
  assert.equal(view.points[0].wallTime, 1699999700000);
});

test('failed trend samples retain a gap rather than becoming zero or joining adjacent successes', () => {
  const r = trendFixture(); r.add('MOTOR_SPEED', 970, 20);
  r.add('MOTOR_SPEED', 980, 4, 'invalid'); r.add('MOTOR_SPEED', 990, 30);
  const view = r.view('MOTOR_SPEED');
  assert.deepEqual(view.points.map(point => point.value), [20, null, 30]);
  assert.deepEqual(view.segments.map(segment => segment.map(point => point.value)), [[20], [30]]);
});

test('long telemetry gaps are not interpolated across paused or missing acquisition', () => {
  const r = trendFixture();
  r.add('ERROR_ANGLE', 930, 5); r.add('ERROR_ANGLE', 940, 6); r.add('ERROR_ANGLE', 980, 7);
  assert.deepEqual(r.view('ERROR_ANGLE').segments.map(segment => segment.length), [2, 1]);
});

test('zero is a valid trend sample, constant boundary values have a finite scale, and failures stay empty', () => {
  const r = trendFixture(); r.add('MOTOR_SPEED', 990, 0);
  const zero = r.view('MOTOR_SPEED');
  assert.equal(zero.empty, false); assert.equal(zero.points[0].value, 0); assert.ok(zero.yMax > zero.yMin);
  r.add('ERROR_ANGLE', 990, 65535);
  const maximum = r.view('ERROR_ANGLE'); assert.ok(maximum.yMax > maximum.yMin);
  r.monitor.history.stepper_x.MOTOR_SPEED = [];
  r.add('MOTOR_SPEED', 990, null, 'error');
  assert.equal(r.view('MOTOR_SPEED').empty, true);
  assert.equal(r.view('MOTOR_SPEED').segments.length, 0);
});

test('a backend session change starts from its own history instead of merging the previous curve', () => {
  const r = trendFixture(); r.add('MOTOR_SPEED', 990, 900);
  const previous = r.view('MOTOR_SPEED'); assert.equal(previous.points.length, 1);
  r.monitor.session_id = 'session-b'; r.monitor.history = {};
  const current = r.view('MOTOR_SPEED');
  assert.equal(current.sessionId, 'session-b'); assert.equal(current.points.length, 0);
  r.monitor.session_id = null;
  assert.equal(r.view('MOTOR_SPEED').empty, true);
});

test('speed and angle use independent raw-value scales with exactly the same time window', () => {
  const r = trendFixture();
  r.add('MOTOR_SPEED', 980, 1); r.add('MOTOR_SPEED', 990, 2);
  r.add('ERROR_ANGLE', 980, 50000); r.add('ERROR_ANGLE', 990, 60000);
  const speed = r.view('MOTOR_SPEED'), angle = r.view('ERROR_ANGLE');
  assert.equal(speed.start, angle.start); assert.equal(speed.end, angle.end);
  assert.ok(speed.yMax < angle.yMin);
  assert.deepEqual(angle.points.map(point => point.value), [50000, 60000]);
});

test('the schedule label uses the reported round and gap, without claiming an unknown order', () => {
  const monitor = snapshot().status.driver_monitor;
  assert.equal(monitorScheduleLabel(monitor), '报警→转速→角度误差 · 轮间隔 1 秒 · 项间隔 0.2 秒');
  monitor.cycle_interval = 3;
  assert.match(monitorScheduleLabel(monitor), /轮间隔 3 秒/);
  delete monitor.read_order;
  assert.match(monitorScheduleLabel(monitor), /^整轮读取/);
  delete monitor.cycle_interval; monitor.alarm_interval = 1; monitor.telemetry_interval = 10;
  assert.equal(monitorScheduleLabel(monitor), '报警 1 秒 / 转速角度 10 秒 · 项间隔 0.2 秒');
});

test('serial round trend gaps allow every driver read and gap, then break on a missing round', () => {
  const r = trendFixture(); r.monitor.cycle_interval = 1;
  for (const stepper of ['stepper_y', 'stepper_z', 'extruder'])
    r.monitor.drivers.push({stepper, type: 'lyx9231'});
  for (const {stepper} of r.monitor.drivers)
    r.monitor.readings[stepper] = Object.fromEntries(DYNAMIC_REGISTERS.map(register => [register, {duration: .4}]));
  // Four drivers need about 8 s per round, despite a configured 1 s post-round delay.
  for (const ended of [950, 958, 966, 990]) r.add('MOTOR_SPEED', ended, 123);
  assert.deepEqual(r.view('MOTOR_SPEED').segments.map(segment => segment.length), [3, 1]);
});

test('old or unknown backend trend cadence keeps the legacy fallback', () => {
  const r = trendFixture(); delete r.monitor.cycle_interval;
  for (const ended of [930, 940, 980]) r.add('ERROR_ANGLE', ended, 5);
  for (const telemetry of [10, undefined, NaN, -1]) {
    r.monitor.telemetry_interval = telemetry;
    assert.deepEqual(r.view('ERROR_ANGLE').segments.map(segment => segment.length), [2, 1]);
  }
});

test('fast round histories retain more than 120 samples inside the five-minute window', () => {
  const r = trendFixture(); r.monitor.cycle_interval = 1;
  for (let index = 0; index < 201; index++) r.add('MOTOR_SPEED', 700 + index * 1.5, index);
  const view = r.view('MOTOR_SPEED');
  assert.equal(view.points.length, 201); assert.equal(view.points[0].ended, 700);
  assert.equal(view.segments.length, 1);
});

function addSecondDriver(r) {
  r.monitor.drivers.push({stepper: 'stepper_y', type: 'lyx9231', registers: Object.keys(REGISTER_INFO)});
  r.data.objects.push('tmc2209 extruder');
  r.data.status['tmc2209 extruder'] = {run_current: .8, drv_status: {otpw: true}};
}

test('mixed configurations show only LYX cards and share passive cache refreshes', async () => {
  const r = rig(); addSecondDriver(r); await r.open();
  assert.deepEqual(r.controller.state.drivers.map(driver => driver.key),
    ['lyx:stepper_x', 'lyx:stepper_y']);
  await r.controller.refresh(); await r.controller.refresh();
  assert.equal(r.queries, 3); assert.equal(r.posts.length, 0); assert.equal(r.monitor.auto_enabled, true);
});

test('explicit card refresh changes only that driver and binds its notice without selecting it', async () => {
  const r = rig(); addSecondDriver(r);
  for (const register of DYNAMIC_REGISTERS) r.update(register, 123);
  const previousX = structuredClone(r.monitor.readings.stepper_x);
  await r.open(); assert.equal(r.controller.state.selectedKey, 'lyx:stepper_x');
  assert.equal(await r.controller.refreshDriver('lyx:stepper_y'), true);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_REFRESH STEPPER=stepper_y']);
  assert.deepEqual(r.monitor.readings.stepper_x, previousX);
  assert.equal(r.monitor.readings.stepper_y.ALARM_CODE.value, 0);
  assert.equal(r.controller.state.selectedKey, 'lyx:stepper_x');
  assert.equal(r.controller.state.messageKey, 'lyx:stepper_y');
  assert.match(r.controller.state.message, /三项状态已刷新/);
});

test('unknown, excluded TMC and removed cards never fall back to a different LYX driver', async () => {
  const r = rig(); addSecondDriver(r); await r.open();
  for (const key of ['lyx:missing', 'tmc2209 extruder'])
    assert.equal(await r.controller.refreshDriver(key), false);
  const normal = r.api.snapshot;
  r.api.snapshot = async () => {
    r.monitor.drivers = r.monitor.drivers.filter(driver => driver.stepper !== 'stepper_y');
    return normal();
  };
  assert.equal(await r.controller.refreshDriver('lyx:stepper_y'), false);
  assert.equal(r.posts.length, 0);
  assert.equal(r.controller.state.messageKey, null);
});

test('one pending card action locks all other card refreshes and the global AUTO control', async () => {
  const r = rig(); addSecondDriver(r); await r.open();
  const normal = r.api.refreshDriver; let release;
  r.api.refreshDriver = async stepper => {
    await normal(stepper); await new Promise(resolve => { release = resolve; });
  };
  const pending = r.controller.refreshDriver('lyx:stepper_y'); await tick();
  assert.equal(r.controller.state.busyKey, 'lyx:stepper_y');
  await r.controller.refreshDriver('lyx:stepper_x'); await r.controller.setAuto(false);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_REFRESH STEPPER=stepper_y']);
  assert.equal(r.monitor.auto_enabled, true);
  release(); await pending;
  assert.equal(r.controller.state.busy, false); assert.equal(r.controller.state.busyKey, null);
});

test('a different driver result or a new backend session cannot confirm a card action', async () => {
  for (const replaceSession of [false, true]) {
    const r = rig(); addSecondDriver(r); r.monitor.session_id = 'old'; await r.open();
    r.api.refreshDriver = async stepper => {
      r.posts.push(buildRefreshCommand(stepper));
      if (replaceSession) r.monitor.session_id = 'new';
      for (const register of DYNAMIC_REGISTERS)
        r.update(register, 0, 'ok', replaceSession ? stepper : 'stepper_x');
    };
    assert.equal(await r.controller.refreshDriver('lyx:stepper_y'), false);
    assert.equal(r.controller.state.messageKey, 'lyx:stepper_y');
    assert.match(r.controller.state.message, /未确认本次/);
    assert.equal(r.posts.length, 1);
  }
});

test('card errors stay scoped while connection errors and AUTO notices are global', async () => {
  const r = rig(); addSecondDriver(r); await r.open();
  r.api.refreshDriver = async stepper => {
    r.posts.push(buildRefreshCommand(stepper)); throw new ApiError('Y 查询失败', 400);
  };
  await r.controller.refreshDriver('lyx:stepper_y');
  assert.equal(r.controller.state.messageKey, 'lyx:stepper_y');
  assert.equal(r.controller.state.message, 'Y 查询失败');
  r.controller.chooseDriver('tmc2209 extruder');
  assert.equal(await r.controller.setAuto(false), true);
  assert.equal(r.controller.state.messageKey, null);
  assert.match(r.controller.state.message, /所有 LYX/);
  r.api.snapshot = async () => { throw new ApiError('离线'); };
  await r.controller.refresh();
  assert.equal(r.controller.state.messageKey, null);
  assert.equal(r.controller.state.message, '离线');
});

test('each driver trend retains its own data and scale in the shared time window', () => {
  const r = trendFixture();
  r.add('MOTOR_SPEED', 990, 12);
  r.monitor.history.stepper_y = {MOTOR_SPEED: [{seq: 2, stepper: 'stepper_y',
    register: 'MOTOR_SPEED', ended: 990, duration: .01, value: 840, outcome: 'ok'}]};
  const x = r.view('MOTOR_SPEED');
  const y = trendData(r.data, 'stepper_y', 'MOTOR_SPEED', 1700000000000);
  assert.equal(x.points[0].value, 12); assert.equal(y.points[0].value, 840);
  assert.ok(x.yMax < y.yMin); assert.equal(x.start, y.start); assert.equal(x.end, y.end);
});

test('verified API endpoint ids allow both configured query selections', () => {
  const endpoints = [
    ['http://192.0.2.10', '05ebc9d6261ffaaa5afdcf73e16633af'],
    ['http://192.0.2.10:7125', '4d413f32d7190d2e546ff8019f92f3ec'],
  ];
  assert.deepEqual(TEST_ENDPOINTS.map(endpoint => [endpoint.apiUrl, endpoint.printerId]), endpoints);
  for (const [apiUrl, printerId] of endpoints) {
    assert.equal(createHash('md5').update(apiUrl).digest('hex'), printerId);
    assert.equal(targetProblem({hostname: TEST_HOSTNAME},
      `http://192.0.2.10/?printer=${printerId}#/`), '');
  }
});

test('unverified page origins cannot bypass the target guard with an absent or verified printer id', () => {
  const origins = ['http://192.0.2.11', 'https://192.0.2.10',
    'http://192.0.2.10:7125', 'http://localhost:18763'];
  for (const origin of origins) for (const query of ['', `?printer=${TEST_PRINTER_ID}`])
    assert.ok(targetProblem({hostname: TEST_HOSTNAME}, `${origin}/${query}`));
});

test('offline preview requires an explicit true option on a loopback page only', () => {
  const info = {hostname: PREVIEW_HOSTNAME};
  for (const hostname of ['localhost', '127.0.0.1', '[::1]']) {
    const url = `http://${hostname}:18763/`;
    assert.ok(targetProblem(info, url));
    assert.ok(targetProblem(info, url, {preview: 'true'}));
    assert.equal(targetProblem(info, url, {preview: true}), '');
  }
  assert.ok(targetProblem(info, 'http://192.0.2.11/', {preview: true}));
  assert.ok(targetProblem(info, 'http://localhost.evil.example/', {preview: true}));
  assert.ok(targetProblem(info, 'file:///tmp/preview.html', {preview: true}));
});

test('a page origin change during preflight prevents POST even with a verified printer id', async () => {
  const r = rig(); let currentUrl = pageUrl;
  r.controller.pageUrl = () => currentUrl; await r.open();
  const normal = r.api.snapshot;
  r.api.snapshot = async () => {
    currentUrl = `http://192.0.2.11/?printer=${TEST_PRINTER_ID}#/`;
    return normal();
  };
  assert.equal(await r.controller.refreshDriver(), false);
  assert.equal(r.posts.length, 0);
});

test('endpoint aliases do not permit unknown ids, duplicate parameters or the wrong hostname', () => {
  const info = {hostname: TEST_HOSTNAME};
  for (const query of ['printer=unknown', 'printer=',
    `printer=${TEST_PRINTER_ID}&printer=${TEST_ENDPOINTS[1].printerId}`,
    `printer=${TEST_ENDPOINTS[1].printerId}&printer=${TEST_ENDPOINTS[1].printerId}`])
    assert.ok(targetProblem(info, `http://192.0.2.10/?${query}`));
  assert.ok(targetProblem({hostname: 'other'},
    `http://192.0.2.10/?printer=${TEST_ENDPOINTS[1].printerId}`));
  assert.ok(targetProblem({hostname: 'other'}, 'http://localhost:18763/', {preview: true}));
});

test('the controller rechecks live explicit preview permission before POST', async () => {
  const r = rig(); let htmlPreviewFlag = false;
  r.data.info.hostname = PREVIEW_HOSTNAME;
  r.controller.pageUrl = 'http://127.0.0.1:18763/';
  r.controller.targetOptions = () => ({preview: htmlPreviewFlag});
  await r.open();
  assert.equal(await r.controller.refreshDriver(), false); assert.equal(r.posts.length, 0);
  htmlPreviewFlag = true;
  assert.equal(await r.controller.refreshDriver(), true); assert.equal(r.posts.length, 1);
  const normal = r.api.snapshot;
  r.api.snapshot = async () => { htmlPreviewFlag = false; return normal(); };
  assert.equal(await r.controller.refreshDriver(), false); assert.equal(r.posts.length, 1);
});

test('source defaults are empty and reject every real action until configured', async () => {
  assert.deepEqual(TARGET_CONFIG, {hostname: '', pageOrigins: [], apiEndpoints: []});
  assert.match(checkTargetProblem({hostname: TEST_HOSTNAME}, pageUrl), /配置为空或无效/);
  const r = rig({targetConfig: undefined}); await r.open();
  assert.equal(await r.controller.refreshDriver(), false);
  assert.equal(await r.controller.setAuto(false), false);
  assert.equal(await r.controller.setAuto(true), false);
  assert.deepEqual(r.posts, []);
});

test('different installations cannot reuse another target hostname, origin or printer id', () => {
  const apiUrl = 'http://198.51.100.20:7125';
  const config = {hostname: 'printer-lab-b', pageOrigins: ['http://198.51.100.20'],
    apiEndpoints: [{apiUrl, printerId: createHash('md5').update(apiUrl).digest('hex')}]};
  const url = `${config.pageOrigins[0]}/?printer=${config.apiEndpoints[0].printerId}#/`;
  assert.equal(checkTargetProblem({hostname: config.hostname}, url, {}, config), '');
  assert.ok(checkTargetProblem({hostname: TEST_HOSTNAME}, url, {}, config));
  assert.ok(checkTargetProblem({hostname: config.hostname}, pageUrl, {}, config));
  assert.ok(checkTargetProblem({hostname: config.hostname},
    `${config.pageOrigins[0]}/?printer=${TEST_PRINTER_ID}`, {}, config));
  assert.ok(checkTargetProblem({hostname: config.hostname}, url, {}, TEST_CONFIG));
});

test('page origins are explicitly configured independently from API endpoint aliases', () => {
  const config = {...TEST_CONFIG, pageOrigins: [...TEST_CONFIG.pageOrigins, 'https://printer.example']};
  for (const origin of config.pageOrigins)
    assert.equal(checkTargetProblem({hostname: TEST_HOSTNAME},
      `${origin}/?printer=${TEST_ENDPOINTS[1].printerId}`, {}, config), '');
  assert.ok(checkTargetProblem({hostname: TEST_HOSTNAME}, `${TEST_ENDPOINTS[1].apiUrl}/`, {}, config));
  assert.ok(checkTargetProblem({hostname: TEST_HOSTNAME}, 'http://user:secret@192.0.2.10/', {}, config));
});

test('malformed and ambiguous target configuration fails closed without throwing', () => {
  const invalid = [null, {}, {...TEST_CONFIG, hostname: ' '}, {...TEST_CONFIG, hostname: ' printer-test'},
    {...TEST_CONFIG, pageOrigins: []}, {...TEST_CONFIG, apiEndpoints: []},
    {...TEST_CONFIG, pageOrigins: ['http://192.0.2.10/']},
    {...TEST_CONFIG, pageOrigins: ['http://192.0.2.10/path']},
    {...TEST_CONFIG, pageOrigins: ['file:///tmp/preview.html']},
    {...TEST_CONFIG, pageOrigins: [...TEST_CONFIG.pageOrigins, ...TEST_CONFIG.pageOrigins]},
    {...TEST_CONFIG, apiEndpoints: [null]},
    {...TEST_CONFIG, apiEndpoints: [{...TEST_ENDPOINTS[0], printerId: 'unknown'}]},
    {...TEST_CONFIG, apiEndpoints: [TEST_ENDPOINTS[0], {...TEST_ENDPOINTS[0]}]},
    {...TEST_CONFIG, apiEndpoints: [TEST_ENDPOINTS[0], {...TEST_ENDPOINTS[1], printerId: TEST_PRINTER_ID}]}];
  for (const apiUrl of ['ftp://192.0.2.10', 'http://user:secret@192.0.2.10',
    'http://192.0.2.10?key=value', 'http://192.0.2.10/#secret', ' http://192.0.2.10'])
    invalid.push({...TEST_CONFIG, apiEndpoints: [{apiUrl, printerId: TEST_PRINTER_ID}]});
  for (const config of invalid)
    assert.match(checkTargetProblem({hostname: TEST_HOSTNAME}, pageUrl, {}, config), /配置为空或无效/);
});

test('a live configuration change during fresh preflight blocks both kinds of POST', async () => {
  for (const action of ['refresh', 'auto']) for (const replacement of [TARGET_CONFIG,
    {...TEST_CONFIG, hostname: 'printer-lab-b'}]) {
    let config = TEST_CONFIG;
    const r = rig({targetConfig: () => config}); await r.open();
    const normal = r.api.snapshot;
    r.api.snapshot = async () => { config = replacement; return normal(); };
    const result = action === 'refresh' ? await r.controller.refreshDriver() : await r.controller.setAuto(false);
    assert.equal(result, false); assert.deepEqual(r.posts, []);
  }
});

test('explicit loopback preview works with empty defaults only for its demo hostname', () => {
  const options = {preview: true}, info = {hostname: PREVIEW_HOSTNAME};
  for (const hostname of ['localhost', '127.0.0.1', '[::1]']) {
    const url = `http://${hostname}:18763/`;
    assert.equal(checkTargetProblem(info, url, options), '');
    assert.ok(checkTargetProblem(info, url));
    assert.ok(checkTargetProblem({hostname: TEST_HOSTNAME}, url, options));
    assert.ok(checkTargetProblem(info, `${url}?printer=${TEST_PRINTER_ID}`, options));
    assert.ok(checkTargetProblem(info, `${url}?printer=a&printer=a`, options));
  }
  assert.ok(checkTargetProblem(info, 'http://192.0.2.10/', options));
});

test('an empty-config demo controller stays passive until an explicit simulated action', async () => {
  const r = rig({targetConfig: undefined, targetOptions: {preview: true},
    pageUrl: 'http://127.0.0.1:18763/'});
  r.data.info.hostname = PREVIEW_HOSTNAME;
  await r.open(); await r.controller.refresh();
  assert.deepEqual(r.posts, []);
  assert.equal(await r.controller.refreshDriver(), true);
  assert.deepEqual(r.posts, ['DRIVER_MONITOR_REFRESH STEPPER=stepper_x']);
});
