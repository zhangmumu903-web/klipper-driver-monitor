import {TARGET_CONFIG} from './driver-monitor-config.mjs';

export const PREVIEW_HOSTNAME = 'driver-monitor-demo';
export const REGISTER_INFO = Object.freeze({
  CHIP_MODEL: ['芯片型号', '0x03'],
  RUN_CURRENT: ['运行电流寄存器', '0x13'],
  ALARM_CODE: ['报警寄存器', '0x08'],
  MOTOR_SPEED: ['电机速度原值', '0x0E'],
  ERROR_ANGLE: ['误差角原值', '0x10'],
});
export const DYNAMIC_REGISTERS = Object.freeze(['ALARM_CODE', 'MOTOR_SPEED', 'ERROR_ANGLE']);
const safeName = value => typeof value === 'string' && value.length > 0
  && !/[^A-Za-z0-9_]/.test(value);

export class ApiError extends Error {
  constructor(message, status = 0, uncertain = false) {
    super(message); this.name = 'ApiError'; this.status = status;
    this.uncertain = uncertain;
  }
}

function validTargetConfig(config) {
  if (!config || typeof config.hostname !== 'string' || !config.hostname.trim()
      || config.hostname !== config.hostname.trim()
      || !Array.isArray(config.pageOrigins) || !config.pageOrigins.length
      || !Array.isArray(config.apiEndpoints) || !config.apiEndpoints.length) return false;
  const publicUrl = value => {
    if (typeof value !== 'string' || value !== value.trim()) return null;
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
        && !url.search && !url.hash ? url : null;
    } catch { return null; }
  };
  if (!config.pageOrigins.every(origin => publicUrl(origin)?.origin === origin)
      || new Set(config.pageOrigins).size !== config.pageOrigins.length) return false;
  if (!config.apiEndpoints.every(endpoint => endpoint && publicUrl(endpoint.apiUrl)
      && typeof endpoint.printerId === 'string' && /^[0-9a-f]{32}$/.test(endpoint.printerId))) return false;
  return new Set(config.apiEndpoints.map(endpoint => endpoint.apiUrl)).size === config.apiEndpoints.length
    && new Set(config.apiEndpoints.map(endpoint => endpoint.printerId)).size === config.apiEndpoints.length;
}

export function targetProblem(info, pageUrl, options = {}, targetConfig = TARGET_CONFIG) {
  let url;
  try { url = new URL(pageUrl); }
  catch { return '页面地址无效，无法核对目标打印机。'; }
  if (url.username || url.password) return '页面地址含登录信息，无法核对目标打印机。';
  const preview = options?.preview === true
    && ['http:', 'https:'].includes(url.protocol)
    && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  const configured = validTargetConfig(targetConfig);
  if (!preview && !configured) return '目标配置为空或无效；请先生成已核对的安装配置。';
  if (!preview && !targetConfig.pageOrigins.includes(url.origin))
    return '页面来源不匹配；请打开安装时已确认的 Fluidd 页面。';
  const ids = url.searchParams.getAll('printer');
  if (ids.length && (ids.length !== 1
      || !configured || !targetConfig.apiEndpoints.some(endpoint => endpoint.printerId === ids[0])))
    return '页面选择了未核对的打印机；请切回已确认的目标。';
  const hostname = preview ? PREVIEW_HOSTNAME : targetConfig.hostname;
  if (info?.hostname !== hostname)
    return `目标不匹配：仅允许 ${hostname}。`;
  return '';
}

export function buildRefreshCommand(stepper) {
  if (!safeName(stepper)) throw new Error('驱动不在允许范围内。');
  return `DRIVER_MONITOR_REFRESH STEPPER=${stepper}`;
}

export function buildAutoCommand(enabled) {
  if (typeof enabled !== 'boolean') throw new Error('自动监测状态必须是布尔值。');
  return `DRIVER_MONITOR_AUTO ENABLE=${enabled ? 1 : 0}`;
}

export function driversFrom(snapshot) {
  const monitor = snapshot.status?.driver_monitor;
  const entries = Array.isArray(monitor?.drivers) ? monitor.drivers : [];
  const drivers = [];
  for (const item of entries) {
    if (item?.type !== 'lyx9231' || !safeName(item.stepper)) continue;
    if (drivers.some(d => d.key === `lyx:${item.stepper}`)) continue;
    drivers.push({key: `lyx:${item.stepper}`, stepper: item.stepper,
      type: 'lyx9231', mode: 'active',
      registers: Object.keys(REGISTER_INFO).filter(r => item.registers?.includes(r))});
  }
  return drivers;
}

