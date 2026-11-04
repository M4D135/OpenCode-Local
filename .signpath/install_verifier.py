#!/usr/bin/env python3
"""Build the pinned verification tool in a runner-local directory, without a key."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(Path(__file__).with_name("tool-versions.json").read_text())["osslsigncode"]
    prefix = args.prefix.resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="opencode-verifier-") as directory:
        root = Path(directory)
        archive = root / "source.tar.gz"
        digest = hashlib.sha256()
        total = 0
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "OpenCode-Local-build"})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as target:
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > spec["bytes"]:
                    raise ValueError("Verifier source exceeds its pinned size")
                digest.update(block)
                target.write(block)
        if total != spec["bytes"] or digest.hexdigest() != spec["sha256"]:
            raise ValueError("Verifier source does not match the pinned SHA-256 and size")
        with tarfile.open(archive) as source:
            source.extractall(root, filter="data")
        source = root / ("osslsigncode-" + spec["commit"])
        build = root / "build"
        subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                        "-DCMAKE_INSTALL_PREFIX=" + str(prefix),
                        "-DBASH_COMPLETION_USER_DIR=" + str(prefix / "share")], check=True)
        subprocess.run(["cmake", "--build", str(build), "--parallel", "2"], check=True)
        subprocess.run(["cmake", "--install", str(build)], check=True)
    binary = prefix / "bin" / "osslsigncode"
    result = subprocess.run([str(binary), "--version"], check=True, capture_output=True, text=True)
    if "osslsigncode " + spec["version"] not in result.stdout:
        raise ValueError("Unexpected verifier version after compilation")
    print(result.stdout.strip())
    if os.environ.get("GITHUB_PATH"):
        with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as target:
            target.write(str(binary.parent) + "\n")


if __name__ == "__main__":
    main()
