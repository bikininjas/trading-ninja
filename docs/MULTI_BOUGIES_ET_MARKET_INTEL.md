# Boussole multi-bougies, résilience et MarketIntel

Livraison du 16 novembre 2026. Le défaut reste **EMA200 4h**, évaluation **4h**.
Les migrations jusqu’à **0017** sont appliquées sur Supabase. 0017 ajoute les
titres/extraits, liens, indice quotidien et réponse finale conservés par analyse.
La release `paper-market-intel9b-ac2171a16feb` est installée ; MarketIntel est
actif sous systemd avec un LOGIN sans droit d’ordre et un premier digest OK.
Selon la précision de Seb (D-098), trading et veille utilisent **qwen3.5:9b**.
Aucun modèle n’a été téléchargé, aucun capital réel activé.

## Configuration

Les paramètres sont dans `app/core/settings.py` et `RiskConfig`, en suivant les
couches existantes. Aucun nouveau `app/config/settings.py` concurrent.

| Paramètre | Défaut | Valeurs |
|---|---|---|
| `MACRO_TIMEFRAME` | `4h` | `1h`, `4h`, `1d` |
| `MACRO_EMA_PERIOD` | `200` | Entier 2 à 500 ; exemples 50, 100, 200 |
| `EVAL_TIMEFRAME` | `4h` | `1h`, `4h`, `1d` |
| `MARKET_INTEL_ENABLED` | `false` | Lecture du contexte public daté par le moteur |
| `MARKET_INTEL_MODEL` | `qwen3.5:9b` | Modèle du worker, indépendant de `LLM_MODEL_NAME` |

`EVAL_TIMEFRAME` est prioritaire sur l’alias historique `DECISION_TIMEFRAME`.
Le preset `swing` choisit macro et évaluation `1d` ; le mode `SWING_MACRO_D1`
conserve sa cadence quotidienne. `ENTRY_TIMEFRAME` reste le flux auxiliaire H1
historique ; ce n’est ni la cadence ni la boussole macro.

Un fichier `.env` rechargé ou une révision administrative validée peut changer
la boussole entre deux cycles. Le scheduler vérifie les changements pendant son
attente, toutes les 30 secondes au maximum. Un changement de cadence est
**différé tant qu’une position est ouverte**, avec motif dans l’accusé de révision.
Les positions existantes gardent leur politique macro immuable. Mode d’exécution,
identités SQL, clés exchange et modèle de trading ne changent pas à chaud.
Les révisions SQL continuent de surcharger le fichier de démarrage.

Le dashboard expose les trois réglages seulement lorsque l’API renvoie leur
contrat. Une ancienne API ne reçoit pas de nouveaux champs silencieusement ignorés.
La prévisualisation distingue paramètres des prochaines positions et cadence.

## Horizons et données

H1-200 et H4-50 couvrent tous deux **200 heures nominales**. Ils n’ont pas la même
EMA : clôtures et coefficient exponentiel diffèrent, et une EMA n’est pas une
fenêtre rectangulaire finie. H4-100 couvre 400 heures nominales, H4-200 800 heures,
D1-200 200 jours. Aucune égalité de rendement ou de faux rebonds n’en découle.

Le feed charge chaque unité nécessaire une seule fois par snapshot, avec la
profondeur maximale requise par les usages de cette série. Il ne lit que les
bougies clôturées. La pente macro utilise dix jours de sa propre série (240 H1,
60 H4, 10 D1), normalisée par l’ATR de cette série. L’ATR d’exécution reste celui
de l’évaluation : la volatilité de dimensionnement ne vient pas du modèle.

Le snapshot expose `macro_timeframe`, `macro_ema_period`, `macro_ema_value`,
`macro_ema_slope_atr`, `macro_ema_threshold_with_margin` et `eval_timeframe`.
Les anciens noms `*_h4` restent des alias de compatibilité pour les indicateurs
de décision ; les nouveaux prompts indiquent explicitement l’unité active.
Le prompt par défaut reste identique ; les variantes reçoivent une identité
`v4-mtf` et une empreinte de template correspondant au texte réellement envoyé.

Une période acceptée par la configuration ne garantit pas assez d’historique.
Kraken REST plafonne à 720 bougies : notamment, une très grande EMA H1 peut
manquer de recul pour calculer sa pente. Le feed refuse un contexte incomplet.
La fraîcheur du prix courant est toujours revalidée avant un achat.

## Retry et cache SQL

