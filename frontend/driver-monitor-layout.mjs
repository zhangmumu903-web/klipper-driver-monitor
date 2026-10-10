// Presentation only: these files never select a printer or issue UART commands.
export const CUSTOMIZATION_PATHS = Object.freeze({
  layout: '/driver-monitor-user/layout.json',
  style: '/driver-monitor-user/custom.css',
  renderer: '/driver-monitor-user/custom-renderer.js',
});
export const DEFAULT_LAYOUT = Object.freeze({
  schema_version: 1, mode: 'cards', axis_order: Object.freeze([]),
  axis_names: Object.freeze({}), show: Object.freeze({
    speed: true, angle: true, trends: true, driver_details: true,
  }), custom_renderer: Object.freeze({enabled: false}),
});
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const axisName = value => typeof value === 'string' && /^[a-z][a-z0-9_]*(?: [a-zA-Z0-9_-]+)?$/.test(value)
  && value.length <= 96 && !['constructor', 'prototype', '__proto__'].includes(value);
function keys(value, allowed, path) {
  if (!record(value)) throw new Error(`${path} 必须是对象`);
  for (const key of Object.keys(value)) if (!allowed.includes(key))
    throw new Error(`${path}.${key} 不是支持的设置`);
}

export function validateLayout(input) {
  keys(input, ['schema_version', 'mode', 'axis_order', 'axis_names', 'show', 'custom_renderer'], 'layout');
  if (input.schema_version !== 1) throw new Error('schema_version 必须为 1');
  const mode = input.mode ?? DEFAULT_LAYOUT.mode;
  if (!['cards', 'compact', 'z-overview'].includes(mode)) throw new Error('mode 必须为 cards、compact 或 z-overview');
  const order = input.axis_order ?? [];
  if (!Array.isArray(order) || order.length > 128 || order.some(name => !axisName(name)))
    throw new Error('axis_order 必须是最多 128 个有效驱动名称的数组');
  const names = input.axis_names ?? {};
  if (!record(names) || Object.keys(names).length > 128) throw new Error('axis_names 必须是驱动名称到显示名称的对象');
  for (const [key, value] of Object.entries(names)) {
    if (!axisName(key) || typeof value !== 'string' || !value.trim() || value.length > 80 || /[\u0000-\u001f\u007f]/.test(value))
      throw new Error('axis_names 的名称必须为 1–80 字符的文本');
  }
  const show = input.show ?? {};
  keys(show, Object.keys(DEFAULT_LAYOUT.show), 'show');
  for (const value of Object.values(show)) if (typeof value !== 'boolean') throw new Error('show 的值必须为 true 或 false');
  const renderer = input.custom_renderer ?? {};
  keys(renderer, ['enabled'], 'custom_renderer');
  if ('enabled' in renderer && typeof renderer.enabled !== 'boolean') throw new Error('custom_renderer.enabled 必须为 true 或 false');
  return Object.freeze({schema_version: 1, mode, axis_order: Object.freeze([...new Set(order)]),
    axis_names: Object.freeze({...names}), show: Object.freeze({...DEFAULT_LAYOUT.show, ...show}),
    custom_renderer: Object.freeze({enabled: renderer.enabled === true})});
}

export async function loadLayout(fetcher = globalThis.fetch, timeoutMs = 4000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(CUSTOMIZATION_PATHS.layout, {
      cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
    });
    if (!response.ok) throw new Error(response.status === 404 ? '未找到 layout.json' : `读取失败（HTTP ${response.status}）`);
    const text = await response.text();
    if (text.length > 32768) throw new Error('layout.json 超过 32 KiB');
    return {layout: validateLayout(JSON.parse(text)), warning: ''};
  } catch (error) {
    const reason = error?.name === 'AbortError' ? '读取超时' : error?.message;
    return {layout: DEFAULT_LAYOUT, warning: `外观配置未加载，已使用默认卡片：${reason || '配置无效'}。后台监测不受影响。`};
  } finally { clearTimeout(timer); }
}

export function displayName(driver, layout) {
  return Object.hasOwn(layout.axis_names, driver.stepper) ? layout.axis_names[driver.stepper] : driver.stepper;
}
export const isZDriver = driver => /^stepper_z\d*$/.test(driver.stepper);
export function orderDrivers(drivers, layout) {
  const positions = new Map(layout.axis_order.map((name, index) => [name, index]));
  return drivers.map((driver, index) => ({driver, index})).sort((a, b) =>
    (positions.get(a.driver.stepper) ?? Infinity) - (positions.get(b.driver.stepper) ?? Infinity) || a.index - b.index
  ).map(item => item.driver);
}
export function groupDrivers(drivers, layout) {
  const ordered = orderDrivers(drivers, layout);
  if (layout.mode !== 'z-overview') return [{key: 'all', title: '', drivers: ordered}];
  return [{key: 'z', title: 'Z 轴总览', drivers: ordered.filter(isZDriver)},
    {key: 'other', title: '其他轴', drivers: ordered.filter(driver => !isZDriver(driver))}]
    .filter(group => group.drivers.length);
}

// Use a separate, fixed same-origin module. No config field can provide a URL.
export async function loadCustomRenderer(layout, importer = url => import(url)) {
  if (!layout.custom_renderer.enabled) return {render: null, warning: ''};
  try {
    const module = await importer(CUSTOMIZATION_PATHS.renderer);
    if (typeof module.render !== 'function') throw new Error('模块未导出 render(context)');
    return {render: module.render, warning: ''};
  } catch (error) {
    return {render: null, warning: `自定义附加区域未加载，保留标准卡片：${error?.message || '模块无效'}。`};
  }
}

export function rendererData(driver, readings, displayStatus = {connected: true, trusted: true, message: ''}) {
  const status = Object.freeze({connected: displayStatus.connected === true,
    trusted: displayStatus.trusted === true, message: String(displayStatus.message || '')});
  const values = {};
  for (const register of ['ALARM_CODE', 'MOTOR_SPEED', 'ERROR_ANGLE', 'CHIP_MODEL', 'RUN_CURRENT']) {
    const reading = readings?.[register];
    values[register] = reading ? Object.freeze({value: Number.isFinite(reading.value) ? reading.value : null,
      outcome: typeof reading.outcome === 'string' ? reading.outcome : 'unknown',
      ended: Number.isFinite(reading.ended) ? reading.ended : null}) : null;
  }
  return Object.freeze({driver: Object.freeze({key: driver.key, stepper: driver.stepper, type: driver.type}),
    readings: status.trusted ? Object.freeze(values) : null, status});
}
