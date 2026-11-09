import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createDashboardServer } from '../server.js';

let server; let url; let cookie; const calls = [];
before(async () => {
  const app = createDashboardServer({ apiBaseUrl: 'http://fake-api.test', fetchImpl: async (target, options = {}) => {
    calls.push([String(target), options]);
    return new Response(JSON.stringify(String(target).endsWith('/api/auth/login')
      ? { token: 'test-token', expires_in: 3600 } : { ok: true }), { status: 200, headers: { 'content-type': 'application/json' } });
  } });
  server = app.server;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  url = `http://127.0.0.1:${server.address().port}`;
  const login = await fetch(url + '/login', { method: 'POST', body: new URLSearchParams({ password: 'fake', totp: '123456' }), redirect: 'manual' });
  cookie = login.headers.get('set-cookie').split(';')[0];
});
after(() => server.close());

test('les observations restent privées et seules les routes précises sont relayées', async () => {
  assert.equal((await fetch(url + '/api/insights/overview')).status, 401);
  assert.equal((await fetch(url + '/api/insights/overview', { headers: { cookie } })).status, 200);
  assert.equal((await fetch(url + '/api/insights/arbitrary', { headers: { cookie } })).status, 404);
  const before = calls.length;
  assert.equal((await fetch(url + '/api/control/arbitrary', { method: 'POST', headers: { cookie } })).status, 404);
  assert.equal(calls.length, before);
});

test('la suspension transmet permission et révision sans transformer la commande en panic', async () => {
  const body = { enabled: false, expected_revision: 4, reason: 'absence' };
  const result = await fetch(url + '/api/control/entries', { method: 'POST', headers: { cookie, 'content-type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal(result.status, 200);
  const [target, options] = calls.at(-1);
  assert.ok(target.endsWith('/api/control/entries'));
  assert.deepEqual(JSON.parse(options.body), body);
  assert.equal(options.headers.Authorization, 'Bearer test-token');
});

test('une origine étrangère ne lance ni rejeu ni expérience ni commande', async () => {
  for (const path of ['/api/insights/replay', '/api/insights/experiments', '/api/control/entries', '/api/settings/preview']) {
    const result = await fetch(url + path, { method: 'POST', headers: { cookie, origin: 'https://foreign.test' }, body: '{}' });
    assert.equal(result.status, 403);
  }
});

test('les identifiants UUID des cycles sont vérifiés avant le proxy', async () => {
  const valid = '/api/insights/cycles/00000000-0000-0000-0000-000000000001/events';
  assert.equal((await fetch(url + valid, { headers: { cookie } })).status, 200);
  assert.equal((await fetch(url + '/api/insights/cycles/not-an-id', { headers: { cookie } })).status, 404);
});

test('le client des observations est servi sous la politique CSP existante', async () => {
  const response = await fetch(url + '/insights.js', { headers: { cookie } });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-security-policy'), /script-src 'self'/);
  assert.match(await response.text(), /textContent/);
});
