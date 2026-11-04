# Détection du lanceur par TEHTRIS

## Ce qui a été observé

Le 4 novembre 2026, le poste de test a signalé **Malware detected** pour
`OpenCodeLocal.exe`. Les notifications indiquaient **Process terminated**, puis
**File quarantined**. Elles montrent donc un arrêt du lanceur et une mise en
quarantaine ; elles ne donnent pas le nom de la règle ou le motif technique de
la détection. Le suffixe `.egambit` visible dans le chemin de quarantaine n’est
pas ajouté par OpenCode Local.

L’exécutable livré avec les versions 1.0.0 et 1.0.1 avait les caractéristiques
suivantes, contrôlées sur le fichier de la release :

| Élément | Valeur |
| --- | --- |
| Nom | `OpenCodeLocal.exe` |
| Taille | 3 476 503 octets |
| SHA-256 | `57461cee2ca4479f8c01712ec804ee2c8a73b90091fd3863ec516bbc3aadc1c5` |
| Architecture | Windows x64 |
| Signature Authenticode embarquée | Absente |
| Ressources de version et manifeste applicatif | Absents dans ce binaire initial |

Cette empreinte correspond au manifeste du pack distribué. Elle doit encore
être comparée à l’empreinte relevée par TEHTRIS sur le poste. Elle ne s’applique
pas aux lanceurs recompilés des nouveaux packs ; leurs empreintes figurent
dans `wad/support/payload-manifest.json` après installation et dans les rapports
`validation/pack-16go.json` et `validation/pack-32go.json` du dépôt.

## Fonctionnement examiné

Le code source du lanceur démarre les composants livrés avec le pack, sans
élévation de privilèges. Le moteur HTTP écoute sur `127.0.0.1` et utilise une
clé aléatoire propre à la session. Le lanceur vérifie que le port est détenu
par le processus qu’il vient de créer. Ses requêtes HTTP n’acceptent pas de
redirection vers un autre serveur.

Les processus sont créés suspendus, rattachés à un **Job Object** appartenant
à la session, puis démarrés. Cette gestion permet d’arrêter les processus du
pack lorsqu’une session se termine. Le code ne modifie pas la protection
TEHTRIS, les exclusions antivirus, les règles du pare-feu ou les services
Windows.

Au premier lancement d’une session OpenCode, les dépendances déjà présentes
dans le pack sont copiées vers son dossier de données utilisateur. Aucun
téléchargement n’est requis pour cette préparation. Les commandes de travail
demandées à OpenCode restent soumises aux autorisations de sa configuration.

La revue du code n’a pas mis en évidence de fonction d’injection dans un
processus tiers ou de désactivation de l’EDR. **Cette revue ne constitue pas
un verdict antivirus et ne permet pas d’affirmer qu’il s’agit d’un faux
positif.** Un binaire récent, sans signature embarquée, et ses opérations de
gestion des processus sont des pistes à examiner avec le détail de l’alerte,
pas des causes démontrées.

## Changements des nouveaux lanceurs

Les nouveaux builds ajoutent des informations de version et un manifeste
Windows explicite `asInvoker`. Le chargement de `dwmapi.dll`, nécessaire à
l’apparence de la fenêtre, est limité au répertoire système Windows. Ces
changements améliorent l’identification du logiciel et le chargement de sa
DLL système. Ils ne prouvent pas que TEHTRIS acceptera ces nouveaux binaires.

Les ressources de version ne constituent pas une signature numérique. Une
signature Authenticode ne peut être ajoutée qu’avec un certificat de signature
de code approprié et une chaîne de confiance reconnue sur les postes cibles.

## Éléments à relever dans la console TEHTRIS

Pour qualifier la détection, relever :

1. Le nom exact de la détection et du moteur ou de la règle qui l’a produite.
2. Le SHA-256 du fichier concerné et sa version de pack.
3. L’heure, le chemin de l’exécutable, le processus parent et la ligne de
   commande affichés dans l’événement.
4. Le moment du blocage : ouverture du lanceur, préparation du premier
   lancement, clic sur **Ouvrir OpenCode** ou utilisation d’un outil.
5. L’arbre des processus et les opérations associées à l’alerte, si la console
   les fournit.

Ces éléments, les sommes de contrôle de la distribution et les sources de la
version concernée permettent à l’équipe qui administre TEHTRIS ou au support
éditeur d’examiner le cas. Les éventuelles décisions d’autorisation dépendent
de cette qualification et des politiques du parc. La compatibilité avec la
politique TEHTRIS du poste reste à valider sur ce poste.

## Références

- Microsoft : [sécurité du chargement des DLL](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-security).
- Microsoft : [LoadLibraryExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw).
- TEHTRIS : [fonctionnement de l’EDR](https://tehtris.com/edr) et [support éditeur](https://tehtris.com/contact).
