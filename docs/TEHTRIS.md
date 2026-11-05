# Détection du lanceur par TEHTRIS

## Correctif 1.2.1 — en cours de validation

Une revue approfondie du code a identifié des défauts de fiabilité et
d’intégrité du lanceur. Le correctif 1.2.1 les traite dans
[`src/launcher.cpp`](../src/launcher.cpp) ; sa validation Windows est en cours.
**Ces défauts n’ont pas été établis comme causes de l’alerte TEHTRIS.** Le
signalement et l’empreinte 1.2.0 ci-dessous restent des éléments historiques,
distincts des nouveaux exécutables et de leurs futurs rapports de validation.

| Fonction concernée | Correction apportée dans les sources 1.2.1 |
| --- | --- |
| `StartProcess`, `ProcessAttributes` | Les tableaux référencés par les attributs de création restent vivants jusqu’à la fin de leur utilisation. La liste d’attributs est libérée par son propriétaire, y compris en cas d’erreur. |
| `StartProcess`, `CreateOwnedJob` | Le Job Object est affecté par Windows pendant `CreateProcessW`, via `PROC_THREAD_ATTRIBUTE_JOB_LIST`. Il n’existe plus d’étape de rattachement séparée après création. La création suspendue puis la reprise sont conservées. |
| `PrepareProfile`, `SeedFileContentsMatch`, `CopySeedFile` | Chaque fichier présent dans le seed installé est comparé à sa copie par blocs. Une dépendance absente ou corrompue, même de taille identique, est réparée atomiquement. Le témoin de préparation est régénéré uniquement après succès ; les données non déclarées par le seed restent intactes. |
| `EnsureProfileDirectory`, `SeedPathAttributes`, `WriteAtomic` | La préparation refuse les liens de la racine du seed, de ses entrées et des cibles du profil privé. Les dossiers du profil sont aussi contrôlés au démarrage et pour les écritures atomiques ou le journal. Les temporaires d’écriture sont uniques et créés avec refus d’écraser un fichier préexistant. |
| `StartEngine`, `RunSession`, `ChildEnvironment` | Les moteurs serveur et discussion directe démarrent dans leur dossier moteur. Le projet reste le dossier de travail d’OpenCode. Les overrides ambiants `GGML_BACKEND_PATH`, `BUN_OPTIONS`, `BUN_BE_BUN` et `BUN_INSPECT_PRELOAD` sont retirés de l’environnement enfant. |
| `LocalHttp` | Les erreurs de lecture, réponses tronquées ou trop volumineuses et longueurs incohérentes sont rejetées, au lieu de conserver un statut HTTP réussi avec un corps incomplet. |
| `OpenLogs` | Une erreur d’accès ou d’ouverture du dossier Journaux est traitée dans l’action de l’interface ; elle ne laisse plus remonter une exception C++ dans le callback de fenêtre. |

La recherche de backends de llama.cpp b12089 parcourt notamment le dossier
courant et charge la bibliothèque indiquée par `GGML_BACKEND_PATH`. Fixer le
dossier de travail du moteur et retirer cet override évite qu’un projet ou
une autre installation fournisse implicitement un backend. Les overrides
Bun concernent le démarrage du runtime embarqué ; leur retrait ne change pas
l’environnement Windows de l’utilisateur. Les références figées figurent en
fin de document.

La comparaison du cache vérifie sa correspondance avec **le seed déjà installé**.
Elle ne prouve ni l’authenticité ni la complétude de ce seed. Les contrôles de
chemin portent sur la racine privée de l’application et ses descendants ; les
ancêtres Windows situés au-dessus peuvent rester légitimement redirigés. Ces
contrôles ne constituent pas une isolation contre un autre processus du même
utilisateur capable de modifier simultanément les fichiers.

