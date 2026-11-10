# Dashboard — supervision Trading Ninja

Client mince de l'API d'administration. **Zéro dépendance** : `node:http`, `fetch`
natif et `node:test`. Node ≥ 20 suffit.

```
dashboard/
  server.js              serveur HTTP, sessions, proxy API, SSE
  public/index.html      page de supervision
  public/login.html      page de connexion (mot de passe + code Authy)
  public/app.js          rendu et commandes (aucun innerHTML avec des données)
  public/style.css       thème sombre, sans framework
  public/favicon.svg     icône locale, aucune requête externe
  test/server.test.js    session, proxy, historique, panic, SSE, origine
  test/insights.test.js  routes privées d’observation et contrôles
```

Documentation détaillée : [`docs/DASHBOARD.md`](../docs/DASHBOARD.md).

## Ce que le dashboard montre

- l'état du bot en clair (« En veille », « Position ouverte », « Arrêt d'urgence »),
  le mode (`PAPER` / `TESTNET` / `LIVE`) et la fraîcheur du flux ;
- le capital total, l'argent disponible, la réserve et la baisse depuis le plus haut ;
- le gain/perte cumulé, les transactions, les pertes, le taux de réussite, le multiple
  R et les frais ;
- cinq jauges de risque : drawdown jour, semaine, depuis le plus haut, pertes
  consécutives et ordres sur une heure, comparées à leurs plafonds ;
- la position ouverte, son stop, son take-profit et le risque ;
- une courbe du capital avec périodes 24 h / 7 j / 30 j / tout, infobulle au survol,
  plus haut et plus bas de la période ;
- un histogramme du gain/perte par jour, avec une infobulle par barre ;
- l'état du modèle : dernière décision, conviction, temps de réponse, répartition
  Achat/Vente/Attente/Arrêt, replis techniques et dernière thèse ;
- les refus du pare-feu, comptés et libellés en français, avec leur part relative ;
- les achats non pris : refus du pare-feu, HOLD sur setup, plans finançables,
  résultat simulé des shadow trades et lignes en attente ;
- les leçons actives et la version qui les a produites ;
- l'activité récente en onglets : signaux, évaluations du pare-feu, ordres et
  positions, avec 10/25/50 lignes et rafraîchissement manuel.

## Réglages

Le panneau « Réglages » écrit dans la table `runtime_settings` (une ligne par mode).
Le bot relit cette ligne à chaque cycle et applique les changements à la boucle
suivante. Le dashboard ne touche jamais à `.env` ni à `.env.local`.

Les libellés sont volontairement sans jargon : « combien le bot peut mettre sur un
achat », « quand le bot s'arrête pour la journée », « niveau de conviction minimum ».
Les valeurs sont des fractions côté API (`0.50` = 50 %) et des pourcentages côté écran.

Les plafonds **hebdomadaire**, **depuis le plus haut** et de **pertes consécutives**
sont affichés en lecture seule : le dashboard les montre pour expliquer une décision,
mais ne permet pas de désarmer un coupe-circuit permanent d'un clic. Un bouton
« Recharger » relit les réglages sans quitter la page.

Un arrêt demandé par le modèle (`HALT`) liquide la position puis met le bot en
`EMERGENCY_HALT` avec une échéance de 24 h. Le dashboard affiche le compte à rebours
et le bouton « Reprendre maintenant » permet de repartir avant. Un panic-sell humain
n'a pas d'échéance : il exige une reprise explicite.

## Démarrage

L'API FastAPI doit tourner et être configurée (voir `backend/README.md`).

Depuis la racine du dépôt :

```bash
scripts/manage.sh start all      # API puis dashboard, en arrière-plan
scripts/manage.sh status
scripts/manage.sh logs all
scripts/manage.sh stop all
```

Ou composant par composant : `scripts/manage.sh start api`, `start dashboard`,
`restart dashboard`. Sans le script :

