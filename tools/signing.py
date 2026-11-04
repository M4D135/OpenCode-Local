#!/usr/bin/env python3
"""Validate returned Authenticode binaries; never create keys or change OS trust.

PE content matching is deliberately stricter than Authenticode hashing: only the
checksum, certificate-directory entry and certificate appended at EOF may differ.
This check is not a trust verdict. Signed imports also require the platform's
signature verifier and the SHA-256 fingerprint of the approved leaf certificate.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOUSE_FILES = ("OpenCodeLocal.exe", "AssembleModel.exe", "Uninstall.exe")
SOURCE_DIRS = {"src", "tools", "installer", "config", "licenses", "docs", ".github", ".signpath"}


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _pe(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("Not a Windows PE executable")
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Invalid PE header")
    optional = pe + 24
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    if optional + optional_size > len(data) or optional_size < 96:
        raise ValueError("Truncated PE optional header")
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in (0x10b, 0x20b):
        raise ValueError("Unsupported PE optional header")
    directories = optional + (112 if magic == 0x20b else 96)
    count_offset = optional + (108 if magic == 0x20b else 92)
    certificate = directories + 4 * 8
    if struct.unpack_from("<I", data, count_offset)[0] < 5 or certificate + 8 > optional + optional_size:
        raise ValueError("PE has no certificate-directory slot")
    offset, size = struct.unpack_from("<II", data, certificate)
    if bool(offset) != bool(size):
        raise ValueError("Incomplete PE certificate directory")
    if size:
        if offset % 8 or offset < optional + optional_size or offset + size != len(data):
            raise ValueError("Certificate must be aligned and occupy the end of the PE")
        cursor = offset
        while cursor < offset + size:
            if cursor + 8 > len(data):
                raise ValueError("Truncated WIN_CERTIFICATE")
            length, revision, kind = struct.unpack_from("<IHH", data, cursor)
            if length <= 8 or revision != 0x200 or kind != 2 or cursor + length > offset + size:
                raise ValueError("Invalid Authenticode certificate record")
            end = cursor + ((length + 7) & ~7)
            if end > offset + size or any(data[cursor + length:end]):
                raise ValueError("Invalid certificate padding")
            cursor = end
    return optional + 64, certificate, offset, size


def image_record(path: Path, *, require_unsigned: bool = True) -> dict:
    data = path.read_bytes()
    checksum, certificate, offset, size = _pe(data)
    if require_unsigned and size:
        raise ValueError("Expected a newly compiled unsigned executable: " + str(path))
    content = bytearray(data[:offset] if size else data)
    content[checksum:checksum + 4] = b"\0" * 4
    content[certificate:certificate + 8] = b"\0" * 8
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "unsigned_bytes": len(content), "image_sha256": hashlib.sha256(content).hexdigest()}


def match_image(path: Path, expected: dict, *, signed: bool) -> None:
    data = path.read_bytes()
    checksum, certificate, offset, size = _pe(data)
    if signed != bool(size):
        raise ValueError(("Missing Authenticode signature: " if signed else "Unsigned mode received a signed executable: ") + str(path))
    if not signed:
        if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError("Unsigned executable differs from the compilation: " + str(path))
        return
    length = expected["unsigned_bytes"]
    # Signers may append at most seven NUL bytes before the aligned certificate.
    if offset < length or offset > ((length + 7) & ~7) or any(data[length:offset]):
        raise ValueError("Signed executable changed its non-certificate length: " + str(path))
    content = bytearray(data[:length])
    content[checksum:checksum + 4] = b"\0" * 4
    content[certificate:certificate + 8] = b"\0" * 8
    if hashlib.sha256(content).hexdigest() != expected["image_sha256"]:
        raise ValueError("Signed executable does not match the compiled image: " + str(path))


def signer_pin(value: str | None) -> str:
    pin = (value or "").replace(":", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("Signed mode requires --expected-signer-sha256 from the approved signing certificate")
    return pin


def verify_trust(path: Path, expected_signer: str) -> dict:
    pin = signer_pin(expected_signer)
    if os.name == "nt":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise RuntimeError("PowerShell is required for Windows Authenticode verification")
        script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$signature = Get-AuthenticodeSignature -LiteralPath $env:OPENCODE_VERIFY_FILE
if ($signature.Status -ne 'Valid') { throw "Authenticode status: $($signature.Status) $($signature.StatusMessage)" }
if ($signature.SignatureType -ne 'Authenticode') { throw 'An embedded Authenticode signature is required' }
if ($null -eq $signature.SignerCertificate) { throw 'No signer certificate' }
if ($null -eq $signature.TimeStamperCertificate) { throw 'A verified timestamp is required' }
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $pin = [BitConverter]::ToString($sha.ComputeHash($signature.SignerCertificate.RawData)).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose() }
if ($pin -ne $env:OPENCODE_VERIFY_SIGNER) { throw 'Unexpected signing certificate' }
[ordered]@{verifier='Windows Authenticode';status='valid';signer_sha256=$pin;publisher=$signature.SignerCertificate.Subject;timestamp='present'} | ConvertTo-Json -Compress
'''
        env = dict(os.environ, OPENCODE_VERIFY_FILE=str(path.resolve()), OPENCODE_VERIFY_SIGNER=pin)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=180)
        if result.returncode:
            raise RuntimeError("Authenticode validation failed for " + str(path) + "\n" + result.stderr[-5000:])
        evidence = json.loads(result.stdout.lstrip("\ufeff"))
        signtool = shutil.which("signtool.exe")
        if not signtool:
            kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits/10/bin"
            choices = sorted(kits.glob("*/x64/signtool.exe"), reverse=True)
            signtool = str(choices[0]) if choices else None
        if not signtool:
            raise RuntimeError("The Windows SDK signtool.exe is required to verify all signatures and timestamp policy")
        checked = subprocess.run([signtool, "verify", "/pa", "/all", "/tw", "/v", str(path)],
                                 capture_output=True, text=True, errors="replace", timeout=180)
        # signtool uses exit 2 for warnings, including the /tw missing timestamp
        # warning. Only a clean exit 0 can authorize packaging a signed release.
        if checked.returncode:
            raise RuntimeError("signtool signature/timestamp validation failed for " + str(path) + "\n" +
                               (checked.stdout + checked.stderr)[-6000:])
        evidence.update(verifier="Windows Authenticode + signtool /pa /all /tw", timestamp="verified")
        return evidence
    executable = shutil.which("osslsigncode")
    if not executable:
        raise RuntimeError("Signed imports require osslsigncode >= 2.13 on this platform, or Windows Authenticode")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True).stdout
    match = re.search(r"osslsigncode\s+(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match or tuple(int(v or 0) for v in match.groups()) < (2, 13, 0):
        raise RuntimeError("osslsigncode >= 2.13 is required for safe PE signature verification")
    # cafile follows SSL_CERT_FILE. Do not let an environment override supply
    # an arbitrary root for release verification; use the installed OS bundle.
    default_ca = ssl.get_default_verify_paths().openssl_cafile
    candidates = [Path("/etc/ssl/certs/ca-certificates.crt"), Path("/etc/pki/tls/certs/ca-bundle.crt")]
    if default_ca:
        candidates.append(Path(default_ca))
    ca = next((str(path) for path in candidates if path.is_file()), None)
    if not ca:
        raise RuntimeError("The system CA certificate bundle is unavailable; no trust override is allowed")
    command = [executable, "verify", "-in", str(path), "-CAfile", ca, "-TSA-CAfile", ca,
               "-require-leaf-hash", "sha256:" + pin, "-verbose"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=240)
    output = result.stdout + result.stderr
    if result.returncode or "Signature verification: ok" not in output:
        raise RuntimeError("Authenticode validation failed for " + str(path) + "\n" + output[-6000:])
    # osslsigncode can accept an untimestamped signature. Release imports cannot.
    if not re.search(r"Timestamp (?:Server Signature )?verification:\s*ok", output, re.IGNORECASE):
        raise RuntimeError("A verified timestamp was not reported for " + str(path))
    return {"verifier": "osslsigncode", "status": "valid", "signer_sha256": pin,
            "timestamp": "verified", "verification_output": output}


def source_identity() -> dict:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).split(b"\0")
    files = []
    for raw in sorted(set(paths)):
        if not raw:
            continue
        name = raw.decode("utf-8")
        parts = PurePosixPath(name).parts
        if len(parts) > 1 and parts[0] not in SOURCE_DIRS:
            continue
        target = ROOT / name
        if target.is_symlink() or not target.is_file():
            raise ValueError("Source input is missing or is a symlink: " + name)
        files.append({"path": name, "sha256": digest(target)})
    tree_digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    changed = subprocess.check_output(["git", "diff", "HEAD", "--name-only", "-z"], cwd=ROOT).split(b"\0")
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).split(b"\0")
    source_names = {file["path"] for file in files}
    # Generated artifacts can live outside ignored build/. They must neither
    # enter the source digest nor make an otherwise clean workflow unclean.
    dirty = any(path and path.decode("utf-8") in source_names for path in changed + untracked)
    return {"commit": commit, "inputs_sha256": tree_digest, "dirty": dirty, "files": files}


def check_source(expected: dict, *, signed: bool) -> None:
    current = source_identity()
    if current["commit"] != expected["commit"] or current["inputs_sha256"] != expected["inputs_sha256"]:
        raise ValueError("Sources changed between signing phases; restart from --phase native")
    if signed and expected.get("dirty"):
        raise ValueError("Signed release mode requires native compilation from a clean source checkout")


def artifact_file(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or any(p in (".", "..") for p in rel.parts) or "\\" in relative:
        raise ValueError("Invalid artifact path: " + relative)
    path = root.joinpath(*rel.parts)
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("Signing artifacts cannot contain symlinks: " + str(current))
        current = current.parent
    if not path.is_file():
        raise ValueError("Missing signing artifact: " + str(path))
    return path


def validate_files(root: Path, records: list[dict], *, signed: bool, expected_signer: str | None) -> dict:
    if signed:
        signer_pin(expected_signer)
    expected = {record["path"] for record in records}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError(f"Signing artifact file set differs; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
    results = {}
    for record in records:
        path = artifact_file(root, record["path"])
        match_image(path, record, signed=signed)
        trust = verify_trust(path, expected_signer) if signed else {"status": "unsigned"}
        results[record["path"]] = {"sha256": digest(path), "bytes": path.stat().st_size, "signature": trust}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture-uninstaller", help="NSIS !uninstfinalize hook; does not execute the PE")
    capture.add_argument("source", type=Path)
    capture.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "capture-uninstaller":
        image_record(args.source)
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.source, args.destination)
        print("Exported unsigned uninstaller:", args.destination)


if __name__ == "__main__":
    main()