**La signature de la version 1.2.1 n’est pas active et son acceptation par
TEHTRIS reste non vérifiée.** Aucun accès à la console, contact avec un tiers
ou changement de protection n’est demandé pour effectuer ces corrections de
code. Aucune exclusion antivirus, modification de service, de pare-feu ou de
magasin de certificats n’est ajoutée. Les contrôles exécutés et leurs limites
doivent être lus dans [VALIDATION.md](../VALIDATION.md) et les rapports de la
version concernée ; une réussite fonctionnelle n’est pas un verdict EDR.

## Signalement du 5 novembre 2026 — pack présenté comme 1.2.0

L’utilisateur signale que Qwen démarre, puis qu’après la saisie de « salut »,
la session attend et TEHTRIS supprime le lanceur. La notification fournie
indique **Malware detected**, **Process terminated** et **File quarantined**
pour `OpenCodeLocal.exe`, dans le dossier d’installation `OpenCodeLocalMoE`.
Le suffixe `.egambit` apparaît dans le chemin de quarantaine ; il n’est pas
ajouté par OpenCode Local. Aucune photo ni aucun chemin nominatif n’est publié
dans ce dossier.

**Le blocage est observé sur le poste signalé.** Il ne s’agit donc plus d’une
simple absence de test sur ce poste. Le pack est présenté comme la version
1.2.0, mais le SHA-256 du fichier effectivement bloqué n’a pas été fourni par
la console EDR. La notification ne donne ni le nom de la règle, ni le moteur
de détection, ni la chronologie détaillée. Elle ne permet pas de confirmer
l’identité du binaire, de conclure à un faux positif ou d’attribuer la cause
au message saisi, au modèle, à la RAM ou à l’absence de signature.

### Identité attendue du lanceur 1.2.0

Le [rapport Windows 1.2.0](../validation/windows-unsigned-1.2.0.json), entrée
`moe/native/OpenCodeLocal.exe`, décrit le lanceur construit depuis le commit
`4a6f4ffa4ab6388bacc2f85adb7b74e31562d9e1`. Le même rapport vérifie que le
lanceur extrait du setup est identique à cette entrée native.

| Élément | Valeur attendue pour 1.2.0 |
| --- | --- |
| Nom | `OpenCodeLocal.exe` |
| Taille | 3 498 714 octets |
| SHA-256 | `ed0f93d403253f6c8779312e1f97851d303f552276e4330da4ea902b7b6f7ac0` |
| Architecture | Windows x64, PE32+ |
| Version | `1.2.0` |
| Signature Authenticode | `NotSigned` |
| Ressources de version et manifeste applicatif | Présents ; niveau `asInvoker` |

Cette empreinte est celle du **lanceur attendu**, pas celle de `Setup.exe` ou
de `Setup.zip`, et pas une empreinte relevée dans l’alerte utilisateur. Elle
doit être comparée au SHA-256 de l’événement central TEHTRIS. Les contrôles
Windows et les essais fonctionnels du modèle ne constituent pas un verdict
antivirus ; leur portée est décrite dans [VALIDATION.md](../VALIDATION.md).

## Historique du 4 novembre 2026 — versions 1.0.0 et 1.0.1

Le premier signalement indiquait déjà **Malware detected**, **Process
terminated** et **File quarantined** pour `OpenCodeLocal.exe`, sans nom de
règle ni motif technique détaillé. L’exécutable distribué avec les versions
1.0.0 et 1.0.1 avait les caractéristiques suivantes, contrôlées sur le fichier
de ces releases historiques :

| Élément | Valeur |
| --- | --- |
| Nom | `OpenCodeLocal.exe` |
| Taille | 3 476 503 octets |
| SHA-256 | `57461cee2ca4479f8c01712ec804ee2c8a73b90091fd3863ec516bbc3aadc1c5` |
| Architecture | Windows x64 |
| Signature Authenticode embarquée | Absente |
| Ressources de version et manifeste applicatif | Absents dans ce binaire initial |

