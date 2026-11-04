# OpenCode Local

Un pack Windows x64 pour utiliser **OpenCode avec Qwen3.5-4B en local**, sur un PC portable disposant de **16 Go de RAM**. Le modèle, le moteur CPU, les dépendances de démarrage et le lanceur sont inclus. Aucune clé d’API ni installation préalable de Python, Node.js, npm ou Ollama n’est nécessaire sur le PC cible.

## Installer

Dans les **Assets** de la [dernière version](https://github.com/M4D135/OpenCode-Local/releases/latest), télécharger les trois fichiers suivants et les placer **dans le même dossier** :

1. `Setup.exe`
2. `Setup-1.bin`
3. `Setup-2.bin`

Lancer `Setup.exe`, puis ouvrir **OpenCode Local** dans le menu Démarrer. L’installation est propre à l’utilisateur et ne demande pas de droits administrateur. L’installateur vérifie le modèle pendant son assemblage ; ne pas renommer les fichiers `.bin`. Les deux fichiers de données sont nécessaires parce que le modèle dépasse la taille autorisée pour un seul fichier de release GitHub.

Le pack peut être téléchargé sur une autre machine, puis transféré par un moyen autorisé vers le PC sans Internet. Les fichiers « Source code » ajoutés automatiquement par GitHub ne contiennent pas les binaires ni le modèle.

## Utiliser

- **OpenCode** : choisir le dossier du projet et lancer la session. Le moteur démarre sur `127.0.0.1`, puis OpenCode s’ouvre dans une console. Les lectures ordinaires sont autorisées ; les modifications et commandes demandent confirmation.
- **Discussion directe** : conversation textuelle avec le même modèle, exécutée directement par `llama-completion.exe`, sans serveur HTTP. Ce mode reste utilisable si la liaison locale est interdite, mais n’offre pas les outils d’OpenCode.
- **Arrêter** : ferme la session et les processus qu’elle a lancés. Fermer le lanceur arrête également son moteur.
- **Diagnostic**, **Journaux** et **Guide** : informations locales pour identifier un problème de démarrage.

L’interface native et le guide sont en français. OpenCode conserve son interface officielle ; on peut lui parler en français.

Dans cette version de `llama-completion`, la discussion directe peut encore afficher du texte entre balises `<think>` avant la réponse, malgré le réglage désactivant ce mode. Le test direct a bien terminé sa réponse ; cette limite amont ne concerne pas le chemin OpenCode avec `llama-server`.

## Ports bloqués et fonctionnement hors ligne

Le mode OpenCode utilise une liaison **locale** sur `127.0.0.1`, avec une clé aléatoire différente à chaque session, sur un port disponible entre `18741` et `18780`. Il n’écoute pas sur le réseau local. Il n’ouvre pas de règle de pare-feu et ne modifie aucune politique Windows.

Bloquer les accès Internet n’empêche normalement pas cette liaison locale. Si les connexions à `127.0.0.1` elles-mêmes sont interdites, utiliser **Discussion directe**. OpenCode a besoin de son API locale ; l’installateur ne peut pas supprimer cette contrainte. Si l’exécution d’applications est interdite par la politique du PC, faire autoriser le pack par l’administrateur.

Les fournisseurs cloud, mises à jour automatiques, téléchargements de serveurs de langage, recherche web, partage de sessions et plugins externes sont désactivés dans le profil du pack. Les bibliothèques de premier démarrage et les grammaires de coloration sont fournies. Les commandes qu’un utilisateur autorise restent des commandes ordinaires : le pack ne constitue pas un bac à sable réseau.

## Configuration prévue

| Élément | Choix |
|---|---|
| Système cible | Windows 10/11, processeur Intel/AMD x64 |
| Mémoire | 16 Go de RAM totale ; fermer les applications lourdes |
| Accélération | CPU ; aucune carte graphique dédiée nécessaire |
| Modèle | Qwen3.5-4B, quantification Q4_K_M, texte uniquement |
| Contexte OpenCode | 32 768 tokens par défaut ; 8 192 et 16 384 proposés |
| Réponse maximale OpenCode | 4 096 tokens |
| Discussion directe | Contexte de 4 096 tokens |
| Espace disque conseillé | Environ 12 Go libres pour le téléchargement, l’installation et le staging |

Le contexte inclut les instructions et les outils d’OpenCode. Le réglage 32K évite de saturer immédiatement ce contexte ; réduire à 16K peut aider sur une machine chargée. La vitesse dépend fortement du CPU et de la mémoire. Un modèle 4B peut se tromper sur un chemin, une commande ou une modification : relire les propositions et travailler dans un projet sauvegardé ou versionné. Git et les compilateurs propres au projet ne sont pas inclus. Les instantanés Git d’OpenCode sont désactivés ; le retour arrière fondé sur ces instantanés ne restaure donc pas les modifications de fichiers.

## Contenu et versions figées

| Composant | Version / provenance |
|---|---|
| OpenCode, exécutable autonome Windows baseline | [v1.19.5 — anomalyco/opencode](https://github.com/anomalyco/opencode/releases/tag/v1.19.5) |
| llama.cpp, moteur CPU Windows x64 | [b12089 — ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/releases/tag/b12089) |
| Qwen3.5-4B-Q4_K_M.gguf | [Quantification Unsloth](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/e87f176479d0855a907a41277aca2f8ee7a09523) du [modèle Qwen](https://huggingface.co/Qwen/Qwen3.5-4B) |
| ripgrep | [15.1.0 — BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0) |
| Bibliothèques Microsoft VC++ | Redistribuables officiels, placés à côté du moteur |
| Lanceur et assembleur | Sources C++ dans `src/` |
| Installateur | NSIS, construit localement |

SHA-256 du modèle complet :

```text
00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4
```

La release fournit `SHA256SUMS.txt`. Les fichiers de verrouillage du dépôt indiquent les URL, tailles et empreintes des composants. Les licences originales des composants principaux sont conservées dans `wad/licenses` après installation ; celles des dépendances npm et des grammaires restent également présentes dans `wad/offline-cache`. La licence MIT de ce dépôt couvre le lanceur, les scripts et l’intégration, sans remplacer les licences des composants embarqués.

## Données et désinstallation

Programme : `%LOCALAPPDATA%\Programs\OpenCodeLocal` par défaut.

Profil séparé : `%LOCALAPPDATA%\OpenCodeLocal`. Il contient les conversations, journaux, caches et la configuration du pack. Les sessions sont stockées localement. L’installation ne remplace pas la configuration d’une autre installation d’OpenCode.

La désinstallation s’effectue depuis les applications installées de Windows. Elle conserve le profil et les projets. Pour effacer aussi l’historique, fermer le lanceur puis supprimer volontairement `%LOCALAPPDATA%\OpenCodeLocal`.

## Vérifications et limites

Consulter [VALIDATION.md](VALIDATION.md) pour les résultats réels. Le moteur et les appels d’outils ont été exercés avec le modèle fourni. Les exécutables Windows sont compilés, mais ce dépôt ne prétend pas remplacer un essai d’installation sur un vrai Windows. Les exécutables d’intégration ne sont pas signés avec un certificat commercial.

## Reconstruire le pack

Les sources et instructions sont dans [BUILD.md](BUILD.md). La construction télécharge les composants officiels sur une machine connectée, vérifie leurs empreintes, prépare le cache hors ligne, compile les exécutables et produit les trois fichiers d’installation.

**Aucun workflow GitHub Actions n’est fourni ni nécessaire.** Les gros fichiers sont distribués comme assets de release, sans Git LFS.
