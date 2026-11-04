# Décisions techniques — Trading Ninja

Une décision = un numéro, jamais réutilisé, jamais supprimé. Si une décision est
remplacée, on en crée une nouvelle qui référence l'ancienne (ex. « remplace D-004 »).
Le but n'est pas d'archiver pour archiver : c'est de pouvoir répondre dans six mois à
« pourquoi c'est fait comme ça ? » sans relire le code.

---

## D-001 — Partitionnement natif au lieu de TimescaleDB

**Contexte** : les specs imposent des hypertables TimescaleDB pour les tables de
télémétrie (`llm_signals`, `risk_evaluations`).
**Constat** : la base cible est Supabase en PostgreSQL 17, où l'extension `timescaledb`
n'est plus disponible. `CREATE EXTENSION` échoue et toute la migration tombe.
**Décision** : `PARTITION BY RANGE (created_at)`, une partition par mois créées 6 mois
d'avance, plus une partition `DEFAULT` par table et une fonction
`ensure_partitions()`.
**Pourquoi** : c'est du PostgreSQL standard, portable, sans dépendance exotique. La
partition `DEFAULT` garantit qu'un `INSERT` ne peut jamais échouer même si la
maintenance a pris du retard — un journal d'audit qui refuse d'écrire arrête le bot.
**Écarté** : garder TimescaleDB (impossible), ou `pg_partman` (disponible, mais ajoute
une couche de configuration pour un besoin que 20 lignes de SQL couvrent).

---

## D-002 — Le LLM ne peut que bloquer, jamais déclencher

**Décision** : le pare-feu ne lit que trois champs du modèle — `action`, `confidence`,
`market_regime` — et tous les trois ne peuvent que produire un refus. Aucune valeur
numérique émise par le modèle (taille, prix, levier, stop) n'est utilisée.
**Pourquoi** : un modèle de 14B quantifié n'a pas d'avantage statistique démontré sur
le marché. La taille et le stop doivent être calculés, pas suggérés. Un champ numérique
halluciné est exactement le scénario qui vide un compte.
**Conséquence assumée** : `SELL` et `HOLD` sont rejetés avec `ACTION_IS_NOT_BUY`. Le
moteur est long-only spot ; le LLM n'a aucun pouvoir de sortie. Les sorties sont le
stop, le take-profit, ou l'intervention humaine.

---

## D-003 — Stop calculé sur l'ATR du timeframe de décision

**Contexte** : les specs calculaient le stop sur l'ATR H1 alors que la décision se prend
sur H4.
**Décision** : `stop = 2,5 × ATR14(H4)`, soit un stop aligné sur l'horizon de décision.
**Pourquoi** : un stop calibré sur une heure est à l'intérieur du bruit normal de quatre
heures. La position est alors fermée par le hasard, pas par une erreur d'analyse, et le
compte saigne les frais plus une série de petites pertes. C'est le poste de perte le
plus coûteux d'une spec écrite par quelqu'un qui ne trade pas.

---

## D-004 — Trois plafonds de dimensionnement concurrents

**Décision** :
```
amount = min(
    (équité × risque%) / distance_au_stop,     # budget de risque
    min(équité × 20 %, plafond absolu 2000) / prix,  # exposition
    (solde_libre × (1 − 0,2 %)) / prix,        # cash réel
)
puis troncature au pas de quantité de l'exchange
```
**Pourquoi** : le plafond absolu n'est pas redondant avec le pourcentage — il borne un
bug de dimensionnement à un montant connu au lieu d'une fraction d'un gros compte.
La troncature est toujours vers le bas : on ne dépasse jamais, on reste en dessous.
**Conséquence à dire à voix haute** : avec ces réglages, le risque effectif atterrit
souvent entre 0,2 % et 0,4 % au lieu du budget théorique (0,5 % en profil `prudent`).
C'est le plafond qui travaille, pas un défaut. Les deux valeurs (`intended_risk_quote` et
`effective_risk_quote`) sont persistées pour que le ledger ne mente pas.

---

## D-005 — Profil prudent par défaut, risque en configuration

**Décision** : presets `prudent` / `standard` / `aggressive` sélectionnés par une
variable d'environnement, chaque réglage surchargeable individuellement. Plafond dur :
`risk_per_trade_pct ≤ 5 %`, refusé au démarrage au-delà.
**Pourquoi** : faire évoluer le risque doit être une édition de `.env`, jamais une
modification de code. Et un plafond dans le constructeur de configuration fait échouer
une faute de frappe au démarrage plutôt qu'à l'exchange.

---

## D-006 — Take-profit défini et persisté