Chaque `fetch_ohlcv` tente **trois appels au total**, sur `ccxt.NetworkError`
(dont `RequestTimeout`) et `httpx.TransportError`. Les attentes entre appels sont
**1 s puis 2 s**, avec plafond exponentiel à 4 s ; dormir 4 s après le dernier
échec ne créerait pas de quatrième tentative et ne retarde donc pas le cache.
Les erreurs fonctionnelles ne sont pas retentées.

Après épuisement, `market_candles_history` est lu avec filtre de clôture, ordre
décroissant et `LIMIT` SQL, puis remis dans l’ordre chronologique. Cache vide,
futur, discontinu, OHLCV incohérent ou dernière clôture vieille d’une période
entière : refus. Le `WARNING` `market_candles_sql_fallback` porte symbole,
unité, provenance, âge et nombre de tentatives. Les dates sources restent celles
du cache. Les accès réseau synchrones sont déportés hors de la boucle asyncio.

Le cache ne devient pas frais par son seul usage : l’ingestion doit l’avoir
alimenté récemment. Le moteur ne reçoit aucun nouveau droit d’écriture sur
l’historique. Si Kraken entier est indisponible, ce repli OHLCV ne remplace ni un
prix courant valide ni les confirmations nécessaires à une exécution.
Les erreurs et lectures SQL de repli sont enregistrables dans le rejeu I/O ;
aucune nouvelle requête ou inférence ne doit apparaître au rejeu.

## Veille publique

Le worker `backend/scripts/market_intel.py --serve` est distinct du moteur. Il
préchauffe le 9B existant avec le mécanisme du moteur (budget 180 s) avant
chaque collecte horaire, sans téléchargement ni changement de modèle. Il
collecte toutes les 60 minutes CoinDesk, Cointelegraph et Fear & Greed, avec
budget réseau de 5 secondes par source, collecte concurrente, corps limités à
1 Mo, flux XML sans DTD/entités, dates valides sur les 12 dernières heures,
déduplication SHA-256 des titres normalisés. L’indice Fear & Greed est quotidien ;
il reste un contexte de sentiment, pas une mesure propre à chaque altcoin.
Les dates sont normalisées en UTC avant le tri et la limite de 80 titres ; pour
un titre répété, la version à la date de publication la plus récente est retenue.
Un horodatage externe hors plage dégrade la collecte au même titre qu'une source invalide.

L’inférence native Ollama `/api/chat` utilise un schéma JSON, un budget de
5 secondes et Qwen 3.5 9B avec `think=false`. Le parseur valide sentiment, score fini dans [-1,1],
booléen strict, actifs configurés et résumé français demandé de 240 caractères.
Les textes externes sont des données non fiables, jamais des instructions.
Tous les titres et descriptions collectés sont transmis ; hashes, provenance et
dates restent dans l’audit et ne saturent plus le contexte du 9B. Une sortie
tronquée ou invalide reste dégradée, sans récupération permissive du JSON.
L’indication systémique est réservée par le prompt aux hacks majeurs, faillites
d’exchange, arrêts réseau et depegs ; elle reste une classification du modèle,
pas une preuve certaine de l’événement.

Source manquante, modèle absent, erreur, dépassement ou sortie invalide :
**NEUTRAL / DEGRADED**, sans nouvelle information validée. Le moteur n’attend
jamais cette collecte ou cette inférence ; il lit les résultats stockés.

`0016_market_intel.sql` ajoute l’ENUM `REJECTED_INTEL_SYSTEMIC_RISK` et la table
privée. Aucun changement des quinze migrations précédentes. Un LOGIN privé
membre uniquement de `tn_market_intel_writer` écrit les digests ; ce groupe
NOLOGIN n’a aucun droit d’ordre ou de position. `anon` et `authenticated` n’ont
aucun accès. Le moteur/API PAPER ont une lecture privée seulement.

`store_within_budget()` réutilise le verdict de quota D-057, mesure la taille
réelle, réserve 64 Ko par insertion et protège **au moins 100 Mo libres**.
Rétention glissante de 30 jours ; purge avant la tentative d’écriture. Le verrou
transactionnel sérialise les insertions de veille. Depuis 0017, les digests conservent les titres et extraits RSS bornés
(500/1000 caractères), liens HTTPS vérifiés, dates, hashes et réponse finale
validée (4096 octets maximum). Aucun article complet n’est téléchargé.
Le quota réserve la taille sérialisée des sources plus une marge ; le minimum
de réservation de 64 Ko est conservé. La rétention de 30 jours purge aussi
ces sources, sans nouvelle table publique ni élargissement de droits.
La validité est de deux heures ; un repli neutre hors ligne ne masque pas la
dernière alerte encore valide. Une alerte future ou expirée ne vaut pas contexte.

