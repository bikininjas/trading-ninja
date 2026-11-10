# Preuves historiques — audit du 9 novembre 2026

Les scripts et résultats de ce répertoire décrivent **master 39db447**, avant les
correctifs publiés les 9–10 novembre. Le snapshot Supabase décrit la cible alors
en 0009 ; elle est désormais en 0013. Ces fichiers ne donnent pas son état courant.

Les scripts `reproduce_memory.py` et `reproduce_sql.py` vérifient la présence des
défauts d’origine. Leurs assertions ne sont pas des tests de non-régression à
exécuter sur la version corrigée. Les contrats actuels sont dans `backend/tests/`.
Les fichiers JSON conservent les observations datées, sans secret.

Les rapports narratifs remplacés ont été supprimés des documents courants. Leurs
versions intégrales restent consultables dans Git :

- [Audit pré go live](https://github.com/bikininjas/trading-ninja/blob/b032e83/docs/AUDIT_PRE_GO_LIVE_2026-11-09.md).
- [Ancienne liste d’améliorations](https://github.com/bikininjas/trading-ninja/blob/b032e83/AMELIORATIONS_2026-11-09.md).

Pour l’état actuel, lire le [suivi des corrections](../../CORRECTIONS_PRE_GO_LIVE.md),
la [preuve Supabase](../../DEPLOIEMENT_SUPABASE_2026-11-10.md) et les
[portes du go live](../../GO_LIVE.md). Les besoins non réalisés sont conservés dans
les [briefs techniques](../../IDEES_ET_BRIEFS_TECHNIQUES.md).
