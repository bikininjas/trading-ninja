# Dashboard v2 — supervision et pilotage

État au 11 novembre 2026. Le plan de déploiement et de passage au réel est
[ROADMAP.md](../docs/ROADMAP.md).

Le dashboard est une **interface d'administration**, pas un produit grand public. Il
montre ce que fait le bot, laisse mettre en pause ou liquider, et permet de changer les
réglages de risque sans éditer un fichier. Il ne calcule aucun prix, aucune taille et
aucun stop : tout cela reste dans le moteur et dans la Risk Gateway.

## 1. Principes

- **Client mince.** Node.js ≥ 20, `node:http`, `fetch` natif et `node:test`. Aucune
  dépendance npm, aucun framework, aucun build.
- **Le jeton ne va pas au navigateur.** Le JWT de l'API reste dans la mémoire du
  processus Node. Le navigateur ne reçoit qu'un identifiant de session aléatoire dans
  un cookie `HttpOnly`, `SameSite=Strict`.
- **Aucune donnée dans `innerHTML`.** Les thèses du LLM, les symboles et les motifs de
  refus passent par `textContent`. Un texte hostile ne peut pas injecter de balise.
- **Lecture seule par défaut.** Le dashboard observe ; les seules écritures sont les
  commandes explicites, suspension/réautorisation des achats, adoption PAPER,
  expériences et acquittement des incidents, ainsi que le formulaire de réglages.
- **Pas d'argent réel sans les verrous du bot.** Le panic-sell fonctionne aussi en
  `LIVE` (annulation globale puis vente au marché), mais uniquement derrière les trois
  verrous du bot ; le dashboard ne peut pas activer `LIVE` à lui seul.

## 2. Architecture

```text
navigateur
  HTML / CSS / JS, zéro dépendance
  cookie tn_session (HttpOnly) + EventSource /events
        │
        ▼
dashboard/server.js  (Node ≥ 20, 127.0.0.1:8081 par défaut)
  sessions en mémoire, proxy API, SSE, assets statiques
        │  Authorization: Bearer <JWT> (jamais exposé au navigateur)
        ▼
FastAPI /api/*  (127.0.0.1:8080)
        │
        ▼
PostgreSQL (Supabase)
```

Le navigateur ne parle jamais directement à FastAPI. Cette médiation permet de garder
le JWT côté serveur, d'appliquer une CSP stricte et de limiter les routes exposées.

### Flux temps réel

1. `GET /events` ouvre un flux SSE.
2. Le serveur Node interroge `GET /api/status` toutes les `DASHBOARD_POLL_MS`
   (2 000 ms par défaut) et pousse chaque résultat.
3. Le navigateur met à jour l'état, l'équité, la position, les jauges de risque et le
   compte à rebours d'un éventuel `HALT`.
4. Les statistiques lourdes (`/api/stats`) sont chargées au démarrage puis toutes les
   15 s. L'historique détaillé (`/api/history/...`) est chargé à la demande.

Une session expirée produit un événement SSE `expired` ; la page renvoie vers
`/login`. Si le flux tombe, le bandeau de connexion passe à « hors ligne » mais les
dernières données restent lisibles.

## 3. Les cinq espaces du dashboard v2

La navigation latérale remplace la page unique. Chaque espace possède un lien
permanent (`/#overview`, `/#portfolio`, `/#decisions`, `/#activity`, `/#settings`).
Le navigateur conserve ses formulaires et données entre deux espaces. Sur mobile,
un menu repliable garde la même navigation ; Échap le ferme et le focus est géré
au clavier.

| Espace | Contenu |
|---|---|
| Vue d’ensemble | Capital total, résultat net, taux de réussite, recul du capital, courbe interactive, état du bot, jauges de risque et position ouverte |
| Portefeuille | Disponible, capital actif, réserve, cagnotte, solde engagé, statistiques de transactions et résultats quotidiens |
| Décisions & IA | Dernière analyse du modèle, refus du pare-feu, parcours des cycles, rejeu, diagnostics, politiques, expériences, scores, protections, achats non pris et leçons |
| Activité | Signaux, pare-feu, ordres et positions ; limite 10/25/50 et export CSV des lignes affichées |
| Réglages | Profil, limites modifiables, révisions demandée/appliquée, exemples fictifs en euros, comparaison et glossaire |

Le bouton **Piloter le bot**, accessible dans chaque espace, ouvre les commandes :
pause, reprise, liquidation, suspension des achats, réconciliation et réinitialisation
du coupe-circuit. La liquidation conserve la confirmation `PANIC` et le TOTP frais.
La pause complète est refusée si une position est ouverte ; la suspension des achats
conserve la gestion des positions. Le bandeau d’arrêt reste visible dans chaque espace.

