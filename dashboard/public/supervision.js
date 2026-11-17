/* Synthèse de lecture : aucun ordre, dimensionnement ou règle financière ici. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  if (!$('mission-brief')) return;
  const text = (id, value) => { if ($(id)) $(id).textContent = value; };
  const number = new Intl.NumberFormat('fr-FR');
  const date = (value) => new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
  const labels = {
    NO_SETUP: 'Aucun setup d’achat', LLM_HOLD: 'Attente du modèle',
    LLM_UNAVAILABLE: 'Modèle indisponible', RISK_REJECTED: 'Refus du pare-feu',
    NOT_SELECTED: 'Autre paire retenue', FILLED: 'Achat exécuté',
    ENTRY_DISABLED: 'Achats suspendus', DATA_UNAVAILABLE: 'Données indisponibles',
    ALREADY_EVALUATED: 'Bougie déjà évaluée', REVALIDATION_REJECTED: 'Refus après actualisation',
  };
  const explanations = {
    NO_SETUP: 'Les conditions d’entrée de la stratégie n’étaient pas réunies.',
    LLM_HOLD: 'Le modèle a proposé d’attendre sur ces tentatives.',
    LLM_UNAVAILABLE: 'L’inférence était indisponible. Examinez les services et les parcours.',
    RISK_REJECTED: 'Le pare-feu a refusé ces intentions. Le détail des motifs figure dans les décisions.',
    NOT_SELECTED: 'La sélection a privilégié une autre paire sur ces tentatives.',
    FILLED: 'Des intentions ont abouti à un achat. Ce compteur ne mesure pas leur rentabilité.',
    ENTRY_DISABLED: 'La commande de suspension a empêché de nouvelles entrées.',
    DATA_UNAVAILABLE: 'Des données manquaient. Examinez la capture et la disponibilité des services.',
    ALREADY_EVALUATED: 'Le moteur avait déjà traité ces bougies.',
    REVALIDATION_REJECTED: 'La vérification finale a refusé ces entrées après actualisation des données.',
  };
  let account = null, online = false, observation = null, observedAt = 0;
  let observationAvailable = false, protection = null;

  function readiness(key, value, tone) {
    text(`readiness-${key}`, value);
    $(`readiness-${key}-dot`).dataset.tone = tone;
  }

  function render() {
    const fresh = observationAvailable && Date.now() - observedAt < 65_000;
    readiness('api', online ? 'Statut reçu' : account ? 'Données anciennes' : 'En attente', online ? 'ok' : 'unknown');
    const proven = fresh && protection?.status === 'OK' && Date.parse(protection.valid_until) > Date.now();
    readiness('engine', proven ? 'Confirmée' : fresh && protection?.status === 'ALERT' ? 'Alerte à examiner' : 'Non confirmée',
      proven ? 'ok' : fresh && protection?.status === 'ALERT' ? 'danger' : 'unknown');
    const entries = observation?.entries;
    readiness('entries', fresh ? entries.enabled ? 'Autorisés par la commande' : 'Suspendus par la commande' : 'À vérifier',
      fresh ? entries.enabled ? 'ok' : 'warn' : 'unknown');
    const settings = observation?.settings;
    const applied = fresh && settings.applied_revision !== null && settings.revision === settings.applied_revision && !settings.apply_error;
    readiness('settings', !fresh ? 'À vérifier' : settings.apply_error ? 'Erreur d’application'
      : applied ? `Révision ${settings.revision}` : `Révision ${settings.revision} en attente`, applied ? 'ok' : fresh ? 'warn' : 'unknown');
    let title = 'En attente du premier statut', description = 'La situation du compte apparaîtra après réception de données valides.', tone = 'unknown';
    if (account && !online) {
      title = 'Le statut du compte est à vérifier';
      description = 'Les dernières valeurs sont conservées. Une connexion à l’API ne prouve pas que le moteur fonctionne.';
    } else if (account) {
      const states = {
        IDLE: ['Le compte est en veille', 'Aucun achat en cours dans le dernier statut reçu.'],
        EVALUATING: ['Une opportunité est en cours d’analyse', 'Le pare-feu doit encore valider toute entrée.'],
        EXECUTING: ['Une opération est en cours', 'Consultez son résultat dans l’activité.'],
        IN_POSITION: ['Des positions sont ouvertes', 'Retrouvez chaque stop et les montants connus dans les positions ci-dessous.'],
        POST_TRADE_SWEEP: ['La clôture est en cours de traitement', 'Le moteur traite la répartition du capital.'],
        COOLDOWN: ['Le compte est en pause', 'Consultez le dernier cycle avant la prochaine analyse.'],
        LOCKED_CIRCUIT_BREAKER: ['Les achats sont bloqués', 'Une protection ou une pause bloque les entrées. Consultez le motif d’arrêt.'],
        EMERGENCY_HALT: ['Un arrêt d’urgence est enregistré', 'Consultez le motif d’arrêt et l’état des protections avant toute reprise.'],
      };
      [title, description] = states[account.state];
      tone = ['EMERGENCY_HALT', 'LOCKED_CIRCUIT_BREAKER'].includes(account.state) ? 'danger' : 'neutral';
      if (fresh && !entries.enabled && tone !== 'danger') {
        title = 'Les nouveaux achats sont suspendus';
        description = 'La suspension conserve la gestion des positions existantes et de leurs protections.';
        tone = 'warn';
      }
      if (!proven) description += ' La surveillance du moteur reste à confirmer.';
    }
    text('mission-title', title); text('mission-description', description);
    $('mission-brief').dataset.tone = tone;
    text('diagnosis-coverage', !fresh ? observation ? 'Lecture ancienne' : 'Indisponible' : observation.opportunities.truncated ? 'Période tronquée' : 'Événements observés');
  }

  function renderObservation(data) {
    const summary = data.opportunities;
    text('observed-opportunities', number.format(summary.unique_opportunities));
    text('observed-attempts', number.format(summary.terminal_attempts));
    text('observed-filled', number.format(summary.terminal_reasons.FILLED || 0));
    const reasons = Object.entries(summary.terminal_reasons).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
    const leading = reasons[0];
    const ties = leading ? reasons.filter(([, n]) => n === leading[1]) : [];
    text('diagnosis-lead', !leading ? 'Aucune tentative terminée observée' : ties.length > 1
      ? 'Plusieurs motifs dominent les observations' : `${labels[leading[0]] || leading[0]} : motif le plus fréquent`);
    let detail = !leading ? 'L’absence d’événements ne prouve ni l’absence de setup ni la disponibilité du moteur.'
      : ties.length > 1 ? 'Les motifs en tête ont le même effectif. Examinez les parcours pour les distinguer.'
        : explanations[leading[0]] || 'Examinez le détail des parcours enregistrés.';
    if (leading && ['RISK_REJECTED', 'REVALIDATION_REJECTED'].includes(leading[0])) {
      const codes = summary.rejection_codes || {};
      const minima = ['SIZING_BELOW_EXCHANGE_MINIMUM', 'EXCHANGE_MIN_NOTIONAL_FAILED', 'INSUFFICIENT_FREE_BALANCE'];
      if (minima.some((code) => codes[code] > 0)) detail += ' Des refus liés au disponible ou aux minima de l’exchange sont enregistrés.';
    }
    detail += ' Bilan sur 7 jours ; ce n’est pas le motif garanti de la prochaine décision.';
    if (summary.truncated) detail += ' La limite de collecte est atteinte : le bilan est partiel.';
    text('diagnosis-detail', detail);
    const list = $('diagnosis-reasons'); list.replaceChildren();
    for (const [reason, count] of reasons) {
      const row = document.createElement('li');
      const label = document.createElement('span'); label.textContent = labels[reason] || reason;
      const value = document.createElement('strong'); value.textContent = number.format(count);
      const meter = document.createElement('meter'); meter.min = 0; meter.max = Math.max(1, summary.terminal_attempts); meter.value = count;
      meter.setAttribute('aria-label', `${label.textContent} : ${count} sur ${summary.terminal_attempts}`);
      row.append(label, value, meter); list.append(row);
    }
    text('observations-updated', `Lecture reçue le ${date(observedAt)} · période de 7 jours`);
    const cycle = data.cycles[0];
    text('runtime-last-cycle', cycle ? date(cycle.started_at) : 'Aucun cycle enregistré');
    const cycleStates = { COMPLETE: 'Cycle complet', FAILED: 'Cycle échoué', RUNNING: 'Cycle en cours', INCOMPLETE: 'Cycle incomplet' };
    text('runtime-cycle-state', cycle ? cycleStates[cycle.status] || cycle.status : 'Aucun cycle reçu');
  }
  document.addEventListener('tn:status', (event) => { account = event.detail; render(); });
  document.addEventListener('tn:connection', (event) => { online = event.detail; render(); });
  document.addEventListener('tn:observations', (event) => {
    observation = event.detail; observedAt = Date.now(); observationAvailable = true;
    renderObservation(observation); render();
  });
  document.addEventListener('tn:observations-unavailable', () => {
    observationAvailable = false;
    text('observations-updated', observedAt ? `Lecture du ${date(observedAt)} conservée · actualisation impossible` : 'Observations indisponibles · vérifier la connexion');
    if (!observation) {
      text('diagnosis-lead', 'Le diagnostic est indisponible');
      text('diagnosis-detail', 'Vérifiez la connexion et les observations dans Décisions & IA.');
    }
    render();
  });
  document.addEventListener('tn:protection', (event) => { protection = event.detail; render(); });
  setInterval(render, 1000);
  render();
})();
