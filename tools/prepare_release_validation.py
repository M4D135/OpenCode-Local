#!/usr/bin/env python3
"""Prepare pinned runtime inputs for the release's Linux model validation.

Downloads are verified by fetch_payload.fetch. The setup comes from the same
successful Windows validation run and provides the portable OpenCode seed.
No model inference or GitHub write occurs in this helper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

import fetch_payload
from pack_profiles import load_pack, pack_names

ROOT = Path(__file__).resolve().parents[1]
MAX_RUNTIME_ARCHIVE_BYTES = 2**30
MAX_ARCHIVE_ENTRIES = 20_000


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_path(name: str) -> PurePosixPath:
    # Backslashes and drive prefixes have no legitimate role in these Linux
    # tar paths. NSIS paths are normalized before calling this check.
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or
            ":" in name or any(ord(character) < 32 for character in name)):
        raise ValueError("Unsafe archive member: " + repr(name))
    return path


def verify_tree(root: Path, *, allow_internal_links: bool) -> None:
    boundary = root.resolve(strict=True)
    for path in root.rglob("*"):
        if path.is_symlink():
            if not allow_internal_links:
                raise ValueError("Unexpected link in the setup extraction: " + str(path))
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(boundary):
                raise ValueError("Extracted link leaves its runtime directory: " + str(path))
        elif not path.is_file() and not path.is_dir():
            raise ValueError("Unexpected special file in runtime inputs: " + str(path))


def extract_tar(archive: Path, destination: Path) -> dict:
    """Extract to an exclusive directory, allowing only safe internal links."""
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz", errorlevel=2) as source:
        members = source.getmembers()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("Runtime archive contains too many entries")
        expanded_bytes = 0
        seen = set()
        for member in members:
            relative = archive_path(member.name)
            if relative in seen:
                raise ValueError("Duplicate runtime archive member: " + member.name)
            seen.add(relative)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError("Runtime archive contains a special file")
            if member.isfile():
                expanded_bytes += member.size
            if expanded_bytes > MAX_RUNTIME_ARCHIVE_BYTES:
                raise ValueError("Runtime archive exceeds the extraction budget")
        # Explicit even on Python versions where the default is still fully
        # trusted. data_filter rejects absolute/outside links and strips
        # dangerous ownership and permission metadata at each extraction.
        source.extractall(destination, members=members, filter="data")
    verify_tree(destination, allow_internal_links=True)
    return {"entries": len(members), "expanded_bytes": expanded_bytes}


def run_sevenzip(executable: str, arguments: list[str]) -> str:
    result = subprocess.run([executable, *arguments], check=False, capture_output=True,
                            text=True, encoding="utf-8", errors="strict", timeout=300)
    if result.returncode != 0:
        raise RuntimeError("7-Zip failed: " + result.stdout[-4000:] + result.stderr[-4000:])
    return result.stdout


def extract_seed(setup: Path, destination: Path, sevenzip: str) -> tuple[Path, dict]:
    listing = run_sevenzip(sevenzip, ["l", "-slt", "-sccUTF-8", str(setup)])
    if not re.search(r"^Type = Nsis\s*$", listing, re.MULTILINE):
        raise ValueError("The validated setup must be an NSIS archive")
    _, boundary, files = listing.partition("----------")
    if not boundary:
        raise ValueError("7-Zip did not return an archive member listing")
    entries = []
    listed_expanded_bytes = 0
    unknown_size_entries = 0
    for block in re.split(r"\r?\n\r?\n", files.strip()):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        name = fields["Path"].replace("\\", "/")
        archive_path(name)
        if "Symbolic Link" in fields or "Hard Link" in fields:
            raise ValueError("Links are not permitted in the setup extraction")
        entries.append(name)
        # Solid NSIS archives can report an empty Size field. Treat that as
        # unknown, then enforce the budget against the extracted files below.
        size = fields.get("Size", "").strip()
        if size:
            if not re.fullmatch(r"[0-9]+", size):
                raise ValueError("Invalid setup archive member size: " + repr(size))
            listed_expanded_bytes += int(size)
        else:
            unknown_size_entries += 1
    if not entries or len(entries) > MAX_ARCHIVE_ENTRIES or len(entries) != len(set(entries)):
        raise ValueError("Invalid or duplicate setup archive entries")
    if listed_expanded_bytes > MAX_RUNTIME_ARCHIVE_BYTES:
        raise ValueError("Setup extraction exceeds the runtime input budget")
    destination.mkdir(parents=True, exist_ok=False)
    run_sevenzip(sevenzip, ["x", "-y", "-bd", "-sccUTF-8", "-o" + str(destination), str(setup)])
    verify_tree(destination, allow_internal_links=False)
    expanded_bytes = 0
    extracted_entries = 0
    for path in destination.rglob("*"):
        extracted_entries += 1
        if extracted_entries > MAX_ARCHIVE_ENTRIES:
            raise ValueError("Setup extraction contains too many entries")
        if path.is_file():
            expanded_bytes += path.stat().st_size
        if expanded_bytes > MAX_RUNTIME_ARCHIVE_BYTES:
            raise ValueError("Setup extraction exceeds the runtime input budget")
    candidates = [path for path in destination.rglob("offline-cache")
                  if path.is_dir() and path.parent.name == "wad"]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one wad/offline-cache in the validated setup")
    seed = candidates[0].resolve(strict=True)
    if not (seed / "seed-manifest.json").is_file():
        raise ValueError("The extracted OpenCode seed has no verification manifest")
    return seed, {"entries": len(entries), "expanded_bytes": expanded_bytes,
                  "listed_expanded_bytes": listed_expanded_bytes,
                  "unknown_size_entries": unknown_size_entries,
                  "extracted_entries": extracted_entries,
                  "setup_sha256": digest(setup)}


def unique_binary(root: Path, name: str) -> Path:
    candidates = [path for path in root.rglob(name) if path.is_file()]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one {name} in its separate runtime tree; found {len(candidates)}")
    path = candidates[0].resolve(strict=True)
    if not path.is_relative_to(root.resolve(strict=True)):
        raise ValueError("Runtime binary leaves its extraction tree")
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError("The Linux runtime is not an ELF executable: " + name)
    if not os.access(path, os.X_OK):
        raise ValueError("The Linux runtime is not executable: " + name)
    return path


def locked_item(items: list[dict], predicate, description: str) -> dict:
    matches = [item for item in items if predicate(item["file"])]
    if len(matches) != 1:
        raise ValueError("Expected one pinned " + description)
    item = matches[0]
    if (not re.fullmatch(r"[A-Za-z0-9_.+-]+", item["file"]) or
            not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) or int(item["size"]) <= 0):
        raise ValueError("Invalid download lock for " + description)
    return item


def prepare(args) -> dict:
    if tuple(pack_names()) != ("moe",):
        raise ValueError("Release validation requires exactly the single moe profile")
    pack = load_pack("moe")
    items = json.loads((ROOT / "config/artifacts.lock.json").read_text(encoding="utf-8"))
    engine_item = locked_item(items, lambda name: name.startswith("llama-") and
                              name.endswith("-bin-ubuntu-x64.tar.gz"), "Linux CPU engine archive")
    opencode_item = locked_item(items, lambda name: name == "opencode-linux-x64-baseline.tar.gz",
                               "Linux baseline OpenCode archive")
    model = pack["model"]
    model_item = locked_item(items, lambda name: name == model["filename"], "MoE model")
    expected_model = {"file": model["filename"], "url": model["url"],
                      "size": model["bytes"], "sha256": model["sha256"]}
    if any(model_item.get(key) != value for key, value in expected_model.items()):
        raise ValueError("The model lock differs from config/packs.json")
    setup = args.setup.resolve(strict=True)
    if not setup.is_file() or args.setup.is_symlink():
        raise ValueError("The validated setup must be a regular file")
    sevenzip = args.sevenzip or shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("7-Zip is required to read the validated setup")
    work = args.work_root.absolute()
    work.mkdir(parents=True, exist_ok=False)
    fetch_payload.DOWNLOADS.mkdir(parents=True, exist_ok=True)
    if fetch_payload.DOWNLOADS.is_symlink():
        raise ValueError("The download cache must not be a symbolic link")
    runtime_records = {}
    for name, item in (("engine", engine_item), ("opencode", opencode_item)):
        if (fetch_payload.DOWNLOADS / item["file"]).is_symlink():
            raise ValueError("A pinned download must not be a symbolic link")
        downloaded = fetch_payload.fetch(item, args.offline)
        extracted = extract_tar(downloaded, work / name)
        runtime_records[name] = {"file": item["file"], "bytes": item["size"],
                                 "sha256": item["sha256"], "version": item.get("version"), **extracted}
    engine = unique_binary(work / "engine", "llama-server")
    opencode = unique_binary(work / "opencode", "opencode")
    seed, setup_record = extract_seed(setup, work / "setup", sevenzip)

    # Preserve room for one release slice and normal validation files. A
    # cached model only avoids the download requirement after full SHA checks.
    cached_model = fetch_payload.DOWNLOADS / model["filename"]
    if cached_model.is_symlink():
        raise ValueError("The cached model must not be a symbolic link")
    cached = (cached_model.is_file() and cached_model.stat().st_size == model["bytes"] and
              digest(cached_model) == model["sha256"])
    configuration = json.loads((ROOT / "config/packs.json").read_text(encoding="utf-8"))
    slice_bytes = int(configuration["slice_bytes"])
    required = (0 if cached else model["bytes"]) + slice_bytes + 2 * 2**30
    free = shutil.disk_usage(fetch_payload.DOWNLOADS).free
    if free < required:
        raise RuntimeError(f"Insufficient disk space before model download: {free} bytes free, "
                           f"{required} required (model, one slice, and 2 GiB reserve)")
    model_path = fetch_payload.fetch(model_item, args.offline)
    outputs = {"engine": str(engine), "model": str(model_path.resolve(strict=True)),
               "opencode": str(opencode), "offline_seed": str(seed)}
    if any(any(ord(character) < 32 for character in value) for value in outputs.values()):
        raise ValueError("Control characters are not permitted in runtime output paths")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as target:
            for name, value in outputs.items():
                target.write(name + "=" + value + "\n")
    return {"schema_version": 1, "passed": True, "pack": "moe", "version": pack["app_version"],
            "outputs": outputs, "runtimes": runtime_records, "setup": setup_record,
            "model": expected_model, "disk_space_before_model": {"free_bytes": free,
            "required_free_bytes": required, "verified_model_already_cached": cached,
            "slice_bytes": slice_bytes, "reserve_bytes": 2 * 2**30},
            "model_loaded": False, "publication_performed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=ROOT / "qa/runtime-inputs")
    parser.add_argument("--report", type=Path, default=ROOT / "qa/runtime-inputs.json")
    parser.add_argument("--sevenzip", help="Explicit 7-Zip executable; otherwise detected on PATH")
    parser.add_argument("--offline", action="store_true", help="Verify cached pinned downloads only")
    args = parser.parse_args()
    try:
        report = prepare(args)
    except Exception as error:
        report = {"schema_version": 1, "passed": False, "error": f"{type(error).__name__}: {error}",
                  "model_loaded": False, "publication_performed": False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
