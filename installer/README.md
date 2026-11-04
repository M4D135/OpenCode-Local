# Installateur Windows hors ligne

La compilation utilise **NSIS 3.09 ou ultérieur**, disponible nativement sous
Linux. Elle ne nécessite ni Wine ni GitHub Actions.

Le pack est composé de trois fichiers à conserver ensemble :

- `Setup.exe` : lanceur, OpenCode, moteur et dépendances.
- `Setup-1.bin` : les 1 500 000 000 premiers octets du modèle.
- `Setup-2.bin` : les 1 240 937 888 octets suivants.

L’utilisateur lance uniquement `Setup.exe`. Les segments ne nécessitent aucune
décompression manuelle. Aucun téléchargement n’est effectué sur le poste cible.

## Compilation

Après préparation et validation du payload, générer la liste exacte des
fichiers à installer et à retirer :

```sh
python3 installer/generate_payload_include.py \
  --payload /chemin/build/payload \
  --output /chemin/build/payload-files.nsh

makensis \
  -DPayloadDir=/chemin/build/payload \
  -DHelperPath=/chemin/build/AssembleModel.exe \
  -DOutputPath=/chemin/dist/Setup.exe \
  installer/OpenCodeLocal.nsi
```

Le générateur refuse les liens symboliques, les noms incompatibles avec Windows
et les collisions de casse. Le modèle GGUF est exclu de `Setup.exe`. Les
licences, caches hors ligne et dépendances doivent déjà se trouver dans `wad`.

## Contrat du helper natif

```text
AssembleModel.exe SOURCE_DIR DEST_MODEL_PATH
```

Le helper vérifie la taille exacte des deux segments et le SHA-256 du modèle :

```text
00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4
```

Le code retour `0` signifie que le modèle final a été vérifié. Toute autre
valeur arrête l’installation. L’assemblage se fait dans un temporaire exclusif,
remplacé atomiquement après validation. Un arrêt brutal peut laisser un fichier
`.partial.<PID>.<tentative>` dans le dossier de préparation ; ce dossier n’est
jamais effacé récursivement à l’aveugle.

## Installation et préservation des données

L’installation concerne l’utilisateur courant, sans élévation, dans
`%LOCALAPPDATA%\Programs\OpenCodeLocal` par défaut. Le répertoire est
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
ajoutés par l’utilisateur et les données dans `%LOCALAPPDATA%\OpenCodeLocal`
sont conservés. Aucun pare-feu, PATH système ou paramètre de sécurité Windows
n’est modifié.

Pour une installation silencieuse, utiliser l’option NSIS `/S`. Une destination
personnalisée se passe avec `/D=chemin` en **dernier argument**. Un déploiement
doit être lancé dans la session utilisateur concernée.

Le moteur d’installation est **NSIS** : <https://nsis.sourceforge.io/>.
Ses notices sont conservées avec les licences de distribution.
