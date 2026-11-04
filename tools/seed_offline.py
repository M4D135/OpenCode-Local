#!/usr/bin/env python3
"""Prepare the real, versioned OpenCode runtime cache for an offline installation.

Only build-time Python/npm are needed. The installed Windows application uses
the standalone OpenCode executable and the copied files, without Node/npm.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config" / "offline"
DEFAULT_OUTPUT = ROOT / "build" / "payload" / "wad" / "offline-cache"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def target_path(output: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or "\\" in relative:
        raise ValueError(f"Invalid cache path: {relative}")
    return output / rel


def valid(path: Path, expected: str) -> bool:
    return path.is_file() and digest(path) == expected


def stage_asset(output: Path, item: dict, *, offline: bool) -> str:
    target = target_path(output, item["path"])
    if valid(target, item["sha256"]):
        return "cached"
    if offline:
        raise RuntimeError(f"Missing or changed offline asset: {item['path']}")
    if not item["url"].startswith("https://"):
        raise ValueError(f"Only HTTPS sources are supported: {item['url']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(3):
        temporary: Path | None = None
        try:
            request = urllib.request.Request(
                item["url"], headers={"User-Agent": "OpenCodeLocal-Offline-Builder/1.0"}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=target.name + ".", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    shutil.copyfileobj(response, stream)
            if temporary.stat().st_size != item["size"]:
                raise ValueError(f"Unexpected size: {item['path']}")
            if not valid(temporary, item["sha256"]):
                raise ValueError(f"SHA256 mismatch: {item['path']}")
            temporary.replace(target)
            return "downloaded"
        except Exception as error:
            last_error = error
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Cannot prepare {item['path']}: {last_error}")


def check_dependencies(directory: Path) -> dict:
    lock = json.loads((SOURCE / "package-lock.json").read_text(encoding="utf-8"))
    installed = 0
    for name, meta in lock["packages"].items():
        if not name or meta.get("optional") or meta.get("dev"):
            continue
        package_path = directory / name / "package.json"
        if not package_path.is_file():
            raise RuntimeError(f"Missing runtime dependency: {name}")
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if package.get("version") != meta.get("version"):
            raise RuntimeError(f"Runtime dependency version differs: {name}")
        installed += 1
    for path in (directory / "node_modules").rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Non-portable symbolic link in Windows seed: {path}")
        if path.is_file() and path.suffix.lower() in {".node", ".so", ".dll"}:
            raise RuntimeError(f"Unexpected native dependency in portable seed: {path}")
    return {"required_packages": installed, "optional_native_dependencies": "omitted"}


def prepare_dependencies(output: Path, *, verify_only: bool, offline: bool) -> dict:
    directory = output / "config" / "opencode"
    if verify_only:
        if not directory.is_dir():
            raise RuntimeError("The prepared dependency directory does not exist")
    else:
        directory.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        if verify_only:
            if not valid(directory / name, digest(SOURCE / name)):
                raise RuntimeError(f"Prepared {name} differs from the source lock")
        else:
            shutil.copy2(SOURCE / name, directory / name)
    if not verify_only:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise RuntimeError("npm is required on the build computer only")
        command = [
            npm, "ci", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund",
            "--registry=https://registry.npmjs.org",
        ]
        if offline:
            command.append("--offline")
        result = subprocess.run(
            command, cwd=directory, text=True, capture_output=True, timeout=300, check=False
        )
        if result.returncode:
            raise RuntimeError("npm ci failed:\n" + result.stderr[-6000:])
        # The SDK is imported by OpenCode. Its development command-line shims
        # are unused; removing them avoids shipping Linux symlinks to Windows.
        shutil.rmtree(directory / "node_modules" / ".bin", ignore_errors=True)
    return check_dependencies(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--offline", action="store_true", help="Use only already cached/downloaded inputs")
    parser.add_argument("--verify-only", action="store_true", help="Verify the prepared seed without downloads or changes")
    args = parser.parse_args()
    output = args.output.resolve()
    manifest_path = SOURCE / "grammar-assets.lock.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dependency_report = prepare_dependencies(
        output, verify_only=args.verify_only, offline=args.offline
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(16, args.jobs))) as pool:
        results = list(pool.map(
            lambda item: stage_asset(output, item, offline=args.offline or args.verify_only),
            manifest["assets"],
        ))
    for item in manifest["local_files"]:
        source = target_path(ROOT, item["source"])
        target = target_path(output, item["path"])
        if not valid(source, item["sha256"]):
            raise RuntimeError(f"Source license hash differs: {item['source']}")
        if args.verify_only:
            if not valid(target, item["sha256"]):
                raise RuntimeError(f"Prepared license hash differs: {item['path']}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    report = {
        "opencode": manifest["opencode"],
        "opentui": manifest["opentui"],
        "package_lock_sha256": digest(SOURCE / "package-lock.json"),
        "grammar_lock_sha256": digest(manifest_path),
        "dependencies": dependency_report,
        "assets_verified": len(results),
        "assets_downloaded": results.count("downloaded"),
        "grammar_languages": sum(a["kind"] == "grammar" for a in manifest["assets"]),
        "node_required_on_target": False,
    }
    if not args.verify_only:
        (output / "seed-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Offline seed preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
