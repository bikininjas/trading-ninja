/**
 * Dashboard Trading Ninja — client mince de l'API d'administration.
 *
 * Rôle : servir une page de supervision et relayer les commandes vers FastAPI.
 * Le jeton JWT reste **dans ce processus** ; le navigateur ne reçoit qu'un cookie
 * de session HttpOnly, SameSite=Strict. Un XSS dans la page ne peut donc pas lire
 * le jeton qui permet de liquider un compte.
 *
 * Zéro dépendance : node:http, fetch natif, node:test. Node >= 20 suffit.
 *
 *     node server.js
 *     DASHBOARD_PORT=8081 API_BASE_URL=http://127.0.0.1:8080 node server.js
 *
 * Par défaut, écoute sur 127.0.0.1 uniquement. Une interface d'administration ne
 * se publie pas sur un réseau sans TLS ni reverse proxy.
 */

import http from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(HERE, 'public');
const COOKIE_NAME = 'tn_session';
const SESSION_TTL_MS = 15 * 60 * 1000;
const MAX_BODY_BYTES = 8 * 1024;

const PAGES = {
  '/': 'index.html',
  '/login': 'login.html',
};

const ASSETS = {
  '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
  '/style.css': ['style.css', 'text/css; charset=utf-8'],
};

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy':
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; " +
    "style-src 'self'; script-src 'self'; frame-ancestors 'none'",
};