export function rawValue(result) {
  if (result?.outcome !== 'ok' || !Number.isInteger(result.value)
      || result.value < 0 || result.value > 65535) return '无有效数值';
  return `${result.value}  ·  0x${result.value.toString(16).padStart(4, '0').toUpperCase()}`;
}

export function resultTime(result, snapshot, receivedAt) {
  const eventtime = snapshot?.eventtime;
  const ended = result?.ended;
  const age = eventtime - ended;
  // Backend timestamps are monotonic seconds, never Unix timestamps.
  if (Number.isFinite(eventtime) && Number.isFinite(ended) && Number.isFinite(receivedAt)
      && Number.isFinite(age) && age >= 0)
    return {wallTime: receivedAt - age * 1000, label: '完成时间'};
  return {wallTime: null, label: '采样时间未知'};
}

export class MoonrakerApi {
  constructor(fetchImpl = globalThis.fetch, timeoutMs = 10000) {
    this.fetch = fetchImpl.bind(globalThis); this.timeoutMs = timeoutMs;
  }
  async request(path, script) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetch(path, {
        method: script === undefined ? 'GET' : 'POST',
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: script === undefined ? {} : {'Content-Type': 'application/json'},
        body: script === undefined ? undefined : JSON.stringify({script}),
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403)
        throw new ApiError('未授权：请先在 Fluidd 登录，再刷新状态。', response.status);
      let body;
      try { body = await response.json(); }
      catch { throw new ApiError('接口未返回有效 JSON。', response.status); }
      if (!response.ok || !Object.hasOwn(body, 'result'))
        throw new ApiError(body?.error?.message || `接口错误 HTTP ${response.status}`, response.status);
      return body.result;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error.name === 'AbortError')
        throw new ApiError(script === undefined ? '状态查询超时。'
          : '请求超时，是否执行未知；不会自动重发，请查看后续状态。', 0, script !== undefined);
      throw new ApiError(script === undefined ? '无法连接当前页面的 Moonraker。'
        : '请求连接中断，是否执行未知；不会自动重发，请查看后续状态。', 0, script !== undefined);
    } finally { clearTimeout(timer); }
  }
  async snapshot() {
    const [info, listing] = await Promise.all([
      this.request('/printer/info'), this.request('/printer/objects/list'),
    ]);
    if (!Array.isArray(listing?.objects)) throw new ApiError('对象列表格式不兼容。');
    const objects = listing.objects;
    const wanted = ['webhooks', 'driver_monitor']
      .filter(n => objects.includes(n));
    if (!wanted.length) throw new ApiError('未发现可查询的 Klipper 状态对象。');
    const query = await this.request('/printer/objects/query?'
      + wanted.map(encodeURIComponent).join('&'));
    if (!query?.status || typeof query.status !== 'object')
      throw new ApiError('状态对象格式不兼容。');
    return {info, objects, status: query.status, eventtime: query.eventtime};
  }
  refreshDriver(stepper) {
    return this.request('/printer/gcode/script', buildRefreshCommand(stepper));
  }
  setAuto(enabled) {
    return this.request('/printer/gcode/script', buildAutoCommand(enabled));
  }
}

export function readingDisplay(reading, snapshot, receivedAt) {
  if (!reading) return {value: '—', status: '未读', tone: 'neutral', wallTime: null};
  const valid = reading.outcome === 'ok' && Number.isInteger(reading.value)
    && reading.value >= 0 && reading.value <= 65535;
  return {value: valid ? String(reading.value) : '—',
    status: valid ? '读取成功' : '读取失败',
    tone: valid ? 'success' : 'warning',
    ...resultTime(reading, snapshot, receivedAt)};
}

export function monitorScheduleLabel(monitor) {
  const gap = Number.isFinite(monitor?.read_gap) && monitor.read_gap >= 0
    ? ` · 项间隔 ${monitor.read_gap} 秒` : '';
  if (Number.isFinite(monitor?.cycle_interval) && monitor.cycle_interval > 0) {
    const labels = {ALARM_CODE: '报警', MOTOR_SPEED: '转速', ERROR_ANGLE: '角度误差'};
    const order = Array.isArray(monitor.read_order) && monitor.read_order.length === 3
      && monitor.read_order.every((register, index) => register === DYNAMIC_REGISTERS[index])
      ? monitor.read_order.map(register => labels[register]).join('→') : '整轮读取';
    return `${order} · 轮间隔 ${monitor.cycle_interval} 秒${gap}`;
  }
  const alarm = Number.isFinite(monitor?.alarm_interval) ? monitor.alarm_interval : '—';
  const telemetry = Number.isFinite(monitor?.telemetry_interval) ? monitor.telemetry_interval : '—';
  return `报警 ${alarm} 秒 / 转速角度 ${telemetry} 秒${gap}`;
}