**Contexte** : la machine à états prévoyait une sortie « take-profit atteint », mais
aucune formule n'était donnée et la table `positions` n'avait pas de colonne pour le
stocker.
**Décision** : `TP = entrée + 2 × distance_au_stop` (rapport 1:2), calculé à l'entrée,
écrit dans `positions.take_profit_price`.
**Pourquoi** : un take-profit non défini signifie des positions qui ne se ferment que
sur le stop — donc une stratégie qui ne peut structurellement pas gagner.

---

## D-007 — Argent en `Decimal`, jamais en flottant

**Décision** : `NUMERIC(28,10)` en base, `Decimal` de bout en bout, `Decimal(str(v))`
pour convertir ce qui vient du JSON ou du modèle.
**Pourquoi** : un flottant binaire ne représente pas 0,1 exactement. Sur des milliers
d'opérations, l'écart se voit dans la comptabilité et rend les rapprochements
impossibles.

---

## D-008 — Le DDL fait autorité sur les autres documents

**Contexte** : trois documents donnaient trois listes différentes de codes de rejet, et
la doc de la Risk Gateway décrivait une table qui ne correspondait pas au schéma SQL.
**Décision** : le DDL PostgreSQL est la référence unique ; les `StrEnum` Python le
mirent à l'identique. Les codes ajoutés depuis (ex. `REJECTED_LOW_VOLATILITY`) existent
des deux côtés.
**Pourquoi** : un pare-feu qui émet `REJECTED_COUNTER_TREND` dans une colonne typée
`REJECTED_COUNTER_TREND_EMA200` échoue à l'exécution sur une décision parfaitement
valide — c'est-à-dire au pire moment.

---

## D-009 — Mode papier par défaut et double verrou d'armement

**Décision** : `TRADING_MODE=paper` par défaut. Le mode réel exige **deux** gestes
explicites (`TRADING_MODE=live` **et** `I_UNDERSTAND_REAL_MONEY=true`), des clés
présentes, et refuse la combinaison `live` + sandbox.
**Pourquoi** : un déploiement accidentel ne doit pas pouvoir engager de l'argent. Le
coût d'un verrou est nul ; le coût d'un bot qui démarre en réel par surprise ne l'est pas.

---

## D-010 — Une seule position ouverte par symbole, garantie par la base

**Décision** : index unique partiel `ux_positions_one_open_per_symbol` sur
`(symbol, mode) WHERE status = 'OPEN'`.
**Pourquoi** : le monoposition était une règle du code. Une course entre deux workers,
ou un redémarrage mal réconcilié, pouvait la contourner. La contrainte au niveau du
moteur de stockage est vraie même quand le code se trompe.

---

## D-011 — Fermer l'accès API public de Supabase

**Contexte** : Supabase expose le schéma `public` à la clé publique via PostgREST.
**Constat mesuré** : les tables étaient protégées par la RLS, **pas les vues**. Une vue
s'exécute avec les droits de son propriétaire et contourne la RLS ; `v_system_health`
renvoyait une ligne à `anon`.
**Décision** : vues en `security_invoker = true`, révocation de tous les droits de
`anon` et `authenticated` sur tables, séquences et fonctions, `ALTER DEFAULT PRIVILEGES`
pour les objets futurs, RLS sur toutes les tables.
**Pourquoi** : ces tables ne sont destinées qu'au backend. Le frontend, s'il doit lire
des données, passera par l'API FastAPI — pas directement par PostgREST.

---

## D-012 — Nouveaux outils en espace utilisateur, jamais à la place du système

**Décision** : Python 3.15.2 et Node 26.13.0 installés dans `~/.local` (déjà en tête de
`PATH`), avec un lien `~/.pyenv/versions/3.15.2` vers l'interpréteur géré par uv.
`/usr/bin/python3` (3.12.3) n'a pas été touché.
**Pourquoi** : sur Ubuntu, `apt` et une partie des outils du bureau dépendent du Python
système ; le remplacer donne une machine difficile à réparer hors ligne. « Système »
signifie ici « disponible dans tous les shells », pas « remplace la copie de l'OS ».
**Point de fragilité à connaître** : le lien pyenv pointe vers l'installation uv. Un
`uv python uninstall 3.15.2` casserait le lien.

---

## D-013 — Alias acceptés pour les clés de l'exchange

**Décision** : `EXCHANGE_API_KEY`, `BINANCE_API_KEY` et `BINANCE_KEY` sont acceptés ;
idem pour le secret. Les noms explicites gagnent sur les alias.
**Pourquoi** : le fichier de secrets est écrit par Seb, pas par le code. Imposer un nom
oblige à éditer un fichier sensible à chaque changement d'exchange, et une variable
mal nommée échoue silencieusement — le bot tournerait sans clé et « ne trouverait
jamais d'opportunité », ce qui est le pire des diagnostics.

