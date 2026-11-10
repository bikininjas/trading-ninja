# Candidat et protocole économique à figer avant la recette WSL

Ce document organise les preuves nécessaires. Il n'atteste ni rentabilité ni
installation. Le dump complet initial a été retiré des prérequis bloquants par
Seb le 10 novembre 2026 ; la base est vide d'ordres, positions, intentions,
capital, sorties en attente et snapshots financiers. Les sept signaux historiques
et les bougies restent des données, sans série de performance validante.

## Constat du compte, 10 novembre 2026

Lecture privée Kraken vers 14:17 UTC, sans ordre : **9,37 EUR disponibles et au
total**, aucun autre actif, aucun ordre ouvert. La clé permet le trading et ne
permet pas les retraits ; l'API n'annonce aucune restriction IP. Trois lectures
privées de tarifs confirment **0,80 % taker et 0,40 % maker par côté** sur BTC/EUR,
ETH/EUR et SOL/EUR. Une simulation maker ne doit pas supposer des fills taker à
moindre coût. Cette lecture du solde n'autorise pas son allocation.

Avec le profil prudent, 9,37 EUR de capital actif hypothétique, frais taker 0,80 %
et glissement 5 pb/côté, la sonde publique refuse les trois paires au filtre de
gain net. Au moment du diagnostic, leurs minima valent environ 3,95 / 3,69 / 3,75
EUR et le risque prévu au minimum représente 1,96 / 2,35 / 2,62 % du capital,
au-dessus du mandat de 0,5 %. Les prix et ATR changent : conserver l'heure et les
hypothèses avec chaque recalcul. Le dernier prix de cette sonde n'est pas une preuve
de fraîcheur source exploitable par le moteur.

Le budget total de perte de 9,37 EUR et le capital actif sont deux notions distinctes,
même si le solde disponible a aujourd'hui ce même montant. Ne pas augmenter le
capital, le risque, l'exposition ou la cadence pour franchir un minimum. Ajouter
du capital ne remplace pas une stratégie dont le gain net attendu est qualifié.

## Candidat PAPER proposé

Les exemples déployables fixent spot long, BTC/ETH/SOL en EUR, H4/H1, profil
prudent, risque prévu de 0,5 %, une position, frais 0,80 %, glissement 5 pb/côté,
classement en observation, sans capital progressif ni promotion. Le capital PAPER
de 9,37 EUR est virtuel et n'est pas un mandat sur les fonds du compte.

Au premier lancement sur WSL, archiver dans un dossier privé :

- le commit exact et les 13 empreintes de migrations ;
- le digest réel de `qwen3.5:9b`, prompt, leçons, politique et révisions appliquées ;
- les paramètres expurgés et leurs empreintes, Python 3.13.17 et les dépendances verrouillées ;
- les données source, leurs dates et leurs empreintes ;
- compte dédié, capital actif autorisé et budget de perte, avant tout canary.

Le candidat n'est pas figé tant que l'identité du modèle et les paramètres réels
du WSL ne sont pas capturés. Une inférence de repli HOLD est comptée comme panne,
sans devenir une décision validante du modèle.

## Mesure préenregistrée

1. Considérer les fenêtres déjà consultées du journal et le golden comme
   **exploration/régression**. Ne pas rebaptiser une période connue « validation
   vierge » après choix des paramètres. Le modèle peut aussi connaître une partie
   de l'histoire via son entraînement ; ce biais doit rester visible.
2. Avant de consulter une nouvelle fenêtre, figer tous les paramètres ci-dessus,
   les bornes UTC et les critères. Une validation prospective commence après ce
   gel, avec captures réelles du runtime et aucune fuite de données futures.
3. Comparer cash (0 rendement, mêmes apports) et buy-and-hold sur le même univers,
   les mêmes dates, capital, coûts, minima et valorisation finale. Exposer les
   refus, positions non closes et hypothèses de liquidation.
4. Évaluer séparément coûts de base et coûts défavorables (1,00 % de frais et
   10 pb de glissement par côté). Ne pas choisir après résultat la variante qui gagne.
5. Compter au moins 100 trades clos simulés issus de la politique gelée selon R4,
   sans additionner des variantes corrélées. Rapporter espérance nette, drawdown,
   concentration, régimes et incertitude par blocs temporels.
6. Exiger une espérance nette positive hors calibration et un drawdown sous les
   plafonds déclarés, avec incertitude compatible avec le mandat et faisabilité du
   compte démontrée. Un backtest mécanique ne prouve pas la performance du LLM.

Si le dimensionnement sûr refuse tous les ordres avec 9,37 EUR, conserver le
résultat **non finançable**. Les cycles sans achat servent à qualifier l'exploitation,
pas à fabriquer les 100 trades. Toute autre stratégie ou enveloppe est une décision
explicite suivie d'une nouvelle qualification, sans relâchement automatique.

## Recette opérationnelle et décisions restantes

Sur le WSL accessible, après go de Seb : installer le bundle, renseigner la
connexion SQL et les secrets admin, exercer TLS/TOTP, les deux webhooks et un
contrôle externe qui reste actif lorsque Windows/WSL s'arrête. Qualifier l'inférence
réelle, les reprises et les délais d'alerte. Le heartbeat et la progression sont
contrôlés séparément : budget de cycle de trente secondes, relu directement par
la sonde. Sa valeur et les délais bout en bout doivent être exercés sur le WSL,
avec ses cycles normaux et les frais/latences réels, avant R3.

R5 conserve 14 jours et 20 cycles sur une version stable. Ces durées ne peuvent
pas être remplacées par les tests de code. Le réel reste bloqué par les preuves
d'exploitation et d'économie, indépendamment du dump initial désormais optionnel.
