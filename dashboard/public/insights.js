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
  let control = null;
  let busy = false;
  let refreshing = false;
  let lastProtection = null;
  let protectionExpiry = null;
  function status(message) { if ($('insights-status')) $('insights-status').textContent = message; }
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? { signal: AbortSignal.timeout(10_000) } : {
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
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    if ($('insights-refresh')) $('insights-refresh').disabled = true;
    try {
      const data = await api('/api/insights/overview');
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
      const cycles = clear('insights-cycles');
      if (!data.cycles.length) line(cycles, 'Aucun cycle enregistré depuis la mise en place des observations.');
      for (const cycle of data.cycles) {
        const row = document.createElement('article'); row.className = 'card';
        line(row, `${localDateTime(cycle.started_at)} · ${cycle.status}`);
        action(row, 'Voir le parcours', async () => {
          const data = await api(`/api/insights/cycles/${cycle.id}/events`);
          for (const event of data.events) {
            line(row, `${event.symbol || 'Cycle'} : ${labels[event.stage] || event.stage}`);
            if (event.body?.code) line(row, rejectionLabels[event.body.code] || event.body.code);
            if (event.body?.reason) line(row, event.body.reason);
            if (event.stage === 'RANKING') detail(row, 'Comparaison des paires et coûts observés', event.body);
          }
        });
        action(row, 'Exporter le cycle', async () => download(`cycle-${cycle.id}.json`, await api(`/api/insights/cycles/${cycle.id}`)));
        if (cycle.status === 'COMPLETE') action(row, 'Vérifier le rejeu', async () => {
          const result = await api(`/api/insights/cycles/${cycle.id}/replay`, {});
          line(row, result.status === 'IDENTICAL' ? 'Décisions et rapport reproduits à l’identique.' : result.status);
        });
        cycles.append(row);
      }
      renderProtection(data.protection);
      renderExperiments(data.experiments);
      const applied = data.settings;
      status(`Observations à jour. Réglages demandés : ${applied.revision} ; appliqués : ${applied.applied_revision ?? 'aucune preuve'}${applied.apply_error ? ` · ${applied.apply_error}` : ''}.`);
      await policies();
    } catch (error) {
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
  refresh();
})();
