# Construire et signer les packs 16 Go et 32 Go

La compilation locale sous Linux reste possible. Des workflows sur des machines standard GitHub permettent aussi la validation Windows et, après acceptation du projet, la signature SignPath. Les deux packs utilisent une seule base de code ; les paramètres sont générés depuis `config/packs.json`. Voir [Code signing policy](CODE_SIGNING.md).

Le code prépare la version 1.1.1. Les releases déjà publiées jusqu'à 1.1.0 restent non signées ; la présence d'un workflow ne signifie pas qu'un certificat a été attribué.

## Prérequis

Python 3.11+, Node.js/npm pour préparer le cache hors ligne, MinGW-w64 x64, windres, NSIS 3.09+ et 7-Zip.

Exemple Ubuntu :

```sh
sudo apt install g++-mingw-w64-x86-64-posix nsis 7zip python3-venv nodejs npm
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-build.txt
python tools/build.py --pack all --signing unsigned --include-parts
```

Cette commande prépare les composants communs, récupère les deux modèles figés, compile et produit les deux distributions. Prévoir de l’espace pour les modèles complets, les parties et les répertoires de travail. Le PC cible n’a besoin d’aucun outil de construction.

`--pack 16go` et `--pack 32go` sélectionnent un seul pack. `--offline` exige tous les caches nécessaires déjà présents ; aucun repli silencieux vers une autre version. `--setup-only` reconstruit les installateurs sans télécharger de GGUF ni réécrire les parties. `--include-parts` demande explicitement leur téléchargement et leur découpage ; les deux options sont incompatibles.

Pour travailler uniquement sur les setups : `python tools/build.py --pack all --signing unsigned --setup-only`.

## Réutiliser un contenu déjà préparé

Un répertoire de base doit contenir `wad` et `wad/support/payload-manifest.json`. Avant toute réutilisation, le script vérifie les fichiers présents contre le manifeste. Pour une base issue d’une release, vérifier aussi le SHA-256 du setup original avant son extraction. Le workflow de signature prépare sa base directement à partir des composants amont verrouillés et recompile toujours nos exécutables depuis les sources.

```sh
python tools/build_packs.py --pack all --signing unsigned \
  --base-payload build/payload \
  --work-root /tmp/opencode-local-build \
  --output-root dist \
  --model-dir downloads \
  --include-parts \
  --sevenzip /usr/bin/7z
```

Sans `--include-parts`, le script produit les ZIP de setup, notices, profils et sommes de contrôle ; les fichiers de modèle restent externes. Le pack 16 Go conserve les empreintes des deux parties déjà distribuées en 1.0.0.

Les sorties de travail sont propres à chaque profil. Le setup et son ZIP sont terminés et contrôlés avant leur publication atomique dans le répertoire de distribution. Ne pas utiliser les répertoires de compilation pour des données personnelles.

## Résultat

```text
dist/16go/Setup-16Go-1.1.1.zip
dist/16go/Setup-1.bin
dist/16go/Setup-2.bin
dist/16go/LISEZ-MOI-16Go.txt
dist/16go/PACK-16Go.json
dist/16go/SHA256SUMS-16Go.txt

dist/32go/Setup-32Go-1.1.1.zip
dist/32go/Setup-32Go-01.bin ... Setup-32Go-11.bin
dist/32go/LISEZ-MOI-32Go.txt
dist/32go/PACK-32Go.json
dist/32go/SHA256SUMS-32Go.txt
```

Chaque ZIP contient uniquement `Setup.exe`. Les parties n’apparaissent que si leur production a été demandée. Un GGUF complet portant le nom figé du profil peut également servir de source au setup.

## Configuration générée

`tools/pack_profiles.py` produit, pour chaque profil :

- `pack_config.h` : modèle, empreinte, tailles, parties, identité, contextes et RAM ;
- `pack-profile.nsh` : identité de l’installateur, source du modèle, tailles et espace disque ;
- `installation.id` : marqueur propre au pack ;
- `pack.json` : paramètres auditables.

Le lanceur et l’assembleur sont compilés avec le même en-tête. Le modèle OpenCode, l’alias du serveur, le GGUF installé et le manifeste doivent donc rester cohérents. Le pack 16 Go conserve l’identité historique pour permettre une mise à jour ; le pack 32 Go utilise ses propres chemins et clés de désinstallation.

Les ressources Windows sont compilées par windres puis liées au lanceur. Le manifeste demande `asInvoker`, sans élévation. La présence de VERSIONINFO n’est pas une signature Authenticode.

## Vérification

```sh
python tools/test_assembler_profiles.py
python tools/test_installer_paths.py --build-only
python tools/test_signing.py
python tools/check_model_compatibility.py --help
```

Le test de l’assembleur emploie de petits fichiers réels, y compris les erreurs de taille, de contenu et de destination. Le test NSIS extrait les véritables fonctions de production ; une compilation sans exécution Windows ne vaut pas un test du parcours d’installation.

Les rapports distinguent les contrôles natifs Linux, les compilations Windows et les essais sur le poste cible. Le grand modèle doit être lancé avec `--no-repack` pour rester sur le chemin de mémoire mappée vérifié. Aucune politique antivirus, règle de pare-feu ni autorisation du poste n’est changée par la construction.

