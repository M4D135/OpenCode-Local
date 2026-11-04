# Modèles des packs 16 Go et 32 Go

Les deux packs utilisent OpenCode et le moteur CPU llama.cpp. Chaque pack contient son propre modèle ; les fichiers de modèle des deux packs ne sont pas interchangeables. La configuration figée se trouve dans [config/packs.json](../config/packs.json).

| Pack | Modèle texte | Quantification | Taille exacte du GGUF | Contexte par défaut | Sortie maximale |
| --- | --- | --- | ---: | ---: | ---: |
| 16 Go | Qwen3.5-4B | Q4_K_M | 2 740 937 888 octets | 8 192 tokens | 2 048 tokens |
| 32 Go | Qwen3.8-27B | UD-Q4_K_M | 16 464 440 224 octets | 16 384 tokens | 4 096 tokens |

## Choix et mémoire

Le pack 16 Go conserve exactement le modèle déjà livré en version 1.0.0/1.0.1, avec la même révision et la même empreinte. Ses deux fichiers `Setup-1.bin` et `Setup-2.bin` restent donc réutilisables.

Le pack 32 Go propose un modèle dense de 27 milliards de paramètres. Qwen décrit des améliorations en code et en tâches agentiques pour la génération 3.8. Son architecture reste `qwen3_5`, également utilisée par Qwen3.5. La compatibilité architecturale ne remplace pas un essai effectif du modèle et des appels d'outils avec la version embarquée du moteur.

Unsloth indique environ 5,5 Go de mémoire d'inférence pour Qwen3.5-4B en 4 bits et 17 à 19 Go pour Qwen3.8-27B en 4 bits. Ce sont des indications générales de l'éditeur des quantifications, pas des mesures de ces packs sur le poste cible, et elles n'incluent pas une garantie sur la mémoire consommée par Windows, OpenCode et les autres applications.

Le contexte est volontairement limité et une seule séquence doit être exécutée simultanément. Le pack 32 Go réserve une marge en exigeant 22 528 Mio de mémoire disponible avant chargement. Pour le pack 16 Go, ce seuil est de 6 144 Mio. Le suffixe `_mb` des champs de configuration correspond ici à des Mio (1 024 × 1 024 octets). Ces seuils sont des choix de fonctionnement prudents, à ajuster uniquement à partir de mesures réelles.

Sur CPU, le modèle 27B doit traiter beaucoup plus de poids que le 4B : une réponse et un parcours d'agent peuvent donc prendre sensiblement plus de temps. Aucun débit en tokens par seconde n'est garanti sans connaître le processeur et mesurer le fonctionnement réel. Les capacités de contexte maximales annoncées par les modèles ne sont pas les réglages de ces packs.

Le pack 32 Go est distribué en onze parties de modèle : dix parties de 1 500 000 000 octets, puis une dernière de 1 464 440 224 octets, nommées `Setup-32Go-01.bin` à `Setup-32Go-11.bin`. Les modèles sont destinés ici au texte et au code ; aucun projecteur visuel ni modèle MTP supplémentaire n'est requis.

## Sources et provenance

Sources consultées le 4 novembre 2026 :

- [Modèle officiel Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B).
- [Modèle officiel Qwen3.8-27B : architecture, capacités et paramètres](https://huggingface.co/Qwen/Qwen3.8-27B).
- [Guide Unsloth Qwen3.5 : indications de mémoire](https://unsloth.ai/docs/models/qwen3.5).
- [Qwen3.8-27B chez Unsloth : indications de mémoire](https://unsloth.ai/models/qwen3.8-27b).
- [GGUF 16 Go à la révision figée](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf).
- [GGUF 32 Go à la révision figée](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/blob/4ca720788d1e01f1bff70c033e0d0028fd02e502/Qwen3.8-27B-UD-Q4_K_M.gguf).

Les deux modèles sont distribués sous licence Apache-2.0. Les GGUF sont des quantifications publiées par Unsloth à partir des modèles Qwen ; les modèles d'origine sont publiés par Qwen.

Les tailles et empreintes du modèle 32 Go ont été relevées dans les métadonnées de son dépôt. Le téléchargement doit toujours être contrôlé contre la taille et le SHA-256 figés dans `config/packs.json`. La sélection d'un modèle, la compilation du pack, la validation d'une archive et un essai d'inférence sont des vérifications distinctes : seuls les essais effectivement exécutés doivent être annoncés comme réussis.

