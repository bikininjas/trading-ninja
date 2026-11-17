/* Veille privée en lecture seule : les titres et la réponse restent liés au digest. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  if (!$('view-intel')) return;
  const labels = { BULLISH: 'Haussier', NEUTRAL: 'Neutre', BEARISH: 'Baissier', CRISIS: 'Risque critique' };
  const actions = { BUY: 'Acheter', SELL: 'Clôturer', HOLD: 'Attendre', HALT: 'Suspendre' };
  const reasons = { SOURCES_UNAVAILABLE: 'Collecte incomplète', MODEL_UNAVAILABLE: 'Modèle indisponible ou délai dépassé', INVALID_RESPONSE: 'Réponse du modèle invalide', UNKNOWN_ASSET: 'Actif hors périmètre dans la réponse' };
  const date = (value) => Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'Date inconnue';
  const text = (id, value) => { $(id).textContent = value; };
  const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const digest = (value) => value === null || (object(value)
    && typeof value.id === 'string' && ['OK', 'DEGRADED'].includes(value.status)
    && Number.isFinite(Date.parse(value.observed_at)) && Number.isFinite(Date.parse(value.valid_until))
    && Object.hasOwn(labels, value.overall_sentiment) && typeof value.systemic_risk_detected === 'boolean'
    && typeof value.catalyst_summary === 'string' && typeof value.model === 'string'
    && Array.isArray(value.affected_assets) && value.affected_assets.every((item) => typeof item === 'string')
    && Array.isArray(value.source_items) && value.source_items.every((item) => object(item)
      && typeof item.title === 'string' && typeof item.description === 'string' && typeof item.source === 'string'
      && Number.isFinite(Date.parse(item.published_at))));
  const parsed = (value) => { if (typeof value !== 'string') return value; try { return JSON.parse(value); } catch { return {}; } };
  let data = null, news = [], version = 0, controller, expiry;
  let clockOffset = 0, readFailed = false;
  let selectedId = new URLSearchParams(location.search).get('digest') || '';
  if (!/^[0-9a-f-]{36}$/i.test(selectedId)) selectedId = '';
  function node(tag, content, className) {
    const item = document.createElement(tag); item.textContent = content;
    if (className) item.className = className;
    return item;
  }
  function safeUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && ['coindesk.com', 'www.coindesk.com', 'cointelegraph.com', 'www.cointelegraph.com'].includes(url.hostname)
        && !url.username && !url.password && (!url.port || url.port === '443') ? url.href : null;
    } catch { return null; }
  }
  function publisher(item) {
    try { return new URL(item.source).hostname.includes('coindesk.com') ? 'CoinDesk' : new URL(item.source).hostname.includes('cointelegraph.com') ? 'Cointelegraph' : 'Source inconnue'; } catch { return 'Source inconnue'; }
  }
  function valid(row) {
    const now = Date.now() + clockOffset;
    return row?.status === 'OK' && Date.parse(row.observed_at) <= now && now < Date.parse(row.valid_until);
  }
  function updateFreshness() {
    clearTimeout(expiry);
    const row = data?.selected, active = data?.active;
    const current = !readFailed && valid(active);
    text('intel-freshness', readFailed ? 'Lecture interrompue' : !row ? 'Aucune analyse' : row.status !== 'OK' ? 'Dégradée' : valid(row) ? 'Encore valide' : 'Archivée · expirée');
    $('intel-freshness').dataset.health = readFailed ? '' : valid(row) ? 'ok' : '';
    text('intel-veto-title', readFailed ? 'État du veto non confirmé' : !current ? 'Aucune analyse actuellement valide' : active.systemic_risk_detected ? 'Veto systémique actif' : 'Aucun veto systémique actif');
    text('intel-veto-description', readFailed ? 'Dernières données conservées. Actualisez pour vérifier l’état courant.' : !current ? 'Cette page ne confirme aucun contexte public utilisable par le moteur.' : active.systemic_risk_detected ? `Achats bloqués pour ${active.affected_assets?.length && !active.affected_assets.includes('ALL') ? active.affected_assets.join(', ') : 'tous les actifs'}. Le filtre technique reste prioritaire.` : 'Le filtre technique et les autres limites de risque restent applicables.');
    $('intel-veto').dataset.tone = !current ? 'unknown' : active.systemic_risk_detected ? 'danger' : 'neutral';
    if (row && active && row.id !== active.id) $('intel-veto-description').append(document.createTextNode(' Vous consultez une autre analyse ; cet état porte sur la dernière analyse valide.'));
    const dates = [row, active].filter(valid).map((item) => Date.parse(item.valid_until));
    if (dates.length) expiry = setTimeout(updateFreshness, Math.max(1, Math.min(...dates) - Date.now() - clockOffset + 10));
  }
  function renderNews() {
    const query = $('intel-search').value.trim().toLocaleLowerCase('fr');
    const source = $('intel-source').value;
    const visible = news.filter((item) => (!source || publisher(item) === source) && `${item.title} ${item.description}`.toLocaleLowerCase('fr').includes(query));
    const box = $('intel-news-list'); box.replaceChildren();
    text('intel-news-count', `${visible.length} / ${news.length} titre${news.length > 1 ? 's' : ''}`);
    if (!visible.length) {
      box.append(node('p', news.length ? 'Aucun titre ne correspond à ces filtres.' : data?.selected?.source_hashes?.length ? 'Les titres de cette analyse ancienne n’ont pas été conservés. Choisissez une analyse plus récente.' : 'Aucun titre disponible pour cette collecte.', 'empty muted'));
    }
    for (const item of visible) {
      const article = document.createElement('article'); article.className = 'intel-article';
      const meta = node('p', `${publisher(item)} · ${date(item.published_at)}`, 'intel-article-meta');
      const title = document.createElement('h3'); const url = safeUrl(item.url);
      if (url) { const link = node('a', item.title); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.append(node('span', ' ↗', 'muted')); title.append(link); }
      else title.textContent = item.title;
      article.append(meta, title, node('p', item.description || 'Description non fournie par la source.', 'intel-article-description'));
      box.append(article);
    }
  }
  function renderDigest(value) {
    data = value; clockOffset = Date.parse(value.as_of) - Date.now(); readFailed = false;
    const row = value.selected;
    const latest = value.latest_attempt;
    text('intel-updated', `Dernière lecture : ${date(value.as_of)} · collecte horaire`);
    text('intel-attempt', latest?.status === 'DEGRADED' ? `Dernière tentative du ${date(latest.observed_at)} dégradée : ${reasons[latest.analysis_error] || 'analyse indisponible'}. Une analyse antérieure peut rester valide.` : 'Titres publics et réponse du modèle conservés ensemble.');
    text('intel-sentiment', row?.status === 'OK' ? labels[row.overall_sentiment] || 'Inconnu' : 'Analyse indisponible');
    $('intel-analysis-title').closest('.intel-analysis').dataset.sentiment = row?.status === 'OK' ? row.overall_sentiment : '';
    text('intel-summary', row?.catalyst_summary || 'Aucune analyse n’a encore été enregistrée.');
    const score = row?.sentiment_score;
    text('intel-score', row?.status === 'OK' && score !== null && score !== undefined && Number.isFinite(Number(score)) ? `${new Intl.NumberFormat('fr-FR', { signDisplay: 'exceptZero', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(score))} / −1 à +1` : '—');
    text('intel-assets', row?.affected_assets?.length ? row.affected_assets.map((asset) => asset === 'ALL' ? 'Marché global' : asset).join(', ') : row?.systemic_risk_detected ? 'Marché global' : 'Aucun actif cité');
    text('intel-model', row?.model || '—'); text('intel-observed', date(row?.observed_at)); text('intel-valid-until', date(row?.valid_until));
    text('intel-fng-value', row?.fear_and_greed_value === null || row?.fear_and_greed_value === undefined ? '—' : `${row.fear_and_greed_value} / 100`);
    text('intel-fng-date', row?.fear_and_greed_at ? `Indice daté du ${date(row.fear_and_greed_at)}` : 'Indice non conservé pour cette analyse');
    text('intel-response-note', row?.analysis_response ? 'Réponse finale renvoyée par le modèle, conservée sans reformulation.' : row?.status === 'OK' ? 'Seule la synthèse structurée a été conservée pour cette ancienne analyse.' : 'Le repli neutre vient du code ; aucune réponse valide du modèle n’est disponible.');
    const digestFields = row && Object.fromEntries(['overall_sentiment', 'sentiment_score', 'systemic_risk_detected', 'affected_assets', 'catalyst_summary'].map((key) => [key, row[key]]));
    const response = row?.analysis_response;
    let formatted = response;
    if (response) { try { formatted = JSON.stringify(JSON.parse(response), null, 2); } catch { /* Texte source affiché comme texte. */ } }
    text('intel-response', formatted || (row?.status === 'OK' ? JSON.stringify(digestFields, null, 2) : 'Aucune réponse valide enregistrée.'));
    const menu = $('intel-history'); menu.replaceChildren();
    const history = [...value.history];
    if (row && !history.some((item) => item.id === row.id)) history.unshift(row);
    if (!history.length) menu.append(new Option('Aucune analyse enregistrée', ''));
    for (const item of history) menu.append(new Option(`${date(item.observed_at)} · ${item.status === 'OK' ? labels[item.overall_sentiment] || 'Analyse' : 'Dégradée'}`, item.id));
    if (row) menu.value = row.id;
    news = row?.source_items || []; renderNews(); updateFreshness();
  }
  async function api(path, signal) {
    const response = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store', signal });
    if (response.status === 401) { location.href = '/login'; throw new Error('Session expirée'); }
    if (!response.ok) throw new Error(response.status === 404 ? 'Analyse introuvable. Revenez à la dernière analyse.' : 'Lecture indisponible. Actualisez ou vérifiez le service.');
    return response.json();
  }
  function renderSignals(rows) {
    const box = $('intel-signals-list'); box.replaceChildren();
    if (!rows.length) box.append(node('p', 'Aucune prise de parole enregistrée. Le moteur peut rester sous son filtre technique sans consulter le LLM.', 'empty muted'));
    for (const signal of rows.slice(0, 10)) {
      const card = document.createElement('article'); card.className = 'intel-signal';
      card.append(node('p', `${signal.symbol} · ${date(signal.created_at)} · ${signal.model_name || 'Modèle non précisé'}`, 'intel-article-meta'));
      card.append(node('h3', `Action retenue : ${actions[signal.action] || signal.action || 'inconnue'} · ${Number.isFinite(Number(signal.confidence)) && signal.confidence !== null ? new Intl.NumberFormat('fr-FR', { style: 'percent', maximumFractionDigits: 0 }).format(Number(signal.confidence)) : 'score inconnu'}`));
      card.append(node('p', signal.thesis || 'Aucune thèse enregistrée.', 'intel-signal-thesis'));
      const raw = parsed(signal.raw_response) || {};
      if (raw.error) card.append(node('p', `Repli du code : ${String(raw.error)}`, 'data-warning'));
      const details = document.createElement('details'); details.className = 'intel-response';
      details.append(node('summary', 'Réponse finale et contexte transmis'));
      const final = typeof raw.content === 'string' ? raw.content : null;
      details.append(node('h4', 'Réponse finale du modèle'));
      details.append(node('pre', final || 'Aucune réponse finale conservée pour ce signal.'));
      details.append(node('h4', 'Contexte transmis au modèle'));
      details.append(node('pre', JSON.stringify(parsed(signal.prompt_payload) || {}, null, 2)));
      card.append(details); box.append(card);
    }
  }
  async function refresh() {
    const request = ++version;
    controller?.abort(); controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]);
    $('intel-refresh').disabled = true;
    const query = selectedId ? `?digest_id=${encodeURIComponent(selectedId)}` : '';
    const results = await Promise.allSettled([api(`/api/insights/market-intel${query}`, signal),
      !$('view-intel').hidden ? api('/api/history/llm_signals?limit=10', signal) : Promise.resolve(null)]);
    if (request !== version) return;
    try {
      if (results[0].status === 'rejected') throw results[0].reason;
      const value = results[0].value;
      if (!object(value) || !Number.isFinite(Date.parse(value.as_of)) || !Array.isArray(value.history)
        || !Object.hasOwn(value, 'selected') || !Object.hasOwn(value, 'active')
        || !digest(value.selected) || !digest(value.active)) throw new Error('Réponse de veille incomplète. Dernières données conservées.');
      renderDigest(value); $('intel-error').hidden = true;
    } catch (error) {
      readFailed = true; text('intel-error', error.message); $('intel-error').hidden = false; updateFreshness();
    }
    if (results[1].status === 'fulfilled' && results[1].value !== null && Array.isArray(results[1].value.rows)) {
      renderSignals(results[1].value.rows); $('intel-signals-error').hidden = true;
    } else if (results[1].status === 'rejected') {
      text('intel-signals-error', 'Réponses de trading indisponibles. Les dernières réponses restent affichées.'); $('intel-signals-error').hidden = false;
    }
    $('intel-refresh').disabled = false;
  }
  $('intel-refresh').addEventListener('click', refresh);
  $('intel-latest').addEventListener('click', () => {
    selectedId = ''; const url = new URL(location.href); url.searchParams.delete('digest'); history.replaceState(null, '', url); refresh();
  });
  $('refresh-all')?.addEventListener('click', () => { if (!$('view-intel').hidden) refresh(); });
  $('intel-search').addEventListener('input', renderNews); $('intel-source').addEventListener('change', renderNews);
  $('intel-history').addEventListener('change', () => {
    selectedId = $('intel-history').value;
    const url = new URL(location.href); if (selectedId) url.searchParams.set('digest', selectedId); else url.searchParams.delete('digest');
    history.replaceState(null, '', url); refresh();
  });
  window.addEventListener('hashchange', () => { if (!$('view-intel').hidden) refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('view-intel').hidden) refresh(); });
  setInterval(() => { if (!document.hidden && !$('view-intel').hidden) refresh(); }, 60000);
  if (!$('view-intel').hidden) refresh();
})();
