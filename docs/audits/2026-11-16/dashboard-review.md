# Dashboard : évaluation et refonte du 16 novembre 2026

Revue du code initial `8749368`, des cinq espaces et des parcours navigateur.
Référence de lecture : [Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).
Les constats ci-dessous décrivent la version initiale ; les corrections sont dans
la candidate v3. Aucun changement du moteur, du risque, du capital ou du schéma.

## Évaluation

Le socle est utile : administration authentifiée, JWT conservé côté serveur,
confirmations des commandes sensibles, textes API rendus sans HTML, limites
explicites et positions multiples. L’interface masque toutefois trop d’informations
opérationnelles derrière les écrans d’analyse et privilégie la courbe et les chiffres.

Constats sur la version initiale, au format fichier:ligne :

- `dashboard/public/index.html:37` — l’accueil donne capital et performance mais
  n’explique pas les motifs observés des achats non aboutis. Correction : synthèse
  de situation, compteurs séparés et distribution des motifs sur sept jours.
- `dashboard/public/index.html:185` — surveillance indépendante enfouie dans un
  volet. Une connexion API et un cycle complet ne prouvent pas que le moteur est
  actuellement surveillé. Correction : indicateur distinct, preuve expirante et
  état inconnu conservé lorsque la preuve manque.
- `dashboard/public/style.css:79` — plusieurs informations essentielles en 8–10 px.
  Correction : textes utiles généralement en 12–14 px, meilleurs contrastes,
  chiffres tabulaires, police IBM Plex Sans locale, contrôles tactiles agrandis.
- `dashboard/public/app.js:1611` — un verdict absent est rendu comme un refus.
  Correction : seules les valeurs booléennes donnent une acceptation ou un refus.
- `dashboard/public/app.js:1752` — une erreur de lecture efface le journal.
  Correction : conservation du dernier lot valide du même onglet, de sa date et
  du filtre ; avertissement explicite sur une réponse invalide ou indisponible.
- `dashboard/public/app.js:1825` — l’export reconstruit les cellules affichées ;
  les arrondis et l’absence d’identifiants limitent le rapprochement comptable.
  Correction : export des sources filtrées, décimales exactes, identifiants et
  devises des frais ; neutralisation des formules de tableur conservée.
- `dashboard/server.js:420` — le polling SSE peut lancer un appel avant la fin
  du précédent. Correction : un seul appel par flux. Même protection pour les
  lectures manuelles du statut côté navigateur.
- `dashboard/public/insights.js:48` — observations lues seulement à l’ouverture
  ou sur demande. Correction : relecture toutes les 30 secondes, validation avant
  rendu et conservation des informations pendant une panne. Les parcours déjà
  ouverts ne sont pas reconstruits lorsque la liste des cycles ne change pas.

## Valeur de la page Activité

C’est un journal d’audit opérationnel utile pour comprendre les faits enregistrés
et investiguer un incident. Sa valeur augmente lorsque l’on peut répondre à une
question concrète : quelle intention, quel verdict, quel ordre, quelle position ?

La v3 ajoute :

- explication de la portée de chaque onglet et compteurs du lot chargé ;
- recherche dans les lignes chargées, export suivant le filtre et options dans
  l’URL, par exemple `/?table=orders&limit=10&q=BTC#activity` ;
- signalement des ordres inconnus, partiels ou rejetés, sans appeler tous les refus
  de risque des incidents ;
- inspection des sources et de leurs identifiants, puis diagnostic de la position
  liée sans recopier manuellement un UUID ;
- quantités/prix du journal conservant les dix décimales possibles du schéma,
  dates avec année, secondes et fuseau, devise des frais explicite ;
- prix moyen non présenté comme exécuté lorsque la quantité exécutée est nulle
  ou inconnue ; absence de données distinguée d’un montant nul.

## Limites conservées

- Les 10, 25 ou 50 dernières lignes ne sont pas un historique exhaustif. Pas de
  pagination historique ni d’analyse de performance globale dans cette vue.
- Les trois compteurs de chaque onglet portent sur le lot avant recherche ; ils
  ne constituent pas une ventilation exhaustive de tous les états possibles.
- Un BUY du modèle est une intention, une validation de risque une autorisation,
  et un ordre FILLED une exécution. Aucun de ces événements seul ne prouve un gain.
- Le résumé d’accueil décrit sept jours d’événements observés. Il annonce les
  égalités, l’absence d’échantillon et la collecte tronquée. Un motif fréquent ne
  devient pas le motif certain du prochain cycle. Les minima ne déclenchent aucun
  conseil d’apport ni relèvement du risque.
- Le statut du compte n’expose pas d’horodatage source complet. La date de
  réception est donc présentée comme telle ; elle n’est pas une date de marché ou
  une preuve de vie du moteur. La preuve de surveillance utilise `valid_until`.
  C-05 n’est pas déclaré intégralement qualifié par cette refonte.
- Les contrôles navigateur utilisent des réponses factices. Ils valident le rendu
  et les interactions, pas la rentabilité, le fonctionnement de l’observateur WSL
  ni les effets financiers de commandes sur la cible.

## Vérifications

- `npm test` dans `dashboard/` : **33 tests réussis**, dont un appel SSE lent
  bloqué pendant plusieurs périodes de polling, sans lecture concurrente.
- `scripts/check_dashboard_browser.py /tmp/tn-dashboard-v3`, sous Python 3.13.17,
  Playwright et Chromium : cinq espaces, largeurs 320/390/768/1440, clavier du
  graphique, navigation mobile, diagnostics, TOTP, rejeu et scénarios de panne.
  Cas ajoutés : minima, collecte tronquée, observations incomplètes, expiration
  de surveillance, journal invalide conservé, recherche, décimales, CSV filtré,
  diagnostic depuis un ordre, options en lien direct, police locale et réponse
  HTTP antérieure à un événement SSE. Captures dans `/tmp/tn-dashboard-v3/`.
- `DATABASE_URL=<PostgreSQL 17 Docker jetable local> bash scripts/check.sh` :
  **TOUT EST VERT**, **916 tests Python hors base**, 65 contrats opt-in collectés
  mais non exécutés par cette commande, **33 Node**, quinze migrations et index
  conformes. Aucun contrat de cette recette ne vise Supabase.
- Syntaxe JavaScript et `git diff --check` : réussis.
- `deploy/enable-multiple-positions-wsl.sh --check` : **110 fichiers** figés,
  dont la synthèse et les polices/licence. Ce contrôle n’installe pas la candidate
  à positions multiples et ne redémarre pas le moteur.
- Sur le dashboard existant en 8081, processus relancé depuis le dépôt, puis
  contrôle HTTP dans une commande séparée : login, scripts, CSS et polices **200**,
  CSP inchangée, API privée **401** sans session. API et moteur conservent leurs
  PID. Le lanceur nohup n’a pas tenu à la fin de sa commande ; le dashboard a été
  relancé dans une session PTY persistante. Une supervision systemd durable du
  dashboard reste à qualifier ; l’installation moteur est distincte.