**Actualiser** relit le statut, les statistiques, la santé et l’activité. Dans
Décisions & IA, il relance également les observations. Les réglages en cours de saisie
ne sont pas écrasés : leur bouton **Recharger** est explicite. L’enregistrement
indique une demande en attente tant que le moteur n’a pas confirmé sa révision.

Les graphiques se redimensionnent selon leur largeur réelle. La courbe du capital
se parcourt au pointeur ou avec les touches gauche/droite, Début et Fin. Les états
vides restent explicites et aucune série factice n’est ajoutée au produit. Un statut
incomplet ou un flux coupé conserve les dernières valeurs et leur date de réception.
Un statut de plus de dix secondes est signalé comme ancien. Une réponse HTTP de
statut commencée avant un événement SSE ne peut pas écraser ce dernier. Les appels
de statistiques et de santé ne se chevauchent pas.

L’export CSV est local, limité aux lignes affichées et neutralise les cellules
susceptibles d’être interprétées comme des formules par un tableur. Il n’ajoute
aucune route d’extraction ni aucun accès à la base.

La connexion reprend le langage visuel de la v2. Les sessions, la CSP et les
contrats API sont conservés. Le panneau d’observation conserve les contrôles de
révision et les confirmations TOTP décrits dans
[OBSERVATION_ET_RECHERCHE.md](../docs/OBSERVATION_ET_RECHERCHE.md).

### Jauges de risque

Les valeurs de drawdown et de plafond sont des **fractions** côté API (`0.02` = 2 %).
La jauge affiche le rapport valeur / plafond :

- `ok` : moins de 75 % du plafond ;
- `warn` : entre 75 % et 100 % ;
- `danger` : plafond atteint ou dépassé.

Les plafonds hebdomadaire, depuis le plus haut et de pertes consécutives sont
**lecture seule** dans le dashboard. Ils restent modifiables par la configuration
d'exploitation et par les presets ; le dashboard ne les expose pas en écriture pour
éviter qu'un clic désarme un coupe-circuit permanent.

### Activité

L'onglet « Signaux » montre la thèse du LLM, l'action, la conviction et le régime.
« Pare-feu » montre le verdict, le motif de refus, le prix et le risque effectif.
« Ordres » montre le sens, le rôle, le statut, la quantité, le prix moyen et les frais.
« Positions » montre l'ouverture, le statut, l'entrée, la quantité, le net et la raison
de sortie.

Les tables sont limitées à 50 lignes par requête. C'est volontaire : le dashboard est
un poste de supervision, pas un outil d'export. Pour une analyse complète, interroger
la base ou les scripts de rapport.

## 4. Contrat API utilisé

Le dashboard ne relaie que les routes nécessaires. Toute autre route de l'API reste
inaccessible depuis le navigateur.

| Route dashboard | Route API | Réponse utile |
|---|---|---|
| `GET /api/status` | `GET /api/status` | état, équité, position, drawdowns, `recent` |
| `GET /api/stats` | `GET /api/stats` | `pnl`, `equity_curve`, `daily`, `rejections`, `llm`, `shadow`, `lessons` |
| `GET /api/history/{table}` | `GET /api/history/{table}?limit=N` | `{ "table": ..., "rows": [...] }` |
| `GET /api/settings` | `GET /api/settings` | réglages + profils |
| `POST /api/settings` | `POST /api/settings` | révision demandée et état d’application, sans promesse d’application immédiate |
| `POST /api/control/pause` | `POST /api/control/pause` | état |
| `POST /api/control/resume` | `POST /api/control/resume` | état |
| `POST /api/control/panic` | `POST /api/control/panic` | état, TOTP requis |
| `GET /events` | interrogation de `/api/status` | flux SSE |
| `POST /api/control/entries` | même route | suspension ou réautorisation révisionnée, TOTP à la réautorisation |
| `GET/POST /api/insights/*` | routes privées autorisées | parcours, rejeu, expériences et incidents ; détails dans le guide d’observation |

Le kill-switch API suspend les achats et conserve les protections sans appeler
Kraken ; la gestion des positions existantes continue. Le panic-sell et le script
de liquidation sont distincts. Une pause ne remplace pas un HALT humain et une
reprise refuse les intentions non rapprochées. Le quota SQL indisponible apparaît
UNKNOWN, sans valeur fictive d’espace libre.

### Historique

`GET /api/history/{table}` n'accepte que quatre noms :

