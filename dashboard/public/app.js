/* Dashboard Trading Ninja — aucune dépendance, aucun innerHTML avec des données.
 * Le texte affiché vient de l'API (thèses du LLM, symboles, motifs de refus) : tout
 * passe par textContent, jamais par innerHTML, pour qu'une thèse ne puisse pas
 * injecter de balise dans la page. */

(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- login --

  const loginError = $('login-error');
  if (loginError) {
    loginError.hidden = !new URLSearchParams(window.location.search).has('error');
    return;
  }

  // ------------------------------------------------------------ constantes --

  const STATE_LABELS = {
    IDLE: 'En veille',
    EVALUATING: 'Analyse',
    EXECUTING: 'Achat',
    IN_POSITION: 'Position ouverte',
    POST_TRADE_SWEEP: 'Après-vente',
    COOLDOWN: 'Pause',
    LOCKED_CIRCUIT_BREAKER: 'Suspendu',
    EMERGENCY_HALT: 'Arrêt d’urgence',
  };

  const ACTION_LABELS = { BUY: 'Achat', SELL: 'Vente', HOLD: 'Attente', HALT: 'Arrêt' };
  const REGIME_LABELS = {
    TRENDING_UP: 'Hausse',
    TRENDING_DOWN: 'Baisse',
    RANGING: 'Range',
    VOLATILE: 'Volatil',
  };
  const SIDE_LABELS = { BUY: 'Achat', SELL: 'Vente' };
  const ORDER_STATUS_LABELS = {
    PENDING: 'En attente',
    SUBMITTED: 'Soumis',
    FILLED: 'Exécuté',
    PARTIALLY_FILLED: 'Partiel',
    CANCELED: 'Annulé',
    REJECTED: 'Refusé',
    EXPIRED: 'Expiré',
    UNKNOWN: 'Inconnu',
  };
  const PURPOSE_LABELS = {
    ENTRY: 'Entrée',
    STOP_LOSS: 'Stop',
    TAKE_PROFIT: 'Objectif',
    EMERGENCY_EXIT: 'Liquidation',
    RECONCILIATION: 'Réconciliation',
  };
  const POSITION_STATUS_LABELS = {
    OPEN: 'Ouverte',
    CLOSED: 'Fermée',
    EMERGENCY_LIQUIDATED: 'Liquidée',
  };
  const REJECTION_LABELS = {
    NONE: 'Aucun',
    ACTION_IS_NOT_BUY: 'Action non acheteuse',
    STALE_MARKET_DATA: 'Données de marché périmées',
    INVALID_MARKET_DATA: 'Données de marché invalides',
    MALFORMED_INTENT: 'Intention mal formée',
    INTERNAL_ERROR: 'Erreur interne',
    BOT_LOCKED: 'Bot verrouillé',
    CIRCUIT_BREAKER_DAILY_DRAWDOWN: 'Coupe-circuit journalier',
    CIRCUIT_BREAKER_WEEKLY_DRAWDOWN: 'Coupe-circuit hebdomadaire',
    CIRCUIT_BREAKER_MAX_DRAWDOWN: 'Coupe-circuit depuis le plus haut',
    CIRCUIT_BREAKER_CONSECUTIVE_LOSSES: 'Pertes consécutives',
    MAX_ORDERS_PER_HOUR_EXCEEDED: 'Trop d’ordres par heure',
    CONFIDENCE_TOO_LOW: 'Confiance trop basse',
    POSITION_ALREADY_OPEN: 'Position déjà ouverte',
    REJECTED_LOW_VOLATILITY: 'Volatilité trop faible',
    REJECTED_EXTREME_VOLATILITY: 'Volatilité extrême',
    REJECTED_COUNTER_TREND_EMA200: 'Contre la tendance EMA200',
    REJECTED_REGIME_CONFLICT: 'Régime contradictoire',
    REJECTED_FALLING_KNIFE: 'Couteau qui tombe',
    INSUFFICIENT_FREE_BALANCE: 'Solde libre insuffisant',
    INVALID_STOP_LOSS_CALCULATION: 'Stop invalide',
    SIZING_BELOW_EXCHANGE_MINIMUM: 'Sous le minimum exchange',
    EXCHANGE_MIN_NOTIONAL_FAILED: 'Notionnel minimum refusé',
    RISK_LIMIT_EXCEEDED: 'Limite de risque dépassée',
  };

  const RANGE_MS = {
    '24h': 24 * 60 * 60 * 1000,
    '7d': 7 * 24 * 60 * 60 * 1000,
    '30d': 30 * 24 * 60 * 60 * 1000,
  };

  // ------------------------------------------------------------ formatage --

  const toNumber = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const money = (value) => {
    const number = toNumber(value);
    if (number === null) return '—';
    return number.toLocaleString('fr-FR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    });
  };

  const axisMoney = (value) => {
    const number = toNumber(value);
    if (number === null) return '—';
    return number.toLocaleString('fr-FR', {
      maximumFractionDigits: Math.abs(number) >= 100 ? 0 : 2,
    });
  };

  const amount = (value) => {
    const number = toNumber(value);
    if (number === null) return '—';
    return number.toLocaleString('fr-FR', { maximumFractionDigits: 8 });
  };

  const percent = (value, digits = 1) => {
    const number = toNumber(value);
    if (number === null) return '—';
    const precision = Number.isInteger(digits) ? digits : 1;
    return `${(number * 100).toLocaleString('fr-FR', {
      minimumFractionDigits: precision,
      maximumFractionDigits: precision,
    })} %`;
  };

  const rMultiple = (value) => {
    const number = toNumber(value);
    if (number === null) return '—';
    return `${number.toLocaleString('fr-FR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} R`;
  };

  const timeOnly = (value) => {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const dateTime = (value) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('fr-FR', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const parseDay = (value) => {
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) {
      const [year, month, day] = value.slice(0, 10).split('-').map(Number);
      return new Date(year, month - 1, day);
    }
    return new Date(value);
  };

  const dayShort = (value) => {
    const date = parseDay(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });
  };

  const stateLabel = (state) => STATE_LABELS[state] || state || '—';
  const actionLabel = (value) => ACTION_LABELS[value] || value || '—';
  const regimeLabel = (value) => REGIME_LABELS[value] || value || '—';
  const sideLabel = (value) => SIDE_LABELS[value] || value || '—';
  const orderStatusLabel = (value) => ORDER_STATUS_LABELS[value] || value || '—';
  const purposeLabel = (value) => PURPOSE_LABELS[value] || value || '—';
  const positionStatusLabel = (value) => POSITION_STATUS_LABELS[value] || value || '—';
  const rejectionLabel = (value) => REJECTION_LABELS[value] || value || '—';

  const setText = (id, value) => {
    const element = $(id);
    if (!element) return;
    element.textContent = value === null || value === undefined ? '—' : String(value);
  };

  const setSign = (id, value) => {
    const element = $(id);
    if (!element) return;
    const number = toNumber(value);
    if (number === null || number === 0) {
      delete element.dataset.sign;
    } else {
      element.dataset.sign = number > 0 ? 'up' : 'down';
    }
  };

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

  function setFeedback(message, kind = '') {
    const element = $('feedback');
    if (!element) return;
    element.textContent = message;
    element.dataset.kind = kind;
  }

  function setSettingsFeedback(message, kind = '') {
    const element = $('settings-feedback');
    if (!element) return;
    element.textContent = message;
    element.dataset.kind = kind;
  }

  // --------------------------------------------------------------- charts --

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgEl(name, attrs = {}) {
    const element = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
    return element;
  }

  function svgText(x, y, text, anchor = 'middle') {
    const element = svgEl('text', { x, y, 'text-anchor': anchor });
    element.textContent = text;
    element.setAttribute('class', 'axis');
    return element;
  }

  let currentRange = 'all';

  function updateRangeButtons() {
    for (const button of document.querySelectorAll('[data-range]')) {
      const active = button.dataset.range === currentRange;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }

  function renderEquitySummary(series) {
    const last = series[series.length - 1];
    const first = series[0];
    const current = last.equity;
    const change = current - first.equity;
    const changePct = first.equity !== 0 ? change / first.equity : null;
    const values = series.map((point) => point.equity);
    const high = Math.max(...values);
    const low = Math.min(...values);
    setText('chart-current', `${money(current)} EUR`);
    setText(
      'chart-change',
      `${change >= 0 ? '+' : ''}${money(change)} EUR`
        + (changePct === null
          ? ''
          : ` (${change >= 0 ? '+' : ''}${(changePct * 100).toLocaleString('fr-FR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })} %)`),
    );
    setSign('chart-change', change);
    setText('chart-high', `${money(high)} EUR`);
    setText('chart-low', `${money(low)} EUR`);
    setText('chart-period', `${dateTime(first.at)} → ${dateTime(last.at)}`);
  }

  function clearEquitySummary(message) {
    const box = $('equity-chart');
    if (box) {
      box.replaceChildren();
      const empty = document.createElement('p');
      empty.className = 'muted empty-state';
      empty.textContent = message;
      box.append(empty);
    }
    for (const id of ['chart-current', 'chart-change', 'chart-high', 'chart-low', 'chart-period']) {
      setText(id, '—');
    }
    const tooltip = $('equity-tooltip');
    if (tooltip) tooltip.hidden = true;
  }

  function renderEquityChart(points) {
    const box = $('equity-chart');
    if (!box) return;
    const all = (points || [])
      .map((point) => ({
        at: new Date(point.at),
        equity: toNumber(point.equity),
      }))
      .filter((point) => point.equity !== null && !Number.isNaN(point.at.getTime()));

    if (all.length === 0) {
      clearEquitySummary('Pas encore d’historique de capital.');
      return;
    }

    const lastTime = all[all.length - 1].at.getTime();
    const rangeMs = RANGE_MS[currentRange] || null;
    const series = rangeMs
      ? all.filter((point) => point.at.getTime() >= lastTime - rangeMs)
      : all;

    if (series.length < 2) {
      clearEquitySummary('Pas assez de points sur cette période. Choisir « Tout ».');
      return;
    }

    box.replaceChildren();

    const W = 900;
    const H = 260;
    const PAD = { left: 68, right: 18, top: 22, bottom: 30 };
    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;
    const values = series.map((point) => point.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const rawSpan = max - min;
    const headroom = rawSpan > 0 ? rawSpan * 0.08 : Math.max(1, Math.abs(max) * 0.01);
    const low = min - headroom;
    const high = max + headroom;
    const span = high - low || 1;
    const xAt = (index) => PAD.left + plotW * (index / (series.length - 1));
    const yAt = (value) => H - PAD.bottom - plotH * ((value - low) / span);
    const baseY = H - PAD.bottom;

    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`,
      role: 'img',
      'aria-label': 'Évolution du capital',
    });

    const title = svgEl('title');
    title.textContent = 'Évolution du capital';
    const description = svgEl('desc');
    description.textContent = `Courant ${money(values[values.length - 1])} EUR, `
      + `plus haut ${money(max)} EUR, plus bas ${money(min)} EUR.`;
    svg.append(title, description);

    const defs = svgEl('defs');
    const gradient = svgEl('linearGradient', {
      id: 'equity-fill', x1: '0', y1: '0', x2: '0', y2: '1',
    });
    gradient.append(
      svgEl('stop', { offset: '0%', 'stop-color': '#5cd6ff', 'stop-opacity': '0.3' }),
      svgEl('stop', { offset: '100%', 'stop-color': '#5cd6ff', 'stop-opacity': '0' }),
    );
    defs.append(gradient);
    svg.append(defs);

    for (let k = 0; k <= 4; k += 1) {
      const value = low + span * (k / 4);
      const y = yAt(value);
      svg.append(svgEl('line', {
        x1: PAD.left, y1: y, x2: W - PAD.right, y2: y, class: 'grid',
      }));
      svg.append(svgText(PAD.left - 8, y + 4, axisMoney(value), 'end'));
    }

    const coords = series.map((point, index) => [xAt(index), yAt(point.equity)]);

    const areaD = [
      `M ${xAt(0).toFixed(1)} ${baseY.toFixed(1)}`,
      ...coords.map(([x, y]) => `L ${x.toFixed(1)} ${y.toFixed(1)}`),
      `L ${xAt(series.length - 1).toFixed(1)} ${baseY.toFixed(1)}`,
      'Z',
    ].join(' ');
    svg.append(svgEl('path', { d: areaD, class: 'area' }));

    const lineD = coords
      .map(([x, y], index) => `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)
      .join(' ');
    svg.append(svgEl('path', { d: lineD, class: 'line' }));

    const lastIndex = series.length - 1;
    svg.append(svgEl('circle', {
      cx: xAt(lastIndex),
      cy: yAt(series[lastIndex].equity),
      r: 4,
      class: 'dot',
    }));

    const labelIndexes = [...new Set([
      0,
      Math.floor(lastIndex / 2),
      lastIndex,
    ])];
    for (const index of labelIndexes) {
      const anchor = index === 0 ? 'start' : index === lastIndex ? 'end' : 'middle';
      svg.append(svgText(
        xAt(index),
        H - 8,
        dayShort(series[index].at),
        anchor,
      ));
    }

    const hoverLine = svgEl('line', {
      x1: 0, y1: PAD.top, x2: 0, y2: baseY, class: 'hover-line', visibility: 'hidden',
    });
    const hoverDot = svgEl('circle', {
      r: 4.5, cx: 0, cy: 0, class: 'hover-dot', visibility: 'hidden',
    });
    svg.append(hoverLine, hoverDot);

    const hidePoint = () => {
      hoverLine.setAttribute('visibility', 'hidden');
      hoverDot.setAttribute('visibility', 'hidden');
      const tooltip = $('equity-tooltip');
      if (tooltip) tooltip.hidden = true;
    };

    const showPoint = (index) => {
      const point = series[index];
      const x = xAt(index);
      const y = yAt(point.equity);
      hoverLine.setAttribute('x1', x);
      hoverLine.setAttribute('x2', x);
      hoverLine.setAttribute('visibility', 'visible');
      hoverDot.setAttribute('cx', x);
      hoverDot.setAttribute('cy', y);
      hoverDot.setAttribute('visibility', 'visible');

      const tooltip = $('equity-tooltip');
      const shell = box.closest('.chart-shell');
      if (!tooltip || !shell) return;
      tooltip.replaceChildren();
      const value = document.createElement('strong');
      value.textContent = `${money(point.equity)} EUR`;
      const stamp = document.createElement('span');
      stamp.textContent = dateTime(point.at);
      tooltip.append(value, stamp);
      tooltip.hidden = false;

      const shellRect = shell.getBoundingClientRect();
      const svgRect = svg.getBoundingClientRect();
      const left = svgRect.left - shellRect.left + (x / W) * svgRect.width;
      const top = svgRect.top - shellRect.top + (y / H) * svgRect.height;
      tooltip.style.left = `${clamp(left, 78, shellRect.width - 78)}px`;
      tooltip.style.top = `${Math.max(4, top - 54)}px`;
    };

    svg.addEventListener('pointermove', (event) => {
      const rect = svg.getBoundingClientRect();
      if (!rect.width) return;
      const x = ((event.clientX - rect.left) / rect.width) * W;
      if (x < PAD.left - 12 || x > W - PAD.right + 12) {
        hidePoint();
        return;
      }
      const ratio = clamp((x - PAD.left) / plotW, 0, 1);
      showPoint(Math.round(ratio * (series.length - 1)));
    });
    svg.addEventListener('pointerleave', hidePoint);

    box.append(svg);
    renderEquitySummary(series);
  }

  function renderDailyChart(days) {
    const box = $('daily-chart');
    if (!box) return;
    box.replaceChildren();
    const series = (days || [])
      .map((day) => ({
        at: parseDay(day.day),
        net: toNumber(day.net_pnl) ?? 0,
        trades: Number(day.trades) || 0,
        wins: Number(day.wins) || 0,
      }))
      .filter((day) => !Number.isNaN(day.at.getTime()));

    if (series.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'muted empty-state';
      empty.textContent = 'Aucune transaction terminée sur la période.';
      box.append(empty);
      setText('daily-total', '14 derniers jours');
      return;
    }

    const W = 900;
    const H = 220;
    const PAD = { left: 52, right: 18, top: 22, bottom: 30 };
    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;
    const values = series.map((day) => day.net);
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const span = max - min || 1;
    const yAt = (value) => H - PAD.bottom - plotH * ((value - min) / span);
    const zeroY = yAt(0);
    const barW = Math.min(34, (plotW / series.length) * 0.66);

    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`,
      role: 'img',
      'aria-label': 'Gain ou perte par jour',
    });
    const title = svgEl('title');
    title.textContent = 'Gain ou perte par jour';
    svg.append(title);

    for (let k = 0; k <= 4; k += 1) {
      const value = min + span * (k / 4);
      const y = yAt(value);
      svg.append(svgEl('line', {
        x1: PAD.left, y1: y, x2: W - PAD.right, y2: y, class: 'grid',
      }));
    }
    svg.append(svgText(PAD.left - 8, yAt(max) + 4, axisMoney(max), 'end'));
    svg.append(svgText(PAD.left - 8, yAt(min) + 4, axisMoney(min), 'end'));
    svg.append(svgEl('line', {
      x1: PAD.left, y1: zeroY, x2: W - PAD.right, y2: zeroY, class: 'grid',
    }));

    series.forEach((day, index) => {
      const center = PAD.left + (plotW / series.length) * (index + 0.5);
      const value = day.net;
      const top = value >= 0 ? yAt(value) : zeroY;
      const height = Math.max(1, Math.abs(yAt(value) - zeroY));
      const rect = svgEl('rect', {
        x: center - barW / 2,
        y: top,
        width: barW,
        height,
        rx: 2,
        class: value >= 0 ? 'bar-pos' : 'bar-neg',
      });
      const rectTitle = svgEl('title');
      rectTitle.textContent = `${dayShort(day.at)} · ${money(value)} EUR · `
        + `${day.trades} transaction(s), ${day.wins} gagnante(s)`;
      rect.append(rectTitle);
      svg.append(rect);
      svg.append(svgText(center, H - 8, dayShort(day.at)));
    });

    box.append(svg);
    const total = values.reduce((sum, value) => sum + value, 0);
    setText('daily-total', `${series.length} jour(s) · net ${money(total)} EUR`);
  }

  // ---------------------------------------------------------- modèles et états --

  function renderLlm(llm) {
    setText('llm-model', llm.model || '—');
    const last = llm.last;
    if (last) {
      setText('llm-action', actionLabel(last.action));
      setText('llm-confidence', percent(last.confidence));
      setText('llm-latency', `${Number(last.inference_time_ms || 0)} ms`);
      setText('llm-thesis', last.thesis || '');
    } else {
      setText('llm-action', 'Aucune décision');
      setText('llm-confidence', '—');
      setText('llm-latency', '—');
      setText('llm-thesis', '');
    }
    setText('llm-total', String(llm.total ?? 0));
    const counts = llm.counts || {};
    setText('llm-counts', ['BUY', 'SELL', 'HOLD', 'HALT']
      .map((action) => `${actionLabel(action)} ${counts[action] ?? 0}`)
      .join(' · '));
    setText('llm-degraded', String(llm.degraded ?? 0));
  }

  function renderShadow(shadow) {
    setText('shadow-total', `${shadow.total ?? 0} achat(s) non pris`);
    const byKind = shadow.by_kind || {};
    setText('shadow-rejected', String(byKind.GATE_REJECTED_BUY ?? 0));
    setText('shadow-holds', String(byKind.MODEL_HOLD ?? 0));
    setText('shadow-planned', String(shadow.planned ?? 0));
    setText('shadow-no-plan', String(shadow.no_plan ?? 0));
    setText('shadow-pnl', money(shadow.net_pnl));
    setText('shadow-wins', `${shadow.wins ?? 0} / ${shadow.losses ?? 0}`);
    setText('shadow-open', String(shadow.open ?? 0));
    const codes = shadow.by_rejection || [];
    setText(
      'shadow-note',
      codes.length
        ? `Refus : ${codes.map((row) => `${rejectionLabel(row.code)} ${row.count}`).join(' · ')}`
        : 'Aucun refus enregistré pour l’instant.',
    );
  }

  function renderLessons(lessons) {
    const active = lessons.active || [];
    const list = $('lessons-list');
    const empty = $('lessons-empty');
    if (!list || !empty) return;
    setText(
      'lessons-meta',
      lessons.version
        ? `v${lessons.version} · ${lessons.source_count ?? 0} résultats`
        : '',
    );
    list.replaceChildren();
    for (const lesson of active) {
      const item = document.createElement('li');
      item.textContent = lesson;
      list.appendChild(item);
    }
    list.hidden = active.length === 0;
    empty.hidden = active.length > 0;
  }

  function renderRejections(rows) {
    const list = $('rejections-list');
    const empty = $('rejections-empty');
    if (!list || !empty) return;
    const valid = (rows || [])
      .filter((row) => Number(row.count) > 0)
      .sort((a, b) => Number(b.count) - Number(a.count));
    const total = valid.reduce((sum, row) => sum + Number(row.count), 0);
    setText('rejections-total', total ? `${total} refus` : '');
    list.replaceChildren();

    if (valid.length === 0) {
      list.hidden = true;
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    list.hidden = false;
    const max = Math.max(...valid.map((row) => Number(row.count)), 1);

    for (const row of valid) {
      const item = document.createElement('div');
      item.className = 'rejection';

      const label = document.createElement('span');
      label.className = 'rejection-label';
      label.textContent = rejectionLabel(row.code);
      const code = document.createElement('span');
      code.className = 'rejection-code';
      code.textContent = row.code || '';
      label.append(' ', code);

      const count = document.createElement('span');
      count.className = 'rejection-count';
      count.textContent = `${row.count} · ${percent(row.percentage)}`;

      const track = document.createElement('div');
      track.className = 'rejection-track';
      const fill = document.createElement('span');
      fill.className = 'rejection-fill';
      fill.style.setProperty('--bar', `${Math.max(2, (Number(row.count) / max) * 100)}%`);
      track.append(fill);

      item.append(label, count, track);
      list.append(item);
    }
  }

  let latestEquityPoints = [];

  function renderStats(stats) {
    if (!stats || typeof stats !== 'object') return;
    latestEquityPoints = stats.equity_curve || [];
    const pnl = stats.pnl || {};
    setText('stat-pnl', money(pnl.net_pnl));
    setSign('stat-pnl', pnl.net_pnl);
    setText('stat-trades', `${pnl.wins ?? 0} / ${pnl.trades ?? 0}`);
    setText('stat-losses', `${pnl.losses ?? 0} perte(s)`);
    setText('stat-winrate', percent(pnl.win_rate));
    setText('stat-r', rMultiple(pnl.r_multiple));
    setText('stat-fees', money(pnl.fees));
    setText('stat-payoff', pnl.payoff_ratio ?? '—');
    setText('stat-profit-factor', pnl.profit_factor_net ?? '—');
    const capital = stats.capital || {};
    setText('capital-engaged', money(capital.engaged_position_quote));
    renderEquityChart(stats.equity_curve || []);
    renderDailyChart(stats.daily || []);
    renderLlm(stats.llm || {});
    renderRejections(stats.rejections || []);
    renderShadow(stats.shadow || {});
    renderLessons(stats.lessons || {});
  }

  async function loadStats() {
    try {
      const response = await fetch('/api/stats', { headers: { Accept: 'application/json' } });
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (!response.ok) return;
      renderStats(await response.json());
    } catch {
      /* le flux temps réel continue ; un graphique manquant n'arrête pas la page */
    }
  }

  // ------------------------------------------------------------ position --

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
      ['Prix actuel', money(position.current_price)],
      ['Quantité', amount(position.amount)],
      ['PnL latent', `${money(position.unrealized_pnl_quote)} EUR (${percent(Number(position.unrealized_pnl_pct) / 100)})`],
      ['Stop-loss', money(position.stop_loss_price)],
      ['ID stop', position.stop_loss_order_id || '—'],
      ['Breakeven', position.is_breakeven_active ? 'actif' : 'non'],
      ['Take-profit', position.take_profit_price ? money(position.take_profit_price) : '—'],
      ['Risque brut', `${money(position.risk_amount_quote)} EUR`],
      ['Perte au stop (frais inclus)', `${money(position.estimated_net_risk_quote)} EUR`],
      ['Ouverte', dateTime(position.opened_at)],
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

  // -------------------------------------------------------------- risque --

  function riskRatio(value, limit) {
    const current = toNumber(value);
    const ceiling = toNumber(limit);
    if (current === null || ceiling === null || ceiling <= 0) return null;
    return current / ceiling;
  }

  function applyRisk(key, valueText, limitText, ratio) {
    setText(`risk-${key}`, valueText);
    setText(`risk-${key}-limit`, limitText);
    const item = document.querySelector(`.risk[data-risk="${key}"]`);
    const bar = $(`risk-${key}-bar`);
    if (!item || !bar) return;
    const level = ratio === null
      ? 'unknown'
      : ratio >= 1
        ? 'danger'
        : ratio >= 0.75
          ? 'warn'
          : 'ok';
    item.dataset.level = level;
    bar.style.setProperty('--meter', `${clamp((ratio ?? 0) * 100, 0, 100).toFixed(1)}%`);
  }

  function renderRisk(status, settings) {
    const drawdowns = status?.drawdowns || {};
    const maxDaily = settings?.max_daily_loss_pct;
    const maxWeekly = settings?.max_weekly_loss_pct;
    const maxHwm = settings?.max_drawdown_from_hwm_pct;

    applyRisk(
      'daily',
      percent(drawdowns.daily),
      `plafond ${maxDaily !== undefined ? percent(maxDaily) : '—'}`,
      riskRatio(drawdowns.daily, maxDaily),
    );
    applyRisk(
      'weekly',
      percent(drawdowns.weekly),
      `plafond ${maxWeekly !== undefined ? percent(maxWeekly) : '—'}`,
      riskRatio(drawdowns.weekly, maxWeekly),
    );
    applyRisk(
      'hwm',
      percent(drawdowns.from_hwm),
      `plafond ${maxHwm !== undefined ? percent(maxHwm) : '—'}`,
      riskRatio(drawdowns.from_hwm, maxHwm),
    );

    const losses = toNumber(status?.consecutive_losses);
    const maxLosses = toNumber(settings?.max_consecutive_losses);
    applyRisk(
      'losses',
      `${losses ?? '—'} / ${maxLosses ?? '—'}`,
      `plafond ${maxLosses ?? '—'} pertes`,
      riskRatio(losses, maxLosses),
    );

    const orders = toNumber(status?.orders_last_hour);
    const maxOrders = toNumber(settings?.max_orders_per_hour);
    applyRisk(
      'orders',
      `${orders ?? '—'} / ${maxOrders ?? '—'}`,
      `plafond ${maxOrders ?? '—'} / h`,
      riskRatio(orders, maxOrders),
    );
  }

  // -------------------------------------------------------------- status --

  let haltTarget = null;
  let lastStatusAt = 0;
  let latestStatus = null;
  let latestSettings = null;

  function updateCountdown() {
    const element = $('halt-countdown');
    if (!element) return;
    if (!haltTarget) {
      element.textContent = 'une reprise manuelle';
      return;
    }
    const remaining = haltTarget.getTime() - Date.now();
    if (remaining <= 0) {
      element.textContent = 'maintenant (au prochain cycle)';
      return;
    }
    const hours = Math.floor(remaining / 3_600_000);
    const minutes = Math.floor((remaining % 3_600_000) / 60_000);
    element.textContent = `${hours} h ${String(minutes).padStart(2, '0')} min`;
  }

  function renderHalt(status) {
    const banner = $('halt-banner');
    if (!banner) return;
    const halt = status.halt || {};
    const isHalt = status.state === 'EMERGENCY_HALT'
      || status.state === 'LOCKED_CIRCUIT_BREAKER';
    banner.hidden = !isHalt;
    if (!isHalt) {
      haltTarget = null;
      return;
    }
    setText('halt-badge', status.state || 'ARRÊT');
    setText('halt-reason', halt.reason || 'arrêt sans motif enregistré');
    const target = halt.auto_resume_at ? new Date(halt.auto_resume_at) : null;
    haltTarget = target && !Number.isNaN(target.getTime()) ? target : null;
    const recovery = $('halt-recovery');
    const manual = $('halt-manual');
    if (recovery) recovery.hidden = !haltTarget;
    if (manual) manual.hidden = Boolean(haltTarget);
    updateCountdown();
  }

  function renderMode(status) {
    const mode = String(status.mode || '').toUpperCase();
    const badge = $('mode-badge');
    if (badge) {
      badge.textContent = mode === 'PAPER'
        ? 'PAPER · simulation'
        : mode === 'LIVE'
          ? 'LIVE · argent réel'
          : mode === 'TESTNET'
            ? 'TESTNET · test'
            : mode || '—';
      badge.dataset.mode = mode;
      badge.title = mode === 'LIVE'
        ? 'Les ordres partent sur un compte réel.'
        : 'Aucun ordre réel envoyé.';
    }
    setText('mode', mode || '—');
    setText(
      'equity-sub',
      mode === 'PAPER'
        ? 'EUR · équité virtuelle du mode papier'
        : mode === 'LIVE'
          ? 'EUR · équité réelle du compte'
          : 'EUR · équité du compte',
    );
    setText(
      'footer-note',
      mode === 'PAPER'
        ? 'aucun ordre réel'
        : mode === 'LIVE'
          ? 'ARGENT RÉEL'
          : 'environnement de test',
    );
  }

  function markStatus() {
    lastStatusAt = Date.now();
    setText('last-update', `maj ${timeOnly(new Date(lastStatusAt))}`);
    setText('updated-footer', timeOnly(new Date(lastStatusAt)));
    const element = $('last-update');
    if (element) element.classList.remove('stale');
  }

  function render(status) {
    if (!status || typeof status !== 'object') return;
    latestStatus = status;

    const badge = $('state');
    if (badge) {
      badge.textContent = stateLabel(status.state);
      badge.dataset.state = status.state ?? '';
      badge.title = status.state ?? '';
    }

    setText('equity', money(status.equity));
    setText('free', money(status.free_quote));
    setText('reserve', money(status.reserve_quote));
    setText('active-capital', money(status.active_equity ?? status.free_quote));
    setText('growth-pot', money(status.growth_quote ?? '0'));
    if (status.capital_steps?.next_review_at) {
      setText('growth-step', `${status.capital_steps.promotions} palier(s) · ajout max. ${money(status.capital_steps.step_quote)} EUR, au plus tous les ${status.capital_steps.interval_days} jours`);
    }
    const drawdown = toNumber(status.drawdowns?.from_hwm);
    setText('drawdown', percent(drawdown));
    const drawdownElement = $('drawdown');
    if (drawdownElement) {
      // Un drawdown positif est une perte : il doit être rouge, pas vert.
      if (drawdown && drawdown > 0) drawdownElement.dataset.sign = 'down';
      else delete drawdownElement.dataset.sign;
    }
    renderMode(status);
    renderHalt(status);
    if (resetButton) resetButton.hidden = status.state !== 'LOCKED_CIRCUIT_BREAKER';
    renderPosition(status.position);
    renderRisk(status, latestSettings);
    markStatus();
  }

  async function loadHealth() {
    try {
      const response = await fetch('/api/system/health', { headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const data = await response.json();
      const kraken = data.kraken || {};
      const ollama = data.ollama || {};
      const db = data.supabase || {};
      const krakenBadge = $('health-kraken');
      if (krakenBadge) {
        krakenBadge.textContent = `Kraken ${kraken.status || '—'}`;
        krakenBadge.dataset.health = kraken.status === 'CONNECTED' ? 'ok' : 'danger';
      }
      const ollamaBadge = $('health-ollama');
      if (ollamaBadge) {
        ollamaBadge.textContent = `Ollama ${ollama.status || '—'}`;
        ollamaBadge.dataset.health = ollama.status === 'UP' ? 'ok' : 'danger';
      }
      const dbBadge = $('health-db');
      if (dbBadge) {
        dbBadge.textContent = `DB ${db.quota_alert_triggered ? 'alerte' : 'ok'}`;
        dbBadge.dataset.health = db.quota_alert_triggered ? 'danger' : 'ok';
      }
    } catch {
      /* télémétrie best-effort */
    }
  }

  async function loadStatus() {
    try {
      const response = await fetch('/api/status', { headers: { Accept: 'application/json' } });
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (!response.ok) return;
      render(await response.json());
    } catch {
      /* le flux SSE reste la source principale */
    }
  }

  // --------------------------------------------------------------- control --

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
      await loadStatus();
      await loadStats();
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
  $('halt-resume')?.addEventListener('click', () => control('resume'));
  const resetButton = $('reset-cb');
  resetButton?.addEventListener('click', async () => {
    const code = window.prompt('Code Authy à 6 chiffres') || '';
    if (!/^\d{6}$/.test(code.trim())) {
      setFeedback('code TOTP à 6 chiffres requis', 'error');
      return;
    }
    await control('circuit-breaker/reset', { totp: code.trim() });
  });

  const killDialog = $('kill-dialog');
  $('kill-switch')?.addEventListener('click', () => {
    const code = $('kill-code');
    if (code) code.value = '';
    killDialog?.showModal();
  });
  $('kill-cancel')?.addEventListener('click', () => killDialog?.close());
  $('kill-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const code = $('kill-code')?.value.trim() ?? '';
    if (!/^\d{6}$/.test(code)) {
      setFeedback('code TOTP à 6 chiffres requis', 'error');
      return;
    }
    killDialog?.close();
    await control('kill-switch', { totp: code });
  });

  $('reconcile')?.addEventListener('click', async () => {
    setFeedback('réconciliation…');
    try {
      const response = await fetch('/api/control/reconcile', { method: 'POST' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setFeedback(data.detail ?? data.error ?? 'réconciliation refusée', 'error');
        return;
      }
      const dialog = $('reconcile-dialog');
      const pre = $('reconcile-diff');
      if (pre) pre.textContent = JSON.stringify(data, null, 2);
      dialog?.showModal();
      setFeedback('réconciliation terminée', 'ok');
    } catch (error) {
      setFeedback(`réseau : ${error.message}`, 'error');
    }
  });
  $('reconcile-close')?.addEventListener('click', () => $('reconcile-dialog')?.close());


  const dialog = $('panic-dialog');
  $('panic')?.addEventListener('click', () => {
    const code = $('panic-code');
    if (code) code.value = '';
    dialog?.showModal();
  });
  $('panic-cancel')?.addEventListener('click', () => dialog?.close());
  $('panic-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const keyword = $('panic-keyword')?.value.trim() ?? '';
    const code = $('panic-code')?.value.trim() ?? '';
    if (keyword !== 'PANIC') {
      setFeedback('saisir PANIC en majuscules', 'error');
      return;
    }
    if (!/^\d{6}$/.test(code)) {
      setFeedback('code TOTP à 6 chiffres requis', 'error');
      return;
    }
    dialog?.close();
    await control('panic', { totp: code });
  });

  // -------------------------------------------------------------- réglages --

  let settingsProfiles = [];

  function optionValue(id) {
    const element = $(id);
    return element ? element.value : '';
  }

  function setRange(id, value) {
    const element = $(id);
    if (element && Number.isFinite(Number(value))) element.value = String(value);
  }

  function refreshSettingOutputs() {
    setText('set-notional-value', `${optionValue('set-notional')} %`);
    setText('set-daily-value', `${optionValue('set-daily')} %`);
    setText('set-orders-value', `${optionValue('set-orders')} / h`);
    setText('set-confidence-value', `${optionValue('set-confidence')} %`);
    setText('set-halt-value', `${optionValue('set-halt')} h`);
  }

  function fillProfileOptions(profiles, selected) {
    const select = $('set-profile');
    if (!select) return;
    select.replaceChildren();
    for (const profile of profiles || []) {
      const option = document.createElement('option');
      option.value = profile.key;
      option.textContent = profile.label;
      if (profile.key === selected) option.selected = true;
      select.append(option);
    }
  }

  function applyProfileValues(profile) {
    if (!profile) return;
    setRange('set-notional', Math.round(Number(profile.max_per_trade_pct) * 100));
    setRange('set-daily', Math.round(Number(profile.max_daily_loss_pct) * 100));
    setRange('set-orders', Number(profile.max_orders_per_hour));
    setRange('set-confidence', Math.round(Number(profile.confidence_floor) * 100));
    refreshSettingOutputs();
  }

  function renderSettings(settings) {
    latestSettings = settings;
    settingsProfiles = settings.profiles || [];
    fillProfileOptions(settingsProfiles, settings.profile);
    setRange('set-notional', Math.round(Number(settings.max_per_trade_pct) * 100));
    setRange('set-daily', Math.round(Number(settings.max_daily_loss_pct) * 100));
    setRange('set-orders', Number(settings.max_orders_per_hour));
    setRange('set-confidence', Math.round(Number(settings.confidence_floor) * 100));
    setRange('set-halt', Number(settings.halt_auto_resume_hours));
    const entryRule = $('set-entry-rule');
    if (entryRule) entryRule.checked = Boolean(settings.entry_rule_enabled);
    refreshSettingOutputs();
    const definition = settingsProfiles.find((profile) => profile.key === settings.profile);
    const applied = settings.application;
    if (applied) {
      setText('settings-updated', `Demandés : ${applied.revision} · appliqués : ${applied.applied_revision ?? 'aucune preuve'}${applied.apply_error ? ` · ${applied.apply_error}` : ''}`);
    }
    setText('set-profile-help', definition?.description || '');
    setText(
      'settings-updated',
      settings.application
        ? `Demandés : ${settings.application.revision} · appliqués : ${settings.application.applied_revision ?? 'aucune preuve'}${settings.application.apply_error ? ` · ${settings.application.apply_error}` : ''}`
        : settings.updated_at
        ? `modifié le ${new Date(settings.updated_at).toLocaleString('fr-FR')}`
        : 'réglages par défaut',
    );
    setText(
      'settings-limits',
      'Lecture seule ici : '
        + `semaine ${percent(settings.max_weekly_loss_pct)} · `
        + `depuis le plus haut ${percent(settings.max_drawdown_from_hwm_pct)} · `
        + `${settings.max_consecutive_losses ?? '—'} pertes consécutives · `
        + `risque par achat ${percent(settings.risk_per_trade_pct)}.`,
    );
    renderRisk(latestStatus, settings);
  }

  async function loadSettings() {
    try {
      const response = await fetch('/api/settings', { headers: { Accept: 'application/json' } });
      if (response.status === 401) {
        window.location.href = '/login';
        return false;
      }
      if (!response.ok) {
        setSettingsFeedback('réglages indisponibles', 'error');
        return false;
      }
      renderSettings(await response.json());
      return true;
    } catch (error) {
      setSettingsFeedback(`réseau : ${error.message}`, 'error');
      return false;
    }
  }

  $('set-profile')?.addEventListener('change', () => {
    const profile = settingsProfiles.find((item) => item.key === optionValue('set-profile'));
    applyProfileValues(profile);
    setText('set-profile-help', profile?.description || '');
  });

  for (const id of ['set-notional', 'set-daily', 'set-orders', 'set-confidence', 'set-halt']) {
    $(id)?.addEventListener('input', refreshSettingOutputs);
  }

  $('settings-reload')?.addEventListener('click', async () => {
    setSettingsFeedback('chargement…');
    const ok = await loadSettings();
    if (ok) setSettingsFeedback('réglages rechargés', 'ok');
  });

  $('settings-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const save = $('settings-save');
    const payload = {
      profile: optionValue('set-profile'),
      max_per_trade_pct: Number(optionValue('set-notional')) / 100,
      max_daily_loss_pct: Number(optionValue('set-daily')) / 100,
      max_orders_per_hour: Number(optionValue('set-orders')),
      confidence_floor: Number(optionValue('set-confidence')) / 100,
      entry_rule_enabled: Boolean($('set-entry-rule')?.checked),
      halt_auto_resume_hours: Number(optionValue('set-halt')),
    };
    if (save) save.disabled = true;
    setSettingsFeedback('enregistrement…');
    try {
      if (latestSettings?.application) {
        payload.expected_revision = latestSettings.application.revision;
        const preview = await fetch('/api/settings/preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ update: Object.fromEntries(Object.entries(payload).filter(([key]) => key !== 'expected_revision')), expected_revision: payload.expected_revision }),
        });
        const impact = await preview.json();
        if (!preview.ok) throw new Error(typeof impact.detail === 'string' ? impact.detail : 'Prévisualisation refusée');
        const safety = Object.values(impact.changes || {}).filter((item) => item.scope === 'GLOBAL_SAFETY').length;
        if (!window.confirm(`${Object.keys(impact.changes || {}).length} réglages changent, dont ${safety} limites globales. Les règles stratégiques des positions déjà versionnées restent conservées. Enregistrer ?`)) return;
      }
      const response = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setSettingsFeedback(data.detail ?? data.error ?? 'réglage refusé', 'error');
        return;
      }
      renderSettings(data);
      setSettingsFeedback('enregistré — appliqué au prochain cycle', 'ok');
      await loadStats();
    } catch (error) {
      setSettingsFeedback(`réseau : ${error.message}`, 'error');
    } finally {
      if (save) save.disabled = false;
    }
  });

  // -------------------------------------------------------------- activité --

  const ACTIVITY = {
    llm_signals: {
      columns: [
        { key: 'created_at', label: 'Heure', format: dateTime, className: 'cell-time' },
        { key: 'symbol', label: 'Paire', className: 'cell-mono' },
        {
          key: 'action',
          label: 'Action',
          format: actionLabel,
          className: 'cell-action',
          decorate: (cell, row) => { cell.dataset.action = String(row.action || '').toLowerCase(); },
        },
        { key: 'confidence', label: 'Conf.', format: percent, className: 'cell-num' },
        { key: 'market_regime', label: 'Régime', format: regimeLabel, className: 'cell-muted' },
        { key: 'thesis', label: 'Thèse', className: 'cell-thesis', title: true },
        { key: 'prompt', label: 'Prompt', className: 'cell-muted' },
      ],
    },
    risk_evaluations: {
      columns: [
        { key: 'created_at', label: 'Heure', format: dateTime, className: 'cell-time' },
        { key: 'symbol', label: 'Paire', className: 'cell-mono' },
        {
          key: 'approved',
          label: 'Verdict',
          format: (value) => (value ? 'Accepté' : 'Refusé'),
          className: 'cell-verdict',
          decorate: (cell, row) => { cell.dataset.verdict = row.approved ? 'ok' : 'danger'; },
        },
        {
          key: 'rejection_code',
          label: 'Motif',
          format: rejectionLabel,
          className: 'cell-muted',
          titleFrom: 'rejection_code',
        },
        { key: 'market_price', label: 'Prix', format: money, className: 'cell-num' },
        {
          key: 'effective_risk_quote',
          label: 'Risque',
          format: (value) => (value === null || value === undefined ? '—' : `${money(value)} EUR`),
          className: 'cell-num',
        },
      ],
    },
    orders: {
      columns: [
        { key: 'created_at', label: 'Heure', format: dateTime, className: 'cell-time' },
        { key: 'symbol', label: 'Paire', className: 'cell-mono' },
        {
          key: 'side',
          label: 'Sens',
          format: sideLabel,
          className: 'cell-action',
          decorate: (cell, row) => { cell.dataset.action = String(row.side || '').toLowerCase(); },
        },
        { key: 'purpose', label: 'Rôle', format: purposeLabel, className: 'cell-muted' },
        { key: 'status', label: 'Statut', format: orderStatusLabel, className: 'cell-muted' },
        { key: 'requested_amount', label: 'Quantité', format: amount, className: 'cell-num' },
        { key: 'average_fill_price', label: 'Prix moyen', format: money, className: 'cell-num' },
        { key: 'fee_amount', label: 'Frais', format: money, className: 'cell-num' },
      ],
    },
    positions: {
      columns: [
        { key: 'opened_at', label: 'Ouverte', format: dateTime, className: 'cell-time' },
        { key: 'symbol', label: 'Paire', className: 'cell-mono' },
        { key: 'status', label: 'Statut', format: positionStatusLabel, className: 'cell-muted' },
        { key: 'entry_price', label: 'Entrée', format: money, className: 'cell-num' },
        { key: 'initial_amount', label: 'Quantité', format: amount, className: 'cell-num' },
        { key: 'net_pnl_quote', label: 'Net', format: money, className: 'cell-num' },
        { key: 'exit_reason', label: 'Sortie', className: 'cell-muted', title: true },
      ],
    },
  };

  let activityTable = 'llm_signals';
  let activityRequest = 0;

  function renderActivityHead() {
    const head = $('activity-head');
    const config = ACTIVITY[activityTable];
    if (!head || !config) return;
    const row = document.createElement('tr');
    for (const column of config.columns) {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = column.label;
      row.append(th);
    }
    head.replaceChildren(row);
  }

  function renderActivityError(message) {
    const body = $('activity-body');
    const config = ACTIVITY[activityTable];
    if (!body || !config) return;
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = config.columns.length;
    cell.className = 'muted';
    cell.textContent = message;
    row.append(cell);
    body.replaceChildren(row);
  }

  function renderActivity(rows) {
    const body = $('activity-body');
    const config = ACTIVITY[activityTable];
    if (!body || !config) return;

    if (!rows || rows.length === 0) {
      renderActivityError('Aucune ligne enregistrée pour cette vue.');
      setText('activity-meta', '0 ligne · à jour');
      return;
    }

    body.replaceChildren();
    for (const data of rows) {
      const row = document.createElement('tr');
      for (const column of config.columns) {
        const cell = document.createElement('td');
        if (column.key === 'prompt') {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'ghost';
          button.textContent = 'Voir';
          button.addEventListener('click', () => inspectPrompt(data.id));
          cell.append(button);
        } else {
          let value = data[column.key];
          if (column.format) value = column.format(value, data);
          cell.textContent = value === null || value === undefined ? '—' : String(value);
        }
        if (column.className) cell.className = column.className;
        if (column.title && data[column.key]) cell.title = String(data[column.key]);
        if (column.titleFrom && data[column.titleFrom]) cell.title = String(data[column.titleFrom]);
        column.decorate?.(cell, data);
        row.append(cell);
      }
      body.append(row);
    }
    setText('activity-meta', `${rows.length} ligne(s) · maj ${timeOnly(new Date())}`);
  }

  async function inspectPrompt(signalId) {
    if (!signalId) return;
    try {
      const response = await fetch(`/api/history/llm_signals/${encodeURIComponent(signalId)}/prompt`, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        setFeedback('prompt indisponible', 'error');
        return;
      }
      const data = await response.json();
      const dialog = $('prompt-dialog');
      const pre = $('prompt-content');
      if (pre) pre.textContent = JSON.stringify(data.prompt_payload || {}, null, 2);
      dialog?.showModal();
    } catch (error) {
      setFeedback(`réseau : ${error.message}`, 'error');
    }
  }

  async function loadActivity() {
    const config = ACTIVITY[activityTable];
    if (!config) return;
    const request = ++activityRequest;
    const limit = Number(optionValue('activity-limit')) || 25;
    setText('activity-meta', 'chargement…');

    let data;
    try {
      const response = await fetch(
        `/api/history/${encodeURIComponent(activityTable)}?limit=${limit}`,
        { headers: { Accept: 'application/json' } },
      );
      if (request !== activityRequest) return;
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (!response.ok) {
        renderActivityError('Historique indisponible.');
        setText('activity-meta', 'indisponible');
        return;
      }
      data = await response.json();
    } catch (error) {
      if (request !== activityRequest) return;
      renderActivityError(`Réseau : ${error.message}`);
      setText('activity-meta', 'hors ligne');
      return;
    }

    if (request !== activityRequest) return;
    try {
      renderActivity(Array.isArray(data.rows) ? data.rows : []);
    } catch (error) {
      renderActivityError(`Affichage : ${error.message}`);
      setText('activity-meta', 'erreur d’affichage');
    }
  }

  function selectActivityTable(table) {
    if (!ACTIVITY[table]) return;
    activityTable = table;
    for (const tab of document.querySelectorAll('[role="tab"][data-table]')) {
      const active = tab.dataset.table === table;
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    }
    $('activity-panel')?.setAttribute('aria-labelledby', `tab-${table}`);
    renderActivityHead();
    renderActivityError('Chargement…');
    loadActivity();
  }

  const activityTabs = [...document.querySelectorAll('[role="tab"][data-table]')];
  activityTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectActivityTable(tab.dataset.table));
    tab.addEventListener('keydown', (event) => {
      let nextIndex = null;
      if (event.key === 'ArrowLeft') nextIndex = (index - 1 + activityTabs.length) % activityTabs.length;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % activityTabs.length;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = activityTabs.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      const next = activityTabs[nextIndex];
      next.focus();
      selectActivityTable(next.dataset.table);
    });
  });

  $('activity-limit')?.addEventListener('change', loadActivity);
  $('activity-refresh')?.addEventListener('click', loadActivity);

  // ---------------------------------------------------------------- stream --

  function setConnection(label, online) {
    const element = $('conn');
    if (!element) return;
    element.textContent = label;
    element.dataset.online = online ? 'true' : 'false';
  }

  const stream = new EventSource('/events');
  stream.onopen = () => setConnection('en ligne', true);
  stream.onerror = () => setConnection('hors ligne', false);
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

  // ------------------------------------------------------------ démarrage --

  for (const button of document.querySelectorAll('[data-range]')) {
    button.addEventListener('click', () => {
      currentRange = button.dataset.range || 'all';
      $('prompt-close')?.addEventListener('click', () => $('prompt-dialog')?.close());
  updateRangeButtons();
      // Le dernier jeu de points reste en mémoire : pas besoin de rappeler l'API.
      renderEquityChart(latestEquityPoints);
    });
  }

  updateRangeButtons();
  renderActivityHead();
  loadStatus();
  loadStats();
  loadHealth();
  loadSettings();
  loadActivity();
  setInterval(loadStats, 15_000);
  setInterval(loadHealth, 30_000);
  setInterval(loadActivity, 20_000);
  setInterval(updateCountdown, 1_000);
  setInterval(() => {
    if (!lastStatusAt) return;
    const age = Math.max(0, Math.round((Date.now() - lastStatusAt) / 1000));
    const element = $('last-update');
    if (!element) return;
    element.textContent = age <= 2
      ? `maj ${timeOnly(new Date(lastStatusAt))}`
      : `maj il y a ${age} s`;
    element.classList.toggle('stale', age > 10);
  }, 1_000);
})();
