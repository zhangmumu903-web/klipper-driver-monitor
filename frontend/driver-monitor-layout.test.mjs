import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {CUSTOMIZATION_PATHS, DEFAULT_LAYOUT, validateLayout, loadLayout, orderDrivers,
  groupDrivers, displayName, loadCustomRenderer, rendererData} from './driver-monitor-layout.mjs';
import {createDriverCard} from './driver-monitor.mjs';

const driver = stepper => ({key: `lyx9231 ${stepper}`, type: 'lyx9231', stepper, mode: 'active'});

test('default install layout validates and protects mandatory fields', () => {
  const source = JSON.parse(readFileSync(new URL('./customization/layout.json', import.meta.url)));
  assert.deepEqual(validateLayout(source), DEFAULT_LAYOUT);
  for (const field of ['alarm', 'protection', 'fault'])
    assert.throws(() => validateLayout({schema_version: 1, show: {[field]: false}}), /不是支持/);
  for (const property of ['url', 'path', 'script'])
    assert.throws(() => validateLayout({schema_version: 1, custom_renderer: {[property]: 'https://evil.invalid/'}}));
  assert.throws(() => validateLayout({schema_version: 1, mode: 'hide-all'}));
  assert.throws(() => validateLayout({schema_version: 2}));
  assert.throws(() => validateLayout({schema_version: 1, show: {angle: 'false'}}));
  assert.throws(() => validateLayout(JSON.parse('{"schema_version":1,"axis_names":{"__proto__":"danger"}}')));
});

test('layout file missing, malformed or oversized gives defaults and visible explanation', async () => {
  for (const fetcher of [
    async () => ({ok: false, status: 404}),
    async () => ({ok: true, text: async () => '{broken'}),
    async () => ({ok: true, text: async () => 'x'.repeat(32769)}),
    async () => { throw new Error('离线'); },
    async () => { throw null; },
    async () => ({ok: true, text: async () => '{"schema_version":1,"show":{"alarm":false}}'}),
  ]) {
    const result = await loadLayout(fetcher);
    assert.equal(result.layout, DEFAULT_LAYOUT); assert.match(result.warning, /使用默认卡片/);
    assert.match(result.warning, /后台监测不受影响/);
  }
  const result = await loadLayout(async (url, options) => {
    assert.equal(url, CUSTOMIZATION_PATHS.layout); assert.equal(options.cache, 'no-store');
    return {ok: true, text: async () => '{"schema_version":1,"mode":"compact"}'};
  });
  assert.equal(result.layout.mode, 'compact'); assert.equal(result.warning, '');
});

test('sorting never filters unnamed axes and renaming is text data', () => {
  const axes = ['extruder', 'stepper_y', 'stepper_z1', 'stepper_x', 'stepper_z'].map(driver);
  const layout = validateLayout({schema_version: 1, axis_order: ['stepper_z', 'stepper_z1', 'absent', 'stepper_z'],
    axis_names: {stepper_z: '<img src=x onerror=alert(1)>'}});
  assert.deepEqual(orderDrivers(axes, layout).map(d => d.stepper),
    ['stepper_z', 'stepper_z1', 'extruder', 'stepper_y', 'stepper_x']);
  assert.equal(displayName(driver('stepper_z'), layout), '<img src=x onerror=alert(1)>');
  assert.equal(displayName(driver('stepper_x'), layout), 'stepper_x');
  assert.equal(axes[0].stepper, 'extruder');
});

test('Z overview includes every Z and other configured axis; no four-axis hard limit', () => {
  const axes = ['stepper_x', 'stepper_z3', 'stepper_z', 'stepper_z1', 'stepper_z2', 'stepper_z4', 'extruder'].map(driver);
  for (const mode of ['cards', 'compact', 'z-overview']) {
    const groups = groupDrivers(axes, validateLayout({schema_version: 1, mode}));
    assert.deepEqual(new Set(groups.flatMap(group => group.drivers)), new Set(axes));
    if (mode === 'z-overview') {
      assert.equal(groups[0].drivers.length, 5);
      assert.deepEqual(groups[1].drivers.map(d => d.stepper), ['stepper_x', 'extruder']);
    }
  }
});

test('custom renderer is explicitly enabled, fixed-path and fail-open to standard display', async () => {
  let calls = 0;
  const importer = async url => { calls++; assert.equal(url, CUSTOMIZATION_PATHS.renderer); return {render() {}}; };
  assert.equal((await loadCustomRenderer(DEFAULT_LAYOUT, importer)).render, null);
  assert.equal(calls, 0);
  const enabled = validateLayout({schema_version: 1, custom_renderer: {enabled: true}});
  assert.equal(typeof (await loadCustomRenderer(enabled, importer)).render, 'function');
  assert.equal(calls, 1);
  for (const importer of [async () => ({}), async () => { throw new Error('脚本缺失'); }, async () => { throw null; }]) {
    const result = await loadCustomRenderer(enabled, importer);
    assert.equal(result.render, null); assert.match(result.warning, /保留标准卡片/);
  }
});

test('renderer receives frozen minimal readings without command controller or mutable backend references', () => {
  const reading = {value: 2, outcome: 'ok', ended: 9, private: {x: 1}};
  const result = rendererData(driver('stepper_z'), {ALARM_CODE: reading});
  assert.deepEqual(Object.keys(result), ['driver', 'readings', 'status']);
  assert.ok(Object.isFrozen(result.status));
  assert.deepEqual(result.status, {connected: true, trusted: true, message: ''});
  assert.equal(result.readings.ALARM_CODE.value, 2);
  assert.equal(result.readings.ALARM_CODE.private, undefined);
  assert.ok(Object.isFrozen(result)); assert.ok(Object.isFrozen(result.driver));
  assert.ok(Object.isFrozen(result.readings.ALARM_CODE));
  reading.value = 4; assert.equal(result.readings.ALARM_CODE.value, 2);
});

