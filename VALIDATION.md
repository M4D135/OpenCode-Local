# Validation des packs 1.1.0

Contrôles réalisés le 4 novembre 2026. Les résultats distinguent compilation, tests natifs Linux et exécution Windows.

| Contrôle | Résultat | Limite |
| --- | --- | --- |
| Profils 16/32 Go, modèle et identités distinctes | Générés depuis `config/packs.json` | RAM cible à confirmer sur les postes |
| Lanceurs et assembleurs Windows x64 | Compilation avec `-Wall -Wextra -Werror` réussie | Exécution Windows non réalisée |
| VERSIONINFO et manifeste `asInvoker` | Présents dans les deux lanceurs | Pas de signature Authenticode |
| Installateurs NSIS et archives ZIP | Compilation et contrôle d’intégrité réussis | L’empreinte des assets est aussi contrôlée après publication |
| Assembleur générique | 40 vérifications natives sur petits fichiers, 2 et 11 parties | Voir `validation/assembler-new-profiles.json` |
| Fonctions de chemins/données NSIS | Compilation sur cible x86-unicode pour les deux profils | Runtime bloqué dans cet environnement ; aucun succès d’exécution revendiqué |
| Modèle Qwen3.8-27B complet | Taille et SHA-256 vérifiés après téléchargement | Empreinte figée dans `config/packs.json` |
| Tokenizer, chargement, template et génération 32 Go | Réussis avec llama.cpp b12089 Linux | Contexte réduit à 1 024 tokens et hôte limité à 8 Gio |
| Appel d’outil 32 Go | `finish_reason=tool_calls`, fonction `done`, arguments `{}` | Pas de boucle complète d’exécution OpenCode ; le choix d’outil forcé par objet est non pris en charge par ce runtime |
| Compatibilité TEHTRIS | Non validée | Une détection sur l’ancien lanceur reste à qualifier |

Rapports : `validation/pack-16go.json`, `validation/pack-32go.json`, `validation/model-32go-parts.json`, `validation/model-32go-compatibility.json`, `validation/assembler-new-profiles.json` et `validation/installer-paths.json`.

## Configuration testée pour le grand modèle

Les essais natifs ont utilisé `--no-repack`, `--no-warmup`, quatre threads et un plafond de 3 Gio pour les allocations anonymes, avec des poids lus par mémoire mappée. Les commandes de production du pack 32 Go incluent `--no-repack`. Les temps mesurés avec 8 Gio disponibles ne prédisent pas le débit sur le PC de 32 Go. La consommation au contexte de production de 16 384 tokens reste à mesurer sur ce PC.

Le choix forcé par nom sous forme d’objet `tool_choice` a été ignoré avec avertissement par le runtime ; le modèle a néanmoins choisi la fonction et le serveur l’a parsée. Le rapport ne prétend pas que ce format d’API est pris en charge.

## Détection antivirus

Les nouvelles ressources et le chargement de `dwmapi.dll` depuis System32 améliorent l’identification et le chargement des DLL. Ils ne constituent ni une signature Authenticode ni une preuve de faux positif. Les éléments à relever dans TEHTRIS sont décrits dans `docs/TEHTRIS.md`.

---

## Historique de la version 1.0.0

# Résultats initiaux du pack 1.0.0

Vérifications effectuées le 4 novembre 2026. Les rapports structurés conservés dans `validation/` correspondent aux essais ci-dessous.

## Ce qui a été testé