```text
llm_signals, risk_evaluations, orders, positions
```

Le serveur Node vérifie cette liste **avant** d'appeler FastAPI et renvoie `400` pour
tout autre nom. La limite est ramenée entre 1 et 50 (défaut : 20). Cela évite qu'un
paramètre `limit=100000` transforme le dashboard en extracteur de base.

### Réglages

Modifiables depuis le dashboard :

- profil (`prudent`, `standard`, `aggressive`, `micro`, `swing`) ;
- part maximale par achat (`max_per_trade_pct`) ;
- perte journalière maximale (`max_daily_loss_pct`) ;
- ordres maximum par heure (`max_orders_per_hour`) ;
- conviction minimale (`confidence_floor`) ;
- reprise automatique après un `HALT` (`halt_auto_resume_hours`) ;
- règle d'entrée activée ou non (`entry_rule_enabled`).

Exposés en lecture seule :

- perte hebdomadaire maximale (`max_weekly_loss_pct`) ;
- drawdown maximal depuis le plus haut (`max_drawdown_from_hwm_pct`) ;
- pertes consécutives maximales (`max_consecutive_losses`) ;
- risque par achat du preset (`risk_per_trade_pct`).

Les modifications sont appliquées au **cycle suivant** : le bot relit
`runtime_settings` à chaque boucle. Le dashboard n'écrit jamais dans `.env` ni dans
`.env.local`.

## 5. Sécurité

| Mesure | Détail |
|---|---|
| Jeton | Le JWT reste dans la mémoire du processus Node, jamais dans le navigateur. |
| Cookie | `tn_session`, `HttpOnly`, `SameSite=Strict`, `Path=/`, durée la plus courte entre 7 jours et `expires_in` de l'API. |
| Session | Identifiant aléatoire de 128 bits ; les sessions expirées sont purgées. |
| CSP | `default-src 'self'`, `script-src 'self'`, `style-src 'self'`, `frame-ancestors 'none'`. |
| En-têtes | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. |
| POST | Vérification de l'origine (`Origin` same-origin) sur les réglages et les commandes. |
| Panic | Code TOTP à 6 chiffres **frais** ; une session volée ne suffit pas. |
| Mode | En `LIVE`, le panic passe par l'exchange (annulation globale + vente) et répond `502` si une vente échoue. Le dashboard ne peut pas passer en `LIVE`. |
| Réseau | Écoute sur `127.0.0.1` par défaut. `0.0.0.0` exige `DASHBOARD_ALLOW_REMOTE=true`. |

**Ne jamais exposer le port 8081 directement sur Internet.** Sans TLS, le mot de passe
et le code Authy circulent en clair. Utiliser Tailscale/WireGuard ou un reverse proxy
HTTPS avec `DASHBOARD_COOKIE_SECURE=true`. Voir D-037.

## 6. Lancer et tester

```bash
# API + dashboard en arrière-plan
scripts/manage.sh start all
scripts/manage.sh status
scripts/manage.sh logs all
scripts/manage.sh stop all
```

Sans le script :

```bash
cd backend && .venv/bin/python -m uvicorn app.api.main:create_app --factory \
    --host 127.0.0.1 --port 8080
cd dashboard && node server.js     # http://127.0.0.1:8081
```

Tests :

```bash
cd dashboard && npm test               # 32 tests Node
cd backend && .venv/bin/python -m pytest  # 921 collectés, dont 63 PostgreSQL opt-in
python scripts/check_dashboard_browser.py /tmp/tn-dashboard-v2
bash scripts/check.sh                   # vérification globale
```

Les tests Node démarrent une fausse API sur un port éphémère : cookie, proxy,
historique borné, refus d'origine, panic TOTP, SSE et expiration de session. Le
`check.sh` vérifie aussi la syntaxe de `server.js`, l'index du code et la cohérence de
la documentation.

### Configuration

Variables d'environnement :

| Variable | Défaut | Rôle |
|---|---|---|
| `API_BASE_URL` | `http://127.0.0.1:8080` | adresse de l'API FastAPI |
| `DASHBOARD_PORT` | `8081` | port d'écoute |
| `DASHBOARD_HOST` | `127.0.0.1` | interface ; ne pas exposer sans TLS |
| `DASHBOARD_POLL_MS` | `2000` | fréquence de rafraîchissement |
| `DASHBOARD_COOKIE_SECURE` | `false` | `true` derrière HTTPS |

Les réglages métier (prudence, part du capital, seuil journalier, conviction,
reprise automatique) ne sont **pas** des variables d'environnement du dashboard :
ils vivent dans `runtime_settings`, côté base, et sont modifiés par le panneau
« Réglages ».

