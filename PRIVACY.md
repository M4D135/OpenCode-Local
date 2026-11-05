# Privacy policy

Updated 5 November 2026. This policy describes the OpenCode Local integration
distributed by [M4D135/OpenCode-Local](https://github.com/M4D135/OpenCode-Local).
It does not describe a hosted AI service or establish a relationship with the
upstream OpenCode, llama.cpp, Qwen or Microsoft projects.

## Local application data

The pack runs a language model on the user's computer. The chosen project,
prompts, conversation history, files read by approved tools and their outputs
can contain personal or confidential information. The integration stores its
settings, logs and the upstream application's state locally in
`%LOCALAPPDATA%\OpenCodeLocalMoE`. Historical profiles in
`%LOCALAPPDATA%\OpenCodeLocal` and `%LOCALAPPDATA%\OpenCodeLocal32Go` are
not deleted by this version. The user's project may be elsewhere.

Logs and diagnostics can include local paths, model names, process identifiers,
error messages and system information. A user who exports or posts these files
should review them and remove credentials, private paths and other sensitive
content before sharing.

The uninstaller removes known program files and retains user profiles and
projects. Local history and settings therefore remain until the user removes
them through the application or deletes the relevant retained profile.

## Network behavior and third-party components

By default, OpenCode talks to the local inference engine on `127.0.0.1`, with a
per-session API key. The direct-chat mode does not need that HTTP connection.
Cloud providers, automatic updates, automatic sharing, external plugins, web
search, automatic language-server downloads and telemetry are disabled by the
pack's configuration. Its startup dependencies and model files are included
for offline use.

The integration does not operate a server that collects prompts, project files,
conversation history, usage analytics or crash reports. It does not add an
automatic reporting endpoint. This is a statement about the reviewed
configuration and code, not a claim that all possible network packets have
been captured or that a third-party application cannot change its behavior.

OpenCode can ask to run normal commands or edit files. A command the user
authorizes can itself contact another system, install software or send data.
Those actions are governed by the user's instructions, permissions and the
software invoked; this pack is not a network sandbox. Changes to its provider,
plugin or tool configuration can also change data handling.

Source projects and license information:

- [OpenCode](https://github.com/anomalyco/opencode).
- [llama.cpp](https://github.com/ggml-org/llama.cpp).
- Model provenance in [config/packs.json](config/packs.json).
- Complete component notices in [THIRD_PARTY.md](THIRD_PARTY.md).

The pack does not change antivirus, endpoint protection, firewall rules or
certificate trust stores. A user's organization may independently monitor its
managed devices; that monitoring is outside the pack's control.

## Website, downloads and signing

Source code, issues and downloads are hosted on GitHub. Visiting GitHub,
downloading a release or posting an issue is subject to
[GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
Public issue content is visible to other people; the application does not
post an issue or upload diagnostics automatically.

The project is preparing a SignPath Foundation application. If accepted,
SignPath will receive project-built executable artifacts and build metadata
to perform signing. These signing inputs must not contain users' projects,
prompts, private keys or other personal application data. Application and
maintainer account information supplied directly to SignPath is governed by
[SignPath's privacy policy](https://signpath.io/privacy-policy).

See [Code signing policy](CODE_SIGNING.md) for the current signing status. A
prepared workflow or application is not evidence that a certificate has been
issued or that any published executable is signed.

## Contact and changes

Questions about this integration can be raised through the repository's issues.
Do not include sensitive information in a public report. Changes that introduce
an automatic external data transfer must be documented here and communicated
in the release notes before they are enabled.