```bash
cd backend && .venv/bin/python -m uvicorn app.api.main:create_app --factory \
    --host 127.0.0.1 --port 8080
cd dashboard && node server.js     # → http://127.0.0.1:8081
```

Variables d'environnement :

| Variable | Défaut | Rôle |
|---|---|---|
| `API_BASE_URL` | `http://[IP_ADDRESS]:8080` | adresse de l'API FastAPI |
| `DASHBOARD_PORT` | `8081` | port d'écoute |
| `DASHBOARD_HOST` | `[IP_ADDRESS]` | interface ; ne pas exposer sans TLS |
| `DASHBOARD_POLL_MS` | `2000` | fréquence de rafraîchissement |
| `DASHBOARD_COOKIE_SECURE` | `false` | `true` derrière HTTPS |

Les réglages métier (prudence, part du capital, seuil journalier, conviction,
reprise automatique) ne sont **pas** des variables d'environnement du dashboard :
ils vivent dans `runtime_settings`, côté base, et sont modifiés par le panneau
« Réglages ».

## Exposition réseau

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

## Modèle de sécurité

- Le jeton JWT de l'API reste **dans le processus Node**. Le navigateur ne reçoit
  qu'un cookie de session `HttpOnly`, `SameSite=Strict`, `Path=/`, 7 jours.
  Un XSS dans la page ne peut pas lire le jeton qui permet de liquider.
- Le cookie contient un identifiant aléatoire (128 bits), pas le jeton.
- Les commandes POST vérifient l'en-tête `Origin` (same-origin uniquement).
- CSP stricte (`default-src 'self'`, pas d'inline), `X-Frame-Options: DENY`,
  `nosniff`, `Referrer-Policy: no-referrer`.
- Le panic-sell redemande un code TOTP **frais** : une session volée ne suffit pas.
- Aucune donnée n'est insérée avec `innerHTML` : tout passe par `textContent`.
- L'historique relayé est limité à une **liste blanche** (`llm_signals`,
  `risk_evaluations`, `orders`, `positions`) et à 50 lignes par requête. Un nom de
  table inconnu est refusé avant d'appeler l'API.
- La CSP interdit les scripts et styles inline : les graphiques sont construits en
  SVG, les jauges en CSSOM, sans dépendance externe.
- Le serveur écoute sur `127.0.0.1` par défaut. Pour un accès distant, mettre un
  reverse proxy authentifié avec TLS.

## Flux et historique

Le flux est du SSE (`/events`) : le serveur interroge l'API toutes les 2 secondes et
pousse l'état au navigateur. Le WebSocket de l'API reste disponible pour d'autres
clients ; le navigateur n'a besoin d'aucune bibliothèque.

Les statistiques lourdes sont rechargées toutes les 15 s. L'historique détaillé
(onglets d'activité) n'est pas poussé : il est demandé à l'ouverture de l'onglet, au
changement de limite ou sur « Rafraîchir ». Une requête plus récente annule
l'affichage d'une requête plus ancienne, pour éviter qu'un onglet revienne en arrière.

Limites assumées : 50 lignes d'historique par vue, 200 points d'équité pour la courbe,
14 jours pour l'histogramme. Le dashboard supervise ; il n'exporte pas la base.

## Tests

```bash
npm test          # ou : node --test test/
```

Les 31 tests démarrent une fausse API sur un port éphémère et vérifient le contrat du
dashboard : redirection sans session, cookie HttpOnly, proxy du statut, historique
borné et liste blanche, refus d'origine étrangère, panic sans code, transmission du
TOTP, SSE, expiration. `bash scripts/check.sh` ajoute la syntaxe, l'index du code et la
cohérence documentaire.

Le contrôle navigateur bureau/mobile est exécuté en CI via
`scripts/check_dashboard_browser.py`, avec réponses API factices. Il ne valide
pas l’installation cible. État courant et portes : [GO_LIVE.md](../docs/GO_LIVE.md).
