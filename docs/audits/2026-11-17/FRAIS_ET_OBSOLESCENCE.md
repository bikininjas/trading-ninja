# Requalification des frais et fichiers historiques — 17 novembre 2026

## Ce qui est établi sur les mesures du 5 novembre

Dans le commit `1d7eb26`, les commandes `backtest.py swing` et `meanrev`
construisent explicitement `PaperBroker(fee_bps / 10000, ...)` pour chaque
scénario. Son code prélève ce taux sur les deux fills ; les deux simulateurs
enregistrent les frais d'entrée et de sortie dans le PnL. Le paramètre
`cost_aware` n'existait pas dans le pare-feu de ce commit : le défaut corrigé
le 17 novembre sur sa transmission ne pouvait donc pas affecter ces mesures.
Cela qualifie **le chemin des frais**, pas l'exactitude de tous les chiffres,
la complétude des données ou la validité économique des stratégies. Le scénario
25 pb est une sensibilité à des frais réduits, pas une simulation d'ordre maker.

L'évaluation LLM `eval_llm_swing.py` du commit `b585d5e` faisait autre chose :
elle calculait le taux `--fee-bps` mais ne l'utilisait pas ; elle demandait le
tarif taker du compte avec repli sur la configuration. L'argument affiché ou
exporté ne prouve donc pas les frais réellement appliqués. Les 144 réponses et
le tarif effectif de cette exécution n'ont été retrouvés ni dans le dépôt ni au
chemin `/tmp/eval_llm_swing.json` indiqué au journal. Les paramètres complets et
l'empreinte des données de cette mesure ne sont pas conservés non plus. Son
ancien tableau reste historique, sans qualification à 80 pb ; il n'est pas
possible de reclasser ses gains/pertes ou de reproduire ses réponses sans ces
éléments. Réinterroger un modèle aujourd'hui produirait une nouvelle expérience.

## Nouveau contrôle mécanique

`requalify_fees.py` lit les bougies H4 sous transaction SQL **READ ONLY** et
repeatable read, sans appel LLM ni exchange, et écrit `fee-requalification.json`.
Fenêtre figée du 5 novembre 2021 08:00 UTC au 5 novembre 2026 04:00 UTC exclu ;
seules les H4 closes sont retenues. Les contraintes viennent du manifeste du
10 novembre 2026. Profil prudent PAPER isolé, 1 000 EUR par scénario, 5 pb de
glissement par côté ; frais de 25 et 80 pb, avec et sans risque net des coûts.
Swing : cassure ou rebond et sortie Chandelier, coupe-circuits permanents levés
comme dans l'étude citée. Mean reversion : RSI 32, sortie `touch`, filtre de
tendance activé et coupe-circuits prudent conservés. Les 36 scénarios sont
indépendants ; les sommes éventuelles ne sont pas un portefeuille.

| Simulation | Risque net des coûts | Frais/côté | Trades | PnL cumulé indépendant |
|---|---:|---:|---:|---:|
| Swing, 3 paires | non | 25 pb | 49 | −18,56 EUR |
| Swing, 3 paires | non | 80 pb | 51 | −47,77 EUR |
| Swing, 3 paires | oui | 25 pb | 49 | −15,90 EUR |
| Swing, 3 paires | oui | 80 pb | 51 | −33,91 EUR |
| Mean reversion, H4 et D1 | non | 25 pb | 17 | +4,68 EUR |
| Mean reversion, H4 et D1 | non | 80 pb | 17 | −31,41 EUR |
| Mean reversion, H4 et D1 | oui | 25 pb | 23 | −4,02 EUR |
| Mean reversion, H4 et D1 | oui | 80 pb | 9 | +0,86 EUR |

Les frais calculés ont été comparés à la somme des deux fills pour chacun des
266 trades. Les séries H4 présentent respectivement 2, 3 et 3 trous sur BTC,
ETH et SOL ; les jours D1 incomplets sont exclus, sans interpolation. Les
empreintes et résultats détaillés sont dans le JSON. Ce nouveau contrôle
n'est **pas** la reproduction exacte des tableaux de 2026-11-05 : code et règles
ont évolué, configuration historique et snapshot original manquent, contraintes
et historique ont pu être modifiés. Petits effectifs, réglages de laboratoire et
trous interdisent de déduire une rentabilité robuste ou un feu vert réel.

## Fichiers et commandes historiques

`backend/scripts/check_exchange.py` cible Binance, alors que le bot installé
utilise Kraken. Il reste une sonde autonome documentée et couverte par
`test_check_exchange.py` ; la retirer supprimerait cette fonctionnalité, pas du
code mort prouvé. Les scripts `deploy/enable-*-wsl.sh` et leur manifeste sont
les preuves/outils des anciennes livraisons : le manifeste fige leurs sources,
et son contrôle échoue désormais sur le code modifié. Ne pas régénérer ce
manifeste pour faire passer une ancienne livraison ; préparer et qualifier une
nouvelle release avant tout déploiement. Aucune suppression de fichier historique
n'a été justifiée par l'inventaire de références.
