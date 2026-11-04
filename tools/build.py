#!/usr/bin/env python3
"""Build the offline Windows package locally on Linux, without GitHub Actions."""
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
    command = [sys.executable, ROOT / "tools/fetch_payload.py"]
    if offline:
        command.append("--offline")
    run(command)
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
    try:
        os.link(downloads / MODEL, model_dir / MODEL)
    except OSError:
        shutil.copyfile(downloads / MODEL, model_dir / MODEL)
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


def compile_native():
    cxx = os.environ.get("OPENCODE_LOCAL_CXX", "x86_64-w64-mingw32-g++")
    common = [cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-static", "-static-libgcc", "-static-libstdc++"]
    run(common + ["-municode", "-mwindows", ROOT / "src/launcher.cpp", "-o", PAYLOAD / "OpenCodeLocal.exe", "-luser32", "-lgdi32", "-lcomctl32", "-lcomdlg32", "-lshell32", "-lole32", "-luuid", "-lwinhttp", "-lws2_32", "-lbcrypt", "-liphlpapi"])
    run(common + ["-municode", ROOT / "src/assemble_model.cpp", "-o", ROOT / "build/AssembleModel.exe"])


def split_model():
    model = PAYLOAD / "wad/model" / MODEL
    if model.stat().st_size != MODEL_SIZE or sha256(model) != MODEL_SHA:
        raise RuntimeError("The staged model is not the pinned GGUF")
    DIST.mkdir(exist_ok=True)
    existing = [DIST / "Setup-1.bin", DIST / "Setup-2.bin"]
    if all(p.is_file() and p.stat().st_size == s for p, s in zip(existing, [SLICE_SIZE, MODEL_SIZE - SLICE_SIZE])):
        test = hashlib.sha256()
        for path in existing:
            with path.open("rb") as stream:
                while block := stream.read(4 * 1024 * 1024):
                    test.update(block)
        if test.hexdigest() == MODEL_SHA:
            print("Model slices already verified", flush=True)
            return
    remaining = MODEL_SIZE
    combined = hashlib.sha256()
    with model.open("rb") as source:
        for number in (1, 2):
            dest = DIST / f"Setup-{number}.bin"
            temp = dest.with_name(dest.name + ".partial")
            size = min(SLICE_SIZE, remaining)
            with temp.open("wb") as target:
                pending = size
                while pending:
                    chunk = source.read(min(pending, 4 * 1024 * 1024))
                    if not chunk:
                        raise RuntimeError("Model unexpectedly truncated")
                    target.write(chunk)
                    combined.update(chunk)
                    pending -= len(chunk)
            temp.replace(dest)
            remaining -= size
    if remaining or combined.hexdigest() != MODEL_SHA:
        raise RuntimeError("Split model validation failed")


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
    parser.add_argument("--offline", action="store_true", help="Use the previously downloaded build cache")
    parser.add_argument("--prepared", action="store_true", help="Reuse an already prepared payload; compile and package it")
    parser.add_argument("--pack", choices=("16go", "32go", "all"), default="all")
    parser.add_argument("--base-payload", type=Path, help="Verified existing payload to reuse without downloads")
    parser.add_argument("--work-root", type=Path, default=ROOT / "build/packs")
    parser.add_argument("--output-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--setup-only", action="store_true", help="Build installer ZIPs; keep existing model files")
    args = parser.parse_args()
    from build_packs import build_pack, verify_base
    from pack_profiles import load_pack
    from fetch_payload import fetch
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
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    for name in (("16go", "32go") if args.pack == "all" else (args.pack,)):
        if not args.setup_only:
            model = load_pack(name)["model"]
            fetch({"file": model["filename"], "url": model["url"],
                   "size": model["bytes"], "sha256": model["sha256"]}, args.offline)
        build_pack(name, base, args.work_root, args.output_root, ROOT / "downloads",
                   not args.setup_only, Path(sevenzip) if sevenzip else None)
    print("COMPLETE", args.output_root, flush=True)


if __name__ == "__main__":
    main()
