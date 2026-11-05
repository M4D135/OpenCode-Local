# Construire le pack unique MoE

Les métadonnées de production et les réglages mémoire viennent de
`config/packs.json`, profil unique `moe`, version 1.2.0. Les dépendances et
le modèle sont figés par taille, révision et SHA-256.

## Construction locale

Prévoir Python 3.12+, Node/npm, MinGW x64 POSIX, NSIS et 7-Zip. Installer
`requirements-build.txt`, puis :

```bash
python tools/build.py --prepare-only --setup-only --signing unsigned
python tools/build_packs.py --phase native --signing unsigned --pack moe --base-payload build/payload --work-root build/signing --artifact-root signing --setup-only
python tools/build_packs.py --phase setup --signing unsigned --pack moe --base-payload build/payload --work-root build/signing --artifact-root signing --setup-only --sevenzip /usr/bin/7z
python tools/build_packs.py --phase finalize --signing unsigned --pack moe --artifact-root signing --output-root dist --setup-only
```

La préparation et le parcours `--setup-only` ne téléchargent jamais les poids.
Le résultat `dist/moe/Setup.zip` contient uniquement `Setup.exe`. Pour construire
localement les parties du modèle, télécharger la source figée puis finaliser
avec `--include-parts --model-dir downloads` au lieu de `--setup-only`.

## Validation et publication GitHub

`validate-unsigned.yml` construit les trois exécutables maison et le setup
sur Ubuntu, puis vérifie leurs métadonnées, diagnostics, contrôles de fichiers,
chemins NSIS et archives sur Windows Server 2022. La finalisation ZIP s'exécute
aussi sur Windows. Seul un petit bundle de candidats est transféré, avec une
rétention d'un jour ; les poids ne transitent pas par les artefacts Actions.

`publish-moe.yml` se déclenche uniquement après un push main validé dans ce
dépôt public. Il reprend le commit et les artefacts exacts, télécharge le modèle
figé, exécute les tests réels Linux avec le runtime b12089 et OpenCode, puis
publie selon `config/release-plan.json`. Une version déjà publiée est ignorée.

La publication vérifie `Setup.zip`, les rapports et le SHA-256 complet du GGUF.
Elle crée une release brouillon, transfère chaque partie de 1,5 Go séparément,
vérifie les empreintes retournées par GitHub et publie seulement après réception
de tous les fichiers. L'espace temporaire ne contient jamais deux copies
complètes du modèle. Les trois anciennes releases et les onze artefacts de
l'audit sont supprimés après vérification du remplacement. Aucun tag ni commit
n'est supprimé ; les IDs, versions et tailles attendus sont strictement contrôlés.

Les runners utilisés sont les runners standard du dépôt public. Le stockage
Actions reste distinct des releases ; aucun cache de plusieurs gigaoctets n'est
créé et les petits artefacts expirent après un jour.

## Signature future

Le parcours SignPath reste séparé, manuel et inactif tant que les accès et la
politique approuvée manquent. L'allowlist sélectionne exactement trois fichiers
`moe/*.exe`, puis `moe/Setup.exe`. Aucun binaire tiers n'est signé comme code du
projet. Les imports exigent le certificat attendu, l'horodatage, la confiance
système et la préservation du contenu compilé. Aucun certificat racine ni
exception antivirus n'est ajouté. Voir [CODE_SIGNING.md](CODE_SIGNING.md).
