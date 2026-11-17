# Taille de la base Supabase

L'objectif est de garder `pg_database_size(current_database())` sous **450 000 000
octets** (450 Mo décimaux). Le nettoyage se déclenche avant ce seuil ; ce n'est
pas une limite physique garantie pour toutes les écritures ou les fichiers WAL.
Une insertion externe massive, une panne du scheduler ou un manque d'espace
réutilisable peut dépasser le seuil entre deux mesures.

La migration `0018_storage_budget.sql` installe une tâche Supabase `pg_cron`
`tn-storage-budget`, toutes les 15 minutes, indépendante du WSL :

- elle mesure la taille et remplace une unique ligne de statut privée ;
- elle supprime au plus 1 000 digests de veille par passage, seulement après
  30 jours et après expiration de leur validité ;
- à partir de **390 Mo**, elle supprime au plus 10 000 bougies Kraken de
  plus de trois ans, au maximum une fois par jour ; les imports CSV sont conservés.
  Cela raccourcit l'historique
  de recherche ; il faut reconstituer les données pour rejouer ces périodes ;
- elle conserve positions, ordres, ledger, signaux, décisions, protections,
  mémoires et preuves de rejeu. Elle ne supprime aucun artefact de cycle ;
- elle conserve sept jours d'historique d'exécution de son propre job cron.

À 390 Mo l'API présente une alerte dans le dashboard ; à 425 Mo le statut SQL
devient `CRITICAL`, à 450 Mo `EXCEEDED`. Une exécution absente depuis plus de
45 minutes est indiquée comme maintenance inconnue. Le témoin dépend de l'API
et du dashboard ; ce n'est pas une notification externe garantie.

Contrôles en lecture, avec un rôle autorisé :

```sql
SELECT * FROM tn_storage_maintenance.status;
SELECT jobname, schedule, active FROM cron.job WHERE jobname = 'tn-storage-budget';
SELECT start_time, status, return_message FROM cron.job_run_details
 WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'tn-storage-budget')
 ORDER BY start_time DESC LIMIT 10;
```

L'utilisateur `tn_paper_api` ne peut que lire le statut ; les rôles publics,
le moteur et le worker de veille n'ont pas accès à cette maintenance. La tâche
est enregistrée sous l'identité administratrice de la migration, mais le code
exécuté est figé dans une fonction privée sans paramètres.

Une suppression PostgreSQL libère généralement l'espace pour **réutilisation**,
sans réduire immédiatement la taille des fichiers. Le vacuum ordinaire peut
réutiliser les pages ; `VACUUM FULL` réécrit la table, prend un verrou exclusif
et nécessite de l'espace supplémentaire. Il n'est jamais lancé automatiquement.
Si la taille reste élevée, il faut diagnostiquer les tables, les transactions et
la disponibilité du job avant toute maintenance lourde. Voir la documentation
[Supabase sur la suppression](https://supabase.com/docs/guides/database/postgres/data-deletion)
et [PostgreSQL sur VACUUM](https://www.postgresql.org/docs/17/routine-vacuuming.html).
