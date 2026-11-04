/* Dashboard Trading Ninja — aucune dépendance, aucun innerHTML avec des données.
 * Le texte affiché vient de l'API (thèses du LLM, symboles) : tout passe par
 * textContent, jamais par innerHTML, pour qu'une thèse ne puisse pas injecter
 * de balise dans la page. */

(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- login --

  const loginError = $('login-error');
  if (loginError) {
    if (new URLSearchParams(window.location.search).has('error')) {
      loginError.hidden = false;
    }
    return;
  }

  // ------------------------------------------------------------ dashboard --

  const money = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('fr-FR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    });
  };

  const percent = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? `${(number * 100).toFixed(2)} %` : '—';
  };

  const setText = (id, value) => {
    const element = $(id);
    if (element) element.textContent = value;
  };

  function setFeedback(message, kind = '') {
    const element = $('feedback');
    if (!element) return;
    element.textContent = message;
    element.dataset.kind = kind;
  }

  function renderPosition(position) {
    const box = $('position');
    const symbol = $('position-symbol');
    if (!box) return;
    box.replaceChildren();
    if (!position) {
      box.className = 'position empty';
      box.textContent = 'Aucune position ouverte.';
      if (symbol) symbol.textContent = '';
      return;
    }
    box.className = 'position';
    if (symbol) symbol.textContent = position.symbol;
    const rows = [
      ['Entrée', money(position.entry_price)],
      ['Quantité', position.amount],
      ['Stop-loss', money(position.stop_loss_price)],
      ['Take-profit', position.take_profit_price ? money(position.take_profit_price) : '—'],
      ['Risque', `${money(position.risk_amount_quote)} EUR`],
      ['Ouverte', new Date(position.opened_at).toLocaleString('fr-FR')],
    ];
    for (const [label, value] of rows) {
      const row = document.createElement('div');
      row.className = 'kv';
      const key = document.createElement('span');
      key.textContent = label;
      const val = document.createElement('strong');
      val.textContent = value ?? '—';
      row.append(key, val);
      box.append(row);
    }
  }

  function renderSignals(signals) {
    const body = $('signals');
    if (!body) return;
    body.replaceChildren();
    if (!signals || signals.length === 0) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.className = 'muted';
      cell.textContent = 'Aucun signal enregistré.';
      row.append(cell);
      body.append(row);
      return;
    }
    for (const signal of signals) {
      const row = document.createElement('tr');
      const cells = [
        new Date(signal.created_at).toLocaleTimeString('fr-FR'),
        signal.symbol,
        signal.action,
        Number(signal.confidence).toFixed(2),
        signal.market_regime,
        signal.thesis,
      ];
      for (const value of cells) {
        const cell = document.createElement('td');
        cell.textContent = value ?? '—';
        if (value === signal.action) cell.dataset.action = String(value).toLowerCase();
        row.append(cell);
      }
      body.append(row);
    }
  }

  function render(status) {
    if (!status || typeof status !== 'object') return;
    const badge = $('state');
    if (badge) {
      badge.textContent = status.state ?? '—';
      badge.dataset.state = status.state ?? '';
    }
    setText('equity', money(status.equity));
    setText('free', money(status.free_quote));
    setText('reserve', money(status.reserve_quote));
    setText('drawdown', percent(status.drawdowns?.from_hwm));
    renderPosition(status.position);
    renderSignals(status.recent?.signals);
    setText('mode', status.mode ?? '—');
    const stamp = new Date().toLocaleTimeString('fr-FR');
    setText('updated', `maj ${stamp}`);
    setText('updated-footer', stamp);
  }

  let busy = false;

  async function control(action, payload) {
    if (busy) return null;
    busy = true;
    setFeedback('envoi…');
    try {
      const response = await fetch(`/api/control/${action}`, {
        method: 'POST',
        headers: payload ? { 'Content-Type': 'application/json' } : {},
        body: payload ? JSON.stringify(payload) : undefined,
      });
      if (response.status === 401) {
        window.location.href = '/login';
        return null;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setFeedback(data.detail ?? data.error ?? 'commande refusée', 'error');
        return null;
      }
      setFeedback('commande acceptée', 'ok');
      if (data.state) render(data);
      return data;
    } catch (error) {
      setFeedback(`réseau : ${error.message}`, 'error');
      return null;
    } finally {
      busy = false;
    }
  }

  $('pause')?.addEventListener('click', () => control('pause'));
  $('resume')?.addEventListener('click', () => control('resume'));

  const dialog = $('panic-dialog');
  $('panic')?.addEventListener('click', () => {
    const code = $('panic-code');
    if (code) code.value = '';
    dialog?.showModal();
  });
  $('panic-cancel')?.addEventListener('click', () => dialog?.close());
  $('panic-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const code = $('panic-code')?.value.trim() ?? '';
    if (!/^\d{6}$/.test(code)) {
      setFeedback('code TOTP à 6 chiffres requis', 'error');
      return;
    }
    dialog?.close();
    await control('panic', { totp: code });
  });

  const stream = new EventSource('/events');
  stream.onopen = () => setText('conn', 'en ligne');
  stream.onerror = () => setText('conn', 'hors ligne');
  stream.onmessage = (event) => {
    try {
      render(JSON.parse(event.data));
    } catch {
      /* un message tronqué ne doit pas casser le flux */
    }
  };
  stream.addEventListener('expired', () => {
    window.location.href = '/login';
  });
})();
