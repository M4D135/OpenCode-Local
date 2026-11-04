#!/usr/bin/env python3
"""Build separate, pinned 16/32 GiB offline installers from a prepared payload.

All large build outputs are completed before atomic publication. The Windows
installer does not download models or bypass endpoint security.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from pack_profiles import ROOT, configure_opencode, generate, load_pack


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(command: list, log: Path | None = None, cwd: Path = ROOT) -> None:
    print("RUN", " ".join(map(str, command[:5])), flush=True)
    if log:
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(list(map(str, command)), cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-10000:])
    else:
        subprocess.run(list(map(str, command)), cwd=cwd, check=True)


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".complete", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, output, 4 * 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != source.stat().st_size:
            raise RuntimeError("Incomplete output copy")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def verify_base(base: Path) -> dict:
    manifest_path = base / "wad/support/payload-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Prepared base is missing payload-manifest.json")
    count = 0
    for entry in json.loads(manifest_path.read_text(encoding="utf-8")):
        if entry["path"].startswith("wad/model/"):
            continue
        target = base / entry["path"]
        if not target.is_file() or target.stat().st_size != entry["size"] or digest(target) != entry["sha256"]:
            raise ValueError("Prepared base differs from its manifest: " + entry["path"])
        count += 1
    return {"base_payload_files_verified": count}


def make_readme(pack: dict) -> str:
    model = pack["model"]
    parts = "\n".join("  " + p["name"] for p in pack["parts"])
    reuse = "Les fichiers Setup-1.bin et Setup-2.bin de la version 1.0.0 restent compatibles.\n" if pack["id"] == "16go" else ""
    speed = "Le modèle 27B demande plus de temps de chargement et de calcul sur CPU.\n" if pack["id"] == "32go" else ""
    return f'''{pack["display_name"]} — version {pack["app_version"]}

INSTALLATION HORS LIGNE
1. Extraire Setup.exe de Setup-{pack["ram_gib"]}Go-{pack["app_version"]}.zip.
2. Placer dans le même dossier SOIT le modèle complet :
   {model["filename"]}
   SOIT toutes ses {len(pack["parts"])} parties :
{parts}
3. Lancer Setup.exe et choisir un dossier dédié au programme.
4. Ouvrir le raccourci « {pack["shortcut_name"]} ».

{reuse}Aucun téléchargement n'est effectué par l'installateur ou le lanceur.
Le setup vérifie la taille et le SHA-256 complet du modèle avant publication.
Un modèle complet présent mais invalide provoque une erreur explicite.

MODÈLE
{model["label"]}
Taille : {model["bytes"]} octets
SHA-256 : {model["sha256"]}
Source figée : {model["url"]}
Contexte initial : {pack["default_context"]} tokens. Une session de calcul à la fois.
{speed}
DOSSIERS
Programme : %LOCALAPPDATA%\\Programs\\{pack["install_dir_name"]}
Données : %LOCALAPPDATA%\\{pack["profile_name"]}
Le pack 32 Go possède son propre profil ; les conversations du pack 16 Go
ne sont pas supprimées lors de son installation.

PROTECTION DU POSTE
Le lanceur reste non signé par Authenticode. Une détection TEHTRIS a été
observée sur le lanceur 1.0.0/1.0.1 ; sa cause n'est pas encore qualifiée.
Consulter wad\\docs\\TEHTRIS.md et les empreintes du pack pour l'analyse.
Ces packs ne modifient pas l'antivirus, le pare-feu ou leurs exclusions.

OpenCode nécessite une connexion locale à 127.0.0.1. Le mode Discussion
directe est disponible lorsque les connexions localhost sont interdites.
Les droits d'écriture et d'exécution restent ceux de la session Windows.
'''


def prepare_payload(base: Path, work: Path, pack: dict) -> Path:
    payload = work / "payload"
    if payload.exists():
        # Only this selected generated directory is replaced, never an installed app.
        shutil.rmtree(payload)
    def ignore(directory, names):
        relative = Path(directory).relative_to(base)
        if relative == Path("wad/model"):
            return names
        if relative == Path("wad/support"):
            return [name for name in names if name in {"Uninstall.exe", "payload-manifest.json"}]
        return []
    shutil.copytree(base, payload, ignore=ignore)
    (payload / "wad/model").mkdir(exist_ok=True)
    generated = work / "generated"
    generate(pack, generated)
    shutil.copyfile(generated / "installation.id", payload / "wad/support/installation.id")
    shutil.copyfile(generated / "pack.json", payload / "wad/support/pack.json")
    template = (base / "wad/config/opencode.json").read_text(encoding="utf-8")
    (payload / "wad/config/opencode.json").write_text(configure_opencode(template, pack), encoding="utf-8")
    for filename in ("TEHTRIS.md", "MODELES.md"):
        source = ROOT / "docs" / filename
        if source.is_file():
            shutil.copyfile(source, payload / "wad/docs" / filename)
    (payload / "wad/docs/LISEZ-MOI.txt").write_text(make_readme(pack), encoding="utf-8")
    return payload


def compile_native(work: Path, payload: Path, pack: dict) -> None:
    cxx = os.environ.get("OPENCODE_LOCAL_CXX", "x86_64-w64-mingw32-g++")
    windres = os.environ.get("OPENCODE_LOCAL_WINDRES", "x86_64-w64-mingw32-windres")
    resource = work / "launcher-res.o"
    # windres definitions need literal escaped quotes, not shell interpolation.
    display = pack["display_name"].replace("—", "-")
    version = pack["app_version"]
    run([windres, "--codepage=65001", "-I", ROOT / "src",
         '-DPACK_DISPLAY_NAME=\\"' + display + '\\"',
         '-DPACK_VERSION_STRING=\\"' + version + '\\"',
         "-DPACK_VERSION_NUMBER=" + version.replace(".", ",") + ",0",
         "-i", ROOT / "src/launcher.rc", "-o", resource, "-O", "coff"], work / "resources.log")
    common = [cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-static",
              "-static-libgcc", "-static-libstdc++", "-I", work / "generated"]
    run(common + ["-municode", "-mwindows", ROOT / "src/launcher.cpp", resource,
                  "-o", payload / "OpenCodeLocal.exe", "-luser32", "-lgdi32", "-lcomctl32",
                  "-lcomdlg32", "-lshell32", "-lole32", "-luuid", "-lwinhttp", "-lws2_32",
                  "-lbcrypt", "-liphlpapi"], work / "launcher-build.log")
    run(common + ["-municode", ROOT / "src/assemble_model.cpp", "-o", work / "AssembleModel.exe"],
        work / "assembler-build.log")


def write_manifest(payload: Path, pack: dict) -> list:
    files = []
    for path in sorted(payload.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in payload: " + str(path))
        if path.is_file() and path.name != "payload-manifest.json":
            files.append({"path": path.relative_to(payload).as_posix(), "size": path.stat().st_size, "sha256": digest(path)})
    model = pack["model"]
    files.append({"path": "wad/model/" + model["filename"], "size": model["bytes"], "sha256": model["sha256"]})
    (payload / "wad/support/payload-manifest.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
    return files


def split_model(model: Path, pack: dict, output: Path) -> list:
    if model.stat().st_size != pack["model"]["bytes"] or digest(model) != pack["model"]["sha256"]:
        raise ValueError("Model is not the pinned GGUF: " + str(model))
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with model.open("rb") as source:
        for part in pack["parts"]:
            destination = output / part["name"]
            fd, name = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".partial", dir=output)
            temporary = Path(name)
            sha = hashlib.sha256()
            remaining = part["bytes"]
            try:
                with os.fdopen(fd, "wb") as target:
                    while remaining:
                        block = source.read(min(4 * 1024 * 1024, remaining))
                        if not block:
                            raise ValueError("Model truncated while splitting")
                        target.write(block)
                        sha.update(block)
                        remaining -= len(block)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            results.append({"name": destination.name, "bytes": destination.stat().st_size, "sha256": sha.hexdigest()})
            print("MODEL PART", destination.name, destination.stat().st_size, flush=True)
        if source.read(1):
            raise ValueError("Unexpected bytes after last model part")
    return results


def build_pack(name: str, base: Path, work_root: Path, output_root: Path,
               model_dir: Path | None = None, include_parts: bool = False, sevenzip: Path | None = None) -> dict:
    pack = load_pack(name)
    work = work_root / name
    work.mkdir(parents=True, exist_ok=True)
    output = output_root / name
    output.mkdir(parents=True, exist_ok=True)
    payload = prepare_payload(base, work, pack)
    compile_native(work, payload, pack)
    files = write_manifest(payload, pack)
    non_model = sum(e["size"] for e in files if not e["path"].startswith("wad/model/"))
    profile = generate(pack, work / "generated", non_model)
    run(["python", ROOT / "installer/generate_payload_include.py", "--payload", payload,
         "--output", work / "payload-files.nsh", "--model-filename", pack["model"]["filename"]], work / "include.log")
    exe = work / "Setup.exe"
    run(["makensis", "-V3", f"-DPayloadDir={payload}", f"-DHelperPath={work / 'AssembleModel.exe'}",
         f"-DPackConfigPath={work / 'generated/pack-profile.nsh'}", f"-DOutputPath={exe}",
         ROOT / "installer/OpenCodeLocal.nsi"], work / "installer-build.log")
    if sevenzip:
        run([sevenzip, "t", exe], work / "installer-integrity.log")
    zip_name = f'Setup-{pack["ram_gib"]}Go-{pack["app_version"]}.zip'
    archive = work / zip_name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
        zipped.write(exe, "Setup.exe")
    with zipfile.ZipFile(archive) as zipped:
        if zipped.namelist() != ["Setup.exe"] or zipped.testzip() is not None:
            raise ValueError("Installer ZIP validation failed")
    atomic_copy(archive, output / zip_name)
    atomic_copy(payload / "wad/docs/LISEZ-MOI.txt", output / f'LISEZ-MOI-{pack["ram_gib"]}Go.txt')
    atomic_copy(work / "generated/pack.json", output / f'PACK-{pack["ram_gib"]}Go.json')
    model_source = dict(pack["model"])
    model_source["parts"] = pack["parts"]
    (output / "MODELE.json").write_text(json.dumps(model_source, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    parts = []
    if include_parts:
        if model_dir is None:
            raise ValueError("--model-dir required for --include-parts")
        parts = split_model(model_dir / pack["model"]["filename"], pack, output)
    checksums = {zip_name: digest(archive), "Setup.exe": digest(exe)}
    checksums.update({p["name"]: p["sha256"] for p in parts})
    if name == "16go" and not parts:
        checksums.update({"Setup-1.bin": "416e11f3cf0391279546e73fd7a3658c368ae4328f0a94d34b6d7d6d0394964f",
                          "Setup-2.bin": "d4761367a069f853868471b7c11cd9a65c6112e5d9476b9c3ac2bd32bcebacfb"})
    checksums[pack["model"]["filename"]] = pack["model"]["sha256"]
    (output / f'SHA256SUMS-{pack["ram_gib"]}Go.txt').write_text("".join(f"{sha}  {filename}\n" for filename, sha in checksums.items()), encoding="ascii")
    result = {"pack": name, "version": pack["app_version"], "model": pack["model"],
              "profile": profile, "payload_entries": len(files), "launcher_sha256": digest(payload / "OpenCodeLocal.exe"),
              "assembler_sha256": digest(work / "AssembleModel.exe"), "setup_sha256": digest(exe),
              "zip": str(output / zip_name), "zip_bytes": archive.stat().st_size, "zip_sha256": digest(archive),
              "zip_contains": ["Setup.exe"], "parts": parts, "native_compilation": "passed",
              "installer_archive_integrity": "passed" if sevenzip else "not_run",
              "windows_execution": "not_run", "tehtris_compatibility": "not_validated"}
    report = ROOT / "validation" / f"pack-{name}.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", choices=("16go", "32go", "all"), default="all")
    parser.add_argument("--base-payload", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=ROOT / "build/packs")
    parser.add_argument("--output-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--include-parts", action="store_true")
    parser.add_argument("--sevenzip", type=Path)
    args = parser.parse_args()
    base = args.base_payload.resolve(strict=True)
    print(json.dumps(verify_base(base)), flush=True)
    for name in (("16go", "32go") if args.pack == "all" else (args.pack,)):
        build_pack(name, base, args.work_root.resolve(), args.output_root.resolve(),
                   args.model_dir, args.include_parts, args.sevenzip)


if __name__ == "__main__":
    main()
