import test from 'node:test';
import assert from 'node:assert/strict';
import { ForecastSettings, duration } from '../public/js/forecast.js';

test('previsão: duração configurável e persistência', () => {
  const original = { document: globalThis.document, localStorage: globalThis.localStorage, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const elements = new Map();
  const storage = new Map();
  const timers = new Map();
  let id = 0;
  function el(name) {
    if (!elements.has(name)) {
      const classes = new Set(['hidden']);
      elements.set(name, { value: '15', textContent: '', handlers: {},
        classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) },
        addEventListener(name, cb) { this.handlers[name] = cb; } });
    }
    return elements.get(name);
  }
  try {
    globalThis.document = { getElementById: el, addEventListener() {}, dispatchEvent() {} };
    globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
    globalThis.setTimeout = (callback, delay) => { timers.set(++id, { callback, delay }); return id; };
    globalThis.clearTimeout = key => timers.delete(key);
    const settings = new ForecastSettings();
    assert.equal(settings.seconds, 15);
    el('forecast-duration').value = '25';
    el('forecast-duration').handlers.change();
    assert.equal(settings.seconds, 25);
    new ForecastSettings();
    assert.equal(el('forecast-duration').value, '25');
    assert.equal(duration('0'), 15);
    assert.equal(duration('abc'), 15);
  } finally { Object.assign(globalThis, original); }
});
