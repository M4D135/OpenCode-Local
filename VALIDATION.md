# Validation du pack 1.0.0

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
