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

**Mise à jour 2026-11-04 (D-048)** : le tarif réel du compte est 0,80 %/côté, soit
1,60 % l'aller-retour ; la valeur Kraken est passée à `0.016`. Le 0,26 % de CCXT était
un tarif historique.

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

---

## D-040 — La réconciliation constate ; elle ne liquide pas elle-même

**Contexte** : l'étape 1.7 exige de relire la position et son stop chez l'exchange.
Aucun exécuteur live n'existe, et une position ouverte sans stop doit être liquidée
« immédiatement ».

**Décision** : `app/runtime/reconciliation.py` est un planificateur pur. Il lit les
positions en base, les ordres ouverts et les soldes, puis produit un constat
(`STOP_ARMED`, `STOP_MISSING`, `STOP_INSUFFICIENT`, `BALANCE_TOO_SMALL`, `ORPHAN_STOP`) et un plan
(`liquidate`, `halt`). Un stop vendeur sans position connue est critique : il peut
solder des avoirs manuels, donc le bot refuse de trader tant qu'il vit — même si le
plan ne l'annule pas lui-même. `scripts/reconcile.py` n'exécute que la lecture.
L'application du plan — vendre, annuler — appartient à l'exécuteur live, quand il
existera et aura été revu.

**Pourquoi** : un outil de diagnostic capable d'envoyer un ordre avant que le chemin
d'envoi soit audité est plus dangereux que pas d'outil du tout. La lecture prouve dès
maintenant la moitié vérifiable de 1.7 (le compte répond, aucun ordre fantôme) sans
créer un second chemin d'exécution qui contournerait la revue.

---

## D-041 — Une bougie clôturée = une évaluation, sans nouvelle migration

**Contexte** : `--cycles 5 --interval 60` redemandait au modèle la même bougie H4
pendant quatre heures, écrivait des signaux en double et pouvait rouvrir une position
juste après une sortie.

