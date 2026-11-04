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
  test/server.test.js    18 tests (session, proxy, panic, SSE, origine)
```

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
| `API_BASE_URL` | `http://127.0.0.1:8080` | adresse de l'API FastAPI |
| `DASHBOARD_PORT` | `8081` | port d'écoute |
| `DASHBOARD_HOST` | `127.0.0.1` | interface ; ne pas exposer sans TLS |
| `DASHBOARD_POLL_MS` | `2000` | fréquence de rafraîchissement |
| `DASHBOARD_COOKIE_SECURE` | `false` | `true` derrière HTTPS |

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
  qu'un cookie de session `HttpOnly`, `SameSite=Strict`, `Path=/`, 15 minutes.
  Un XSS dans la page ne peut pas lire le jeton qui permet de liquider.
- Le cookie contient un identifiant aléatoire (128 bits), pas le jeton.
- Les commandes POST vérifient l'en-tête `Origin` (same-origin uniquement).
- CSP stricte (`default-src 'self'`, pas d'inline), `X-Frame-Options: DENY`,
  `nosniff`, `Referrer-Policy: no-referrer`.
- Le panic-sell redemande un code TOTP **frais** : une session volée ne suffit pas.
- Aucune donnée n'est insérée avec `innerHTML` : tout passe par `textContent`.
- Le serveur écoute sur `127.0.0.1` par défaut. Pour un accès distant, mettre un
  reverse proxy authentifié avec TLS.

## Ce que la page affiche

- état de la machine (`IDLE`, `IN_POSITION`, `LOCKED…`, `EMERGENCY_HALT`) ;
- équité, disponible, réserve, drawdown depuis le plus haut ;
- position ouverte : entrée, quantité, stop, take-profit, risque ;
- cinq derniers signaux du LLM (heure, action, confiance, régime, thèse) ;
- commandes pause, reprise et panic-sell.

Le flux est du SSE (`/events`) : le serveur interroge l'API toutes les 2 secondes et
pousse l'état au navigateur. Le WebSocket de l'API reste disponible pour d'autres
clients ; le navigateur n'a besoin d'aucune bibliothèque.

## Tests

```bash
npm test          # ou : node --test test/
```

Les tests démarrent une fausse API sur un port éphémère et vérifient le contrat du
dashboard : redirection sans session, cookie HttpOnly, proxy du statut, refus
d'origine étrangère, panic sans code, transmission du TOTP, SSE, expiration.
