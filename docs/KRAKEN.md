# Kraken spot — intégration et exploitation

État au **10 novembre 2026**. Le moteur utilise CCXT **4.5.92**, version figée dans
`backend/requirements.lock`. L’exécution LIVE est écrite ; le passage au réel reste
soumis à [Roadmap](ROADMAP.md). Les observations de clés, tarifs et minima de
novembre 2026 conservées dans le journal ne valent pas qualification de la clé cible.

## Modules et responsabilités

| Module | Rôle |
|---|---|
| `app/exchange/kraken.py` | Contraintes marché, précision Decimal, bracket et contrôle des permissions |
| `app/market/feed.py` | Bougies clôturées, indicateurs et fraîcheur du prix source |
| `app/execution/factory.py` | Choix du broker selon le mode ; aucune promotion PAPER vers LIVE par surcharge |
| `app/execution/live.py` | Création/annulation/remplacement d’ordres, fills et protection distante |
| `app/execution/journal.py` | Reçus durables des mutations exchange |
| `app/core/coordination.py` | Verrou financier commun au moteur et aux urgences |
| `app/runtime/execution_recovery.py` | Rapprochement des intentions après interruption |
| `app/runtime/reconciliation.py` | Comparaison ledger/exchange et application contrôlée |

`risk_gate.py` reste pur : il valide et dimensionne, sans appel à Kraken.
L’adaptateur conserve les montants en chaînes/Decimal ; un arrondi ne peut pas
augmenter la quantité validée ni abaisser le stop validé.

## Permissions et clés

`verify_key_permissions()` lit `GetApiKeyInfo` au démarrage LIVE. Retrait autorisé,
permission requise manquante ou réponse illisible entraînent un refus. La recette
de la clé cible et des restrictions IP reste à exécuter avant R6.

Les alias `KRAKEN_API_KEY`/`KRAKEN_SECRET_KEY` sont supportés, ainsi que
`EXCHANGE_API_KEY`/`EXCHANGE_SECRET`, prioritaires. Une clé d’urgence distincte peut
être configurée avec `KILL_SWITCH_API_KEY`/`KILL_SWITCH_SECRET` ; une paire incomplète
est refusée. Le script de liquidation avertit s’il réutilise la clé principale.
Le surveillant exige sa propre paire `OBSERVER_API_KEY`/`OBSERVER_SECRET` en lecture seule.

Kraken exige des nonces croissants par clé. Les verrous financiers du projet
coordonnent ses mutations ; ils ne couvrent pas un autre client signant avec la
même clé. La concurrence entre processus et les clés dédiées restent à qualifier
sur l’installation cible. `adjustForTimeDifference` ne résout pas cette question.

## Entrées, protections et reprise

L’entrée approuvée utilise un achat marché avec protection attachée
`close.ordertype=stop-loss`. Un stop indépendant utilise `stopLossPrice` dans CCXT,
pas `stopPrice`. Le take-profit reste géré localement : aucun OCO spot natif n’est
supposé. Le compte dédié évite de confondre les avoirs du bot et des avoirs manuels.

Avant une mutation, le broker exige le verrou financier et un reçu durable
PREPARED. Les états ACKNOWLEDGED/FILLED sont rapprochés ; UNKNOWN bloque le renvoi
et la reprise automatique. Après un fill partiel, le cumul est dédupliqué et la
protection porte sur le reliquat confirmé. Le nouvel ID d’un stop remplacé est
persisté et réutilisé. Les contrats testés et leurs limites sont dans
[Preuves des correctifs](JOURNAL.md#correctifs-pre-go-live).

Le kill-switch **API** suspend les achats sans signer chez Kraken ni annuler les
stops ; la gestion d’une position existante continue. La liquidation par
`scripts/kill_switch.py` et le panic-sell sont des commandes financières distinctes.
Une autorisation d’achat déjà persistée peut finir après suspension.

## Coûts et faisabilité

Relire les contraintes via `market_info()` et le tarif via `resolve_taker_fee()`.
Les tarifs et minima d’anciennes mesures ne sont pas des constantes. La faisabilité
dépend aussi du prix, de l’ATR, des frais, du glissement et du plafond d’exposition.
Le plus petit ordre peut rester incompatible avec le budget de perte retenu.

Le profil `progressif` et ses enveloppes sont PAPER uniquement. Réserve et cagnotte
sont des écritures comptables, pas des retraits Kraken ; les clés du bot ne doivent
pas autoriser de retrait. Les critères économiques et tout changement de budget
relèvent de ROADMAP.md, pas du résultat d’un scan de marchés.

## Commandes

Depuis `backend/`, avec le venv installé selon son README :

```bash
.venv/bin/python scripts/check_kraken.py --markets
.venv/bin/python scripts/check_kraken.py --permissions
.venv/bin/python scripts/check_kraken.py --affordable --profile prudent --equity 9.37
.venv/bin/python scripts/check_kraken.py --scan
.venv/bin/python scripts/reconcile.py                 # lecture seule
.venv/bin/python scripts/kill_switch.py --dry-run     # aperçu, aucune liquidation
.venv/bin/python -m pytest tests/test_kraken_adapter.py tests/test_live_execution.py tests/test_execution_safety.py
```

La sonde privée `.venv/bin/python scripts/live_probe.py --confirm-live` utilise
systématiquement `validate=true`. Un timeout ou résultat inconnu renvoie un échec ;
aucune vente compensatoire ni absence d’exécution déduite des seuls ordres ouverts.
Les commandes de liquidation LIVE exigent leurs confirmations et les verrous de
configuration ; elles ne sont pas nécessaires pour consulter ces diagnostics.
