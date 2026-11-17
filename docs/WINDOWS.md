# Windows et WSL2

La cible d'exploitation est Linux sous WSL2 avec systemd. Le dashboard est
accessible depuis le navigateur Windows sur `http://127.0.0.1:8081` lorsque le
service WSL écoute localement. La recette et l'accès privé/TLS restent décrits
dans [deploy/README.md](../deploy/README.md) ; ne pas ouvrir l'administration
sur Internet. L'arrêt de Windows, la veille du PC ou l'arrêt de WSL interrompent
les services : prévoir une surveillance extérieure au PC.

Depuis PowerShell Windows, après installation du dépôt et des dépendances dans la
distribution WSL, le lanceur délègue aux scripts existants. Depuis la racine du
dépôt, adapter explicitement le nom et le chemin Linux à l'installation voulue :

```powershell
wsl.exe --list --verbose
.\scripts\windows-wsl.ps1 -Distribution Ubuntu-24.04 -Repository /home/seb/GITRepos/trading-ninja -Action status
.\scripts\windows-wsl.ps1 -Distribution Ubuntu-24.04 -Repository /home/seb/GITRepos/trading-ninja -Action logs -Component engine
.\scripts\windows-wsl.ps1 -Distribution Ubuntu-24.04 -Repository /home/seb/GITRepos/trading-ninja -Action check
```

Le même lanceur accepte `start`, `stop` et `restart`, avec `api`, `dashboard`,
`engine`, `intel` ou `all` ; `all` inclut la veille MarketIntel lorsqu'elle est
installée. Une installation unique depuis WSL avec
`sudo bash deploy/install-manage-sudoers.sh` permet ensuite de gérer les services
sans mot de passe, y compris depuis PowerShell. Voir la
[procédure](../deploy/README.md#redémarrage-complet-sans-mot-de-passe).
`check` exécute les vérifications du dépôt, y compris les
contrôles SQL configurés. Il ne configure ni WSL ni les secrets ; conserver le
dépôt et les configurations privées dans le système de fichiers Linux du WSL.
Le lanceur transmet le code de sortie de WSL. Selon la politique PowerShell du
poste, son exécution peut nécessiter une configuration locale appropriée.

L'exécution native Windows n'est pas prise en charge : services systemd et
scripts Bash, verrouillage `fcntl`, permissions Unix des fichiers et verrou de
dépendances qualifié Linux ne sont pas interchangeables avec les services,
verrous et ACL Windows. Un port natif exige une implémentation et des tests sur
Windows pour ces éléments ; le lanceur ci-dessus utilise les composants Linux
existants et ne constitue pas ce port.