---

## D-014 — Paramètres de risque resserrés, et tracés

**Décision** : les valeurs par défaut s'écartent volontairement de la spec d'origine.
Drawdown journalier 3 % → **2 %**, pertes consécutives autorisées 3 → **4** (avec
étranglement du risque dès 2), plancher de confiance 0,75 → **0,80**, stop 1,5 × ATR →
**2,5 × ATR** (celui-là fait l'objet de D-003).

**Pourquoi** : 3 % de drawdown journalier est atteignable par simple variance en trois
trades ; 0,75 de confiance est le niveau où un modèle de 14B devine ; trois pertes
consécutives arrivent par hasard une fois sur huit à taux de réussite égal.
**La raison de cette entrée** : ces changements avaient été faits sans être consignés.
Un paramètre qui dérive sans trace rend toute analyse ultérieure incompréhensible.

---

## D-015 — `paper` et `testnet` refusent un sandbox désactivé

**Contexte** : le verrou d'armement ne couvrait que `live`. Une configuration
`TRADING_MODE=testnet` avec `EXCHANGE_SANDBOX_MODE=false` chargeait **sans aucune
erreur**, étiquetait les enregistrements `TESTNET`, et parlait à l'exchange réel. Le
ledger aurait mélangé des ordres réels dans les statistiques de test.

**Décision** : `paper` et `testnet` exigent `EXCHANGE_SANDBOX_MODE=true`. Le seul chemin
vers l'argent réel est `live` + `false` + `I_UNDERSTAND_REAL_MONEY=true` + clés présentes.
`execution_mode` ne dépend plus que du mode déclaré.

**Pourquoi** : une garantie qui ne couvre que le cas nominal n'est pas une garantie.
Trouvé par l'audit à froid, pas par les tests — d'où les trois tests ajoutés.

---

## D-016 — `bot_state` : une ligne par mode

**Contexte** : la table était un singleton unique. Passer de `PAPER` à `LIVE` héritait du
plus-haut d'équité et des verrous de la simulation : un HWM de 1000 USDT de simulation
face à un compte réel de 100 USDT produit un drawdown de -90 %, donc un verrou permanent
et un bot qui refuse tout trade sans explication.

**Décision** : clé primaire sur `mode`, trois lignes préexistantes, et `v_system_health`
filtre ses compteurs par mode.

**Pourquoi** : l'état d'exécution d'un mode n'a aucune signification dans un autre. Séparer
les lignes rend l'erreur impossible plutôt que de la documenter.

---

## D-017 — Le pare-feu ne lève jamais : `INTERNAL_ERROR`

**Contexte** : le module promettait « retourne toujours une décision », mais deux entrées
le faisaient lever — un horodatage naïf (soustraction naive/aware) et un `Decimal('NaN')`
(qui n'est jamais `<= 0`, donc traversait toutes les comparaisons).

**Décision** : validation explicite des fuseaux et des valeurs finies en entrée, plus une
enveloppe `try/except` autour du pipeline qui convertit toute exception imprévue en refus
`INTERNAL_ERROR`. Le code est ajouté à l'ÉNUM SQL par la migration 0003.

**Pourquoi** : une exception qui remonte jusqu'à la boucle principale peut être avalée
comme un `continue`, et laisser une position ouverte sans surveillance. Un refus explicite
est toujours préférable à un crash silencieux dans un système qui manipule de l'argent.

---

## D-018 — `apiRestrictions` fait autorité sur `canWithdraw`

**Contexte** : `GET /api/v3/account` renvoie `canWithdraw=true` pour le compte vérifié,
alors que `GET /sapi/v1/account/apiRestrictions` renvoie `enableWithdrawals=false` pour la
même clé. Les deux champs ne décrivent pas la même chose : le premier est une capacité du
**compte**, le second une permission de la **clé**.

**Décision** : le verdict de la sonde (`scripts/check_exchange.py`) lit
`apiRestrictions.enableWithdrawals` en priorité. `canWithdraw` n'est utilisé qu'en
l'absence de cet endpoint (testnet), et dans ce cas seulement comme signal de refus.

**Pourquoi** : la première version de la sonde condamnait la clé à cause de
`canWithdraw=true`. Un garde-fou qui crie au loup sur une configuration saine finit
désactivé par l'humain qu'il devait protéger — c'est exactement le mécanisme par lequel
les alarmes perdent leur autorité.

---

## D-019 — L'index du code est généré, jamais écrit à la main

**Contexte** : un index manuel (liste des modules, des classes, des tests) est exact le
jour où il est écrit et faux dès le commit suivant. Le dépôt a déjà payé ce prix avec des
nombres de tests périmés dans quatre documents.

**Décision** : `scripts/index_codebase.py` produit `docs/CODE_INDEX.md` depuis les
sources, avec une empreinte SHA-256 stable. `scripts/check.sh` section 6 exécute
`--check` et échoue si l'index n'a pas été régénéré après une modification.

**Pourquoi** : une règle qui vit dans un document ne survit pas au prochain contributeur ;
une vérification qui échoue, si. Le coût est d'une seconde par `check.sh`.

**Point de vigilance** : le compteur de tests vient de `pytest --collect-only`, pas d'un
comptage de `def test_*`. Un `parametrize` transforme une fonction en plusieurs cas ;
annoncer le nombre de fonctions au lieu du nombre de cas serait un index faux, donc
inutile.

---

## D-020 — Kraken : `stopLossPrice`, et bracket par `close`

**Contexte** : le brief de migration proposait
`type="stop-loss", params={"stopPrice": prix}`. Requête réellement construite par
CCXT 4.5.91 : `ordertype=stop-loss`, `volume`, et **aucun prix** — `stopPrice` est
recopié tel quel sans être lu.

**Décision** : le stop se construit avec `stopLossPrice`, et l'entrée utilise le
paramètre natif `close` de Kraken (`{"close": {"ordertype": "stop-loss", "price": …}}`)
pour que la protection soit attachée à l'ordre d'achat.

**Pourquoi** : un ordre `stop-loss` sans prix est au mieux rejeté, au pire une position
nue. Le bracket supprime la fenêtre entre l'achat et la pose du stop, qui est le moment
où une coupure réseau coûte le plus cher.

---

## D-021 — Marge de frais par exchange, pas une constante

**Contexte** : `fee_buffer_pct` valait 0,002, calibré sur Binance (0,10 % par côté).
Kraken prend 0,26 % par côté, soit 0,52 % l'aller-retour.

**Décision** : la valeur par défaut dépend d'`EXCHANGE_ID` — `0.002` pour Binance,
`0.0052` pour Kraken — et `FEE_BUFFER_PCT` reste prioritaire pour surcharger.

**Pourquoi** : une marge trop faible fait échouer le dernier achat pour quelques
centimes de frais, au moment où le signal est le meilleur. Le tarif maker est plus bas,
mais un bot qui entre au marché est taker : on budgète le pire des deux.

---

## D-022 — Permissions Kraken : `GetApiKeyInfo`, comparaison par jetons

**Contexte** : le brief exigeait de refuser une clé autorisant les retraits. Kraken
expose bien `GetApiKeyInfo` (sans permission préalable), mais renvoie des jetons
kebab-case — `query-funds`, `modify-trades`, `close-trades` — et non les libellés de son
interface.

**Décision** : `verify_key_permissions()` lit cet endpoint, refuse tout jeton
`withdraw`, et exige cinq droits par comparaison de **jetons exacts** (au pluriel près),
avec plusieurs formulations acceptées par exigence.

**Pourquoi** : la première version comparait des sous-chaînes ; `query-closed-trades`
satisfaisait alors l'exigence « annuler des ordres » parce que « closed » contient
« close ». Une clé sans droit de clôture passait le contrôle. Trouvé par un test, pas
par relecture.

**Conséquence opérationnelle** : la clé présente le 2026-11-04 portait `withdraw-funds`
et a été refusée — comportement attendu. Elle a ensuite été corrigée : plus de retrait,
plus de dépôt, allowlist d'IP active. Elle passe désormais.

**Piège de lecture** : Kraken renvoie `ip_allowlist` en snake_case, alors que sa
documentation montre `ipAllowlist`. Lire un seul des deux noms faisait croire à une clé
sans restriction d'IP alors que la restriction était bien en place. Le nom de champ est
une donnée d'API, pas une constante : les deux formes sont acceptées.

---

## D-023 — Pas de sandbox Kraken : le papier est interne, sur bougies clôturées

**Contexte** : CCXT n'expose aucune URL de test pour Kraken, et le brief demandait de
« écouter les WebSockets temps réel » en mode papier.

**Décision** : le mode `paper` reste entièrement interne (aucun appel d'ordre), et les
décisions continuent de se prendre sur des bougies **clôturées** H4/H1. Les WebSockets
pourront servir à l'affichage, jamais à la décision.

**Pourquoi** : une bougie en cours change jusqu'à sa clôture ; s'en servir rend le
comportement irreproductible entre backtest, papier et réel. C'est la propriété qui
donne sa valeur au pare-feu déterministe.

---

## D-024 — Profit sweeper Kraken : ledger virtuel, jamais de retrait

**Contexte** : le spot Kraken n'a pas de compartiment « funding » séparé comme Binance.
Déplacer réellement les 30 % demanderait une permission de retrait, que D-022 refuse.

**Décision** : option comptable. `profit_sweeps` enregistre l'écrémage avec
`transfer_status = 'SIMULATED'` et `to_account = 'virtual_reserve'`. La réserve est
`SUM(swept_amount)` par mode et devise, et le solde passé au pare-feu devient
`free_quote = solde_exchange − réserve`.

**Pourquoi** : le capital mis de côté doit être invisible pour le dimensionnement, mais
il n'a aucune raison de quitter l'exchange. Aucune migration de schéma n'est nécessaire :
les colonnes existent déjà.

---

## D-025 — Le capital minimum est une propriété calculable, pas une surprise

**Contexte** : avec 9,37 EUR, le pare-feu refuse chaque évaluation en
`SIZING_BELOW_EXCHANGE_MINIMUM`. Rien ne distingue ce cas d'un marché sans opportunité :
le bot tourne, ne trade jamais, et le diagnostic coûte des jours.

**Décision** : `app/engine/affordability.py`, module pur, calcule pour une paire le
minimum d'ordre (`max(min_qty × prix, min_notional)`), la distance au stop, le risque
que ce minimum représenterait, et l'équité qu'il exige. Il nomme la contrainte
bloquante : `risk`, `exposure` ou `absolute_cap`. `scripts/check_kraken.py --affordable`
l'expose sur données réelles, en bougies clôturées uniquement.

**Pourquoi** : l'équité requise est une propriété du marché (prix, ATR, limites) et du
profil, pas une opinion. La calculer avant le premier signal évite le pire des
diagnostics — un bot silencieusement inactif — et permet de répondre à « et si je
montais le risque à X ? » sans modifier la configuration.

---

## D-026 — Profil `micro` pour un compte de 9,37 EUR

**Contexte** : décision de ne pas ajouter de capital. Au profil prudent (0,5 %), l'équité
requise mesurée est de 25,00 EUR (BTC/EUR), 30,49 (ETH/EUR), 38,96 (SOL/EUR) : aucune
paire n'est tradable. Le minimum d'ordre de Kraken impose un plancher en valeur absolue,
donc un plancher en pourcentage.

**Décision** : ajout d'un preset `micro` — risque 1,75 %, exposition 50 %, confiance
0,85, drawdown journalier 8 %, hebdomadaire 15 %, depuis le plus haut 20 %. Il est
activé dans `backend/.env` (non secret, ignoré par git) pour `BTC/EUR` et `ETH/EUR`.
`SOL/EUR` est exclu : il manque 1,76 EUR pour le rendre tradable.

**Chiffres** : équité requise 8,36 EUR (BTC/EUR) et 8,71 EUR (ETH/EUR) ; perte maximale
par trade 0,16 EUR. Le drawdown journalier de 8 % laisse passer 4,6 pertes avant le
verrou — un test vérifie que `daily >= 2 × risque` pour tous les presets, sinon une
seule perte fermerait la journée.

**Pourquoi** : à cette taille, refuser de relever le risque signifie ne jamais trader.
Les montants absolus restent triviaux (0,16 EUR par trade), et les pourcentages élevés
ne portent que sur 9,37 EUR. Ce n'est pas une décision d'argent réel : aucun exécuteur
n'existe encore.

**Réversible** : `RISK_PROFILE=prudent` dans `backend/.env`, une ligne.

---

## D-027 — L'univers de paires est calculé, pas choisi à la main

**Contexte** : la question « peut-on trader d'autres cryptos que BTC/ETH/SOL ? » a une
réponse chiffrée, pas une réponse d'intuition. Avec 9,37 EUR, la contrainte n'est pas la
notoriété de la paire mais son couple (minimum d'ordre, ATR) : plus un actif est volatil,
plus son stop est large, plus l'équité requise grimpe.

**Décision** : `check_kraken.py --scan` parcourt tout l'univers `quote` (spot, actif,
stablecoins exclus), filtre sur la liquidité (≥ 100 k EUR sur 24 h) et sur le minimum
d'ordre finançable, puis calcule l'ATR14 H4 réel des plus liquides. Le résultat est une
liste de paires tradables, avec l'équité requise pour chacune.

**Constat mesuré** : 10 paires EUR sont finançables à 9,37 EUR en `micro` — `BTC`, `ETH`,
`XRP`, `TRX`, `ADA`, `LINK`, `DOGE`, `PAXG`, `HBAR`, `BNB`. Les alts moins connues
(`NEAR`, `ZRO`, `TAO`, `ONDO`, `ARB`) demandent 12 à 27 EUR parce que leur ATR de 2 à 4 %
élargit le stop. Autrement dit : à ce capital, on peut trader des alts, mais pas les plus
volatiles.

**Sélection retenue** : les 7 paires dont l'équité requise laisse au moins 0,4 EUR de
marge — `BTC`, `ETH`, `ADA`, `DOGE`, `PAXG`, `HBAR`, `BNB`. `XRP`, `TRX` et `LINK`
passent à 0,03-0,23 EUR près : les inclure produirait des refus qui apparaissent et
disparaissent au rythme de l'ATR.

**Pourquoi** : une liste figée vieillit au premier mouvement de prix. Le scan la
recalcule, et la marge évite les paires qui ne sont tradables qu'une heure sur deux.
`PAXG` (or tokenisé) est conservée : son ATR de 0,77 % en fait la paire la plus stable de
l'univers, utile précisément quand les cryptos sont trop volatiles.

---

## D-028 — LLM : API native, décodage contraint, `think=false`, 8 s mesurés

**Contexte** : le cahier des charges demande un décodage contraint par grammaire, une
température nulle et un plafond de 4 s. Mesures sur la machine : `qwen3.5:9b` met
**6,3 s** pour 82 tokens avec la réflexion activée, **1,0-1,5 s** avec `think=false`.
L'endpoint compatible OpenAI d'Ollama ignore à la fois `think` et `format`.

**Décision** : le client parle à l'API **native** `/api/chat`, impose `think=false`,
`format=<schéma JSON>` et `temperature=0`. Le délai par défaut passe à 8 s. La validation
stricte reste la garantie : un serveur qui ignorerait `format` ne produirait pas un
signal non vérifié, il produirait un repli.

**Pourquoi** : un plafond de 4 s sur ce matériel garantit 100 % de replis, donc un bot
qui ne trade jamais tout en ayant l'air de fonctionner. Mieux vaut un délai mesuré et
documenté qu'un chiffre de spec qui ne tient pas.

**Écart restant** : le 14B du cahier des charges n'est pas installé. `ollama pull
qwen2.5:14b-instruct-q4_K_M` suffit à le mettre en place ; le client n'a pas à changer.

---

## D-029 — Le broker papier est pessimiste par construction

**Contexte** : un backtest flatteur ne prouve rien. La question n'est pas « combien
aurait-on gagné » mais « le système survit-il à ses propres hypothèses ».

**Décision** : l'achat paie le prix + 5 bps, la vente le prix − 5 bps, les frais taker
sont prélevés des deux côtés, un gap remplit à l'ouverture (pas au niveau), et si une
bougie touche le stop **et** le take-profit, le stop est retenu.

**Pourquoi** : sans données tick, l'ordre des extrêmes dans une bougie est inconnu.
Choisir le TP embellirait les résultats d'un biais non mesurable. Le seul biais
acceptable est celui qui sous-estime la performance.

---

## D-030 — Un HOLD n'entre pas dans le pare-feu

**Contexte** : le pare-feu rejette toute intention non-acheteuse avec
`ACTION_IS_NOT_BUY`. Y passer chaque HOLD remplirait `risk_evaluations` de dizaines de
lignes par jour et noierait les vrais rejets sous une catégorie sans information.

**Décision** : un HOLD est persisté dans `llm_signals` et n'est pas évalué. Un SELL, lui,
passe par le pare-feu : c'est une intention directionnelle qui mérite un rejet explicite.

**Pourquoi** : `risk_evaluations` doit rester le registre des décisions de risque, pas le
journal des abstentions. Le taux de HOLD se lit dans `llm_signals`, où il a sa place.

---

## D-031 — Le ledger papier vit dans `equity_snapshots`

**Contexte** : le schéma n'a pas de colonne « cash papier ». Un redémarrage ne doit pas
réinitialiser un portefeuille simulé, sinon les drawdowns et le HWM repartent de zéro.

**Décision** : chaque cycle et chaque entrée/sortie écrivent `equity_quote` et
`free_quote`. Le cash disponible est le `free_quote` du dernier instantané ; l'équité
initiale vient de `PAPER_START_EQUITY`, ou du solde réel lu **en lecture seule** si la
variable est vide.

**Pourquoi** : la base est déjà persistante et le schéma suffit ; ajouter une table
aurait dupliqué une information qui appartient aux instantanés d'équité.

---

## D-032 — L'écrémage est virtuel, la réserve est comptable

**Contexte** : Kraken n'a pas de compartiment « spot / funding » comme Binance. Le
schéma `profit_sweeps` prévoit pourtant `from_account` / `to_account` et un statut.

**Décision** : en papier, l'écrémage écrit une ligne `SIMULATED` : 30 % du gain net,
seuil de transfert 1,00 EUR. La réserve n'est pas un solde séparé, c'est
`SUM(swept_amount)` des lignes `SIMULATED`/`SUCCESS`. Elle est retirée du capital de
trading (donc du dimensionnement) mais reste dans l'équité : l'argent est à soi, il
n'est simplement plus risqué.

**Sous le seuil** : la ligne est écrite en `SKIPPED_BELOW_MINIMUM` avec
`swept_amount = 0` et une date de report. Ni échec, ni faux succès. Le montant reste
dans le capital de trading.

**Reports cumulés** : une ligne `SKIPPED_BELOW_MINIMUM` n'est pas perdue. La somme des
reports **postérieurs au dernier écrémage exécuté** est réinjectée dans le calcul
suivant : dès que le cumul atteint le seuil, une ligne `SIMULATED` solde l'ensemble.
Pas de colonne de consommation, pas de migration : l'ordre chronologique suffit, et un
écrémage exécuté solde par construction tous les reports antérieurs.

---

## D-033 — Le kill-switch est un override humain, pas un état de la boucle

**Contexte** : la machine à états interdit toute sortie automatique de
`EMERGENCY_HALT` et `LOCKED_CIRCUIT_BREAKER`. Un kill-switch qui respecterait cette
règle refuserait de fonctionner précisément quand on en a besoin.

**Décision** : `PostgresRepository.force_halt()` écrit l'état et le journal sans passer
par le graphe. La liquidation ne passe pas non plus par le Risk Gateway : celui-ci ne
statue que sur les **achats**, et une vente d'urgence n'est pas une prise de risque.
Le script refuse le mode `live` tant que l'exécuteur réel n'est pas câblé.

**Pourquoi** : un bouton d'arrêt d'urgence doit être disponible depuis n'importe quel
état, y compris un état incohérent après un crash. En revanche, il ne doit pas être un
chemin déguisé pour envoyer des ordres réels : c'est pourquoi le mode live est refusé
explicitement plutôt que partiellement implémenté.

---

## D-034 — L'API d'administration exige trois preuves et ne publie pas sa documentation

**Contexte** : l'API peut arrêter le bot et liquider une position. Une session volée ne
doit pas suffire à solder un compte.

**Décision** : mot de passe scrypt (stdlib, sel aléatoire, comparaison à temps
constant), session JWT HS256 de 15 minutes, TOTP obligatoire au login **et** pour le
panic-sell (code frais, anti-rejeu 90 s), limiteur de 5 tentatives par IP et par
5 minutes. Swagger et OpenAPI désactivés. Le WebSocket s'authentifie par un premier
message contenant le jeton, jamais par l'URL — une URL finit dans les journaux d'accès.
Démarrage refusé si `JWT_SECRET_KEY` (< 32 caractères), `TOTP_SECRET` ou
`ADMIN_PASSWORD_HASH` manquent.

**Pourquoi** : un seul facteur sur un bouton d'arrêt d'urgence est un point de
défaillance unique. Et un jeton dans une query string est un jeton publié.

**Compatibilité** : TOTP standard (6 chiffres, 30 s, SHA-1), donc Authy, Aegis,
FreeOTP ou Google Authenticator fonctionnent sans adaptation.

---

## D-035 — Pause = état bloqué, reprise seulement depuis un état bloqué

**Contexte** : pause et reprise sont des commandes d'administration, mais elles ne
doivent pas devenir un moyen de contourner la machine à états.

**Décision** : `pause` force `LOCKED_CIRCUIT_BREAKER` ; `resume` n'accepte que
`LOCKED_CIRCUIT_BREAKER` ou `EMERGENCY_HALT` et repasse en `IDLE` en journalisant
`unlocked_by = admin`. Une reprise depuis `IDLE` répond 409.

**Position ouverte** : `pause` est refusé (409) tant qu'une position est ouverte :
`LOCKED_CIRCUIT_BREAKER` arrêterait aussi la surveillance du stop, et le bot ne verrait
plus la bougie qui perce le niveau. Il faut attendre la clôture ou liquider via le
panic-sell.

**Pourquoi** : reprendre un bot qui n'est pas arrêté masquerait un bug d'état. Le graphe
reste la référence ; seules les actions humaines explicites (pause, reprise,
kill-switch) écrivent l'état directement, et chacune laisse une ligne dans
`system_state_logs`.

---

## D-036 — Le dashboard est un client mince, sans dépendance, et ne voit jamais le jeton

**Contexte** : le dashboard peut liquider une position. Un jeton stocké dans
`localStorage` est lisible par n'importe quel script de la page ; une bibliothèque de
plus est une surface d'attaque de plus.

**Décision** : serveur Node.js **zéro dépendance** (`node:http`, `fetch` natif,
`node:test`). Il détient le JWT en mémoire et ne rend au navigateur qu'un identifiant
de session aléatoire dans un cookie `HttpOnly`, `SameSite=Strict`, 15 minutes. Le flux
temps réel est du SSE produit par le serveur (interrogation de l'API toutes les 2 s) :
le navigateur n'a besoin d'aucun client WebSocket et le jeton ne transite jamais par
l'URL. CSP stricte, `X-Frame-Options: DENY`, contrôle d'`Origin` sur les POST, et
aucun `innerHTML` avec des données de l'API.

**Pourquoi** : le pire scénario n'est pas « le dashboard ne s'affiche pas », c'est
« un script injecté lit le jeton et liquide le compte ». Le cookie HttpOnly et
l'absence de dépendance suppriment les deux chemins les plus courants.

**Limite** : sessions en mémoire, une instance = un opérateur. Un redémarrage
déconnecte ; un stockage partagé serait nécessaire pour plusieurs instances.

---

## D-037 — L'exposition réseau du dashboard exige deux gestes

**Contexte** : écouter sur `0.0.0.0` rend la page d'administration joignable depuis le
réseau. Sans TLS, le mot de passe et le code TOTP traversent le réseau en clair.

**Décision** : `DASHBOARD_HOST` seul ne suffit pas. Toute valeur hors boucle locale
exige `DASHBOARD_ALLOW_REMOTE=true`, sinon le serveur refuse de démarrer avec un
message explicite ; il avertit ensuite à chaque démarrage. L'API FastAPI reste sur
`127.0.0.1` : le dashboard est le seul point d'entrée réseau.

**Pourquoi** : une exposition accidentelle (variable oubliée, copier-coller d'un
tutoriel) ne doit pas suffire à publier une interface qui peut liquider un compte. Le
second geste transforme un accident en décision, comme pour `TRADING_MODE=live`.

**Limite** : ce n'est pas du chiffrement. Pour un accès distant, Tailscale/WireGuard ou
un reverse proxy HTTPS avec `DASHBOARD_COOKIE_SECURE=true`.

---

## D-038 — Un PID n'est tué qu'après vérification de sa ligne de commande

**Contexte** : `scripts/manage.sh` arrête des processus via des fichiers PID. Après un
redémarrage, un PID enregistré peut avoir été recyclé par un processus sans rapport.

**Décision** : avant tout `kill`, le script lit `ps -p <pid> -o args=` et vérifie que la
commande contient le motif attendu (`uvicorn app.api.main` ou `node server.js`). Sinon
il supprime le fichier PID sans rien tuer. Il refuse aussi de démarrer un composant si
le port est déjà occupé, et vérifie les trois secrets de l'API avant lancement.

**Pourquoi** : tuer à l'aveugle est le seul vrai danger d'un script de gestion. Le coût
de la vérification est une ligne de `ps`.

---

## D-039 — Le backtest de régression rejoue un golden, il n'appelle pas le LLM

**Contexte** : un backtest qui interroge le modèle à chaque vérification coûte deux
minutes et n'est pas reproductible : à température 0, une décision isolée peut
basculer d'un processus à l'autre (observé sur le même instantané). L'utiliser comme
test de non-régression produirait des échecs aléatoires.

**Décision** : un golden contient les bougies, les décisions enregistrées du modèle et
les métriques attendues. La régression rejoue ces décisions à travers le code courant,
sans réseau ni LLM : moins d'une seconde, déterministe. Deux empreintes détectent les
changements d'entrée — prompt/modèle et risque/frais/capital. Toute modification
apparente exige une action explicite : `record` pour de nouvelles décisions, ou
`--bless` après relecture du diff. La veille hebdomadaire, elle, interroge à nouveau
le modèle sur 12 décisions témoins et tolère 10 % de bascules.

**Pourquoi** : un test qui se met à jour tout seul ne protège de rien. Ici, un
changement de risque ou de prompt casse le test **avant** le commit, et le diff du
golden montre l'impact chiffré.

**Limite** : le golden fige un comportement observé, pas une vérité. Il détecte les
changements, il ne prouve pas la rentabilité.
