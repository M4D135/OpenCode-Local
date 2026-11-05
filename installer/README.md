# Installateur Windows hors ligne

La compilation utilise **NSIS 3.09 ou ultérieur**, disponible nativement sous
Linux. Elle ne nécessite ni Wine ni GitHub Actions.

Le pack unique est composé de `Setup.zip` contenant seulement `Setup.exe`,
et de huit parties `Modele-MoE-01.bin` à `Modele-MoE-08.bin` : sept parties de
1 500 000 000 octets, puis une de 256 586 464 octets. Le fichier GGUF complet
peut remplacer les parties. Garder le setup extrait et les parties ensemble.

L’utilisateur lance uniquement `Setup.exe`. Les segments ne nécessitent aucune
décompression manuelle. Aucun téléchargement n’est effectué sur le poste cible.

## Compilation

Utiliser les trois phases décrites dans [BUILD.md](../BUILD.md). Elles génèrent
l'en-tête C++, les variables NSIS, la liste des fichiers et le désinstalleur
à partir de la configuration unique `config/packs.json`.

Le générateur refuse les liens symboliques, les noms incompatibles avec Windows
et les collisions de casse. Le modèle GGUF est exclu de `Setup.exe`. Les
licences, caches hors ligne et dépendances doivent déjà se trouver dans `wad`.

## Contrat du helper natif

```text
AssembleModel.exe SOURCE_DIR DEST_MODEL_PATH
```

Le helper vérifie la taille exacte des huit segments et le SHA-256 du modèle :

```text
2e8f5f705355c56311432d0a8a5d14a696dbb7e4b197d05c75ba805fc1857bef
```

Le code retour `0` signifie que le modèle final a été vérifié. Toute autre
valeur arrête l’installation. L’assemblage se fait dans un temporaire exclusif,
remplacé atomiquement après validation. Un arrêt brutal peut laisser un fichier
`.partial.<PID>.<tentative>` dans le dossier de préparation ; ce dossier n’est
jamais effacé récursivement à l’aveugle.

## Installation et préservation des données

L’installation concerne l’utilisateur courant, sans élévation, dans
`%LOCALAPPDATA%\Programs\OpenCodeLocalMoE` par défaut. Le répertoire est
personnalisable. Le menu Démarrer reçoit un raccourci ; celui du Bureau est
facultatif. Le désinstalleur est dans `wad\support`.

Un répertoire non vide est accepté uniquement s’il possède le marqueur exact
`wad\support\installation.id`. Les jonctions et liens symboliques sont refusés
dans le chemin et les fichiers de l’installation. Le verrou partagé avec le
lanceur empêche une mise à jour pendant une session active.

Les fichiers sont préparés dans un dossier voisin. Le modèle est assemblé et
vérifié avant remplacement de l’installation précédente. Celle-ci est déplacée
en sauvegarde le temps du remplacement. Si le déplacement final échoue, sa
restauration est tentée ; même en cas d’échec, ses fichiers restent en sauvegarde.

La suppression utilise la liste explicite des fichiers livrés, puis retire les
dossiers uniquement s’ils sont vides. Aucun `RMDir /r` n’est utilisé. Les fichiers
ajoutés par l’utilisateur et les données dans `%LOCALAPPDATA%\OpenCodeLocalMoE`
sont conservés. Aucun pare-feu, PATH système ou paramètre de sécurité Windows
n’est modifié.

Pour une installation silencieuse, utiliser l’option NSIS `/S`. Une destination
personnalisée se passe avec `/D=chemin` en **dernier argument**. Un déploiement
doit être lancé dans la session utilisateur concernée.

Le moteur d’installation est **NSIS** : <https://nsis.sourceforge.io/>.
Ses notices sont conservées avec les licences de distribution.
