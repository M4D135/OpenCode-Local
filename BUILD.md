# Construire les packs 16 Go et 32 Go

La compilation s’effectue localement sous Linux, sans GitHub Actions. Les deux packs utilisent une seule base de code ; les paramètres sont générés depuis `config/packs.json`.

## Prérequis

Python 3.11+, Node.js/npm pour préparer le cache hors ligne, MinGW-w64 x64, windres, NSIS 3.09+ et 7-Zip.

Exemple Ubuntu :

```sh
sudo apt install g++-mingw-w64-x86-64-posix nsis 7zip python3-venv nodejs npm
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-build.txt
python tools/build.py --pack all
```

Cette commande prépare les composants communs, récupère les deux modèles figés, compile et produit les deux distributions. Prévoir de l’espace pour les modèles complets, les parties et les répertoires de travail. Le PC cible n’a besoin d’aucun outil de construction.

`--pack 16go` et `--pack 32go` sélectionnent un seul pack. `--offline` exige tous les caches nécessaires déjà présents ; aucun repli silencieux vers une autre version. `--setup-only` reconstruit les installateurs sans recopier les modèles en parties.

## Réutiliser un contenu déjà préparé

Un répertoire de base doit contenir `OpenCodeLocal.exe`, `wad` et `wad/support/payload-manifest.json`. Avant toute réutilisation, le script vérifie les fichiers présents contre le manifeste. Pour une base issue d’une release, vérifier aussi le SHA-256 du setup original avant son extraction.

```sh
python tools/build_packs.py --pack all \
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
dist/16go/Setup-16Go-1.1.0.zip
dist/16go/Setup-1.bin
dist/16go/Setup-2.bin
dist/16go/LISEZ-MOI-16Go.txt
dist/16go/PACK-16Go.json
dist/16go/SHA256SUMS-16Go.txt

dist/32go/Setup-32Go-1.1.0.zip
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
python tools/check_model_compatibility.py --help
```

Le test de l’assembleur emploie de petits fichiers réels, y compris les erreurs de taille, de contenu et de destination. Le test NSIS extrait les véritables fonctions de production ; une compilation sans exécution Windows ne vaut pas un test du parcours d’installation.

Les rapports distinguent les contrôles natifs Linux, les compilations Windows et les essais sur le poste cible. Le grand modèle doit être lancé avec `--no-repack` pour rester sur le chemin de mémoire mappée vérifié. Aucune politique antivirus, règle de pare-feu ni autorisation du poste n’est changée par la construction.

## Provenance et licences

- `config/artifacts.lock.json` : binaires, runtime et composant de base verrouillés ;
- `config/packs.json` : modèles 16/32 Go, révisions, tailles et SHA-256 ;
- `config/offline/package-lock.json` : dépendances de démarrage ;
- `config/offline/grammar-assets.lock.json` : grammaires, requêtes et licences.

Aucun workflow GitHub Actions, Git LFS ni certificat de signature n’est requis pour construire. Une signature Authenticode réelle exige un certificat approprié ; les builds fournis n’en possèdent pas.