### Exposition réseau

Par défaut, le dashboard écoute sur `127.0.0.1` : il n'est joignable que depuis la
machine. Pour l'ouvrir au réseau, **deux gestes** sont exigés :

```bash
# dashboard/.env
DASHBOARD_HOST=0.0.0.0
DASHBOARD_ALLOW_REMOTE=true
```

Le second geste n'est pas une formalité : sans TLS devant, le mot de passe et le
code Authy circulent **en clair** sur le réseau. Utiliser Tailscale/WireGuard, ou un
reverse proxy HTTPS avec `DASHBOARD_COOKIE_SECURE=true`. Ne jamais exposer ce port
directement sur Internet.

Sous WSL2, `0.0.0.0` rend le port joignable depuis Windows via l'IP de la VM
(`hostname -I`) ; pour le LAN, le pare-feu Windows et un `portproxy` peuvent être
nécessaires. Vérifier avec `ss -ltn` que l'écoute est bien sur `0.0.0.0:8081`.


## 7. Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| Redirection vers `/login` en boucle | cookie absent, expiré, ou `DASHBOARD_COOKIE_SECURE=true` sans HTTPS | vérifier l'URL, le cookie, et `DASHBOARD_COOKIE_SECURE`. |
| `hors ligne` dans l'en-tête | flux SSE coupé ou API arrêtée | `scripts/manage.sh status`, `logs all`. |
| Courbe vide | aucune équité enregistrée, ou pas assez de points sur la période | choisir « Tout » ; vérifier `equity_snapshots`. |
| `Historique indisponible` | table non exposée ou API en erreur | utiliser un des quatre onglets prévus ; vérifier les logs API. |
| Réglages refusés (`422`) | valeur hors bornes ou payload incohérent | relire le message, revenir à un preset, réessayer. |
| Page inaccessible depuis le réseau | écoute sur `127.0.0.1` | lire la section « Exposition réseau » de `dashboard/README.md` ; ne pas ouvrir 8081 sans TLS. |
| Session expirée | TTL API plus court que 7 jours | se reconnecter ; la session Node suit `expires_in`. |

## 8. Limites connues

- Le contrôle Playwright/Chromium est automatisé dans la CI, séparément de
  `scripts/check.sh`. Il couvre les cinq espaces, les flux invalides/coupés, le capital, la
  suspension/TOTP, le rejeu, les diagnostics, les profils, le prompt, l’export CSV,
  la connexion et quatre largeurs sans débordement avec une API factice ; ce n’est pas une recette du
  déploiement cible ni de tous les scénarios de flux.
- Les réglages ne demandent pas de TOTP frais : la session 2FA suffit. La question
  reste ouverte dans la feuille de route.
- Les plafonds hebdomadaire, depuis le plus haut et de pertes consécutives sont en
  lecture seule dans le dashboard.
- L'historique est borné à 50 lignes par vue ; les graphiques utilisent au plus 200
  points d'équité.
- Le dashboard ne remplace pas les scripts de mesure : backtests, balayages et rapports
  restent dans `backend/scripts/`.

## 9. Décisions liées

- D-036 — client mince, jeton côté serveur.
- D-037 — exposition réseau et TLS.
- D-044 — réglages d'exécution en base.
- D-045 — session de 7 jours.
- D-062 — historique en liste blanche et plafonds de risque en lecture seule.


### Mémoire d’expérience

Dans **Décisions & IA**, le panneau Mémoire d’expérience montre le dernier passage
du service, ses motifs d’attente, les situations enregistrées, les résultats
exploitables, les simulations ouvertes, les cas écartés et les versions de la
mémoire. La route privée `GET /api/insights/learning` est uniquement en lecture.
Un passage trop ancien ou une actualisation en panne ne reste pas présenté comme
courant. L’interface n’envoie aucune commande d’apprentissage ni de trading.

Les lectures HTTP expirent après dix secondes. Un statut, des statistiques, des
réglages ou une mémoire incomplets conservent les dernières valeurs valides avec
un avertissement ; un statut sans champ `position` n’efface pas une position
ouverte. Un PnL inconnu et les ratios sans perte de référence affichent « — ».
Le profit factor utilise les gains nets divisés par les pertes nettes absolues :
les frais sont déjà inclus dans ces résultats.

Les plans de simulation incohérents sont écartés sans PnL. Une panne de cours
laisse les lignes concernées en attente et n’empêche pas les autres marchés
d’être évalués ; ce passage échoue sans publier de nouvelles leçons.
