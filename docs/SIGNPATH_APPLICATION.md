# SignPath Foundation application — prepared information

Status on 4 November 2026: **prepared, not submitted**. Account contact details
and acceptance of the Foundation's terms must be completed by the maintainer.
No certificate or service access has been granted.

The live application is at <https://signpath.org/apply>. Do not submit a second
application if the maintainer has already submitted this one. Update this
status from an actual submission confirmation, not from a filled form.

## Project fields

| Field | Prepared value |
| --- | --- |
| Project name | OpenCode Local |
| Repository URL | https://github.com/M4D135/OpenCode-Local |
| Homepage URL | https://github.com/M4D135/OpenCode-Local |
| Download URL | https://github.com/M4D135/OpenCode-Local/releases |
| Privacy policy URL | https://github.com/M4D135/OpenCode-Local/blob/main/PRIVACY.md |
| Code signing policy | https://github.com/M4D135/OpenCode-Local/blob/main/CODE_SIGNING.md |
| Category | Developer Tools |
| Maintainer type | Individual maintainer(s) |
| Build system | GitHub Actions |
| Discovery channel | AI / LLM tools |
| Exact source | ChatGPT, followed by verification against Microsoft Learn and SignPath Foundation documentation. |

### Tagline

Offline Windows packs for running OpenCode with local language models.

### Description

OpenCode Local is an independent open-source Windows integration project that
packages a native launcher, a per-user offline installer and upstream components
for local AI-assisted coding. It provides separate memory profiles, local model
integrity checks, an OpenCode coding session and a direct chat mode. Projects
and conversations remain on the local computer by default. The application does
not change antivirus, firewall or endpoint protection settings. It is not
affiliated with the upstream OpenCode, llama.cpp or Qwen projects.

### Reputation — factual statement

This is a new project, first published on 4 November 2026. It does not yet have
independent media coverage or an established user community. Its public source,
tagged releases, model provenance, checksums and validation reports are available
at https://github.com/M4D135/OpenCode-Local . The unsigned build and Windows
component validation passed in
https://github.com/M4D135/OpenCode-Local/actions/runs/41922735490 ; this was not a
complete installation with production models. An endpoint security detection
on an early unsigned launcher is openly documented in docs/TEHTRIS.md; the cause
has not been classified as a false positive. We request an eligibility review
and do not claim an established reputation.

Do not use the popularity of upstream OpenCode or Qwen as this project's own
reputation. The Foundation may decline a new project or request more evidence.

## Maintainer fields and final submission

The form requires the maintainer's first name, last name and email address for
account creation and notifications. These values are intentionally not recorded
in this public repository. The company field is optional; this individual
project must not be presented as endorsed by a user's employer.

The form also requires explicit agreement to the Foundation's Code of Conduct
and processing of the supplied personal data. Optional marketing consent is
separate. The maintainer must review those choices on the live form. Complete
any CAPTCHA personally if one is presented. A submission confirmation is not
the same as approval or issuance of signing credentials.

## Points to disclose during review

- Our integration source uses MIT; third-party licenses remain separate.
- Only our launcher, model assembler, generated uninstaller and final setup
  will receive our project signatures. Upstream executables are not re-signed.
- Microsoft Visual C++ runtime DLLs are proprietary system libraries and retain
  their Microsoft license. Ask the Foundation to confirm that the system-library
  exception covers this redistribution.
- Full model weights and the large `.bin` parts are not submitted for signing.
- The proposed trusted build is a GitHub-hosted workflow; local binaries are
  not substituted for its compiled outputs. Signing is manually initiated and
  still requires the service's designated human approval.
- There is an unresolved TEHTRIS detection on an early unsigned launcher. A
  signature is not being represented as an antivirus approval.
- Existing releases remain explicitly unsigned until the complete signed path
  has succeeded and the resulting artifacts have passed verification.
