import {TARGET_CONFIG} from './driver-monitor-config.mjs';
import {MonitorController, MoonrakerApi, PREVIEW_HOSTNAME,
  DYNAMIC_REGISTERS, readingDisplay, targetProblem, trendData,
  monitorScheduleLabel, alarmProtectionDisplay, alarmShutdownMessage} from './driver-monitor-core.mjs';

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const button = (text, className = '') => {
  const node = el('button', className, text); node.type = 'button'; return node;
};
const clockText = timestamp => {
  if (!Number.isFinite(timestamp)) return '—';
  const date = new Date(timestamp);
  const time = date.toLocaleTimeString('zh-CN', {hour12: false});
  return date.toDateString() === new Date().toDateString() ? time
    : `${date.toLocaleDateString('zh-CN')} ${time}`;
};

function createTrendChart(register, label) {
  const figure = el('figure', 'dm-trend');
  const caption = el('figcaption'); caption.append(el('span', '', label), el('span', 'dm-trend-unit', '原值'));
  const plot = el('div', 'dm-trend-plot');
  const svg = (tag, attributes = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const chart = svg('svg', {viewBox: '0 0 420 96', role: 'img',
    'aria-label': `${label}最近5分钟趋势，Y轴为寄存器原值`});
  const grid = svg('g', {class: 'dm-chart-grid'});
  grid.append(svg('line', {x1: 43, x2: 410, y1: 10, y2: 10}),
    svg('line', {x1: 43, x2: 410, y1: 74, y2: 74}));
  const yHigh = svg('text', {x: 37, y: 13, 'text-anchor': 'end'});
  const yLow = svg('text', {x: 37, y: 77, 'text-anchor': 'end'});
  const xLabels = svg('g', {class: 'dm-chart-axis'});
  xLabels.append(svg('text', {x: 43, y: 92}, '5分钟前'),
    svg('text', {x: 226, y: 92, 'text-anchor': 'middle'}, '2分30秒前'),
    svg('text', {x: 410, y: 92, 'text-anchor': 'end'}, '现在'));
  const marks = svg('g', {class: 'dm-chart-marks'});
  const cursor = svg('line', {class: 'dm-chart-cursor', x1: 43, x2: 43, y1: 7, y2: 76});
  cursor.style.display = 'none';
  chart.append(grid, yHigh, yLow, xLabels, marks, cursor);
  const empty = el('span', 'dm-trend-empty', '近5分钟无有效数据');
  const tooltip = el('span', 'dm-trend-tooltip'); tooltip.hidden = true;
  plot.append(chart, empty, tooltip); figure.append(caption, plot);
  let model = null, hoveredX = null, context = '';
  const x = point => 43 + (point.ended - model.start) / 300 * 367;
  const y = point => 74 - (point.value - model.yMin) / (model.yMax - model.yMin) * 64;
  function showHover(position) {
    if (!model?.points.length) { tooltip.hidden = true; cursor.style.display = 'none'; return; }
    const at = model.start + Math.max(0, Math.min(1, (position - 43) / 367)) * 300;
    const point = model.points.reduce((best, candidate) =>
      Math.abs(candidate.ended - at) < Math.abs(best.ended - at) ? candidate : best);
    cursor.setAttribute('x1', String(x(point))); cursor.setAttribute('x2', String(x(point)));
    cursor.style.display = '';
    tooltip.textContent = `${clockText(point.wallTime)} · ${point.value === null ? '读取失败，无有效值' : `原值 ${point.value}`}`;
    tooltip.hidden = false;
  }
  chart.addEventListener('pointermove', event => {
    const rect = chart.getBoundingClientRect();
    if (!rect.width) return;
    hoveredX = (event.clientX - rect.left) / rect.width * 420; showHover(hoveredX);
  });
  chart.addEventListener('pointerleave', () => {
    hoveredX = null; tooltip.hidden = true; cursor.style.display = 'none';
  });
  return {figure, update(snapshot, stepper, receivedAt) {
    model = trendData(snapshot, stepper, register, receivedAt);
    const nextContext = `${model.sessionId || ''}:${stepper || ''}`;
    if (context !== nextContext) { context = nextContext; hoveredX = null; }
    marks.replaceChildren();
    empty.hidden = !model.empty;
    yHigh.textContent = model.empty ? '—' : String(model.yMax);
    yLow.textContent = model.empty ? '—' : String(model.yMin);
    for (const segment of model.segments) {
      const path = segment.map((point, index) => `${index ? 'L' : 'M'}${x(point).toFixed(2)},${y(point).toFixed(2)}`).join(' ');
      marks.append(svg('path', {d: path}));
      for (const point of segment) marks.append(svg('circle', {cx: x(point), cy: y(point), r: 2}));
    }
    if (hoveredX !== null) showHover(hoveredX);
    else { tooltip.hidden = true; cursor.style.display = 'none'; }
  }};
}

export function isDashboardLocation(href) {
  const route = new URL(href).hash.slice(1).split('?')[0];
  return route === '' || route === '/';
}

function createDriverCard(driver, onRefresh) {
  const card = el('section', 'dm-card');
  card.dataset.driverKey = driver.key; card.dataset.stepper = driver.stepper;
  card.setAttribute('aria-label', `${driver.stepper} 驱动监测`);
  const header = el('header', 'dm-header');
  header.append(el('h3', '', driver.stepper),
    el('span', 'dm-mode', driver.type.toUpperCase()));
  card.append(header);
  const read = button('立即刷新', 'dm-primary'); read.dataset.action = 'refresh';
  read.addEventListener('click', () => onRefresh(driver.key)); header.append(read);
  const protection = el('p', 'dm-protection'); protection.hidden = true;
  const protectionLabel = el('span', 'dm-protection-label');
  const protectionState = el('span', 'dm-protection-state');
  protection.append(protectionLabel, protectionState);
  const values = el('div', 'dm-values'), rows = new Map();
  const names = {ALARM_CODE: '报警码', MOTOR_SPEED: '转速原值', ERROR_ANGLE: '角度误差原值'};
  for (const register of DYNAMIC_REGISTERS) {
    const row = el('div', register === 'ALARM_CODE' ? 'dm-reading dm-alarm' : 'dm-reading');
    row.dataset.register = register;
    const value = el('strong', 'dm-number', '—');
    const meta = el('span', 'dm-reading-meta');
    const status = el('span', '', '未读'), time = el('time', '', '—');
    meta.append(status, time); row.append(el('span', 'dm-label', names[register]), value, meta);
    values.append(row); rows.set(register, {row, value, status, time});
  }
  const details = el('details', 'dm-details');
  details.append(el('summary', '', '驱动信息'));
  const secondary = el('p', 'dm-secondary-info'); details.append(secondary);
  const trends = el('section', 'dm-trends');
  const speedTrend = createTrendChart('MOTOR_SPEED', '转速');
  const angleTrend = createTrendChart('ERROR_ANGLE', '角度误差');
  trends.append(el('div', 'dm-trend-heading', '最近 5 分钟'), speedTrend.figure, angleTrend.figure);
  const message = el('p', 'dm-message'); message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite'); message.hidden = true;
  card.append(protection, values, trends, details, message);
  let graphSnapshot = null;
  return {element: card, render(state, controller, targetError) {
    const problem = controller.actionProblem('refresh', driver.key);
    read.disabled = state.busy || Boolean(problem); read.title = problem;
    read.textContent = state.busy && state.busyKey === driver.key ? '刷新中…' : '立即刷新';
    const ownMessage = state.messageKey === driver.key ? state.message : '';
    message.textContent = ownMessage; message.hidden = !ownMessage;
    message.dataset.tone = state.messageKey === driver.key ? state.tone : 'neutral';
    const monitor = state.snapshot?.status?.driver_monitor;
    const protectionView = state.connected && !targetError ? alarmProtectionDisplay(monitor, driver.stepper) : null;
    protection.hidden = !protectionView;
    protectionLabel.textContent = protectionView?.label || '';
    protectionState.textContent = protectionView?.state || '';
    protection.dataset.tone = protectionView?.tone || 'neutral';
    const data = state.connected && !targetError ? monitor?.readings?.[driver.stepper] : null;
    const trendSnapshot = state.connected && !targetError ? state.snapshot : null;
    if (trendSnapshot !== graphSnapshot) {
      graphSnapshot = trendSnapshot;
      speedTrend.update(trendSnapshot, driver.stepper, state.receivedAt);
      angleTrend.update(trendSnapshot, driver.stepper, state.receivedAt);
    }
    for (const register of DYNAMIC_REGISTERS) {
      const view = readingDisplay(data?.[register], state.snapshot, state.receivedAt);
      const row = rows.get(register);
      row.value.textContent = view.value; row.status.textContent = view.status;
      row.time.textContent = clockText(view.wallTime); row.row.dataset.tone = view.tone;
      row.row.title = `${register} · ${view.label || '尚无采样时间'}`;
      if (register === 'ALARM_CODE' && view.status === '读取成功' && data[register].value !== 0)
        row.row.dataset.tone = 'alarm';
    }
    secondary.textContent = ['CHIP_MODEL', 'RUN_CURRENT'].map(register => {
      const view = readingDisplay(data?.[register], state.snapshot, state.receivedAt);
      const label = register === 'CHIP_MODEL' ? '型号' : '电流寄存器原值';
      return `${label}：${view.value}（${view.status} · ${clockText(view.wallTime)}）`;
    }).join('；');
  }};
}

export function mountDriverMonitor(targetElement) {
  if (!targetElement?.append || !targetElement.isConnected)
    throw new Error('需要一个已挂入仪表板的驱动监测容器。');
  if (document.getElementById('klipper-driver-monitor')) return null;
  const host = el('div'); host.id = 'klipper-driver-monitor';
  const shadow = host.attachShadow({mode: 'open'});
  const sheet = el('link'); sheet.rel = 'stylesheet';
  sheet.href = new URL('./driver-monitor.css', import.meta.url).href;
  const toolbar = el('section', 'dm-toolbar'); toolbar.setAttribute('aria-label', '全部驱动监测控制');
  const header = el('header', 'dm-header'); header.append(el('h2', '', '驱动监测'));
  const auto = button('暂停全部 LYX 监测', 'dm-secondary'); auto.dataset.action = 'auto';
  header.append(auto);
  const targetOptions = () => ({preview:
    document.documentElement.dataset.driverMonitorPreview === 'true'
    && ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)});
  const preview = el('p', 'dm-preview', '离线预览 · 模拟回复 · 未连接设备');
  preview.hidden = !targetOptions().preview;
  const message = el('p', 'dm-message'); message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite');
  const protectionNote = el('p', 'dm-protection-note'); protectionNote.hidden = true;
  protectionNote.textContent = '报警停机保护已开启，不能暂停全部 LYX 监测。';
  const shutdownReason = el('p', 'dm-shutdown-reason'); shutdownReason.hidden = true;
  shutdownReason.setAttribute('role', 'alert');
  toolbar.append(header, preview, protectionNote, shutdownReason, message);
  const collection = el('div', 'dm-cards');
  const empty = el('p', 'dm-empty', '尚未发现 LYX 驱动，请检查 LYX 配置及 [driver_monitor] 是否已加载。');
  shadow.append(sheet, toolbar, collection, empty); targetElement.append(host);

  const cards = new Map();
  let driverSignature = '', refreshTimer = null, ticker = null;
  let disposed = false, observer;
  function syncTheme() {
    const source = targetElement.parentElement?.querySelector('.app-draggable .v-card')
      || targetElement;
    const computed = getComputedStyle(source);
    const light = Boolean(source.closest('.theme--light'));
    const background = computed.backgroundColor;
    const surface = background && background !== 'rgba(0, 0, 0, 0)' && background !== 'transparent'
      ? background : light ? '#ffffff' : '#1e1e1e';
    if (host.style.getPropertyValue('--dm-surface') !== surface)
      host.style.setProperty('--dm-surface', surface);
    if (host.style.color !== computed.color) host.style.color = computed.color;
    if (host.style.fontFamily !== computed.fontFamily) host.style.fontFamily = computed.fontFamily;
    const theme = light ? 'light' : 'dark';
    if (host.dataset.theme !== theme) host.dataset.theme = theme;
  }
  const controller = new MonitorController({api: new MoonrakerApi(), pageUrl: () => location.href,
    targetOptions, targetConfig: TARGET_CONFIG, onChange: render});
  function render(state) {
    if (disposed) return;
    const targetError = state.snapshot ? targetProblem(state.snapshot.info, location.href, targetOptions(), TARGET_CONFIG) : '';
    preview.hidden = !targetOptions().preview;
    const expectedHostname = targetOptions().preview ? PREVIEW_HOSTNAME : TARGET_CONFIG.hostname || '目标未配置';
    header.title = `${state.snapshot?.info?.hostname || expectedHostname} · ${location.origin}`;
    const signature = JSON.stringify(state.drivers.map(d => [d.key, d.type, d.stepper]));
    if (signature !== driverSignature) {
      driverSignature = signature;
      const keys = new Set(state.drivers.map(driver => driver.key));
      for (const [key, view] of cards) if (!keys.has(key)) { view.element.remove(); cards.delete(key); }
      for (const driver of state.drivers) {
        if (!cards.has(driver.key)) cards.set(driver.key, createDriverCard(driver, key => {
          syncActivity(); if (active()) safe(controller.refreshDriver(key), key);
        }));
        collection.append(cards.get(driver.key).element);
      }
    }
    empty.hidden = state.drivers.length > 0;
    const monitor = state.snapshot?.status?.driver_monitor;
    const hasLyx = state.drivers.some(driver => driver.mode === 'active');
    const autoProblem = controller.actionProblem('auto');
    auto.hidden = !hasLyx; auto.disabled = state.busy || Boolean(autoProblem);
    auto.title = autoProblem || '作用于所有已配置 LYX 驱动的后台监测';
    auto.textContent = monitor?.auto_enabled ? '暂停全部 LYX 监测' : '恢复全部 LYX 监测';
    protectionNote.hidden = !(hasLyx && state.connected && !targetError && monitor?.shutdown_on_alarm === true);
    const shutdownText = state.connected && !targetError ? alarmShutdownMessage(monitor) : '';
    shutdownReason.textContent = shutdownText; shutdownReason.hidden = !shutdownText;
    const backgroundState = hasLyx ? (monitor?.auto_enabled
      ? monitorScheduleLabel(monitor) : `后台监测已暂停 · ${monitorScheduleLabel(monitor)}`)
      : '尚未发现可监测的 LYX 驱动。';
    message.dataset.tone = state.messageKey === null ? state.tone : 'neutral';
    message.textContent = (state.messageKey === null ? state.message : '') || backgroundState;
    if (targetError) message.textContent = targetError;
    else if (hasLyx && state.connected && !state.busy && autoProblem) message.textContent = autoProblem;
    for (const view of cards.values()) view.render(state, controller, targetError);
  }
  const active = () => !disposed && host.isConnected && isDashboardLocation(location.href);
  const safe = (promise, key = null) => Promise.resolve(promise).catch(error => {
    if (disposed) return;
    controller.state.message = error.message || '操作未完成。';
    controller.state.messageKey = key; controller.state.tone = 'error'; controller.emit();
  });
  function scheduleRefresh() {
    if (refreshTimer !== null) clearTimeout(refreshTimer);
    refreshTimer = null;
    if (!active() || document.hidden) return;
    refreshTimer = setTimeout(async () => {
      if (active() && !document.hidden && !controller.state.busy) await controller.refresh();
      scheduleRefresh();
    }, 3000);
  }
  function syncActivity() {
    if (!host.isConnected) { destroy(); return; }
    const dashboard = isDashboardLocation(location.href);
    host.hidden = !dashboard;
    controller.setOpen(dashboard);
    controller.setVisible(dashboard && !document.hidden);
    scheduleRefresh();
  }
  const visibilityChanged = () => {
    syncActivity();
    if (active() && !document.hidden) safe(controller.refresh());
  };
  const navigationChanged = () => {
    controller.setOpen(false);
    syncActivity();
    if (active() && !document.hidden) safe(controller.refresh());
  };
  const pageLeaving = () => { controller.setOpen(false); controller.setVisible(false); };
  auto.addEventListener('click', () => {
    syncActivity();
    if (active()) safe(controller.setAuto(!controller.state.snapshot?.status?.driver_monitor?.auto_enabled));
  });
  document.addEventListener('visibilitychange', visibilityChanged);
  window.addEventListener('pagehide', pageLeaving);
  window.addEventListener('popstate', navigationChanged);
  window.addEventListener('hashchange', navigationChanged);
  observer = new MutationObserver(() => { if (!host.isConnected) destroy(); });
  observer.observe(document.body, {childList: true, subtree: true});
  ticker = setInterval(() => {
    if (!host.isConnected) destroy();
    else if (active() && !document.hidden) { syncTheme(); render(controller.state); }
  }, 500);
  function destroy() {
    if (disposed) return;
    disposed = true;
    controller.setOpen(false); controller.setVisible(false);
    observer?.disconnect(); clearInterval(ticker);
    if (refreshTimer !== null) clearTimeout(refreshTimer);
    document.removeEventListener('visibilitychange', visibilityChanged);
    window.removeEventListener('pagehide', pageLeaving);
    window.removeEventListener('popstate', navigationChanged);
    window.removeEventListener('hashchange', navigationChanged);
    host.remove(); cards.clear();
  }
  syncActivity(); syncTheme();
  if (active() && !document.hidden) safe(controller.refresh()); // One GET loop for all cards.
  render(controller.state);
  return {controller, host, destroy};
}

