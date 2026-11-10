# Migrations Supabase — 10 novembre 2026

Projet `trninja` (`sugbashfjondgmlejpnl`), PostgreSQL 17.11.0.011.
Autorisation explicite de Seb pour les migrations, puis pour les exports et sauvegardes.
Source : master `24a449c`. Les migrations existantes n’ont pas été modifiées.

## Application et contrôles

Les empreintes 0001–0009 de la cible correspondaient au dépôt. Les migrations
0010–0013 ont été appliquées dans l’ordre via le connecteur Supabase ; chacune
inclut son inscription atomique dans `public.schema_migrations`. Le registre
Supabase contient aussi les quatre applications datées. Les treize empreintes
applicatives concordent avec master après application.

| Migration | Registre Supabase | Résultat |
|---|---|---|
| 0010_capital_ledger | 20261110071316 | Appliquée |
| 0011_insights_and_controls | 20261110071327 | Appliquée |
| 0012_private_maintenance | 20261110071340 | Appliquée |
| 0013_durable_executions | 20261110071350 | Appliquée |

- 44 relations, 6 vues, 16 partitions des deux journaux critiques.
- RLS présente sur toutes les tables publiques ; aucune lecture/écriture de table
  ou vue autorisée à `anon`/`authenticated`.
- Aucune fonction privilégiée publique exécutable par ces rôles ; six vues en
  `security_invoker`. Les nouveaux index et colonnes sont présents.
- Test distant des écritures critiques et de l’unicité monoposition réussi,
  transaction entièrement annulée. Appels réels de maintenance refusés aux deux
  rôles exposés ; borne de 24 mois vérifiée.
- Aucun ordre ni position avant/après. Les trois réglages existants et les
  **323 374 bougies** conservent exactement leurs empreintes avant/après.
- Advisor sécurité : les avertissements EXECUTE ont disparu. Restent 41 notices
  INFO « RLS sans policy » : les tables sont privées et les droits publics fermés.
  [Explication Supabase](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).

Preuve structurée sans contenu financier :
[supabase_migrations.json](audits/2026-11-10/supabase_migrations.json).

## Sauvegarde : portée exacte

La revue automatique a initialement refusé un export global de tables privées,
avant l’autorisation explicite d’export. La préparation a donc conservé les
définitions de fonctions, ACLs, options des vues et empreintes de données, sans
exporter les contenus privés. Positions et ordres étaient vides ; les migrations
sont additives et ne réécrivent pas les réglages ni l’historique marché.

Le schéma 0001–0009 et les fonctions sauvegardées ont été restaurés sur un
PostgreSQL 17 local séparé, puis les quatre charges de migration exactes rejouées.
`db_migrate.py verify` y a réussi avant l’application distante.

Cette préparation ciblée **ne constitue pas une sauvegarde complète de la base**.
La sauvegarde complète, sa conservation hors machine et sa restauration avec les
données restent à qualifier en R3. Les autorisations nécessaires sont désormais
accordées ; elles ne constituent pas une preuve d’exécution de cette recette.

Le déploiement des services, la surveillance et la qualification économique
restent ouverts dans [GO_LIVE.md](GO_LIVE.md). Aucun ordre réel, démarrage de
moteur LIVE, suppression de données ou hausse de budget n’a été effectué.
