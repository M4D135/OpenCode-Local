# OpenCode Local — deux packs Windows hors ligne

Deux distributions distinctes pour utiliser **OpenCode avec un modèle local**, sans clé cloud ni installation préalable de Python, Node.js, npm ou Ollama sur le PC cible.

| Pack | Modèle | Taille du modèle | Contexte initial | Parties du modèle |
| --- | --- | ---: | ---: | ---: |
| **16 Go** | Qwen3.5-4B Q4_K_M | 2,74 Go | 8 192 tokens | 2, compatibles avec la version 1.0.0 |
| **32 Go** | Qwen3.8-27B UD-Q4_K_M | 16,46 Go | 16 384 tokens | 11, nommées `Setup-32Go-01.bin` à `Setup-32Go-11.bin` |

Les deux packs sont destinés à Windows 10/11 Intel ou AMD x64, en calcul CPU. Le modèle 27B demande davantage de temps de calcul. Les estimations mémoire et les sources des modèles sont détaillées dans [MODELES.md](docs/MODELES.md).

## Télécharger et installer

La [release](https://github.com/M4D135/OpenCode-Local/releases/latest) distingue les deux packs. **Chaque ZIP de setup contient seulement `Setup.exe`**, afin de permettre son téléchargement sous forme d’archive.

### Pack 16 Go

1. Extraire `Setup-16Go-1.1.0.zip`.
2. Placer `Setup.exe`, `Setup-1.bin` et `Setup-2.bin` dans le même dossier.
3. Lancer le setup, puis le raccourci **OpenCode Local**.

Les deux `.bin` déjà téléchargés pour la version 1.0.0 restent utilisables : le modèle et ses empreintes sont inchangés. Ils sont disponibles dans la [release initiale](https://github.com/M4D135/OpenCode-Local/releases/tag/v1.0.0).

### Pack 32 Go

1. Extraire `Setup-32Go-1.1.0.zip`.
2. Placer les **11 fichiers** `Setup-32Go-01.bin` à `Setup-32Go-11.bin` à côté de `Setup.exe`.
3. Lancer le setup, puis le raccourci **OpenCode Local 32 Go**.

Pour les deux packs, le fichier GGUF complet, portant le nom exact indiqué dans `PACK-16Go.json` ou `PACK-32Go.json`, peut remplacer toutes les parties. Un fichier complet présent mais invalide provoque une erreur explicite. Le setup vérifie la taille, le format GGUF et le SHA-256 complet avant de remplacer une installation.

**L’installation ne télécharge rien.** Tous les fichiers peuvent être récupérés sur une autre machine, puis transférés vers le PC cible par un moyen autorisé. Les archives GitHub « Source code » ne contiennent pas le programme ni les modèles. Chaque partie reste sous la limite des assets GitHub ; ne pas mélanger les fichiers des deux modèles.

## Utilisation

- **OpenCode** : choisir le dossier du projet et démarrer une session. Les lectures ordinaires sont autorisées ; les modifications et commandes demandent confirmation.
- **Discussion directe** : conversation avec le modèle, sans serveur HTTP. Ce mode n’offre pas les outils d’OpenCode.
- **Arrêter** : termine la session et les processus qu’elle a créés. Fermer le lanceur arrête également son moteur.
- **Diagnostic**, **Journaux** et **Guide** : informations locales pour examiner un problème.

L’interface native est en français ; OpenCode conserve son interface officielle. Le pack 32 Go dispose de ses propres données et raccourcis. Les deux packs partagent la même base de code, avec un profil généré depuis [config/packs.json](config/packs.json).

Les contextes proposés sont 4 096, 8 192 et 16 384 tokens. Une ancienne préférence de 32 768 tokens est remplacée par le défaut du pack. La sortie maximale et la réserve de compaction s’adaptent au contexte choisi. Le lanceur contrôle la RAM installée et la RAM libre avant de charger le modèle : au moins 6 Gio disponibles pour le pack 16 Go et 22 Gio pour le pack 32 Go.

Le pack 32 Go désactive le repacking CPU pour utiliser le chemin de chargement mémoire mappée vérifié. Une seule séquence de calcul est utilisée par session. Le débit dépend du processeur et de la mémoire ; aucun débit mesuré sur le poste cible n’est annoncé.

## Réseau et sécurité du poste

OpenCode communique avec son moteur sur **127.0.0.1**, avec une clé aléatoire par session et un port disponible entre `18741` et `18780`. Le serveur n’écoute pas sur le réseau local. Si les connexions localhost sont interdites, utiliser **Discussion directe** ; l’installateur ne modifie pas le pare-feu.

Les fournisseurs cloud, mises à jour automatiques, recherche web, plugins externes, partages de sessions et téléchargements de serveurs de langage sont désactivés. Les dépendances de démarrage et les grammaires sont incluses. Les commandes autorisées dans OpenCode restent des commandes normales : le pack n’est pas un bac à sable réseau.

**TEHTRIS a arrêté et mis en quarantaine le lanceur des versions 1.0.0/1.0.1 sur un poste.** La cause précise n’est pas connue. Les nouveaux lanceurs ajoutent des ressources de version, un manifeste `asInvoker` et un chargement de DLL système restreint, mais restent **non signés par Authenticode**. Ces changements ne prouvent pas une acceptation par l’EDR. Voir [TEHTRIS.md](docs/TEHTRIS.md) pour les empreintes, les constats et les informations nécessaires à l’analyse. Aucun antivirus ni aucune exclusion ne sont modifiés par ces packs.

## Dossiers et désinstallation

| Pack | Programme par défaut | Données personnelles |
| --- | --- | --- |
| 16 Go | `%LOCALAPPDATA%\Programs\OpenCodeLocal` | `%LOCALAPPDATA%\OpenCodeLocal` |
| 32 Go | `%LOCALAPPDATA%\Programs\OpenCodeLocal32Go` | `%LOCALAPPDATA%\OpenCodeLocal32Go` |

L’installation est propre à l’utilisateur, sans droits administrateur. Les deux marqueurs d’installation, entrées de désinstallation et raccourcis sont distincts. La désinstallation supprime uniquement les fichiers connus du pack et conserve les profils et projets. Aucun installateur n’accepte de remplacer un dossier situé à l’intérieur des données personnelles de l’un ou l’autre pack.

Prévoir au moins **5 Gio libres** sur le disque de destination pour l’installation du pack 16 Go et **20 Gio libres** pour celle du pack 32 Go, en plus des fichiers de téléchargement déjà présents.

## Versions et vérifications

Les composants communs restent OpenCode **1.19.5**, llama.cpp **b12089**, ripgrep **15.1.0**, les DLL Microsoft officielles et les caches hors ligne déjà verrouillés. Les URLs, révisions, tailles et SHA-256 des modèles figurent dans `config/packs.json` ; ceux des autres composants figurent dans `config/artifacts.lock.json`.

Les licences sont conservées dans `wad/licenses` et les arborescences des dépendances. Voir [THIRD_PARTY.md](THIRD_PARTY.md).

[VALIDATION.md](VALIDATION.md) distingue les tests réalisés : compilation Windows, intégrité des archives, assemblage avec petits fichiers, et tests du moteur Linux. Le modèle 32 Go a chargé, généré une réponse et produit un appel d’outil parsé par le serveur. **Cela ne vaut pas un test d’installation Windows, de politique TEHTRIS, ni une boucle complète OpenCode sur le poste cible.**

## Construction

Voir [BUILD.md](BUILD.md). La compilation locale produit `dist/16go` et `dist/32go`. Aucun workflow GitHub Actions ni Git LFS n’est nécessaire.
