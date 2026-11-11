/**
 * Tests du dashboard : session, proxy, commandes, SSE, sécurité.
 *
 * Aucun réseau externe : une fausse API FastAPI tourne sur un port éphémère, et le
 * dashboard aussi. On vérifie le contrat du client mince, pas le comportement de
 * l'API (qui a ses propres tests côté Python).
 */

import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, test } from 'node:test';

import { createDashboardServer, resolveHost } from '../server.js';

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function urlOf(server) {
  const { port } = server.address();
  return `http://127.0.0.1:${port}`;
}

async function startFakeApi() {
  const calls = [];
  const server = http.createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    const send = (status, payload) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(payload));
    };

    if (req.url === '/api/auth/login' && req.method === 'POST') {
      const { password, totp } = JSON.parse(body || '{}');
      if (password === 'secret' && totp === '123456') {
        return send(200, { token: 'tok', token_type: 'bearer', expires_in: 900 });
      }
      return send(401, { detail: 'identifiants invalides' });
    }
    if (req.url === '/api/status') {
      if (req.headers.authorization !== 'Bearer tok') return send(401, { detail: 'session' });
      return send(200, {
        mode: 'PAPER',
        state: 'IDLE',
        equity: '9.37',
        free_quote: '9.37',
        reserve_quote: '0',
        drawdowns: { daily: '0', weekly: '0', from_hwm: '0' },
        position: null,
        recent: { signals: [] },
      });
    }
    if (req.url === '/api/stats') {
      if (req.headers.authorization !== 'Bearer tok') return send(401, { detail: 'session' });
      return send(200, {
        pnl: { trades: 3, wins: 2, win_rate: '0.666', net_pnl: '0.12', fees: '0.05' },
        equity_curve: [],
        daily: [],
        rejections: [],
        llm: { model: 'test-model', counts: {}, total: 0, degraded: 0,
               avg_inference_ms: 0, last: null },
      });
    }
    const parsed = new URL(req.url, 'http://dashboard.test');
    if (parsed.pathname.startsWith('/api/history/')) {
      if (req.headers.authorization !== 'Bearer tok') return send(401, { detail: 'session' });
      const table = parsed.pathname.split('/').pop();
      calls.push(['history', parsed.pathname, parsed.searchParams.get('limit')]);
      return send(200, {
        table,
        rows: [{ created_at: '2026-11-04T12:00:00Z', symbol: 'BTC/EUR' }],
      });
    }
    if (req.url === '/api/settings' && req.method === 'GET') {
      if (req.headers.authorization !== 'Bearer tok') return send(401, { detail: 'session' });
      return send(200, {
        profile: 'micro', profile_label: 'Micro', max_per_trade_pct: '0.50',
        max_daily_loss_pct: '0.08', max_orders_per_hour: 3,
        confidence_floor: '0.60', entry_rule_enabled: true,
        halt_auto_resume_hours: 24, updated_at: null, profiles: [],
      });
    }
    if (req.url === '/api/settings' && req.method === 'POST') {
      calls.push(['settings', JSON.parse(body || '{}')]);
      return send(200, {
        profile: 'micro', profile_label: 'Micro', max_per_trade_pct: '0.35',
        max_daily_loss_pct: '0.08', max_orders_per_hour: 3,
        confidence_floor: '0.60', entry_rule_enabled: true,
        halt_auto_resume_hours: 24, updated_at: null, profiles: [],
      });
    }
    if (req.url === '/api/control/pause' && req.method === 'POST') {
      calls.push(['pause']);
      return send(200, { mode: 'PAPER', state: 'LOCKED_CIRCUIT_BREAKER' });
    }
    if (req.url === '/api/control/resume' && req.method === 'POST') {
      calls.push(['resume']);
      return send(200, { mode: 'PAPER', state: 'IDLE' });
    }
    if (req.url === '/api/control/panic' && req.method === 'POST') {
      calls.push(['panic', JSON.parse(body || '{}')]);
      return send(200, { state: 'EMERGENCY_HALT', liquidated: [] });
    }
    send(404, { detail: 'introuvable' });
  });
  await listen(server);
  return { server, url: urlOf(server), calls };
}

function firstCookie(response) {
  const cookies = response.headers.getSetCookie?.() ?? [];
  const raw = cookies[0] ?? response.headers.get('set-cookie') ?? '';
  return raw.split(';')[0];
}

let api;
let dash;
let dashUrl;

before(async () => {
  api = await startFakeApi();
  dash = createDashboardServer({
    apiBaseUrl: api.url,
    sessionTtlMs: 60_000,
    pollMs: 50,
  });
  await listen(dash.server);
  dashUrl = urlOf(dash.server);
});

