## Corrections de fiabilité et d’intégrité du lanceur

Cette version met à jour le programme OpenCode Local et son installateur.
Le modèle **Qwen3.6-35B-A3B UD-IQ2_XXS reste identique à celui de la version
1.2.0**. Aucun nouveau modèle n’est téléchargé ou publié avec cette mise à jour.

### Mettre à jour une installation 1.2.0

1. Fermer les sessions OpenCode Local encore ouvertes et conserver le dossier installé.
2. Télécharger **Setup.zip** et en extraire `Setup.exe` : le ZIP contient uniquement cet exécutable.
3. Lancer le setup et choisir **le même dossier d’installation**.

Si aucun fichier GGUF ni aucune partie `.bin` n’est présent à côté du setup,
le modèle de l’installation existante est réutilisé. L’installateur contrôle
l’identité de l’installation, puis vérifie la taille et le SHA-256 du modèle
pendant sa copie dans le nouveau dossier préparé. L’ancienne installation
n’est remplacée qu’après réussite de ces contrôles. Les projets, réglages et
conversations restent dans leur dossier de données séparé.

Si des fichiers du modèle sont présents à côté du setup, ils doivent être
complets et valides. Un fichier externe incomplet ou invalide ne provoque pas
de bascule silencieuse vers le modèle installé.

### Première installation

Télécharger les huit fichiers **Modele-MoE-01.bin à Modele-MoE-08.bin** depuis
la [release 1.2.0](https://github.com/M4D135/OpenCode-Local/releases/tag/v1.2.0)
et les placer à côté du nouveau `Setup.exe`. Le GGUF complet du même modèle
peut remplacer ces huit parties. Les fichiers des versions 1.0.x et 1.1.0
appartiennent à d’autres modèles et ne conviennent pas.

La release 1.2.0 est conservée comme source de ces poids. Les identités, tailles
et empreintes de ses huit fichiers sont vérifiées via les métadonnées de
publication ; les poids ne sont ni retéléchargés ni dupliqués dans cette release.

### Vérifications et limites

La publication exige les **nouveaux contrôles Windows** de cette version :
identité des exécutables, contenu exact du setup, fonctions de l’installateur
et tests de régression du lanceur. Le rapport `VALIDATION-WINDOWS.json` indique
le commit et la version effectivement vérifiés. Les empreintes de `Setup.zip`,
du setup et des exécutables sont fournies avec la distribution.

**Aucune nouvelle inférence ni mesure de RAM du modèle n’est revendiquée pour
1.2.1.** Les résultats d’inférence de 1.2.0 restent des résultats historiques de
cette version, pas des tests du code Windows modifié.

### Signature et antivirus

**Version non signée. Acceptation TEHTRIS non vérifiée.** Les corrections portent
sur la fiabilité et l’intégrité du lanceur ; elles n’établissent pas la cause de
la détection signalée. Aucune exclusion, modification de l’antivirus, du pare-feu
ou des certificats de confiance n’est effectuée.