export function alarmProtectionDisplay(monitor, stepper) {
  if (monitor?.shutdown_on_alarm !== true) return null;
  const protection = monitor.protection?.[stepper];
  let state = '使能状态未知', tone = 'warning';
  if (monitor.alarm_shutdown?.stepper === stepper) { state = '已触发报警停机'; tone = 'error'; }
  else if (monitor.ready === false) state = '等待 Klipper 就绪';
  else if (protection?.enabled === false) { state = '未使能待命'; tone = 'neutral'; }
  else if (protection?.enabled === true && protection.armed === true) {
    state = '已使能监测'; tone = 'success';
  } else if (protection?.enabled === true) state = '等待使能生效';
  return {label: '使能后报警停机', state, tone};
}

export function alarmShutdownMessage(monitor) {
  const alarm = monitor?.alarm_shutdown;
  if (!alarm || typeof alarm !== 'object') return '';
  const detail = [alarm.message, alarm.reason].find(value => typeof value === 'string' && value.trim());
  return detail ? `报警停机：${detail.trim()}` : '已触发驱动报警停机，请查看 Klipper 故障信息。';
}

function trendInterval(monitor) {
  if (!Number.isFinite(monitor?.cycle_interval) || monitor.cycle_interval <= 0)
    return Number.isFinite(monitor?.telemetry_interval) && monitor.telemetry_interval > 0
      ? monitor.telemetry_interval : 10;
  const drivers = [...new Set((Array.isArray(monitor.drivers) ? monitor.drivers : [])
    .filter(driver => driver?.type === 'lyx9231' && safeName(driver.stepper))
    .map(driver => driver.stepper))];
  const gap = Number.isFinite(monitor.read_gap) && monitor.read_gap >= 0 ? monitor.read_gap : .2;
  let duration = 0;
  for (const stepper of drivers) for (const register of DYNAMIC_REGISTERS) {
    const elapsed = monitor.readings?.[stepper]?.[register]?.duration;
    if (Number.isFinite(elapsed) && elapsed > 0) duration += elapsed;
  }
  // The configured delay starts AFTER every driver's three reads. Include their
  // gaps and cached read durations so a normal serial round is not a missing sample.
  return monitor.cycle_interval + Math.max(1, drivers.length) * 3 * gap + duration;
}

export function trendData(snapshot, stepper, register, receivedAt) {
  const monitor = snapshot?.status?.driver_monitor;
  const sessionId = typeof monitor?.session_id === 'string' ? monitor.session_id : null;
  const end = Number.isFinite(snapshot?.eventtime) ? snapshot.eventtime : 0;
  const start = end - 300;
  const interval = trendInterval(monitor);
  const history = monitor?.history?.[stepper]?.[register];
  const allowed = register === 'MOTOR_SPEED' || register === 'ERROR_ANGLE';
  const points = sessionId && allowed && Array.isArray(history) ? history.slice(-600)
    .filter(reading => reading?.stepper === stepper && reading.register === register
      && Number.isFinite(reading.ended) && reading.ended >= start && reading.ended <= end)
    .sort((a, b) => a.ended - b.ended)
    .map(reading => {
      const valid = reading.outcome === 'ok' && Number.isInteger(reading.value)
        && reading.value >= 0 && reading.value <= 65535;
      return {seq: reading.seq, ended: reading.ended, value: valid ? reading.value : null,
        outcome: valid ? 'ok' : 'invalid', ...resultTime(reading, snapshot, receivedAt)};
    }) : [];
  const segments = [];
  let current = null, previous = null;
  for (const point of points) {
    if (point.value === null) { current = null; previous = null; continue; }
    if (!current || point.ended - previous.ended > interval * 2) {
      current = []; segments.push(current);
    }
    current.push(point); previous = point;
  }
  const values = points.filter(point => point.value !== null).map(point => point.value);
  const low = values.length ? Math.min(...values) : 0;
  const high = values.length ? Math.max(...values) : 1;
  const padding = Math.max(1, (high - low) * .08);
  return {sessionId, start, end, points, segments, empty: values.length === 0,
    yMin: Math.max(0, Math.floor(low - padding)), yMax: Math.min(65535, Math.ceil(high + padding))};
}

