# Validation du pack unique MoE 1.2.0

## Contrôles Windows réussis — 5 novembre 2026

Le [run GitHub Actions 41993863228](https://github.com/M4D135/OpenCode-Local/actions/runs/41993863228)
a terminé avec succès ses jobs de construction et de validation Windows sur le
commit `c2e190e6c2fc9ef55a0a5290b67e3ce082d0ba2c`, pour le seul profil `moe`.
Les contrôles ont été exécutés sous Windows NT 10.0.20348 avec PowerShell 7.6.9.
Le [rapport Windows complet](validation/windows-unsigned-1.2.0.json) conserve
les résultats détaillés, dont les cas de refus attendus.

| Contrôle exécuté sous Windows | Résultat | Portée |
| --- | --- | --- |
| Contrôles principaux | 8/8 réussis | Trois exécutables natifs, diagnostic du lanceur, assembleur, fonctions NSIS et setup |
| Assembleur sur petits fichiers | 38/38 réussis | Exécution réelle des fixtures de deux et onze parties, intégrité et préservation des données |
| Assembleur de production MoE | 5/5 réussis | Exécutable Windows réel ; grandes tailles et rejets attendus |
| Dossiers d'installation NSIS | 16/16 réussis | Dossiers neufs, Documents, espaces et Unicode ; protection des données et des anciennes installations |
| Métadonnées du modèle NSIS | 8/8 réussis | GGUF complet, huit parties, tailles incorrectes, parties absentes et priorité du GGUF |
| Archive du setup | Réussie | 3 821 entrées ; intégrité vérifiée et correspondance SHA-256 exacte des trois exécutables maison embarqués |
| ZIP final | Réussi | Un seul `Setup.exe`, identique au setup validé ; empreintes et provenance enregistrées |

Le setup validé contient **127 278 384 octets** et porte le SHA-256
`8baa7b5eb32b03afd825b1cb69bac5764aa799b24c0d2b459aa7f39bf6e57291`.
Le [manifeste de finalisation](validation/finalize-unsigned-1.2.0.json) conserve
aussi l'empreinte du ZIP. La [synthèse de provenance](validation/windows-unsigned-summary-1.2.0.json)
relie les rapports à ce commit, aux jobs et à l'artefact GitHub téléchargé ; son
empreinte ZIP a été vérifiée contre le digest annoncé par GitHub. Seul le petit
artefact de rapports a été récupéré pour cette synthèse.

Ces contrôles Windows n'ont téléchargé ni chargé le modèle. Les vérifications
de grandes tailles utilisent des fichiers creux ; le diagnostic du lanceur
vérifie le refus attendu d'un paquet volontairement incomplet. Ils ne qualifient
donc ni une session de modèle ni sa consommation de RAM. L'installation complète
du pack avec les vrais poids sur un poste Windows 10/11 reste à vérifier.

## Modèle et mémoire — validation en cours

Lors de ce relevé, le [run de validation et publication 41994232790](https://github.com/M4D135/OpenCode-Local/actions/runs/41994232790)
est encore en cours pour le même commit. L'exécution réelle du GGUF
Qwen3.6-35B-A3B UD-IQ2_XXS, les appels d'outils, la session OpenCode et les mesures
de mémoire ne disposent pas encore d'un résultat final conservé ici. Aucun
résultat du modèle 27B historique ci-dessous ne qualifie ce modèle IQ2.

Le budget estimé reste de **13 075 Mio à 8K**, avec **6 144 Mio de réserve**.
Les mesures doivent confirmer ce budget avant publication ; elles préciseront
le RSS, les paramètres et leurs limites. Les exécutables de ce run sont
explicitement **non signés** (`NotSigned`) ; aucun certificat réel n'a été
utilisé et la compatibilité TEHTRIS reste non validée.

### Premier essai réel et correction du délai

Le [premier essai Linux](validation/model-runtime-first-attempt-1.2.0.json),
avec la configuration précédente à un checkpoint et le prompt OpenCode standard,
a réussi la génération, l'appel d'outil et la restitution d'une preuve lue dans
un fichier. Il **n'a pas validé le harnais complet** : le préremplissage continu
du prompt OpenCode d'environ 7 150 tokens a dépassé le délai de 420 secondes.
Une disparition normale de PID lors du nettoyage était également comptée à tort
comme une erreur du moniteur. Le pic RSS partiel était de 11 687 682 048 octets.

La nouvelle configuration raccourcit le prompt général sans retirer les outils,
conserve quatre checkpoints et réserve 2,25 Gio de surcoût. Le validateur distingue
les PID disparus des vraies erreurs et autorise 800 secondes par session, dans une
limite totale de 30 minutes. Tous les contrôles de lecture, de refus d'écriture et
de mémoire restent exigés. Le retest complet est encore nécessaire.

---

## Historique des validations précédentes

# Validation des packs et de la chaîne de signature

## Préparation 1.1.1 — état au 4 novembre 2026

La chaîne de construction et de vérification des deux profils est préparée pour
une signature SignPath en deux étapes. **Aucun certificat réel n'a été utilisé,
aucune demande de signature n'a été envoyée et aucune release signée n'est
publiée.** Les essais ci-dessous portent sur des candidats explicitement non
signés. L'installation complète avec les modèles et l'acceptation par TEHTRIS
restent des validations distinctes sur les postes cibles.

### Contrôles locaux terminés

| Contrôle | Résultat | Portée |
| --- | --- | --- |
| Intégrité de la chaîne de signature | 16 tests réussis | Fichiers altérés, absence de signature, certificat attendu, provenance, chemins, liens et absence de téléchargement de modèles |
| Parcours natifs → setups → ZIP | Réussi pour les deux profils | Construction unsigned avec réimport du désinstalleur exporté par NSIS |
| Intégrité NSIS et extraction complète | Réussies | 3 813 fichiers par pack vérifiés par taille et SHA-256 ; les exécutables maison extraits sont identiques aux entrées natives |
| ZIP finaux | Réussis | Chaque ZIP contient uniquement `Setup.exe`, identique au setup final |
| Assembleur et profils | 40 cas natifs réussis ; deux compilations Windows réussies | En-têtes de production issus du dossier de construction de la chaîne de signature |
| Workflows et restrictions SignPath | Syntaxe et refus attendus vérifiés | Actions épinglées, chemins exacts, version, politique explicite, absence de configuration, reruns, dépôt/ref et budget |

Le rapport [signing-pipeline-local-1.1.1.json](validation/signing-pipeline-local-1.1.1.json)
conserve les empreintes et la portée du parcours local. Il indique explicitement
que le checkout local comportait le correctif de copie lors de cet essai ; il
ne se présente pas comme une construction SignPath ou une qualification Windows.

### Vérification GitHub et Windows

Le [run GitHub Actions 41922735490](https://github.com/M4D135/OpenCode-Local/actions/runs/41922735490)
a terminé **avec succès ses deux jobs**, construction Linux et validation
Windows, sur le commit `46f462dfc6de7f9450d5cbf517a7cb6967d598c3`.
Le job Windows a utilisé le runner standard `windows-2022`, Windows NT
10.0.20348 et PowerShell 7.6.9. La finalisation des deux ZIP a également réussi
sur Windows, après les vérifications des composants.

| Contrôle exécuté sous Windows | Résultat | Portée |
| --- | --- | --- |
| Contrôles principaux | 14/14 réussis | Six exécutables natifs, deux diagnostics, assembleur, fonctions NSIS et deux setups |
| Assembleur sur petits fichiers | 38/38 réussis | Compilation MSVC et exécution réelle, profils de deux et onze parties, SHA-256 et préservation des données |
| Assembleurs de production | 9/9 réussis | Exécutables Windows réels ; tailles des grands fichiers et rejets attendus |
| Dossiers d'installation NSIS | 30/30 réussis | Dossier neuf imbriqué, dossier par défaut et sous-dossier dans Documents acceptés ; destinations incompatibles refusées |
| Métadonnées des modèles NSIS | 15/15 réussis | Tailles exactes, fichiers tronqués, parties absentes et erreurs attendues pour les deux profils |
| Archives des deux setups | Réussies | Intégrité et correspondance SHA-256 exacte des trois exécutables maison embarqués dans chaque setup |
| ZIP finaux | Réussis | Un seul `Setup.exe` par ZIP ; contrôles de provenance et empreintes enregistrés |

Les contrôles de tailles de production utilisent des fichiers creux, avec une
taille logique maximale de 16 464 440 225 octets et une allocation physique
maximale observée de 65 536 octets par fichier. Ils ne représentent pas un
chargement des poids du modèle ni une mesure de RAM. Aucun modèle n'a été
téléchargé pour ce run.

Rapports conservés :

- [Rapport Windows complet](validation/windows-unsigned-1.1.1.json), comprenant les résultats détaillés de l'assembleur et des fonctions NSIS.
- [Synthèse et origine du run](validation/windows-unsigned-1.1.1-summary.json), dont l'identifiant et l'empreinte de l'artefact téléchargé.
- [Manifeste de finalisation](validation/finalize-unsigned-1.1.1.json), avec les empreintes des setups et ZIP de développement.

**Limites :** les exécutables de ce run sont explicitement non signés. Le
parcours avec un vrai certificat reste à activer et vérifier après acceptation
par SignPath. Le setup complet n'a pas été installé avec les vrais modèles sur
un poste Windows 10/11 ; l'interface, les opérations d'installation/réparation/
désinstallation, les sessions de modèle en contexte de production, la RAM
cible et la compatibilité TEHTRIS restent à qualifier sur ces postes.

### Défaut de manifeste corrigé

L'extraction complète a trouvé dans les manifestes des deux setups 1.1.0 une
entrée surnuméraire : `wad/opencode/.opencode.exe.uMQYmH`, un temporaire de copie
non présent dans les archives NSIS. Ce n'était pas un composant runtime
manquant. Le constructeur 1.1.1 ne copie désormais depuis une base réutilisée
que les fichiers déclarés dans son manifeste vérifié. Un test couvre le cas
d'un fichier supplémentaire, y compris caché, sans modifier la base d'origine.

Les fichiers `.bin` des modèles sont inchangés et restent réutilisables.
La politique et les prérequis d'activation sont détaillés dans
[CODE_SIGNING.md](CODE_SIGNING.md).

---

## Historique : validation des packs 1.1.0

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

