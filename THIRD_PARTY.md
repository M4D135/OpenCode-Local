# Composants tiers

Ce pack assemble des composants indépendants. Il n’est pas une distribution officielle d’OpenCode, Qwen, Unsloth, llama.cpp ou Microsoft.

| Composant | Licence / attribution | Source |
|---|---|---|
| OpenCode | MIT, copyright OpenCode ; exécutable autonome officiel non modifié | https://github.com/anomalyco/opencode |
| llama.cpp | MIT, The ggml authors | https://github.com/ggml-org/llama.cpp |
| Modèle Qwen3.8-27B | Apache 2.0, Qwen ; quantification GGUF par Unsloth | https://huggingface.co/Qwen/Qwen3.8-27B et https://huggingface.co/unsloth/Qwen3.8-27B-GGUF |
| Modèle Qwen3.5-4B | Apache 2.0, Qwen ; quantification GGUF par Unsloth | https://huggingface.co/Qwen/Qwen3.5-4B et https://huggingface.co/unsloth/Qwen3.5-4B-GGUF |
| ripgrep | MIT / Unlicense, Andrew Gallant et contributeurs | https://github.com/BurntSushi/ripgrep |
| LLVM OpenMP fourni avec le moteur | Apache 2.0 avec exceptions LLVM ; texte original dans `LICENSE-LLVM-OpenMP` | https://github.com/llvm/llvm-project |
| Microsoft Visual C++ Runtime | Conditions de redistribution Microsoft ; DLL officielles x64 placées à côté du moteur | https://aka.ms/VCRedistLicense |
| Dépendances npm d’OpenCode | Licences propres à chaque package, verrouillées dans `config/offline/package-lock.json` | Registre npm et dépôts indiqués par les packages |
| Grammaires et requêtes Tree-sitter | Licences des auteurs d’origine, conservées avec leur manifeste | `config/offline/grammar-assets.lock.json` |
| NSIS | zlib/libpng pour le moteur et licences associées à ses composants | https://nsis.sourceforge.io/License |
| Runtime GCC/MinGW statiquement lié à l’intégration | GCC Runtime Library Exception et licences MinGW-w64 | https://www.gnu.org/licenses/gcc-exception-3.1.html et https://www.mingw-w64.org/ |

Les textes complets des licences et les notices redistribuées se trouvent dans `wad/licenses` dans le pack. Les licences fournies avec les dépendances restent également présentes dans leur arborescence. Les sources de l’intégration sont disponibles dans ce dépôt. Aucun logo ou certificat ne suggère une validation du pack par les projets cités.