export function installDriverMonitorCards() {
  let instance = null, generatedSlot = null, queued = false, disposed = false;
  function sync() {
    queued = false;
    if (disposed) return;
    if (!isDashboardLocation(location.href)) {
      instance?.destroy(); instance = null;
      generatedSlot?.remove(); generatedSlot = null;
      return;
    }
    if (instance?.host.isConnected) return;
    instance?.destroy(); instance = null;
    let slot = document.querySelector('[data-driver-monitor-slot]');
    if (!slot) {
      const lists = [...document.querySelectorAll('.app-draggable.list-group')]
        .filter(list => list.parentElement?.matches('div.col-md-6.col-lg-6.col-12'));
      const list = lists.find(candidate => [...candidate.children].some(child =>
        child.classList.contains('v-card') && child.textContent.trim().startsWith('控制台')))
        || lists[1];
      if (!list) return; // No verified dashboard column: never fall back to document.body.
      generatedSlot = el('div', 'mb-2 mb-md-4');
      generatedSlot.setAttribute('data-driver-monitor-slot', '');
      list.parentElement.insertBefore(generatedSlot, list);
      slot = generatedSlot;
    }
    instance = mountDriverMonitor(slot);
  }
  const schedule = () => {
    if (!disposed && !queued) { queued = true; queueMicrotask(sync); }
  };
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, {childList: true, subtree: true});
  window.addEventListener('hashchange', sync);
  window.addEventListener('popstate', sync);
  sync();
  return {destroy() {
    disposed = true;
    observer.disconnect(); window.removeEventListener('hashchange', sync);
    window.removeEventListener('popstate', sync);
    instance?.destroy(); generatedSlot?.remove();
  }};
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installDriverMonitorCards, {once: true});
  else installDriverMonitorCards();
}
