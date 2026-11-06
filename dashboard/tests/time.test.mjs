import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/utils/time.js', import.meta.url), 'utf8');
async function load(zone) {
  const code = source.replace('import.meta.env.VITE_TIME_ZONE', JSON.stringify(zone));
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}

test('configured timezone, midnight and invalid input', async () => {
  const time = await load('America/Argentina/Buenos_Aires');
  assert.equal(time.TIME_ZONE, 'America/Argentina/Buenos_Aires');
  assert.equal(time.formatTime('2026-11-04T03:00:00Z'), '00:00:00');
  assert.match(time.formatDateTime('2026-11-04T03:00:00Z'), /4\/11\/2026/);
  assert.equal(time.formatTime('invalid'), '-');
  assert.equal(time.formatDateTime(null), '-');
});

test('invalid timezone falls back to UTC', async () => {
  const time = await load('Invalid/Zone');
  assert.equal(time.TIME_ZONE, 'UTC');
  assert.equal(time.formatTime('2026-11-04T03:00:00Z'), '03:00:00');
});
