# Configuration de la chaîne SignPath

Ces fichiers préparent une intégration. Leur présence ne signifie pas que SignPath Foundation a accepté le projet, créé un certificat ou signé une version. Le workflow de signature échoue avant la compilation si les paramètres nécessaires manquent.

## Deux workflows séparés

`validate-unsigned.yml` compile les deux profils depuis les sources lors des pushes sur `main`, des pull requests et des déclenchements manuels. Les pushes ne changeant que la documentation indiquée dans `paths-ignore` ou les rapports de `validation/` ne relancent pas le build. Les pull requests restent contrôlées pour éviter un statut requis manquant. Le workflow ne référence aucun secret SignPath. Il contrôle les installateurs sous Windows et produit des rapports. Ses exécutables restent **non signés**.

`signpath-release.yml` est exclusivement manuel, sur `main`, dans `M4D135/OpenCode-Local`. Le slug de politique doit être saisi explicitement et correspondre à la configuration approuvée. Un nouvel essai de signature doit être un nouveau déclenchement manuel ; les relances de jobs sont refusées. Aucun des workflows ne crée de release GitHub, n’en remplace les fichiers ou ne télécharge les modèles.

Les jobs utilisent uniquement les runners GitHub hébergés standard `ubuntu-24.04` et `windows-2022`, avec des actions épinglées par SHA de commit dans `tool-versions.json`. Ils sont limités aux dépôts publics. Les minutes standard d’un dépôt public et le stockage des artefacts sont des postes distincts : **le stockage reste soumis aux quotas et à la facturation du compte**. Cette configuration ne promet pas un stockage gratuit illimité.

Les artefacts expirent après un jour. Le workflow unsigned transfère un seul bundle de candidats plafonné à 300 000 000 octets et des rapports ; ses ZIP de contrôle ne sont pas uploadés une deuxième fois. La signature nécessite les entrées natives et les entrées setup conservées par GitHub pour la provenance, puis un bundle de transfert Windows. Après validation, ce dernier est remplacé par les ZIP finaux et les rapports sous le même nom. Les exécutions répétées peuvent néanmoins cumuler du stockage jusqu’à expiration : vérifier les quotas avant de lancer plusieurs builds.

## Paramètres requis après accord de SignPath

Créer le secret de dépôt `SIGNPATH_API_TOKEN` avec un compte autorisé à soumettre des demandes. Le jeton n’apparaît jamais dans un fichier du dépôt. Le contrôle préalable ne reçoit qu’un booléen indiquant sa présence ; seuls les deux appels à l’action officielle SignPath reçoivent sa valeur.

Configurer ces variables de dépôt à partir des valeurs réellement attribuées au projet :

| Variable | Valeur attendue |
| --- | --- |
| `SIGNPATH_ORGANIZATION_ID` | Identifiant UUID de l’organisation SignPath |
| `SIGNPATH_PROJECT_SLUG` | Slug du projet approuvé |
| `SIGNPATH_SIGNING_POLICY_SLUG` | Slug de la politique de release imposant une approbation humaine |
| `SIGNPATH_NATIVE_ARTIFACT_CONFIGURATION_SLUG` | Slug associé à `native-artifacts.xml` |
| `SIGNPATH_SETUP_ARTIFACT_CONFIGURATION_SLUG` | Slug associé à `setup-artifacts.xml` |
| `SIGNPATH_EXPECTED_PUBLISHER` | Sujet complet et exact du certificat approuvé, tel que renvoyé par Windows |
| `SIGNPATH_EXPECTED_SIGNER_SHA256` | SHA-256 du certificat DER approuvé, 64 caractères hexadécimaux sans séparateur |

Le dernier champ n’est ni l’empreinte du setup ni le `Thumbprint` SHA-1 affiché habituellement par Windows. Ces deux identifiants d’éditeur doivent être obtenus du certificat attribué, pas supposés à partir d’un nom commercial. Lors d’un renouvellement de certificat, leur modification doit être revue avant une nouvelle signature.

Dans SignPath, activer le système de build de confiance GitHub, relier le dépôt et installer l’application GitHub SignPath conformément à ses instructions. Importer les deux configurations XML séparément ; ne pas conserver une configuration générique qui signerait tous les EXE ou DLL. `github-policy.yml` exige des runners GitHub hébergés et refuse les relances. La politique du service doit également limiter la provenance à ce dépôt, `main` et `.github/workflows/signpath-release.yml`, et imposer l’approbation humaine de chaque demande. Ces réglages côté service ne sont pas activés par le simple ajout de ces fichiers.

## Ordre des opérations

1. La préparation récupère et contrôle les dépendances tierces verrouillées, sans modèle et sans ancien setup utilisé comme source de nos exécutables.
2. Le build recompile, pour chaque profil, `OpenCodeLocal.exe`, `AssembleModel.exe` et `Uninstall.exe`. Le premier artefact SignPath contient exactement ces six fichiers, avec les chemins `16go/…` et `32go/…`.
3. La première demande signe ces six fichiers après approbation du service. L’import vérifie leur contenu compilé, la chaîne de confiance, l’horodatage et le certificat attendu. Les exécutables tiers ne sont pas sélectionnés pour signature.
4. Les installateurs sont construits en incorporant les exécutables signés. La deuxième demande porte seulement sur `16go/Setup.exe` et `32go/Setup.exe`.
5. Le job Windows vérifie les huit signatures, l’identité exacte de l’éditeur, le certificat, les fichiers intégrés et les petites fixtures des composants. Il doit réussir avant l’emballage final.
6. La finalisation produit les deux ZIP contenant chacun **uniquement `Setup.exe`**, recalcule leurs SHA-256 et conserve les manifestes de provenance. Les fichiers `.bin` des modèles ne changent pas.

Les XML imposent `ProductName=OpenCode Local`, le `ProductVersion` issu de `config/packs.json` et l’`OriginalFilename` exact de chaque exécutable. Aucun joker ne peut sélectionner un autre binaire. Les manifestes de build relient les fichiers à la même révision source ; le checkout Windows conserve les fins de ligne du dépôt pour vérifier les mêmes octets.

Le vérificateur Linux est compilé depuis la source officielle épinglée d’osslsigncode 2.13, avec contrôle de taille et de SHA-256 avant compilation. La chaîne ne désactive pas les contrôles de confiance ou d’horodatage. La vérification Windows utilise les mécanismes Authenticode et le SDK Windows ; aucun certificat racine n’est ajouté au système.

## Résultats et limites

Un artefact `signpath-output-…` est d’abord un **candidat** en attente du job Windows. Seul un workflow terminé avec succès et un fichier `verification/workflow-provenance.json` marqué `windows_validated_and_packaged` indiquent que les ZIP finaux ont été produits. En cas d’échec, aucun ZIP final n’est annoncé comme validé et aucune release n’est publiée.

Les demandes de signature, identifiants des artefacts d’entrée, SHA source et rapports restent associés au résultat. La signature ne prouve pas l’acceptation par TEHTRIS sur le poste cible, qui reste à vérifier séparément.

Références officielles : [intégration GitHub](https://docs.signpath.io/trusted-build-systems/github), [syntaxe des configurations](https://docs.signpath.io/artifact-configuration/syntax), [restrictions de métadonnées](https://docs.signpath.io/artifact-configuration/reference), [conditions de SignPath Foundation](https://signpath.org/terms.html), [facturation GitHub Actions](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions).