Cette ancienne empreinte correspondait au manifeste de la distribution
1.0.0/1.0.1 ; sa correspondance avec le fichier bloqué le 4 novembre n’a pas
été confirmée par un hash EDR. **Elle ne s’applique pas au pack unique 1.2.0.**
Les rapports `validation/pack-16go.json` et `validation/pack-32go.json`
concernent également des packs historiques. Pour 1.2.0, utiliser uniquement
l’identité attendue et le rapport Windows cités ci-dessus.

## Fonctionnement historique du lanceur 1.2.0

Le code du lanceur 1.2.0 démarre `llama-server.exe`, puis `opencode.exe`, sans
élévation de privilèges. Le premier fonctionne sans fenêtre de console, avec
un journal de sortie ; le second ouvre une console visible. Le moteur HTTP
écoute sur `127.0.0.1` et utilise une clé aléatoire propre à la session. Le
lanceur vérifie que le port est détenu par le moteur qu’il vient de créer.
Ses requêtes HTTP n’acceptent pas de redirection vers un autre serveur.

Le parent `OpenCodeLocal.exe` reste ensuite actif dans une boucle d’attente
des processus ; **il ne lit ni ne retransmet le texte « salut »**. Le code
correspondant est la fonction `RunSession`, dans le
[code 1.2.0 figé](https://github.com/M4D135/OpenCode-Local/blob/4a6f4ffa4ab6388bacc2f85adb7b74e31562d9e1/src/launcher.cpp#L930-L948). La proximité temporelle
entre la saisie et l’alerte ne suffit pas à identifier l’événement déclencheur.
L’arbre réel des éventuels descendants d’OpenCode n’est pas disponible dans
la notification fournie.

Dans cette version, les enfants étaient créés suspendus, puis rattachés à un
**Job Object** appartenant à la session par un appel séparé, avant leur reprise.
`CreateOwnedJob` configurait
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
([source 1.2.0 figée](https://github.com/M4D135/OpenCode-Local/blob/4a6f4ffa4ab6388bacc2f85adb7b74e31562d9e1/src/launcher.cpp#L661-L737)). **Si TEHTRIS termine le lanceur,
la fermeture de son handle de job entraîne aussi l’arrêt de ses enfants.**
Ce mécanisme explique pourquoi toute la session peut disparaître quand le
parent est bloqué ; il n’identifie pas la cause de la détection. Le code ne
modifie pas la protection TEHTRIS, les exclusions antivirus, les règles du
pare-feu ou les services Windows.

Au premier lancement d’une session OpenCode, les dépendances déjà présentes
dans le pack sont copiées vers son dossier de données utilisateur. Aucun
téléchargement n’est requis pour cette préparation. Les commandes de travail
demandées à OpenCode restent soumises aux autorisations de sa configuration.

La première revue ciblée du lanceur et de sa compilation n’avait pas mis en évidence de
fonction d’injection dans un processus tiers, de désactivation de l’EDR ou de
packer. Elle ne constitue pas un audit complet des composants tiers.
La revue approfondie a ensuite identifié les défauts corrigés dans 1.2.1,
décrits plus haut. **Aucun lien causal entre ces défauts et la détection n’a
été établi.** Le fonctionnement des processus et l’état de signature restent
des éléments descriptifs, pas une preuve de faux positif.

## Métadonnées et signature

Le lanceur 1.2.0 dispose d’informations de version et d’un manifeste Windows
explicite `asInvoker`, absents du binaire initial 1.0.0/1.0.1. Le chargement
de `dwmapi.dll`, nécessaire à l’apparence de la fenêtre, est limité au
répertoire système Windows. Ces dispositions améliorent l’identification du
logiciel et le chargement de sa DLL système ; elles n’ont pas établi son
acceptation par la politique TEHTRIS du poste signalé.

Les ressources de version ne constituent pas une signature numérique. Une
signature Authenticode ne peut être ajoutée qu’avec un certificat de signature
de code approprié et une chaîne de confiance reconnue sur les postes cibles.

## Informations manquantes pour qualifier l’alerte

La notification locale ne fournit pas les éléments suivants :

1. L’identifiant de l’alerte, le nom exact de la détection, son moteur, sa
   règle ou catégorie et la distinction éventuelle entre verdict sur fichier
   et événement comportemental.
2. Le SHA-256, la taille et la version du fichier concerné, nécessaires à sa
   comparaison avec l’identité du lanceur 1.2.0 ci-dessus.
3. La chronologie horodatée avec fuseau, le PID, le chemin, le processus
   parent et les lignes de commande associés à l’événement.
4. L’arbre parent/enfants et les opérations retenues comme déclencheurs, dont
   les éventuels descendants d’OpenCode autour de la première saisie.
5. Les actions appliquées (arrêt, quarantaine), la version de l’agent TEHTRIS
   et la politique concernée, si ces informations sont disponibles.

Ces éléments permettraient de distinguer le fichier bloqué, son comportement
et la décision de protection. L’utilisateur n’a pas accès à cette console ;
aucune collecte de ces informations ne lui est demandée pour poursuivre les
corrections du logiciel. La notification locale « Malware detected » seule
ne fournit pas cette qualification.

## Journaux locaux utiles

Les fichiers déjà présents peuvent compléter l’alerte centrale, sans restaurer
ni relancer l’exécutable mis en quarantaine :

- `%LOCALAPPDATA%\OpenCodeLocalMoE\logs\launcher.log` : démarrage et suivi de session.
- `%LOCALAPPDATA%\OpenCodeLocalMoE\logs\moteur-*.log` : sortie du moteur local pour chaque session.
- `%LOCALAPPDATA%\OpenCodeLocalMoE\logs\diagnostic.txt` : diagnostic, s’il a déjà été généré.
- `%LOCALAPPDATA%\OpenCodeLocalMoE\data\opencode\log` : journaux OpenCode existants.

L’arrêt forcé peut laisser un journal incomplet ; l’absence d’une dernière
ligne n’est pas un verdict. Avant tout partage, retirer les noms de comptes,
les chemins personnels, le contenu des projets et les secrets éventuels.
Tout éventuel partage ultérieur doit utiliser un canal autorisé, sans publier
de capture du poste ou de données de travail dans une issue publique.

## Référence support et état du dossier

La [page de contact officielle TEHTRIS](https://tehtris.com/contact) indique
le [portail support](https://my.tehtris.com/) et l’adresse
`support-services@tehtris.com`. Ces coordonnées sont conservées comme
référence documentaire ; aucun contact n’est demandé dans le cadre du
correctif 1.2.1.

**Aucune soumission au support TEHTRIS ni demande d’autorisation n’a été
effectuée dans le cadre de cette mise à jour.** La cause et la qualification
de l’alerte restent ouvertes. Le correctif de code 1.2.1 est en cours de
validation ; il n’est pas présenté comme une résolution vérifiée de la
détection. Les protections du poste restent inchangées.

## Références

- Microsoft : [sécurité du chargement des DLL](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-security).
- Microsoft : [LoadLibraryExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw).
- Microsoft : [Job Objects et arrêt des processus du groupe](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- llama.cpp b12089 : [recherche et chargement des backends](https://github.com/ggml-org/llama.cpp/blob/b12089/ggml/src/ggml-backend-reg.cpp).
- OpenCode 1.19.5 : [version Bun embarquée](https://github.com/anomalyco/opencode/blob/v1.19.5/package.json) et [options de compilation](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/opencode/script/build.ts).
- Bun 1.4.4 : [démarrage du programme embarqué et overrides](https://github.com/oven-sh/bun/blob/bun-v1.4.4/src/runtime/cli/mod.rs) et [préchargements du runtime](https://github.com/oven-sh/bun/blob/bun-v1.4.4/src/runtime/cli/Arguments.rs).
- TEHTRIS : [fonctionnement de l’EDR](https://tehtris.com/edr) et [support éditeur](https://tehtris.com/contact).
