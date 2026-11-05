# Modèle du pack unique 1.2.0

Le modèle retenu est **Qwen3.6-35B-A3B UD-IQ2_XXS**, un MoE avec 35 milliards
de paramètres au total et environ 3 milliards actifs par token. Le pack utilise
uniquement le texte ; aucun projecteur d'images ni modèle auxiliaire n'est chargé.

## Source figée

- Auteur du modèle : [Qwen](https://huggingface.co/Qwen/Qwen3.6-35B-A3B).
- Conversion GGUF : [Unsloth](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF).
- Révision : `a483e9e6cbd595906af30beda3187c2663a1118c`.
- Fichier : `Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf`.
- Taille : **10 756 586 464 octets**.
- SHA-256 : `2e8f5f705355c56311432d0a8a5d14a696dbb7e4b197d05c75ba805fc1857bef`.
- Licence : Apache-2.0.

Les métadonnées LFS ont été vérifiées auprès de Hugging Face le 5 novembre 2026.
Le téléchargement complet et chaque partie publiée sont contrôlés par SHA-256.
Le fichier complet est 5 707 853 760 octets plus petit que l'ancien
Qwen3.8-27B UD-Q4_K_M. Cette différence de taille ne promet pas une baisse
strictement identique du pic de RAM.

## Pourquoi ce choix

La cible est un PC avec **20 Gio disponibles**. Une quantification plus forte
laisse une marge au moteur, à OpenCode et aux applications du poste. IQ2_XXS
privilégie donc la mémoire ; elle peut dégrader la précision et la fiabilité des
outils par rapport aux quantifications plus larges. Aucun score de benchmark du
modèle original n'est revendiqué pour cette conversion.

Le MoE réduit les calculs par token. Il ne dispense pas de conserver l'ensemble
des poids. Les tailles de modèle, la mémoire des experts actifs et la consommation
totale du processus sont trois mesures différentes.

## Réglages mémoire

Le cache serveur de prompts est désactivé (`--cache-ram 0`), les checkpoints
récurrents sont limités à un, et le chargement utilise `--load-mode mmap
--no-repack`. Le runtime b12089 pourrait sinon consacrer par défaut jusqu'à
8 Gio au cache de prompts, indépendamment du cache KV.

Le contexte initial est **8 192 tokens** ; 4 096 reste disponible. Le modèle
hybride n'a que dix couches d'attention complète : le cache KV BF16 théorique
est d'environ 160 Mio à 8K, hors états récurrents et buffers. Réduire ce contexte
à 4K n'économise qu'environ 80 Mio, mais retire de la place aux instructions et
schémas d'outils OpenCode. BF16 est conservé pour éviter une quantification
supplémentaire du cache pour un gain limité.

Le budget de démarrage compte les poids complets, 2 Gio de surcoût moteur/outils
et 256 Mio par bloc de 4K pour les caches/buffers, puis ajoute 6 Gio de réserve.
À 8K : 12 819 Mio estimés pour la session, 18 963 Mio libres demandés au total.
Ce calcul est une précaution de démarrage, **pas une réservation de RAM** et
pas un maximum imposé par Windows.

Sources techniques : [configuration officielle du modèle](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/config.json),
[valeurs par défaut llama.cpp b12089](https://github.com/ggml-org/llama.cpp/blob/b12089/common/common.h),
[options du moteur](https://github.com/ggml-org/llama.cpp/blob/b12089/common/arg.cpp).
