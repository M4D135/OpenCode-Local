#!/usr/bin/env python3
"""Small, secret-free checks for the signing workflow's configuration and artifacts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "{http://signpath.io/artifact-configuration/v1}"
PROFILES = ("16go", "32go")
HOUSE_FILES = ("OpenCodeLocal.exe", "AssembleModel.exe", "Uninstall.exe")


def expected_files(phase: str) -> set[str]:
    names = HOUSE_FILES if phase == "native" else ("Setup.exe",)
    return {profile + "/" + name for profile in PROFILES for name in names}


def configuration() -> str:
    version = json.loads((ROOT / "config/packs.json").read_text(encoding="utf-8"))["app_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("config/packs.json must contain an explicit numeric three-part app_version")
    for phase in ("native", "setup"):
        document = ET.parse(ROOT / ".signpath" / (phase + "-artifacts.xml")).getroot()
        if document.tag != NAMESPACE + "artifact-configuration":
            raise ValueError("Unexpected SignPath artifact configuration namespace")
        parameters = document.find(NAMESPACE + "parameters")
        if parameters is None or len(parameters) != 1 or parameters[0].attrib != {"name": "version", "required": "true"}:
            raise ValueError("The source version must be required, with no fallback version")
        archive = document.find(NAMESPACE + "zip-file")
        if archive is None or {element.get("path") for element in archive} != expected_files(phase):
            raise ValueError("The SignPath allowlist must select only the expected home-built PE files")
        if len(archive) != len(expected_files(phase)) or len(document) != 2:
            raise ValueError("Unexpected signing directive or duplicate artifact entry")
        for element in archive:
            if element.tag != NAMESPACE + "pe-file":
                raise ValueError("Only explicit PE files may be selected")
            attributes = {"path": element.get("path"), "product-name": "OpenCode Local",
                          "product-version": "${version}",
                          "original-filename": PurePosixPath(element.get("path", "")).name}
            if element.attrib != attributes:
                raise ValueError("Unexpected PE name or version restriction")
            if len(element) != 1 or element[0].tag != NAMESPACE + "authenticode-sign":
                raise ValueError("Exactly one Authenticode signing directive is required")
            if element[0].get("hash-algorithm") != "sha256":
                raise ValueError("The signing digest must be SHA-256")
    pins = json.loads((ROOT / ".signpath/tool-versions.json").read_text())["actions"]
    for filename in ("validate-unsigned.yml", "signpath-release.yml"):
        text = (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8")
        for action in re.findall(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", text, re.MULTILINE):
            repository, separator, commit = action.partition("@")
            if not separator or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError("An action is not pinned to a full commit: " + action)
            if pins.get(repository.lower(), {}).get("commit") != commit:
                raise ValueError("An action differs from tool-versions.json: " + action)
    print("SignPath configuration and action pins checked; source version " + version)
    return version


def require_signpath() -> None:
    if os.environ.get("GITHUB_REPOSITORY") != "M4D135/OpenCode-Local":
        raise ValueError("Signing is limited to the maintained M4D135/OpenCode-Local repository")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Signing requires a manual workflow_dispatch on main")
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("Start a new manual workflow instead of re-running a signing job")
    required = ("SIGNPATH_ORGANIZATION_ID", "SIGNPATH_PROJECT_SLUG", "SIGNPATH_SIGNING_POLICY_SLUG",
                "SIGNPATH_NATIVE_ARTIFACT_CONFIGURATION_SLUG", "SIGNPATH_SETUP_ARTIFACT_CONFIGURATION_SLUG",
                "SIGNPATH_EXPECTED_PUBLISHER", "SIGNPATH_EXPECTED_SIGNER_SHA256", "REQUESTED_SIGNING_POLICY")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if os.environ.get("HAS_SIGNPATH_API_TOKEN", "").lower() != "true":
        missing.append("secret SIGNPATH_API_TOKEN")
    if missing:
        raise ValueError("SignPath is not configured: " + ", ".join(missing) +
                         ". No signing request or signed release will be produced.")
    for name in required:
        if os.environ[name] != os.environ[name].strip() or any(ord(c) < 32 for c in os.environ[name]):
            raise ValueError("Whitespace or control characters in " + name)
    uuid.UUID(os.environ["SIGNPATH_ORGANIZATION_ID"])
    for name in required:
        if name.endswith("SLUG") or name == "REQUESTED_SIGNING_POLICY":
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", os.environ[name]):
                raise ValueError("Invalid SignPath slug in " + name)
    if os.environ["REQUESTED_SIGNING_POLICY"] != os.environ["SIGNPATH_SIGNING_POLICY_SLUG"]:
        raise ValueError("The explicitly requested signing policy does not match the approved repository configuration")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", os.environ["SIGNPATH_EXPECTED_SIGNER_SHA256"]):
        raise ValueError("SIGNPATH_EXPECTED_SIGNER_SHA256 must be the 64-character SHA-256 of the approved DER certificate")
    print("Explicit SignPath configuration present; service approval and certificate validity remain required")


def regular_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.exists():
        raise ValueError("Missing or linked artifact path: " + str(root))
    paths = [root] if root.is_file() else list(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Symlinks are not permitted in signing artifacts")
    return [path for path in paths if path.is_file()]


def layout(phase: str, root: Path, max_bytes: int) -> None:
    paths = regular_files(root)
    actual = {path.relative_to(root).as_posix() for path in paths}
    if actual != expected_files(phase):
        raise ValueError("Unexpected artifact layout; missing=" + repr(sorted(expected_files(phase) - actual)) +
                         "; extra=" + repr(sorted(actual - expected_files(phase))))
    for path in paths:
        with path.open("rb") as source:
            if source.read(2) != b"MZ":
                raise ValueError("Expected a PE executable: " + str(path))
    size = sum(path.stat().st_size for path in paths)
    if size > max_bytes:
        raise ValueError("Artifact exceeds the configured transfer budget")
    print(json.dumps({"phase": phase, "files": len(paths), "bytes": size, "max_bytes": max_bytes}))


def provenance(output: Path) -> None:
    version = configuration()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected = os.environ.get("GITHUB_SHA")
    if expected and commit != expected:
        raise ValueError("The build checkout differs from this workflow run's source SHA")
    record = {"schema_version": 1, "source_commit": commit, "version": version,
              "repository": os.environ.get("GITHUB_REPOSITORY"), "ref": os.environ.get("GITHUB_REF"),
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "workflow": os.environ.get("GITHUB_WORKFLOW"),
              "native_artifact_id": os.environ.get("NATIVE_ARTIFACT_ID"),
              "setup_artifact_id": os.environ.get("SETUP_ARTIFACT_ID"),
              "native_signing_request_id": os.environ.get("NATIVE_SIGNING_REQUEST_ID"),
              "setup_signing_request_id": os.environ.get("SETUP_SIGNING_REQUEST_ID"),
              "signing_policy": os.environ.get("REQUESTED_SIGNING_POLICY"),
              "status": "candidates_pending_windows_validation"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--require-signpath", action="store_true")
    check = commands.add_parser("layout")
    check.add_argument("phase", choices=("native", "setup"))
    check.add_argument("root", type=Path)
    check.add_argument("--max-bytes", type=int, default=300_000_000)
    budget = commands.add_parser("budget")
    budget.add_argument("paths", type=Path, nargs="+")
    budget.add_argument("--max-bytes", type=int, default=300_000_000)
    record = commands.add_parser("provenance")
    record.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        version = configuration()
        if args.require_signpath:
            require_signpath()
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as target:
                target.write("version=" + version + "\n")
    elif args.command == "layout":
        layout(args.phase, args.root, args.max_bytes)
    elif args.command == "budget":
        files = {path.resolve() for root in args.paths for path in regular_files(root)}
        size = sum(path.stat().st_size for path in files)
        if size > args.max_bytes:
            raise ValueError("Transfer budget exceeded: " + str(size) + " bytes")
        print(json.dumps({"files": len(files), "bytes": size, "max_bytes": args.max_bytes}))
    else:
        provenance(args.output)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(1)
