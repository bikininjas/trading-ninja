/* Observations privées. Les contenus reçus sont rendus en textContent, jamais en HTML. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const localDateTime = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(undefined, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  };
  const labels = { NO_SETUP: 'Pas de configuration d’achat', LLM_HOLD: 'Attente du modèle',
    LLM_UNAVAILABLE: 'Modèle indisponible', RISK_REJECTED: 'Refus du pare-feu', NOT_SELECTED: 'Autre paire retenue',
    FILLED: 'Achat exécuté', ENTRY_DISABLED: 'Achats suspendus', DATA_UNAVAILABLE: 'Données indisponibles',
    ALREADY_EVALUATED: 'Bougie déjà évaluée', REVALIDATION_REJECTED: 'Refus après actualisation' };
  const rejectionLabels = { SIZING_BELOW_EXCHANGE_MINIMUM: 'Quantité finançable inférieure au minimum', EXCHANGE_MIN_NOTIONAL_FAILED: 'Montant inférieur au minimum de la paire', INSUFFICIENT_FREE_BALANCE: 'Disponible insuffisant', RISK_LIMIT_EXCEEDED: 'Budget de risque dépassé', CONFIDENCE_TOO_LOW: 'Score sous le seuil configuré', REJECTED_COUNTER_TREND_EMA200: 'Tendance longue défavorable' };
  const stages = { OBSERVED: 'Marché observé', SIGNAL: 'Réponse du modèle', APPROVED: 'Pare-feu : achat autorisé', RANKING: 'Comparaison des paires', LLM_HALT: 'Arrêt demandé par le modèle', SELL_WITHOUT_POSITION: 'Vente sans position', REVALIDATION_REJECTED: 'Refus après actualisation', SUBMITTED: 'Envoi préparé', FILLED: 'Achat exécuté' };
  let control = null;
  let busy = false;
  let refreshing = false;
  let lastProtection = null;
  let protectionExpiry = null;
  let cyclesSignature = null;
  const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const count = (value) => Number.isSafeInteger(value) && value >= 0;
  function validOverview(data) {
    if (!record(data) || !record(data.entries) || typeof data.entries.enabled !== 'boolean'
      || !count(data.entries.revision) || !record(data.opportunities)
      || !['unique_opportunities', 'terminal_attempts'].every((key) => count(data.opportunities[key]))
      || typeof data.opportunities.truncated !== 'boolean'
      || !record(data.opportunities.terminal_reasons)
      || !Object.values(data.opportunities.terminal_reasons).every(count)
      || Object.values(data.opportunities.terminal_reasons).reduce((sum, n) => sum + n, 0) !== data.opportunities.terminal_attempts
      || !Array.isArray(data.cycles) || !data.cycles.every((cycle) => record(cycle)
        && typeof cycle.id === 'string' && typeof cycle.status === 'string' && Number.isFinite(Date.parse(cycle.started_at)))
      || !Array.isArray(data.experiments) || !record(data.settings) || !count(data.settings.revision)
      || !(data.settings.applied_revision === null || count(data.settings.applied_revision))
      || !record(data.protection) || !['OK', 'ALERT', 'UNKNOWN'].includes(data.protection.status)
      || !Array.isArray(data.protection.incidents)) return false;
    return data.opportunities.rejection_codes === undefined || (record(data.opportunities.rejection_codes)
      && Object.values(data.opportunities.rejection_codes).every(count));
  }
  function status(message) { if ($('insights-status')) $('insights-status').textContent = message; }
  async function api(path, body, timeout = 10_000) {
    const response = await fetch(path, body === undefined ? { signal: AbortSignal.timeout(timeout) } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (response.status === 401) { window.location.href = '/login'; throw new Error('Session expirée'); }
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail || result.error || 'Action refusée'));
    return result;
  }
  function clear(id) { const box = $(id); box?.replaceChildren(); return box; }
  function line(box, text) { const p = document.createElement('p'); p.textContent = text; box.append(p); return p; }
  function action(box, title, callback) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'ghost'; button.textContent = title;
    button.addEventListener('click', async () => { button.disabled = true; try { await callback(); } catch (error) { status(error.message); } finally { button.disabled = false; } });
    box.append(button);
  }
  function detail(box, title, value) {
    const d = document.createElement('details'); const s = document.createElement('summary'); s.textContent = title;
    const pre = document.createElement('pre'); pre.textContent = JSON.stringify(value, null, 2); pre.style.whiteSpace = 'pre-wrap';
    d.append(s, pre); box.append(d);
  }
  function download(name, value) {
    const href = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = href; a.download = name; a.click(); URL.revokeObjectURL(href);
  }
  function renderLatency(cycles) {
    const box = clear('cycle-latency-results');
    if (!cycles.length) { line(box, 'Aucun cycle enregistré.'); return; }
    const list = document.createElement('ol'); list.className = 'cycle-list';
    for (const cycle of cycles) {
      const item = document.createElement('li');
      const start = Date.parse(cycle.started_at);
      const end = cycle.finished_at ? Date.parse(cycle.finished_at) : NaN;
      const seconds = Number.isFinite(end) && end >= start ? (end - start) / 1000 : null;
      const value = document.createElement('strong');
      value.textContent = seconds === null ? 'Durée inconnue' : `${seconds.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`;
      item.dataset.over = String(seconds !== null && seconds > 30);
      const description = document.createElement('span');
      description.textContent = `${localDateTime(cycle.started_at)} · ${cycle.status}${seconds !== null && seconds > 30 ? ' · budget dépassé' : ''}`;
      item.append(value, description); list.append(item);
    }
    box.append(list);
  }
  async function loadFeasibility() {
    const box = $('feasibility-results');
    try {
      const data = await api('/api/history/risk_evaluations?limit=50');
      if (!record(data) || !Array.isArray(data.rows) || !data.rows.every((row) => record(row) && typeof row.symbol === 'string' && Number.isFinite(Date.parse(row.created_at)))) throw new Error('Réponse incomplète');
      box.replaceChildren();
      if (!data.rows.length) { line(box, 'Aucune évaluation enregistrée dans le lot récent.'); return; }
      const latest = new Map();
      for (const row of data.rows) if (!latest.has(row.symbol) || Date.parse(row.created_at) > Date.parse(latest.get(row.symbol).created_at)) latest.set(row.symbol, row);
      const grid = document.createElement('div'); grid.className = 'feasibility-grid';
      for (const row of latest.values()) {
        const card = document.createElement('article'); card.className = 'feasibility-card';
        const title = document.createElement('strong'); title.textContent = row.symbol; card.append(title);
        line(card, `${localDateTime(row.created_at)} · ${row.approved === true ? 'Autorisé' : row.approved === false ? 'Refusé' : 'Verdict inconnu'}`);
        if (row.approved === false) line(card, rejectionLabels[row.rejection_code] || row.rejection_code || 'Motif indisponible');
        if (row.rejection_details) line(card, row.rejection_details);
        line(card, `Quantité calculée : ${row.computed_amount ?? 'indisponible'} · prix : ${row.market_price ?? 'indisponible'}`);
        line(card, `Solde libre : ${row.free_quote_balance ?? 'indisponible'} · risque prévu : ${row.effective_risk_quote ?? 'indisponible'}`);
        grid.append(card);
      }
      box.append(grid);
      if (data.rows.length === 50) line(box, 'Lot limité à 50 évaluations : certaines paires peuvent être absentes.');
    } catch (error) { box.replaceChildren(); line(box, `Évaluations indisponibles : ${error.message}`); }
  }
  async function loadFeasibilitySymbols() {
    const select = $('feasibility-symbol');
    try {
      const data = await api('/api/insights/feasibility');
      if (!Array.isArray(data.symbols) || !data.symbols.every((s) => typeof s === 'string')) throw new Error('Univers indisponible');
      $('feasibility-universe').textContent = data.source === 'last_cycle'
        ? `Paires du dernier cycle capturé le ${localDateTime(data.observed_at)} ; la configuration du moteur peut avoir changé depuis.`
        : 'Paires de la configuration de l’API ; univers du moteur non confirmé.';
      select.replaceChildren();
      if (!data.symbols.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'Aucune paire configurée'; select.append(option); }
      for (const symbol of data.symbols) { const option = document.createElement('option'); option.value = symbol; option.textContent = symbol; select.append(option); }
    } catch (error) { select.replaceChildren(); $('feasibility-universe').textContent = 'Univers du moteur indisponible.'; const option = document.createElement('option'); option.value = ''; option.textContent = 'Univers indisponible'; select.append(option); line(clear('feasibility-current'), error.message); }
  }
  $('feasibility-symbol')?.addEventListener('change', () => {
    const box = clear('feasibility-current'); line(box, 'Lancez le calcul pour la paire sélectionnée.');
  });
  $('feasibility-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const select = $('feasibility-symbol'); const symbol = select.value;
    const button = event.currentTarget.querySelector('button'); const box = clear('feasibility-current');
    if (!symbol) { line(box, 'Aucune paire disponible.'); return; }
    button.disabled = true; select.disabled = true; line(box, 'Lecture des données publiques…');
    try {
      const data = await api(`/api/insights/feasibility?symbol=${encodeURIComponent(symbol)}`, undefined, 25_000);
      if (!record(data) || data.symbol !== symbol || !Number.isFinite(Date.parse(data.observed_at)) || typeof data.quote !== 'string') throw new Error('Réponse incomplète');
      box.replaceChildren();
      line(box, `${data.symbol} · lecture ${localDateTime(data.observed_at)} · prix ${data.price ?? 'indisponible'} ${data.quote}`);
      line(box, `Date du prix fournie par Kraken : ${data.ticker_timestamp_ms == null ? 'indisponible' : localDateTime(data.ticker_timestamp_ms)}`);
      line(box, `Minimum exchange : ${data.min_amount ?? 'indisponible'} unités et ${data.min_notional ?? 'indisponible'} ${data.quote} · pas ${data.amount_step ?? 'indisponible'}`);
      line(box, `Plus petit ordre calculé : ${data.minimum_quantity ?? 'indisponible'} unités · ${data.minimum_order_quote ?? 'indisponible'} ${data.quote}, hors frais et glissement`);
      line(box, `Frais d’entrée estimés : ${data.estimated_entry_fee ?? 'indisponibles'} ${data.quote} · total estimé : ${data.estimated_entry_total ?? 'indisponible'} ${data.quote}`);
      line(box, `Solde libre enregistré : ${data.free_quote ?? 'indisponible'} ${data.quote} · ${data.balance_at ? localDateTime(data.balance_at) : 'date indisponible'} · ${data.balance_fresh === false ? 'solde ancien ou date invalide : comparaison indisponible' : data.cash_sufficient === true ? 'suffisant pour ce minimum estimé' : data.cash_sufficient === false ? 'insuffisant pour ce minimum estimé' : 'comparaison indisponible'}`);
      line(box, `Révision de réglages appliquée : ${data.applied_revision ?? 'inconnue'} · demandée : ${data.requested_revision ?? 'inconnue'}. Taux de frais et glissement issus des réglages appliqués.`);
      line(box, 'Estimation indicative au prix lu : ni taille réelle de l’ordre, ni vérification complète du risque, ni garantie d’exécution. Les frais minimaux et arrondis de frais ne sont pas modélisés.');
    } catch (error) { box.replaceChildren(); line(box, `Calcul indisponible : ${error.message}`); }
    finally { button.disabled = false; select.disabled = false; }
  });
  function renderJourney(box, events) {
    if (!Array.isArray(events) || !events.every(record)) throw new Error('Parcours incomplet');
    box.replaceChildren();
    if (!events.length) { line(box, 'Aucun événement enregistré pour ce cycle.'); return; }
    const groups = new Map();
    for (const event of events) {
      const symbol = event.symbol || 'Cycle';
      if (!groups.has(symbol)) groups.set(symbol, []);
      groups.get(symbol).push(event);
    }
    for (const [symbol, items] of groups) {
      const section = document.createElement('section'); section.className = 'journey-group';
      const title = document.createElement('strong'); title.textContent = symbol; section.append(title);
      const list = document.createElement('ol'); list.className = 'journey-list';
      for (const event of items) {
        const item = document.createElement('li');
        const name = document.createElement('strong'); name.textContent = stages[event.stage] || labels[event.stage] || event.stage || 'Étape inconnue'; item.append(name);
        if (event.body?.action) line(item, `Action : ${event.body.action}`);
        if (event.body?.code) line(item, rejectionLabels[event.body.code] || event.body.code);
        if (event.body?.reason) line(item, event.body.reason);
        if (event.stage === 'RANKING') detail(item, 'Comparaison et coûts enregistrés', event.body);
        list.append(item);
      }
      section.append(list); box.append(section);
    }
    if (events.length >= 1000) line(box, 'Parcours limité aux 1 000 premiers événements enregistrés.');
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    if ($('insights-refresh')) $('insights-refresh').disabled = true;
    try {
      const data = await api('/api/insights/overview');
      if (!validOverview(data)) throw new Error('Observations incomplètes ou incohérentes');
      document.dispatchEvent(new CustomEvent('tn:observations', { detail: data }));
      control = data.entries;
      $('entries-toggle').disabled = false;
      $('entries-toggle').textContent = control.enabled ? 'Suspendre les nouveaux achats' : 'Réautoriser les nouveaux achats';
      $('entries-status').textContent = `${control.enabled ? 'Achats autorisés' : 'Achats suspendus'} · révision ${control.revision}`;
      const box = clear('opportunity-summary');
      const summary = data.opportunities;
      line(box, `${summary.unique_opportunities} opportunités observées · ${summary.terminal_attempts} tentatives terminées`);
      for (const [reason, count] of Object.entries(summary.terminal_reasons)) line(box, `${labels[reason] || reason} : ${count}`);
      for (const [code, count] of Object.entries(summary.rejection_codes || {})) line(box, `${rejectionLabels[code] || code} : ${count}`);
      if (summary.cycles) line(box, `${summary.cycles.complete} cycles complets, ${summary.cycles.failed} en échec et ${summary.cycles.unfinished} sans fin confirmée.`);
      if (summary.expected_windows_current_config !== undefined) line(box, `${summary.expected_windows_current_config} fenêtres théoriques avec l’univers actuel ; présence du moteur non garantie.`);
      if (summary.truncated) line(box, 'Période tronquée : réduire la fenêtre pour une lecture complète.');
      const signature = JSON.stringify(data.cycles);
      renderLatency(data.cycles);
      await loadFeasibility();
      if (signature !== cyclesSignature) {
        cyclesSignature = signature;
        const cycles = clear('insights-cycles');
        if (!data.cycles.length) line(cycles, 'Aucun cycle enregistré depuis la mise en place des observations.');
        for (const cycle of data.cycles) {
          const row = document.createElement('article'); row.className = 'card';
          line(row, `${localDateTime(cycle.started_at)} · ${cycle.status}`);
          const journey = document.createElement('div'); journey.className = 'journey'; journey.hidden = true;
          action(row, 'Voir le parcours', async () => {
            if (!journey.hidden) { journey.hidden = true; return; }
            const data = await api(`/api/insights/cycles/${cycle.id}/events`);
            renderJourney(journey, data.events); journey.hidden = false;
          });
          action(row, 'Exporter le cycle', async () => download(`cycle-${cycle.id}.json`, await api(`/api/insights/cycles/${cycle.id}`)));
          if (cycle.status === 'COMPLETE') action(row, 'Vérifier le rejeu', async () => {
            const result = await api(`/api/insights/cycles/${cycle.id}/replay`, {});
            line(row, result.status === 'IDENTICAL' ? 'Décisions et rapport reproduits à l’identique.' : result.status);
          });
          row.append(journey); cycles.append(row);
        }
      }
      renderProtection(data.protection);
      renderExperiments(data.experiments);
      const applied = data.settings;
      status(`Observations à jour. Réglages demandés : ${applied.revision} ; appliqués : ${applied.applied_revision ?? 'aucune preuve'}${applied.apply_error ? ` · ${applied.apply_error}` : ''}.`);
      try { await policies(); } catch (error) { status(`Observations reçues ; politiques indisponibles : ${error.message}`); }
    } catch (error) {
      const latency = $('cycle-latency-results');
      if (latency) { latency.replaceChildren(); line(latency, `Durées indisponibles : ${error.message}`); }
      document.dispatchEvent(new CustomEvent('tn:observations-unavailable'));
      if (lastProtection) renderProtection({ ...lastProtection, status: 'UNKNOWN', reason: 'OBSERVATION_UNAVAILABLE' });
      status(`Observations indisponibles : ${error.message}`);
    } finally {
      refreshing = false;
      if ($('insights-refresh')) $('insights-refresh').disabled = false;
    }
  }
  async function policies() {
    const data = await api('/api/insights/policy'); const box = clear('position-policies');
    line(box, `Règles demandées pour les prochains achats : ${data.next_position.id.slice(0, 12)}`);
    if (!data.positions.length) line(box, 'Aucune position en cours.');
    for (const position of data.positions) {
      line(box, `${position.symbol} : ${position.policy_id ? position.policy_id.slice(0, 12) : 'règles historiques à vérifier'}`);
      if (!position.policy_id) action(box, 'Adopter les règles actuelles en papier', async () => {
        const totp = window.prompt('Code Authy courant pour adopter ces règles de gestion') || '';
        if (!/^\d{6}$/.test(totp)) throw new Error('Code Authy à six chiffres requis.');
        await api(`/api/control/positions/${position.id}/adopt-policy`, { totp, expected_policy_id: data.legacy_adoption.id });
        await refresh();
      });
    }
  }
  function renderProtection(data) {
    clearTimeout(protectionExpiry);
    const remaining = Math.min(30000, Date.parse(data.valid_until) - Date.now());
    if (data.status === 'OK' && !(remaining > 0)) data = { ...data, status: 'UNKNOWN', reason: 'PROOF_EXPIRED' };
    lastProtection = data;
    document.dispatchEvent(new CustomEvent('tn:protection', { detail: data }));
    const box = clear('protection-results');
    const health = data.status === 'OK' ? 'Surveillance à jour' : data.status === 'ALERT' ? 'Surveillance en alerte' : 'Surveillance inconnue';
    const reasons = { RUNTIME_HEARTBEAT_STALE: 'moteur sans signe de vie récent', RUNTIME_HEARTBEAT_MISSING: 'aucune preuve de vie du moteur',
      RUNTIME_NOT_MONITORED: 'moteur non surveillé', OBSERVER_STALE: 'observation trop ancienne', OBSERVER_MISSING: 'aucune observation',
      INCOMPLETE_OBSERVATION: 'observation incomplète', ACTIVE_INCIDENT: 'incident ouvert',
      OBSERVER_NOTIFICATION_PENDING: 'alerte du surveillant en attente de livraison',
      OBSERVER_NOTIFICATION_UNKNOWN: 'état de livraison des alertes inconnu',
      RUNTIME_STALLED: 'cycle au-delà du budget de trente secondes', RUNTIME_LATE: 'cycle programmé en retard',
      RUNTIME_INCOMPLETE: 'dernier cycle échoué ou incomplet', RUNTIME_MISSING: 'aucune preuve de cycle',
      OBSERVATION_UNAVAILABLE: 'actualisation impossible', PROOF_EXPIRED: 'preuve de surveillance expirée' };
    line(box, `${health}${data.status !== 'OK' ? ` · ${reasons[data.reason] || 'preuve insuffisante'}` : ''}${data.latest ? ` · observation ${localDateTime(data.latest.observed_at)}` : ''}`);
    if (data.status === 'OK') protectionExpiry = setTimeout(() => renderProtection({ ...data, status: 'UNKNOWN', reason: 'PROOF_EXPIRED' }), remaining + 1);
    if (data.latest) detail(box, 'État observé des protections', data.latest.body);
    for (const incident of data.incidents) {
      line(box, `${incident.incident_key} · ${incident.resolved_at ? 'résolu' : incident.acknowledged_at ? 'acquitté, encore ouvert' : 'ouvert'}`);
      if (!incident.resolved_at && !incident.acknowledged_at) action(box, 'Acquitter la lecture', async () => {
        await api(`/api/insights/incidents/${incident.id}/acknowledge`, {}); await refresh();
      });
    }
  }
  function renderExperiments(items) {
    const box = clear('experiment-results');
    if (!items.length) line(box, 'Aucune comparaison enregistrée.');
    for (const item of items) {
      line(box, `${localDateTime(item.created_at)} · ${item.state} · aucune promotion automatique`);
      for (const variant of item.result?.variants || []) line(box, `${variant.name} : ${variant.trades} trades, capital final ${variant.equity_end}, drawdown ${Number(variant.max_drawdown * 100).toFixed(2)} %`);
      detail(box, 'Résultats et hypothèses', item.result);
    }
  }
  $('insights-refresh')?.addEventListener('click', refresh);
  $('refresh-all')?.addEventListener('click', refresh);
  $('entries-toggle')?.addEventListener('click', async () => {
    if (!control || busy) return;
    busy = true;
    try {
      const body = { enabled: !control.enabled, expected_revision: control.revision, reason: 'commande dashboard' };
      if (body.enabled) {
        body.totp = window.prompt('Code Authy courant pour réautoriser les achats') || '';
        if (!/^\d{6}$/.test(body.totp)) throw new Error('Code Authy à six chiffres requis.');
      }
      await api('/api/control/entries', body); await refresh();
    } catch (error) { status(error.message); } finally { busy = false; }
  });
  $('diagnostic-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const id = $('diagnostic-id').value.trim();
      if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error('Identifiant de position invalide.');
      const result = await api(`/api/positions/${id}/diagnostics`); const box = clear('trade-diagnostics');
      line(box, `${result.symbol} · couverture ${Number(result.coverage * 100).toFixed(0)} % · extrêmes intrabougie inconnus`);
      for (const [label, value] of [['Meilleur PnL observé', result.mfe_quote], ['Pire PnL observé', result.mae_quote], ['Résultat réalisé', result.realized_net_pnl_quote], ['Frais connus', result.fees_quote]]) line(box, `${label} : ${value ?? 'indisponible'}`);
      detail(box, 'Déroulement de la position', result);
    } catch (error) { status(error.message); }
  });
  async function file(id, maxBytes) {
    const chosen = $(id).files[0]; if (!chosen) throw new Error('Choisir un fichier JSON.');
    if (chosen.size > maxBytes) throw new Error('Fichier trop volumineux.');
    return JSON.parse(await chosen.text());
  }
  $('experiment-run')?.addEventListener('click', async () => {
    if (busy) return; busy = true; status('Comparaison en simulation en cours…');
    try { await api('/api/insights/experiments', await file('experiment-file', 1024 * 1024)); await refresh(); }
    catch (error) { status(error.message); } finally { busy = false; }
  });
  $('replay-run')?.addEventListener('click', async () => {
    try { const result = await api('/api/insights/replay', await file('replay-file', 8 * 1024 * 1024)); const box = clear('replay-results'); line(box, 'Rejeu hors ligne terminé.'); detail(box, 'Résultat', result); }
    catch (error) { status(error.message); }
  });
  $('confidence-load')?.addEventListener('click', async () => {
    try {
      const end = new Date(); const start = new Date(end.getTime() - 90 * 86400000);
      const data = await api(`/api/insights/confidence?since=${encodeURIComponent(start.toISOString())}&until=${encodeURIComponent(end.toISOString())}`);
      const box = clear('confidence-results'); line(box, 'Les scores ne sont pas des probabilités de gain. Échantillon limité aux achats exécutés et clos.');
      for (const cohort of data.cohorts) line(box, `${cohort.model} : ${cohort.count} résultats · ${cohort.evidence}${cohort.model_identity_verified ? '' : ' · version des poids non vérifiée'}`);
      detail(box, 'Cohortes, effectifs et limites', data);
    } catch (error) { status(error.message); }
  });
  loadFeasibilitySymbols();
  refresh();
  setInterval(refresh, 30_000);
})();
