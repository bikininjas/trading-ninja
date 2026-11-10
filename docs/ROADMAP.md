# Feuille de route — Trading Ninja

Mise à jour : **10 novembre 2026**. Statuts : ⬜ à faire · 🟨 en cours ·
✅ exécuté et vérifié · ⛔ bloqué · 🔵 décision de Seb attendue.

**NO-GO pour le réel.** Le code de sécurité est intégré et Supabase est en 0013.
Le déploiement des services, la sauvegarde complète, la recette opérateur et la
qualification économique restent à démontrer. Le plan de passage de référence est
[GO_LIVE.md](GO_LIVE.md), portes R0–R7 ; aucune phase ci-dessous ne permet de le contourner.

Une étape commence avec un statut daté et une entrée dans [JOURNAL.md](JOURNAL.md).
Elle passe à ✅ après une vérification réelle. Les décisions sont consignées dans
[DECISIONS.md](DECISIONS.md). Les anciens résultats restent dans le journal et
[BACKTEST.md](BACKTEST.md), avec leur date et leur configuration.

## État vérifié de la version candidate

| Périmètre | Preuve | État |
|---|---|---|
| Correctifs de sécurité et de reprise | Lots 1–6, 744 Python dont 35 PostgreSQL, 31 Node et navigateur ; CI de 24a449c réussie | ✅ [Suivi](CORRECTIONS_PRE_GO_LIVE.md) |
| Schéma Supabase trninja | Treize empreintes conformes ; droits et intégrité contrôlés le 10 novembre | ✅ [Déploiement](DEPLOIEMENT_SUPABASE_2026-11-10.md) |
| Environnement reproductible | Python 3.13.17, lock exact, CI PostgreSQL/Node/navigateur | ✅ livré ; protections de branche à qualifier en R0 |
| Briefs I-01 à I-10 | Première version implémentée ; observation et recherche disponibles | ✅ code ; qualification de chaque brief encore ouverte |
| Installation et surveillance de la cible | Services homogènes, alertes réelles, sonde externe, TLS/accès privé | 🟨 R3 |
| Sauvegarde complète et restauration des données | Préparation ciblée du schéma effectuée ; recette complète non exécutée | ⬜ R3 |
| Rentabilité et financement d’un ordre sûr | Effectif et résultats hors calibration insuffisants | ⛔ R4 |
| Canary et exploitation réelle | Aucun ordre réel ni armement autorisé par les corrections | ⬜ R6–R7 |

## Phase 0 — Fondations ✅

Pare-feu déterministe, types SQL/Python, configuration bornée, isolation des modes,
RLS et vues privées sont livrés. Les migrations 0001–0013 sont versionnées et
appliquées sur la cible ; les fonctions privilégiées refusent les rôles publics.
Les preuves locales et distantes sont liées dans le tableau ci-dessus.

## Phase 1 — Moteur 🟨

Le moteur PAPER, l’API, le dashboard et le chemin LIVE sont écrits. Le capital
progressif et la sélection par classement restent réservés au papier.

- 🟨 **Qualification financière R2** : compléter la matrice d’interruption,
  pertes de verrou, timeouts et concurrence moteur/urgences ; les trois contrats
  d’arrêt brutal ne constituent pas une preuve exhaustive.
- 🟨 **Déploiement R3** : installer et identifier la même version sur les services,
  rapprocher les positions/intentions avant reprise et exercer les procédures.
- ⬜ **Surveillance R3** : clé lecture seule distincte, canal réel, détection des
  arrêts du moteur et du surveillant, accès privé/TLS, rétention et quota.
- ⬜ **Sauvegarde R3** : export complet, conservation hors machine et restauration
  avec données sur une base séparée. Les autorisations Supabase sont accordées.

## Phase 2 — Simulation 🟨

Les outils de rejeu, diagnostics, expériences indépendantes et mesures historiques
sont livrés. Le golden sert à détecter une régression ; ses deux pertes ne démontrent
aucune rentabilité. Les comparaisons négatives restent conservées dans BACKTEST.md.

- 🟨 **Protocole R4** : figer politique, modèle, prompt, leçons, données et coûts
  avant la période finale hors calibration ; comparer au cash et au buy & hold.
- ⛔ **Critère économique** : au moins 100 trades clos simulés, espérance nette
  positive, drawdown sous le plafond déclaré et plusieurs régimes de marché.
  Zéro trade ou zéro perte ne valide jamais une stratégie.
- ⬜ **Recette PAPER R5** : exécuter la durée, les scénarios de panne et les contrôles
  prescrits dans GO_LIVE.md sur la version candidate déployée.

## Phase 3 — Pré-live ⬜

Kraken spot n’offre pas de testnet utilisable dans l’adaptateur. `live_probe.py`
utilise exclusivement `validate=true` : son succès ne représente pas un ordre réel.
Le canary R6 exige les portes précédentes, un compte dédié, des clés qualifiées,
un ordre finançable dans le budget et l’autorisation explicite de Seb.

## Phase 4 — Argent réel ⬜

Le passage R7 exige un canary réconcilié, une observation réelle à budget constant
et une décision explicite de poursuite. Le budget de perte totale documenté reste
9,37 EUR ; aucun apport, hausse de risque ou relâchement des minima n’est automatique.

## Livraison documentaire

- ✅ **Lot 8 — Nettoyage des documents remplacés** *(2026-11-10)* :
  constats consolidés dans CORRECTIONS_PRE_GO_LIVE.md, besoins UI transférés dans
  les briefs C-01 à C-07. Liens relatifs, aides CLI, collecte des tests et index
  vérifiés ; publication sur master.
