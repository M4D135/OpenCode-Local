#!/usr/bin/env python3
"""Prepare pinned runtime inputs and invoke the explicit signing build phases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "build/payload"
DIST = ROOT / "dist"
MODEL = "Qwen3.5-4B-Q4_K_M.gguf"
MODEL_SIZE = 2740937888
MODEL_SHA = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
SLICE_SIZE = 1500000000


def run(args):
    print("RUN", " ".join(str(arg) for arg in args), flush=True)
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_flat(archive, destination, predicate=lambda name: True):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            name = Path(item.filename).name
            if item.is_dir() or not predicate(name):
                continue
            if name in ("", ".", ".."):
                raise RuntimeError("Unsafe archive entry")
            with source.open(item) as src, (destination / name).open("wb") as out:
                shutil.copyfileobj(src, out)


def stage_runtime():
    from cabarchive import CabArchive
    data = (ROOT / "downloads/vc_redist.x64.exe").read_bytes()
    found = {}
    offset = 0
    while (offset := data.find(b"MSCF", offset)) >= 0:
        if offset + 12 > len(data):
            break
        length = struct.unpack_from("<I", data, offset + 8)[0]
        if 36 <= length <= len(data) - offset:
            try:
                outer = CabArchive(data[offset:offset + length])
                for value in outer.values():
                    if not value.buf.startswith(b"MSCF"):
                        continue
                    for name, dll in CabArchive(value.buf).items():
                        if name.endswith(".dll_amd64"):
                            found[name.removesuffix("_amd64")] = dll.buf
            except (ValueError, KeyError):
                pass
        offset += 4
    required = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
    if not required <= found.keys():
        raise RuntimeError("Could not extract the official x64 VC++ runtime")
    prefixes = ("msvcp", "vcruntime", "concrt", "vcomp", "vcamp", "vccorlib")
    for name, contents in found.items():
        if name.startswith(prefixes):
            (PAYLOAD / "wad/engine" / name).write_bytes(contents)


def stage(offline):
    # A setup-only/base preparation never downloads a GGUF, including the
    # legacy 4B entry in artifacts.lock.json. The runtime comes directly from
    # pinned upstream archives; no previous Setup.exe is reused.
    from fetch_payload import fetch
    items = json.loads((ROOT / "config/artifacts.lock.json").read_text(encoding="utf-8"))
    for item in items:
        if not item["file"].endswith((".gguf", ".tar.gz")):
            fetch(item, offline)
    # Preserve downloaded grammar files for an explicitly offline rebuild.
    saved_seed = ROOT / "build/saved-offline-seed"
    seed = PAYLOAD / "wad/offline-cache"
    if offline and seed.exists():
        if saved_seed.exists():
            shutil.rmtree(saved_seed)
        seed.rename(saved_seed)
    # Only this generated build directory is reset, never user data.
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True)
    downloads = ROOT / "downloads"
    extract_flat(downloads / "llama-b12089-bin-win-cpu-x64.zip", PAYLOAD / "wad/engine")
    extract_flat(downloads / "opencode-windows-x64-baseline.zip", PAYLOAD / "wad/opencode", lambda n: n == "opencode.exe")
    extract_flat(downloads / "ripgrep-15.1.0-x86_64-pc-windows-msvc.zip", PAYLOAD / "wad/tools", lambda n: n == "rg.exe")
    model_dir = PAYLOAD / "wad/model"
    model_dir.mkdir(parents=True)
    stage_runtime()
    if offline and saved_seed.exists():
        saved_seed.rename(seed)
    command = [sys.executable, ROOT / "tools/seed_offline.py"]
    if offline:
        command.append("--offline")
    run(command)


def integration_files():
    shutil.copytree(ROOT / "docs", PAYLOAD / "wad/docs", dirs_exist_ok=True)
    shutil.copytree(ROOT / "licenses", PAYLOAD / "wad/licenses", dirs_exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", PAYLOAD / "wad/licenses/OpenCodeLocal-MIT.txt")
    shutil.copyfile(ROOT / "THIRD_PARTY.md", PAYLOAD / "wad/licenses/THIRD_PARTY.md")
    (PAYLOAD / "wad/config").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "config/opencode.json.template", PAYLOAD / "wad/config/opencode.json")
    (PAYLOAD / "wad/support").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "installer/installation.id", PAYLOAD / "wad/support/installation.id")


def manifest():
    files = []
    for path in sorted(PAYLOAD.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Symlink in Windows payload: " + str(path))
        if path.is_file() and path.name != "payload-manifest.json":
            relative = path.relative_to(PAYLOAD).as_posix()
            if len(relative) > 180:
                raise RuntimeError("Path too long for default Windows installation: " + relative)
            files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})
    (PAYLOAD / "wad/support/payload-manifest.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {len(files)} files", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use only SHA-256-verified cached inputs")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare build/payload from pinned upstream archives; no model download or compilation")
    parser.add_argument("--prepared", action="store_true", help="Reuse build/payload without downloads")
    parser.add_argument("--phase", choices=("native", "setup", "finalize", "all"), default="all")
    parser.add_argument("--signing", choices=("unsigned", "signed"), required=True)
    parser.add_argument("--pack", choices=("16go", "32go", "all"), default="all")
    parser.add_argument("--base-payload", type=Path)
    parser.add_argument("--work-root", type=Path, default=ROOT / "build/signing")
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "signing")
    parser.add_argument("--output-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--signed-native-root", type=Path)
    parser.add_argument("--signed-setup-root", type=Path)
    parser.add_argument("--expected-signer-sha256")
    parser.add_argument("--setup-only", action="store_true", help="Never download or rewrite models or .bin parts")
    parser.add_argument("--include-parts", action="store_true", help="Explicitly fetch and split the pinned models")
    args = parser.parse_args()
    if args.setup_only and args.include_parts:
        parser.error("--setup-only and --include-parts are mutually exclusive")
    if args.prepare_only and args.include_parts:
        parser.error("Base preparation excludes all models")
    from build_packs import main as build_main, verify_base
    from pack_profiles import load_pack
    from fetch_payload import fetch
    base = None
    if args.phase != "finalize" or args.prepare_only:
        if args.base_payload:
            base = args.base_payload.resolve(strict=True)
        elif args.prepared:
            base = PAYLOAD
        else:
            stage(args.offline)
            integration_files()
            run([sys.executable, ROOT / "tools/seed_offline.py", "--verify-only"])
            manifest()
            base = PAYLOAD
        verify_base(base)
    if args.prepare_only:
        print("PREPARED", base, "models excluded; no executable has been signed", flush=True)
        return
    if args.include_parts:
        if args.phase not in ("all", "finalize"):
            parser.error("--include-parts is only supported when finalizing")
        for name in (("16go", "32go") if args.pack == "all" else (args.pack,)):
            model = load_pack(name)["model"]
            fetch({"file": model["filename"], "url": model["url"],
                   "size": model["bytes"], "sha256": model["sha256"]}, args.offline)
    command = ["--phase", args.phase, "--signing", args.signing, "--pack", args.pack,
               "--work-root", str(args.work_root), "--artifact-root", str(args.artifact_root),
               "--output-root", str(args.output_root)]
    if base:
        command += ["--base-payload", str(base)]
    for argument in ("signed_native_root", "signed_setup_root", "expected_signer_sha256"):
        if value := getattr(args, argument):
            command += ["--" + argument.replace("_", "-"), str(value)]
    if args.include_parts:
        command += ["--include-parts", "--model-dir", str(ROOT / "downloads")]
    if args.setup_only:
        command.append("--setup-only")
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if sevenzip:
        command += ["--sevenzip", sevenzip]
    build_main(command)


if __name__ == "__main__":
    main()