class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.attrs = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener() {}
  remove() { this.removed = true; }
}
function find(root, predicate) {
  if (predicate(root)) return root;
  for (const child of root.children) { const found = find(child, predicate); if (found) return found; }
}
const byClass = (root, name) => find(root, node => node.className?.split(' ').includes(name));

test('optional fields and broken extension cannot hide alarm, protection or communication errors', () => {
  globalThis.document = {createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag)};
  try {
    const layout = validateLayout({schema_version: 1, mode: 'compact', axis_names: {stepper_z: '<b>左Z</b>'},
      show: {speed: false, angle: false, trends: false, driver_details: false}});
    let calls = 0;
    const card = createDriverCard(driver('stepper_z'), () => {}, layout, () => { calls++; throw null; });
    const state = {connected: true, busy: false, snapshot: {eventtime: 10, status: {driver_monitor: {
      shutdown_on_alarm: true, protection: {stepper_z: {enabled: true, armed: true}},
      readings: {stepper_z: {ALARM_CODE: {value: 2, outcome: 'ok', ended: 9},
        MOTOR_SPEED: {value: null, outcome: 'invalid', ended: 9}}},
    }}}};
    card.render(state, {actionProblem: () => ''}, '');
    assert.equal(byClass(card.element, 'dm-alarm').hidden, false);
    assert.equal(byClass(card.element, 'dm-protection').hidden, false);
    assert.equal(byClass(card.element, 'dm-fault').hidden, false);
    assert.match(byClass(card.element, 'dm-fault').textContent, /转速原值读取失败/);
    assert.equal(byClass(card.element, 'dm-custom-warning').hidden, false);
    assert.equal(byClass(card.element, 'dm-trends').hidden, true);
    assert.equal(byClass(card.element, 'dm-details').hidden, true);
    assert.equal(find(card.element, node => node.tagName === 'h3').textContent, '<b>左Z</b>');
    card.render({...state, snapshot: {...state.snapshot}}, {actionProblem: () => ''}, '');
    assert.equal(calls, 1); // Failing extension is not re-run by the 500 ms ticker.
    assert.ok(!('innerHTML' in find(card.element, node => node.tagName === 'h3')));
    card.destroy(); assert.equal(card.element.removed, true);
  } finally { delete globalThis.document; }
});

test('custom stylesheet is attached inside the monitor ShadowRoot after the base stylesheet', () => {
  const source = readFileSync(new URL('./driver-monitor.mjs', import.meta.url), 'utf8');
  assert.match(source, /customSheet\.href = `\$\{CUSTOMIZATION_PATHS.style\}/);
  assert.match(source, /shadow\.append\(sheet, customSheet, toolbar/);
  assert.ok(!source.includes('innerHTML'));
});


test('extension loses stale successful data on disconnect with the same snapshot and restores it on reconnect', () => {
  globalThis.document = {createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag)};
  try {
    const seen = []; let cleaned = 0;
    const renderer = context => {
      seen.push(context);
      const line = new Node('p');
      line.textContent = context.status.trusted
        ? String(context.readings.ALARM_CODE.value) : context.status.message;
      context.element.replaceChildren(line);
      return () => { cleaned++; };
    };
    const card = createDriverCard(driver('stepper_z'), () => {}, DEFAULT_LAYOUT, renderer);
    const snapshot = {eventtime: 10, status: {driver_monitor: {
      shutdown_on_alarm: true, readings: {stepper_z: {ALARM_CODE: {value: 0, outcome: 'ok', ended: 9}}},
    }}};
    const state = {connected: true, busy: false, snapshot};
    const controller = {actionProblem: () => ''};
    card.render(state, controller, '');
    assert.equal(seen.length, 1); assert.equal(seen[0].readings.ALARM_CODE.value, 0);
    const area = byClass(card.element, 'dm-custom-area');
    assert.equal(area.children[0].textContent, '0');

    state.connected = false;
    card.render(state, controller, ''); // Cached snapshot deliberately unchanged.
    assert.equal(seen.length, 2); assert.equal(cleaned, 1);
    assert.equal(seen[1].readings, null); assert.equal(seen[1].status.connected, false);
    assert.equal(seen[1].status.trusted, false);
    assert.match(area.children[0].textContent, /连接中断/);
    card.render(state, controller, ''); assert.equal(seen.length, 2);

    state.connected = true;
    card.render(state, controller, '');
    assert.equal(seen.length, 3); assert.equal(seen[2].status.trusted, true);
    assert.equal(seen[2].readings.ALARM_CODE.value, 0); assert.equal(area.children[0].textContent, '0');
    card.render(state, controller, '不是已验证的目标主机');
    assert.equal(seen.length, 4); assert.equal(seen[3].readings, null);
    assert.equal(seen[3].status.connected, true); assert.equal(seen[3].status.trusted, false);
    assert.match(seen[3].status.message, /目标主机/);
    assert.match(byClass(card.element, 'dm-fault').textContent, /目标主机/);
    card.render(state, controller, ''); assert.equal(seen.length, 5);
    assert.equal(area.children[0].textContent, '0');
    card.destroy(); assert.equal(cleaned, 5);
  } finally { delete globalThis.document; }
});