## Signature en trois phases

Le mode de signature est obligatoire : `--signing unsigned` pour une construction
de validation, `--signing signed` pour importer des signatures réelles avec
vérification stricte. Le mode signé ne peut pas être simulé avec un simple drapeau.

### 1. Construire les exécutables maison

```sh
python tools/build.py --prepare-only --signing unsigned --setup-only
python tools/build_packs.py --phase native --signing unsigned --pack all \
  --base-payload build/payload --work-root build/signing \
  --artifact-root signing --setup-only
```

Cette préparation ne télécharge aucun modèle. Dans `signing/native/<profil>`,
chaque pack produit `OpenCodeLocal.exe`, `AssembleModel.exe` et `Uninstall.exe`.
Le désinstalleur est exporté à la compilation par `!uninstfinalize`, sans lancer
de programme Windows. Le stub intermédiaire d'export n'est jamais distribué.

Le manifeste enregistre le commit, les sources et les images PE originales.
Le workflow charge les six fichiers comme artefact GitHub puis les soumet à
SignPath. Les dépendances amont ne sont pas signées au nom de notre projet.
L'approbation humaine prévue par la politique SignPath reste nécessaire.

### 2. Importer les natifs signés puis construire les setups

Après le retour du service, en conservant les sous-dossiers `16go` et `32go` :

```sh
python tools/build_packs.py --phase setup --signing signed --pack all \
  --base-payload build/payload --work-root build/signing \
  --artifact-root signing --signed-native-root signing/signed-native \
  --expected-signer-sha256 "$OPENCODE_SIGNER_CERT_SHA256" \
  --setup-only --sevenzip /usr/bin/7z
```

L'empreinte attendue est le SHA-256 du certificat public de signature sur ses
octets DER. Ce n'est ni une clé privée ni l'empreinte d'un EXE. Elle ne doit être
configurée qu'après attribution et vérification indépendante du certificat.

La phase refuse un certificat inattendu, un fichier modifié ou supplémentaire,
une signature manquante et un changement de sources entre les étapes. Elle
recalcule le manifeste sur les octets signés, embarque le désinstalleur signé
sans le régénérer et produit `signing/setup/<profil>/Setup.exe`. Le workflow
soumet ensuite ces deux setups à une seconde demande de signature.

### 3. Vérifier et finaliser les ZIP

```sh
python tools/build_packs.py --phase finalize --signing signed --pack all \
  --artifact-root signing --signed-native-root signing/signed-native \
  --signed-setup-root signing/signed-setup \
  --expected-signer-sha256 "$OPENCODE_SIGNER_CERT_SHA256" \
  --output-root dist --setup-only
```

La finalisation peut s'exécuter sous Windows sans les modèles ni la base de
dépendances. Elle vérifie les signatures et exige que les natifs fournis soient
exactement ceux intégrés aux setups. Chaque ZIP contient uniquement le
`Setup.exe` final vérifié. `SIGNATURES.json` distingue le mode signé du mode de
validation non signé. Les fichiers `.bin` restent inchangés.

Sur Linux, les imports signés exigent `osslsigncode` 2.13+, une chaîne de
confiance valide, le certificat attendu et un horodatage vérifié. La présence
de la chaîne réelle SignPath dans le jeu de racines utilisé devra être vérifiée
lors de l'activation : un jeu de certificats TLS n'inclut pas nécessairement
toutes les racines de signature de code. Aucun échec ne doit être ignoré.

Sur Windows, la qualification utilise Authenticode et SignTool du SDK. Aucun
test ne modifie la confiance Windows ni n'installe une racine de test. Les fins
de ligne des sources sont fixées par `.gitattributes` pour conserver leurs
empreintes entre Linux et Windows.

Les fichiers [.signpath](.signpath) décrivent la configuration à importer après
acceptation. Ils ne créent pas le compte, les politiques ou un certificat sur
le service. Le workflow signé est manuel et ne publie pas automatiquement de
release. Les vérifications sans certificat ne doivent pas être présentées
comme une validation du service SignPath ou de la politique TEHTRIS du poste.

Les machines standard GitHub-hosted sont gratuites en temps de calcul pour les
dépôts publics. Le stockage temporaire des artefacts reste soumis aux quotas
et paramètres du compte ; leur taille et leur conservation sont limitées.
Les modèles ne sont pas téléchargés ni stockés dans les artefacts de signature.

## Provenance et licences

- `config/artifacts.lock.json` : binaires, runtime et composant de base verrouillés ;
- `config/packs.json` : modèles 16/32 Go, révisions, tailles et SHA-256 ;
- `config/offline/package-lock.json` : dépendances de démarrage ;
- `config/offline/grammar-assets.lock.json` : grammaires, requêtes et licences.

La construction locale non signée ne requiert ni GitHub Actions, ni Git LFS,
ni certificat. Une signature Authenticode réelle exige l'accès à un service ou
à un certificat approprié ; les builds publics existants n'en possèdent pas.