after(() => {
  dash.server.close();
  api.server.close();
});

async function login() {
  const response = await fetch(`${dashUrl}/login`, {
    method: 'POST',
    body: new URLSearchParams({ password: 'secret', totp: '123456' }),
    redirect: 'manual',
  });
  return firstCookie(response);
}

// ---------------------------------------------------------------------------
// Session et pages
// ---------------------------------------------------------------------------

test('la racine exige une session', async () => {
  const response = await fetch(`${dashUrl}/`, { redirect: 'manual' });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), '/login');
});

test('un mauvais mot de passe ramène à la page de connexion', async () => {
  const response = await fetch(`${dashUrl}/login`, {
    method: 'POST',
    body: new URLSearchParams({ password: 'faux', totp: '123456' }),
    redirect: 'manual',
  });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), '/login?error=1');
});

test('un login valide pose un cookie HttpOnly et SameSite=Strict', async () => {
  const response = await fetch(`${dashUrl}/login`, {
    method: 'POST',
    body: new URLSearchParams({ password: 'secret', totp: '123456' }),
    redirect: 'manual',
  });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), '/');
  const raw = response.headers.getSetCookie()[0];
  assert.match(raw, /HttpOnly/);
  assert.match(raw, /SameSite=Strict/);
  assert.match(raw, /Max-Age=60/);
});

test('la page de supervision est servie avec une session', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/`, { headers: { cookie } });
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Trading Ninja/i);
  assert.match(html, /id="panic"/);
  assert.match(html, /id="control-dialog"/);
  assert.match(html, /data-view="overview"/);
});

test('les fichiers statiques sont servis avec le bon type', async () => {
  const response = await fetch(`${dashUrl}/style.css`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /text\/css/);

  const icon = await fetch(`${dashUrl}/favicon.svg`);
  assert.equal(icon.status, 200);
  assert.match(icon.headers.get('content-type'), /image\/svg\+xml/);
});

test('une URL inconnue renvoie 404', async () => {
  const response = await fetch(`${dashUrl}/inconnu`);
  assert.equal(response.status, 404);
});

// ---------------------------------------------------------------------------
// Proxy d'état et de commandes
// ---------------------------------------------------------------------------

test('le statut exige une session', async () => {
  const response = await fetch(`${dashUrl}/api/status`);
  assert.equal(response.status, 401);
});

test('le statut est relayé depuis l’API', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/api/status`, { headers: { cookie } });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.mode, 'PAPER');
  assert.equal(body.equity, '9.37');
});

test('la pause est relayée', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/api/control/pause`, {
    method: 'POST',
    headers: { cookie },
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).state, 'LOCKED_CIRCUIT_BREAKER');
});

test('les statistiques sont relayées', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/api/stats`, { headers: { cookie } });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.pnl.trades, 3);
  assert.equal(body.llm.model, 'test-model');
});

test('l’historique est relayé avec une limite bornée', async () => {
  const cookie = await login();
  const before = api.calls.length;
  const response = await fetch(`${dashUrl}/api/history/llm_signals?limit=999`, {
    headers: { cookie },
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.table, 'llm_signals');
  const recorded = api.calls.slice(before).find(([name]) => name === 'history');
  assert.deepEqual(recorded, ['history', '/api/history/llm_signals', '50']);
});

test('l’historique applique une limite par défaut', async () => {
  const cookie = await login();
  const before = api.calls.length;
  const response = await fetch(`${dashUrl}/api/history/orders`, { headers: { cookie } });
  assert.equal(response.status, 200);
  const recorded = api.calls.slice(before).find(([name]) => name === 'history');
  assert.deepEqual(recorded, ['history', '/api/history/orders', '20']);
});

test('l’historique refuse une table inconnue sans appeler l’API', async () => {
  const cookie = await login();
  const before = api.calls.length;
  const response = await fetch(`${dashUrl}/api/history/passwords`, { headers: { cookie } });
  assert.equal(response.status, 400);
  assert.equal(api.calls.length, before);
});

test('l’historique exige une session', async () => {
  const response = await fetch(`${dashUrl}/api/history/llm_signals`);
  assert.equal(response.status, 401);
});

test('les réglages sont lisibles et modifiables', async () => {
  const cookie = await login();
  const read = await fetch(`${dashUrl}/api/settings`, { headers: { cookie } });
  assert.equal(read.status, 200);
  assert.equal((await read.json()).profile_label, 'Micro');

  const before = api.calls.length;
  const write = await fetch(`${dashUrl}/api/settings`, {
    method: 'POST',
    headers: { cookie, 'content-type': 'application/json' },
    body: JSON.stringify({ max_per_trade_pct: 0.35 }),
  });
  assert.equal(write.status, 200);
  assert.equal((await write.json()).max_per_trade_pct, '0.35');
  const recorded = api.calls.slice(before).find(([name]) => name === 'settings');
  assert.deepEqual(recorded[1], { max_per_trade_pct: 0.35 });
});

test('une origine étrangère est refusée sur les réglages', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/api/settings`, {
    method: 'POST',
    headers: { cookie, origin: 'http://evil.example', 'content-type': 'application/json' },
    body: JSON.stringify({ max_per_trade_pct: 0.35 }),
  });
  assert.equal(response.status, 403);
});

test('le cookie de session suit la plus courte des deux durées', async () => {
  const response = await fetch(`${dashUrl}/login`, {
    method: 'POST',
    body: new URLSearchParams({ password: 'secret', totp: '123456' }),
    redirect: 'manual',
  });
  const raw = response.headers.getSetCookie?.()[0]
    ?? response.headers.get('set-cookie')
    ?? '';
  assert.match(raw, /HttpOnly/);
  assert.match(raw, /SameSite=Strict/);
  // sessionTtlMs = 60 s dans les tests, expires_in = 900 s côté fausse API.
  assert.match(raw, /Max-Age=60(;|$)/);
});

test('une origine étrangère est refusée sur les commandes', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/api/control/pause`, {
    method: 'POST',
    headers: { cookie, origin: 'http://evil.example' },
  });
  assert.equal(response.status, 403);
});

