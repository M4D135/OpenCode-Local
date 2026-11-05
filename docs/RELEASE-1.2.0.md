## Pack unique MoE léger

Cette version remplace les deux packs 16/32 Go par **un seul pack**, réglé pour
un PC de 32 Gio ayant environ **20 Gio de RAM disponibles**.

### Téléchargements

- **Setup.zip** : contient uniquement `Setup.exe`.
- **Modele-MoE-01.bin à Modele-MoE-08.bin** : les huit parties du nouveau modèle, à placer à côté de `Setup.exe`.
- **SHA256SUMS.txt** et **MODELE.json** : empreintes et provenance du contenu.

Les anciens `.bin` appartiennent à d'autres modèles et ne sont pas compatibles.
Le modèle complet `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf` peut remplacer les huit parties.
Aucun téléchargement n'est lancé sur le poste cible.

### Modèle et consommation

**Qwen3.6-35B-A3B UD-IQ2_XXS** : MoE 35B total / 3B actifs, poids de **10,76 Go
(10,02 Gio)**, soit **5,71 Go de moins** que l'ancien fichier 27B.

- Cache de conversations du serveur désactivé ; un seul checkpoint.
- Poids mappés sans copie de repacking ; une seule session de calcul.
- Contexte initial 8 192 tokens, cache KV BF16, batch 256 / micro-batch 64.
- Threads limités aux cœurs physiques, avec une marge pour le poste.
- Budget de démarrage estimé à **12,52 Gio**, plus **6 Gio de réserve**.

Le budget est une estimation et non un plafond Windows. Les experts inactifs
comptent aussi dans la mémoire des poids. La quantification IQ2 peut réduire la
qualité ; aucun benchmark du modèle original n'est présenté comme un résultat
de cette version.

### Vérifications

La publication attend les contrôles Windows du lanceur, de l'assembleur, des
chemins d'installation et du contenu exact du setup. Elle exige aussi une vraie
inférence Linux, un aller-retour d'appel d'outil et une session OpenCode sur un
fichier de preuve. Les rapports détaillés sont joints à cette release.
Ces contrôles ne remplacent pas une installation complète et une mesure sur
chaque modèle de portable Windows.

### Signature et antivirus

**Version non signée.** La [Code signing policy](https://github.com/M4D135/OpenCode-Local/blob/main/CODE_SIGNING.md)
prépare une candidature au programme gratuit SignPath Foundation ; aucune
acceptation ni délivrance de certificat n'est revendiquée. Une ancienne détection
TEHTRIS reste à qualifier et son acceptation n'est pas garantie.

Après vérification des fichiers de cette release, les anciennes releases
1.0.0, 1.0.1 et 1.1.0 sont retirées à la demande du mainteneur. Les tags et
l'historique du code sont conservés.
