# Détection du lanceur par TEHTRIS

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

## Fonctionnement examiné

Le code du lanceur 1.2.0 démarre `llama-server.exe`, puis `opencode.exe`, sans
élévation de privilèges. Le premier fonctionne sans fenêtre de console, avec
un journal de sortie ; le second ouvre une console visible. Le moteur HTTP
écoute sur `127.0.0.1` et utilise une clé aléatoire propre à la session. Le
lanceur vérifie que le port est détenu par le moteur qu’il vient de créer.
Ses requêtes HTTP n’acceptent pas de redirection vers un autre serveur.

Le parent `OpenCodeLocal.exe` reste ensuite actif dans une boucle d’attente
des processus ; **il ne lit ni ne retransmet le texte « salut »**. Le code
correspondant est la fonction `RunSession`, dans
[`src/launcher.cpp`](../src/launcher.cpp#L930-L948). La proximité temporelle
entre la saisie et l’alerte ne suffit pas à identifier l’événement déclencheur.
L’arbre réel des éventuels descendants d’OpenCode doit être relevé dans l’EDR.

Les enfants sont créés suspendus, rattachés à un **Job Object** appartenant
à la session, puis repris. `CreateOwnedJob` configure
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
([source](../src/launcher.cpp#L661-L737)). **Si TEHTRIS termine le lanceur,
la fermeture de son handle de job entraîne aussi l’arrêt de ses enfants.**
Ce mécanisme explique pourquoi toute la session peut disparaître quand le
parent est bloqué ; il n’identifie pas la cause de la détection. Le code ne
modifie pas la protection TEHTRIS, les exclusions antivirus, les règles du
pare-feu ou les services Windows.

Au premier lancement d’une session OpenCode, les dépendances déjà présentes
dans le pack sont copiées vers son dossier de données utilisateur. Aucun
téléchargement n’est requis pour cette préparation. Les commandes de travail
demandées à OpenCode restent soumises aux autorisations de sa configuration.

La revue ciblée du lanceur et de sa compilation n’a pas mis en évidence de
fonction d’injection dans un processus tiers, de désactivation de l’EDR ou de
packer. Elle ne constitue pas un audit complet des composants tiers.
**Aucun défaut de code expliquant cette détection n’a été identifié.** Les
opérations de gestion des processus et l’état de signature sont des éléments
à décrire au support, pas des causes démontrées ni une preuve de faux positif.

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

## Éléments à relever dans la console TEHTRIS

Pour qualifier la détection, relever :

1. L’identifiant de l’alerte, le nom exact de la détection, son moteur et sa
   règle ou catégorie ; préciser s’il s’agit d’un verdict sur fichier ou d’un
   événement comportemental lorsque la console le distingue.
2. Le SHA-256, la taille et la version du fichier concerné ; comparer le hash
   à celui du lanceur 1.2.0 ci-dessus.
3. La chronologie horodatée avec fuseau, le PID, le chemin, le processus
   parent et les lignes de commande associés à l’événement.
4. L’arbre parent/enfants et les opérations retenues comme déclencheurs, dont
   les éventuels descendants d’OpenCode autour de la première saisie.
5. Les actions appliquées (arrêt, quarantaine), la version de l’agent TEHTRIS
   et la politique concernée, si ces informations sont disponibles.

Ces éléments permettent à l’équipe qui administre TEHTRIS de distinguer le
fichier bloqué, son comportement et la décision de protection. La notification
locale « Malware detected » seule ne fournit pas cette qualification.

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
Transmettre les détails nécessaires par le canal de support autorisé du parc,
sans publier de capture du poste ou de données de travail dans une issue publique.

## Support officiel et état du dossier

La [page de contact officielle TEHTRIS](https://tehtris.com/contact) indique
le [portail support](https://my.tehtris.com/) et l’adresse
`support-services@tehtris.com`. L’équipe qui administre le poste peut y joindre
l’export de l’alerte, les journaux pertinents, l’identité attendue du fichier et
les sources de la version concernée pour demander une analyse de la détection.

**Aucune soumission au support TEHTRIS ni demande d’autorisation n’a été
effectuée dans le cadre de cette mise à jour.** La cause et la qualification
de l’alerte restent ouvertes. Aucun correctif de code ou nouveau binaire n’est
présenté comme une résolution ; les protections du poste restent inchangées.

## Références

- Microsoft : [sécurité du chargement des DLL](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-security).
- Microsoft : [LoadLibraryExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw).
- Microsoft : [Job Objects et arrêt des processus du groupe](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- TEHTRIS : [fonctionnement de l’EDR](https://tehtris.com/edr) et [support éditeur](https://tehtris.com/contact).
