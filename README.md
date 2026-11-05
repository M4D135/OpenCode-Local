# OpenCode Local — pack unique MoE léger

Un assistant de code local pour Windows x64, avec **OpenCode**, un moteur CPU
et **Qwen3.6-35B-A3B UD-IQ2_XXS**. La version 1.2.0 remplace les deux anciens
packs 16/32 Go par un seul pack, réglé pour un PC de 32 Gio disposant d'environ
20 Gio de RAM libres avant lancement.

## Télécharger et installer hors ligne

La [release 1.2.0](https://github.com/M4D135/OpenCode-Local/releases/tag/v1.2.0)
est publiée après validation Windows et exécution réelle du modèle.
[Télécharger directement Setup.zip — 127,3 Mo](https://github.com/M4D135/OpenCode-Local/releases/download/v1.2.0/Setup.zip).

1. Télécharger **Setup.zip** et les **huit fichiers Modele-MoE-01.bin à Modele-MoE-08.bin** de la même release.
2. Extraire `Setup.exe` du ZIP : **il contient uniquement cet exécutable**.
3. Placer `Setup.exe` et les huit `.bin` dans le même dossier, puis lancer le setup.
4. Ouvrir **OpenCode Local MoE**, choisir le projet et cliquer sur **Ouvrir OpenCode**.

Le modèle GGUF complet peut remplacer les huit parties. Les anciens fichiers
`Setup-*.bin` appartiennent à d'autres modèles et ne sont pas réutilisables.
L'installateur vérifie les tailles et le SHA-256 complet du modèle avant de
l'utiliser. Aucun téléchargement n'est lancé sur le poste cible.

## Mémoire et CPU

| Réglage | Pack unique |
| --- | --- |
| Modèle | Qwen3.6-35B-A3B, MoE 35B total / 3B actifs |
| Quantification | UD-IQ2_XXS, priorité à l'économie mémoire |
| Fichier des poids | 10 756 586 464 octets : 10,76 Go / 10,02 Gio |
| Contexte | 8 192 tokens par défaut ; 4 096 disponible |
| Budget de session au démarrage | Estimation prudente de 13 075 Mio / 12,77 Gio à 8K |
| Réserve pour les autres logiciels | 6 144 Mio / 6 Gio |
| RAM libre nécessaire au démarrage | 19 219 Mio / 18,77 Gio à 8K |
| Poids | Mappés en mémoire, sans copie de repacking |
| Cache de conversations du serveur | Désactivé ; quatre checkpoints récurrents |
| Cache KV | BF16, Flash Attention |
| Calcul | Une séquence, batch 256, micro-batch 64 |
| Threads | Cœurs physiques, maximum 8 ; un cœur laissé disponible à partir de 4 |

Les 3B actifs réduisent le travail de calcul par token ; **tous les experts
restent dans le budget des poids**. La RAM dépend aussi du contexte, des outils
et des applications ouvertes. Le contrôle de démarrage vérifie une estimation
avec réserve, sans imposer de plafond système. La quantification IQ2 peut réduire
la qualité du modèle ; les scores du modèle original ne sont pas ceux de ce pack.

Les résultats mesurés sont fournis dans les rapports `VALIDATION-MODELE.json`
et `VALIDATION-WINDOWS.json` de la release. Une mesure Linux ne constitue pas
une qualification de toutes les configurations Windows.

L'essai complet sur le runner Linux à quatre threads a mesuré **10,97 Gio de
RSS combiné moteur/OpenCode**, sans swap observé pour ces processus. Génération,
lecture réelle avec OpenCode et refus d'écriture sans autorisation ont réussi.
Le premier échange OpenCode de ce scénario a pris **316 secondes**, puis le
contrôle d'autorisation **19 secondes** : un premier échange sur CPU peut donc
prendre plusieurs minutes. Ces durées ne prédisent pas celles de chaque portable.
Les mesures, paramètres et limites sont conservés dans [VALIDATION.md](VALIDATION.md).

## Utilisation et données

- **Ouvrir OpenCode** : le modèle écoute uniquement sur `127.0.0.1`, avec une clé de session aléatoire.
- **Discussion directe** : console sans serveur HTTP si les connexions localhost sont interdites.
- **Arrêter** : ferme les processus de la session. Le verrou de profil évite un second modèle simultané.
- **Diagnostic** et **Journaux** : fichiers locaux avec mémoire détectée, paramètres et erreurs.

Le programme s'installe dans `%LOCALAPPDATA%\Programs\OpenCodeLocalMoE` et
conserve ses données dans `%LOCALAPPDATA%\OpenCodeLocalMoE`. Les profils et
installations historiques restent séparés ; leur suppression locale n'est pas
automatique. La désinstallation conserve les projets et données personnelles.

OpenCode peut lire le projet ; l'écriture et les commandes demandent une
permission. Les fonctions web, le partage et les mises à jour automatiques sont
désactivés. Une commande shell explicitement autorisée conserve les possibilités
réseau de la session Windows.

## Code signing policy

**La distribution 1.2.0 est explicitement non signée.** La chaîne est préparée
pour une signature SignPath en deux étapes, mais aucun certificat n'a été fourni.
La demande au programme gratuit SignPath Foundation est préparée ; elle n'est
pas présentée comme acceptée.

Voir la [Code signing policy](CODE_SIGNING.md), la [politique de confidentialité](PRIVACY.md)
et le [dossier de candidature](docs/SIGNPATH_APPLICATION.md).
**Un nouveau blocage TEHTRIS a été signalé le 5 novembre 2026**, avec un pack
présenté comme 1.2.0 : arrêt et quarantaine de `OpenCodeLocal.exe` après la
première saisie « salut ». Le SHA-256 du fichier bloqué et la règle de détection
ne sont pas disponibles ; la cause reste à qualifier. Les validations de
construction et du modèle ne prouvent pas l’acceptation par l’EDR. Le
[dossier TEHTRIS](docs/TEHTRIS.md) distingue l’identité attendue du lanceur,
l’historique et les éléments à recueillir. Aucune protection du poste n’est modifiée.

## Construction et validation

[Instructions de construction](BUILD.md) · [Rapports et limites](VALIDATION.md)
· [Modèle et sources](docs/MODELES.md) · [Licences tierces](THIRD_PARTY.md)

Le workflow de publication attend les tests Windows, vérifie l'inférence et les
appels d'outils, puis transfère le modèle par parties. Après publication vérifiée,
les anciennes releases 1.0.0, 1.0.1 et 1.1.0 ont été supprimées : **19,84 Go de
fichiers**, plus **769 Mo d'anciens artefacts Actions**. Leurs tags et l'historique
du code sont conservés.
