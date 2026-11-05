## Protection de l’installation avant remplacement

Cette version ajoute une garde dans le setup pour préserver l’installation
existante lorsque le modèle attendu n’a pas été correctement préparé.
**Les blocages TEHTRIS signalés sur `OpenCodeLocal.exe` et `AssembleModel.exe`
restent non résolus. Cette maintenance ne garantit pas une exécution sans
alerte antivirus.**

### Changement apporté

Après le retour réussi de l’assembleur, et avant le premier renommage de
l’installation existante, le setup contrôle à nouveau le fichier GGUF préparé :

- le fichier doit exister et être un fichier ordinaire ;
- son chemin ne doit pas traverser une jonction ou un lien ;
- sa taille doit correspondre exactement au modèle attendu, avec une lecture
  sur 64 bits.

Si l’un de ces contrôles échoue, le setup interrompt l’installation avant de
remplacer le dossier existant. Cela couvre notamment le cas où l’assembleur
retourne le code `0` alors qu’aucun fichier de sortie n’est présent.

La vérification du contenu par SHA-256 reste effectuée par l’assembleur pendant
la copie. La garde supplémentaire du setup contrôle l’état du fichier produit ;
elle ne constitue pas une validation antivirus.

### Pack et modèle

Le pack reste unique. Le modèle **Qwen3.6-35B-A3B UD-IQ2_XXS**, les composants
du moteur et les paramètres de mémoire et de calcul restent inchangés.

**Setup.zip contient uniquement `Setup.exe`.** Pour mettre à jour une
installation 1.2.0 ou 1.2.1, conserver son dossier et sélectionner ce même
dossier dans le setup. En l’absence de GGUF ou de parties `.bin` à côté du
setup, le modèle installé est vérifié et copié dans le dossier de préparation.
Les projets, réglages et conversations restent dans leur dossier de données
séparé.

Pour une première installation, les huit fichiers **Modele-MoE-01.bin à
Modele-MoE-08.bin** restent disponibles dans la
[release 1.2.0](https://github.com/M4D135/OpenCode-Local/releases/tag/v1.2.0).
Ils doivent être placés à côté de `Setup.exe`. Le GGUF complet du même modèle
peut remplacer ces huit parties. Tout fichier externe présent doit être
complet et valide.

La release 1.2.0 est conservée comme source du modèle. La publication contrôle
les identités, tailles et empreintes des huit parties à partir de ses
métadonnées ; aucun poids n’est retéléchargé ou dupliqué pour cette maintenance.

### Vérifications et portée

La publication exige une **nouvelle validation Windows du commit de la version 1.2.2** :
contrôles des exécutables, contenu exact du setup, fonctions de l’installateur
et tests de régression du lanceur. Le test de la garde doit notamment refuser
une sortie absente malgré un code de retour réussi.

Le rapport `VALIDATION-WINDOWS.json` identifie le commit et la version testés.
Les empreintes de l’archive, du setup et des exécutables accompagnent la
distribution. Aucune nouvelle inférence, mesure de RAM du modèle ou validation
sur le poste de l’utilisateur n’est revendiquée pour cette version.

### Signature et antivirus

**Version non signée. Fonctionnement sous TEHTRIS non validé.** La cause des
détections photographiées n’est pas établie ; le setup et le programme peuvent
encore être bloqués. Cette version ne modifie ni l’antivirus, ni ses exclusions,
ni le pare-feu, ni les certificats de confiance.