Le filtre technique reste prioritaire. Une news positive ne force jamais un
achat. Sur un BUY techniquement admissible, une alerte valide globale (liste vide
ou `ALL`) ou visant le ticker bloque l’achat avec le nouveau code. Le score et le
résumé enrichissent le prompt ; aucun nombre de la veille ne dimensionne un ordre.
Le veto est recontrôlé avec l’horloge du dernier prix, y compris son expiration.

## Mise en route distincte de la qualification du code

0016 et le LOGIN isolé sont installés. Le worker refuse une identité `postgres`,
un compte sans groupe de veille ou doté de droits financiers. Ne jamais copier
ses identifiants dans la configuration du moteur. Le script de mise à jour,
vérifié puis exécuté par Seb avec sudo, conserve capital, risque, univers,
nombre de positions et politique de reprise du moteur :

```bash
bash deploy/enable-market-intel-wsl.sh --check
sudo bash deploy/enable-market-intel-wsl.sh
```

Le worker systemd `trading-ninja-market-intel.service` est actif et activé au boot.
Le moteur conserve `Restart=no` et son état d’activation au boot précédent.
Les réglages privés sont dans `/etc/trading-ninja/`, mode 0600 ; le LOGIN de veille
n’est membre que de `tn_market_intel_writer`. Pour une exécution manuelle séparée :

```bash
cd backend
# Avec un fichier privé déjà préparé, identité de veille isolée :
.venv/bin/python scripts/market_intel.py --config /chemin/prive/market-intel.env --once
.venv/bin/python scripts/market_intel.py --config /chemin/prive/market-intel.env --serve
```

La collecte HTTP réelle a trouvé 25 articles et un indice disponible. Après
préchauffage et réduction des métadonnées du prompt, le 9B produit un digest OK,
persisté avec le LOGIN isolé. Le service a confirmé ce résultat après démarrage.
Cette preuve d’exécution ne qualifie ni l’exactitude des nouvelles ni une veille
longue. Le 14B, absent lors de D-097, n’est plus requis selon D-098.
La comparaison rapide et ses limites sont dans `BACKTEST.md` ; les preuves de
livraison, schéma et services sont dans `JOURNAL.md`.

Sources : [Kraken OHLC](https://docs.kraken.com/api-reference/market-data/get-ohlc-data),
[Ollama chat](https://docs.ollama.com/api/chat),
[Alternative.me](https://alternative.me/crypto/fear-and-greed-index/),
[CoinDesk RSS](https://www.coindesk.com/arc/outboundfeeds/rss),
[Cointelegraph RSS](https://cointelegraph.com/rss).

La cadence acquittée par le moteur est publiée dans `applied_payload`. Le
surveillant et la couverture théorique de l’API la relisent ; une nouvelle
cadence différée par une position ouverte ne remplace pas celle appliquée.

## Dashboard : Veille & IA (D-099)

L’espace `http://localhost:8081/#intel` associe chaque analyse aux sources
collectées pour elle. Le filtre par éditeur et la recherche restent locaux.
Les liens s’ouvrent vers CoinDesk/Cointelegraph en HTTPS ; les contenus RSS et
les réponses sont rendus comme texte. Aucune image distante n’est chargée.

Le volet Qwen affiche sentiment, score, synthèse, actifs, modèle, dates, indice
Fear & Greed et réponse finale JSON. Le menu des douze analyses récentes permet
une relecture datée (`?digest=<UUID>#intel`). L’état du veto porte toujours sur
la dernière analyse valide, même lorsqu’une archive est sélectionnée. Il expire
localement à son échéance ; une erreur de lecture le rend non confirmé, sans
effacer les derniers titres. Les replis dégradés sont distingués du modèle.

Les analyses 0016 n’ont que des hashes : leur absence de titres/réponse brute
est indiquée, sans remplacement par des articles récents. Les titres collectés
sans analyse valide restent des sources collectées, pas un avis du modèle.

Les dix derniers signaux de trading sont séparés de la veille : dates et thèse,
action retenue, réponse initiale du modèle et contexte transmis. Les contrôles
peuvent modifier l’action retenue. Une absence de nouveau signal peut simplement
venir du filtre technique ; la page ne lance aucune inférence ni ordre.

API privée : `GET /api/insights/market-intel?digest_id=<UUID>` ; lecture SQL
bornée à douze résumés et aux sources du digest sélectionné/actif, délai 5 s.
Actualisation de la vue visible toutes les 60 s. Le déploiement dédié est
`deploy/enable-market-intel-news-wsl.sh` : il met à jour worker/API/dashboard
et prépare la release du prochain démarrage moteur, en conservant le processus
moteur en cours et ses paramètres. Les migrations restent un geste distinct.
