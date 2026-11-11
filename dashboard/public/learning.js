/* Mémoire PAPER : lecture seule et texte uniquement, fraîcheur explicite. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  if (!$('learning-panel')) return;
  let busy = false, last = null, unavailable = false;
  const text = (id, value) => { $(id).textContent = value ?? '—'; };
  const date = (value) => value ? new Date(value).toLocaleString('fr-FR') : '—';
  const states = { NOT_STARTED: 'Le service d’apprentissage n’a pas encore enregistré de passage.',
    RUNNING: 'Évaluation des achats fictifs en cours.', WAITING: 'La mémoire attend de nouvelles expériences.',
    LEARNED: 'Une nouvelle version de la mémoire a été enregistrée.', FAILED: 'Le dernier passage a échoué.',
    STALE: 'Le service n’a pas donné de nouvelles récemment.', PAPER_ONLY: 'Mémoire automatique disponible en PAPER uniquement.' };
  const reasons = { NOT_ENOUGH_RESULTS: 'Pas encore assez de résultats exploitables.',
    NOT_ENOUGH_NEW_RESULTS: 'Les derniers résultats ont déjà été relus ; la mémoire attend de nouveaux cas.',
    COOLDOWN: 'Le délai entre deux relectures n’est pas encore écoulé.',
    NEW_LESSONS: 'Les observations sont disponibles pour les prochaines analyses PAPER.',
    INTERRUPTED: 'Le passage précédent a été interrompu. Sa réussite n’est pas confirmée.',
    SIMULATION_DATA_UNAVAILABLE: 'Certains cours sont indisponibles ou incohérents. Les autres simulations ont été traitées ; la relecture attend un lot valide.',
    LLMError: 'Le modèle n’a pas pu produire une mémoire exploitable.',
    LessonsError: 'Les observations proposées par le modèle ont été refusées.' };
  const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const count = (value) => Number.isInteger(value) && value >= 0;
  const stamp = (value) => typeof value === 'string' && Number.isFinite(Date.parse(value));
  function valid(data) {
    if (!record(data) || !Object.hasOwn(states, data.state) || !Array.isArray(data.history)
      || !data.history.every((item) => record(item) && count(item.version) && count(item.source_count)
        && typeof item.active === 'boolean' && typeof item.model_name === 'string' && stamp(item.created_at)
        && (item.valid_until === null || stamp(item.valid_until))
        && Array.isArray(item.lessons) && item.lessons.every((lesson) => typeof lesson === 'string'))) return false;
    if (data.state === 'PAPER_ONLY') return data.run === null && data.history.length === 0;
    if (!record(data.simulations) || !['total', 'eligible', 'pending', 'excluded', 'no_plan'].every((key) => count(data.simulations[key]))
      || !record(data.policy) || !['min_samples', 'min_new_samples', 'cooldown_hours'].every((key) => count(data.policy[key]) && data.policy[key] > 0)) return false;
    if (data.state === 'NOT_STARTED') return data.run === null && data.valid_until === null;
    return record(data.run) && ['RUNNING', 'WAITING', 'LEARNED', 'FAILED'].includes(data.run.status)
      && (data.state === data.run.status || data.state === 'STALE') && stamp(data.valid_until)
      && stamp(data.run.started_at) && (data.run.status === 'RUNNING' || stamp(data.run.finished_at))
      && count(data.run.source_count) && count(data.run.new_source_count);
  }
  function state() {
    if (!last || unavailable) return;
    const stale = last.valid_until && Date.now() >= Date.parse(last.valid_until);
    text('learning-state', states[stale ? 'STALE' : last.state] || 'État de la mémoire inconnu.');
  }
  function render(data) {
    unavailable = false; last = data; state();
    const run = data.run, policy = data.policy;
    text('learning-reason', data.state === 'PAPER_ONLY' ? 'Les résultats PAPER ne sont pas chargés dans ce mode.'
      : run ? reasons[run.reason] || 'Consulter le journal du service pour le détail.' : 'Le lancement du service est nécessaire pour automatiser les relectures.');
    for (const key of ['total','eligible','pending','excluded','no_plan']) text(`learning-${key.replace('_','-')}`, data.simulations?.[key]);
    text('learning-progress', policy ? `Relecture à partir de ${policy.min_samples} résultats, puis ${policy.min_new_samples} nouveaux cas minimum. Au plus une relecture toutes les ${policy.cooldown_hours} h. Dernier lot : ${run?.source_count ?? 0} résultats, dont ${run?.new_source_count ?? 0} nouveaux.` : '');
    text('learning-updated', `Dernier passage enregistré : ${date(run?.finished_at || run?.started_at)}.`);
    const history = $('learning-history'); history.replaceChildren();
    if (!data.history?.length) { const p = document.createElement('p'); p.textContent = 'Aucune version enregistrée.'; history.append(p); }
    for (const item of data.history || []) {
      const article = document.createElement('article'), title = document.createElement('strong'), meta = document.createElement('p'), list = document.createElement('ul');
      const active = item.active && (!item.valid_until || Date.parse(item.valid_until) > Date.now());
      title.textContent = `Version ${item.version} · ${active ? 'active' : 'inactive ou expirée'}`;
      meta.textContent = `${date(item.created_at)} · ${item.source_count} résultats · ${item.model_name}`;
      for (const lesson of item.lessons || []) { const li = document.createElement('li'); li.textContent = lesson; list.append(li); }
      article.append(title, meta, list); history.append(article);
    }
  }
  async function refresh() {
    if (busy) return;
    busy = true; $('learning-refresh').disabled = true;
    try {
      const response = await fetch('/api/insights/learning', { signal: AbortSignal.timeout(10_000) });
      if (response.status === 401) { window.location.href = '/login'; return; }
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      if (!valid(data)) throw new Error('invalid');
      render(data);
    } catch {
      unavailable = true;
      text('learning-state', 'Actualisation de la mémoire impossible.');
      text('learning-reason', last ? 'Dernières données conservées avec leur date ; vérifier la connexion et le service.' : 'Vérifier la connexion, le service et les migrations de la base.');
    } finally { busy = false; $('learning-refresh').disabled = false; }
  }
  $('learning-refresh').addEventListener('click', refresh);
  $('insights-refresh')?.addEventListener('click', refresh);
  $('refresh-all')?.addEventListener('click', refresh);
  refresh(); setInterval(refresh, 30_000); setInterval(state, 1000);
})();