function parseCookies(header = '') {
  return Object.fromEntries(
    header
      .split(';')
      .map((part) => part.trim())
      .filter((part) => part.includes('='))
      .map((part) => {
        const index = part.indexOf('=');
        return [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      }),
  );
}

function sessionCookie(sessionId, ttlSeconds, secure) {
  const parts = [
    `${COOKIE_NAME}=${sessionId}`,
    'HttpOnly',
    'SameSite=Strict',
    'Path=/',
    `Max-Age=${ttlSeconds}`,
  ];
  if (secure) parts.push('Secure');
  return parts.join('; ');
}

function clearCookie(secure) {
  return sessionCookie('', 0, secure);
}

function securityHeaders(extra = {}) {
  return { ...SECURITY_HEADERS, 'Cache-Control': 'no-store', ...extra };
}

function sendJson(res, status, payload, headers = {}) {
  const body = JSON.stringify(payload ?? {});
  res.writeHead(status, securityHeaders({
    'Content-Type': 'application/json; charset=utf-8',
    ...headers,
  }));
  res.end(body);
}

function redirect(res, location, headers = {}) {
  res.writeHead(302, securityHeaders({ Location: location, ...headers }));
  res.end();
}

async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new Error('corps de requête trop volumineux');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function readJson(req) {
  const raw = await readBody(req);
  if (!raw) return {};
  try {
    const value = JSON.parse(raw);
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function sameOrigin(req) {
  const origin = req.headers.origin;
  if (!origin) return true; // curl, formulaires same-origin sans Origin
  try {
    return new URL(origin).host === req.headers.host;
  } catch {
    return false;
  }
}

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1']);

/**
 * Décide sur quelle interface écouter.
 *
 * Écouter ailleurs que sur la boucle expose une page d'administration au réseau :
 * le mot de passe et le code TOTP y circulent en clair si TLS n'est pas devant.
 * Comme pour le mode live, l'exposition demande deux gestes explicites.
 */
export function resolveHost(env = process.env) {
  const host = env.DASHBOARD_HOST ?? '127.0.0.1';
  if (!LOOPBACK_HOSTS.has(host) && env.DASHBOARD_ALLOW_REMOTE !== 'true') {
    throw new Error(
      `DASHBOARD_HOST=${host} exposerait l'administration sur le réseau. `
      + 'Ajouter DASHBOARD_ALLOW_REMOTE=true dans dashboard/.env après avoir lu '
      + "dashboard/README.md : sans TLS, le mot de passe et le code Authy sont "
      + 'visibles sur le réseau.',
    );
  }
  return host;
}

export function createDashboardServer(options = {}) {
  const apiBaseUrl = (options.apiBaseUrl ?? process.env.API_BASE_URL ?? 'http://127.0.0.1:8080')
    .replace(/\/+$/, '');
  const sessionTtlMs = options.sessionTtlMs ?? SESSION_TTL_MS;
  const pollMs = options.pollMs ?? Number(process.env.DASHBOARD_POLL_MS ?? 2000);
  const cookieSecure = options.cookieSecure ?? process.env.DASHBOARD_COOKIE_SECURE === 'true';
  const fetchImpl = options.fetchImpl ?? fetch;

  /** sid -> { token, expiresAt } */
  const sessions = new Map();

  function getSession(req) {
    const sid = parseCookies(req.headers.cookie ?? '')[COOKIE_NAME];
    if (!sid) return null;
    const session = sessions.get(sid);
    if (!session) return null;
    if (session.expiresAt <= Date.now()) {
      sessions.delete(sid);
      return null;
    }
    return { sid, ...session };
  }

  function requireSession(req, res) {
    const session = getSession(req);
    if (!session) {
      sendJson(res, 401, { error: 'session requise' });
      return null;
    }
    return session;
  }

  async function callApi(pathname, { method = 'GET', token, body } = {}) {
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      const response = await fetchImpl(apiBaseUrl + pathname, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(10_000),
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = { raw: text.slice(0, 500) };
      }
      return { status: response.status, data };
    } catch (error) {
      return { status: 502, data: { error: `API injoignable : ${error.message}` } };
    }
  }

  async function sendPage(res, filename) {
    const data = await readFile(path.join(PUBLIC_DIR, filename));
    res.writeHead(200, securityHeaders({ 'Content-Type': 'text/html; charset=utf-8' }));
    res.end(data);
  }

  async function sendAsset(res, filename, contentType) {
    const data = await readFile(path.join(PUBLIC_DIR, filename));
    res.writeHead(200, securityHeaders({ 'Content-Type': contentType }));
    res.end(data);
  }

  function expireSession(res, session) {
    sessions.delete(session.sid);
    sendJson(res, 401, { error: 'session expirée' }, { 'Set-Cookie': clearCookie(cookieSecure) });
  }

  async function handle(req, res) {
    const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
    const pathname = url.pathname;
    const method = req.method ?? 'GET';

    if (method === 'GET' && pathname in PAGES) {
      const session = getSession(req);
      if (pathname === '/login') {
        if (session) return redirect(res, '/');
        return sendPage(res, PAGES[pathname]);
      }
      if (!session) return redirect(res, '/login');
      return sendPage(res, PAGES[pathname]);
    }

    if (method === 'GET' && pathname in ASSETS) {
      const [filename, contentType] = ASSETS[pathname];
      return sendAsset(res, filename, contentType);
    }

    if (method === 'POST' && pathname === '/login') {
      const form = new URLSearchParams(await readBody(req));
      const password = form.get('password') ?? '';
      const totp = (form.get('totp') ?? '').replace(/\s+/g, '');
      if (!password || !/^\d{6}$/.test(totp)) {
        return redirect(res, '/login?error=1');
      }
      const result = await callApi('/api/auth/login', {
        method: 'POST',
        body: { password, totp },
      });
      if (result.status !== 200 || !result.data?.token) {
        return redirect(res, '/login?error=1');
      }
      const sid = randomUUID();
      // Purge les sessions expirées : sans cela, un serveur qui tourne des jours
      // accumule une entrée par connexion, y compris celles jamais relues.
      const now = Date.now();
      for (const [key, value] of sessions) {
        if (value.expiresAt <= now) sessions.delete(key);
      }
      sessions.set(sid, { token: result.data.token, expiresAt: now + sessionTtlMs });
      return redirect(res, '/', {
        'Set-Cookie': sessionCookie(sid, Math.floor(sessionTtlMs / 1000), cookieSecure),
      });
    }

    if (method === 'POST' && pathname === '/logout') {
      if (!sameOrigin(req)) return sendJson(res, 403, { error: 'origine refusée' });
      const session = getSession(req);
      if (session) sessions.delete(session.sid);
      return redirect(res, '/login', { 'Set-Cookie': clearCookie(cookieSecure) });
    }

    if (method === 'GET' && pathname === '/api/status') {
      const session = requireSession(req, res);
      if (!session) return;
      const result = await callApi('/api/status', { token: session.token });
      if (result.status === 401) return expireSession(res, session);
      return sendJson(res, result.status, result.data);
    }

    if (method === 'POST' && pathname.startsWith('/api/control/')) {
      if (!sameOrigin(req)) return sendJson(res, 403, { error: 'origine refusée' });
      const session = requireSession(req, res);
      if (!session) return;

      let body;
      if (pathname === '/api/control/panic') {
        const payload = await readJson(req);
        const totp = String(payload.totp ?? '').replace(/\s+/g, '');
        if (!/^\d{6}$/.test(totp)) {
          return sendJson(res, 400, { error: 'code TOTP à 6 chiffres requis' });
        }
        body = { totp };
      }
      const result = await callApi(pathname, { method: 'POST', token: session.token, body });
      if (result.status === 401) return expireSession(res, session);
      return sendJson(res, result.status, result.data);
    }

    if (method === 'GET' && pathname === '/events') {
      const session = requireSession(req, res);
      if (!session) return;
      res.writeHead(200, securityHeaders({
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
        'X-Accel-Buffering': 'no',
      }));
      res.write('retry: 3000\n\n');

      let closed = false;
      let timer = null;
      const stop = () => {
        closed = true;
        if (timer) clearInterval(timer);
      };
      req.on('close', stop);

      const tick = async () => {
        if (closed) return;
        const result = await callApi('/api/status', { token: session.token });
        if (closed) return;
        if (result.status === 401) {
          sessions.delete(session.sid);
          res.write('event: expired\ndata: {}\n\n');
          stop();
          res.end();
          return;
        }
        try {
          res.write(`data: ${JSON.stringify(result.data)}\n\n`);
        } catch {
          stop();
        }
      };

      await tick();
      if (!closed) timer = setInterval(tick, pollMs);
      return;
    }

    return sendJson(res, 404, { error: 'introuvable' });
  }

  const server = http.createServer((req, res) => {
    handle(req, res).catch((error) => {
      if (!res.headersSent) {
        sendJson(res, 500, { error: `erreur interne : ${error.message}` });
      } else {
        res.end();
      }
    });
  });

  return { server, sessions, config: { apiBaseUrl, sessionTtlMs, pollMs, cookieSecure } };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    process.loadEnvFile(fileURLToPath(new URL('./.env', import.meta.url)));
  } catch {
    // Pas de dashboard/.env : les variables d'environnement suffisent.
  }

  let host;
  try {
    host = resolveHost();
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }

  const port = Number(process.env.DASHBOARD_PORT ?? 8081);
  const { server, config } = createDashboardServer();
  server.listen(port, host, () => {
    console.log(`Dashboard Trading Ninja : http://${host}:${port}`);
    console.log(`API : ${config.apiBaseUrl}`);
    if (!LOOPBACK_HOSTS.has(host)) {
      console.warn(
        '\nATTENTION : dashboard exposé au réseau sans TLS.\n'
        + '  Le mot de passe et le code Authy circulent en clair.\n'
        + '  Utiliser Tailscale/WireGuard ou un reverse proxy HTTPS.\n'
        + '  Ne jamais ouvrir ce port directement sur Internet.\n',
      );
    }
  });
}