const readingKey = reading => reading
  ? JSON.stringify([reading.seq, reading.ended, reading.stepper, reading.register]) : null;

export class MonitorController {
  constructor({api, pageUrl, targetOptions = {}, targetConfig = TARGET_CONFIG,
    onChange = () => {}, wallNow = () => Date.now()}) {
    this.api = api; this.pageUrl = pageUrl; this.onChange = onChange; this.wallNow = wallNow;
    this.targetOptions = targetOptions;
    this.targetConfig = targetConfig;
    this.refreshPromise = null; this.epoch = 0;
    this.state = {open: false, visible: true, connected: false, busy: false,
      refreshing: false, snapshot: null, drivers: [], selectedKey: '', receivedAt: null,
      busyKey: null, messageKey: null, message: '正在读取状态…', tone: 'neutral'};
  }
  emit() { this.onChange(this.state); }
  selected() { return this.state.drivers.find(driver => driver.key === this.state.selectedKey); }
  safetyProblem() {
    const s = this.state;
    if (!s.connected || !s.snapshot) return '尚未连接到目标打印机。';
    const target = targetProblem(s.snapshot.info,
      typeof this.pageUrl === 'function' ? this.pageUrl() : this.pageUrl,
      typeof this.targetOptions === 'function' ? this.targetOptions() : this.targetOptions,
      typeof this.targetConfig === 'function' ? this.targetConfig() : this.targetConfig);
    if (target) return target;
    if (s.snapshot.status.webhooks?.state !== 'ready') return 'Klipper 尚未 ready。';
    return ''; // Monitoring is allowed while printing; it does not move or heat anything.
  }
  actionProblem(kind, key = this.state.selectedKey, enabled) {
    const s = this.state;
    if (!s.open || !s.visible) return '当前不在可见的仪表板。';
    const unsafe = this.safetyProblem();
    if (unsafe) return unsafe;
    if (!s.snapshot.objects.includes('driver_monitor')) return '未安装监测后端。';
    const monitor = s.snapshot.status.driver_monitor;
    if (monitor?.schema_version !== 1 || typeof monitor.auto_enabled !== 'boolean'
        || !monitor.readings || typeof monitor.readings !== 'object') return '监测后端状态版本不兼容。';
    if (monitor.ready !== true) return '监测后端尚未 ready。';
    if (kind === 'auto') {
      if (!s.drivers.some(driver => driver.mode === 'active')) return '尚未发现可监测的 LYX 驱动。';
      const pausing = enabled === false || (enabled === undefined && monitor.auto_enabled === true);
      if (pausing && monitor.shutdown_on_alarm === true)
        return '报警停机保护已开启，不能暂停全部 LYX 监测。';
    } else {
      const driver = s.drivers.find(item => item.key === key);
      if (!driver) return '驱动已移除或不在允许范围内。';
      if (driver.mode !== 'active') return '驱动不支持现场读取。';
    }
    if (kind === 'refresh' && (monitor.active === true || monitor.cycle_active === true))
      return '后台正在读取，请等本轮完成。';
    return '';
  }
  async refresh({fresh = false} = {}) {
    if (this.refreshPromise) {
      if (!fresh) return this.refreshPromise;
      await this.refreshPromise;
    }
    this.state.refreshing = true; this.emit();
    this.refreshPromise = (async () => {
      try {
        const snapshot = await this.api.snapshot(), s = this.state;
        s.snapshot = snapshot; s.connected = true; s.receivedAt = this.wallNow();
        s.drivers = driversFrom(snapshot);
        if (!s.drivers.some(driver => driver.key === s.selectedKey)) s.selectedKey = s.drivers[0]?.key || '';
        const unsafe = this.safetyProblem();
        if (unsafe) { s.message = unsafe; s.tone = 'warning'; s.messageKey = null; }
        else if (!s.busy) { s.message = ''; s.tone = 'neutral'; s.messageKey = null; }
        return true;
      } catch (error) {
        this.state.connected = false; this.state.message = error.message;
        this.state.tone = 'error'; this.state.messageKey = null; return false;
      } finally { this.state.refreshing = false; this.emit(); }
    })();
    try { return await this.refreshPromise; }
    finally { this.refreshPromise = null; }
  }
  setOpen(open) { this.state.open = open; if (!open) this.epoch++; this.emit(); }
  setVisible(visible) { this.state.visible = visible; if (!visible) this.epoch++; this.emit(); }
  chooseDriver(key) {
    if (this.state.busy || !this.state.drivers.some(driver => driver.key === key)) return;
    this.epoch++; this.state.selectedKey = key; this.emit();
  }
  refreshDriver(key = this.state.selectedKey) { return this.runAction('refresh', undefined, key); }
  setAuto(enabled) {
    buildAutoCommand(enabled); // Validate before entering the asynchronous command path.
    return this.runAction('auto', enabled);
  }
  async runAction(kind, enabled, key = null) {
    const s = this.state;
    if (s.busy) return false;
    const target = kind === 'refresh' ? s.drivers.find(driver => driver.key === key) : null;
    const notice = (message, tone, messageKey = target?.key || null) => {
      s.message = message; s.tone = tone; s.messageKey = messageKey;
    };
    const initialProblem = this.actionProblem(kind, key, enabled);
    if (initialProblem) { notice(initialProblem, 'warning'); this.emit(); return false; }
    const ticket = this.epoch, stepper = target?.stepper;
    s.busy = true; s.message = kind === 'refresh' ? '正在刷新三项状态…' : '正在更新监测状态…';
    s.busyKey = s.messageKey = target?.key || null;
    s.tone = 'neutral'; this.emit();
    try {
      if (!await this.refresh({fresh: true})) return false;
      const problem = this.actionProblem(kind, key, enabled);
      const current = kind === 'refresh' ? s.drivers.find(driver => driver.key === key) : null;
      if (problem || ticket !== this.epoch || (target &&
          (current?.stepper !== stepper || current?.type !== target.type || current?.mode !== target.mode))) {
        notice(problem || '页面或驱动已变化，本次未发送。', 'warning', current?.key || null); return false;
      }
      const before = s.snapshot.status.driver_monitor;
      const session = before.session_id;
      const previous = kind === 'refresh' ? Object.fromEntries(DYNAMIC_REGISTERS.map(register =>
        [register, readingKey(before.readings[stepper]?.[register])])) : {};
      let postError = null;
      try {
        if (kind === 'refresh') await this.api.refreshDriver(stepper);
        else await this.api.setAuto(enabled);
      } catch (error) { postError = error; }
      // Exactly one GET confirmation after each POST, even after error/timeout. Never repost.
      const refreshed = await this.refresh({fresh: true});
      if (postError) { notice(postError.message, 'error'); return false; }
      if (!refreshed) return false;
      const unsafe = this.safetyProblem();
      if (unsafe) { notice(unsafe, 'warning', null); return false; }
      const monitor = s.snapshot.status.driver_monitor;
      if (kind === 'auto') {
        if (monitor?.auto_enabled !== enabled) {
          notice('未确认监测状态变化，请查看当前缓存。', 'warning'); return false;
        }
        notice(enabled ? '所有 LYX 驱动的后台监测已恢复。'
          : '所有 LYX 驱动的后台监测已暂停；正在进行的一次完整查询可能仍会完成。', 'neutral');
        return true;
      }
      if (!s.drivers.some(driver => driver.key === key)) {
        notice('目标驱动已移除，未确认本次刷新。', 'warning', null); return false;
      }
      if (monitor?.session_id !== session) {
        notice('监测会话已变化，未确认本次刷新；当前显示新会话缓存。', 'warning'); return false;
      }
      const readings = DYNAMIC_REGISTERS.map(register => monitor?.readings?.[stepper]?.[register]);
      const complete = readings.every((reading, index) => reading
        && reading.stepper === stepper && reading.register === DYNAMIC_REGISTERS[index]
        && readingKey(reading) !== previous[DYNAMIC_REGISTERS[index]]);
      if (!complete) {
        notice('未确认本次完整刷新，当前显示后台缓存。', 'warning'); return false;
      }
      const valid = readings.every(reading => readingDisplay(reading, s.snapshot, s.receivedAt).status === '读取成功');
      notice(valid ? '三项状态已刷新。' : '三项已尝试读取，失败项没有有效数值。',
        valid ? 'success' : 'warning'); return true;
    } finally { s.busy = false; s.busyKey = null; this.emit(); }
  }
}