test('le panic exige un code TOTP', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/api/control/panic`, {
    method: 'POST',
    headers: { cookie, 'content-type': 'application/json' },
    body: JSON.stringify({}),
  });
  assert.equal(response.status, 400);
});

test('le panic transmet le code à l’API', async () => {
  const cookie = await login();
  const before = api.calls.length;
  const response = await fetch(`${dashUrl}/api/control/panic`, {
    method: 'POST',
    headers: { cookie, 'content-type': 'application/json' },
    body: JSON.stringify({ totp: '654321' }),
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).state, 'EMERGENCY_HALT');
  const recorded = api.calls.slice(before).find(([name]) => name === 'panic');
  assert.deepEqual(recorded[1], { totp: '654321' });
});

test('la déconnexion efface le cookie', async () => {
  const cookie = await login();
  const response = await fetch(`${dashUrl}/logout`, {
    method: 'POST',
    headers: { cookie },
    redirect: 'manual',
  });
  assert.equal(response.status, 302);
  assert.match(response.headers.getSetCookie()[0], /Max-Age=0/);
});

// ---------------------------------------------------------------------------
// Flux temps réel
// ---------------------------------------------------------------------------

test('le flux SSE pousse le statut', async () => {
  const cookie = await login();
  const controller = new AbortController();
  const response = await fetch(`${dashUrl}/events`, {
    headers: { cookie },
    signal: controller.signal,
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /text\/event-stream/);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  const deadline = Date.now() + 3000;
  while (!text.includes('data:') && Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
  }
  controller.abort();
  assert.match(text, /data: \{"mode":"PAPER"/);
});

test('une session expirée renvoie vers la connexion', async () => {
  const shortLived = createDashboardServer({
    apiBaseUrl: api.url,
    sessionTtlMs: 10,
    pollMs: 1000,
  });
  await listen(shortLived.server);
  const shortUrl = urlOf(shortLived.server);
  try {
    const response = await fetch(`${shortUrl}/login`, {
      method: 'POST',
      body: new URLSearchParams({ password: 'secret', totp: '123456' }),
      redirect: 'manual',
    });
    const cookie = firstCookie(response);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const page = await fetch(`${shortUrl}/`, { headers: { cookie }, redirect: 'manual' });
    assert.equal(page.status, 302);
    assert.equal(page.headers.get('location'), '/login');
  } finally {
    shortLived.server.close();
  }
});


// ---------------------------------------------------------------------------
// Exposition réseau
// ---------------------------------------------------------------------------

test('par défaut, le dashboard n’écoute que sur la boucle locale', () => {
  assert.equal(resolveHost({}), '127.0.0.1');
  assert.equal(resolveHost({ DASHBOARD_HOST: 'localhost' }), 'localhost');
});

test('0.0.0.0 est refusé sans accord explicite', () => {
  assert.throws(() => resolveHost({ DASHBOARD_HOST: '0.0.0.0' }), /ALLOW_REMOTE/);
});

test('0.0.0.0 est accepté avec DASHBOARD_ALLOW_REMOTE=true', () => {
  assert.equal(
    resolveHost({ DASHBOARD_HOST: '0.0.0.0', DASHBOARD_ALLOW_REMOTE: 'true' }),
    '0.0.0.0',
  );
});