**Décision** : `app/runtime/scheduler.py` calcule la prochaine clôture (alignée sur
l'époque Unix) ; `python -m app.main --serve` y aligne ses cycles. Avant de construire
un instantané, le runtime compare `last_signal_at(mode, symbole)` à l'heure de clôture
de la dernière bougie : un signal postérieur prouve que la bougie a déjà été traitée,
y compris par un processus précédent. Aucune colonne ni migration ajoutée.

**Pourquoi** : le journal `llm_signals` contient déjà l'information et survit aux
redémarrages. Le sens de l'erreur est choisi : manquer une bougie après une panne est
acceptable, la trader deux fois ne l'est pas.

---

## D-042 — Les drawdowns se vérifient sur une transaction annulée

**Contexte** : le critère 1.8 exige que les trois drawdowns calculés en SQL
correspondent à un calcul manuel, sur un jeu de données figé. Aucun test unitaire ne
prouve les fenêtres `date_trunc('day')`, `now() - interval '7 days'` ni les casts
`NUMERIC`.

**Décision** : `backend/scripts/verify_drawdowns.py` écrit cinq instantanés dans
`equity_snapshots` — HWM à 10 jours, pic 7 jours à 16, pic du jour à 12, équité
courante à 9,60 — calcule, compare à 20 % / 40 % / 60 %, puis annule la transaction.
Le script est appelé par `scripts/check.sh` ; une base injoignable est une alerte, une
valeur fausse est un échec.

**Pourquoi** : la base réelle est le seul endroit où le SQL est réellement exécuté.
Le rollback permet de l'utiliser sans polluer le ledger ; des valeurs qui tombent juste
(0,2 / 0,4 / 0,6) rendent la comparaison exacte, sans tolérance flottante.

---

## D-043 — Le LLM peut déclencher, dans un cadre déterministe (remplace D-002 sur le déclenchement)

**Contexte** : le 2026-11-04, Seb demande que le LLM puisse acheter, vendre et tout
arrêter, et que le bot soit autonome sauf quand il actionne le bouton rouge. La version
précédente (D-002) interdisait au modèle de déclencher un achat ; dans les faits,
l'orchestrateur exigeait déjà un BUY du modèle, donc la documentation était en avance
sur le code. Et le plancher `micro` à 0,85 face à un modèle qui plafonne à 0,72 rendait
tout achat impossible.

**Décision** :
- le modèle émet `BUY`, `SELL`, `HOLD` ou `HALT` ;
- `BUY` : seulement dans un setup déterministe (`app/engine/entry_rule.py`) et après
  `evaluate()`. Le modèle ne dimensionne ni le prix ni le stop ;
- `SELL` : seulement pour clôturer une position ouverte, via
  `app/engine/exit_guard.py`. Jamais de short, jamais une quantité supérieure à celle
  détenue, jamais un prix proposé par le modèle ;
- `HALT` : liquide la position éventuelle puis force `EMERGENCY_HALT` ; exige une
  confiance ≥ 0,80. La reprise reste humaine (dashboard / API) ;
- le bouton rouge (panic-sell 2FA) reste le seul override manuel ;
- le plancher de confiance `micro` passe de 0,85 à 0,60. C'est une expérience mesurée
  par le backtest, pas une suppression de garde-fou : le dimensionnement, le stop et
  le plafond d'exposition ne dépendent pas de la confiance.

**Pourquoi** : le modèle plafonne à 0,72 sur les BUY ; à 0,85, il ne pouvait jamais
acheter. La règle d'entrée évite de l'interroger dans n'importe quelle zone, et le
pare-feu continue de refuser, dimensionner et plafonner. Un SELL ou un HALT ne peut
que réduire l'exposition ; il n'a pas besoin du budget de risque, mais il a son propre
garde-fou.

**Ce qui ne change pas** : D-002 reste vrai sur les valeurs numériques — aucune taille,
aucun prix, aucun stop, aucun levier émis par le modèle n'est lu. Le stop distant, le
plafond d'exposition et le coupe-circuit au plus haut restent les protections
principales.

**Limite assumée** : un `HALT` du modèle exige une reprise humaine. Si Seb préfère une
reprise automatique après un cooldown de 24 h, c'est une nouvelle décision, pas un
réglage caché.

---

## D-044 — Les réglages du dashboard vivent en base, et un arrêt du LLM expire en 24 h

**Contexte** : Seb veut pouvoir modifier les paramètres depuis le dashboard, sans
jargon financier, et que le bot reprenne tout seul 24 h après un arrêt demandé par le
modèle — ou immédiatement via un bouton. La première version stockait ces réglages
dans un fichier JSON local ; ce n'était pas la source de vérité partagée entre l'API
et le bot.

**Décision** :
- table `runtime_settings` (migration 0005), une ligne par mode, avec `payload JSONB`,
  `updated_at` et `updated_by` ;
- liste blanche `RUNTIME_ALLOWED_KEYS` : le payload ne peut pas changer le mode,
  l'exchange, la base ou les secrets ;
- le bot relit la ligne à chaque cycle (`Orchestrator._refresh_settings`) et applique
  les overrides via `load_settings(overrides=…)` ;
- l'API expose `GET /api/settings` et `POST /api/settings` ; le dashboard écrit dans la
  table, jamais dans `.env` ;
- un `HALT` du modèle pose `cooldown_until = now + HALT_AUTO_RESUME_HOURS` (24 h par
  défaut) et `lock_details = "LLM HALT …"`. Le cycle suivant reprend automatiquement à
  l'échéance ; le bouton « Reprendre maintenant » reprend avant ;
- un panic-sell humain laisse `cooldown_until = NULL` : pas de reprise automatique,
  il faut un geste explicite.

**Pourquoi** : un réglage doit être auditable et partagé entre l'API et le bot. La
liste blanche empêche un payload de changer le mode d'exécution ou les clés, et la
validation (`max_daily >= 2 × risk_per_trade`) refuse une combinaison qui ferait
fermer la journée sur une seule perte.

**Sécurité** : `POST /api/settings` exige une session 2FA valide. Le panic-sell garde
son TOTP frais ; les réglages ne peuvent pas déclencher d'ordre.

---

## D-045 — Session du dashboard de 7 jours, pas de reconnexion toutes les 15 minutes

**Contexte** : Seb refuse de saisir mot de passe + code Authy toutes les 15 minutes
pour consulter une page de supervision.

**Décision** : le jeton de session de l'API dure **7 jours**
(`TOKEN_TTL_SECONDS = 604800`). Le dashboard pose un cookie `tn_session` `HttpOnly`,
`SameSite=Strict`, dont le `Max-Age` est le plus court entre le TTL du jeton renvoyé
par l'API (`expires_in`) et son propre TTL (7 jours également). Le jeton lui-même
reste dans le processus Node ; le navigateur ne reçoit qu'un identifiant aléatoire.

**Pourquoi** : c'est un confort acceptable pour une administration sur réseau de
confiance. Le cookie n'est pas lisible en JavaScript, et le panic-sell exige toujours
un TOTP frais : une session volée ne suffit pas à liquider. La durée est un compromis
assumé, pas un oubli.

**Conséquence** : un redémarrage du processus Node vide la table des sessions en
mémoire ; le cookie devient inutile et il faut se reconnecter. Le jeton de l'API, lui,
reste valable 7 jours. Ne jamais exposer le dashboard sur Internet sans TLS.

---

## D-046 — Shadow trades : mesurer les achats non pris, sans les prendre

**Contexte** : le pare-feu refuse des BUY, et le modèle dit HOLD sur des setups
d'entrée valides. Sans mesure, impossible de savoir si ces refus évitent des pertes
ou ratent des gains. La table `shadow_trades` (migration 0006) enregistre ces achats
non pris et rejoue leur issue.

**Décision** :
- deux `kind` : `GATE_REJECTED_BUY` (le modèle a dit BUY, le pare-feu a refusé) et
  `MODEL_HOLD` (HOLD sur une règle d'entrée déclenchée) ;
- le plan est recalculé par le **même** `evaluate()` avec une confiance forcée à 1,0
  et le régime du signal ; s'il est encore refusé (contre-tendance, volatilité,
  minimum exchange), la ligne reste `NO_PLAN` : on mesure le refus, pas un trade
  imaginaire ;
- un plan finançable est `OPEN`, puis évalué par
  `scripts/shadow_report.py --evaluate` sur les bougies H4 suivantes, avec le stop
  prioritaire, les frais et le slippage du broker papier ; statut `STOP_LOSS`,
  `TAKE_PROFIT` ou `END_OF_DATA` ;
- aucune écriture d'ordre, aucune position, aucun argent. Le shadow ne peut pas
  influencer le pare-feu : il est écrit après la décision, en lecture seule.

**Pourquoi** : c'est la seule façon de mesurer l'opportunité des refus sans les
transformer en trades réels. Les `NO_PLAN` sont une information en soi : ils montrent
que le refus vient de la structure ou du minimum exchange, pas du modèle.

**Limite** : un `MODEL_HOLD` n'a pas d'`evaluation_id` (le pare-feu n'a pas été
appelé) ; le plan est une reconstruction. Un `NO_PLAN` n'a pas d'issue simulée : on
ne prétend pas qu'un trade refusé pour contre-tendance aurait gagné.

---

## D-047 — Mémoire de leçons : une relecture, pas un entraînement

**Contexte** : le modèle local est sans état ; l'appeler plus souvent ne l'entraîne
pas. Les résultats commencent à exister (positions closes, shadow trades évalués), et
il faut les réinjecter sans multiplier les appels.

**Décision** :
- table `lessons` (migration 0007), une seule version active par mode, expiration
  après 30 jours ;
- `scripts/lessons.py review` : **un seul** appel LLM, lit les derniers trades clos
  et shadow trades évalués, demande 1 à 5 leçons de 240 caractères maximum, les
  valide et les stocke ; refuse sous 5 résultats (sauf `--force`) ;
- les leçons sont injectées dans le prompt de décision (`SignalClient._payload`),
  avec un rappel qu'elles ne modifient ni la taille, ni le prix, ni le stop ;
- le prompt de relecture est séparé : il ne peut pas produire BUY/SELL/HALT, et sa
  sortie ne passe jamais par le pare-feu ;
- A/B : `backtest.py measure --lessons-file` mesure la politique avec et sans
  leçons ; le golden reste la référence du prompt de base.

**Pourquoi** : avec zéro position réelle, un fine-tuning serait du bruit. Une mémoire
de cinq observations coûte un appel par semaine et reste mesurable. Les leçons sont
du contexte, pas des ordres : le pare-feu reste le seul décideur.

**Limite** : un shadow négatif est une **perte évitée**, pas une perte. Le digest doit
le dire explicitement, sinon le modèle produit des leçons inversées (observé puis
corrigé le 2026-11-04). La relecture ne tourne pas tant qu'il n'y a pas cinq résultats.

---

## D-048 — Frais Kraken : lire le tarif du compte, pas celui de CCXT

**Contexte** : CCXT expose `market['taker'] = 0.0026`, un tarif historique. Le compte
Kraken est au palier 1 depuis le 2026-07-09 : **0,80 % taker / 0,40 % maker**.
`fetch_trading_fee("BTC/EUR")` le confirme (`taker=0.008`, volume 30 jours 0). Le
paper, le backtest et les shadow trades sous-estimaient donc les frais d'un facteur 3.

**Décision** :
- `resolve_taker_fee(exchange, symbol, fallback)` lit `fetch_trading_fee` ; repli
  0,008 si l'appel privé échoue ; `market_info` accepte les overrides ;
- `ExchangeSettings.taker_fee/maker_fee` (défauts Kraken 0,008/0,004), surchargeables
  par `TAKER_FEE_PCT` / `MAKER_FEE_PCT` ;
- `fee_buffer_pct` Kraken = 0,016 (aller-retour 1,60 %) ;
- tous les chemins qui simulent un ordre (paper, backtest, shadow, kill-switch, API)
  utilisent le tarif résolu ;
- le golden a été ré-enregistré avec 0,80 %.

**Conséquence** : les résultats v3 passent de −0,4999 à **−0,6951 EUR** ; le BTC de
−3,58 % à **−5,13 %**. Aucun scénario n'est positif. À ce capital, un aller-retour à
1,60 % coûte ~39 % du budget de risque par trade : il faut une entrée nettement
meilleure, pas un réglage de sortie.

---

## D-049 — Le risque affiché est brut ; la perte nette au stop est assumée

**Contexte** : `risk_per_trade_pct` dimensionne sur la distance au stop, **hors frais**.
Depuis D-048, les frais réels sont 1,60 % par aller-retour. Au stop, la perte nette vaut
donc le risque brut + les frais, soit ~1,4× le budget affiché à 9,37 EUR.

**Décision** :
- on ne réduit pas la taille : à 9,37 EUR, plusieurs tickets passeraient sous le minimum
  Kraken, et le bot ne traderait plus ;
- on garde les ordres au marché (taker, 0,80 %/côté) ;
- le dashboard affiche deux lignes : « Risque brut » (`risk_amount_quote`) et « Perte au
  stop (frais inclus) » (`risk_amount_quote + notionnel × 2 × taker`) ;
- les coupe-circuits (drawdown quotidien/hebdomadaire/HWM, pertes consécutives) restent
  calculés sur le **PnL net** : la protection du compte ne dépend pas de cette convention.

**Pourquoi pas les ordres limit maintenant** : 0,40 %/côté économiserait 0,80 % par
aller-retour, mais il faudrait modéliser les fills (prix touché, partiel, expiration) ;
le backtest actuel ne le fait pas, et l'entrée est déjà négative **avant** frais.
Optimiser l'exécution ne corrige pas une entrée perdante. À rouvrir quand une entrée
sera positive nette.

**Conséquence** : le risque par trade affiché n'est pas la perte maximale nette ; c'est
le risque brut. La perte nette estimée est visible, et les coupe-circuits restent nets.

---

## D-050 — Exécuteur réel : stop exchange, TP local, sous-compte dédié

**Contexte** : le chemin réel n'existait pas ; seul `PaperBroker` était câblé. Le
bracket Kraken (`close`) ne porte qu'une protection conditionnelle, et un take-profit
séparé sans OCO peut vendre deux fois si le stop et le TP se déclenchent ensemble.

**Décision** :
- `app/execution/live.py` est le **seul** module qui appelle `create_order()`. Il envoie
  un achat marché avec le stop bracket, attend le fill, relit le stop chez l'exchange,
  et revend immédiatement si le stop manque. Prix, quantité et frais viennent de la
  réponse réelle.
- **Pas de TP exchange pour l'instant** : le TP reste surveillé localement sur la bougie
  H4, tant qu'un OCO n'est pas écrit. Le stop, lui, vit chez l'exchange et survit à
  l'arrêt du PC.
- En live, la boucle lit **d'abord** `position_state` (base + stop) : base plate → elle
  enregistre la vente réelle via `last_sell_fill` sans renvoyer d'ordre ; base
  supérieure à la position → halte (compte partagé) ; stop absent → vente d'urgence
  puis halte.
- `main.py` refuse de démarrer en live si `inspect_exchange` n'est pas propre.
  `scripts/reconcile.py --apply --confirm` liquide les positions sans stop couvrant ;
  `kill_switch.py --confirm-live` et l'API panic utilisent le broker réel.
- **Sous-compte dédié obligatoire** : le broker refuse d'entrer si un solde de base
  existe déjà sur la paire (>1 % de la taille). Sinon il ne peut pas distinguer sa
  position d'un avoir manuel et pourrait vendre l'avoir de Seb.

**Conséquence** : le chemin est écrit et testé sans réseau (442 tests), mais **aucun
ordre réel n'a été envoyé**. L'activation exige un sous-compte dédié, le montant
acceptable, les trois verrous, et une réconciliation propre. Le TP manqué pendant un
arrêt du bot est un coût accepté ; le stop, non.

---

## D-051 — Filtre de pente EMA200 : protéger le régime, pas créer un edge

**Contexte** : la règle d'entrée achetait un pullback EMA50 dès que `EMA50 > EMA200` et
que le prix était au-dessus de l'EMA200. Dans la fenêtre mesurée (BTC, 25 jours), les
3 trades approuvés sont tous sortis au stop : −0,4811 EUR (−5,13 %). Une EMA50 encore
au-dessus d'une EMA200 peut coïncider avec un sommet : la structure « haussière » se
retourne avant que le prix ne repasse sous l'EMA200.

**Décision** :
- `MarketSnapshot.ema_200_slope_atr` porte la pente de l'EMA200 H4 sur **60 bougies
  (10 jours)**, divisée par l'ATR H4 ; `build_snapshot` la calcule sans lookahead.
- `RiskConfig.entry_ema200_slope_min` (défaut **2,0 ATR** dans les presets) refuse une
  entrée si la pente est insuffisante. `0` désactive le filtre.
- Le refus est explicite : « EMA200 monte de X ATR sur 10 jours (minimum Y) : tendance
  pas assez haussière ».
- Le golden a été ré-enregistré après revue : 44 → 19 décisions dans la zone, 3 → 2
  trades, −0,4811 → −0,3268 EUR. Le seuil 3,0 ATR laisse 1 trade à −0,1391 EUR ; au-delà,
  plus aucun trade.

**Conséquence** : le filtre coupe les entrées dans un trend qui s'essouffle et réduit la
perte de 32 %, mais **ne rend pas la stratégie positive**. Deux trades ne prouvent rien.
C'est une protection de régime, pas une arête ; ne pas passer en réel sur ce résultat.

---

## D-052 — Mandat short : mesuré en laboratoire, pas encore exécutable

**Contexte** : le mandat spot long-only perd sur la fenêtre mesurée. Le short est la
seule façon de gagner quand le marché baisse, mais **Kraken spot ne permet pas de vendre
à découvert** : il faut un compte marge (si éligible) ou Kraken Futures, un autre
adaptateur, des contrats, du levier, du financement et un seuil de liquidation. Le compte
de 9,37 EUR est très en dessous de ce qu'exige ce type de produit.

**Décision** :
- `app/backtest/short_lab.py` + `scripts/short_lab.py` mesurent un mandat short **hors
  production** : mêmes bougies, mêmes frais, même slippage, aucun ordre, aucune base.
- La règle miroir vend un rallye dans une structure baissière (prix sous EMA200, EMA50
  sous EMA200, pente EMA200 ≤ 0, RSI 30–65, distance à l'EMA50 bornée).
- `SHORT` n'est **pas** ajouté à `signal_action`, au Risk Gateway, au broker papier ni à
  l'orchestrateur. Le chemin réel reste long-only et refuse toujours le short.
- Mesure : 1 trade le 29 octobre, **+0,93 %** avec stop 2,5/TP 1,5 ; **+1,98 %** avec
  stop 3/TP 2 ; la variante « distance −2..0 ATR » perd −2,91 % sur 2 trades. Le
  buy & hold BTC est −6,03 %.

**Conséquence** : le short change le signe sur cette fenêtre, mais **un seul trade ne
prouve rien**. Avant de câbler quoi que ce soit : mesurer sur plusieurs régimes et sur
les 7 paires, exiger un nombre de trades interprétable, puis choisir le lieu
d'exécution (marge spot ou futures) et écrire les garde-fous de liquidation. Ne pas
présenter ce laboratoire comme une stratégie rentable.

---

## D-053 — Historique : lire les archives Kraken par plages, pas les télécharger

**Contexte** : l'API REST OHLC de Kraken rend au plus 720 bougies et ignore un `since`
plus ancien (30 jours de H1, 120 jours de H4). Valider un filtre de tendance H4 sur
25 jours est statistiquement impossible. Kraken publie des archives OHLCVT depuis la
première transaction de chaque marché, mais une archive trimestrielle pèse ~575 Mo et
l'archive complète 9,5 Go — dont on ne veut que six fichiers CSV de quelques dizaines
de kilo-octets.

**Décision** :
- `app/market/kraken_archive.py` lit le **répertoire central** du ZIP par requête HTTP
  Range (le serveur annonce `accept-ranges: bytes`), puis chaque membre par un second
  Range. Gestion ZIP64 (offsets 64 bits dans le champ supplémentaire 0x0001).
- `MultiPartRangeSource` présente les cinq parties de l'archive complète comme un seul
  espace d'octets : une lecture à l'offset X est routée vers la partie qui le contient,
  sans concaténation ni téléchargement.
- Les archives trimestrielles ne sont publiées que pour les derniers trimestres
  (2026Q1+ au 2026-11-04) ; `--source auto` bascule sur l'archive complète dès qu'un
  trimestre manque (HTTP 404). Coût mesuré : ~3 s et quelques mégaoctets.
- La cohérence est vérifiée à chaque import : l'agrégation H1 → H4 doit reproduire le
  H4 publié **au prix près**. Une divergence fait échouer l'import, elle ne produit pas
  un backtest silencieusement faux.

**Conséquence** : l'historique est reproductible et vérifiable sans stocker 10 Go. Le
prix de cette approche est une dépendance au format ZIP et à l'en-tête `Range` de
Kraken, tous deux testés (`tests/test_kraken_archive.py`).

---

## D-054 — `market_candles_history` : table simple, ni hypertable ni partitions

**Contexte** : le brief demande une hypertable TimescaleDB partitionnée par mois. Or
TimescaleDB n'existe pas sur Supabase PostgreSQL 17 (D-011) — `CREATE EXTENSION
timescaledb` échoue — et le volume réel ne justifie aucun partitionnement.

**Décision** :
- Table simple, clé primaire `(symbol, timeframe, open_time)`, contraintes de cohérence
  OHLC et volume, colonne `source` (`kraken-archive` | `kraken-rest` | `csv`).
- Volume mesuré : 3 paires × 2 timeframes sur 13 ans = 158 600 lignes, quelques Mo.
  Une partition mensuelle ajouterait un `DEFAULT` et un `ensure_partitions()` pour un
  gain nul : le parcours par index couvre déjà la fenêtre glissante.
- Une vue `v_history_coverage` (en `security_invoker`) rend la couverture, les bougies
  attendues, les manquantes et la profondeur en jours.
- RLS activée sans policy + `REVOKE` sur `anon, authenticated` : le schéma `public` est
  exposé par PostgREST, ces données sont publiques mais n'ont rien à y faire (D-011).

**Conséquence** : si un jour 20 paires en 1 minute entrent en jeu (~31 M lignes/an), ce
sera une nouvelle migration et une décision explicite sur le partitionnement — pas une
modification de cette table.

---

## D-055 — Un trou dans l'historique est signalé, jamais interpolé

**Contexte** : Kraken ne publie que les intervalles où un trade a eu lieu. Un fichier
peut donc contenir des trous, et la soudure entre la fin d'archive (30/09/2026) et la
fenêtre REST de 30 jours laisse 111 heures de H1 absentes au 2026-11-04.

**Décision** :
- Aucune interpolation, aucune ligne synthétique n'est insérée.
- `detect_gaps` et `v_history_coverage` rendent chaque trou (début, fin, bougies
  manquantes, durée) ; `ingest_history.py --report` les liste et le backtest les
  traverse sans les voir.
- Le complément quotidien `--rest-only` est la seule façon de combler la soudure ; il
  n'invente rien, il récupère les 720 dernières bougies réelles.

**Conséquence** : un trou coûte quelques bougies ignorées, jamais un stop déclenché par
un prix fabriqué. Une bougie inventée est un mensonge qui se propage dans le ledger.

---

## D-056 — Le balayage remplace le LLM par une réponse constante, pas par `mechanical`

**Contexte** : mesurer un filtre sur deux ans avec le modèle local coûterait ~1 s par
bougie H4 (≈ 4 400 par paire). `run_backtest(mechanical=True)` existe pour la baseline,
mais il remplit `baseline_trades` et laisse `trades` vide : les métriques de l'objet
(PF, espérance, drawdown) resteraient muettes ou porteraient sur la mauvaise série.

**Décision** :
- `backtest.py sweep` passe un `propose` constant (BUY, confiance 1,0, régime
  `TRENDING_UP`). Le chemin exercé est celui de la production : règle d'entrée →
  `evaluate()` → sizing → `PaperBroker` → sortie stop/TP.
- Les métriques sont calculées sur `result.trades`, comme pour une mesure LLM.
- L'équité de départ est explicite (`--equity 1000`) et découplée du solde réel.
- Le verdict applique les seuils du brief (N ≥ 100, PF ≥ 1,30, E > 0, DD ≤ 15 %) et
  affiche la raison du refus, pas seulement OK/KO.

**Conséquence** : un filtre se juge sur un échantillon long et déterministe, le modèle
sur une fenêtre courte et enregistrée (golden). Les deux mesures ne se mélangent pas.

---

## D-057 — Plancher de 100 Mo libres avant toute écriture d'historique

**Contexte** : le plan Supabase utilisé plafonne à 500 Mo. L'ingestion multi-années a
écrit ~158 000 bougies (~35 Mo avec l'index), et la télémétrie (`llm_signals`,
`risk_evaluations`) grossit à chaque cycle. Une ingestion trop large, ou un bug de
pagination qui redemande les mêmes archives, remplit le quota — et un quota plein ne
casse pas seulement l'ingestion : PostgreSQL refuse toute écriture, y compris les
décisions du bot.

**Décision** : avant chaque lot, `scripts/ingest_history.py` mesure la taille réelle
(`pg_database_size`) et le coût moyen d'une ligne de `market_candles_history`
(`pg_total_relation_size` / lignes). Si le lot ferait passer la base sous
`SUPABASE_DB_MIN_FREE_MB` (100 par défaut) sur `SUPABASE_DB_QUOTA_MB` (500 par défaut),
l'écriture est refusée avec `BudgetExceeded` et le message dit quoi réduire. `--report`
affiche l'espace restant et signale « SOUS LE PLANCHER ».

**Pourquoi 100 Mo** : c'est environ trois fois le volume de l'historique actuel. Le
plancher doit rester supérieur à la croissance de la télémétrie entre deux purge
manuelles, sinon le garde-fou protège l'historique en laissant mourir le journal.

**Écarté** : se reposer sur l'alerte du tableau de bord Supabase (trop tard : quand
elle arrive, les écritures échouent déjà), et purger automatiquement les vieilles
bougies (elles sont exactement ce que le backtest multi-années mesure).

---

## D-058 — Deux modes d'entrée, et une bande RSI corrigée par la mesure

**Contexte** : le balayage 24 mois (D-056) ne produit que 2 à 9 trades par paire et par
an et aucun PF ≥ 0,62. Le brief propose de remplacer « acheter l'extension au-dessus de
l'EMA50 H4 » par « acheter le repli sur l'EMA50 H1 » pour élargir l'échantillon.

**Décision** :
- `EntryMode` (`EMA50_H4`, `PULLBACK_H1`) dans `RiskConfig`, choisi par `ENTRY_MODE`.
  Défaut `EMA50_H4` : le comportement de production et le golden ne changent pas
  (rejoué, métriques identiques, fingerprint re-bénit).
- `PULLBACK_H1` : la dernière H1 clôturée doit avoir un **bas ≤ EMA50 H1**, une
  **clôture > EMA50 H1**, une **extension ≤ 1 ATR H1** au-dessus de l'EMA et un
  **RSI H1 ≤ 55**. Les garde-fous communs (prix > EMA200 + marge, pente de l'EMA200,
  bande d'ATR, couteau qui tombe) restent identiques : le mode ne change que la
  finesse du repli, jamais la direction ni le risque.
- La bande RSI 38–45 du brief est corrigée en **38–55 après mesure** : sur 102 replis
  BTC, le RSI(14) H1 d'une bougie qui referme au-dessus de son EMA50 est de 45 à 60
  (0 occurrence sous 45). La borne basse était vide *par construction*, pas sélective.
  La zone de valeur est portée par le contact avec l'EMA50 ; le RSI ne sert qu'à
  refuser une remontée déjà chaude.
- Le mode n'est **pas** modifiable à chaud (absent de `RUNTIME_ALLOWED_KEYS`) : c'est
  un choix de déploiement, pas un curseur de dashboard.

**Mesuré** (48 scénarios : 3 paires × 2 modes × 4 pentes × 2 niveaux de frais,
24 mois, coupe-circuits levés — D-059) : **aucun scénario positif**. PF de 0,11 à 0,62,
N de 14 à 39 trades par paire et par 24 mois. Le mode H1 ne crée pas d'arête : sur BTC
il améliore le PF à pente égale (0,46 contre 0,29 à 1,5) mais sur ETH et SOL il fait
moins bien (0,50 contre 0,62 ; 0,34 contre 0,45). Son seul avantage mesurable est un
drawdown plus faible sur BTC (13–14 % contre 18–31 %), parce qu'il trade moins.

**Conséquence** : l'hypothèse « élargir le repli débloque N ≥ 100 » est **invalidée**.
Le plafond n'est pas la finesse du setup mais la **cadence de décision H4** (une
opportunité H1 sur quatre est seulement vue) et la taille de l'échantillon par paire.
La prochaine mesure utile est une cadence H1 ou un portefeuille multi-paires, pas un
troisième filtre d'entrée.

---

## D-059 — Le balayage lève les coupe-circuits permanents et affiche le gel

**Contexte** : en production, `max_consecutive_losses` et `max_drawdown_from_hwm_pct`
sont des **verrous durs** : après quatre pertes consécutives ou −10 % depuis le plus
haut, plus aucun achat n'est approuvé et rien ne réinitialise le compteur. C'est le bon
comportement pour protéger un compte réel. Dans un backtest de 24 mois, il tronque la
mesure au premier mauvais mois : sur BTC en mode `EMA50_H4` pente 2,0, **9 trades avec
verrou** contre **22 sans**, dernier trade le 2025-08-02 contre 2026-10-15. Les
« 2 à 9 trades par paire et par an » du balayage précédent étaient donc en partie un
artefact du verrou, pas seulement la rareté du setup.

**Décision** :
- `sweep --max-consecutive-losses N` et `sweep --max-hwm-drawdown FRACTION` permettent
  de lever explicitement ces deux plafonds (0 / `None` = valeur du `.env`). Le bandeau
  d'en-tête affiche les valeurs levées.
- Chaque scénario expose une colonne `gel` (nombre de rejets par coupe-circuit
  *permanent*) et la date du dernier trade. Le verdict ajoute `GEL` dès qu'un
  coupe-circuit a rejeté : un run tronqué ne peut plus se lire comme une mesure pleine.
- Les coupe-circuits quotidiens et hebdomadaires ne sont **pas** levés : ils se
  réarment d'eux-mêmes et ne tronquent pas la fenêtre.

**Pourquoi** : mesurer un filtre exige la fenêtre entière. Laisser croire qu'un run de
trois mois décrit 24 mois est pire que de lever un garde-fou dans une simulation
virtuelle — et le résultat ne s'en trouve pas flatté (PF 0,30 avec verrou, 0,30 sans).

**Écarté** : réinitialiser silencieusement le compteur à chaque mois (inventerait un
comportement absent de la production), et ne rien changer (mesure tronquée, verdict
trompeur).

---

## D-060 — Moteur swing Daily : stop structurel, breakeven et petit compte

**Contexte** : les cadences H1/H4 échouent sur le ratio bruit/frais (1,60 % aller-retour
chez Kraken) et le minimum notionnel (~10 EUR) interdit de fractionner un compte de
quelques euros. Le brief propose un mode `SWING_MACRO_D1` : positions de plusieurs jours
à semaines, cible > 8 %, ratio gain/risque ≥ 2,5.

**Décision** :
- **Entrée** (`EntryMode.SWING_MACRO_D1`) : clôture D1 > EMA50 **et** > EMA200, pente de
  l'EMA50 positive sur 5 jours, puis **ou** cassure du plus haut des 20 D1 précédentes
  (Donchian), **ou** rebond de valeur (la D1 précédente touche l'EMA20, la clôture
  repasse au-dessus, RSI 50–65). Le mode exige `DECISION_TIMEFRAME=1d` au démarrage.
- **Stop** (`StopMode.STRUCTURAL_D1`) : sous le creux des 10 dernières D1, mais jamais à
  plus de 5 % sous l'entrée. Le plancher de distance évite qu'un creux lointain ne
  réduise la taille sous le minimum de l'exchange. Si le creux manque, le trade est
  refusé — jamais de repli silencieux sur l'ATR.
- **Breakeven** : à +1 R, le stop passe à l'entrée + frais. En papier, il est écrit en
  base ; en live, il n'est **pas** déplacé tant que le stop distant n'est pas réécrit
  (aucun ordre réel n'est actif).
- **Sorties** : stop structurel prioritaire, puis clôture D1 sous l'EMA20, ou clôture
  au-dessus de la bande de Bollinger (20 ; 2,5) avec divergence baissière du RSI
  (nouveau sommet de clôture, RSI plus bas qu'au sommet précédent). Pas de take-profit
  fixe : `TAKE_PROFIT_R_MULTIPLE=0` dans le preset `swing`.
- **Petit compte** : `SMALL_ACCOUNT_FULL_ALLOCATION=true` autorise 100 % de l'équité en
  exposition sous `SMALL_ACCOUNT_THRESHOLD_QUOTE` (50 EUR). Le plafond de 50 % reste la
  règle par défaut ; le drapeau est le seul chemin vers 100 %. Un **plancher de
  viabilité** porte le risque au minimum nécessaire pour atteindre le notionnel minimal
  (`min_notional × distance_stop / équité`), plafonné à 5 % et jamais appliqué pendant
  l'étranglement après pertes. En dessous, le refus est explicite
  (`EXCHANGE_MIN_NOTIONAL_FAILED`).

**Pourquoi** : un stop D1 dans le bruit H1 est le défaut le plus coûteux d'un brief de
swing ; un stop structurel de 5 % maximum laisse respirer la position sans laisser
dériver la perte. Le plancher de viabilité est de l'arithmétique, pas une baisse de
garde-fou : 1,75 % de 20 EUR avec un stop à 5 % ne finance que 7 EUR, sous le minimum
Kraken.

**Mesure** (commande réelle, 80 pb/côté, coupe-circuits permanents levés) :
- 2021-11 → 2026-11 : BTC `both` 31 trades (7,2/an), réussite 9,7 %, payoff 4,06,
  PF 0,44, −126,94 EUR ; ETH 19 trades, PF 0,57, −55,45 EUR ; SOL 13 trades, PF 0,19,
  −55,46 EUR. **Aucun positif sur la fenêtre demandée.**
- Tout l'historique : BTC `both` 87 trades (7,1/an), réussite 17,2 %, payoff 10,56,
  PF 2,20, +148,9 % ; BTC Donchian seul 37 trades (3,0/an), réussite 35,1 %, PF 4,23,
  +199,9 % ; ETH `value` 54 trades (5,2/an), PF 1,67, +36,5 %. La réussite de 40 %
  n'est **jamais** atteinte : le système est un trend-following à faible taux de
  réussite et fort payoff.
- Durée moyenne des positions : 9,4 jours sur BTC, 5,8 sur ETH — dans la cible 3–15
  jours. Sorties : 39 stops / 46 EMA20 / 2 Bollinger sur BTC.

**Conséquence** : l'implémentation est complète et testée, mais **le mode n'est pas
activé** : sur les cinq dernières années, il perd de l'argent sur les trois paires. Le
résultat positif sur l'historique complet est porté par les cycles 2014–2021 ; il ne
prouve pas une arête actuelle. Ne pas figer les paramètres sur ces échantillons, ne pas
activer le réel.

**Écarté** : un take-profit fixe à 2,5 R (le brief demande des cibles > 8 %, mais la
sortie de tendance capture les mouvements plus longs) ; un stop ATR H4 pour un trade D1
(le bruit ferme la position avant que la thèse soit jouée) ; 100 % d'exposition sans
drapeau (le plafond de 50 % reste la protection par défaut).

---

## D-061 — Quatre chantiers swing : LLM D1, Chandelier, infrastructure D1, breakeven live

**Contexte** : D-060 a livré le moteur `SWING_MACRO_D1` mais l'a laissé non activé —
négatif sur 2021-11 → 2026-11, positif seulement sur l'historique complet. Le brief
demande de trancher quatre points avant toute conclusion : (1) le LLM ajoute-t-il de la
valeur sur les signaux D1, (2) une sortie Chandelier bat-elle l'EMA20, (3) l'infra D1
(migration, scheduler) est-elle prête, (4) le breakeven live peut-il déplacer un stop
sans jamais laisser la position nue.

**Décision** :

1. **Évaluation LLM D1, sans ordre.** `SignalClient.propose(..., decision_timeframe=)`
   accepte `4h` et `1d`. Le prompt H4 (`SYSTEM_PROMPT`, `v3`) reste **intact** pour ne
   pas déplacer le golden ; le D1 a son propre `D1_SYSTEM_PROMPT` et
   `D1_PROMPT_VERSION="v3-d1"`, avec un payload d'indicateurs D1 uniquement (EMA20/50/200,
   Donchian 20, Bollinger, RSI, ATR, creux 10 D1, plus haut Chandelier). Aucun indicateur
   H4 ne fuit dans le prompt D1. `scripts/eval_llm_swing.py` rejoue les trades du
   backtest, interroge le modèle et n'écrit ni ordre ni ligne en base :
   `app/backtest/llm_eval.py` calcule **rétention** (gagnants approuvés / gagnants) et
   **filtration** (perdants rejetés / perdants). Un dénominateur vide reste `n/a`, jamais
   0 %.
2. **Sortie Chandelier optionnelle, EMA20 par défaut.** `SwingExitMode.EMA20` conserve
   le comportement historique ; `SwingExitMode.CHANDELIER` ajoute
   `max(High_20) − 3,0 × ATR_14`, cliquet uniquement haussier, sortie sur **clôture**
   strictement sous le niveau (`TREND_EXIT_CHANDELIER`). Le stop structurel intrabar
   reste en place et n'est jamais desserré. `SWING_EXIT_MODE`,
   `SWING_CHANDELIER_PERIOD` et `SWING_CHANDELIER_ATR_MULTIPLE` sont lus depuis
   l'environnement, insensibles à la casse. Le champ D1 s'appelle
   `highest_high_chandelier` pour rester correct si la période change.
3. **Infrastructure D1.** La migration `0009_allow_1d_timeframe.sql` élargit le CHECK de
   `market_candles_history` à `('1h', '4h', '1d')`, ajoute `86400` à la vue de couverture
   et conserve `security_invoker = true`. Le scheduler attend **300 s** après la clôture
   D1 (00:05 UTC) au lieu de 5 s, pour laisser Kraken finaliser le chandelier.
4. **Breakeven live sûr.** `update_stop_loss_order()` n'annule jamais un stop sans
   remplacement garanti : si l'exchange supporte `editOrder`, il édite sur place ; sinon
   il **crée** le nouveau stop, vérifie qu'il est ouvert au bon prix, puis annule
   l'ancien. Si la création échoue, l'ancien stop reste ; si l'annulation échoue, le
   nouveau stop est retiré et l'erreur est remontée. Un stop plus bas est refusé, un prix
   inchangé est un no-op, un stop déjà terminé n'est pas remplacé. **Aucun ordre réel
   n'est envoyé** : la fonction est testée hors réseau et n'est pas branchée en
   production.
5. **Critères swing révisés.** Abandon du winrate ≥ 40 %, incompatible avec un payoff de
   10. Nouveaux critères sur l'historique complet, nets de 80 pb/côté : **PF ≥ 1,35**,
   **payoff ≥ 3,5**, **MDD ≤ 20 %**, **N ≥ 50 trades**. Un filtre ne se fige pas sur
   moins de 50 trades.

**Mesuré — Chandelier vs EMA20, 80 pb/côté, coupe-circuits levés** :

| Fenêtre | Paire | EMA20 | Chandelier |
|---|---|---|---|
| 2021-11 → 2026-11 | BTC | 31 trades, PF 0,44, −12,69 % | 23 trades, PF 0,77, −4,81 % |
| 2021-11 → 2026-11 | ETH | 19 trades, PF 0,57, −5,55 % | PF 0,38 |
| 2021-11 → 2026-11 | SOL | 13 trades, PF 0,19 | 11 trades, PF 0,14 |
| tout l'historique | BTC | 87 trades, PF 2,20, +148,89 % | **71 trades, PF 2,81, +192,47 %, DD 11,99 %** |
| tout l'historique | ETH | 74 trades, PF 1,10, +6,08 % | **62 trades, PF 1,93, +68,29 %** |
| tout l'historique | SOL | 13 trades, PF 0,19 | 11 trades, PF 0,14 |

Le Chandelier **améliore** BTC et ETH sur l'historique complet (PF et drawdown) mais ne
retourne pas la fenêtre récente : BTC reste négatif (−4,81 %), ETH et SOL se dégradent.
Il ne satisfait pas les nouveaux critères sur la fenêtre demandée. BTC et ETH passent
PF/payoff/MDD/N sur l'historique complet, SOL non.

**Mesure LLM D1 — réelle mais négative.** Après réparation du runner Ollama
(installation locale 0.40.14 dans `~/.local/opt/ollama-0.40.14`, second serveur sur
`127.0.0.1:11435`, `OLLAMA_MODELS=/mnt/e/models/ollama`), `scripts/eval_llm_swing.py`
a rejoué les **144 trades** du mode Chandelier sur tout l'historique. Les 144 appels sont
**non dégradés** :

| paire | trades | gagnants | approuvés | rétention | filtration |
|---|---|---|---|---|---|
| BTC/EUR | 71 | 14 | 16 | 42,9 % | 82,5 % |
| ETH/EUR | 62 | 8 | 12 | 12,5 % | 79,6 % |
| SOL/EUR | 11 | 1 | 2 | 0,0 % | 80,0 % |
| **global** | **144** | **23** | **30** | **30,4 %** | **81,0 %** |

Le modèle **filtre 81 % des perdants** (98/121), bien au-delà des 25 % demandés, mais il
**rejette 69,6 % des gagnants** (16/23) : rétention 30,4 %, très en dessous des 90 %
exigés. Verdict `KO rétention<90%`. Il est donc trop sélectif, pas assez discriminant :
la confiance moyenne des BUY (0,62) ne sépare pas gagnants (0,65) et perdants (0,62), et
le seuil de confiance n'est pas le levier. La mesure est reproductible avec la commande
du `HANDOFF_SWING_D1.md` §4. Ollama système reste cassé (`llama-server` absent de
`/usr/local/lib/ollama`) ; le serveur local est un contournement documenté, pas une
réparation de l'installation système.

**Conséquence** : les quatre chantiers sont **implémentés et testés**, mais aucun ne
produit de mesure positive nette qui justifierait d'activer le mode swing ou le réel.
Le mode reste **non activé**, `SWING_EXIT_MODE` reste `ema20` par défaut, et le chemin
live reste fermé. Le prochain levier reste l'entrée ou un filtre de régime, pas un
quatrième filtre ni l'exécution.

**Validé par** : 581 tests Python (dont `test_exit_rule.py`, `test_llm_eval.py` et les
extensions scheduler/settings/risk-gate/feed/swing/live/LLM/schéma) ; migration 0009
appliquée sur Supabase (`db_migrate.py status` → 9 appliquées, 0 en attente) ; golden
re-béni après vérification que les métriques sont identiques (seuls `created_at` et
`risk_fingerprint` changent).

## D-062 — Dashboard : historique en liste blanche et coupe-circuits en lecture seule

**Contexte** : le dashboard gagne des jauges de risque et un panneau d'activité qui
affiche les signaux, les évaluations du pare-feu, les ordres et les positions. Ces
écrans ne doivent pas devenir une porte dérobée vers la base ni permettre de désarmer
un coupe-circuit permanent d'un clic. Le panic-sell exige déjà un TOTP frais ; les
réglages ordinaires, non.

**Décision** :

1. Le proxy Node `GET /api/history/{table}` n'accepte que `llm_signals`,
   `risk_evaluations`, `orders` et `positions`. Tout autre nom est refusé en `400`
   **avant** l'appel à FastAPI. La limite est bornée à 1..50 (défaut 20).
2. Le dashboard expose en lecture seule `max_weekly_loss_pct`,
   `max_drawdown_from_hwm_pct` et `max_consecutive_losses`, ainsi que le risque par
   achat du preset. Ces plafonds restent modifiables par la configuration
   d'exploitation et les presets, pas par le formulaire web.
3. Les réglages ordinaires restent protégés par la session 2FA seulement ; le
   panic-sell garde son TOTP frais. La question d'un TOTP frais pour les réglages
   reste ouverte, car elle ajoute une friction à chaque ajustement.
4. Les volumes affichés sont bornés : 200 points d'équité, 14 jours d'histogramme,
   50 lignes d'activité. Le dashboard supervise ; il n'exporte pas la base.

**Conséquence** : un XSS ou une session volée ne peut ni extraire un historique
arbitraire, ni désarmer les coupe-circuits permanents, ni liquider sans TOTP. Un
opérateur qui veut modifier les plafonds passe par la configuration, ce qui laisse une
trace et évite le clic réflexe.

**Validé par** : 26 tests Node (dont liste blanche, limite bornée, refus sans appel
API et session requise), 581 tests Python, `scripts/check.sh`, et un smoke test
Chromium headless (rendu, quatre périodes, infobulle, onglets, aucune erreur JS).

---

## D-063 — Le panic-sell réel constate chez l'exchange, puis écrit au ledger

**Contexte** : le panic-sell de l'API répondait `409` en mode live (« non câblé »).
Le seul bouton rouge n'existait donc qu'en papier, au moment précis où une position
réelle pouvait rester sans protection. La liquidation `paper` part des positions en
base ; en réel, la base peut être en retard sur l'exchange (stop déjà déclenché,
solde manuel oublié, ordre resté ouvert).

**Décision** :

1. `LiveBroker.panic_sell()` annule **tous** les ordres ouverts (liste globale, repli
   par symbole si l'exchange l'exige), puis vend au marché **tout solde de base**
   réellement détenu, y compris hors univers configuré. `emergency_exit` annule de
   nouveau les ordres du symbole avant la vente : un stop ne peut pas vendre pendant
   la liquidation.
2. Chaque vente est vérifiée après coup : un solde résiduel supérieur à 0,1 % de la
   quantité vendue est une erreur explicite, pas un succès silencieux.
3. `record_panic_exits()` écrit les **fills réels** au ledger, y compris pour un solde
   sans position en base (ligne `EMERGENCY_EXIT` sans `position_id`), puis force
   `EMERGENCY_HALT`. Un échec partiel lève `PanicLiquidationError` **après** la halte
   et l'API répond `502` avec la liste de ce qui reste exposé.
4. `KILL_SWITCH_API_KEY` / `KILL_SWITCH_SECRET` portent une seconde paire de clés
   dédiée au kill-switch : Kraken impose des nonces croissants **par clé**, donc le
   script ne peut pas signer pendant que le bot tourne avec la même clé. La paire est
   optionnelle ; son absence produit un avertissement, jamais un démarrage muet.
5. `scripts/live_probe.py` exécute le test 3.1 : un ordre **sous le minimum**, plafonné
   à 0,05 EUR, pour lire le rejet réel de l'exchange. Si l'ordre était accepté, il est
   revendu immédiatement et le script sort en erreur.

**Conséquence** : le panic-sell réel ne dépend plus de la fraîcheur de la base ; il
solde l'exchange d'abord, raconte ensuite. Une erreur de vente est visible (502) au
lieu d'un `200` rassurant. Le test 3.1 a été exécuté sur le compte principal
(0,0000007 BTC ≈ 0,05 EUR) : Kraken a répondu
`EGeneral:Invalid arguments:volume minimum not met`, aucun ordre créé.

**Validé par** : 7 tests de plomberie (panic complet, échec partiel, solde hors
univers, ledger suivi/non suivi, absence de solde), `test_panic_is_wired_in_live_mode`,
`live_probe.py --confirm-live` sur Kraken.

---

## D-064 — Mean Reversion Bollinger : hypothèse mesurée, rejetée

> **Note de numérotation** : le brief de session du 2026-11-05 demande de consigner
> cette décision sous « D-060 ». D-060 est déjà pris par le moteur swing Daily ; la
> présente décision porte donc **D-064**, sans réattribuer l'ancien numéro.

**Contexte** : après l'échec du trend-following sur 2021-2026 (2.9, 2.11), la
directive de session demande d'explorer une famille opposée : acheter la peur
(clôture sous la bande basse, RSI < 32, volume en hausse) et vendre le retour à la
moyenne. Le risque évident est de confondre l'hypothèse avec l'effet du filtre de
tendance déjà présent dans le pare-feu.

**Décision** :

1. `app/backtest/mean_reversion.py` implémente la règle demandée, avec sortie au
   contact de la bande médiane (`touch`) ou à sa clôture (`close`), et stop structurel
   sous le creux des 10 bougies.
2. Le pare-feu reste l'autorité : chaque intention passe par `evaluate()`. Un champ
   `RiskConfig.allow_counter_trend` (défaut `False`, **jamais lu depuis l'env**) permet
   au seul laboratoire de neutraliser le filtre EMA200 pour mesurer l'hypothèse brute.
   La variante est étiquetée `raw` et n'est pas activable.
3. Mesure 2021-2026 sur BTC/ETH/SOL, H4 et D1, RSI 30/32/35, `touch`/`close`,
   80/100 pb : 144 scénarios. **Hypothèse rejetée** : 18 nets positifs, 5 PF ≥ 1,30,
   tous avec 2 à 6 trades. La variante `raw` plafonne à PF 0,85 et perd 4 804 EUR
   cumulés. Le filtre de tendance explique les rares cas positifs, pas la règle
   moyenne-réversion.
4. Ne pas figer de seuil sur ces échantillons, ne pas ajouter de quatrième filtre.
   Le seuil d'activation réel (PF ≥ 1,30 et N ≥ 100 sur la fenêtre) reste inchangé et
   **non atteint**.

**Conséquence** : deux familles testées (suivi de tendance et retour à la moyenne),
aucune ne produit d'espérance positive robuste sur la fenêtre récente. Le prochain
levier reste la cadence de décision ou la couverture multi-paires, pas l'exécution.

**Validé par** : `backtest.py meanrev --months 60 …` → 144 scénarios, sortie conservée
dans `docs/BACKTEST.md` § « Mean Reversion » ; 4 tests unitaires (déclenchement,
refus du pare-feu, variante brute, deux modes de sortie).
