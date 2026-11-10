# Suivi des corrections pré go live — 10 novembre 2026

La base de l’audit reste le commit 39db447. Ses reproductions et snapshots sont
historiques : leurs assertions décrivent le défaut de cette version. Les nouveaux
contrats de non-régression se trouvent dans les tests backend, et imposent désormais
le comportement corrigé. La qualification du réel reste celle de [GO_LIVE.md](GO_LIVE.md).

| Lot | Corrections | Preuves / publication |
|---|---|---|
| 1 | A02 kill-switch sans mutation exchange, A09 migration de maintenance privée, refus de promotion PAPER/LIVE par surcharge | 698 Python, 31 Node ; master `7fe2683`, SHA distant vérifié |
| 2 | A01 compte live relu avant dimensionnement final, A08 clôtures attendues/ticker daté, A04 raison HALT, A05 âge des coûts, A10 fills annulés partiels | 703 Python ; master `8cf3b92`, SHA distant vérifié |
| 3 | A03/D01 journal durable et identifiants avant envoi, UNKNOWN bloquant, reprise et verrou financier commun, fills cumulatifs/idempotents, protection du reliquat et IDs de stops, A07 sonde validate-only | Suite 731 puis contrat durable supplémentaire, 732 collectés ; master `f2a2785`, SHA distant vérifié |
| 4 | A06 autorisation durable atomique avec permission et état, révision conservée ; fenêtre H1 et continuité vérifiées avant entrée, prix sans horodatage refusé ou fondé sur un trade public daté de moins de 5 secondes, quota inconnu explicite, dépendances figées et CI | 738 Python dont 31 SQL, 31 Node, navigateur ; master `ed0a750`, SHA distant vérifié |
| 5 | Rapport panic durable conservé, pertes consécutives non réinitialisées, snapshot cash interdit avec reliquat, frais du fill non suivi conservés | 740 Python puis 14 contrats ciblés dont un SQL ajouté : 741 collectés ; master `50b4304`, SHA distant vérifié |
| 6 | Arrêt brutal réel d’un processus sur PREPARED/ACKNOWLEDGED/FILLED, exclusion concurrente, HALT immédiat et reprise bloquée, reçus durables après reconnexion | 744 Python dont 35 PostgreSQL passés, avec 3 contrats d’arrêt brutal réel ; master `24a449c`, CI réussie (42772119512) |
| 7 | Application Supabase 0010–0013 après autorisation, contrôles distants et intégrité | Treize empreintes conformes ; master documentaire `b032e83`, CI réussie (42774443151) ; preuve de déploiement liée ci-dessous |

La CI cible Python 3.13.17, disponible via uv, sur un environnement installé depuis
`requirements.lock`. Le Python système et l’ancien interpréteur de travail ne sont
pas remplacés. Les captures de cycle restent liées à leur environnement de calcul ;
un changement de Python n’autorise pas le rejeu d’un paquet incompatible.

La suspension bloque les nouvelles **autorisations** d’achat. Une intention déjà
préparée durablement peut finir ; elle est rapprochée avant tout nouvel envoi.
Le kill-switch conserve les stops ; la liquidation est une commande distincte.

## Cible et qualification restantes

Seb a explicitement autorisé les migrations le 10 novembre, puis les exports et
sauvegardes. Les migrations 0010–0013 ont été appliquées sur trninja et les treize
empreintes vérifiées. Écritures/rollback, droits fermés et intégrité des réglages
et de l’historique contrôlés. La préparation/restauration était ciblée sur le schéma
et les fonctions ; la sauvegarde complète reste à qualifier.
Voir [DEPLOIEMENT_SUPABASE_2026-11-10.md](DEPLOIEMENT_SUPABASE_2026-11-10.md).

La CI GitHub du lot 4 a réussi : [run 42771455250](https://github.com/bikininjas/trading-ninja/actions/runs/42771455250),
Python/SQL/Node/index/navigateur sur `ed0a750`. Chaque nouveau commit doit
repasser le workflow ; les protections de branche restent à qualifier séparément. De même, les
pannes simulées ne remplacent pas la recette opérateur sur la cible, les tests
multi-processus de toute la matrice d’interruption, l’installation du surveillant,
le canal d’alerte réel, la rétention et la validation économique.

Aucun ordre réel, augmentation de budget, installation de clé, armement live ou
canary n’est autorisé par ces corrections. Les durées PAPER et de stabilisation
réelle du plan ne sont pas réputées acquises.
