# Code signing policy

## Current status

**Signing is not active.** The historical 1.0.0, 1.0.1 and 1.1.0 Windows packs
were unsigned. The single MoE 1.2.0 distribution is also explicitly unsigned.
The integration prepares a verifiable signing pipeline and
a SignPath Foundation application. Preparing the integration is not acceptance
by the Foundation, certificate issuance or a completed signing operation.

The last confirmed application state on 4 November 2026 was prepared, not
submitted. This source update does not assert a later submission or acceptance. No signing certificate or SignPath API credential has been
provided to this repository. Only a release whose signature verification
report succeeds may be described as signed.

## Proposed service and attribution

The project intends to apply to the free open-source signing program operated
by [SignPath Foundation](https://signpath.org/). If the application is accepted
and signing is activated, signed releases will carry this attribution:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

That sentence describes the **proposed service**, not a claim that current
releases are signed or that SignPath has approved this application.
The Foundation's [conditions](https://signpath.org/terms) apply to that service;
account setup and acceptance of those conditions must be completed by the
maintainer. In particular, signing requests need a designated human approver.

## Maintainer and signing roles

Repository: [M4D135/OpenCode-Local](https://github.com/M4D135/OpenCode-Local).

- Maintainer, committer and reviewer: [M4D135](https://github.com/M4D135).
- Proposed release-signing approver: M4D135, once the service is configured.
- Contributions from other authors must be reviewed before signing.
- Before activating signing, each person with repository or signing access
  must confirm that multi-factor authentication is enabled as required by
  SignPath. This policy does not assert that an account setting has already
  been checked.

No workflow may grant itself signing approval, select an arbitrary certificate,
or silently replace a failed production signature with a test certificate.

## Files covered by this project

Only the following artifacts built from this repository's source code are
submitted for this project's signatures in the single `moe` profile:

| File | Purpose |
| --- | --- |
| `OpenCodeLocal.exe` | Native Windows launcher |
| `AssembleModel.exe` | Model verification and assembly helper |
| `Uninstall.exe` | Uninstaller generated from this repository's NSIS script |
| `Setup.exe` | Final installer assembled from the verified inputs |

OpenCode, llama.cpp, ripgrep, the Microsoft runtime, parser binaries and other
upstream components retain their original signatures, or remain unsigned.
They are not signed as if maintained by this project. Their sources, pinned
hashes and redistribution notices are described in [THIRD_PARTY.md](THIRD_PARTY.md)
and the lock files. Microsoft's runtime is a proprietary system runtime
redistributed under its own license; its eligibility under SignPath's system
library exception must be assessed by the Foundation.

Model GGUF files and their `.bin` parts do not change when executables are
signed. Their existing SHA-256 checks remain applicable. A checksum is an
integrity check, not an Authenticode signature.

## Build and verification requirements

The signing workflow must build the integration binaries from the checked-out
commit on standard GitHub-hosted runners. A manually supplied executable or
an executable downloaded from an old release cannot substitute for that build.
Upstream inputs are downloaded from their pinned sources and checked against
the repository's lock files.

The build sequence is:

1. Compile the launchers and assemblers; generate the uninstallers from NSIS.
2. Upload only these three project-owned binaries and their build metadata as
   the native-artifact signing request.
3. Obtain the signed binaries after the service's approval and verify the
   expected signer, valid Authenticode signatures, timestamps and preservation
   of the executable content.
4. Recompute the payload manifests using the signed inputs and build the
   installer with the signed uninstaller embedded unchanged.
5. Submit the final installer as a separate signing request.
6. Verify the final signatures and packaging, then create the ZIP containing
   exactly one `Setup.exe` and regenerate distribution hashes.

The separate `publish-moe.yml` workflow can publish an explicitly unsigned
release after Windows and real model/harness validation. It cannot claim
SignPath approval or access signing secrets. The release plan records the
maintainer-requested cleanup of old downloads only after replacement checks.

The signed path must fail if credentials, an approved policy, a required
signature, the expected certificate identity or any required verification are
missing. Unsigned validation builds are useful test artifacts, but must be
labelled as such and must not be published as signed releases.

The signing job is started manually by a maintainer. Pull requests cannot use
signing secrets. The configured SignPath service must also enforce repository,
branch, artifact and human-approval constraints; workflow YAML alone does not
configure those server-side controls. The proposed artifact definitions are
versioned in `.signpath/` for review and import after acceptance.

## Endpoint security and privacy

An early unsigned launcher was terminated and quarantined by TEHTRIS on one
reported workstation. The available notification did not identify the precise
rule or establish a false positive. [TEHTRIS.md](docs/TEHTRIS.md) records this
open issue. Signing establishes provenance; it does not override an antivirus
decision, establish that the program is harmless, or prove acceptance by that
workstation's policy.

The software does not modify endpoint protection, firewall rules or certificate
trust stores. No installation step should add a test root certificate or an
antivirus exclusion. For data handling, see [PRIVACY.md](PRIVACY.md).

## Reporting a signing or provenance problem

Report a discrepancy through the repository's issues, identifying the release,
filename and SHA-256. Remove personal paths, credentials and workplace data
from public reports. The maintainer must investigate reported provenance or
policy violations and cooperate with SignPath if the service is activated.

References: [SignPath conditions](https://signpath.org/terms),
[GitHub build provenance](https://docs.signpath.io/trusted-build-systems/github),
[Microsoft code-signing guidance](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/deployment/use-code-signing-for-better-control-and-protection).