| Vérification | Résultat | Portée |
|---|---|---|
| Archives Windows, OpenCode, ripgrep, modèle et runtime | PASS | Tailles et SHA-256 des cinq téléchargements verrouillés |
| Cache hors ligne OpenCode | PASS | 23 paquets JS portables ; 104 fichiers de grammaires, requêtes et licences validés |
| Préparation du cache sans téléchargement | PASS | `seed_offline.py --offline` et `--verify-only` |
| Modèle GGUF, chargement CPU et réponse simple | PASS | Homologue Linux officiel de llama.cpp b12089, contexte 32K |
| Appel structuré d’une fonction puis lecture de son résultat | PASS | Le modèle a produit l’appel attendu, puis repris la preuve retournée par l’outil |
| Configuration et fournisseur OpenCode | PASS | OpenCode 1.19.5 Linux : `debug config` et `models local`, seul modèle `local/qwen3.5-4b` |
| OpenCode complet avec l’outil `read` | PASS | Lecture réelle d’un fichier du projet avec son chemin exact, preuve retrouvée dans la réponse, permissions conservées |
| Discussion directe | PASS | `llama-completion` avec les options du lanceur, un tour automatique, réponse finale « Bonjour », code 0 |
| Assembleur : modèle entier | PASS | Même source C++ exécutée sous Linux ; les deux fichiers distribués reconstruisent exactement le GGUF attendu |
| Assembleur : erreurs et préservation | PASS | SHA standards, tailles incorrectes, parties absentes, liens, destination source, contenu corrompu de tailles correctes, préservation d’un ancien fichier |
| Runtime Microsoft | PASS | Extraction contrôlée de 12 DLL PE x64, identiques à celles du payload |
| Compilation C++ Windows | PASS | MinGW x64, sans avertissement ; imports du lanceur et de l’assembleur limités aux DLL système Windows |
| Compilation de l’installateur NSIS | PASS | Fixture puis pack complet ; véritable `Setup.exe`, compression zlib, code de retour 0 |
| Archive finale et extraction | PASS | `7z t` code 0 ; 3 809 fichiers de payload ; SHA-256 des trois exécutables principaux extraits identique à leurs sources |

## Limites observées

Le premier essai OpenCode avec un nom de fichier relatif a échoué : le petit modèle a inventé un chemin absolu hors projet. La demande d’autorisation correspondante a été refusée automatiquement par le test non interactif. L’essai suivant a utilisé le chemin absolu correct sans élargir les permissions et a réussi. Cela confirme une limite pratique du modèle 4B : les appels d’outils ne sont pas infaillibles.

Le test OpenCode complet a pris 126,78 secondes sur le CPU de l’environnement de construction, dont un préremplissage important du prompt d’outils. Le test de discussion directe a pris 34,82 secondes et généré 196 tokens. Ce sont des mesures de cet environnement, pas une prévision de vitesse pour un portable.

Dans `llama-completion` b12089, `--reasoning off` est accepté mais ne désactive pas le texte `<think>` dans ce chemin de formatage du chat. Le test direct a néanmoins terminé avec la bonne réponse. `--chat-template-kwargs` n’est pas accepté par ce binaire. Cette limite ne concerne pas le chemin serveur utilisé par OpenCode.

## Ce qui reste à valider sur Windows

**Le setup et le lanceur n’ont pas été exécutés sur un vrai Windows.** Wine est indisponible dans l’environnement de construction : la création des sockets Unix requises est refusée avant le démarrage du code Windows. La compilation ne remplace pas une qualification de l’interface, de l’installation, de la réparation et de la désinstallation.

Une validation cible doit couvrir Windows 10/11 x64, le chargement CPU, une session OpenCode, le mode direct, un compte comportant des accents, l’interruption d’une installation, une réinstallation et la conservation du profil à la désinstallation.

L’environnement interdit aussi la capture `ptrace`. Aucune capture exhaustive prouvant l’absence de tout paquet externe n’est revendiquée. Les paramètres officiels désactivent les fonctions réseau automatiques, les dépendances sont présentes et les tests utilisent le fournisseur local. Les outils de commande autorisés par l’utilisateur ne constituent pas un environnement isolé du réseau.

Les exécutables d’intégration ne portent pas de signature Authenticode commerciale. Le SHA-256 des fichiers de distribution permet de contrôler leur intégrité, sans constituer une signature d’éditeur.

La compilation complète émet l’avertissement NSIS 9000 sur le nom `Setup.exe` et les mécanismes de compatibilité Windows associés à ce nom. Le nom demandé est conservé. Ce message n’empêche pas la compilation ; il n’est pas présenté comme un test Windows réussi. Le journal de construction et les tailles/empreintes finales sont conservés avec les rapports.

## Sources techniques

- [OpenCode 1.19.5](https://github.com/anomalyco/opencode/tree/v1.19.5)
- [llama.cpp b12089](https://github.com/ggml-org/llama.cpp/tree/b12089)
- [Chemin du mode direct](https://github.com/ggml-org/llama.cpp/blob/b12089/tools/completion/completion.cpp)
- [Traitement des templates de chat](https://github.com/ggml-org/llama.cpp/blob/b12089/common/chat.cpp)

Le pack possède des diagnostics locaux supplémentaires : `OpenCodeLocal.exe /diagnostic` écrit un rapport dans le profil utilisateur ; `/smoke-test` est destiné à la qualification Windows du moteur et d’une réponse locale.

