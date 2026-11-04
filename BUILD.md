# Construire localement

L’utilisateur final télécharge les fichiers de release et lance `Setup.exe`. Cette page s’adresse à la personne qui reconstruit le pack.

## Environnement

La version 1.0.0 est produite sous Linux avec MinGW-w64 et NSIS. Aucun service GitHub Actions, runner distant ni Git LFS n’est nécessaire.

Prérequis : Python 3.11 ou plus récent, Node.js/npm uniquement pour préparer le cache, compilateur C++ MinGW x64, NSIS 3.x.

Exemple Ubuntu :

```sh
sudo apt install g++-mingw-w64-x86-64-posix nsis python3-venv nodejs npm
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-build.txt
python tools/build.py
```

La préparation peut nécessiter environ 15 Go d’espace disque et une connexion pour les composants officiels. Le PC cible n’a besoin d’aucun de ces outils.

## Résultat

```text
dist/Setup.exe
dist/Setup-1.bin
dist/Setup-2.bin
dist/SHA256SUMS.txt
dist/LISEZ-MOI.txt
```

`Setup.exe` contient le lanceur, OpenCode, llama.cpp, ripgrep, les bibliothèques, les notices et le cache. Les deux `.bin` sont les morceaux bruts du modèle, de 1 500 000 000 et 1 240 937 888 octets. L’assembleur vérifie la taille et le SHA-256 du modèle entier avant de finaliser le fichier. Chaque asset reste sous la limite GitHub de 2 GiB.

## Sources figées et contrôles

- `config/artifacts.lock.json` : archives officielles, modèle et runtime Microsoft avec tailles et SHA-256.
- `config/offline/package-lock.json` : dépendances de démarrage npm. Les scripts d’installation et dépendances natives optionnelles sont exclus.
- `config/offline/grammar-assets.lock.json` : grammaires, requêtes et licences OpenTUI, avec empreintes.
- `config/opencode.json.template` : configuration du fournisseur local ; le lanceur remplace le port, la clé et le contexte.
- `src/launcher.cpp` : interface et gestion des processus Windows.
- `src/assemble_model.cpp` : assemblage streaming du modèle et vérification d’intégrité.
- `installer/OpenCodeLocal.nsi` : installateur et désinstallateur NSIS.

Le script ne prend pas une version « latest ». Un composant dont le contenu a changé est refusé. Les DLL Microsoft sont extraites du redistribuable officiel verrouillé et placées localement, sans installer un service ou modifier le système.

Après une première préparation, `python tools/build.py --offline` utilise les téléchargements et caches existants. Il faut conserver `downloads/`, le seed de `build/payload/wad/offline-cache` et le cache npm du compte ayant effectué la préparation : npm reconstruit les dépendances à partir de son cache, tandis que le script préserve les grammaires déjà téléchargées. Une entrée absente ou corrompue fait échouer ce mode sans repli vers Internet. `--prepared` réutilise directement `build/payload` et sert à recompiler l’intégration ou les documents sans refaire la préparation. Ces dossiers sont des sorties de construction ; ne pas y placer de données personnelles.

Le manifeste installé `wad/support/payload-manifest.json` décrit les fichiers et leurs empreintes. `SHA256SUMS.txt` décrit les fichiers de distribution.

## Vérification

```sh
python tools/fetch_payload.py --offline
python tools/seed_offline.py --verify-only
cd dist
sha256sum -c SHA256SUMS.txt
```

Les tests du modèle utilisent les homologues Linux des moteurs officiels ; `tools/fetch_payload.py --with-linux-tests` les récupère. Le rapport de validation distingue ces tests des essais Windows. Tester le setup, les chemins contenant des accents, le démarrage OpenCode et la désinstallation sur un vrai Windows avant de diffuser ce pack largement.

La signature Authenticode n’est pas incluse : elle exigerait un certificat du propriétaire. Le dépôt ne contient pas de clés ni de secrets de signature.
